import datetime
import uuid

import jwt
from django.conf import settings
from django.utils import timezone

from core.models import SesionSysadmin
from core.utils.audit import client_ip

# Fundacion de sesion del SysAdmin (portal de plataforma). Gestor unico de
# core.sesion_sysadmin, equivalente a session_service para los tenants, pero:
#   · la sesion apunta a core.sysadmin (no a core.usuario) y NO tiene tenant;
#   · el JWT lleva typ='sysadmin' y NO lleva 'tid'.
#
# El token solo acredita identidad; es core.sesion_sysadmin quien decide si esa
# identidad conserva una sesion viva (misma invariante del Sprint 5): una sesion
# revocada invalida el token aunque su exp criptografico no haya pasado.

# Marca inequivoca de que el token pertenece al portal de plataforma. El
# middleware ramifica por este valor; un token de tenant nunca lo trae.
TYP_SYSADMIN = "sysadmin"
ALG = "HS256"

# El SysAdmin no pertenece a ningun tenant, asi que no hay
# config_seguridad_tenant.jwt_expiracion_horas que leer: la expiracion viene de
# settings (configurable por entorno), no de un numero magico.
JWT_EXPIRACION_HORAS_DEFAULT = 8


def _horas_expiracion():
    return getattr(settings, "SYSADMIN_JWT_EXPIRACION_HORAS", JWT_EXPIRACION_HORAS_DEFAULT)


def crear_sesion(request, sysadmin_id):
    """Crea una sesion valida de SysAdmin y devuelve (sesion, token).

    Responsabilidad unica: sesiones. No valida credenciales ni emite eventos de
    auditoria; de eso se encarga sysadmin_auth_service, su unico llamador, que
    emite el evento LOGIN con el id devuelto aqui.
    """
    now = timezone.now()
    expira_en = now + datetime.timedelta(hours=_horas_expiracion())
    jti = uuid.uuid4()

    sesion = SesionSysadmin.objects.create(
        id=uuid.uuid4(),
        sysadmin_id=sysadmin_id,
        jwt_id=str(jti),
        ip_origen=client_ip(request) or None,
        user_agent=request.headers.get("User-Agent") or None,
        emitida_en=now,
        expira_en=expira_en,
    )

    payload = {
        "sub": str(sysadmin_id),
        "typ": TYP_SYSADMIN,
        "jti": str(jti),
        "iat": now,
        "exp": expira_en,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=ALG)
    return sesion, token


def sesion_valida(jti):
    """Unica autoridad sobre si un JWT de SysAdmin sigue vigente. Verdadero solo
    si la sesion existe, no esta revocada y no ha expirado. Sin efectos
    secundarios (no revoca ni audita), como session_service.sesion_valida."""
    if not jti:
        return False
    return SesionSysadmin.objects.filter(
        jwt_id=str(jti), revocada_en__isnull=True, expira_en__gt=timezone.now()
    ).exists()


def sysadmin_id_de(jti):
    """Devuelve el sysadmin_id de una sesion viva por su jti, o None. El
    middleware lo usa para no confiar en el 'sub' del token: la identidad se
    reconfirma contra la fila de sesion vigente."""
    if not jti:
        return None
    return (
        SesionSysadmin.objects.filter(
            jwt_id=str(jti), revocada_en__isnull=True, expira_en__gt=timezone.now()
        )
        .values_list("sysadmin_id", flat=True)
        .first()
    )


def _activas(sysadmin_id):
    return SesionSysadmin.objects.filter(
        sysadmin_id=sysadmin_id, revocada_en__isnull=True, expira_en__gt=timezone.now()
    )


def revocar_una(jti, sysadmin_id, revocada_por=None):
    """Logout de la sesion en curso: revoca el jti si pertenece al propio
    SysAdmin. Idempotente (0 si ya no habia una sesion viva con ese jti).

    No abre audit_context: sesion_sysadmin no lleva el trigger fn_auditar (tabla
    global de plataforma), asi que el evento LOGOUT lo emite el llamador como
    evento de plataforma manual. revocada_por por defecto es el propio actor."""
    if not jti:
        return 0
    actor = revocada_por if revocada_por is not None else sysadmin_id
    return _activas(sysadmin_id).filter(jwt_id=str(jti)).update(
        revocada_en=timezone.now(), revocada_por_id=actor
    )


def revocar_todas_de(sysadmin_id, revocada_por=None):
    """Cierra TODAS las sesiones vivas de un SysAdmin. Aun sin endpoint propio,
    es la pieza que necesitara el dia que exista desactivar un SysAdmin
    (activo=False debe matar sus sesiones vivas, igual que RF-08 con usuarios).
    Devuelve el conteo."""
    actor = revocada_por if revocada_por is not None else sysadmin_id
    return _activas(sysadmin_id).update(
        revocada_en=timezone.now(), revocada_por_id=actor
    )
