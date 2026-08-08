from django.utils import timezone

from django.db import connection

from core.models import LogAuditoria, SesionSysadmin, Sysadmin
from core.services import sysadmin_session_service
from core.utils.audit import client_ip, sysadmin_context

# Orquestador de autenticacion del portal de plataforma (SysAdmin). Equivale a
# auth_service (RF-16) para los tenants, pero de UNA fase: valida password y, si
# es correcto, crea la sesion y emite el token. Sin MFA por ahora (decision
# aprobada; core.sysadmin.mfa_secret queda reservada) y sin bloqueo por intentos
# (core.sysadmin no tiene contador: los fallos se AUDITAN pero no se cuentan
# todavia — diferido documentado).

# Eventos de plataforma. Se registran manualmente en la bitacora append-only
# (usuario_id NULL, tenant_id NULL, entidad='sysadmin'): el trigger fn_auditar
# no cubre sesion_sysadmin (tabla global) ni podria atribuir el actor, porque su
# id no vive en core.usuario (FK de log_auditoria.usuario_id).
EVENTO_LOGIN = "LOGIN"
EVENTO_LOGIN_FAILED = "LOGIN_FAILED"
EVENTO_LOGOUT = "LOGOUT"

# entidad comun para todos los eventos del portal de plataforma: los hace
# greppables y los separa de los eventos de tenant (entidad='sesion'/'usuario').
ENTIDAD = "sysadmin"


class SysAdminLoginError(Exception):
    """Login de SysAdmin rechazado -> HTTP 401."""


def _validar(correo, password):
    """Delega en core.intentar_login_sysadmin (validador puro): valida y
    devuelve (resultado, sysadmin_id, mensaje) sin tocar estado."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT resultado, sysadmin_id, mensaje FROM core.intentar_login_sysadmin(%s, %s)",
            [correo, password],
        )
        return cursor.fetchone()


def _registrar_evento(operacion, sysadmin_id, entidad_id, ip, criticidad="NORMAL"):
    """Evento de plataforma manual. usuario_id/tenant queda NULL (el actor no es
    un usuario de tenant); el SysAdmin responsable se identifica por entidad_id
    o, en un fallo sin cuenta, por el correo intentado."""
    LogAuditoria.objects.create(
        tenant=None,
        usuario_id=None,
        entidad=ENTIDAD,
        entidad_id=str(entidad_id),
        operacion=operacion,
        criticidad=criticidad,
        ip_origen=ip or None,
        ocurrido_en=timezone.now(),
    )


def autenticar(request, correo, password):
    """Login de una fase (RF-16 equivalente para plataforma). Valida credenciales
    contra core.sysadmin; si son correctas crea la sesion y devuelve el token.

    Devuelve (token, mensaje). Lanza SysAdminLoginError en cualquier rechazo. El
    LoginError se lanza FUERA del sysadmin_context para que la escritura del
    evento LOGIN_FAILED se confirme (misma tecnica que auth_service)."""
    ip = client_ip(request)
    error = None
    token = None
    mensaje_ok = None

    with sysadmin_context(request):
        resultado, sysadmin_id, mensaje = _validar(correo, password)

        if resultado == "ok":
            sesion, token = sysadmin_session_service.crear_sesion(request, sysadmin_id)
            _registrar_evento(EVENTO_LOGIN, sysadmin_id, sesion.id, ip)
            mensaje_ok = "Autenticacion completada."
        else:
            # 'credenciales' (con o sin cuenta) o 'inactivo': fallo auditado. Sin
            # cuenta, la referencia es el correo intentado (visibilidad de fuerza
            # bruta); no se cuenta el intento (sin bloqueo por ahora).
            criticidad = "ALTA" if resultado == "inactivo" else "NORMAL"
            _registrar_evento(
                EVENTO_LOGIN_FAILED, sysadmin_id, sysadmin_id or correo, ip, criticidad
            )
            error = mensaje

    if error is not None:
        raise SysAdminLoginError(error)
    return token, mensaje_ok


def contexto(request):
    """Identidad y sesion en curso del SysAdmin autenticado. Es el equivalente
    de /api/core/me/ (RF-09) para el portal de plataforma: el shell lo llama al
    arrancar para validar un token restaurado del storage y saber quien opera,
    en vez de deducirlo de que el listado de tenants no haya dado 401.

    No expone password_hash ni mfa_secret (mismo criterio de RF-09/CA02).
    Tampoco hay roles ni permisos que devolver: el SysAdmin es superusuario
    plano sobre /api/admin/ y no pasa por PermissionResolver.

    Lanza Sysadmin.DoesNotExist si la cuenta ya no existe; la vista lo traduce a
    401 (sesion obsoleta), igual que MeView con un usuario borrado.
    """
    sysadmin = Sysadmin.objects.get(id=request.sysadmin_id)
    sesion = SesionSysadmin.objects.filter(jwt_id=request.session_jti).first()

    return {
        "id": str(sysadmin.id),
        "correo": sysadmin.correo,
        "activo": sysadmin.activo,
        "created_at": sysadmin.created_at.isoformat(),
        "mfa_enrolado": bool(sysadmin.mfa_secret),
        "sesion": None if sesion is None else {
            "jti": sesion.jwt_id,
            "emitida_en": sesion.emitida_en.isoformat(),
            # El portal puede avisar del vencimiento sin decodificar el JWT.
            "expira_en": sesion.expira_en.isoformat(),
            "ip_origen": sesion.ip_origen,
        },
    }


def logout(request, jti, sysadmin_id):
    """Cierra la sesion en curso del SysAdmin (RF-17 equivalente). Revoca el jti
    y emite LOGOUT solo si habia una sesion viva (idempotente)."""
    with sysadmin_context(request):
        n = sysadmin_session_service.revocar_una(jti, sysadmin_id)
        if n:
            _registrar_evento(EVENTO_LOGOUT, sysadmin_id, jti, client_ip(request))
    return n
