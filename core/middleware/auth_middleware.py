import jwt
from django.conf import settings

from core.services import session_service


class JWTCustomMiddleware:
    """Decodifica el Bearer JWT y expone request.usuario_id / request.tenant_slug.

    Responde una sola pregunta: ¿esta sesion sigue siendo valida? Sin reglas de
    negocio, sin permisos, sin estados del usuario, sin auditoria. Dos puertas:

      1. firma y expiracion criptografica del token (barato, sin DB);
      2. session_service.sesion_valida(jti): el token acredita identidad, pero
         es core.sesion quien decide si esa identidad conserva una sesion viva
         (contrato del Sprint 5). Una sesion revocada se rechaza aunque el JWT
         no haya expirado.

    No bloquea: si algo falla deja usuario_id/tenant_slug en None y cada vista
    decide si exige autenticacion. Los tokens antiguos sin jti (previos a la
    persistencia de sesion) no pasan la segunda puerta y fuerzan un nuevo login.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.usuario_id = None
        request.tenant_slug = None
        # jti de la sesion en curso: lo necesitan logout (RF-17) y "cerrar las
        # demas" (RF-19) para actuar sobre / excluir la sesion actual. Es
        # identidad de sesion, no logica de negocio; el middleware sigue solo
        # leyendo (no escribe ni audita).
        request.session_jti = None

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
            try:
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            except jwt.InvalidTokenError:
                payload = None

            if payload and session_service.sesion_valida(payload.get("jti")):
                request.usuario_id = payload.get("sub")
                request.tenant_slug = payload.get("tid")
                request.session_jti = payload.get("jti")

        return self.get_response(request)
