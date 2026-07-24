from django.urls import path

from core.views import (
    ActivarUsuarioView,
    LoginView,
    ConfigSeguridadView,
    LogoutView,
    RecuperarPasswordView,
    RestablecerPasswordView,
    MeView,
    OtpView,
    PermisoCatalogoView,
    RolDetailView,
    RolListCreateView,
    SesionCerrarOtrasView,
    SesionDetailView,
    SesionListView,
    UsuarioCerrarSesionesView,
    UsuarioListCreateView,
    UsuarioDetailView,
    UsuarioReactivarView,
    UsuarioResetMfaView,
    UsuarioSuspenderView,
    UsuarioRolCreateView,
    UsuarioRolDeleteView,
)

urlpatterns = [
    path('api/auth/login/', LoginView.as_view(), name='login'),
    path('api/auth/otp/', OtpView.as_view(), name='otp'),
    path('api/auth/logout/', LogoutView.as_view(), name='logout'),
    path('api/auth/activar/', ActivarUsuarioView.as_view(), name='activar-usuario'),
    # RF-18: recuperar / restablecer contrasena
    path('api/auth/recuperar/', RecuperarPasswordView.as_view(), name='recuperar-password'),
    path('api/auth/restablecer/', RestablecerPasswordView.as_view(), name='restablecer-password'),
    path('api/core/me/', MeView.as_view(), name='me'),
    # RF-22: politicas de seguridad del tenant
    path('api/core/config-seguridad/', ConfigSeguridadView.as_view(), name='config-seguridad'),
    # RF-19: autogestion de sesiones propias. 'cerrar-otras/' va antes del
    # patron <jti> para que no lo capture como si fuera un identificador.
    path('api/core/sesiones/', SesionListView.as_view(), name='sesion-list'),
    path(
        'api/core/sesiones/cerrar-otras/',
        SesionCerrarOtrasView.as_view(),
        name='sesion-cerrar-otras',
    ),
    path('api/core/sesiones/<str:jti>/', SesionDetailView.as_view(), name='sesion-detail'),
    path('api/core/usuarios/', UsuarioListCreateView.as_view(), name='usuario-list-create'),
    # RF-07
    path('api/core/usuarios/<str:pk>/', UsuarioDetailView.as_view(), name='usuario-detail'),
    # RF-07 / RF-16 RN07: reseteo de MFA (accion exclusiva del TENANT_ADMIN)
    path(
        'api/core/usuarios/<str:pk>/reset-mfa/',
        UsuarioResetMfaView.as_view(),
        name='usuario-reset-mfa',
    ),
    # RF-08: suspender / reactivar usuario (accion del TENANT_ADMIN)
    path('api/core/usuarios/<str:pk>/suspender/', UsuarioSuspenderView.as_view(), name='usuario-suspender'),
    path('api/core/usuarios/<str:pk>/reactivar/', UsuarioReactivarView.as_view(), name='usuario-reactivar'),
    # RF-19/RN02: el TENANT_ADMIN cierra todas las sesiones de un usuario
    path(
        'api/core/usuarios/<str:pk>/cerrar-sesiones/',
        UsuarioCerrarSesionesView.as_view(),
        name='usuario-cerrar-sesiones',
    ),
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
