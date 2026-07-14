import jwt
from django.conf import settings


class JWTCustomMiddleware:
    """Decodifica el Bearer JWT (emitido por LoginView) y expone
    request.usuario_id / request.tenant_slug. No bloquea peticiones sin
    token o con token invalido/expirado: solo deja ambos atributos en None
    para que cada vista decida si el endpoint requiere autenticacion."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.usuario_id = None
        request.tenant_slug = None

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
            try:
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            except jwt.InvalidTokenError:
                payload = None

            if payload:
                request.usuario_id = payload.get("usuario_id")
                request.tenant_slug = payload.get("tenant_slug")

        return self.get_response(request)
