import datetime
import json

import jwt
from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.services.auth_service import LoginError, intentar_login


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
