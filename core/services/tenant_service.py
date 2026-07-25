import re
import uuid

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from core.models import (
    ConfigSeguridadTenant,
    DominioReservado,
    LogAuditoria,
    Modulo,
    ModuloDependencia,
    PlanComercial,
    PlanModulo,
    Rol,
    Sesion,
    TenantModulo,
    Usuario,
    UsuarioRol,
)
from core.services import session_service
from core.services.usuario_service import (
    ACTIVACION_VIGENCIA,
    _encolar_correo_activacion,
    _hash_token,
    _nuevo_token_activacion,
    _validar_correo,
    _validar_password,
    ESTADOS_QUE_CONSUMEN_LICENCIA,
)
from core.utils.audit import audit_context, client_ip, sysadmin_context
from core.utils.errors import BusinessRuleError
from core.utils.pagination import paginate

# RF-01/CA03: el "dominio comercial" es el slug ([a-z0-9-], 3-50), unico a nivel
# plataforma. El campo dominio_comercial (texto libre) queda como giro/nombre.
SLUG_RE = re.compile(r"^[a-z0-9-]{3,50}$")

NOMBRE_ADMIN = "TENANT_ADMIN"

# RF-02/CA01: la ERS acota el listado a 50 registros por pagina como maximo.
MAX_PAGE_SIZE_TENANTS = 50

# RF-02/CA02: campos de orden permitidos (no un ORDER BY arbitrario del cliente).
_ORDEN_TENANTS = {
    "razon_social": "razon_social",
    "created_at": "created_at",
    "estado": "estado",
    "plan": "plan__codigo",
}
_TRUE = {"1", "true", "si", "sí"}


# ------------------------------------------------------- eventos de plataforma

def _evento_plataforma(request, operacion, entidad, entidad_id, tenant_id=None,
                       valores=None, criticidad="NORMAL"):
    """Evento de plataforma (SysAdmin) manual. Las tablas de core ya se auto-
    auditan por trigger (fn_auditar), pero con usuario_id NULL porque el actor
    SysAdmin no vive en core.usuario. Este evento captura al RESPONSABLE (CA08):
    su id va en valores_despues, no en usuario_id (FK a core.usuario)."""
    LogAuditoria.objects.create(
        tenant_id=tenant_id,
        usuario_id=None,
        entidad=entidad,
        entidad_id=str(entidad_id),
        operacion=operacion,
        valores_despues=valores,
        criticidad=criticidad,
        ip_origen=client_ip(request) or None,
        ocurrido_en=timezone.now(),
    )


# -------------------------------------------------------------- serializacion

def serialize_tenant(tenant, detalle=False):
    """RF-02. En listado: datos generales. En detalle (CA05): + modulos activos,
    usuarios registrados y consumo de licencias."""
    data = {
        "id": str(tenant.id),
        "slug": tenant.slug,
        "razon_social": tenant.razon_social,
        "dominio_comercial": tenant.dominio_comercial,
        "plan": {"codigo": tenant.plan.codigo, "nombre": tenant.plan.nombre,
                 "licencias_max": tenant.plan.licencias_max},
        "estado": tenant.estado,
        "motivo_suspension": tenant.motivo_suspension,
        "created_at": tenant.created_at.isoformat(),
        "updated_at": tenant.updated_at.isoformat(),
    }
    if not detalle:
        return data

    modulos = list(
        Modulo.objects.filter(
            core_tenant_modulo_modulo_set__tenant=tenant,
            core_tenant_modulo_modulo_set__activo=True,
        ).order_by("fase", "nombre").values("codigo", "nombre", "fase")
    )
    licencias_consumidas = Usuario.objects.filter(
        tenant=tenant, estado__in=ESTADOS_QUE_CONSUMEN_LICENCIA
    ).count()
    data["modulos_activos"] = modulos
    data["usuarios"] = {
        "registrados": Usuario.objects.filter(tenant=tenant).count(),
        "licencias_consumidas": licencias_consumidas,
        "licencias_max": tenant.plan.licencias_max,
    }
    return data


# ------------------------------------------------------------------- RF-01

def _validar_slug(slug):
    if not slug:
        raise BusinessRuleError("El dominio comercial es obligatorio.", campo="slug")
    if not SLUG_RE.match(slug):
        raise BusinessRuleError(
            "El dominio solo admite minusculas, digitos y guion, de 3 a 50 caracteres.",
            campo="slug",
        )
    # RN07/CA10: no puede coincidir con una palabra reservada de plataforma.
    if DominioReservado.objects.filter(palabra=slug).exists():
        raise BusinessRuleError(
            "Este dominio esta reservado para uso del sistema.", campo="slug"
        )


def crear_tenant(data, request):
    """RF-01: el SysAdmin registra un nuevo tenant. Crea el entorno en estado
    'pendiente' + su config de seguridad + rol TENANT_ADMIN + admin inicial
    'pendiente' con token de activacion (24h), y activa los modulos del plan.

    No crea ningun registro parcial: todo ocurre dentro de una sola transaccion
    (sysadmin_context), asi que un fallo de validacion tardio revierte todo (3a).

    Devuelve (tenant, admin, token_activacion): el token no se persiste en claro;
    se entrega al SysAdmin (y se encola por correo al admin inicial).
    """
    slug = (data.get("slug") or "").strip().lower()
    razon_social = (data.get("razon_social") or "").strip()
    dominio_comercial = (data.get("dominio_comercial") or "").strip()
    correo = (data.get("correo") or "").strip()
    nombre_admin = (data.get("nombre_completo") or "").strip()
    plan_codigo = (data.get("plan") or "").strip()

    # CA02: obligatorios.
    if not razon_social:
        raise BusinessRuleError("La razon social es obligatoria.", campo="razon_social")
    if not nombre_admin:
        raise BusinessRuleError(
            "El nombre del administrador inicial es obligatorio.", campo="nombre_completo"
        )
    _validar_slug(slug)
    _validar_correo(correo)

    # CA05: plan vigente del catalogo.
    plan = PlanComercial.objects.filter(codigo=plan_codigo, activo=True).first()
    if plan is None:
        raise BusinessRuleError(
            "El plan contratado no existe o no esta vigente.", campo="plan"
        )

    # RN03/CA03: dominio (slug) unico a nivel plataforma, incluso de tenants
    # suspendidos o dados de baja.
    from core.models import Tenant
    if Tenant.objects.filter(slug=slug).exists():
        raise BusinessRuleError(
            "El dominio comercial ya se encuentra registrado.", campo="slug"
        )
    # CA09: razon social duplicada (mensaje diferenciado).
    if Tenant.objects.filter(razon_social=razon_social).exists():
        raise BusinessRuleError(
            "La razon social ya se encuentra registrada.", campo="razon_social"
        )
    # RN04: el correo del admin inicial no puede existir en NINGUN tenant.
    if Usuario.objects.filter(correo=correo).exists():
        raise BusinessRuleError(
            "El correo ya esta asociado a un usuario de la plataforma.", campo="correo"
        )

    token, token_hash = _nuevo_token_activacion()
    now = timezone.now()

    with sysadmin_context(request):
        tenant = Tenant.objects.create(
            id=uuid.uuid4(),
            slug=slug,
            razon_social=razon_social,
            dominio_comercial=dominio_comercial or razon_social,
            plan=plan,
            estado="pendiente",
            created_at=now,
            updated_at=now,
        )

        # config_seguridad_tenant: se inserta solo tenant_id para que la DB
        # aplique los DEFAULT de la politica (igual que el seed); crearla por
        # ORM enviaria NULLs a columnas NOT NULL sin default en Python.
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core.config_seguridad_tenant (tenant_id) VALUES (%s)",
                [str(tenant.id)],
            )

        rol_admin = Rol.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            nombre=NOMBRE_ADMIN,
            es_sistema=True,
            activo=True,
            created_at=now,
        )

        # CA06/CA07: admin inicial 'pendiente', sin password, con token 24h.
        admin = Usuario.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            correo=correo,
            nombre_completo=nombre_admin,
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
        UsuarioRol.objects.create(
            usuario=admin, rol=rol_admin, asignado_en=now, asignado_por=None
        )

        # RN05/RN06: activa unicamente los modulos incluidos en el plan (el
        # nucleo esta entre ellos y nunca se deshabilita).
        modulos_plan = list(
            PlanModulo.objects.filter(plan=plan).values_list("modulo_id", flat=True)
        )
        TenantModulo.objects.bulk_create(
            [
                TenantModulo(tenant=tenant, modulo_id=mid, activo=True, activado_en=now)
                for mid in modulos_plan
            ]
        )

        _encolar_correo_activacion(admin, token, now)

        # CA08: evento de plataforma con el SysAdmin responsable.
        _evento_plataforma(
            request, "CREATE_TENANT", "tenant", tenant.id, tenant_id=tenant.id,
            valores={"sysadmin_id": request.sysadmin_id, "slug": slug,
                     "razon_social": razon_social, "plan": plan.codigo},
        )

    return tenant, admin, token


def activar_tenant(data, request):
    """RF-01 (flujo de activacion nuevo): el administrador inicial consume el
    enlace de un solo uso (24h), define su contrasena conforme a la politica del
    tenant (RF-22) y, en cascada, el usuario Y el tenant pasan a 'activo'. Corre
    sobre este endpoint dedicado, NUNCA sobre el login (RF-16/RN08).

    El enrolamiento MFA sigue en el primer login (misma desviacion documentada
    ya vigente para los usuarios de tenant): adelantarlo aqui duplicaria la
    maquina de estados de RF-16. Emite el evento INICIALIZACION_ENTORNO.

    Endpoint publico: el admin aun no puede autenticarse (RN08); la autorizacion
    la da el token, no un JWT.
    """
    token = (data.get("token") or "").strip()
    password = data.get("password") or ""
    if not token:
        raise BusinessRuleError("token es obligatorio.", campo="token")

    admin = (
        Usuario.objects.select_related("tenant")
        .filter(token_activacion=_hash_token(token))
        .first()
    )
    # Mensaje unico para token inexistente/consumido/vencido (no revela nada).
    if (
        admin is None
        or admin.token_activacion_exp is None
        or admin.token_activacion_exp <= timezone.now()
    ):
        raise BusinessRuleError("El enlace de activacion es invalido o expiro.", campo="token")

    tenant = admin.tenant
    # Este flujo es exclusivo del alta de tenant: el tenant debe estar
    # 'pendiente'. Un usuario normal (tenant ya activo) se activa por RF-05.
    if tenant.estado != "pendiente":
        raise BusinessRuleError("La cuenta no puede activarse en este momento.")

    _validar_password(tenant, password)

    with audit_context(request, tenant_id=tenant.id, usuario_id=admin.id):
        with connection.cursor() as cursor:
            # Consumo del token + fijado de password en el mismo UPDATE (un solo
            # uso atomico). El hash lo calcula Postgres (bcrypt), como RF-05.
            cursor.execute(
                'UPDATE "core"."usuario"'
                " SET password_hash = crypt(%s, gen_salt('bf', 12)),"
                "     estado = 'activo', token_activacion = NULL,"
                "     token_activacion_exp = NULL, intentos_fallidos = 0,"
                "     bloqueado_hasta = NULL"
                " WHERE id = %s AND token_activacion = %s",
                [password, str(admin.id), _hash_token(token)],
            )
            if cursor.rowcount != 1:
                raise BusinessRuleError(
                    "El enlace de activacion es invalido o expiro.", campo="token"
                )
            # Cascada: el tenant pasa a activo.
            cursor.execute(
                "UPDATE core.tenant SET estado = 'activo' WHERE id = %s", [str(tenant.id)]
            )

        _evento_plataforma(
            request, "INICIALIZACION_ENTORNO", "tenant", tenant.id, tenant_id=tenant.id,
            valores={"admin_id": str(admin.id), "slug": tenant.slug}, criticidad="ALTA",
        )

    admin.refresh_from_db()
    tenant.refresh_from_db()
    return tenant, admin


# ------------------------------------------------------------------- RF-02

def listar_tenants(request):
    """RF-02: listado de tenants para el SysAdmin. Cross-tenant (sin
    tenant_scoped): la vista ya exige SysAdminRequiredMixin. Busqueda por razon
    social / dominio / id (CA03); filtros por estado, plan y rango de fecha
    (CA04); orden por razon social, fecha, estado o plan (CA02); paginado a 50
    (CA01). RN01 lo garantiza la separacion de superficies (un usuario de tenant
    nunca llega aqui)."""
    from core.models import Tenant

    qs = Tenant.objects.select_related("plan").all()

    search = (request.GET.get("search") or "").strip()
    if search:
        filtro = Q(razon_social__icontains=search) | Q(slug__icontains=search) \
            | Q(dominio_comercial__icontains=search)
        try:
            filtro |= Q(id=uuid.UUID(search))
        except (ValueError, AttributeError):
            pass
        qs = qs.filter(filtro)

    estado = request.GET.get("estado")
    if estado:
        qs = qs.filter(estado=estado)

    plan = request.GET.get("plan")
    if plan:
        qs = qs.filter(plan__codigo=plan)

    desde, hasta = request.GET.get("desde"), request.GET.get("hasta")
    if desde:
        qs = qs.filter(created_at__gte=desde)
    if hasta:
        qs = qs.filter(created_at__lte=hasta)

    campo = _ORDEN_TENANTS.get((request.GET.get("orden") or "razon_social").strip(), "razon_social")
    if request.GET.get("desc", "").strip().lower() in _TRUE:
        campo = "-" + campo
    qs = qs.order_by(campo)

    # CA01: tope de 50 por pagina (mas estricto que el default general de 100).
    request.GET = request.GET.copy()
    try:
        ps = int(request.GET.get("page_size", 50))
    except (TypeError, ValueError):
        ps = 50
    request.GET["page_size"] = str(min(ps, MAX_PAGE_SIZE_TENANTS))

    envelope = paginate(qs, request, lambda t: serialize_tenant(t, detalle=False))
    if envelope["count"] == 0:
        envelope["mensaje"] = "No se encontraron organizaciones para los criterios seleccionados."
    return envelope


def detalle_tenant(tenant_id):
    """RF-02/CA05: vista de detalle de un tenant. Lanza Tenant.DoesNotExist si
    no existe (la vista lo traduce a 404)."""
    from core.models import Tenant

    try:
        tenant = Tenant.objects.select_related("plan").get(id=tenant_id)
    except (ValidationError, ValueError):
        raise Tenant.DoesNotExist
    return serialize_tenant(tenant, detalle=True)


# ------------------------------------------------------------------- RF-03

def _obtener_tenant(tenant_id):
    from core.models import Tenant
    try:
        return Tenant.objects.select_related("plan").get(id=tenant_id)
    except (ValidationError, ValueError):
        raise Tenant.DoesNotExist


def _mapa_dependencias():
    """codigo_modulo -> {codigos de los que depende} y su inverso (quien depende
    de cada modulo). Ambos por codigo, ya resueltos desde core.modulo."""
    deps, inverso = {}, {}
    filas = (
        ModuloDependencia.objects.select_related("modulo", "depende_de")
        .values_list("modulo__codigo", "depende_de__codigo")
    )
    for mod, dep in filas:
        deps.setdefault(mod, set()).add(dep)
        inverso.setdefault(dep, set()).add(mod)
    return deps, inverso


def editar_tenant(tenant_id, data, request):
    """RF-03: el SysAdmin edita datos generales, plan y modulos activos de un
    tenant, sin afectar la integridad de los datos existentes (RN01/RN02).

    Modulos: data['modulos'] = {'activar': [codigos], 'desactivar': [codigos]}.
      · RN03: el nucleo (fase 0) no puede desactivarse.
      · RN05/CA08: no se activa un modulo cuya dependencia no quedara activa.
      · RN07/CA09: desactivar un modulo del que dependen otros activos exige
        confirmar_cascada=true; entonces se apagan tambien los dependientes (todo
        o nada, misma transaccion). Sin confirmar, se rechaza listando los
        afectados por nombre.
      · Solo se pueden activar modulos incluidos en el plan del tenant (RN05).

    Devuelve el tenant. La activacion/desactivacion (tenant_modulo) y el cambio
    de datos/plan se auto-auditan por trigger (CA07); ademas se emite un evento
    de plataforma con el SysAdmin responsable.
    """
    tenant = _obtener_tenant(tenant_id)
    if tenant.estado == "baja_logica":
        raise BusinessRuleError(
            "El tenant esta dado de baja; reactivelo antes de editarlo.", campo="estado"
        )

    cambios = {}

    # --- datos generales ---
    if "razon_social" in data:
        razon = (data["razon_social"] or "").strip()
        if not razon:
            raise BusinessRuleError("La razon social no puede quedar vacia.", campo="razon_social")
        from core.models import Tenant
        if Tenant.objects.filter(razon_social=razon).exclude(id=tenant.id).exists():
            raise BusinessRuleError("La razon social ya se encuentra registrada.", campo="razon_social")
        tenant.razon_social = razon
        cambios["razon_social"] = razon

    if "dominio_comercial" in data:
        tenant.dominio_comercial = (data["dominio_comercial"] or "").strip() or tenant.razon_social
        cambios["dominio_comercial"] = tenant.dominio_comercial

    # --- plan (RN02/RN04/CA03: se permite; no se borran datos ni usuarios) ---
    if "plan" in data:
        plan = PlanComercial.objects.filter(codigo=(data["plan"] or "").strip(), activo=True).first()
        if plan is None:
            raise BusinessRuleError("El plan indicado no existe o no esta vigente.", campo="plan")
        tenant.plan = plan
        cambios["plan"] = plan.codigo

    # --- modulos ---
    modulos_data = data.get("modulos") or {}
    activar = {c.strip().upper() for c in (modulos_data.get("activar") or []) if c}
    desactivar = {c.strip().upper() for c in (modulos_data.get("desactivar") or []) if c}

    aplicar_modulos = None
    if activar or desactivar:
        aplicar_modulos = _resolver_modulos(
            tenant, activar, desactivar, bool(data.get("confirmar_cascada"))
        )

    if not cambios and aplicar_modulos is None:
        raise BusinessRuleError("No se recibio ningun cambio.")

    now = timezone.now()
    with sysadmin_context(request):
        request._sysadmin_tenant_id = tenant.id  # atribuye escrituras al tenant
        if cambios:
            tenant.updated_at = now
            tenant.save()

        if aplicar_modulos is not None:
            encender, apagar = aplicar_modulos
            for mid in encender:
                TenantModulo.objects.update_or_create(
                    tenant=tenant, modulo_id=mid,
                    defaults={"activo": True, "activado_en": now},
                )
            if apagar:
                TenantModulo.objects.filter(tenant=tenant, modulo_id__in=apagar).update(activo=False)
            cambios["modulos_encendidos"] = sorted(encender)
            cambios["modulos_apagados"] = sorted(apagar)

        _evento_plataforma(
            request, "UPDATE_TENANT", "tenant", tenant.id, tenant_id=tenant.id,
            valores={"sysadmin_id": request.sysadmin_id, "cambios": cambios},
        )

    tenant.refresh_from_db()
    return tenant


def _resolver_modulos(tenant, activar, desactivar, confirmar_cascada):
    """Valida y resuelve la activacion/desactivacion de modulos de RF-03.
    Devuelve (ids_a_encender, ids_a_apagar) o lanza BusinessRuleError. Trabaja
    con codigos y traduce a ids al final."""
    catalogo = {m.codigo: m for m in Modulo.objects.all()}
    desconocidos = (activar | desactivar) - set(catalogo)
    if desconocidos:
        raise BusinessRuleError(
            "Modulos no reconocidos: " + ", ".join(sorted(desconocidos)), campo="modulos"
        )

    solapan = activar & desactivar
    if solapan:
        raise BusinessRuleError(
            "Un modulo no puede activarse y desactivarse a la vez: " + ", ".join(sorted(solapan)),
            campo="modulos",
        )

    # RN03: el nucleo (fase 0) nunca se desactiva.
    nucleo = {c for c in desactivar if catalogo[c].fase == 0}
    if nucleo:
        raise BusinessRuleError(
            "El modulo de Identidad y Usuarios (nucleo) no puede desactivarse.",
            campo="modulos", extra={"modulos": sorted(nucleo)},
        )

    # RN05: solo se activan modulos incluidos en el plan del tenant.
    plan_codigos = set(
        PlanModulo.objects.filter(plan=tenant.plan).values_list("modulo__codigo", flat=True)
    )
    fuera_plan = activar - plan_codigos
    if fuera_plan:
        raise BusinessRuleError(
            "El plan del tenant no incluye estos modulos; actualice el plan primero: "
            + ", ".join(sorted(fuera_plan)),
            campo="modulos", extra={"modulos": sorted(fuera_plan)},
        )

    activos = set(
        TenantModulo.objects.filter(tenant=tenant, activo=True).values_list("modulo__codigo", flat=True)
    )
    deps, inverso = _mapa_dependencias()

    # RN07/CA09: desactivar en cascada. Cierre transitivo de dependientes activos.
    if desactivar:
        cerrados = set(desactivar)
        pendientes = set(desactivar)
        afectados = set()
        while pendientes:
            base = pendientes.pop()
            for dependiente in inverso.get(base, set()):
                if dependiente in activos and dependiente not in cerrados:
                    afectados.add(dependiente)
                    cerrados.add(dependiente)
                    pendientes.add(dependiente)
        if afectados and not confirmar_cascada:
            nombres = sorted(catalogo[c].nombre for c in afectados)
            raise BusinessRuleError(
                "Estos modulos dependen de los que intenta desactivar. Confirme la "
                "desactivacion en cascada para apagarlos tambien: " + ", ".join(nombres),
                campo="modulos",
                extra={"requiere_confirmar_cascada": True,
                       "modulos_afectados": sorted(afectados)},
            )
        desactivar = cerrados  # incluye los dependientes si se confirmo

    # Conjunto activo resultante, para validar dependencias de las activaciones.
    proyeccion = (activos | activar) - desactivar

    # RN05/CA08: cada modulo activado necesita sus dependencias activas.
    for codigo in activar:
        faltan = deps.get(codigo, set()) - proyeccion
        if faltan:
            requerido = catalogo[sorted(faltan)[0]].nombre
            raise BusinessRuleError(
                f"Este modulo requiere que [{requerido}] este activo primero.",
                campo="modulos", extra={"modulo": codigo, "requiere": sorted(faltan)},
            )

    encender = [catalogo[c].id for c in activar]
    apagar = [catalogo[c].id for c in desactivar]
    return encender, apagar


# ------------------------------------------------------------------- RF-04

TIPOS_SUSPENSION = ("cumplimiento", "administrativa")


def _revocar_sesiones_tenant(tenant):
    """CA02: invalida de inmediato TODAS las sesiones activas de los usuarios del
    tenant. El actor es el SysAdmin (no un usuario), asi que revocada_por queda
    NULL; el trigger de sesion audita cada revocacion."""
    return Sesion.objects.filter(
        usuario__tenant=tenant, revocada_en__isnull=True
    ).update(revocada_en=timezone.now(), revocada_por=None)


def suspender_tenant(tenant_id, data, request, estado_destino="suspendido"):
    """RF-04: suspende (o da de baja logica) un tenant. No borra informacion
    (RN01/RN02/CA04). Invalida todas las sesiones (CA02) y bloquea el login e
    invocacion de la API (RN03/CA03: el validador ya rechaza tenant no-'activo').
    Reversible (RN05).

    'tipo' (cumplimiento/administrativa) se registra en auditoria. DESVIACION
    DOCUMENTADA (RN06/CA07): el bloqueo diferenciado de EXPORTACION de datos del
    tenant no se implementa porque no existe endpoint de exportacion de datos de
    negocio del TENANT_ADMIN que bloquear (RF-24 exporta la bitacora, no datos).
    """
    tenant = _obtener_tenant(tenant_id)
    if tenant.estado == estado_destino:
        etiqueta = "dado de baja" if estado_destino == "baja_logica" else "suspendido"
        raise BusinessRuleError(f"El tenant ya esta {etiqueta}.", campo="estado")

    tipo = (data.get("tipo") or "administrativa").strip().lower()
    if tipo not in TIPOS_SUSPENSION:
        raise BusinessRuleError(
            "tipo debe ser 'cumplimiento' o 'administrativa'.", campo="tipo"
        )
    motivo = (data.get("motivo") or "").strip()
    if not motivo:
        raise BusinessRuleError("El motivo es obligatorio.", campo="motivo")

    now = timezone.now()
    with sysadmin_context(request):
        request._sysadmin_tenant_id = tenant.id
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE core.tenant SET estado = %s, motivo_suspension = %s WHERE id = %s",
                [estado_destino, motivo, str(tenant.id)],
            )
        revocadas = _revocar_sesiones_tenant(tenant)
        operacion = "TENANT_BAJA" if estado_destino == "baja_logica" else "TENANT_SUSPEND"
        _evento_plataforma(
            request, operacion, "tenant", tenant.id, tenant_id=tenant.id,
            valores={"sysadmin_id": request.sysadmin_id, "tipo": tipo,
                     "motivo": motivo, "sesiones_revocadas": revocadas},
            criticidad="ALTA",
        )

    tenant.refresh_from_db()
    return tenant, revocadas


def reactivar_tenant(tenant_id, request):
    """RF-04/RN05: reactiva un tenant suspendido o dado de baja. Sin perdida de
    informacion. El login vuelve a funcionar de inmediato (el validador acepta
    'activo'). No re-crea sesiones: los usuarios deben volver a autenticarse."""
    tenant = _obtener_tenant(tenant_id)
    if tenant.estado == "activo":
        raise BusinessRuleError("El tenant ya esta activo.", campo="estado")
    if tenant.estado == "pendiente":
        raise BusinessRuleError(
            "El tenant esta pendiente de activacion; no se reactiva por esta via.",
            campo="estado",
        )

    with sysadmin_context(request):
        request._sysadmin_tenant_id = tenant.id
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE core.tenant SET estado = 'activo', motivo_suspension = NULL WHERE id = %s",
                [str(tenant.id)],
            )
        _evento_plataforma(
            request, "TENANT_REACTIVATE", "tenant", tenant.id, tenant_id=tenant.id,
            valores={"sysadmin_id": request.sysadmin_id},
        )

    tenant.refresh_from_db()
    return tenant
