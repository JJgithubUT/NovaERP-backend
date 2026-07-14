import datetime
import json

import jwt
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.models import Modulo, Permiso, Rol, Usuario
from core.services.auth_service import LoginError, intentar_login
from core.utils.auth import UNAUTHORIZED, LoginRequiredMixin


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

        permisos = list(
            Permiso.objects.filter(core_rol_permiso_permiso_set__rol__in=roles)
            .distinct()
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
                "permisos": permisos,
                "modulos": modulos,
            }
        )
