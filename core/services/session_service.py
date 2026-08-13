import datetime
import uuid

import jwt
from django.conf import settings
from django.utils import timezone

from core.models import ConfigSeguridadTenant, Sesion, Tenant
from core.utils.audit import audit_context, client_ip

# Expiracion por defecto cuando el tenant no tiene fila en
# config_seguridad_tenant, alineada con el DEFAULT de la columna.
JWT_EXPIRACION_HORAS_DEFAULT = 8

# Contrato definitivo del JWT (Sprint 5): el token solo acredita identidad.
#   sub -> usuario   tid -> tenant (slug)   jti -> sesion   iat / exp
# Cualquier dato dinamico (permisos, roles, estado) se resuelve siempre desde
# la base de datos, nunca desde el token.
ALG = "HS256"


def _horas_expiracion(tenant):
    horas = (
        ConfigSeguridadTenant.objects.filter(tenant=tenant)
        .values_list("jwt_expiracion_horas", flat=True)
        .first()
    )
    return horas or JWT_EXPIRACION_HORAS_DEFAULT


def crear_sesion(request, tenant, usuario_id):
    """Crea una sesion valida y devuelve (sesion, token). RF-16.

    Responsabilidad unica: sesiones. No cuenta intentos ni emite eventos de
    autenticacion; de eso se encarga el servicio de autenticacion, que es su
    unico llamador y quien emite el evento LOGIN con el id que se devuelve aqui.

    Debe invocarse DENTRO del audit_context ya abierto por el login y con el
    actor ya fijado (set_audit_user), para que la fila de auditoria del INSERT
    en core.sesion (via trigger core.fn_auditar) herede el contexto completo.
    """
    now = timezone.now()
    horas = _horas_expiracion(tenant)
    expira_en = now + datetime.timedelta(hours=horas)
    jti = uuid.uuid4()

    sesion = Sesion.objects.create(
        id=uuid.uuid4(),
        tenant=tenant,
        usuario_id=usuario_id,
        jwt_id=str(jti),
        ip_origen=client_ip(request) or None,
        user_agent=request.headers.get("User-Agent") or None,
        emitida_en=now,
        expira_en=expira_en,
    )

    payload = {
        "sub": str(usuario_id),
        "tid": tenant.slug,
        "jti": str(jti),
        "iat": now,
        "exp": expira_en,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=ALG)
    return sesion, token


def sesion_valida(jti, tenant_slug=None):
    """Unica autoridad sobre si un JWT sigue vigente (Sprint 5): el token
    acredita identidad, pero es core.sesion quien decide si esa identidad
    conserva una sesion viva. La revocacion (RF-17/RF-19) nunca depende del
    contenido del token, solo del estado persistido aqui.

    Verdadero solo si la sesion existe, no ha sido revocada y no ha expirado.
    Sin efectos secundarios: no cierra sesiones ni audita; eso pertenece a los
    RF que corresponda.

    core.sesion tiene tenant_id y por tanto RLS: bajo el rol restringido de
    produccion, consultarla sin fijar antes app.current_tenant_id siempre
    devuelve vacio (la politica tenant_isolation la filtra), aunque la sesion
    exista y sea valida. tenant_slug viene del propio JWT (tid), asi que se
    resuelve el tenant antes de abrir el contexto, igual que en validar_otp.
    """
    if not jti:
        return False
    tenant = Tenant.objects.filter(slug=tenant_slug).first() if tenant_slug else None
    with audit_context(None, tenant_id=tenant.id if tenant else None):
        return Sesion.objects.filter(
            jwt_id=str(jti), revocada_en__isnull=True, expira_en__gt=timezone.now()
        ).exists()


# ------------------------------------------------------- RF-17 / RF-19

def _activas(usuario_id):
    return Sesion.objects.filter(
        usuario_id=usuario_id, revocada_en__isnull=True, expira_en__gt=timezone.now()
    )


def listar_activas(usuario_id):
    """RF-19/CA01: sesiones activas de UN usuario (siempre el propio: la vista
    filtra por el usuario del JWT, RN01). Mas recientes primero."""
    return _activas(usuario_id).order_by("-emitida_en")


def serialize_sesion(sesion, jti_actual=None):
    """RF-19/CA01. 'ultima_actividad' = 'iniciada_en' (emitida_en): DESVIACION
    DOCUMENTADA (F2) — no se rastrea la ultima actividad real para no convertir
    el middleware en escritor ni contaminar la bitacora de RF-20 con un UPDATE
    por minuto y sesion. 'dispositivo' es el User-Agent crudo; el frontend
    deriva el tipo (evita una libreria de parseo). La ubicacion aproximada por
    IP (narrativa, no exigida por CA01) queda fuera: requeriria una base GeoIP."""
    return {
        "id": str(sesion.id),
        "jti": sesion.jwt_id,
        "dispositivo": sesion.user_agent,
        "ip_origen": sesion.ip_origen,
        "iniciada_en": sesion.emitida_en.isoformat(),
        "ultima_actividad": sesion.emitida_en.isoformat(),
        "expira_en": sesion.expira_en.isoformat(),
        "actual": bool(jti_actual) and sesion.jwt_id == str(jti_actual),
    }


def _revocar(request, queryset, revocada_por=None):
    """Marca revocada_en/revocada_por sobre un queryset ya acotado. La
    revocacion es un UPDATE sobre core.sesion -> el trigger fn_auditar la
    audita con el actor (revocador) que fija audit_context. Devuelve cuantas
    filas se revocaron (idempotente: 0 si no habia ninguna viva).

    revocada_por se pasa explicito cuando no hay sesion autenticada que lo
    aporte: el restablecimiento de contrasena (RF-18) invalida las sesiones
    sin que el usuario tenga una sesion en curso."""
    actor = revocada_por if revocada_por is not None else getattr(request, "usuario_id", None)
    with audit_context(request, usuario_id=actor):
        return queryset.update(revocada_en=timezone.now(), revocada_por_id=actor)


def revocar_una(request, jti, usuario_id):
    """RF-17 (logout de la sesion actual) y RF-19/CA02 (cerrar una sesion
    propia). Acotada a usuario_id: nadie revoca la sesion de otro (RN01).
    Idempotente."""
    if not jti:
        return 0
    return _revocar(request, _activas(usuario_id).filter(jwt_id=str(jti)))


def revocar_otras(request, usuario_id, jti_actual):
    """RF-19/CA03: cierra todas las sesiones propias EXCEPTO la actual."""
    qs = _activas(usuario_id)
    if jti_actual:
        qs = qs.exclude(jwt_id=str(jti_actual))
    return _revocar(request, qs)


def revocar_todas_de_usuario(request, usuario_id, revocada_por=None):
    """RF-19/RN02: el TENANT_ADMIN fuerza el cierre de TODAS las sesiones de un
    usuario. Tambien lo usa RF-18 (restablecer contrasena) para invalidar las
    sesiones previas, pasando revocada_por=usuario porque no hay sesion en
    curso. Devuelve solo el conteo."""
    return _revocar(request, _activas(usuario_id), revocada_por=revocada_por)
