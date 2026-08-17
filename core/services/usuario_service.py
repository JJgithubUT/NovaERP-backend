import hashlib
import re
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone

from core.models import ConfigSeguridadTenant, Notificacion, Rol, Usuario, UsuarioRol
from core.services import session_service
from core.utils import filtros
from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError

CORREO_MAX_LEN = 150  # RF-05/CA02
CORREO_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ACTIVACION_VIGENCIA = timedelta(hours=24)  # RF-05/CA06
RESET_VIGENCIA = timedelta(hours=1)  # RF-18/RN01: enlace de un solo uso, 1 hora

# RF-18/CA01/RN02: la solicitud siempre responde lo mismo, exista o no el
# correo, para no revelar su existencia.
MSG_RECUPERAR = "Si el correo existe en nuestro sistema, recibira instrucciones."

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


def _encolar_correo(usuario, token, now, asunto, instruccion, ruta, correo_destino=None):
    """Postcondicion "correo enviado (o en cola de reintento)" de RF-05 y CA04
    de RF-07: el envio real lo hara el worker de notificaciones de RF-25; aqui
    la notificacion queda en 'pendiente' para que ese worker la tome.

    Un unico encolado para los flujos que emiten un token de un solo uso
    (alta de cuenta, reconfirmacion de correo, restablecimiento de contrasena),
    porque el mecanismo de verificacion de identidad es el mismo. No indica una
    vigencia fija en el texto: cada flujo tiene la suya (24 h activacion, 1 h
    reset) y la DB la hace cumplir.

    `ruta` es la pagina del frontend que canjea el token (p.ej. '/activar' o
    '/restablecer'); junto con FRONTEND_URL arma el enlace clicable. El token
    en claro se deja tambien en el cuerpo -por si FRONTEND_URL no esta
    configurado o el cliente de correo no muestra el enlace- porque las
    pantallas de canje aceptan pegarlo a mano."""
    enlace = f"{settings.FRONTEND_URL}{ruta}?token={token}" if settings.FRONTEND_URL else None
    return Notificacion.objects.create(
        id=uuid.uuid4(),
        tenant=usuario.tenant,
        usuario=usuario,
        canal="email",
        asunto=asunto,
        cuerpo=(
            f"Hola {usuario.nombre_completo}: {instruccion}. "
            + (f"Siga este enlace: {enlace}\n\n" if enlace else "")
            + f"Si el enlace no funciona, use este token de un solo uso: {token}"
            + (f" (enviado a {correo_destino})" if correo_destino else "")
        ),
        estado="pendiente",
        intentos=0,
        created_at=now,
    )


def _encolar_correo_activacion(usuario, token, now):
    return _encolar_correo(
        usuario, token, now,
        asunto="Activacion de su cuenta NovaERP",
        instruccion="active su cuenta y defina su contrasena",
        ruta="/activar",
    )


def _encolar_correo_verificacion(usuario, token, now):
    """RF-07/CA04: nuevo correo de validacion tras un cambio de direccion."""
    return _encolar_correo(
        usuario, token, now,
        asunto="Confirme su nueva direccion de correo NovaERP",
        instruccion="confirme su nueva direccion de correo",
        ruta="/activar",
        correo_destino=usuario.correo,
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
        "telefono": usuario.telefono,
        "puesto": usuario.puesto,
        "departamento": usuario.departamento,
        "estado": usuario.estado,
        "mfa_enrolado": usuario.mfa_enrolado,
        "roles": roles,
        "created_at": usuario.created_at.isoformat(),
    }


# ----------------------------------------------------------------- RF-06

# Campos de ordenamiento permitidos (CA04). Se mapean a expresiones seguras;
# no se acepta un ORDER BY arbitrario del cliente.
_ORDEN_DIRECTORIO = {
    "nombre": "nombre_completo",
    "created_at": "created_at",
    "ultimo_acceso": "ultimo_acceso",
}
_TRUE = {"1", "true", "si", "sí"}


def serialize_usuario_directorio(usuario):
    """Fila del directorio (RF-06). Los roles salen del prefetch (sin N+1) y
    ultimo_acceso de la anotacion del queryset."""
    ua = getattr(usuario, "ultimo_acceso", None)
    return {
        "id": str(usuario.id),
        "correo": usuario.correo,
        "nombre_completo": usuario.nombre_completo,
        "telefono": usuario.telefono,
        "puesto": usuario.puesto,
        "departamento": usuario.departamento,
        "estado": usuario.estado,
        "roles": sorted(ur.rol.nombre for ur in usuario.core_usuario_rol_usuario_set.all()),
        "ultimo_acceso": ua.isoformat() if ua else None,
        "created_at": usuario.created_at.isoformat(),
    }


def directorio_usuarios(request):
    """RF-06: directorio paginado del tenant. RN01/RN02/CA05: siempre acotado
    al tenant del JWT, sin bypass posible. Busqueda por nombre/correo/puesto
    (CA02); filtros por estado, rol, departamento y rango de creacion (CA03);
    orden por nombre, fecha de alta o ultimo acceso (CA04).

    DESVIACION DOCUMENTADA (RN03/CA06): el enmascaramiento de datos personales
    por politica de privacidad del tenant y la auditoria condicional de la
    consulta no se implementan: el esquema de Fase 0 no tiene configuracion de
    politica de privacidad (seria territorio de RF-22). Se devuelven los campos
    a quien tiene core:usuarios:leer, y la lectura no se audita (consistente con
    el default de RF-20).
    """
    from django.db.models import Max, OuterRef, Q, Subquery

    from core.models import LogAuditoria
    from core.utils.auth import tenant_scoped
    from core.utils.pagination import paginate

    ultimo = (
        LogAuditoria.objects.filter(usuario_id=OuterRef("id"), operacion="LOGIN")
        .values("usuario_id")
        .annotate(m=Max("ocurrido_en"))
        .values("m")
    )
    qs = (
        tenant_scoped(Usuario.objects.all(), request)
        .annotate(ultimo_acceso=Subquery(ultimo))
        .prefetch_related("core_usuario_rol_usuario_set__rol")
    )

    search = (request.GET.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(nombre_completo__icontains=search)
            | Q(correo__icontains=search)
            | Q(puesto__icontains=search)
        )

    for campo, val in filtros.filtros_validados(
        Usuario, request, ("estado", "departamento")
    ).items():
        qs = qs.filter(**{campo: val})

    rol_id = request.GET.get("rol")
    if rol_id:
        # No es un campo de Usuario: se valida a mano como uuid.
        filtros.uuid_o_400(rol_id, "rol")
        qs = qs.filter(core_usuario_rol_usuario_set__rol_id=rol_id).distinct()

    desde, hasta = filtros.rango_validado(request)
    if desde:
        qs = qs.filter(created_at__gte=desde)
    if hasta:
        qs = qs.filter(created_at__lte=hasta)

    campo = _ORDEN_DIRECTORIO[filtros.clave_orden(request, _ORDEN_DIRECTORIO, "nombre")]
    if request.GET.get("desc", "").strip().lower() in _TRUE:
        campo = "-" + campo
    qs = qs.order_by(campo)

    envelope = paginate(qs, request, serialize_usuario_directorio)
    if envelope["count"] == 0:
        # CA07
        envelope["mensaje"] = "No se encontraron usuarios para los criterios seleccionados."
    return envelope


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
            telefono=(data.get("telefono") or "").strip() or None,
            puesto=(data.get("puesto") or "").strip() or None,
            departamento=(data.get("departamento") or "").strip() or None,
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
    """Un unico mecanismo de verificacion de identidad por token de un solo
    uso (24 h), para los dos flujos que lo necesitan:

      · Alta de cuenta (RF-05): el usuario aun no tiene password_hash, asi que
        el token es el unico camino para fijar su contrasena.
      · Reconfirmacion de correo (RF-07/RN05): el usuario ya tiene contrasena
        y solo confirma su nueva direccion; NO se le pide ni se le cambia la
        contrasena.

    Cual de los dos aplica lo decide el estado del propio registro, no un
    parametro del cliente: si password_hash es NULL hay que fijar contrasena,
    y si no, no. Corre fuera del login (RF-16) y sin JWT, porque en ambos
    casos el usuario no puede autenticarse todavia.

    El enrolamiento obligatorio de MFA que la ERS describe en el flujo de alta
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

    debe_fijar_password = not usuario.password_hash
    if debe_fijar_password:
        _validar_password(usuario.tenant, password)
    elif password:
        # Se rechaza en vez de ignorarse: aceptar la contrasena en silencio
        # dejaria al usuario creyendo que la cambio, y convertiria un enlace
        # de confirmacion de correo en un restablecimiento de credenciales.
        raise BusinessRuleError(
            "Esta cuenta ya tiene contrasena; este enlace solo confirma el correo. "
            "Para restablecerla use el flujo de recuperacion.",
            campo="password",
        )

    # El responsable es el propio usuario, que todavia no tiene sesion: se
    # pasa explicitamente para que la bitacora no lo registre como una
    # escritura anonima.
    with audit_context(request, tenant_id=usuario.tenant_id, usuario_id=usuario.id):
        # El token se consume dentro del mismo UPDATE que aplica el cambio,
        # para que el enlace sea atomicamente de un solo uso.
        comun = (
            " estado = 'activo', token_activacion = NULL, token_activacion_exp = NULL,"
            " intentos_fallidos = 0, bloqueado_hasta = NULL"
            " WHERE id = %s AND token_activacion = %s"
        )
        with connection.cursor() as cursor:
            if debe_fijar_password:
                # El hash lo calcula Postgres con pgcrypto (bcrypt, cost 12),
                # el mismo algoritmo que verifica core.intentar_login; Django
                # nunca almacena ni deriva la contrasena.
                cursor.execute(
                    'UPDATE "core"."usuario"'
                    " SET password_hash = crypt(%s, gen_salt('bf', 12))," + comun,
                    [password, str(usuario.id), _hash_token(token)],
                )
            else:
                cursor.execute(
                    'UPDATE "core"."usuario" SET' + comun,
                    [str(usuario.id), _hash_token(token)],
                )

            if cursor.rowcount != 1:
                raise BusinessRuleError(
                    "El enlace de activacion es invalido o expiro.", campo="token"
                )

    usuario.refresh_from_db()
    return usuario


def reenviar_activacion(usuario, request):
    """Cierre del flujo de alta de RF-05: el TENANT_ADMIN reemite el correo con
    el token de un usuario que sigue esperando activarse.

    Sin esta via, un enlace vencido (24 h) o que nunca llego deja la cuenta
    atrapada: RN03 mantiene la activacion fuera del login (RF-16), y el
    restablecimiento de contrasena (RF-18) exige una cuenta ya activa. La unica
    salida era dar de alta otro usuario con otro correo. Es el equivalente, en
    el ambito del tenant, de lo que reenviar_activacion de RF-01 hace para el
    administrador inicial.

    Cubre los dos estados que esperan un token, y encola el correo que
    corresponde a cada uno:

      · 'pendiente'              -> alta sin contrasena: correo de activacion.
      · 'pendiente_verificacion' -> cambio de correo (RF-07\\RN05): correo de
                                    confirmacion de la direccion nueva.

    ROTA el token: emite uno nuevo y descarta el anterior en la misma
    escritura, de modo que un enlace filtrado deja de servir en cuanto se
    reenvia. El token en claro se devuelve una sola vez (en la tabla vive su
    hash), igual que en el alta.

    Devuelve (usuario, token). El estado no cambia: sigue esperando a que el
    titular consuma el enlace.
    """
    if usuario.tenant.estado != "activo":
        raise BusinessRuleError(
            "El tenant no esta activo; no admite reenvios de activacion.",
            extra={"estado_tenant": usuario.tenant.estado},
        )

    if usuario.estado not in ("pendiente", "pendiente_verificacion"):
        raise BusinessRuleError(
            "Este usuario no esta esperando activacion; no hay enlace que reenviar.",
            campo="estado",
            extra={"estado": usuario.estado},
        )

    token, token_hash = _nuevo_token_activacion()
    now = timezone.now()

    with audit_context(request, tenant_id=usuario.tenant_id):
        usuario.token_activacion = token_hash
        usuario.token_activacion_exp = now + ACTIVACION_VIGENCIA
        usuario.updated_at = now
        usuario.save(
            update_fields=["token_activacion", "token_activacion_exp", "updated_at"]
        )

        if usuario.estado == "pendiente":
            _encolar_correo_activacion(usuario, token, now)
        else:
            _encolar_correo_verificacion(usuario, token, now)

    return usuario, token


# ----------------------------------------------------------------- RF-08

def _es_ultimo_admin_activo(usuario):
    """RF-08/RN04: True si el usuario tiene el rol de sistema (TENANT_ADMIN) y
    no queda ningun OTRO usuario activo con ese rol en el tenant. Mismo
    criterio que RF-15/RN02, pero medido sobre el estado del usuario."""
    tiene_admin = UsuarioRol.objects.filter(
        usuario=usuario, rol__es_sistema=True, rol__activo=True
    ).exists()
    if not tiene_admin:
        return False
    otros = (
        UsuarioRol.objects.filter(
            rol__es_sistema=True,
            rol__activo=True,
            rol__tenant=usuario.tenant,
            usuario__estado="activo",
        )
        .exclude(usuario=usuario)
        .values("usuario")
        .distinct()
        .count()
    )
    return otros == 0


def suspender_usuario(usuario, request):
    """RF-08: suspende un usuario. No borra informacion (RN01). Cierra todas
    sus sesiones de inmediato (RN03/CA02) y bloquea el login (RN02: el
    validador ya rechaza 'suspendido' con el mensaje de CA03). No puede
    suspenderse al ultimo TENANT_ADMIN activo (RN04).

    Fuera de alcance (la ERS lo difiere al Modulo de Workflow, Fase 1): el
    bloqueo de aprobaciones pendientes del usuario suspendido (RN06/CA07). No
    existe entidad de aprobacion ni la bandera is_blocked_by_suspension en el
    esquema de Fase 0.
    """
    if usuario.estado == "suspendido":
        raise BusinessRuleError("El usuario ya esta suspendido.", campo="estado")
    if _es_ultimo_admin_activo(usuario):
        raise BusinessRuleError(
            "No puede suspenderse al ultimo administrador activo del tenant.",
            campo="estado",
        )

    with audit_context(request, tenant_id=usuario.tenant_id):
        usuario.estado = "suspendido"
        usuario.updated_at = timezone.now()
        usuario.save(update_fields=["estado", "updated_at"])
        # RN03/CA02: cierre inmediato de todas las sesiones. El actor es el
        # TENANT_ADMIN que ejecuta la suspension (request.usuario_id).
        session_service.revocar_todas_de_usuario(request, usuario.id)

    return usuario


def reactivar_usuario(usuario, request):
    """RF-08: reactiva un usuario suspendido. RN05: conserva todos los roles
    previos (no se tocan). CA04: el login vuelve a funcionar de inmediato."""
    if usuario.estado == "activo":
        raise BusinessRuleError("El usuario ya esta activo.", campo="estado")
    if usuario.estado != "suspendido":
        # 'pendiente'/'pendiente_verificacion' se resuelven por activacion
        # (RF-05) o reconfirmacion (RF-07), no por esta via.
        raise BusinessRuleError(
            "Solo puede reactivarse un usuario suspendido.", campo="estado"
        )

    with audit_context(request, tenant_id=usuario.tenant_id):
        usuario.estado = "activo"
        usuario.updated_at = timezone.now()
        usuario.save(update_fields=["estado", "updated_at"])

    return usuario


# ----------------------------------------------------------------- RF-18

def solicitar_restablecimiento(data, request):
    """RF-18: el usuario pide restablecer su contrasena olvidada. Siempre
    responde el mismo mensaje generico (RN02/CA01): NUNCA revela si el correo
    existe, ni devuelve el token (a diferencia de RF-05/07, aqui el solicitante
    puede no ser el titular). Si el usuario existe y esta activo, se emite un
    token de un solo uso (1 h, RN01) y se encola el correo.

    El token se guarda (hasheado) en token_activacion, reutilizando esa columna
    de "accion de un solo uso"; implica que solo puede haber una accion de token
    pendiente a la vez (activacion / reconfirmacion de correo / reset).
    """
    tenant_slug = (data.get("tenant_slug") or "").strip()
    correo = (data.get("correo") or "").strip()

    usuario = (
        Usuario.objects.select_related("tenant")
        .filter(tenant__slug=tenant_slug, correo=correo, estado="activo", tenant__estado="activo")
        .first()
    )
    if usuario is None:
        return  # RN02: mismo resultado que si existiera

    token, token_hash = _nuevo_token_activacion()
    now = timezone.now()
    with audit_context(request, tenant_id=usuario.tenant_id, usuario_id=usuario.id):
        usuario.token_activacion = token_hash
        usuario.token_activacion_exp = now + RESET_VIGENCIA
        usuario.updated_at = now
        usuario.save(update_fields=["token_activacion", "token_activacion_exp", "updated_at"])
        _encolar_correo(
            usuario, token, now,
            asunto="Restablecimiento de contrasena NovaERP",
            instruccion="restablezca su contrasena",
            ruta="/restablecer",
        )


def restablecer_password(data, request):
    """RF-18: consume el token de un solo uso y fija la nueva contrasena.
    CA02: token expirado o ya usado -> mismo error, sin distinguir el motivo.
    RN03/CA03: al restablecer, se invalidan TODAS las sesiones previas.
    CA04: la solicitud y la confirmacion quedan auditadas (fn_auditar + el
    evento de revocacion de sesiones)."""
    token = (data.get("token") or "").strip()
    password = data.get("password") or ""
    if not token:
        raise BusinessRuleError("token es obligatorio.", campo="token")

    usuario = (
        Usuario.objects.select_related("tenant")
        .filter(token_activacion=_hash_token(token))
        .first()
    )
    if (
        usuario is None
        or usuario.token_activacion_exp is None
        or usuario.token_activacion_exp <= timezone.now()
        or usuario.tenant.estado != "activo"
    ):
        raise BusinessRuleError(
            "El enlace de restablecimiento es invalido o expiro.", campo="token"
        )

    _validar_password(usuario.tenant, password)

    with audit_context(request, tenant_id=usuario.tenant_id, usuario_id=usuario.id):
        with connection.cursor() as cursor:
            # El hash lo calcula Postgres (bcrypt), como en la activacion. Se
            # consume el token en el mismo UPDATE (un solo uso).
            cursor.execute(
                'UPDATE "core"."usuario" SET password_hash = crypt(%s, gen_salt(\'bf\', 12)),'
                " token_activacion = NULL, token_activacion_exp = NULL,"
                " intentos_fallidos = 0, bloqueado_hasta = NULL"
                " WHERE id = %s AND token_activacion = %s",
                [password, str(usuario.id), _hash_token(token)],
            )
            if cursor.rowcount != 1:
                raise BusinessRuleError(
                    "El enlace de restablecimiento es invalido o expiro.", campo="token"
                )
        # RN03/CA03: invalidar todas las sesiones previas. El actor es el propio
        # usuario (no hay sesion autenticada en este flujo).
        session_service.revocar_todas_de_usuario(request, usuario.id, revocada_por=usuario.id)

    usuario.refresh_from_db()
    return usuario


# ----------------------------------------------------------------- RF-07

# La ERS define dos actores con alcances distintos. El propietario solo toca
# "datos personales: nombre, telefono"; el correo, el puesto y el departamento
# no estan en esa lista (puesto/departamento son datos de la organizacion que
# gestiona el TENANT_ADMIN, RF-06), asi que cambiarlos exige core:usuarios:editar.
CAMPOS_PROPIOS = ("nombre_completo", "telefono")
CAMPOS_ADMIN = ("nombre_completo", "telefono", "correo", "puesto", "departamento")

# Campos que ningun actor puede tocar por esta via. id y tenant son inmutables
# (RN01/RN02); estado es RF-08; los roles son RF-14/RF-15; el resto son
# atributos de seguridad que solo cambian por sus flujos dedicados.
CAMPOS_PROHIBIDOS = {
    "id", "tenant", "tenant_id", "tenant_slug",
    "roles", "rol", "rol_id",
    "estado",
    "password", "password_hash", "mfa_secret", "mfa_enrolado",
    "token_activacion", "token_activacion_exp",
    "intentos_fallidos", "bloqueado_hasta",
    "created_at", "updated_at",
}


def editar_usuario(usuario, data, request):
    """RF-07: modifica la informacion de un usuario sin tocar su historial.

    Separacion de responsabilidades: este servicio NO administra roles. Las
    invariantes de la ERS que hablan de roles (CA03, todo usuario conserva al
    menos uno; RN06, no dejar al tenant sin administrador) las garantizan
    RF-14/RF-15 y el trigger core.validar_usuario_rol_minimo, que aplican a
    cualquier escritura venga de donde venga.

    RN05/CA04: si cambia el correo, el usuario pasa a 'pendiente_verificacion'
    y debe reconfirmarlo antes del siguiente login. No hay que bloquear el
    login a mano: core.intentar_login ya rechaza cualquier estado distinto de
    'activo'.
    """
    es_propio = str(usuario.id) == str(request.usuario_id)
    permitidos = CAMPOS_PROPIOS if es_propio else CAMPOS_ADMIN

    enviados_prohibidos = sorted(CAMPOS_PROHIBIDOS & set(data))
    if enviados_prohibidos:
        raise BusinessRuleError(
            "Estos campos no se modifican por esta via; use el requerimiento "
            "correspondiente (roles: RF-14/RF-15, estado: RF-08, credenciales: RF-18).",
            campo=enviados_prohibidos[0],
            extra={"campos_no_editables": enviados_prohibidos},
        )

    fuera_de_alcance = sorted(set(data) - set(permitidos))
    if fuera_de_alcance:
        raise BusinessRuleError(
            "Solo puede modificar sus datos personales."
            if es_propio
            else "Campos no reconocidos para la edicion de usuario.",
            campo=fuera_de_alcance[0],
            extra={"campos_permitidos": list(permitidos)},
        )

    token = None
    now = timezone.now()
    correo_cambio = False

    if "nombre_completo" in data:
        nombre_completo = (data["nombre_completo"] or "").strip()
        if not nombre_completo:
            raise BusinessRuleError(
                "nombre_completo no puede quedar vacio.", campo="nombre_completo"
            )
        usuario.nombre_completo = nombre_completo

    if "telefono" in data:
        usuario.telefono = (data["telefono"] or "").strip() or None

    if "puesto" in data:
        usuario.puesto = (data["puesto"] or "").strip() or None

    if "departamento" in data:
        usuario.departamento = (data["departamento"] or "").strip() or None

    if "correo" in data:
        # CA02: se re-ejecutan las mismas validaciones del alta (RF-05).
        correo = (data["correo"] or "").strip()
        _validar_correo(correo)
        if correo.lower() != (usuario.correo or "").lower():
            # RN03: sigue siendo unico dentro del tenant tras la edicion.
            if (
                Usuario.objects.filter(tenant_id=usuario.tenant_id, correo=correo)
                .exclude(pk=usuario.pk)
                .exists()
            ):
                raise BusinessRuleError(
                    "El correo ya esta registrado en este tenant.", campo="correo"
                )
            usuario.correo = correo
            correo_cambio = True

    with audit_context(request, tenant_id=usuario.tenant_id):
        if correo_cambio:
            token, token_hash = _nuevo_token_activacion()
            usuario.estado = "pendiente_verificacion"
            usuario.token_activacion = token_hash
            usuario.token_activacion_exp = now + ACTIVACION_VIGENCIA

        usuario.updated_at = now
        usuario.save()

        if correo_cambio:
            _encolar_correo_verificacion(usuario, token, now)

    return usuario, token


# --------------------------------------------------- RF-07 / RF-16 (RN07)

def resetear_mfa(usuario, request):
    """RN07: reseteo del segundo factor por perdida de dispositivo, accion
    exclusiva del TENANT_ADMIN (la vista exige core:usuarios:reset_mfa, sin el
    bypass de auto-edicion). Deja al usuario sin secreto y sin enrolar.

    No persiste ningun "token de re-enrolamiento": la maquina de estados de
    RF-16 ya obliga a re-enrolar en el proximo login (mfa_secret NULL -> la
    fase 1 emite un reto de enrolamiento antes de completar el acceso). Asi se
    cumple RN07 ("consumir en su siguiente login") sin almacenamiento nuevo.

    Las sesiones ya emitidas no se revocan (eso es RF-17/RF-19); solo el
    proximo login exige el nuevo segundo factor. La escritura se audita sola
    (fn_auditar) con el TENANT_ADMIN como responsable.
    """
    usuario.mfa_secret = None
    usuario.mfa_enrolado = False
    usuario.updated_at = timezone.now()
    with audit_context(request, tenant_id=usuario.tenant_id):
        usuario.save(update_fields=["mfa_secret", "mfa_enrolado", "updated_at"])
    return usuario
