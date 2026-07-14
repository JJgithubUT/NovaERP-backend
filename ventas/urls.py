from django.urls import path

from ventas.views import ClienteDetailView, ClienteListCreateView

urlpatterns = [
    path('api/ventas/clientes/', ClienteListCreateView.as_view(), name='cliente-list-create'),
    path('api/ventas/clientes/<str:pk>/', ClienteDetailView.as_view(), name='cliente-detail'),
]
