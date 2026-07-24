import json

from django.core.exceptions import ValidationError
from django.db.models import Max
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.models import LogAuditoria, Modulo, Permiso, Rol, Tenant, Usuario
from core.services import auditoria_service as auditoria_svc
from core.services import auth_service
from core.services import config_service as config_svc
from core.services import rol_service as rol_svc
from core.services import session_service
from core.services import usuario_service as usuario_svc
from core.services.auth_service import LoginError
from core.utils.auth import UNAUTHORIZED, LoginRequiredMixin, tenant_scoped
from core.utils.errors import BusinessRuleError
from core.utils.permissions import (
    SIN_PERMISO,
    PermissionDeniedError,
    PermissionRequiredMixin,
    PermissionResolver,
)
from core.utils.views import CatalogDetailView, CatalogListCreateView


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(View):
    """RF-16 fase 1: valida password y devuelve un reto de segundo factor
    (password -> reto OTP -> sesion). Vista fina; toda la orquestacion vive en
    auth_service.autenticar. El token de sesion se emite en la fase 2 (OtpView)."""

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

        # El tenant se resuelve para el contexto de auditoria y la expiracion;
        # si no existe, el validador falla igual con el mensaje generico de
        # RN04 (sin revelar la inexistencia).
        tenant = Tenant.objects.filter(slug=tenant_slug).first()

        try:
            reto = auth_service.autenticar(request, tenant, tenant_slug, correo, password)
        except LoginError as e:
            return JsonResponse({"mensaje": str(e)}, status=401)

        return JsonResponse(reto)


@method_decorator(csrf_exempt, name="dispatch")
class OtpView(View):
    """RF-16 fase 2: valida el codigo del segundo factor contra el reto emitido
    en la fase 1 y, si es correcto, completa el login emitiendo el JWT de
    sesion. Endpoint publico a proposito: el usuario aun no tiene sesion; la
    autorizacion la da el reto de un solo flujo, no un JWT de sesion."""

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"mensaje": "JSON invalido."}, status=400)

        reto = data.get("reto")
        codigo = data.get("codigo")
        if not reto or not codigo:
            return JsonResponse({"mensaje": "reto y codigo son obligatorios."}, status=400)

        try:
            token, mensaje = auth_service.validar_otp(request, reto, codigo)
        except LoginError as e:
            return JsonResponse({"mensaje": str(e)}, status=401)

        return JsonResponse({"token": token, "mensaje": mensaje})


# ---------------------------------------------------------------- RF-05

@method_decorator(csrf_exempt, name="dispatch")
class UsuarioListCreateView(PermissionRequiredMixin, View):
    """RF-06 (GET, directorio) y RF-05 (POST, alta). Cada metodo pide su
    permiso: leer para consultar, crear para dar de alta."""

    permisos = {"GET": "core:usuarios:leer", "POST": "core:usuarios:crear"}

    def get(self, request):
        try:
            return JsonResponse(usuario_svc.directorio_usuarios(request))
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

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
class UsuarioDetailView(PermissionRequiredMixin, View):
    """RF-07: edicion de usuario. La ERS define dos actores con alcances
    distintos, asi que la autorizacion es por objeto:

      · sobre un tercero  -> exige core:usuarios:editar (modificacion total)
      · sobre uno mismo   -> no exige permiso, pero el servicio limita los
                             campos a los datos personales

    No administra roles ni estado: eso vive en RF-14/RF-15 y RF-08."""

    permisos = {"PATCH": "core:usuarios:editar"}

    def permiso_para(self, metodo):
        if str(self.kwargs.get("pk")) == str(self.request.usuario_id):
            return SIN_PERMISO
        return super().permiso_para(metodo)

    def patch(self, request, pk):
        try:
            usuario = tenant_scoped(
                Usuario.objects.select_related("tenant"), request
            ).get(pk=pk)
        except (Usuario.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            usuario, token = usuario_svc.editar_usuario(usuario, data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        payload = usuario_svc.serialize_usuario(usuario)
        if token:
            # RF-07/CA04: mismo criterio que en el alta (RF-05). El enlace de
            # confirmacion solo existe en claro en este instante; se entrega a
            # quien edito mientras no exista el worker de correo de RF-25.
            payload["verificacion_token"] = token
        return JsonResponse(payload)


@method_decorator(csrf_exempt, name="dispatch")
class UsuarioResetMfaView(PermissionRequiredMixin, View):
    """RF-07 / RF-16 RN07: el TENANT_ADMIN resetea el segundo factor de un
    usuario (perdida de dispositivo). Accion de seguridad exclusiva del admin,
    separada de la edicion de datos personales: exige core:usuarios:reset_mfa
    siempre, sin el bypass de auto-edicion (un usuario no se resetea el MFA a si
    mismo por esta via). Tras el reset, el usuario re-enrola en su proximo login."""

    permiso_requerido = "core:usuarios:reset_mfa"

    def post(self, request, pk):
        try:
            usuario = tenant_scoped(
                Usuario.objects.select_related("tenant"), request
            ).get(pk=pk)
        except (Usuario.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        usuario = usuario_svc.resetear_mfa(usuario, request)
        return JsonResponse(usuario_svc.serialize_usuario(usuario))


# ---------------------------------------------------------------- RF-08

@method_decorator(csrf_exempt, name="dispatch")
class UsuarioSuspenderView(PermissionRequiredMixin, View):
    """RF-08: el TENANT_ADMIN suspende un usuario. Cierra sus sesiones y bloquea
    su login. Accion exclusiva del admin (permiso, sin bypass de propiedad)."""

    permiso_requerido = "core:usuarios:suspender"

    def post(self, request, pk):
        try:
            usuario = tenant_scoped(Usuario.objects.select_related("tenant"), request).get(pk=pk)
        except (Usuario.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            usuario = usuario_svc.suspender_usuario(usuario, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(usuario_svc.serialize_usuario(usuario))


@method_decorator(csrf_exempt, name="dispatch")
class UsuarioReactivarView(PermissionRequiredMixin, View):
    """RF-08: el TENANT_ADMIN reactiva un usuario suspendido; conserva sus roles."""

    permiso_requerido = "core:usuarios:suspender"

    def post(self, request, pk):
        try:
            usuario = tenant_scoped(Usuario.objects.select_related("tenant"), request).get(pk=pk)
        except (Usuario.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            usuario = usuario_svc.reactivar_usuario(usuario, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(usuario_svc.serialize_usuario(usuario))


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


# ---------------------------------------------------------------- RF-18

@method_decorator(csrf_exempt, name="dispatch")
class RecuperarPasswordView(View):
    """RF-18: solicitud de restablecimiento. Publico. SIEMPRE responde el mismo
    mensaje (RN02/CA01): no revela si el correo existe."""

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"mensaje": "JSON invalido."}, status=400)

        usuario_svc.solicitar_restablecimiento(data, request)
        return JsonResponse({"mensaje": usuario_svc.MSG_RECUPERAR})


@method_decorator(csrf_exempt, name="dispatch")
class RestablecerPasswordView(View):
    """RF-18: confirma el restablecimiento con el token de un solo uso. Publico
    (el usuario no puede autenticarse: olvido su contrasena). Al terminar,
    todas sus sesiones previas quedan invalidadas (RN03)."""

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            usuario_svc.restablecer_password(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        return JsonResponse({"mensaje": "Contrasena restablecida. Inicie sesion de nuevo."})


# ---------------------------------------------------------------- RF-17

@method_decorator(csrf_exempt, name="dispatch")
class LogoutView(LoginRequiredMixin, View):
    """RF-17: cierra la sesion en curso. Revoca la sesion del jti actual; el
    middleware hara que el mismo token devuelva 401 de inmediato (CA01).
    Idempotente (CA02): cerrar dos veces no genera error."""

    def post(self, request):
        session_service.revocar_una(request, request.session_jti, request.usuario_id)
        return JsonResponse({"detail": "Sesion cerrada."})


# ---------------------------------------------------------------- RF-19

@method_decorator(csrf_exempt, name="dispatch")
class SesionListView(LoginRequiredMixin, View):
    """RF-19/CA01: el usuario lista SOLO sus propias sesiones activas (RN01:
    nadie ve las de otro). Autogestion: no exige permiso, solo sesion."""

    def get(self, request):
        sesiones = session_service.listar_activas(request.usuario_id)
        return JsonResponse(
            {
                "results": [
                    session_service.serialize_sesion(s, request.session_jti)
                    for s in sesiones
                ]
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class SesionCerrarOtrasView(LoginRequiredMixin, View):
    """RF-19/CA03: cierra todas las sesiones propias excepto la actual."""

    def post(self, request):
        n = session_service.revocar_otras(
            request, request.usuario_id, request.session_jti
        )
        return JsonResponse({"revocadas": n})


@method_decorator(csrf_exempt, name="dispatch")
class SesionDetailView(LoginRequiredMixin, View):
    """RF-19/CA02: cierra una sesion propia por su jti (logout remoto).
    Acotada al usuario del JWT: un jti ajeno no revoca nada (RN01) y no revela
    su existencia."""

    def delete(self, request, jti):
        n = session_service.revocar_una(request, jti, request.usuario_id)
        return JsonResponse({"revocada": n > 0})


@method_decorator(csrf_exempt, name="dispatch")
class UsuarioCerrarSesionesView(PermissionRequiredMixin, View):
    """RF-19/RN02: el TENANT_ADMIN fuerza el cierre de TODAS las sesiones de un
    usuario de su tenant. Solo devuelve el conteo, sin detalle de dispositivos
    (privacidad). Accion exclusiva del admin (permiso, sin bypass de propiedad)."""

    permiso_requerido = "core:sesiones:revocar"

    def post(self, request, pk):
        try:
            usuario = tenant_scoped(Usuario.objects.all(), request).get(pk=pk)
        except (Usuario.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        n = session_service.revocar_todas_de_usuario(request, usuario.id)
        return JsonResponse({"revocadas": n})


# ------------------------------------------------------- RF-21 / RF-23 / RF-24

@method_decorator(csrf_exempt, name="dispatch")
class BitacoraView(PermissionRequiredMixin, View):
    """RF-21: consulta de la bitacora de auditoria del tenant. Solo lectura."""

    permiso_requerido = "core:bitacora:leer"

    def get(self, request):
        try:
            return JsonResponse(auditoria_svc.consultar_bitacora(request))
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)


@method_decorator(csrf_exempt, name="dispatch")
class BitacoraExportView(PermissionRequiredMixin, View):
    """RF-24: exporta la consulta de la bitacora a CSV o PDF (?formato=). La
    exportacion se audita como un evento propio (EXPORT)."""

    permiso_requerido = "core:bitacora:exportar"

    def get(self, request):
        formato = (request.GET.get("formato") or "csv").strip().lower()
        if formato not in auditoria_svc.FORMATOS_EXPORT:
            return JsonResponse({"detail": "formato debe ser csv o pdf."}, status=400)
        try:
            return auditoria_svc.exportar_bitacora(request, formato)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)


@method_decorator(csrf_exempt, name="dispatch")
class ReporteActividadView(PermissionRequiredMixin, View):
    """RF-23: reporte consolidado de actividad de usuarios del tenant. Sin
    ?formato= devuelve JSON paginado; ?formato=csv|pdf exporta el reporte
    completo a archivo (CA03)."""

    permiso_requerido = "core:reportes:leer"

    def get(self, request):
        formato = (request.GET.get("formato") or "").strip().lower()
        try:
            if formato in auditoria_svc.FORMATOS_EXPORT:
                return auditoria_svc.exportar_actividad(request, formato)
            return JsonResponse(auditoria_svc.reporte_actividad(request))
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)


# ---------------------------------------------------------------- RF-22

@method_decorator(csrf_exempt, name="dispatch")
class ConfigSeguridadView(PermissionRequiredMixin, View):
    """RF-22: consulta (leer) y edicion (editar) de las politicas de seguridad
    del tenant, cada metodo con su permiso."""

    permisos = {
        "GET": "core:politicas:leer",
        "PUT": "core:politicas:editar",
        "PATCH": "core:politicas:editar",
    }

    def get(self, request):
        try:
            cfg = config_svc.obtener_config(request)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)
        return JsonResponse(config_svc.serialize_config(cfg))

    def put(self, request):
        return self._guardar(request)

    def patch(self, request):
        return self._guardar(request)

    def _guardar(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            cfg = config_svc.actualizar_config(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)
        return JsonResponse(config_svc.serialize_config(cfg))


class MeView(LoginRequiredMixin, View):
    """RF-09: contexto del usuario autenticado: perfil, tenant, roles VIGENTES
    (CA04), permisos derivados y modulos habilitados. Nunca expone
    contrasena/hash/tokens/OTP (CA02). Incluye el ultimo acceso (CA03)."""

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

        # CA03: fecha/hora del ultimo login exitoso. Se toma del evento LOGIN
        # de la bitacora (RF-16 lo emite en cada login), que es la fuente de
        # verdad; incluye el login de la sesion en curso.
        ultimo_acceso = (
            LogAuditoria.objects.filter(usuario_id=usuario.id, operacion="LOGIN")
            .aggregate(m=Max("ocurrido_en"))["m"]
        )

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
                    "ultimo_acceso": ultimo_acceso.isoformat() if ultimo_acceso else None,
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
