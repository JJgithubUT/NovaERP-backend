import hashlib
import re
import secrets
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone

from core.models import ConfigSeguridadTenant, Notificacion, Rol, Usuario, UsuarioRol
from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError

CORREO_MAX_LEN = 150  # RF-05/CA02
CORREO_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ACTIVACION_VIGENCIA = timedelta(hours=24)  # RF-05/CA06

# RF-05/RN05: el plan limita los usuarios "activos", pero un usuario en
# 'pendiente' ya tiene su licencia apartada (activarse solo depende de que
# abra su enlace). Contar solo 'activo' permitiria crear altas pendientes sin
# tope y rebasar el plan al activarlas; 'suspendido' si libera licencia (RF-08).
ESTADOS_QUE_CONSUMEN_LICENCIA = ("pendiente", "pendiente_verificacion", "activo")

# Politica de contrasena por defecto, alineada al DEFAULT de
# core.config_seguridad_tenant, para tenants sin fila de configuracion (RF-22).
PASSWORD_MIN_LEN_DEFAULT = 12


def _hash_token(token):
    """El token de activacion es una credencial: en la tabla se guarda su
    SHA-256, nunca el valor en claro. Es un hash sin sal a proposito, para
    poder localizar al usuario por token en la activacion."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _nuevo_token_activacion():
    token = secrets.token_urlsafe(32)
    return token, _hash_token(token)


def _validar_correo(correo):
    if not correo:
        raise BusinessRuleError("correo es obligatorio.", campo="correo")
    if len(correo) > CORREO_MAX_LEN or not CORREO_RE.match(correo):
        raise BusinessRuleError(
            f"correo no tiene un formato valido (maximo {CORREO_MAX_LEN} caracteres).",
            campo="correo",
        )


def _validar_roles(tenant, roles_data):
    """RF-05/RN04 y RN06: al menos un rol, y solo roles activos del propio
    tenant del creador."""
    if not roles_data:
        raise BusinessRuleError("Debe asignar al menos un rol al usuario.", campo="roles")
    if not isinstance(roles_data, list):
        raise BusinessRuleError("roles debe ser una lista de identificadores.", campo="roles")

    solicitados = {str(r) for r in roles_data}
    try:
        roles = list(Rol.objects.filter(tenant=tenant, id__in=solicitados, activo=True))
    except (ValidationError, ValueError):
        raise BusinessRuleError("roles contiene identificadores invalidos.", campo="roles")

    if len(roles) != len(solicitados):
        raise BusinessRuleError(
            "Uno o mas roles no existen en este tenant o estan inactivos.", campo="roles"
        )
    return roles


def _validar_licencias(tenant):
    """RF-05/RN05 + flujo alternativo 3a."""
    licencias_max = tenant.plan.licencias_max
    consumidas = Usuario.objects.filter(
        tenant=tenant, estado__in=ESTADOS_QUE_CONSUMEN_LICENCIA
    ).count()
    if consumidas >= licencias_max:
        raise BusinessRuleError(
            "Se alcanzo el limite de usuarios del plan contratado.",
            campo="plan",
            extra={"licencias_max": licencias_max, "licencias_consumidas": consumidas},
        )


def _validar_password(tenant, password):
    """La politica no se codifica aqui: vive en core.config_seguridad_tenant,
    que es lo que RF-22 permitira editar por tenant."""
    config = ConfigSeguridadTenant.objects.filter(tenant=tenant).first()
    min_len = config.politica_password_min_len if config else PASSWORD_MIN_LEN_DEFAULT

    if len(password) < min_len:
        raise BusinessRuleError(
            f"La contrasena debe tener al menos {min_len} caracteres.", campo="password"
        )

    regex = config.politica_password_regex if config else None
    if not regex:
        return

    try:
        cumple = re.search(regex, password) is not None
    except re.error:
        # Fallar abierto seria aceptar contrasenas debiles sin que nadie se
        # entere; se corta la activacion y se senala el error de configuracion.
        raise BusinessRuleError(
            "La politica de contrasena del tenant esta mal configurada; "
            "contacte al administrador."
        )

    if not cumple:
        raise BusinessRuleError(
            "La contrasena no cumple la politica de seguridad del tenant.", campo="password"
        )


def _encolar_correo_activacion(usuario, token, now):
    """RF-05 postcondicion "correo enviado (o en cola de reintento)": el envio
    real lo hara el worker de notificaciones de RF-25; aqui la notificacion
    queda en 'pendiente' para que ese worker la tome. Mientras tanto el enlace
    tambien se devuelve al TENANT_ADMIN que dio de alta al usuario (ver
    crear_usuario) para que pueda compartirlo."""
    return Notificacion.objects.create(
        id=uuid.uuid4(),
        tenant=usuario.tenant,
        usuario=usuario,
        canal="email",
        asunto="Activacion de su cuenta NovaERP",
        cuerpo=(
            f"Hola {usuario.nombre_completo}: active su cuenta y defina su "
            f"contrasena con el siguiente token de un solo uso, vigente 24 horas: {token}"
        ),
        estado="pendiente",
        intentos=0,
        created_at=now,
    )


def serialize_usuario(usuario):
    roles = list(
        Rol.objects.filter(core_usuario_rol_rol_set__usuario_id=usuario.id)
        .order_by("nombre")
        .values_list("nombre", flat=True)
    )
    return {
        "id": str(usuario.id),
        "correo": usuario.correo,
        "nombre_completo": usuario.nombre_completo,
        "estado": usuario.estado,
        "mfa_enrolado": usuario.mfa_enrolado,
        "roles": roles,
        "created_at": usuario.created_at.isoformat(),
    }


def crear_usuario(data, request):
    """RF-05: alta de usuario dentro del tenant del TENANT_ADMIN autenticado.

    El tenant sale del JWT y nunca del payload (RN01), el usuario nace en
    'pendiente' sin password (RN03/CA05) y solo puede fijarla consumiendo el
    token de activacion (ver activar_usuario), nunca por el login de RF-16.

    Devuelve (usuario, token_activacion_en_claro): el token no se persiste en
    claro, asi que este es el unico momento en que puede entregarse.
    """
    tenant = get_tenant(request)
    if tenant.estado != "activo":
        raise BusinessRuleError(
            "El tenant no esta activo; no admite altas de usuario.",
            extra={"estado_tenant": tenant.estado},
        )

    correo = (data.get("correo") or "").strip()
    _validar_correo(correo)

    nombre_completo = (data.get("nombre_completo") or "").strip()
    if not nombre_completo:
        raise BusinessRuleError("nombre_completo es obligatorio.", campo="nombre_completo")

    # RN02: unico dentro del tenant (correo es citext, la comparacion la
    # resuelve la DB sin distinguir mayusculas).
    if Usuario.objects.filter(tenant=tenant, correo=correo).exists():
        raise BusinessRuleError("El correo ya esta registrado en este tenant.", campo="correo")

    roles = _validar_roles(tenant, data.get("roles"))
    _validar_licencias(tenant)

    token, token_hash = _nuevo_token_activacion()
    now = timezone.now()

    # CA07: audit_context publica creador, tenant e IP para el trigger.
    with audit_context(request, tenant_id=tenant.id):
        usuario = Usuario.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            correo=correo,
            nombre_completo=nombre_completo,
            password_hash=None,
            mfa_secret=None,
            mfa_enrolado=False,
            estado="pendiente",
            token_activacion=token_hash,
            token_activacion_exp=now + ACTIVACION_VIGENCIA,
            intentos_fallidos=0,
            bloqueado_hasta=None,
            created_at=now,
            updated_at=now,
        )
        UsuarioRol.objects.bulk_create(
            [
                UsuarioRol(
                    usuario=usuario,
                    rol=rol,
                    asignado_en=now,
                    asignado_por_id=request.usuario_id,
                )
                for rol in roles
            ]
        )
        _encolar_correo_activacion(usuario, token, now)

    return usuario, token


def activar_usuario(data, request):
    """Flujo de activacion de RF-05: el enlace de un solo uso (24 h) es el
    unico camino para que un usuario 'pendiente' fije su contrasena. Corre
    fuera del login (RF-16) y sin JWT, porque el usuario todavia no puede
    autenticarse.

    El enrolamiento obligatorio de MFA que la ERS describe en este mismo flujo
    queda pendiente de RF-16 (Autenticar usuario + segundo factor), que es
    quien introduce el TOTP y su verificacion; adelantarlo aqui implicaria
    inventar el formato del secreto antes de tener quien lo valide.
    """
    token = (data.get("token") or "").strip()
    password = data.get("password") or ""
    if not token:
        raise BusinessRuleError("token es obligatorio.", campo="token")

    usuario = (
        Usuario.objects.select_related("tenant")
        .filter(token_activacion=_hash_token(token))
        .first()
    )
    # Un solo mensaje para token inexistente, ya consumido o vencido: no
    # revela si el enlace existio (mismo criterio que RN04 de RF-16).
    if (
        usuario is None
        or usuario.token_activacion_exp is None
        or usuario.token_activacion_exp <= timezone.now()
    ):
        raise BusinessRuleError("El enlace de activacion es invalido o expiro.", campo="token")

    if usuario.estado == "suspendido" or usuario.tenant.estado != "activo":
        raise BusinessRuleError("La cuenta no puede activarse en este momento.")

    _validar_password(usuario.tenant, password)

    # El responsable de la activacion es el propio usuario, que todavia no
    # tiene sesion: se pasa explicitamente para que la bitacora no lo registre
    # como una escritura anonima.
    with audit_context(request, tenant_id=usuario.tenant_id, usuario_id=usuario.id):
        # El hash lo calcula Postgres con pgcrypto (bcrypt, cost 12), el mismo
        # algoritmo que verifica core.intentar_login; Django nunca almacena ni
        # deriva la contrasena. Se consume el token en el mismo UPDATE para
        # que el enlace sea atomicamente de un solo uso.
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE "core"."usuario" SET password_hash = crypt(%s, gen_salt(\'bf\', 12)),'
                " estado = 'activo', token_activacion = NULL, token_activacion_exp = NULL,"
                " intentos_fallidos = 0, bloqueado_hasta = NULL"
                " WHERE id = %s AND token_activacion = %s",
                [password, str(usuario.id), _hash_token(token)],
            )
            if cursor.rowcount != 1:
                raise BusinessRuleError(
                    "El enlace de activacion es invalido o expiro.", campo="token"
                )

    usuario.refresh_from_db()
    return usuario
