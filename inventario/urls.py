from django.urls import path

from inventario.views import (
    AjusteListCreateView,
    AlertaStockMinimoAckView,
    AlertaStockMinimoListView,
    AlmacenDetailView,
    AlmacenListCreateView,
    KardexListView,
    MovimientoListCreateView,
    ProductoDetailView,
    ProductoListCreateView,
    StockDisponibleListView,
    TransferenciaListCreateView,
    ValuacionInventarioListView,
)

urlpatterns = [
    path('api/inventario/productos/', ProductoListCreateView.as_view(), name='producto-list-create'),
    path('api/inventario/productos/<str:pk>/', ProductoDetailView.as_view(), name='producto-detail'),
    path('api/inventario/almacenes/', AlmacenListCreateView.as_view(), name='almacen-list-create'),
    path('api/inventario/almacenes/<str:pk>/', AlmacenDetailView.as_view(), name='almacen-detail'),
    path('api/inventario/movimientos/', MovimientoListCreateView.as_view(), name='movimiento-list-create'),
    path('api/inventario/ajustes/', AjusteListCreateView.as_view(), name='ajuste-list-create'),
    path('api/inventario/transferencias/', TransferenciaListCreateView.as_view(), name='transferencia-list-create'),
    path('api/inventario/stock-disponible/', StockDisponibleListView.as_view(), name='stock-disponible-list'),
    path('api/inventario/kardex/', KardexListView.as_view(), name='kardex-list'),
    path('api/inventario/valuacion/', ValuacionInventarioListView.as_view(), name='valuacion-list'),
    path(
        'api/inventario/alertas-stock-minimo/',
        AlertaStockMinimoListView.as_view(),
        name='alerta-stock-minimo-list',
    ),
    path(
        'api/inventario/alertas-stock-minimo/<int:pk>/notificar/',
        AlertaStockMinimoAckView.as_view(),
        name='alerta-stock-minimo-ack',
    ),
]
