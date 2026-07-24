from core.utils.audit import audit_context


def serialize_stock_disponible(row):
    return {
        "sku": row.sku,
        "producto": row.producto,
        "almacen": row.almacen,
        "cantidad": str(row.cantidad),
        "reservado": str(row.reservado),
        "disponible": str(row.disponible),
    }


def serialize_kardex(row):
    return {
        "movimiento_id": row.movimiento_id,
        "sku": row.sku,
        "producto": row.producto,
        "almacen": row.almacen,
        "tipo": row.tipo,
        "cantidad": str(row.cantidad),
        "costo_unitario": str(row.costo_unitario) if row.costo_unitario is not None else None,
        "referencia_tipo": row.referencia_tipo,
        "referencia_id": row.referencia_id,
        "ocurrido_en": row.ocurrido_en.isoformat() if row.ocurrido_en else None,
    }


def serialize_valuacion(row):
    return {
        "sku": row.sku,
        "producto": row.producto,
        "almacen": row.almacen,
        "cantidad": str(row.cantidad),
        "costo_referencia": str(row.costo_referencia),
        "valor_total": str(row.valor_total),
    }


def serialize_alerta(a):
    return {
        "id": a.id,
        "producto_id": str(a.producto_id),
        "almacen_id": str(a.almacen_id),
        "cantidad_al_disparo": str(a.cantidad_al_disparo),
        "disparada_en": a.disparada_en.isoformat(),
        "notificada": a.notificada,
    }


def marcar_alerta_notificada(alerta, request):
    alerta.notificada = True
    with audit_context(request, tenant_id=alerta.tenant_id):
        alerta.save(update_fields=["notificada"])
    return alerta
