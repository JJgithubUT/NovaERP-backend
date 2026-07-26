import uuid
from decimal import Decimal, InvalidOperation

from django.db import connection
from django.utils import timezone

from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError
from core.utils.permissions import exigir_permiso
from core.utils.pagination import paginate
from inventario.models import Almacen, Producto
from ventas.models import (
    Cliente, ConfigVentas, Cotizacion, CotizacionLinea, PedidoLinea, PedidoVenta,
)

PERMISO_AUTORIZAR_CREDITO = "finanzas:credito:autorizar"


def _to_decimal(value, campo):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise BusinessRuleError(f"{campo} debe ser un numero valido.", campo=campo)


def _config(tenant):
    cfg, _ = ConfigVentas.objects.get_or_create(tenant=tenant)
    return cfg


def _generar_folio(tenant):
    n = PedidoVenta.objects.filter(tenant=tenant).count()
    for intento in range(5):
        folio = f"PED-{n + 1 + intento:06d}"
        if not PedidoVenta.objects.filter(tenant=tenant, folio=folio).exists():
            return folio
    raise BusinessRuleError("No fue posible generar un folio de pedido unico.")


def _dentro_de_credito(cliente_id, total):
    """RF-38/RN02: usa ventas.validar_limite_credito (saldo CxC + monto <= limite)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT ventas.validar_limite_credito(%s, %s)", [str(cliente_id), str(total)])
        return bool(cursor.fetchone()[0])


def serialize_pedido(p):
    lineas = p.ventas_pedido_linea_pedido_set.all().order_by("id")
    return {
        "id": str(p.id),
        "folio": p.folio,
        "cliente_id": str(p.cliente_id),
        "cotizacion_id": str(p.cotizacion_id) if p.cotizacion_id else None,
        "almacen_id": str(p.almacen_id) if p.almacen_id else None,
        "estado": p.estado,
        "total": str(p.total),
        "lineas": [{
            "id": str(l.id), "producto_id": str(l.producto_id),
            "cantidad": str(l.cantidad), "cantidad_facturada": str(l.cantidad_facturada),
            "cantidad_reservada": str(l.cantidad_reservada), "precio_unitario": str(l.precio_unitario),
            "pendiente_facturar": str(l.cantidad - l.cantidad_facturada),
        } for l in lineas],
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


# --------------------------------------------------------------- RF-38 (crear)

def _lineas_desde_data(tenant, lineas_data):
    if not lineas_data:
        raise BusinessRuleError("El pedido debe tener al menos una linea.", campo="lineas")
    resultado = []
    for i, linea in enumerate(lineas_data):
        try:
            producto = Producto.objects.get(tenant=tenant, id=linea.get("producto_id"), activo=True)
        except (Producto.DoesNotExist, ValueError):
            raise BusinessRuleError("Producto no encontrado o dado de baja.", campo=f"lineas[{i}].producto_id")
        cantidad = _to_decimal(linea.get("cantidad"), f"lineas[{i}].cantidad")
        if cantidad <= 0:
            raise BusinessRuleError("cantidad debe ser > 0.", campo=f"lineas[{i}].cantidad")
        if linea.get("precio_unitario") is not None:
            precio = _to_decimal(linea["precio_unitario"], f"lineas[{i}].precio_unitario")
            if precio < 0:
                raise BusinessRuleError("precio_unitario debe ser >= 0.", campo=f"lineas[{i}].precio_unitario")
        else:
            precio = producto.precio_venta
        resultado.append({"producto": producto, "cantidad": cantidad, "precio": precio})
    return resultado


def crear_pedido(data, request):
    """RF-38: crea un pedido en 'borrador'. RN01: se origina de una cotizacion
    APROBADA (copia sus lineas) o se crea directo con lineas propias. La reserva
    de stock y la validacion de credito ocurren al CONFIRMAR (confirmar_pedido),
    no al crear el borrador."""
    tenant = get_tenant(request)

    cotizacion = None
    if data.get("cotizacion_id"):
        try:
            cotizacion = Cotizacion.objects.select_related("cliente").get(
                tenant=tenant, id=data["cotizacion_id"]
            )
        except (Cotizacion.DoesNotExist, ValueError):
            raise BusinessRuleError("Cotizacion no encontrada.", campo="cotizacion_id")
        if cotizacion.estado != "aprobada":
            raise BusinessRuleError(
                "Solo una cotizacion aprobada puede convertirse en pedido.", campo="cotizacion_id"
            )
        cliente = cotizacion.cliente
        lineas = [
            {"producto_id": l.producto_id, "producto": None, "cantidad": l.cantidad, "precio": l.precio_unitario}
            for l in CotizacionLinea.objects.filter(cotizacion=cotizacion)
        ]
        # resolver Producto objetos (validando que sigan activos)
        for l in lineas:
            try:
                l["producto"] = Producto.objects.get(tenant=tenant, id=l["producto_id"], activo=True)
            except (Producto.DoesNotExist, ValueError):
                raise BusinessRuleError("Un producto de la cotizacion ya no esta disponible.", campo="lineas")
    else:
        try:
            cliente = Cliente.objects.get(tenant=tenant, id=data.get("cliente_id"), activo=True)
        except (Cliente.DoesNotExist, ValueError):
            raise BusinessRuleError("Cliente no encontrado o dado de baja.", campo="cliente_id")
        lineas = _lineas_desde_data(tenant, data.get("lineas"))

    total = sum((l["cantidad"] * l["precio"] for l in lineas), Decimal("0")).quantize(Decimal("0.01"))
    now = timezone.now()
    with audit_context(request, tenant_id=tenant.id):
        pedido = PedidoVenta.objects.create(
            id=uuid.uuid4(), tenant=tenant, folio=_generar_folio(tenant), cliente=cliente,
            cotizacion=cotizacion, estado="borrador", total=total, almacen=None,
            created_at=now, updated_at=now,
        )
        PedidoLinea.objects.bulk_create([
            PedidoLinea(id=uuid.uuid4(), pedido=pedido, producto=l["producto"], cantidad=l["cantidad"],
                        cantidad_facturada=Decimal("0"), cantidad_reservada=Decimal("0"),
                        precio_unitario=l["precio"])
            for l in lineas
        ])
    return pedido


# --------------------------------------------------------------- RF-38 (confirmar)

def confirmar_pedido(pedido, data, request):
    """RF-38: confirma un pedido en borrador. Valida credito (RN02) y reserva
    stock (RN03/CA01) en el almacen indicado:
      · credito excedido -> bloquea salvo autorizar_credito=true por un actor con
        finanzas:credito:autorizar.
      · stock suficiente -> reserva y estado 'confirmado'.
      · stock insuficiente y el tenant permite backorder -> reserva lo disponible
        y estado 'pendiente_surtido' (faltante por linea); si no permite backorder,
        se bloquea."""
    if pedido.estado != "borrador":
        raise BusinessRuleError(
            "Solo un pedido en borrador puede confirmarse.", campo="estado",
            extra={"estado_actual": pedido.estado},
        )

    tenant = pedido.tenant
    try:
        almacen = Almacen.objects.get(tenant=tenant, id=data.get("almacen_id"))
    except (Almacen.DoesNotExist, ValueError):
        raise BusinessRuleError("almacen_id es obligatorio y debe existir.", campo="almacen_id")

    # RN02: limite de credito.
    if not _dentro_de_credito(pedido.cliente_id, pedido.total):
        if data.get("autorizar_credito") is True:
            exigir_permiso(request, PERMISO_AUTORIZAR_CREDITO)  # PermissionDenied -> 403
        else:
            raise BusinessRuleError(
                "El pedido excede el limite de credito del cliente; requiere autorizacion.",
                campo="credito", extra={"permiso_requerido": PERMISO_AUTORIZAR_CREDITO},
            )

    cfg = _config(tenant)
    backorder = cfg.permite_backorder
    lineas = list(pedido.ventas_pedido_linea_pedido_set.all())

    now = timezone.now()
    todo_reservado = True
    with audit_context(request, tenant_id=tenant.id):
        with connection.cursor() as cursor:
            for l in lineas:
                cursor.execute(
                    "SELECT cantidad, reservado FROM inventario.stock_actual "
                    "WHERE tenant_id=%s AND producto_id=%s AND almacen_id=%s FOR UPDATE",
                    [str(tenant.id), str(l.producto_id), str(almacen.id)],
                )
                row = cursor.fetchone()
                disponible = (row[0] - row[1]) if row else Decimal("0")

                if disponible >= l.cantidad:
                    reservar = l.cantidad
                elif backorder:
                    reservar = disponible if disponible > 0 else Decimal("0")
                    todo_reservado = False
                else:
                    raise BusinessRuleError(
                        "Stock insuficiente para confirmar el pedido (backorder deshabilitado).",
                        campo="stock",
                        extra={"producto_id": str(l.producto_id), "disponible": str(disponible),
                               "requerido": str(l.cantidad)},
                    )

                if reservar > 0:
                    cursor.execute(
                        "UPDATE inventario.stock_actual SET reservado = reservado + %s, actualizado_en = now() "
                        "WHERE tenant_id=%s AND producto_id=%s AND almacen_id=%s",
                        [str(reservar), str(tenant.id), str(l.producto_id), str(almacen.id)],
                    )
                l.cantidad_reservada = reservar
                l.save(update_fields=["cantidad_reservada"])

        pedido.almacen = almacen
        pedido.estado = "confirmado" if todo_reservado else "pendiente_surtido"
        pedido.updated_at = now
        pedido.save(update_fields=["almacen", "estado", "updated_at"])

    pedido.refresh_from_db()
    return pedido


# --------------------------------------------------------------- RF-39 / RF-40

def get_pedido(request, pk):
    tenant = get_tenant(request)
    try:
        return PedidoVenta.objects.get(tenant=tenant, id=pk)
    except ValueError:
        raise PedidoVenta.DoesNotExist


def listar_pedidos(request):
    """RF-39: listado paginado con filtros por cliente, estado y rango de fecha."""
    tenant = get_tenant(request)
    qs = PedidoVenta.objects.filter(tenant=tenant)
    for campo in ("cliente_id", "estado"):
        val = request.GET.get(campo)
        if val:
            qs = qs.filter(**{campo: val})
    desde, hasta = request.GET.get("desde"), request.GET.get("hasta")
    if desde:
        qs = qs.filter(created_at__gte=desde)
    if hasta:
        qs = qs.filter(created_at__lte=hasta)
    return paginate(qs.order_by("-created_at"), request, serialize_pedido)


def editar_pedido(pedido, data, request):
    """RF-40: edicion de lineas de un pedido en BORRADOR (antes de confirmar /
    reservar / facturar). Editar un pedido ya confirmado exige cancelarlo y
    crear uno nuevo: DESVIACION DOCUMENTADA para no romper la integridad de la
    reserva de stock (la ERS permite editar lineas no facturadas; se simplifica
    a solo-borrador dado el modelo de reserva)."""
    if pedido.estado != "borrador":
        raise BusinessRuleError(
            "Solo un pedido en borrador puede editarse; cancele y cree uno nuevo.",
            campo="estado", extra={"estado_actual": pedido.estado},
        )
    tenant = pedido.tenant
    with audit_context(request, tenant_id=tenant.id):
        if "lineas" in data:
            lineas = _lineas_desde_data(tenant, data["lineas"])
            PedidoLinea.objects.filter(pedido=pedido).delete()
            PedidoLinea.objects.bulk_create([
                PedidoLinea(id=uuid.uuid4(), pedido=pedido, producto=l["producto"], cantidad=l["cantidad"],
                            cantidad_facturada=Decimal("0"), cantidad_reservada=Decimal("0"),
                            precio_unitario=l["precio"])
                for l in lineas
            ])
            pedido.total = sum((l["cantidad"] * l["precio"] for l in lineas), Decimal("0")).quantize(Decimal("0.01")).quantize(Decimal("0.01"))
        pedido.updated_at = timezone.now()
        pedido.save(update_fields=["total", "updated_at"])
    return pedido


# --------------------------------------------------------------- RF-41 (cancelar)

def cancelar_pedido(pedido, request):
    """RF-41: cancela el pedido y libera todo el stock reservado (RN01). RN02: un
    pedido con facturas emitidas no se cancela por aqui (sigue el flujo de nota
    de credito, RF-44); solo se puede cancelar mientras no haya facturado."""
    if pedido.estado in ("facturado_parcial", "facturado_total"):
        raise BusinessRuleError(
            "El pedido tiene facturas emitidas; use nota de credito (RF-44) sobre lo facturado.",
            campo="estado", extra={"estado_actual": pedido.estado},
        )
    if pedido.estado == "cancelado":
        raise BusinessRuleError("El pedido ya esta cancelado.", campo="estado")

    tenant = pedido.tenant
    lineas = list(pedido.ventas_pedido_linea_pedido_set.all())
    with audit_context(request, tenant_id=tenant.id):
        with connection.cursor() as cursor:
            for l in lineas:
                if l.cantidad_reservada > 0 and pedido.almacen_id:
                    cursor.execute(
                        "UPDATE inventario.stock_actual SET reservado = reservado - %s, actualizado_en = now() "
                        "WHERE tenant_id=%s AND producto_id=%s AND almacen_id=%s",
                        [str(l.cantidad_reservada), str(tenant.id), str(l.producto_id), str(pedido.almacen_id)],
                    )
                    l.cantidad_reservada = Decimal("0")
                    l.save(update_fields=["cantidad_reservada"])
        pedido.estado = "cancelado"
        pedido.updated_at = timezone.now()
        pedido.save(update_fields=["estado", "updated_at"])
    return pedido
