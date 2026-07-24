from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.utils.auth import tenant_scoped
from core.utils.pagination import paginate
from core.utils.permissions import PermissionRequiredMixin
from core.utils.views import (
    TRUE_VALUES,
    CatalogDetailView,
    CatalogListCreateView,
    ListCreateView,
    ReadOnlyListView,
)
from inventario.models import (
    AjusteInventario,
    Almacen,
    AlertaStockMinimo,
    Movimiento,
    Producto,
    Transferencia,
    VKardex,
    VStockDisponible,
    VValuacionInventario,
)
from inventario.services import catalogo_service as catalogo_svc
from inventario.services import consulta_service as consulta_svc
from inventario.services import movimiento_service as mov_svc


class ProductoListCreateView(CatalogListCreateView):
    model = Producto
    permisos = {"GET": "inventario:productos:leer", "POST": "inventario:productos:crear"}
    search_fields = ("sku", "nombre")
    ordering = ("sku",)
    serialize_fn = staticmethod(catalogo_svc.serialize_producto)
    create_fn = staticmethod(catalogo_svc.crear_producto)


class ProductoDetailView(CatalogDetailView):
    model = Producto
    permisos = {"PATCH": "inventario:productos:editar", "DELETE": "inventario:productos:eliminar"}
    serialize_fn = staticmethod(catalogo_svc.serialize_producto)
    edit_fn = staticmethod(catalogo_svc.editar_producto)
    deactivate_fn = staticmethod(catalogo_svc.dar_de_baja_producto)


class AlmacenListCreateView(CatalogListCreateView):
    model = Almacen
    permisos = {"GET": "inventario:almacenes:leer", "POST": "inventario:almacenes:crear"}
    search_fields = ("nombre",)
    ordering = ("nombre",)
    serialize_fn = staticmethod(catalogo_svc.serialize_almacen)
    create_fn = staticmethod(catalogo_svc.crear_almacen)


class AlmacenDetailView(CatalogDetailView):
    model = Almacen
    permisos = {"PATCH": "inventario:almacenes:editar", "DELETE": "inventario:almacenes:eliminar"}
    serialize_fn = staticmethod(catalogo_svc.serialize_almacen)
    edit_fn = staticmethod(catalogo_svc.editar_almacen)
    deactivate_fn = staticmethod(catalogo_svc.dar_de_baja_almacen)


# ---------------------------------------------------------------- RF-58

class MovimientoListCreateView(ListCreateView):
    model = Movimiento
    permisos = {"GET": "inventario:movimientos:leer", "POST": "inventario:movimientos:crear"}
    ordering = ("-ocurrido_en", "-id")
    serialize_fn = staticmethod(mov_svc.serialize_movimiento)
    create_fn = staticmethod(mov_svc.crear_movimiento_manual)
    filter_fields = ("producto_id", "almacen_id", "tipo")
    date_field = "ocurrido_en"


# ---------------------------------------------------------------- RF-59

class StockDisponibleListView(ReadOnlyListView):
    model = VStockDisponible
    permiso_requerido = "inventario:stock:leer"
    ordering = ("producto", "almacen")
    serialize_fn = staticmethod(consulta_svc.serialize_stock_disponible)
    tenant_via_id = True


# ---------------------------------------------------------------- RF-60

class AjusteListCreateView(ListCreateView):
    model = AjusteInventario
    permisos = {"GET": "inventario:ajustes:leer", "POST": "inventario:ajustes:crear"}
    ordering = ("-created_at",)
    serialize_fn = staticmethod(mov_svc.serialize_ajuste)
    create_fn = staticmethod(mov_svc.crear_ajuste)
    filter_fields = ("producto_id", "almacen_id")
    date_field = "created_at"


# ---------------------------------------------------------------- RF-61

class TransferenciaListCreateView(ListCreateView):
    model = Transferencia
    permisos = {"GET": "inventario:transferencias:leer", "POST": "inventario:transferencias:crear"}
    ordering = ("-created_at",)
    serialize_fn = staticmethod(mov_svc.serialize_transferencia)
    create_fn = staticmethod(mov_svc.crear_transferencia)
    filter_fields = ("producto_id", "almacen_origen_id", "almacen_destino_id")
    date_field = "created_at"


# ---------------------------------------------------------------- RF-62

class KardexListView(ReadOnlyListView):
    model = VKardex
    permiso_requerido = "inventario:kardex:leer"
    ordering = ("-ocurrido_en", "-movimiento_id")
    serialize_fn = staticmethod(consulta_svc.serialize_kardex)
    filter_fields = ("sku", "almacen", "tipo")
    date_field = "ocurrido_en"
    tenant_via_id = True


# ---------------------------------------------------------------- RF-63

@method_decorator(csrf_exempt, name="dispatch")
class AlertaStockMinimoListView(PermissionRequiredMixin, View):
    permiso_requerido = "inventario:alertas:leer"

    def get(self, request):
        qs = tenant_scoped(AlertaStockMinimo.objects.all(), request).order_by("-disparada_en")

        producto_id = request.GET.get("producto_id")
        if producto_id:
            qs = qs.filter(producto_id=producto_id)

        almacen_id = request.GET.get("almacen_id")
        if almacen_id:
            qs = qs.filter(almacen_id=almacen_id)

        notificada = request.GET.get("notificada")
        if notificada is not None:
            qs = qs.filter(notificada=notificada.strip().lower() in TRUE_VALUES)

        return JsonResponse(paginate(qs, request, consulta_svc.serialize_alerta))


@method_decorator(csrf_exempt, name="dispatch")
class AlertaStockMinimoAckView(PermissionRequiredMixin, View):
    permiso_requerido = "inventario:alertas:notificar"

    def post(self, request, pk):
        try:
            alerta = tenant_scoped(AlertaStockMinimo.objects.all(), request).get(pk=pk)
        except (AlertaStockMinimo.DoesNotExist, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        alerta = consulta_svc.marcar_alerta_notificada(alerta, request)
        return JsonResponse(consulta_svc.serialize_alerta(alerta))


# ---------------------------------------------------------------- RF-64

class ValuacionInventarioListView(ReadOnlyListView):
    model = VValuacionInventario
    permiso_requerido = "inventario:valuacion:leer"
    ordering = ("producto", "almacen")
    serialize_fn = staticmethod(consulta_svc.serialize_valuacion)
    tenant_via_id = True
