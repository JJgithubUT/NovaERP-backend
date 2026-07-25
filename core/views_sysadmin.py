import json

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.services import sysadmin_auth_service
from core.services.sysadmin_auth_service import SysAdminLoginError
from core.utils.auth import SysAdminRequiredMixin

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
