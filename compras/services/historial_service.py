from compras.models import OrdenCompra, RecepcionMercancia
from compras.services.orden_service import serialize_orden
from compras.services.recepcion_service import serialize_recepcion
from finanzas.models import CuentaPorPagar


def serialize_cuenta_por_pagar(cxp):
    return {
        "id": str(cxp.id),
        "proveedor_id": str(cxp.proveedor_id),
        "origen_tipo": cxp.origen_tipo,
        "origen_id": str(cxp.origen_id),
        "monto_original": str(cxp.monto_original),
        "saldo": str(cxp.saldo),
        "created_at": cxp.created_at.isoformat(),
    }


def historial_por_proveedor(proveedor):
    """RF-51: ordenes, recepciones y cuentas por pagar generadas para un
    proveedor, con su estado, agrupadas en un solo payload."""
    ordenes = OrdenCompra.objects.filter(proveedor=proveedor).order_by("-created_at")
    recepciones = RecepcionMercancia.objects.filter(orden__proveedor=proveedor).order_by("-created_at")
    cuentas = CuentaPorPagar.objects.filter(proveedor=proveedor).order_by("-created_at")

    return {
        "proveedor_id": str(proveedor.id),
        "razon_social": proveedor.razon_social,
        "ordenes": [serialize_orden(o) for o in ordenes],
        "recepciones": [serialize_recepcion(r) for r in recepciones],
        "cuentas_por_pagar": [serialize_cuenta_por_pagar(c) for c in cuentas],
    }
