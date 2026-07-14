from django.urls import path

from inventario.views import (
    AlmacenDetailView,
    AlmacenListCreateView,
    ProductoDetailView,
    ProductoListCreateView,
)

urlpatterns = [
    path('api/inventario/productos/', ProductoListCreateView.as_view(), name='producto-list-create'),
    path('api/inventario/productos/<str:pk>/', ProductoDetailView.as_view(), name='producto-detail'),
    path('api/inventario/almacenes/', AlmacenListCreateView.as_view(), name='almacen-list-create'),
    path('api/inventario/almacenes/<str:pk>/', AlmacenDetailView.as_view(), name='almacen-detail'),
]
