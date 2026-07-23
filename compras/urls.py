from django.urls import path

from compras.views import (
    ConfigAprobacionView,
    CuentaPorPagarListView,
    OrdenCompraCancelarView,
    OrdenCompraDetailView,
    OrdenCompraListCreateView,
    ProveedorDetailView,
    ProveedorHistorialView,
    ProveedorListCreateView,
    RecepcionListCreateView,
)

urlpatterns = [
    path('api/compras/proveedores/', ProveedorListCreateView.as_view(), name='proveedor-list-create'),
    path('api/compras/proveedores/<str:pk>/', ProveedorDetailView.as_view(), name='proveedor-detail'),
    path(
        'api/compras/proveedores/<str:pk>/historial/',
        ProveedorHistorialView.as_view(),
        name='proveedor-historial',
    ),
    path('api/compras/ordenes/', OrdenCompraListCreateView.as_view(), name='orden-compra-list-create'),
    path('api/compras/ordenes/<str:pk>/', OrdenCompraDetailView.as_view(), name='orden-compra-detail'),
    path(
        'api/compras/ordenes/<str:pk>/cancelar/',
        OrdenCompraCancelarView.as_view(),
        name='orden-compra-cancelar',
    ),
    path('api/compras/recepciones/', RecepcionListCreateView.as_view(), name='recepcion-list-create'),
    path('api/compras/cuentas-por-pagar/', CuentaPorPagarListView.as_view(), name='cuenta-por-pagar-list'),
    path('api/compras/config-aprobacion/', ConfigAprobacionView.as_view(), name='config-aprobacion'),
]
