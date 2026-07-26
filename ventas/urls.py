from django.urls import path

from ventas.views import (
    ClienteDetailView,
    ClienteListCreateView,
    CotizacionDetailView,
    CotizacionListCreateView,
    CotizacionResolverView,
    FacturaDetailView,
    FacturaListCreateView,
    FacturaNotaCreditoView,
    OportunidadCerrarView,
    OportunidadDetailView,
    OportunidadEtapaView,
    OportunidadListCreateView,
    OportunidadPipelineView,
    PedidoCancelarView,
    PedidoConfirmarView,
    PedidoDetailView,
    PedidoListCreateView,
)

urlpatterns = [
    path('api/ventas/clientes/', ClienteListCreateView.as_view(), name='cliente-list-create'),
    path('api/ventas/clientes/<str:pk>/', ClienteDetailView.as_view(), name='cliente-detail'),
    # RF-30..33: oportunidades. 'pipeline/' antes de '<pk>/' para que no lo capture.
    path('api/ventas/oportunidades/', OportunidadListCreateView.as_view(), name='oportunidad-list-create'),
    path('api/ventas/oportunidades/pipeline/', OportunidadPipelineView.as_view(), name='oportunidad-pipeline'),
    path('api/ventas/oportunidades/<str:pk>/', OportunidadDetailView.as_view(), name='oportunidad-detail'),
    path('api/ventas/oportunidades/<str:pk>/etapa/', OportunidadEtapaView.as_view(), name='oportunidad-etapa'),
    path('api/ventas/oportunidades/<str:pk>/cerrar/', OportunidadCerrarView.as_view(), name='oportunidad-cerrar'),
    # RF-34..37: cotizaciones
    path('api/ventas/cotizaciones/', CotizacionListCreateView.as_view(), name='cotizacion-list-create'),
    path('api/ventas/cotizaciones/<str:pk>/', CotizacionDetailView.as_view(), name='cotizacion-detail'),
    path('api/ventas/cotizaciones/<str:pk>/resolver/', CotizacionResolverView.as_view(), name='cotizacion-resolver'),
    # RF-38..41: pedidos
    path('api/ventas/pedidos/', PedidoListCreateView.as_view(), name='pedido-list-create'),
    path('api/ventas/pedidos/<str:pk>/', PedidoDetailView.as_view(), name='pedido-detail'),
    path('api/ventas/pedidos/<str:pk>/confirmar/', PedidoConfirmarView.as_view(), name='pedido-confirmar'),
    path('api/ventas/pedidos/<str:pk>/cancelar/', PedidoCancelarView.as_view(), name='pedido-cancelar'),
    # RF-42..44: facturas y notas de credito
    path('api/ventas/facturas/', FacturaListCreateView.as_view(), name='factura-list-create'),
    path('api/ventas/facturas/<str:pk>/', FacturaDetailView.as_view(), name='factura-detail'),
    path('api/ventas/facturas/<str:pk>/nota-credito/', FacturaNotaCreditoView.as_view(), name='factura-nota-credito'),
]
