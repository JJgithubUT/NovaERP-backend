import json

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.models import Sysadmin, Tenant
from core.services import catalogo_service
from core.services import sysadmin_auth_service
from core.services import tenant_service
from core.services.sysadmin_auth_service import SysAdminLoginError
from core.utils.auth import UNAUTHORIZED, SysAdminRequiredMixin
from core.utils.errors import BusinessRuleError

# Portal de plataforma (SysAdmin). Superficie separada de la de tenant: sus
# tokens llevan typ='sysadmin', su sesion vive en core.sesion_sysadmin y sus
# vistas usan SysAdminRequiredMixin (no PermissionResolver). Fundacion para
# RF-01..04, que colgaran de aqui.


@method_decorator(csrf_exempt, name="dispatch")
class SysAdminLoginView(View):
    """Login del SysAdmin (una fase, sin MFA por ahora). Publico a proposito: el
    actor aun no tiene sesion. Vista fina; la orquestacion vive en
    sysadmin_auth_service.autenticar."""

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"mensaje": "JSON invalido."}, status=400)

        correo = data.get("correo")
        password = data.get("password")
        if not correo or not password:
            return JsonResponse(
                {"mensaje": "correo y password son obligatorios."}, status=400
            )

        try:
            token, mensaje = sysadmin_auth_service.autenticar(request, correo, password)
        except SysAdminLoginError as e:
            return JsonResponse({"mensaje": str(e)}, status=401)

        return JsonResponse({"token": token, "mensaje": mensaje})


@method_decorator(csrf_exempt, name="dispatch")
class SysAdminLogoutView(SysAdminRequiredMixin, View):
    """Cierra la sesion en curso del SysAdmin. Revoca el jti actual; el mismo
    token devuelve 401 de inmediato. Idempotente."""

    def post(self, request):
        sysadmin_auth_service.logout(request, request.session_jti, request.sysadmin_id)
        return JsonResponse({"detail": "Sesion cerrada."})


class SysAdminMeView(SysAdminRequiredMixin, View):
    """Contexto del SysAdmin autenticado. El portal lo usa para rehidratar su
    shell al recargar: si responde 200 el token guardado sigue vivo, y si
    responde 401 hay que volver al login. Sin secretos."""

    def get(self, request):
        try:
            return JsonResponse(sysadmin_auth_service.contexto(request))
        except Sysadmin.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)


class CatalogosView(SysAdminRequiredMixin, View):
    """Catalogo maestro de plataforma (planes, modulos, dependencias y dominios
    reservados) que necesitan el alta de tenant (RF-01) y el editor de modulos
    (RF-03). Solo lectura: son tablas de semilla, no se administran por API.

    Existe para que el portal no tenga que replicar el seed: la respuesta trae
    el mismo grafo de dependencias con el que _resolver_modulos valida, de modo
    que lo que el frontend anticipe coincida con lo que el backend aplica."""

    def get(self, request):
        return JsonResponse(catalogo_service.catalogos())


# ------------------------------------------------------------ RF-01 / RF-02

@method_decorator(csrf_exempt, name="dispatch")
class TenantListCreateView(SysAdminRequiredMixin, View):
    """RF-02 (GET, listado de tenants) y RF-01 (POST, registrar tenant). Solo
    SysAdmin (SysAdminRequiredMixin). CA06 de RF-02: un usuario de tenant nunca
    llega aqui (su request.sysadmin_id es None) -> 401."""

    def get(self, request):
        return JsonResponse(tenant_service.listar_tenants(request))

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            tenant, admin, token = tenant_service.crear_tenant(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        payload = tenant_service.serialize_tenant(tenant, detalle=True)
        payload["admin_inicial"] = {
            "id": str(admin.id),
            "correo": admin.correo,
            "nombre_completo": admin.nombre_completo,
            "estado": admin.estado,
        }
        # El token solo existe en claro en este instante (en la tabla se guarda
        # su hash). Se entrega al SysAdmin mientras el worker de correo de RF-25
        # entrega el enlace al admin inicial.
        payload["activacion_token"] = token
        return JsonResponse(payload, status=201)


@method_decorator(csrf_exempt, name="dispatch")
class TenantDetailView(SysAdminRequiredMixin, View):
    """RF-02/CA05 (GET, detalle) y RF-03 (PATCH, editar datos/plan/modulos)."""

    def get(self, request, pk):
        try:
            return JsonResponse(tenant_service.detalle_tenant(pk))
        except Tenant.DoesNotExist:
            return JsonResponse({"detail": "No encontrado"}, status=404)

    def patch(self, request, pk):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            tenant = tenant_service.editar_tenant(pk, data, request)
        except Tenant.DoesNotExist:
            return JsonResponse({"detail": "No encontrado"}, status=404)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(tenant_service.serialize_tenant(tenant, detalle=True))


# ------------------------------------------------------------------ RF-04

@method_decorator(csrf_exempt, name="dispatch")
class TenantSuspenderView(SysAdminRequiredMixin, View):
    """RF-04: suspende (o da de baja logica con ?baja) un tenant. Invalida sus
    sesiones y bloquea el acceso. Reversible."""

    def post(self, request, pk):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        estado = "baja_logica" if request.GET.get("baja", "").lower() in ("1", "true") else "suspendido"
        try:
            tenant, revocadas = tenant_service.suspender_tenant(pk, data, request, estado_destino=estado)
        except Tenant.DoesNotExist:
            return JsonResponse({"detail": "No encontrado"}, status=404)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        payload = tenant_service.serialize_tenant(tenant, detalle=False)
        payload["sesiones_revocadas"] = revocadas
        return JsonResponse(payload)


@method_decorator(csrf_exempt, name="dispatch")
class TenantReactivarView(SysAdminRequiredMixin, View):
    """RF-04/RN05: reactiva un tenant suspendido o dado de baja."""

    def post(self, request, pk):
        try:
            tenant = tenant_service.reactivar_tenant(pk, request)
        except Tenant.DoesNotExist:
            return JsonResponse({"detail": "No encontrado"}, status=404)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(tenant_service.serialize_tenant(tenant, detalle=False))


@method_decorator(csrf_exempt, name="dispatch")
class TenantReenviarActivacionView(SysAdminRequiredMixin, View):
    """RF-01: reemite el enlace de activacion del administrador inicial de un
    tenant que sigue 'pendiente'. Rota el token (el anterior deja de servir) y
    devuelve el nuevo en claro por unica vez."""

    def post(self, request, pk):
        try:
            admin, token = tenant_service.reenviar_activacion(pk, request)
        except Tenant.DoesNotExist:
            return JsonResponse({"detail": "No encontrado"}, status=404)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        return JsonResponse({
            "activacion_token": token,
            "expira_en": admin.token_activacion_exp.isoformat(),
            "admin_inicial": {
                "id": str(admin.id),
                "correo": admin.correo,
                "nombre_completo": admin.nombre_completo,
                "estado": admin.estado,
            },
        })


@method_decorator(csrf_exempt, name="dispatch")
class TenantActivarView(View):
    """RF-01 (flujo de activacion): el administrador inicial consume el enlace
    de un solo uso, define su contrasena y activa en cascada usuario + tenant.
    Publico a proposito (el admin aun no puede autenticarse, RN08); nunca pasa
    por el login (RF-16)."""

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            tenant, admin = tenant_service.activar_tenant(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        payload = tenant_service.serialize_tenant(tenant, detalle=True)
        payload["admin_inicial"] = {"id": str(admin.id), "correo": admin.correo,
                                    "estado": admin.estado}
        return JsonResponse(payload)
