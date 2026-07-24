import uuid
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from compras.models import ConfigAprobacion, OrdenCompra, OrdenCompraLinea, Proveedor
from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError
from inventario.models import Producto

ESTADOS_EDITABLES = {"pendiente_aprobacion", "enviada"}
ESTADOS_CANCELABLES = {"pendiente_aprobacion", "enviada", "recibida_parcial"}


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


def _generar_folio(tenant):
    n = OrdenCompra.objects.filter(tenant=tenant).count()
    for intento in range(5):
        folio = f"OC-{n + 1 + intento:06d}"
        if not OrdenCompra.objects.filter(tenant=tenant, folio=folio).exists():
            return folio
    raise BusinessRuleError("No fue posible generar un folio de orden unico, intente de nuevo.")


def _validar_lineas(tenant, lineas_data):
    if not lineas_data:
        raise BusinessRuleError("La orden debe tener al menos una linea.", campo="lineas")

    lineas = []
    for i, linea in enumerate(lineas_data):
        producto_id = linea.get("producto_id")
        if not producto_id:
            raise BusinessRuleError(
                "producto_id es obligatorio en cada linea.", campo=f"lineas[{i}].producto_id"
            )
        try:
            producto = Producto.objects.get(tenant=tenant, id=producto_id, activo=True)
        except (Producto.DoesNotExist, ValueError):
            raise BusinessRuleError(
                "Producto no encontrado o dado de baja.", campo=f"lineas[{i}].producto_id"
            )

        cantidad = _to_decimal_positivo(linea.get("cantidad"), f"lineas[{i}].cantidad")
        costo_unitario = _to_decimal(linea.get("costo_unitario"), f"lineas[{i}].costo_unitario")
        if costo_unitario < 0:
            raise BusinessRuleError(
                "costo_unitario debe ser >= 0.", campo=f"lineas[{i}].costo_unitario"
            )

        lineas.append({"producto": producto, "cantidad": cantidad, "costo_unitario": costo_unitario})
    return lineas


def serialize_orden_linea(linea):
    return {
        "id": str(linea.id),
        "producto_id": str(linea.producto_id),
        "cantidad": str(linea.cantidad),
        "cantidad_recibida": str(linea.cantidad_recibida),
        "costo_unitario": str(linea.costo_unitario),
    }


def serialize_orden(orden):
    lineas = orden.compras_orden_compra_linea_orden_set.all().order_by("id")
    return {
        "id": str(orden.id),
        "folio": orden.folio,
        "proveedor_id": str(orden.proveedor_id),
        "estado": orden.estado,
        "total": str(orden.total),
        "lineas": [serialize_orden_linea(l) for l in lineas],
        "created_at": orden.created_at.isoformat(),
        "updated_at": orden.updated_at.isoformat(),
    }


def crear_orden_compra(data, request):
    """RF-47: si el total supera el umbral de aprobacion del tenant
    (RF-52), la orden queda en 'pendiente_aprobacion' y no puede recibirse
    (RF-49) hasta pasar a 'enviada'. El motor de aprobaciones formal
    (RF-83/RF-86, BPM) no existe todavia en el alcance actual, asi que ese
    transito se hace por ahora vía PATCH manual (ver editar_orden_compra),
    mismo tipo de gap ya documentado en
    inventario/services/movimiento_service.py para los ajustes."""
    tenant = get_tenant(request)

    proveedor_id = data.get("proveedor_id")
    if not proveedor_id:
        raise BusinessRuleError("proveedor_id es obligatorio.", campo="proveedor_id")
    try:
        proveedor = Proveedor.objects.get(tenant=tenant, id=proveedor_id, activo=True)
    except (Proveedor.DoesNotExist, ValueError):
        raise BusinessRuleError("Proveedor no encontrado o dado de baja.", campo="proveedor_id")

    lineas = _validar_lineas(tenant, data.get("lineas"))
    total = sum((l["cantidad"] * l["costo_unitario"] for l in lineas), Decimal("0"))

    umbral = ConfigAprobacion.objects.filter(tenant=tenant).values_list("umbral_monto", flat=True).first()
    estado = "pendiente_aprobacion" if (umbral is not None and total > umbral) else "enviada"

    now = timezone.now()
    with audit_context(request, tenant_id=tenant.id):
        orden = OrdenCompra.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            folio=_generar_folio(tenant),
            proveedor=proveedor,
            estado=estado,
            total=total,
            created_at=now,
            updated_at=now,
        )
        OrdenCompraLinea.objects.bulk_create(
            [
                OrdenCompraLinea(
                    id=uuid.uuid4(),
                    orden=orden,
                    producto=l["producto"],
                    cantidad=l["cantidad"],
                    cantidad_recibida=Decimal("0"),
                    costo_unitario=l["costo_unitario"],
                )
                for l in lineas
            ]
        )

    return orden


def editar_orden_compra(orden, data, request):
    """RF-48: solo editable mientras no tenga recepciones registradas y no
    este cerrada/cancelada. El unico cambio de estado permitido por PATCH es
    pendiente_aprobacion -> enviada (aprobacion manual, ver nota en
    crear_orden_compra); cualquier otra transicion pasa por /cancelar/."""
    if orden.estado not in ESTADOS_EDITABLES:
        raise BusinessRuleError(
            "La orden no puede editarse en su estado actual.",
            campo="estado",
            extra={"estado_actual": orden.estado},
        )

    tenant = orden.tenant
    lineas_actuales = list(OrdenCompraLinea.objects.filter(orden=orden))
    tiene_recepciones = any(l.cantidad_recibida > 0 for l in lineas_actuales)

    with audit_context(request, tenant_id=tenant.id):
        if "proveedor_id" in data:
            if tiene_recepciones:
                raise BusinessRuleError(
                    "No se puede cambiar el proveedor de una orden con recepciones registradas.",
                    campo="proveedor_id",
                )
            try:
                orden.proveedor = Proveedor.objects.get(
                    tenant=tenant, id=data["proveedor_id"], activo=True
                )
            except (Proveedor.DoesNotExist, ValueError):
                raise BusinessRuleError("Proveedor no encontrado o dado de baja.", campo="proveedor_id")

        if "lineas" in data:
            if tiene_recepciones:
                raise BusinessRuleError(
                    "No se puede editar las lineas de una orden con recepciones registradas.",
                    campo="lineas",
                )
            nuevas = _validar_lineas(tenant, data["lineas"])
            OrdenCompraLinea.objects.filter(orden=orden).delete()
            OrdenCompraLinea.objects.bulk_create(
                [
                    OrdenCompraLinea(
                        id=uuid.uuid4(),
                        orden=orden,
                        producto=l["producto"],
                        cantidad=l["cantidad"],
                        cantidad_recibida=Decimal("0"),
                        costo_unitario=l["costo_unitario"],
                    )
                    for l in nuevas
                ]
            )
            orden.total = sum((l["cantidad"] * l["costo_unitario"] for l in nuevas), Decimal("0"))

            umbral = (
                ConfigAprobacion.objects.filter(tenant=tenant)
                .values_list("umbral_monto", flat=True)
                .first()
            )
            orden.estado = (
                "pendiente_aprobacion" if (umbral is not None and orden.total > umbral) else "enviada"
            )

        if "estado" in data:
            nuevo_estado = data["estado"]
            if orden.estado == "pendiente_aprobacion" and nuevo_estado == "enviada":
                orden.estado = "enviada"
            else:
                raise BusinessRuleError(
                    "Transicion de estado no permitida. Use /cancelar/ para cancelar una orden.",
                    campo="estado",
                )

        orden.updated_at = timezone.now()
        orden.save()

    return orden


def cancelar_orden_compra(orden, request):
    """RF-48 RN: una orden con recepciones parciales no se cancela
    retroactivamente sobre lo ya recibido; cancelar solo cierra la orden
    para que no admita mas recepciones (RF-49 valida el estado), dejando
    intacto el historial de lo ya recibido."""
    if orden.estado not in ESTADOS_CANCELABLES:
        raise BusinessRuleError(
            "La orden no puede cancelarse en su estado actual.",
            campo="estado",
            extra={"estado_actual": orden.estado},
        )

    orden.estado = "cancelada"
    orden.updated_at = timezone.now()
    with audit_context(request, tenant_id=orden.tenant_id):
        orden.save(update_fields=["estado", "updated_at"])
    return orden
