from django.urls import path

from compras.views import ProveedorDetailView, ProveedorListCreateView

urlpatterns = [
    path('api/compras/proveedores/', ProveedorListCreateView.as_view(), name='proveedor-list-create'),
    path('api/compras/proveedores/<str:pk>/', ProveedorDetailView.as_view(), name='proveedor-detail'),
]
