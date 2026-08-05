import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import connection
from django.utils import timezone

from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError
from core.utils import filtros
from core.utils.pagination import paginate
from finanzas.models import CuentaPorCobrar
from inventario.models import Movimiento
from ventas.models import (
    ConfigVentas, FacturaLinea, FacturaVenta, NotaCredito, PedidoLinea, PedidoVenta,
)
from ventas.services.atribucion import resolver_vendedor

CENTAVO = Decimal("0.01")
# Estados de pedido desde los que se puede facturar (queda pendiente por facturar).
ESTADOS_FACTURABLES = {"confirmado", "pendiente_surtido", "facturado_parcial"}


def _to_decimal(value, campo):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise BusinessRuleError(f"{campo} debe ser un numero valido.", campo=campo)


def _config(tenant):
    cfg, _ = ConfigVentas.objects.get_or_create(tenant=tenant)
    return cfg


def _generar_folio(tenant):
    n = FacturaVenta.objects.filter(tenant=tenant).count()
    for intento in range(5):
        folio = f"FAC-{n + 1 + intento:06d}"
        if not FacturaVenta.objects.filter(tenant=tenant, folio=folio).exists():
            return folio
    raise BusinessRuleError("No fue posible generar un folio de factura unico.")


def serialize_factura(f):
    lineas = f.ventas_factura_linea_factura_set.all().order_by("id")
    cxc = CuentaPorCobrar.objects.filter(factura=f).first()
    return {
        "id": str(f.id),
        "folio": f.folio,
        "pedido_id": str(f.pedido_id),
        "cliente_id": str(f.cliente_id),
        "vendedor_id": str(f.vendedor_id) if f.vendedor_id else None,
        "estado": f.estado,
        "subtotal": str(f.subtotal),
        "impuestos": str(f.impuestos),
        "total": str(f.total),
        "fecha_emision": f.fecha_emision.isoformat(),
        "cxc_saldo": str(cxc.saldo) if cxc else None,
        "lineas": [{
            "id": str(l.id), "pedido_linea_id": str(l.pedido_linea_id),
            "cantidad": str(l.cantidad), "precio_unitario": str(l.precio_unitario),
            "importe": str(l.importe),
        } for l in lineas],
    }


# --------------------------------------------------------------- RF-42

def _plan_de_facturacion(pedido, data):
    """Determina (pedido_linea, cantidad) a facturar. Si el cliente especifica
    'lineas' se validan; si no, se factura todo lo pendiente que este reservado.
    RN01: cantidad <= pendiente de facturar. Ademas cantidad <= reservada (no se
    factura lo que no esta en stock / no se reservo)."""
    lineas_por_id = {str(l.id): l for l in pedido.ventas_pedido_linea_pedido_set.all()}
    plan = []

    if data.get("lineas"):
        for i, item in enumerate(data["lineas"]):
            pl = lineas_por_id.get(str(item.get("pedido_linea_id")))
            if pl is None:
                raise BusinessRuleError("pedido_linea_id no pertenece al pedido.", campo=f"lineas[{i}]")
            cant = _to_decimal(item.get("cantidad"), f"lineas[{i}].cantidad")
            if cant <= 0:
                raise BusinessRuleError("cantidad debe ser > 0.", campo=f"lineas[{i}].cantidad")
            pendiente = pl.cantidad - pl.cantidad_facturada
            if cant > pendiente:
                raise BusinessRuleError(
                    "cantidad excede lo pendiente de facturar.", campo=f"lineas[{i}].cantidad",
                    extra={"maximo_facturable": str(pendiente)},
                )
            if cant > pl.cantidad_reservada:
                raise BusinessRuleError(
                    "No se puede facturar mas de lo reservado/en stock.", campo=f"lineas[{i}].cantidad",
                    extra={"reservado": str(pl.cantidad_reservada)},
                )
            plan.append((pl, cant))
    else:
        for pl in lineas_por_id.values():
            pendiente = pl.cantidad - pl.cantidad_facturada
            cant = min(pendiente, pl.cantidad_reservada)
            if cant > 0:
                plan.append((pl, cant))

    if not plan:
        raise BusinessRuleError("No hay cantidades pendientes/reservadas por facturar.", campo="lineas")
    return plan


def generar_factura(pedido, data, request):
    """RF-42: genera una factura (total o parcial) de un pedido confirmado.
    RN01: no factura mas de lo pendiente por linea (trigger validar_cantidad_facturable).
    RN02: el stock reservado pasa a salida definitiva (movimiento de inventario).
    RN03: crea automaticamente la Cuenta por Cobrar (finanzas)."""
    if pedido.estado not in ESTADOS_FACTURABLES:
        raise BusinessRuleError(
            "El pedido no admite facturacion en su estado actual.",
            campo="estado", extra={"estado_actual": pedido.estado},
        )
    if not pedido.almacen_id:
        raise BusinessRuleError("El pedido no tiene almacen de surtido; confirmelo primero.", campo="estado")

    tenant = pedido.tenant
    plan = _plan_de_facturacion(pedido, data)
    iva_pct = _config(tenant).iva_pct

    subtotal = sum((pl.precio_unitario * cant for pl, cant in plan), Decimal("0")).quantize(CENTAVO, ROUND_HALF_UP)
    impuestos = (subtotal * iva_pct / Decimal("100")).quantize(CENTAVO, ROUND_HALF_UP)
    total = (subtotal + impuestos).quantize(CENTAVO, ROUND_HALF_UP)

    now = timezone.now()
    with audit_context(request, tenant_id=tenant.id):
        factura = FacturaVenta.objects.create(
            id=uuid.uuid4(), tenant=tenant, folio=_generar_folio(tenant), pedido=pedido,
            cliente_id=pedido.cliente_id, estado="emitida", subtotal=subtotal,
            impuestos=impuestos, total=total, fecha_emision=now,
            # RN-06: la venta es de quien la trabajo, no de quien factura.
            vendedor_id=resolver_vendedor(data, request, tenant, heredado=pedido.vendedor_id),
        )

        with connection.cursor() as cursor:
            for pl, cant in plan:
                # factura_linea: importe es GENERATED; el trigger BEFORE INSERT
                # valida RN01 y auto-incrementa pedido_linea.cantidad_facturada.
                cursor.execute(
                    "INSERT INTO ventas.factura_linea (id, factura_id, pedido_linea_id, cantidad, precio_unitario) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    [str(uuid.uuid4()), str(factura.id), str(pl.id), str(cant), str(pl.precio_unitario)],
                )
                # RN02: salida definitiva de inventario (el trigger baja stock_actual.cantidad).
                Movimiento.objects.create(
                    tenant=tenant, producto_id=pl.producto_id, almacen_id=pedido.almacen_id,
                    tipo="salida", cantidad=cant, costo_unitario=None,
                    referencia_tipo="factura_venta", referencia_id=str(factura.id),
                    creado_por_id=request.usuario_id, ocurrido_en=now,
                )
                # La reserva se consume: libera reservado y reduce la reserva de la linea.
                cursor.execute(
                    "UPDATE inventario.stock_actual SET reservado = reservado - %s, actualizado_en = now() "
                    "WHERE tenant_id=%s AND producto_id=%s AND almacen_id=%s",
                    [str(cant), str(tenant.id), str(pl.producto_id), str(pedido.almacen_id)],
                )
                cursor.execute(
                    "UPDATE ventas.pedido_linea SET cantidad_reservada = cantidad_reservada - %s WHERE id=%s",
                    [str(cant), str(pl.id)],
                )

        # RN03: Cuenta por Cobrar automatica.
        CuentaPorCobrar.objects.create(
            id=uuid.uuid4(), tenant=tenant, cliente_id=pedido.cliente_id, factura=factura,
            monto_original=total, saldo=total, created_at=now,
        )

        # Estado del pedido segun cobertura de facturacion (lee valores frescos).
        pendientes = PedidoLinea.objects.filter(pedido=pedido).values_list("cantidad", "cantidad_facturada")
        todo_facturado = all(c == f for c, f in pendientes)
        pedido.estado = "facturado_total" if todo_facturado else "facturado_parcial"
        pedido.updated_at = timezone.now()
        pedido.save(update_fields=["estado", "updated_at"])

    factura.refresh_from_db()
    return factura


# --------------------------------------------------------------- RF-43

def get_factura(request, pk):
    tenant = get_tenant(request)
    try:
        return FacturaVenta.objects.get(tenant=tenant, id=pk)
    except ValueError:
        raise FacturaVenta.DoesNotExist


def listar_facturas(request):
    """RF-43: listado paginado con filtros por cliente, estado y rango de fecha."""
    tenant = get_tenant(request)
    qs = FacturaVenta.objects.filter(tenant=tenant)
    for campo, val in filtros.filtros_validados(
        FacturaVenta, request, ("cliente_id", "estado", "pedido_id")
    ).items():
        qs = qs.filter(**{campo: val})
    desde, hasta = filtros.rango_validado(request)
    if desde:
        qs = qs.filter(fecha_emision__gte=desde)
    if hasta:
        qs = qs.filter(fecha_emision__lte=hasta)
    return paginate(qs.order_by("-fecha_emision"), request, serialize_factura)


# --------------------------------------------------------------- RF-44

def crear_nota_credito(factura, data, request):
    """RF-44: emite una nota de credito ligada a la factura (RN01: la factura no
    se edita/elimina, se corrige con NC). Revierte saldo en CxC. RN02: si es una
    NC TOTAL y se pide reingresar_stock, reingresa a inventario el stock facturado."""
    tenant = factura.tenant
    motivo = (data.get("motivo") or "").strip()
    if not motivo:
        raise BusinessRuleError("El motivo es obligatorio.", campo="motivo")
    monto = _to_decimal(data.get("monto"), "monto")
    if monto <= 0:
        raise BusinessRuleError("monto debe ser > 0.", campo="monto")

    cxc = CuentaPorCobrar.objects.filter(factura=factura).first()
    if cxc is None:
        raise BusinessRuleError("La factura no tiene cuenta por cobrar asociada.", campo="factura")
    if monto > cxc.saldo:
        raise BusinessRuleError(
            "El monto de la nota de credito no puede exceder el saldo por cobrar.",
            campo="monto", extra={"saldo": str(cxc.saldo)},
        )

    es_total = monto == factura.total
    reingresar = bool(data.get("reingresar_stock")) and es_total

    now = timezone.now()
    with audit_context(request, tenant_id=tenant.id):
        NotaCredito.objects.create(
            id=uuid.uuid4(), tenant=tenant, factura=factura, motivo=motivo, monto=monto, created_at=now,
        )
        # Revierte el saldo en CxC (RN02).
        cxc.saldo = (cxc.saldo - monto).quantize(CENTAVO)
        cxc.save(update_fields=["saldo"])
        # La factura queda marcada (integridad fiscal: no se elimina).
        factura.estado = "con_nota_credito"
        factura.save(update_fields=["estado"])

        # RN02: reingreso de stock solo en NC total y si se solicita.
        if reingresar and factura.pedido.almacen_id:
            for l in factura.ventas_factura_linea_factura_set.select_related():
                Movimiento.objects.create(
                    tenant=tenant, producto_id=PedidoLinea.objects.get(id=l.pedido_linea_id).producto_id,
                    almacen_id=factura.pedido.almacen_id, tipo="entrada", cantidad=l.cantidad,
                    costo_unitario=None, referencia_tipo="nota_credito", referencia_id=str(factura.id),
                    creado_por_id=request.usuario_id, ocurrido_en=now,
                )

    factura.refresh_from_db()
    return factura
