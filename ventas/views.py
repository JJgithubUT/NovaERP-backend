from core.utils.views import CatalogDetailView, CatalogListCreateView
from ventas.models import Cliente
from ventas.services import catalogo_service as svc


class ClienteListCreateView(CatalogListCreateView):
    model = Cliente
    search_fields = ("razon_social", "rfc_o_id_fiscal")
    ordering = ("razon_social",)
    serialize_fn = staticmethod(svc.serialize_cliente)
    create_fn = staticmethod(svc.crear_cliente)


class ClienteDetailView(CatalogDetailView):
    model = Cliente
    serialize_fn = staticmethod(svc.serialize_cliente)
    edit_fn = staticmethod(svc.editar_cliente)
    deactivate_fn = staticmethod(svc.dar_de_baja_cliente)
