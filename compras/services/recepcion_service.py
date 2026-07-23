import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from compras.models import OrdenCompra, OrdenCompraLinea, RecepcionLinea, RecepcionMercancia
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError
from finanzas.models import CuentaPorPagar
from inventario.models import Almacen, Movimiento

ESTADOS_RECIBIBLES = {"enviada", "recibida_parcial"}


def _to_decimal(value, campo):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise BusinessRuleError(f"{campo} debe ser un numero valido.", campo=campo)


def _to_decimal_positivo(value, campo):
    valor = _to_decimal(value, campo)
    if valor <= 0:
        raise BusinessRuleError(f"{campo} debe ser mayor a cero.", campo=campo)
    return valor


def serialize_recepcion_linea(linea):
    return {
        "id": str(linea.id),
        "orden_compra_linea_id": str(linea.orden_compra_linea_id),
        "cantidad": str(linea.cantidad),
        "costo_unitario": str(linea.costo_unitario),
    }


def serialize_recepcion(recepcion):
    lineas = recepcion.compras_recepcion_linea_recepcion_set.all().order_by("id")
    return {
        "id": str(recepcion.id),
        "orden_id": str(recepcion.orden_id),
        "almacen_id": str(recepcion.almacen_id),
        "recibido_por": str(recepcion.recibido_por_id) if recepcion.recibido_por_id else None,
        "lineas": [serialize_recepcion_linea(l) for l in lineas],
        "created_at": recepcion.created_at.isoformat(),
    }


def crear_recepcion(data, request):
    """RF-49/RF-50: registra la entrada fisica contra una orden 'enviada' o
    'recibida_parcial', genera el movimiento de entrada de inventario
    (RF-58 -> trigger inventario.aplicar_movimiento actualiza stock_actual
    y dispara alertas) y la cuenta por pagar preliminar vinculada a la
    orden y al proveedor (RF-50). El costo promedio ponderado/PEPS por
    almacen (RN03 del ERS) no esta implementado: se actualiza
    costo_referencia del producto al ultimo costo de recepcion, mismo
    nivel de simplificacion que el resto de Fase 1."""
    tenant = get_tenant(request)

    orden_id = data.get("orden_id")
    if not orden_id:
        raise BusinessRuleError("orden_id es obligatorio.", campo="orden_id")

    almacen_id = data.get("almacen_id")
    if not almacen_id:
        raise BusinessRuleError("almacen_id es obligatorio.", campo="almacen_id")

    lineas_data = data.get("lineas")
    if not lineas_data:
        raise BusinessRuleError("La recepcion debe tener al menos una linea.", campo="lineas")

    now = timezone.now()
    with transaction.atomic():
        try:
            orden = OrdenCompra.objects.select_for_update().get(tenant=tenant, id=orden_id)
        except (OrdenCompra.DoesNotExist, ValueError):
            raise BusinessRuleError("Orden de compra no encontrada.", campo="orden_id")

        if orden.estado not in ESTADOS_RECIBIBLES:
            raise BusinessRuleError(
                "La orden no admite recepciones en su estado actual.",
                campo="orden_id",
                extra={"estado_actual": orden.estado},
            )

        try:
            almacen = Almacen.objects.get(tenant=tenant, id=almacen_id, activo=True)
        except (Almacen.DoesNotExist, ValueError):
            raise BusinessRuleError("Almacen no encontrado o dado de baja.", campo="almacen_id")

        lineas_orden = {
            str(l.id): l for l in OrdenCompraLinea.objects.select_for_update().filter(orden=orden)
        }

        a_recibir = []
        ya_solicitado = {}
        for i, linea in enumerate(lineas_data):
            linea_id = str(linea.get("orden_compra_linea_id") or "")
            orden_linea = lineas_orden.get(linea_id)
            if orden_linea is None:
                raise BusinessRuleError(
                    "orden_compra_linea_id no pertenece a esta orden.",
                    campo=f"lineas[{i}].orden_compra_linea_id",
                )

            cantidad = _to_decimal_positivo(linea.get("cantidad"), f"lineas[{i}].cantidad")
            acumulado = ya_solicitado.get(linea_id, Decimal("0"))
            pendiente = orden_linea.cantidad - orden_linea.cantidad_recibida - acumulado
            if cantidad > pendiente:
                raise BusinessRuleError(
                    "La cantidad recibida no puede exceder la cantidad pendiente de la orden.",
                    campo=f"lineas[{i}].cantidad",
                    extra={"cantidad_pendiente": str(pendiente)},
                )
            ya_solicitado[linea_id] = acumulado + cantidad

            costo_unitario = _to_decimal(linea.get("costo_unitario"), f"lineas[{i}].costo_unitario")
            if costo_unitario < 0:
                raise BusinessRuleError(
                    "costo_unitario debe ser >= 0.", campo=f"lineas[{i}].costo_unitario"
                )

            a_recibir.append(
                {"orden_linea": orden_linea, "cantidad": cantidad, "costo_unitario": costo_unitario}
            )

        recepcion = RecepcionMercancia.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            orden=orden,
            almacen=almacen,
            recibido_por_id=request.usuario_id,
            created_at=now,
        )

        monto_cxp = Decimal("0")
        for item in a_recibir:
            orden_linea = item["orden_linea"]
            cantidad = item["cantidad"]
            costo_unitario = item["costo_unitario"]

            RecepcionLinea.objects.create(
                id=uuid.uuid4(),
                recepcion=recepcion,
                orden_compra_linea=orden_linea,
                cantidad=cantidad,
                costo_unitario=costo_unitario,
            )

            orden_linea.cantidad_recibida = orden_linea.cantidad_recibida + cantidad
            orden_linea.save(update_fields=["cantidad_recibida"])

            Movimiento.objects.create(
                tenant=tenant,
                producto=orden_linea.producto,
                almacen=almacen,
                tipo="entrada",
                cantidad=cantidad,
                costo_unitario=costo_unitario,
                referencia_tipo="recepcion_mercancia",
                referencia_id=str(recepcion.id),
                creado_por_id=request.usuario_id,
                ocurrido_en=now,
            )

            orden_linea.producto.costo_referencia = costo_unitario
            orden_linea.producto.save(update_fields=["costo_referencia"])

            monto_cxp += cantidad * costo_unitario

        lineas_actualizadas = list(OrdenCompraLinea.objects.filter(orden=orden))
        if all(l.cantidad_recibida >= l.cantidad for l in lineas_actualizadas):
            orden.estado = "recibida_total"
        else:
            orden.estado = "recibida_parcial"
        orden.updated_at = now
        orden.save(update_fields=["estado", "updated_at"])

        CuentaPorPagar.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            proveedor=orden.proveedor,
            origen_tipo="recepcion_mercancia",
            origen_id=recepcion.id,
            monto_original=monto_cxp,
            saldo=monto_cxp,
            created_at=now,
        )

    return recepcion
