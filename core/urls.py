from django.urls import path

from core.views import (
    ActivarUsuarioView,
    LoginView,
    MeView,
    PermisoCatalogoView,
    RolDetailView,
    RolListCreateView,
    UsuarioCreateView,
    UsuarioRolCreateView,
    UsuarioRolDeleteView,
)

urlpatterns = [
    path('api/auth/login/', LoginView.as_view(), name='login'),
    path('api/auth/activar/', ActivarUsuarioView.as_view(), name='activar-usuario'),
    path('api/core/me/', MeView.as_view(), name='me'),
    path('api/core/usuarios/', UsuarioCreateView.as_view(), name='usuario-create'),
    # RF-14 / RF-15
    path(
        'api/core/usuarios/<str:pk>/roles/',
        UsuarioRolCreateView.as_view(),
        name='usuario-rol-create',
    ),
    path(
        'api/core/usuarios/<str:pk>/roles/<str:rol_pk>/',
        UsuarioRolDeleteView.as_view(),
        name='usuario-rol-delete',
    ),
    # RF-10 / RF-11 / RF-12 / RF-13
    path('api/core/roles/', RolListCreateView.as_view(), name='rol-list-create'),
    path('api/core/roles/<str:pk>/', RolDetailView.as_view(), name='rol-detail'),
    path('api/core/permisos/', PermisoCatalogoView.as_view(), name='permiso-catalogo'),
]
