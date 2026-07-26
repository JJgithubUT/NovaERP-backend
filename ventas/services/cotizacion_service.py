import datetime
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import connection
from django.utils import timezone

from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError
from core.utils.pagination import paginate
from core.utils.permissions import exigir_permiso, tiene_permiso
from inventario.models import Producto
from ventas.models import Cliente, Cotizacion, CotizacionLinea, ConfigVentas, Oportunidad

CENTAVO = Decimal("0.01")
ESTADOS_EDITABLES = {"borrador", "pendiente_aprobacion"}
PERMISO_AJUSTAR_PRECIO = "ventas:cotizaciones:ajustar_precio"


def _to_decimal(value, campo):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise BusinessRuleError(f"{campo} debe ser un numero valido.", campo=campo)


def _config(tenant):
    cfg, _ = ConfigVentas.objects.get_or_create(tenant=tenant)
    return cfg


def _parse_date(valor):
    """vigente_hasta -> date (o None). Se parsea aqui para que el objeto en
    memoria tenga un date real (comparaciones de 'vencida' sin round-trip)."""
    if not valor:
        return None
    if isinstance(valor, datetime.date):
        return valor
    try:
        return datetime.date.fromisoformat(str(valor))
    except ValueError:
        raise BusinessRuleError("vigente_hasta debe tener formato YYYY-MM-DD.", campo="vigente_hasta")


def _generar_folio(tenant):
    n = Cotizacion.objects.filter(tenant=tenant).count()
    for intento in range(5):
        folio = f"COT-{n + 1 + intento:06d}"
        if not Cotizacion.objects.filter(tenant=tenant, folio=folio).exists():
            return folio
    raise BusinessRuleError("No fue posible generar un folio de cotizacion unico.")


def _validar_lineas(tenant, lineas_data, request):
    """RF-34/RN01: el precio se toma del catalogo del producto; un precio manual
    distinto exige ventas:cotizaciones:ajustar_precio (y queda auditado por la
    escritura). Devuelve la lista normalizada."""
    if not lineas_data:
        raise BusinessRuleError("La cotizacion debe tener al menos una linea.", campo="lineas")

    resultado = []
    for i, linea in enumerate(lineas_data):
        pid = linea.get("producto_id")
        if not pid:
            raise BusinessRuleError("producto_id es obligatorio.", campo=f"lineas[{i}].producto_id")
        try:
            producto = Producto.objects.get(tenant=tenant, id=pid, activo=True)
        except (Producto.DoesNotExist, ValueError):
            raise BusinessRuleError("Producto no encontrado o dado de baja.", campo=f"lineas[{i}].producto_id")

        cantidad = _to_decimal(linea.get("cantidad"), f"lineas[{i}].cantidad")
        if cantidad <= 0:
            raise BusinessRuleError("cantidad debe ser > 0.", campo=f"lineas[{i}].cantidad")

        if linea.get("precio_unitario") is not None:
            precio = _to_decimal(linea["precio_unitario"], f"lineas[{i}].precio_unitario")
            if precio < 0:
                raise BusinessRuleError("precio_unitario debe ser >= 0.", campo=f"lineas[{i}].precio_unitario")
            # Ajuste manual solo si difiere del catalogo (RN01).
            if precio != producto.precio_venta:
                exigir_permiso(request, PERMISO_AJUSTAR_PRECIO)
        else:
            precio = producto.precio_venta

        descripcion = (linea.get("descripcion") or producto.nombre).strip()
        resultado.append({"producto": producto, "descripcion": descripcion,
                          "cantidad": cantidad, "precio": precio})
    return resultado


def _insertar_lineas(cotizacion_id, lineas):
    """Inserta las lineas via SQL: cotizacion_linea.importe es GENERATED ALWAYS,
    asi que la DB la calcula; el ORM no puede escribir esa columna."""
    with connection.cursor() as cursor:
        for l in lineas:
            cursor.execute(
                "INSERT INTO ventas.cotizacion_linea "
                "(id, cotizacion_id, producto_id, descripcion, cantidad, precio_unitario) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [str(uuid.uuid4()), str(cotizacion_id), str(l["producto"].id),
                 l["descripcion"], str(l["cantidad"]), str(l["precio"])],
            )


def _totales(lineas, descuento_pct):
    subtotal = sum((l["cantidad"] * l["precio"] for l in lineas), Decimal("0")).quantize(CENTAVO, ROUND_HALF_UP)
    total = (subtotal * (Decimal("100") - descuento_pct) / Decimal("100")).quantize(CENTAVO, ROUND_HALF_UP)
    return subtotal, total


def _vencida(cot):
    return bool(cot.vigente_hasta and cot.vigente_hasta < timezone.now().date()
                and cot.estado not in ("aprobada", "rechazada"))


def serialize_cotizacion(cot):
    lineas = cot.ventas_cotizacion_linea_cotizacion_set.all().order_by("id")
    return {
        "id": str(cot.id),
        "folio": cot.folio,
        "cliente_id": str(cot.cliente_id),
        "oportunidad_id": str(cot.oportunidad_id) if cot.oportunidad_id else None,
        "estado": cot.estado,
        "vencida": _vencida(cot),
        "subtotal": str(cot.subtotal),
        "descuento_pct": str(cot.descuento_pct),
        "total": str(cot.total),
        "vigente_hasta": cot.vigente_hasta.isoformat() if cot.vigente_hasta else None,
        "lineas": [{
            "id": str(l.id), "producto_id": str(l.producto_id), "descripcion": l.descripcion,
            "cantidad": str(l.cantidad), "precio_unitario": str(l.precio_unitario),
            "importe": str(l.importe),
        } for l in lineas],
        "created_at": cot.created_at.isoformat(),
        "updated_at": cot.updated_at.isoformat(),
    }


# --------------------------------------------------------------- RF-34

def crear_cotizacion(data, request):
    """RF-34: crea una cotizacion en 'borrador'. Precio del catalogo (RN01),
    totales automaticos (RN02). RN03: si el descuento supera el maximo del tenant
    (config_ventas.descuento_max_pct), nace 'pendiente_aprobacion' en vez de
    'borrador' (el flujo formal de aprobacion es BPM, RF-83/86, fuera de alcance:
    la liberacion a 'aprobada' es RF-37 manual, mismo stand-in que Compras)."""
    tenant = get_tenant(request)

    try:
        cliente = Cliente.objects.get(tenant=tenant, id=data.get("cliente_id"), activo=True)
    except (Cliente.DoesNotExist, ValueError):
        raise BusinessRuleError("Cliente no encontrado o dado de baja.", campo="cliente_id")

    oportunidad = None
    if data.get("oportunidad_id"):
        try:
            oportunidad = Oportunidad.objects.get(tenant=tenant, id=data["oportunidad_id"])
        except (Oportunidad.DoesNotExist, ValueError):
            raise BusinessRuleError("Oportunidad no encontrada.", campo="oportunidad_id")

    descuento_pct = _to_decimal(data.get("descuento_pct") or 0, "descuento_pct")
    if not (0 <= descuento_pct <= 100):
        raise BusinessRuleError("descuento_pct debe estar entre 0 y 100.", campo="descuento_pct")

    lineas = _validar_lineas(tenant, data.get("lineas"), request)
    subtotal, total = _totales(lineas, descuento_pct)

    cfg = _config(tenant)
    estado = "pendiente_aprobacion" if descuento_pct > cfg.descuento_max_pct else "borrador"

    now = timezone.now()
    with audit_context(request, tenant_id=tenant.id):
        cot = Cotizacion.objects.create(
            id=uuid.uuid4(), tenant=tenant, folio=_generar_folio(tenant), cliente=cliente,
            oportunidad=oportunidad, estado=estado, subtotal=subtotal, descuento_pct=descuento_pct,
            total=total, vigente_hasta=_parse_date(data.get("vigente_hasta")),
            created_at=now, updated_at=now,
        )
        _insertar_lineas(cot.id, lineas)
    return cot


# --------------------------------------------------------------- RF-35

def get_cotizacion(request, pk):
    tenant = get_tenant(request)
    try:
        return Cotizacion.objects.get(tenant=tenant, id=pk)
    except ValueError:
        raise Cotizacion.DoesNotExist


def listar_cotizaciones(request):
    """RF-35: listado paginado con filtros por cliente, estado y rango de fecha.
    ?vencida=true acota a las vencidas (derivado de vigente_hasta)."""
    tenant = get_tenant(request)
    qs = Cotizacion.objects.filter(tenant=tenant)

    for campo in ("cliente_id", "estado"):
        val = request.GET.get(campo)
        if val:
            qs = qs.filter(**{campo: val})
    desde, hasta = request.GET.get("desde"), request.GET.get("hasta")
    if desde:
        qs = qs.filter(created_at__gte=desde)
    if hasta:
        qs = qs.filter(created_at__lte=hasta)
    if (request.GET.get("vencida") or "").lower() in ("1", "true"):
        qs = qs.filter(vigente_hasta__lt=timezone.now().date()).exclude(estado__in=("aprobada", "rechazada"))

    qs = qs.order_by("-created_at")
    return paginate(qs, request, serialize_cotizacion)


# --------------------------------------------------------------- RF-36

def editar_cotizacion(cot, data, request):
    """RF-36: solo editable en 'borrador' o 'pendiente_aprobacion'. Una aprobada,
    rechazada o ya convertida en pedido no se edita (requiere nueva version;
    el versionado formal no esta en el esquema -> se bloquea la edicion y se
    regenera creando otra cotizacion: desviacion documentada)."""
    if cot.estado not in ESTADOS_EDITABLES:
        raise BusinessRuleError(
            "La cotizacion no puede editarse en su estado actual; genere una nueva.",
            campo="estado", extra={"estado_actual": cot.estado},
        )

    tenant = cot.tenant
    with audit_context(request, tenant_id=tenant.id):
        if "descuento_pct" in data:
            descuento_pct = _to_decimal(data["descuento_pct"], "descuento_pct")
            if not (0 <= descuento_pct <= 100):
                raise BusinessRuleError("descuento_pct debe estar entre 0 y 100.", campo="descuento_pct")
            cot.descuento_pct = descuento_pct
        if "vigente_hasta" in data:
            cot.vigente_hasta = _parse_date(data["vigente_hasta"])

        if "lineas" in data:
            lineas = _validar_lineas(tenant, data["lineas"], request)
            CotizacionLinea.objects.filter(cotizacion=cot).delete()
            _insertar_lineas(cot.id, lineas)
            cot.subtotal, cot.total = _totales(lineas, cot.descuento_pct)
        elif "descuento_pct" in data:
            # recalcula el total con las lineas existentes si solo cambio el descuento
            lineas = [{"cantidad": l.cantidad, "precio": l.precio_unitario}
                      for l in CotizacionLinea.objects.filter(cotizacion=cot)]
            cot.subtotal, cot.total = _totales(lineas, cot.descuento_pct)

        # RN03: re-evalua el umbral de descuento tras el cambio.
        cfg = _config(tenant)
        cot.estado = "pendiente_aprobacion" if cot.descuento_pct > cfg.descuento_max_pct else "borrador"
        cot.updated_at = timezone.now()
        cot.save()
    return cot


# --------------------------------------------------------------- RF-37

def resolver_cotizacion(cot, data, request):
    """RF-37: aprueba o rechaza. RN01: una cotizacion vencida no puede aprobarse.
    Solo desde 'borrador'/'pendiente_aprobacion'. Canal interno (el portal de
    cliente es fase futura, fuera de alcance). Queda auditado quien/cuando."""
    if cot.estado not in ESTADOS_EDITABLES:
        raise BusinessRuleError(
            "La cotizacion ya fue resuelta.", campo="estado", extra={"estado_actual": cot.estado}
        )

    decision = (data.get("decision") or "").strip()
    if decision not in ("aprobar", "rechazar"):
        raise BusinessRuleError("decision debe ser 'aprobar' o 'rechazar'.", campo="decision")

    if decision == "aprobar" and _vencida(cot):
        raise BusinessRuleError(
            "La cotizacion esta vencida; genere una nueva version con vigencia actualizada.",
            campo="estado",
        )

    cot.estado = "aprobada" if decision == "aprobar" else "rechazada"
    cot.updated_at = timezone.now()
    with audit_context(request, tenant_id=cot.tenant_id):
        cot.save(update_fields=["estado", "updated_at"])
    return cot
