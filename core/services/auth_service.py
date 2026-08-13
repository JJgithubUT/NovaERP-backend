import datetime

import jwt
from django.conf import settings
from django.db import connection
from django.utils import timezone

from core.models import LogAuditoria, Tenant, Usuario
from core.services import notificacion_service
from core.services import session_service
from core.utils import secretos, totp
from core.utils.audit import audit_context, client_ip, set_audit_user

# Eventos de autenticacion (RF-16). Un unico mecanismo para los tres; nunca un
# camino distinto por evento.
EVENTO_LOGIN = "LOGIN"
EVENTO_LOGIN_FAILED = "LOGIN_FAILED"
EVENTO_ACCOUNT_LOCKED = "ACCOUNT_LOCKED"

# Reto OTP efimero entre la fase 1 (password) y la fase 2 (codigo). NO es una
# sesion: no tiene fila en core.sesion, asi que el middleware nunca lo acepta
# para acceder a endpoints protegidos. Vida corta; los reintentos los acota el
# bloqueo global de RN02 (registrar_intento_fallido), fuente unica de conteo.
RETO_TYP = "otp_challenge"
RETO_MINUTOS = 5


class LoginError(Exception):
    """Login rechazado -> HTTP 401. El mensaje ya viene del validador con el
    criterio de RN04 (generico) o el especifico de bloqueo/inactivo."""


def _validar_credenciales(tenant_slug, correo, password):
    """Delega en core.intentar_login, que es un VALIDADOR PURO: valida y
    devuelve (resultado, usuario_id, mensaje) sin tocar estado. Toda mutacion
    (contar/desbloquear intentos) la orquesta este servicio."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT resultado, usuario_id, mensaje FROM core.intentar_login(%s, %s, %s)",
            [tenant_slug, correo, password],
        )
        return cursor.fetchone()  # (resultado, usuario_id, mensaje)


def _registrar_intento_fallido(usuario_id):
    """Incrementa el contador y devuelve el bloqueo resultante (o None). Se
    invoca desde aqui, no desde el validador, para cerrar el residual de RF-20:
    corre dentro del audit_context con app.current_user_id ya fijado."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT core.registrar_intento_fallido(%s)", [str(usuario_id)])
        return cursor.fetchone()[0]


def _reset_intentos_fallidos(usuario_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT core.reset_intentos_fallidos(%s)", [str(usuario_id)])


def _registrar_evento(tipo, tenant, usuario_id, ip, entidad_id, criticidad="NORMAL"):
    """Unico mecanismo de eventos de autenticacion. core.fn_auditar (RF-20)
    solo cubre CUD sobre tablas; LOGIN / LOGIN_FAILED / ACCOUNT_LOCKED son
    eventos de dominio, asi que se insertan explicitamente en la bitacora
    append-only (que no se audita a si misma: no recurre). entidad='sesion'
    para LOGIN, ='usuario' para los fallos, que no tienen sesion."""
    LogAuditoria.objects.create(
        tenant=tenant,
        usuario_id=usuario_id,
        entidad="sesion" if tipo == EVENTO_LOGIN else "usuario",
        entidad_id=str(entidad_id),
        operacion=tipo,
        criticidad=criticidad,
        ip_origen=ip or None,
        ocurrido_en=timezone.now(),
    )


def _emitir_reto(usuario_id, tenant_slug):
    """Reto OTP: JWT efimero firmado con SECRET_KEY. Solo acredita que la fase
    1 (password) se supero para este usuario; no concede acceso por si mismo."""
    ahora = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": str(usuario_id),
        "tid": tenant_slug,
        "typ": RETO_TYP,
        "iat": ahora,
        "exp": ahora + datetime.timedelta(minutes=RETO_MINUTOS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def _leer_reto(reto):
    """Devuelve el payload del reto o lanza LoginError si es invalido, expirado
    o de otro tipo. No revela cual de las causas (mensaje generico)."""
    try:
        payload = jwt.decode(reto or "", settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise LoginError("El reto de verificacion es invalido o expiro.")
    if payload.get("typ") != RETO_TYP:
        raise LoginError("El reto de verificacion es invalido o expiro.")
    return payload


def _contar_fallo(tenant, usuario_id, ip):
    """Cuenta un intento fallido (RN02) con el UNICO contador global y emite
    LOGIN_FAILED; si este intento disparo el bloqueo, emite ACCOUNT_LOCKED
    (ALTA) y encola la alerta. Compartido por el fallo de password (fase 1) y
    el de OTP (fase 2): no hay dos contadores."""
    bloqueado_hasta = _registrar_intento_fallido(usuario_id)
    _registrar_evento(EVENTO_LOGIN_FAILED, tenant, usuario_id, ip, usuario_id)
    now = timezone.now()
    if bloqueado_hasta is not None and bloqueado_hasta > now:
        _registrar_evento(
            EVENTO_ACCOUNT_LOCKED, tenant, usuario_id, ip, usuario_id, criticidad="ALTA"
        )
        # RF-25: notifica al usuario y a los TENANT_ADMIN (evento de seguridad).
        usuario = Usuario.objects.get(id=usuario_id)
        notificacion_service.notificar_bloqueo(None, tenant, usuario, bloqueado_hasta, now)


def autenticar(request, tenant, tenant_slug, correo, password):
    """FASE 1 del login (RF-16). Valida password y, si es correcto, NO emite
    sesion: emite un reto OTP y, si el usuario aun no tiene segundo factor, lo
    provisiona (RN07 se cumple en el primer login en vez de en la activacion:
    desviacion documentada, ver ARCHITECTURE.md).

    Devuelve un dict con el reto y el modo ('otp' o 'enroll'); en 'enroll'
    incluye el secreto y la URI otpauth una sola vez. Lanza LoginError en
    cualquier rechazo. NO resetea el contador: el login aun no termino; el
    reset ocurre al validar el OTP (fase 2), de modo que los fallos de password
    y de OTP se acumulan en el mismo contador (RN02).
    """
    ip = client_ip(request)
    error = None
    respuesta = None

    with audit_context(request, tenant_id=tenant.id if tenant else None):
        resultado, usuario_id, mensaje = _validar_credenciales(
            tenant_slug, correo, password
        )

        if resultado == "ok":
            set_audit_user(usuario_id)
            usuario = Usuario.objects.get(id=usuario_id)
            reto = _emitir_reto(usuario_id, tenant_slug)

            # Necesita enrolar si no hay secreto (incluye el admin sembrado:
            # mfa_enrolado=True pero secret NULL) o si quedo sin confirmar.
            if usuario.mfa_secret is None or not usuario.mfa_enrolado:
                secreto = totp.generar_secreto()
                usuario.mfa_secret = secretos.cifrar(secreto)
                usuario.mfa_enrolado = False
                usuario.updated_at = timezone.now()
                usuario.save(update_fields=["mfa_secret", "mfa_enrolado", "updated_at"])
                respuesta = {
                    "reto": reto,
                    "mfa": "enroll",
                    "secret": secreto,
                    "otpauth_uri": totp.otpauth_uri(secreto, correo),
                    "mensaje": "Configure su segundo factor y confirme el codigo.",
                }
            else:
                respuesta = {
                    "reto": reto,
                    "mfa": "otp",
                    "mensaje": "Ingrese el codigo de su segundo factor.",
                }

        elif resultado == "credenciales" and usuario_id is not None:
            set_audit_user(usuario_id)
            _contar_fallo(tenant, usuario_id, ip)
            error = mensaje

        else:
            # Bloqueado, inactivo, o tenant/usuario inexistente: rechazo sin
            # contar intento (CA04) y sin escrituras.
            error = mensaje

    if error is not None:
        raise LoginError(error)
    return respuesta


def validar_otp(request, reto, codigo):
    """FASE 2 del login (RF-16). Verifica el codigo TOTP contra el secreto del
    usuario; si es correcto completa el login (confirma enrolamiento si estaba
    pendiente, resetea el contador, crea la sesion y emite LOGIN). Un fallo de
    OTP cuenta en el mismo contador global de RN02.

    Devuelve (token, mensaje); lanza LoginError en cualquier rechazo.
    """
    ip = client_ip(request)
    payload = _leer_reto(reto)

    # El tenant sale del propio JWT (no de una consulta a core.usuario) porque
    # esa tabla tiene RLS: bajo el rol restringido de produccion, buscar al
    # usuario antes de fijar app.current_tenant_id siempre devuelve vacio (la
    # politica tenant_isolation lo filtra), aunque el codigo sea correcto.
    tenant = Tenant.objects.filter(slug=payload.get("tid")).first()
    if tenant is None:
        raise LoginError("El reto de verificacion es invalido o expiro.")

    error = None
    token = None
    mensaje_ok = None

    with audit_context(request, tenant_id=tenant.id):
        usuario = (
            Usuario.objects.select_related("tenant").filter(id=payload.get("sub")).first()
        )
        if usuario is None:
            raise LoginError("El reto de verificacion es invalido o expiro.")
        set_audit_user(usuario.id)

        # El bloqueo pudo dispararse entre fases (o durante los reintentos de
        # OTP): una cuenta bloqueada no completa el login aunque el codigo sea
        # correcto, y no se cuenta (CA04).
        if usuario.bloqueado_hasta is not None and usuario.bloqueado_hasta > timezone.now():
            error = f"Cuenta bloqueada, intente después de {usuario.bloqueado_hasta}"
        else:
            secreto = secretos.descifrar(usuario.mfa_secret) if usuario.mfa_secret else None
            if secreto and totp.verificar_codigo(secreto, codigo):
                if not usuario.mfa_enrolado:
                    usuario.mfa_enrolado = True
                    usuario.updated_at = timezone.now()
                    usuario.save(update_fields=["mfa_enrolado", "updated_at"])
                _reset_intentos_fallidos(usuario.id)
                sesion, token = session_service.crear_sesion(request, tenant, usuario.id)
                _registrar_evento(EVENTO_LOGIN, tenant, usuario.id, ip, sesion.id)
                mensaje_ok = "Autenticacion completada."
            else:
                _contar_fallo(tenant, usuario.id, ip)
                error = "Codigo de verificacion incorrecto."

    if error is not None:
        raise LoginError(error)
    return token, mensaje_ok
