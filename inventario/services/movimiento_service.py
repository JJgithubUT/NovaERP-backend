import uuid
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils import timezone

from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError
from inventario.models import AjusteInventario, Almacen, Movimiento, Producto, StockActual, Transferencia

# El ERS v3.0 describe un catalogo de 5 motivos (Merma, Conteo fisico,
# Dano, Robo, Correccion de captura), pero el CHECK real de la tabla
# (ajuste_inventario_motivo_check) solo acepta estos 3. Se usa el valor
# real de la DB hasta que se amplie el constraint.
MOTIVOS_AJUSTE = {"merma", "conteo_fisico", "otro"}


def _to_decimal(value, campo):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise BusinessRuleError(f"{campo} debe ser un numero valido.", campo=campo)


def _to_decimal_positivo(value, campo):
    cantidad = _to_decimal(value, campo)
    if cantidad <= 0:
        raise BusinessRuleError(f"{campo} debe ser mayor a cero.", campo=campo)
    return cantidad


def _get_producto(tenant, producto_id):
    if not producto_id:
        raise BusinessRuleError("producto_id es obligatorio.", campo="producto_id")
    try:
        return Producto.objects.get(tenant=tenant, id=producto_id, activo=True)
    except (Producto.DoesNotExist, ValidationError, ValueError):
        raise BusinessRuleError("Producto no encontrado o dado de baja.", campo="producto_id")


def _get_almacen(tenant, almacen_id, campo="almacen_id"):
    if not almacen_id:
        raise BusinessRuleError(f"{campo} es obligatorio.", campo=campo)
    try:
        return Almacen.objects.get(tenant=tenant, id=almacen_id, activo=True)
    except (Almacen.DoesNotExist, ValidationError, ValueError):
        raise BusinessRuleError("Almacen no encontrado o dado de baja.", campo=campo)


def _cantidad_actual(tenant, producto, almacen, lock=False):
    qs = StockActual.objects.filter(tenant=tenant, producto=producto, almacen=almacen)
    if lock:
        qs = qs.select_for_update()
    row = qs.first()
    return row.cantidad if row else Decimal("0")


def _validar_stock_suficiente(tenant, producto, almacen, cantidad):
    # RF-58 CA: ninguna salida puede dejar el stock por debajo de cero. El
    # ERS contempla una excepcion de "stock negativo" configurable por
    # tenant para backorders, pero esa bandera aun no existe en el esquema
    # (core.config_seguridad_tenant no la tiene) -> se aplica el bloqueo
    # estricto para todos los tenants hasta que se agregue la columna.
    disponible = _cantidad_actual(tenant, producto, almacen, lock=True)
    if disponible - cantidad < 0:
        raise BusinessRuleError(
            "Stock insuficiente: la operacion dejaria el stock por debajo de cero.",
            campo="cantidad",
            extra={"stock_actual": str(disponible)},
        )


# ------------------------------------------------------------- Movimiento

def serialize_movimiento(m):
    return {
        "id": m.id,
        "producto_id": str(m.producto_id),
        "almacen_id": str(m.almacen_id),
        "tipo": m.tipo,
        "cantidad": str(m.cantidad),
        "costo_unitario": str(m.costo_unitario) if m.costo_unitario is not None else None,
        "referencia_tipo": m.referencia_tipo,
        "referencia_id": m.referencia_id,
        "ocurrido_en": m.ocurrido_en.isoformat(),
    }


def crear_movimiento_manual(data, request):
    """RF-58: alta manual de entrada/salida (ej. carga inicial de stock,
    mermas puntuales). Las variantes ajuste_*/transferencia_* solo se crean
    desde crear_ajuste()/crear_transferencia(), nunca desde aqui."""
    tipo = (data.get("tipo") or "").strip()
    if tipo not in ("entrada", "salida"):
        raise BusinessRuleError("tipo debe ser 'entrada' o 'salida'.", campo="tipo")

    tenant = get_tenant(request)
    producto = _get_producto(tenant, data.get("producto_id"))
    almacen = _get_almacen(tenant, data.get("almacen_id"))
    cantidad = _to_decimal_positivo(data.get("cantidad"), "cantidad")
    costo_unitario = data.get("costo_unitario")
    if costo_unitario is not None:
        costo_unitario = _to_decimal(costo_unitario, "costo_unitario")
        if costo_unitario < 0:
            raise BusinessRuleError("costo_unitario debe ser >= 0.", campo="costo_unitario")

    with audit_context(request, tenant_id=tenant.id):
        if tipo == "salida":
            _validar_stock_suficiente(tenant, producto, almacen, cantidad)

        movimiento = Movimiento.objects.create(
            tenant=tenant,
            producto=producto,
            almacen=almacen,
            tipo=tipo,
            cantidad=cantidad,
            costo_unitario=costo_unitario,
            referencia_tipo="manual",
            referencia_id=None,
            creado_por_id=request.usuario_id,
            ocurrido_en=timezone.now(),
        )
    return movimiento


# --------------------------------------------------------- AjusteInventario

def serialize_ajuste(a):
    return {
        "id": str(a.id),
        "producto_id": str(a.producto_id),
        "almacen_id": str(a.almacen_id),
        "motivo": a.motivo,
        "cantidad": str(a.cantidad),
        "aprobado_por": str(a.aprobado_por_id) if a.aprobado_por_id else None,
        "created_at": a.created_at.isoformat(),
    }


def crear_ajuste(data, request):
    """RF-60: conteo fisico / mermas. El ERS liga los ajustes que exceden un
    umbral configurable al motor de workflow (RF-83) para requerir
    aprobacion previa; ni el umbral por tenant ni el enganche a BPM existen
    todavia en el esquema, asi que por ahora todo ajuste se aplica de
    inmediato y queda registrado con aprobado_por = quien lo captura. Este
    gap se cierra cuando se implemente el modulo BPM (Fase 2)."""
    motivo = (data.get("motivo") or "").strip()
    if motivo not in MOTIVOS_AJUSTE:
        raise BusinessRuleError(
            f"motivo debe ser uno de: {', '.join(sorted(MOTIVOS_AJUSTE))}.",
            campo="motivo",
        )

    tenant = get_tenant(request)
    producto = _get_producto(tenant, data.get("producto_id"))
    almacen = _get_almacen(tenant, data.get("almacen_id"))
    cantidad = _to_decimal(data.get("cantidad"), "cantidad")
    if cantidad == 0:
        raise BusinessRuleError("cantidad no puede ser cero.", campo="cantidad")

    tipo_movimiento = "ajuste_positivo" if cantidad > 0 else "ajuste_negativo"
    cantidad_abs = abs(cantidad)

    with audit_context(request, tenant_id=tenant.id):
        if cantidad < 0:
            _validar_stock_suficiente(tenant, producto, almacen, cantidad_abs)

        ajuste = AjusteInventario.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            almacen=almacen,
            producto=producto,
            motivo=motivo,
            cantidad=cantidad,
            aprobado_por_id=request.usuario_id,
            created_at=timezone.now(),
        )
        Movimiento.objects.create(
            tenant=tenant,
            producto=producto,
            almacen=almacen,
            tipo=tipo_movimiento,
            cantidad=cantidad_abs,
            costo_unitario=producto.costo_referencia,
            referencia_tipo="ajuste_inventario",
            referencia_id=str(ajuste.id),
            creado_por_id=request.usuario_id,
            ocurrido_en=timezone.now(),
        )
    return ajuste


# ---------------------------------------------------------- Transferencia

def serialize_transferencia(t):
    return {
        "id": str(t.id),
        "producto_id": str(t.producto_id),
        "almacen_origen_id": str(t.almacen_origen_id),
        "almacen_destino_id": str(t.almacen_destino_id),
        "cantidad": str(t.cantidad),
        "created_at": t.created_at.isoformat(),
    }


def crear_transferencia(data, request):
    """RF-61: dos movimientos ligados (salida origen + entrada destino) en
    una sola transaccion atomica; si algo falla, se revierte todo."""
    tenant = get_tenant(request)
    producto = _get_producto(tenant, data.get("producto_id"))
    almacen_origen = _get_almacen(tenant, data.get("almacen_origen_id"), campo="almacen_origen_id")
    almacen_destino = _get_almacen(tenant, data.get("almacen_destino_id"), campo="almacen_destino_id")
    cantidad = _to_decimal_positivo(data.get("cantidad"), "cantidad")

    if almacen_origen.id == almacen_destino.id:
        raise BusinessRuleError(
            "El almacen origen y destino no pueden ser el mismo.", campo="almacen_destino_id"
        )

    with audit_context(request, tenant_id=tenant.id):
        _validar_stock_suficiente(tenant, producto, almacen_origen, cantidad)

        transferencia = Transferencia.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            producto=producto,
            almacen_origen=almacen_origen,
            almacen_destino=almacen_destino,
            cantidad=cantidad,
            created_at=timezone.now(),
        )

        now = timezone.now()
        Movimiento.objects.create(
            tenant=tenant,
            producto=producto,
            almacen=almacen_origen,
            tipo="transferencia_salida",
            cantidad=cantidad,
            costo_unitario=producto.costo_referencia,
            referencia_tipo="transferencia",
            referencia_id=str(transferencia.id),
            creado_por_id=request.usuario_id,
            ocurrido_en=now,
        )
        Movimiento.objects.create(
            tenant=tenant,
            producto=producto,
            almacen=almacen_destino,
            tipo="transferencia_entrada",
            cantidad=cantidad,
            costo_unitario=producto.costo_referencia,
            referencia_tipo="transferencia",
            referencia_id=str(transferencia.id),
            creado_por_id=request.usuario_id,
            ocurrido_en=now,
        )
    return transferencia
