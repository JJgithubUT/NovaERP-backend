import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError
from inventario.models import Almacen, Producto


def _to_decimal(value, campo):
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise BusinessRuleError(f"{campo} debe ser un numero valido.", campo=campo)


# ---------------------------------------------------------------- Producto

def serialize_producto(producto):
    return {
        "id": str(producto.id),
        "sku": producto.sku,
        "nombre": producto.nombre,
        "descripcion": producto.descripcion,
        "costo_referencia": str(producto.costo_referencia),
        "precio_venta": str(producto.precio_venta),
        "stock_minimo": str(producto.stock_minimo),
        "activo": producto.activo,
    }


def crear_producto(data, request):
    sku = (data.get("sku") or "").strip()
    nombre = (data.get("nombre") or "").strip()
    if not sku:
        raise BusinessRuleError("sku es obligatorio.", campo="sku")
    if not nombre:
        raise BusinessRuleError("nombre es obligatorio.", campo="nombre")

    costo_referencia = _to_decimal(data.get("costo_referencia"), "costo_referencia")
    precio_venta = _to_decimal(data.get("precio_venta"), "precio_venta")
    stock_minimo = _to_decimal(data.get("stock_minimo"), "stock_minimo")
    if costo_referencia < 0:
        raise BusinessRuleError("costo_referencia debe ser >= 0.", campo="costo_referencia")
    if precio_venta < 0:
        raise BusinessRuleError("precio_venta debe ser >= 0.", campo="precio_venta")

    tenant = get_tenant(request)
    if Producto.objects.filter(tenant=tenant, sku=sku).exists():
        raise BusinessRuleError("SKU ya existe en este tenant.", campo="sku")

    now = timezone.now()
    with transaction.atomic():
        producto = Producto.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            sku=sku,
            nombre=nombre,
            descripcion=(data.get("descripcion") or None),
            costo_referencia=costo_referencia,
            precio_venta=precio_venta,
            stock_minimo=stock_minimo,
            activo=True,
            created_at=now,
            updated_at=now,
        )
    return producto


def editar_producto(producto, data, request):
    if "sku" in data:
        nuevo_sku = (data["sku"] or "").strip()
        if not nuevo_sku:
            raise BusinessRuleError("sku no puede quedar vacio.", campo="sku")
        if nuevo_sku != producto.sku and Producto.objects.filter(
            tenant=producto.tenant, sku=nuevo_sku
        ).exists():
            raise BusinessRuleError("SKU ya existe en este tenant.", campo="sku")
        producto.sku = nuevo_sku

    if "nombre" in data:
        nombre = (data["nombre"] or "").strip()
        if not nombre:
            raise BusinessRuleError("nombre no puede quedar vacio.", campo="nombre")
        producto.nombre = nombre

    if "descripcion" in data:
        producto.descripcion = data["descripcion"] or None

    if "costo_referencia" in data:
        costo = _to_decimal(data["costo_referencia"], "costo_referencia")
        if costo < 0:
            raise BusinessRuleError("costo_referencia debe ser >= 0.", campo="costo_referencia")
        producto.costo_referencia = costo

    if "precio_venta" in data:
        precio = _to_decimal(data["precio_venta"], "precio_venta")
        if precio < 0:
            raise BusinessRuleError("precio_venta debe ser >= 0.", campo="precio_venta")
        producto.precio_venta = precio

    if "stock_minimo" in data:
        stock_minimo = _to_decimal(data["stock_minimo"], "stock_minimo")
        if stock_minimo < 0:
            raise BusinessRuleError("stock_minimo debe ser >= 0.", campo="stock_minimo")
        producto.stock_minimo = stock_minimo

    producto.save()
    return producto


def dar_de_baja_producto(producto, request):
    producto.activo = False
    producto.save(update_fields=["activo"])
    return producto


# ----------------------------------------------------------------- Almacen

def serialize_almacen(almacen):
    return {
        "id": str(almacen.id),
        "nombre": almacen.nombre,
        "ubicacion": almacen.ubicacion,
        "activo": almacen.activo,
    }


def crear_almacen(data, request):
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        raise BusinessRuleError("nombre es obligatorio.", campo="nombre")

    tenant = get_tenant(request)
    if Almacen.objects.filter(tenant=tenant, nombre=nombre).exists():
        raise BusinessRuleError(
            "Ya existe un almacen con ese nombre en este tenant.", campo="nombre"
        )

    with transaction.atomic():
        almacen = Almacen.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            nombre=nombre,
            ubicacion=(data.get("ubicacion") or None),
            activo=True,
        )
    return almacen


def editar_almacen(almacen, data, request):
    if "nombre" in data:
        nuevo_nombre = (data["nombre"] or "").strip()
        if not nuevo_nombre:
            raise BusinessRuleError("nombre no puede quedar vacio.", campo="nombre")
        if nuevo_nombre != almacen.nombre and Almacen.objects.filter(
            tenant=almacen.tenant, nombre=nuevo_nombre
        ).exists():
            raise BusinessRuleError(
                "Ya existe un almacen con ese nombre en este tenant.", campo="nombre"
            )
        almacen.nombre = nuevo_nombre

    if "ubicacion" in data:
        almacen.ubicacion = data["ubicacion"] or None

    almacen.save()
    return almacen


def dar_de_baja_almacen(almacen, request):
    almacen.activo = False
    almacen.save(update_fields=["activo"])
    return almacen
