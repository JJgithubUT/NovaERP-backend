from compras.models import Proveedor
from compras.services import catalogo_service as svc
from core.utils.views import CatalogDetailView, CatalogListCreateView


class ProveedorListCreateView(CatalogListCreateView):
    model = Proveedor
    search_fields = ("razon_social", "rfc_o_id_fiscal")
    ordering = ("razon_social",)
    serialize_fn = staticmethod(svc.serialize_proveedor)
    create_fn = staticmethod(svc.crear_proveedor)


class ProveedorDetailView(CatalogDetailView):
    model = Proveedor
    serialize_fn = staticmethod(svc.serialize_proveedor)
    edit_fn = staticmethod(svc.editar_proveedor)
    deactivate_fn = staticmethod(svc.dar_de_baja_proveedor)
