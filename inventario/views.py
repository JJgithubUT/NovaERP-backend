from core.utils.views import CatalogDetailView, CatalogListCreateView
from inventario.models import Almacen, Producto
from inventario.services import catalogo_service as svc


class ProductoListCreateView(CatalogListCreateView):
    model = Producto
    search_fields = ("sku", "nombre")
    ordering = ("sku",)
    serialize_fn = staticmethod(svc.serialize_producto)
    create_fn = staticmethod(svc.crear_producto)


class ProductoDetailView(CatalogDetailView):
    model = Producto
    serialize_fn = staticmethod(svc.serialize_producto)
    edit_fn = staticmethod(svc.editar_producto)
    deactivate_fn = staticmethod(svc.dar_de_baja_producto)


class AlmacenListCreateView(CatalogListCreateView):
    model = Almacen
    search_fields = ("nombre",)
    ordering = ("nombre",)
    serialize_fn = staticmethod(svc.serialize_almacen)
    create_fn = staticmethod(svc.crear_almacen)


class AlmacenDetailView(CatalogDetailView):
    model = Almacen
    serialize_fn = staticmethod(svc.serialize_almacen)
    edit_fn = staticmethod(svc.editar_almacen)
    deactivate_fn = staticmethod(svc.dar_de_baja_almacen)
