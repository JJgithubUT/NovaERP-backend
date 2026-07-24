import datetime
import json

import jwt
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.models import Modulo, Permiso, Rol, Tenant, Usuario
from core.services import rol_service as rol_svc
from core.services import usuario_service as usuario_svc
from core.services.auth_service import LoginError, intentar_login
from core.utils.auth import UNAUTHORIZED, LoginRequiredMixin, tenant_scoped
from core.utils.errors import BusinessRuleError
from core.utils.permissions import (
    PermissionDeniedError,
    PermissionRequiredMixin,
    PermissionResolver,
)
from core.utils.views import CatalogDetailView, CatalogListCreateView


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(View):
    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"mensaje": "JSON invalido."}, status=400)

        tenant_slug = data.get("tenant_slug")
        correo = data.get("correo")
        password = data.get("password")
        if not tenant_slug or not correo or not password:
            return JsonResponse(
                {"mensaje": "tenant_slug, correo y password son obligatorios."},
                status=400,
            )

        try:
            result = intentar_login(tenant_slug, correo, password)
        except LoginError as e:
            return JsonResponse({"mensaje": str(e)}, status=401)

        payload = {
            "usuario_id": str(result["usuario_id"]),
            "tenant_slug": tenant_slug,
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=8),
        }
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

        return JsonResponse({"token": token, "mensaje": result["mensaje"]})


# ---------------------------------------------------------------- RF-05

@method_decorator(csrf_exempt, name="dispatch")
class UsuarioCreateView(PermissionRequiredMixin, View):
    """RF-05: alta de usuario dentro del tenant. Solo POST; el directorio
    paginado con sus filtros y ordenamientos es RF-06."""

    permiso_requerido = "core:usuarios:crear"

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            usuario, token = usuario_svc.crear_usuario(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

        payload = usuario_svc.serialize_usuario(usuario)
        # El token solo existe en claro en este instante (en la tabla se
        # guarda su hash). Se entrega al TENANT_ADMIN que dio el alta para que
        # pueda pasar el enlace mientras el worker de correo de RF-25 no exista.
        payload["activacion_token"] = token
        return JsonResponse(payload, status=201)


@method_decorator(csrf_exempt, name="dispatch")
class ActivarUsuarioView(View):
    """Flujo de activacion de RF-05. Endpoint publico a proposito: el usuario
    aun no puede autenticarse (RN03) y la autorizacion la da el token de un
    solo uso, no un JWT. Nunca pasa por LoginView (RF-16)."""

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            usuario = usuario_svc.activar_usuario(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        return JsonResponse(usuario_svc.serialize_usuario(usuario))


class MeView(LoginRequiredMixin, View):
    """Contexto del usuario autenticado: perfil, tenant, roles activos,
    permisos derivados de esos roles y modulos habilitados para el tenant.
    Pensado para que el frontend lo consuma justo despues del login."""

    def get(self, request):
        try:
            usuario = Usuario.objects.select_related("tenant", "tenant__plan").get(
                id=request.usuario_id, tenant__slug=request.tenant_slug
            )
        except (Usuario.DoesNotExist, ValidationError, ValueError):
            # El JWT es valido pero ya no corresponde a un usuario/tenant
            # real (usuario borrado, tenant renombrado, id malformado, etc).
            return JsonResponse(UNAUTHORIZED, status=401)

        tenant = usuario.tenant

        roles = list(
            Rol.objects.filter(
                core_usuario_rol_rol_set__usuario_id=usuario.id,
                activo=True,
            ).distinct().order_by("nombre")
        )

        # Conjunto efectivo resuelto por el mismo motor que autoriza: incluye
        # el bypass de rol de sistema y excluye los permisos inertes por
        # modulo desactivado (RF-10/RN04). Es informativo para la UI; la
        # autorizacion real siempre se resuelve en cada peticion (RF-12/RN01).
        resolver = PermissionResolver.for_request(request)
        permisos = list(
            Permiso.objects.filter(codigo__in=resolver.codigos)
            .order_by("codigo")
            .values("codigo", "dominio", "recurso", "accion")
        )

        modulos = list(
            Modulo.objects.filter(
                core_tenant_modulo_modulo_set__tenant=tenant,
                core_tenant_modulo_modulo_set__activo=True,
            )
            .distinct()
            .order_by("fase", "nombre")
            .values("codigo", "nombre", "fase")
        )

        return JsonResponse(
            {
                "usuario": {
                    "id": str(usuario.id),
                    "nombre_completo": usuario.nombre_completo,
                    "correo": usuario.correo,
                    "estado": usuario.estado,
                    "mfa_enrolado": usuario.mfa_enrolado,
                },
                "tenant": {
                    "id": str(tenant.id),
                    "slug": tenant.slug,
                    "razon_social": tenant.razon_social,
                    "estado": tenant.estado,
                    "plan": tenant.plan.codigo,
                },
                "roles": [r.nombre for r in roles],
                "es_admin": resolver.es_admin,
                "permisos": permisos,
                "modulos": modulos,
            }
        )


# ---------------------------------------------------- RF-10 / RF-11 / RF-12 / RF-13

class RolListCreateView(CatalogListCreateView):
    """GET: catalogo de roles del tenant con sus permisos, indicador de
    permisos inertes y numero de usuarios asignados (RF-11).
    POST: alta de rol personalizado con permisos del catalogo maestro (RF-10)."""

    model = Rol
    permisos = {"GET": "core:roles:leer", "POST": "core:roles:crear"}
    search_fields = ("nombre",)
    ordering = ("nombre",)
    serialize_fn = staticmethod(rol_svc.serialize_rol)
    create_fn = staticmethod(rol_svc.crear_rol)


class RolDetailView(CatalogDetailView):
    """PATCH: modificar nombre y conjunto de permisos (RF-12).
    DELETE: baja logica del rol (RF-13)."""

    model = Rol
    permisos = {"PATCH": "core:roles:editar", "DELETE": "core:roles:eliminar"}
    serialize_fn = staticmethod(rol_svc.serialize_rol)
    edit_fn = staticmethod(rol_svc.editar_rol)
    deactivate_fn = staticmethod(rol_svc.desactivar_rol)


class PermisoCatalogoView(PermissionRequiredMixin, View):
    """RF-11/CA01: catalogo maestro de permisos agrupado por dominio, con los
    de modulos desactivados marcados como inertes (CA02). Es el origen del
    selector de permisos de RF-10 y RF-12."""

    permiso_requerido = "core:roles:leer"

    def get(self, request):
        try:
            return JsonResponse(rol_svc.catalogo_permisos(request))
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)


# ---------------------------------------------------------- RF-14 / RF-15

class _UsuarioRolBase(PermissionRequiredMixin, View):
    def get_usuario(self, request, pk):
        return tenant_scoped(Usuario.objects.select_related("tenant"), request).get(pk=pk)


@method_decorator(csrf_exempt, name="dispatch")
class UsuarioRolCreateView(_UsuarioRolBase):
    """RF-14: asigna uno o varios roles activos del tenant a un usuario."""

    permiso_requerido = "core:asignaciones:crear"

    def post(self, request, pk):
        try:
            usuario = self.get_usuario(request, pk)
        except (Usuario.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            usuario = rol_svc.asignar_roles(usuario, data, request)
        except PermissionDeniedError as e:
            return JsonResponse(e.to_dict(), status=403)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        return JsonResponse(rol_svc.roles_de_usuario(usuario))


@method_decorator(csrf_exempt, name="dispatch")
class UsuarioRolDeleteView(_UsuarioRolBase):
    """RF-15: revoca un rol de un usuario."""

    permiso_requerido = "core:asignaciones:eliminar"

    def delete(self, request, pk, rol_pk):
        try:
            usuario = self.get_usuario(request, pk)
        except (Usuario.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        try:
            usuario = rol_svc.revocar_rol(usuario, rol_pk, request)
        except PermissionDeniedError as e:
            return JsonResponse(e.to_dict(), status=403)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        except (ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        return JsonResponse(rol_svc.roles_de_usuario(usuario))
