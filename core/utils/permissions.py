import uuid

from django.core.exceptions import ValidationError
from django.db import connection
from django.http import JsonResponse

from core.utils.auth import UNAUTHORIZED, LoginRequiredMixin, _is_authenticated, get_tenant

# Metodos HTTP que transportan datos y por tanto exigen permiso declarado.
# OPTIONS/HEAD quedan fuera para no romper CORS ni los health checks.
METODOS_PROTEGIDOS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# Centinela que permite_para() puede devolver cuando un metodo no exige
# permiso de catalogo porque la autorizacion es por objeto: el actor accede a
# su propio registro. La ERS lo define asi en RF-07 ("Usuario propietario:
# solo datos personales") y volvera a aparecer en RF-09 y RF-19.
#
# Es un centinela explicito y no None para no debilitar el fallo cerrado:
# None sigue significando "permiso no declarado" y responde 403.
SIN_PERMISO = object()

# Dominio del nucleo (Modulos 1-7). RF-01/RN06: el modulo de Identidad y
# Gestion de Usuarios nunca puede deshabilitarse, asi que sus permisos jamas
# quedan inertes por RF-10/RN04 y se excluyen del filtro por modulo activo.
DOMINIO_NUCLEO = "core"

# Una sola consulta por peticion. Devuelve una fila por rol activo del usuario
# dentro de su tenant, con el codigo de permiso o NULL si el rol no tiene
# ninguno (LEFT JOIN: un rol de sistema sin filas en rol_permiso debe seguir
# apareciendo para que se detecte el bypass).
#
# El LEFT JOIN a core.permiso aplica RF-10/RN04: un permiso cuyo modulo esta
# desactivado en el tenant no entra al conjunto efectivo, pero su fila en
# core.rol_permiso se conserva intacta ("queda inerte, no se elimina del rol").
_SQL_PERMISOS = """
    SELECT r.es_sistema, p.codigo
      FROM core.usuario_rol ur
      JOIN core.rol r
        ON r.id = ur.rol_id
       AND r.activo
       AND r.tenant_id = %(tenant)s
      LEFT JOIN core.rol_permiso rp
        ON rp.rol_id = r.id
      LEFT JOIN core.permiso p
        ON p.id = rp.permiso_id
       AND (
            p.dominio = %(nucleo)s
         OR EXISTS (
              SELECT 1
                FROM core.tenant_modulo tm
                JOIN core.modulo m ON m.id = tm.modulo_id
               WHERE tm.tenant_id = %(tenant)s
                 AND tm.activo
                 AND m.codigo = upper(p.dominio)
            )
       )
     WHERE ur.usuario_id = %(usuario)s
"""

# Catalogo completo aplicable al tenant, con el mismo filtro de modulo activo.
# Solo se consulta para expandir el conjunto efectivo de un rol con bypass
# (ver PermissionResolver.codigos); las comprobaciones puntuales no lo usan.
_SQL_CATALOGO = """
    SELECT p.codigo
      FROM core.permiso p
     WHERE p.dominio = %(nucleo)s
        OR EXISTS (
             SELECT 1
               FROM core.tenant_modulo tm
               JOIN core.modulo m ON m.id = tm.modulo_id
              WHERE tm.tenant_id = %(tenant)s
                AND tm.activo
                AND m.codigo = upper(p.dominio)
           )
"""


def codigos_aplicables(tenant_id):
    """Codigos del catalogo maestro que aplican a un tenant: los del nucleo
    mas los de modulos activos (RF-10/RN04). Unico lugar donde vive esa regla;
    la consumen tanto el resolutor de permisos como la validacion de RF-10 y
    RF-12, que no pueden dejar elegir permisos de un modulo inactivo."""
    with connection.cursor() as cursor:
        cursor.execute(_SQL_CATALOGO, {"tenant": tenant_id, "nucleo": DOMINIO_NUCLEO})
        return frozenset(fila[0] for fila in cursor.fetchall())


class PermissionDeniedError(Exception):
    """El usuario esta autenticado pero le falta el permiso -> HTTP 403.
    Pensado para los servicios, que no pueden devolver JsonResponse."""

    def __init__(self, codigo, detail=None):
        self.codigo = codigo
        self.detail = detail or "No cuenta con permisos para esta operacion"
        super().__init__(self.detail)

    def to_dict(self):
        return {"detail": self.detail, "permiso_requerido": self.codigo}


class PermissionResolver:
    """Conjunto efectivo de permisos del usuario de una peticion.

    Se resuelve contra la base de datos y NO contra el JWT: RF-12/RN01 exige
    que un cambio de permisos surta efecto en la siguiente peticion de
    cualquier usuario con ese rol, sin esperar a que expire el token. Los
    permisos que /api/core/me/ expone son informativos para pintar la UI.

    Se cachea en el propio request, asi que son cero consultas si el endpoint
    no comprueba nada y exactamente una si comprueba uno o veinte permisos.
    """

    def __init__(self, request):
        self.request = request
        self._cargado = False
        self._codigos = frozenset()
        self._codigos_catalogo = None
        self._es_admin = False

    @classmethod
    def for_request(cls, request):
        if not hasattr(request, "_permission_resolver"):
            request._permission_resolver = cls(request)
        return request._permission_resolver

    def _cargar(self):
        if self._cargado:
            return

        # El tenant sale del slug del JWT, nunca del payload de la peticion:
        # es el mismo aislamiento que aplica tenant_scoped() en las consultas.
        tenant = get_tenant(self.request)
        usuario_id = uuid.UUID(str(self.request.usuario_id))

        with connection.cursor() as cursor:
            cursor.execute(
                _SQL_PERMISOS,
                {"tenant": tenant.id, "usuario": usuario_id, "nucleo": DOMINIO_NUCLEO},
            )
            filas = cursor.fetchall()

        # RF-12/RN02: TENANT_ADMIN es un rol protegido que no puede perder
        # permisos, asi que no se le siembran filas en rol_permiso; se
        # reconoce por es_sistema. El bypass no atraviesa el aislamiento
        # multi-tenant: la consulta ya acota los roles a r.tenant_id.
        self._es_admin = any(fila[0] for fila in filas)
        # RF-14/RN01: privilegios = union de los permisos de todos los roles.
        self._codigos = frozenset(fila[1] for fila in filas if fila[1])
        self._cargado = True

    @property
    def codigos(self):
        """Conjunto efectivo de codigos. Para un rol con bypass se expande al
        catalogo aplicable al tenant: si no, /api/core/me/ le devolveria una
        lista vacia al frontend de un TENANT_ADMIN y este ocultaria toda la
        interfaz pese a que el backend le permite todo.

        Solo lo consume /me/; las comprobaciones de autorizacion usan tiene(),
        que resuelve el bypass sin necesitar esta expansion.
        """
        self._cargar()
        if not self._es_admin:
            return self._codigos

        if self._codigos_catalogo is None:
            self._codigos_catalogo = codigos_aplicables(get_tenant(self.request).id)
        return self._codigos_catalogo

    @property
    def es_admin(self):
        self._cargar()
        return self._es_admin

    def tiene(self, codigo):
        self._cargar()
        return self._es_admin or codigo in self._codigos


def tiene_permiso(request, codigo):
    """Predicado directo. Puede lanzar Tenant.DoesNotExist si el slug del JWT
    ya no corresponde a un tenant real (sesion obsoleta)."""
    return PermissionResolver.for_request(request).tiene(codigo)


def exigir_permiso(request, codigo):
    """Helper para los servicios, que no devuelven respuestas HTTP:

        exigir_permiso(request, "compras:ordenes:aprobar")

    Lanza PermissionDeniedError, que las vistas traducen a 403. Se usa cuando
    el permiso depende de los datos y no solo del endpoint (por ejemplo,
    autorizar una operacion solo si supera cierto umbral); cuando basta con el
    endpoint, PermissionRequiredMixin lo resuelve sin tocar el servicio.
    """
    if not tiene_permiso(request, codigo):
        raise PermissionDeniedError(codigo)


class PermissionRequiredMixin(LoginRequiredMixin):
    """Autorizacion declarativa: la vista no ejecuta logica, solo declara que
    permiso pide cada metodo HTTP.

        class ProductoListCreateView(CatalogListCreateView):
            permisos = {
                "GET":  "inventario:productos:leer",
                "POST": "inventario:productos:crear",
            }

    o, si todos los metodos piden lo mismo:

        permiso_requerido = "core:roles:leer"

    Falla cerrado a proposito: si un metodo de METODOS_PROTEGIDOS no tiene
    permiso declarado, responde 403 en vez de dejar pasar. Olvidar una
    declaracion cierra el endpoint, no lo abre. Los endpoints que la ERS
    define para "todos los usuarios autenticados" (RF-17) no usan este mixin
    sino LoginRequiredMixin directamente.

    Cuando la autorizacion depende del objeto y no solo del endpoint, se
    sobrescribe permiso_para() y se devuelve SIN_PERMISO para el caso en que
    el actor opera sobre su propio registro:

        def permiso_para(self, metodo):
            if self.kwargs.get("pk") == str(self.request.usuario_id):
                return SIN_PERMISO
            return super().permiso_para(metodo)

    En ese caso la vista o el servicio deben acotar lo que el propietario
    puede tocar; saltarse el permiso no es saltarse las reglas de negocio.
    """

    permisos = {}
    permiso_requerido = None

    def permiso_para(self, metodo):
        if self.permisos:
            return self.permisos.get(metodo)
        return self.permiso_requerido

    def dispatch(self, request, *args, **kwargs):
        if _is_authenticated(request) and request.method in METODOS_PROTEGIDOS:
            codigo = self.permiso_para(request.method)
            if codigo is SIN_PERMISO:
                return super().dispatch(request, *args, **kwargs)
            if codigo is None:
                return JsonResponse(
                    PermissionDeniedError(None).to_dict(), status=403
                )
            from core.models import Tenant

            try:
                autorizado = tiene_permiso(request, codigo)
            except (Tenant.DoesNotExist, ValidationError, ValueError):
                # El JWT es valido pero su usuario/tenant ya no existe o su id
                # esta malformado: sesion obsoleta, 401 y no 403.
                return JsonResponse(UNAUTHORIZED, status=401)

            if not autorizado:
                return JsonResponse(PermissionDeniedError(codigo).to_dict(), status=403)

        return super().dispatch(request, *args, **kwargs)
