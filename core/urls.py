from django.urls import path

from core.views import (
    ActivarUsuarioView,
    LoginView,
    BitacoraExportView,
    BitacoraView,
    ConfigSeguridadView,
    ReporteActividadView,
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
from core.views_sysadmin import (
    CatalogosView,
    SysAdminLoginView,
    SysAdminLogoutView,
    SysAdminMeView,
    TenantActivarView,
    TenantDetailView,
    TenantListCreateView,
    TenantReactivarView,
    TenantReenviarActivacionView,
    TenantSuspenderView,
)

urlpatterns = [
    # Portal de plataforma (SysAdmin) — superficie separada de la de tenant.
    # Fundacion habilitante para RF-01..04 (gestion de tenants).
    path('api/admin/login/', SysAdminLoginView.as_view(), name='sysadmin-login'),
    path('api/admin/logout/', SysAdminLogoutView.as_view(), name='sysadmin-logout'),
    path('api/admin/me/', SysAdminMeView.as_view(), name='sysadmin-me'),
    # Catalogo maestro de plataforma (planes, modulos, dependencias) que
    # consumen los formularios de RF-01 y RF-03.
    path('api/admin/catalogos/', CatalogosView.as_view(), name='sysadmin-catalogos'),
    # RF-01 (registrar) / RF-02 (consultar) / RF-03 (editar) tenants
    path('api/admin/tenants/', TenantListCreateView.as_view(), name='tenant-list-create'),
    path('api/admin/tenants/<str:pk>/', TenantDetailView.as_view(), name='tenant-detail'),
    # RF-04: suspender / baja logica / reactivar tenant
    path('api/admin/tenants/<str:pk>/suspender/', TenantSuspenderView.as_view(), name='tenant-suspender'),
    path('api/admin/tenants/<str:pk>/reactivar/', TenantReactivarView.as_view(), name='tenant-reactivar'),
    # RF-01: reemitir el enlace de activacion del admin inicial (24 h, un solo uso)
    path(
        'api/admin/tenants/<str:pk>/reenviar-activacion/',
        TenantReenviarActivacionView.as_view(),
        name='tenant-reenviar-activacion',
    ),
    # RF-01 flujo de activacion (publico: el admin inicial aun no puede autenticarse)
    path('api/auth/activar-tenant/', TenantActivarView.as_view(), name='tenant-activar'),
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
    # RF-21 / RF-24: consulta y exportacion de la bitacora de auditoria
    path('api/core/bitacora/', BitacoraView.as_view(), name='bitacora'),
    path('api/core/bitacora/export/', BitacoraExportView.as_view(), name='bitacora-export'),
    # RF-23: reporte de actividad de usuarios
    path('api/core/reportes/actividad/', ReporteActividadView.as_view(), name='reporte-actividad'),
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
