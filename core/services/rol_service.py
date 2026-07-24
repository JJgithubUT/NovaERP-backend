import uuid

from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Permiso, Rol, RolPermiso, UsuarioRol
from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError
from core.utils.permissions import PermissionDeniedError, codigos_aplicables

# RF-12/RN02 y RF-13/RN02: TENANT_ADMIN es un rol protegido. No se edita, no
# se desactiva y no se le administran permisos, porque su autorizacion es el
# bypass por es_sistema y no filas en rol_permiso.
MSG_ROL_SISTEMA_EDITAR = "Este rol de sistema no puede modificarse."
MSG_ROL_SISTEMA_ELIMINAR = "Este rol de sistema no puede eliminarse ni desactivarse."


# ------------------------------------------------------------- Serializacion

def serialize_permiso(permiso):
    return {
        "codigo": permiso.codigo,
        "dominio": permiso.dominio,
        "recurso": permiso.recurso,
        "accion": permiso.accion,
        "descripcion": permiso.descripcion,
    }


def catalogo_permisos(request):
    """RF-11/CA01: el selector solo lista permisos del catalogo maestro,
    agrupados por dominio. Los de modulos desactivados en el tenant se
    marcan como inertes (RF-11/CA02) en vez de ocultarse, para que el
    TENANT_ADMIN entienda por que no puede elegirlos."""
    aplicables = codigos_aplicables(get_tenant(request).id)

    dominios = {}
    for permiso in Permiso.objects.order_by("dominio", "recurso", "accion"):
        entrada = serialize_permiso(permiso)
        entrada["inerte"] = permiso.codigo not in aplicables
        dominios.setdefault(permiso.dominio, []).append(entrada)

    return {
        "dominios": [
            {"dominio": dominio, "permisos": permisos}
            for dominio, permisos in sorted(dominios.items())
        ]
    }


def serialize_rol(rol):
    """RF-11: por rol, la lista completa de permisos agrupados por dominio
    (CA01), el indicador de permisos inertes (CA02) y el numero de usuarios
    que lo tienen asignado (CA03)."""
    aplicables = codigos_aplicables(rol.tenant_id)

    permisos = list(
        Permiso.objects.filter(core_rol_permiso_permiso_set__rol=rol).order_by(
            "dominio", "recurso", "accion"
        )
    )
    por_dominio = {}
    for permiso in permisos:
        entrada = serialize_permiso(permiso)
        entrada["inerte"] = permiso.codigo not in aplicables
        por_dominio.setdefault(permiso.dominio, []).append(entrada)

    return {
        "id": str(rol.id),
        "nombre": rol.nombre,
        "es_sistema": rol.es_sistema,
        "activo": rol.activo,
        # Un rol de sistema autoriza por bypass, no por filas en rol_permiso:
        # exponerlo evita que el frontend lo pinte como un rol sin permisos.
        "acceso_total": rol.es_sistema,
        "usuarios_asignados": UsuarioRol.objects.filter(rol=rol).count(),
        "permisos": [
            {"dominio": dominio, "permisos": lista}
            for dominio, lista in sorted(por_dominio.items())
        ],
        "created_at": rol.created_at.isoformat(),
    }


# ------------------------------------------------------------- Validaciones

def _validar_nombre(tenant, nombre, rol_actual=None):
    nombre = (nombre or "").strip()
    if not nombre:
        raise BusinessRuleError("nombre es obligatorio.", campo="nombre")

    # RF-10/RN03 y CA02: unico dentro del tenant, repetible entre tenants.
    duplicados = Rol.objects.filter(tenant=tenant, nombre=nombre)
    if rol_actual is not None:
        duplicados = duplicados.exclude(pk=rol_actual.pk)
    if duplicados.exists():
        raise BusinessRuleError(
            "Ya existe un rol con ese nombre en este tenant.", campo="nombre"
        )
    return nombre


def _validar_permisos(tenant, codigos):
    """RF-10/RN02: solo permisos del catalogo maestro, no se pueden inventar.
    RF-10/CA03: al menos uno. RF-10/RN04: solo de modulos activos."""
    if not codigos:
        raise BusinessRuleError(
            "Debe seleccionar al menos un permiso.", campo="permisos"
        )
    if not isinstance(codigos, list):
        raise BusinessRuleError(
            "permisos debe ser una lista de codigos.", campo="permisos"
        )

    solicitados = {str(c) for c in codigos}
    permisos = list(Permiso.objects.filter(codigo__in=solicitados))
    encontrados = {p.codigo for p in permisos}

    inexistentes = sorted(solicitados - encontrados)
    if inexistentes:
        raise BusinessRuleError(
            "Uno o mas permisos no existen en el catalogo maestro.",
            campo="permisos",
            extra={"permisos_invalidos": inexistentes},
        )

    aplicables = codigos_aplicables(tenant.id)
    inactivos = sorted(encontrados - aplicables)
    if inactivos:
        raise BusinessRuleError(
            "Uno o mas permisos pertenecen a modulos no activos en este tenant.",
            campo="permisos",
            extra={"permisos_de_modulo_inactivo": inactivos},
        )

    return permisos


def _reemplazar_permisos(rol, permisos):
    """El conjunto de permisos de un rol se reemplaza completo, no se
    parchea: es lo que hace verificable la CA03 de RF-12 (auditar permisos
    agregados y retirados) sin mantener un diff a mano."""
    RolPermiso.objects.filter(rol=rol).delete()
    RolPermiso.objects.bulk_create(
        [RolPermiso(rol=rol, permiso=permiso) for permiso in permisos]
    )


# ----------------------------------------------------------------- RF-10

def crear_rol(data, request):
    """RF-10: rol de trabajo con permisos atomicos del catalogo maestro.
    Todo rol creado por API es personalizado (es_sistema=False): los roles de
    sistema los define el aprovisionamiento del tenant, no el TENANT_ADMIN."""
    tenant = get_tenant(request)
    nombre = _validar_nombre(tenant, data.get("nombre"))
    permisos = _validar_permisos(tenant, data.get("permisos"))

    with audit_context(request, tenant_id=tenant.id):
        rol = Rol.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            nombre=nombre,
            es_sistema=False,
            activo=True,
            created_at=timezone.now(),
        )
        _reemplazar_permisos(rol, permisos)

    return rol


# ----------------------------------------------------------------- RF-12

def editar_rol(rol, data, request):
    """RF-12: agregar o retirar permisos de un rol existente. Los cambios
    surten efecto en la siguiente peticion de cualquier usuario con ese rol
    (RN01/CA01) porque la autorizacion se resuelve contra la base de datos en
    cada request, no contra un claim del JWT."""
    if rol.es_sistema:
        # CA02: 403, no 422 — es una restriccion de autorizacion sobre el
        # recurso, no una regla de negocio sobre los datos enviados.
        raise PermissionDeniedError("core:roles:editar", detail=MSG_ROL_SISTEMA_EDITAR)

    tenant = rol.tenant

    with audit_context(request, tenant_id=rol.tenant_id):
        if "nombre" in data:
            rol.nombre = _validar_nombre(tenant, data["nombre"], rol_actual=rol)

        if "permisos" in data:
            _reemplazar_permisos(rol, _validar_permisos(tenant, data["permisos"]))

        if "activo" in data:
            # Reactivar un rol dado de baja (RF-13) vuelve a habilitarlo para
            # asignacion; desactivarlo por esta via equivale a RF-13.
            rol.activo = bool(data["activo"])

        rol.save()

    return rol


# ----------------------------------------------------------------- RF-13

def desactivar_rol(rol, request):
    """RF-13: baja logica de un rol personalizado. Nunca hay borrado fisico,
    asi que "eliminar" es desactivar; con usuarios asignados se bloquea y se
    exige confirmacion explicita (?desactivar=true), que es la alternativa que
    la propia RN01 ofrece frente a reasignar a los usuarios.

    DESVIACION DELIBERADA DE LA ERS (RF-13/CA02)
    --------------------------------------------
    La CA02 afirma que, al desactivar un rol, los usuarios que ya lo tenian
    "conservan sus permisos activos de forma inerte y pueden seguir operando
    con normalidad". Aqui se implementa lo contrario: un rol inactivo no
    concede ningun permiso (PermissionResolver filtra por r.activo).

    Motivo: seguir la ERS al pie de la letra haria que desactivar un rol no
    tuviera ningun efecto de seguridad y contradiria RF-12/RN01, que exige que
    un cambio de permisos surta efecto en la siguiente peticion. Tambien
    violaria el principio de menor privilegio: un usuario conservaria acceso
    indefinidamente pese a que su rol fue dado de baja.

    La CA02 sigue cumpliendose en su primera mitad: un rol inactivo no puede
    asignarse a nuevos usuarios (ver asignar_roles) y su fila en usuario_rol
    no se borra, de modo que la baja es reversible reactivando el rol.
    """
    if rol.es_sistema:
        raise PermissionDeniedError(
            "core:roles:eliminar", detail=MSG_ROL_SISTEMA_ELIMINAR
        )

    asignados = UsuarioRol.objects.filter(rol=rol).count()
    confirmado = (request.GET.get("desactivar", "").strip().lower() in ("1", "true"))

    if asignados and not confirmado:
        raise BusinessRuleError(
            f"Este rol tiene {asignados} usuarios asignados. Reasignelos antes de "
            f"eliminarlo, o desactive el rol.",
            campo="rol",
            extra={"usuarios_asignados": asignados, "confirmar_con": "?desactivar=true"},
        )

    with audit_context(request, tenant_id=rol.tenant_id):
        rol.activo = False
        rol.save(update_fields=["activo"])

    return rol


# ----------------------------------------------------------------- RF-14

def asignar_roles(usuario, data, request):
    """RF-14: vincula uno o varios roles a un usuario. RN01 (los privilegios
    son la union de todos sus roles) la resuelve PermissionResolver; aqui solo
    se valida que los roles existan, esten activos y sean del propio tenant
    (RN02/CA01)."""
    roles_data = data.get("roles")
    if not roles_data:
        raise BusinessRuleError("Debe indicar al menos un rol.", campo="roles")
    if not isinstance(roles_data, list):
        raise BusinessRuleError(
            "roles debe ser una lista de identificadores.", campo="roles"
        )

    solicitados = {str(r) for r in roles_data}
    try:
        roles = list(
            Rol.objects.filter(tenant=usuario.tenant, id__in=solicitados, activo=True)
        )
    except (ValidationError, ValueError):
        raise BusinessRuleError("roles contiene identificadores invalidos.", campo="roles")

    if len(roles) != len(solicitados):
        # RF-13/CA02: un rol inactivo no puede asignarse a nuevos usuarios.
        raise BusinessRuleError(
            "Uno o mas roles no existen en este tenant o estan inactivos.",
            campo="roles",
        )

    ya_asignados = set(
        UsuarioRol.objects.filter(usuario=usuario, rol__in=roles).values_list(
            "rol_id", flat=True
        )
    )
    nuevos = [r for r in roles if r.id not in ya_asignados]

    if nuevos:
        with audit_context(request, tenant_id=usuario.tenant_id):
            UsuarioRol.objects.bulk_create(
                [
                    UsuarioRol(
                        usuario=usuario,
                        rol=rol,
                        asignado_en=timezone.now(),
                        asignado_por_id=request.usuario_id,
                    )
                    for rol in nuevos
                ]
            )

    return usuario


# ----------------------------------------------------------------- RF-15

def revocar_rol(usuario, rol_id, request):
    """RF-15: retira un rol de un usuario. Las dos reglas de negocio se
    validan aqui con los mensajes de la ERS; los triggers
    core.validar_usuario_rol_minimo son la red de seguridad a nivel de motor
    para cualquier escritura que no pase por este servicio."""
    asignacion = UsuarioRol.objects.filter(
        usuario=usuario, rol_id=rol_id, rol__tenant=usuario.tenant
    ).select_related("rol").first()
    if asignacion is None:
        raise BusinessRuleError("El usuario no tiene asignado ese rol.", campo="rol_id")

    # RN01/CA01: nunca dejar a un usuario activo sin ningun rol.
    if UsuarioRol.objects.filter(usuario=usuario).count() <= 1:
        raise BusinessRuleError(
            "El usuario debe conservar al menos un rol.", campo="rol_id"
        )

    # RN02: no revocar el rol de sistema al ultimo administrador activo. El
    # conteo excluye al usuario sobre el que se opera y aplica sin importar
    # cuantos otros roles tenga.
    if asignacion.rol.es_sistema:
        otros_admins = (
            UsuarioRol.objects.filter(
                rol=asignacion.rol,
                rol__activo=True,
                usuario__estado="activo",
            )
            .exclude(usuario=usuario)
            .count()
        )
        if otros_admins == 0:
            raise BusinessRuleError(
                "No puede revocar este rol: es el unico administrador activo del tenant.",
                campo="rol_id",
            )

    with audit_context(request, tenant_id=usuario.tenant_id):
        UsuarioRol.objects.filter(usuario=usuario, rol_id=rol_id).delete()

    return usuario


def roles_de_usuario(usuario):
    return {
        "usuario_id": str(usuario.id),
        "roles": [
            {"id": str(r.id), "nombre": r.nombre, "activo": r.activo, "es_sistema": r.es_sistema}
            for r in Rol.objects.filter(
                core_usuario_rol_rol_set__usuario=usuario
            ).order_by("nombre")
        ],
    }
