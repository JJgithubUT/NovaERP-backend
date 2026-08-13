import jwt
from django.conf import settings

from core.services import session_service, sysadmin_session_service


class JWTCustomMiddleware:
    """Decodifica el Bearer JWT y expone la identidad en el request.

    Responde una sola pregunta: ¿esta sesion sigue siendo valida? Sin reglas de
    negocio, sin permisos, sin estados del usuario, sin auditoria. Dos puertas:

      1. firma y expiracion criptografica del token (barato, sin DB);
      2. la sesion persistida decide si esa identidad conserva una sesion viva
         (contrato del Sprint 5). Una sesion revocada se rechaza aunque el JWT
         no haya expirado.

    Ramifica por el campo 'typ' del token en dos superficies excluyentes:

      · typ == 'sysadmin' -> portal de plataforma. Valida contra
        core.sesion_sysadmin y expone request.sysadmin_id. NO fija usuario_id ni
        tenant_slug: el SysAdmin no pertenece a ningun tenant.
      · en otro caso      -> sesion de tenant (comportamiento previo). Valida
        contra core.sesion y expone usuario_id / tenant_slug.

    Un token de tenant nunca activa la rama de plataforma y viceversa: son
    almacenes de sesion distintos con un jti que no colisiona entre tablas.

    No bloquea: si algo falla deja los tres identificadores en None y cada vista
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
        # Identidad del portal de plataforma. Distinta de usuario_id a proposito:
        # las vistas de tenant nunca deben tratar a un SysAdmin como usuario, ni
        # las de plataforma tratar a un usuario como SysAdmin.
        request.sysadmin_id = None

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
            try:
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            except jwt.InvalidTokenError:
                payload = None

            if payload:
                jti = payload.get("jti")
                if payload.get("typ") == sysadmin_session_service.TYP_SYSADMIN:
                    # La identidad se reconfirma contra la fila de sesion viva,
                    # no se toma del 'sub' del token.
                    sysadmin_id = sysadmin_session_service.sysadmin_id_de(jti)
                    if sysadmin_id is not None:
                        request.sysadmin_id = str(sysadmin_id)
                        request.session_jti = jti
                elif session_service.sesion_valida(jti, tenant_slug=payload.get("tid")):
                    request.usuario_id = payload.get("sub")
                    request.tenant_slug = payload.get("tid")
                    request.session_jti = jti

        return self.get_response(request)
