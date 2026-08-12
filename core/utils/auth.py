from functools import wraps

from django.http import JsonResponse

from core.utils.errors import ParametroInvalido

UNAUTHORIZED = {"detail": "No autorizado o sesion expirada"}

# La autorizacion vive en core/utils/permissions.py y se resuelve por codigo
# de permiso, nunca por nombre de rol.


def _is_authenticated(request):
    return bool(getattr(request, "usuario_id", None)) and bool(
        getattr(request, "tenant_slug", None)
    )


def auth_required(view_func):
    """Decorador para vistas basadas en funcion. JWTCustomMiddleware ya
    corrio y dejo request.usuario_id/request.tenant_slug en None si no
    habia un JWT valido; aqui solo se corta el paso si falta alguno."""

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not _is_authenticated(request):
            return JsonResponse(UNAUTHORIZED, status=401)
        return view_func(request, *args, **kwargs)

    return wrapped


class LoginRequiredMixin:
    """Mixin para vistas basadas en clase (CBV). Uso:

        class MiVista(LoginRequiredMixin, View):
            def get(self, request):
                ...

    Debe ir a la izquierda de View en la lista de bases para que su
    dispatch() se ejecute primero.

    Ademas de exigir la sesion, es el punto unico donde ParametroInvalido se
    traduce a 400: por aqui pasan todas las vistas de tenant (directamente o via
    PermissionRequiredMixin), asi que un filtro mal formado responde 400 tambien
    en los endpoints que se escriban despues, sin que cada uno lo recuerde.
    """

    def dispatch(self, request, *args, **kwargs):
        if not _is_authenticated(request):
            return JsonResponse(UNAUTHORIZED, status=401)
        try:
            return super().dispatch(request, *args, **kwargs)
        except ParametroInvalido as e:
            return JsonResponse(e.to_dict(), status=400)


class SysAdminRequiredMixin:
    """Mixin para las vistas del portal de plataforma (SysAdmin). Exige que el
    middleware haya reconocido una sesion de plataforma viva (request.sysadmin_id).

    El SysAdmin es superusuario plano sobre su superficie (RF-01..04): no pasa
    por PermissionResolver, que es por-tenant y por-rol. Simetricamente, una
    sesion de tenant NO satisface este mixin (su request.sysadmin_id es None), y
    las vistas de tenant no aceptan a un SysAdmin (su request.usuario_id es None):
    las dos superficies quedan aisladas.
    """

    def dispatch(self, request, *args, **kwargs):
        if not getattr(request, "sysadmin_id", None):
            return JsonResponse(UNAUTHORIZED, status=401)
        try:
            return super().dispatch(request, *args, **kwargs)
        except ParametroInvalido as e:
            return JsonResponse(e.to_dict(), status=400)


def tenant_scoped(queryset, request, lookup="tenant__slug"):
    """Helper para estandarizar el aislamiento por tenant en las futuras
    vistas de ventas/inventario/finanzas/etc, todas con FK `tenant`:

        clientes = tenant_scoped(Cliente.objects.all(), request)

    equivale a `Cliente.objects.filter(tenant__slug=request.tenant_slug)`.
    Se asume que la vista ya paso por LoginRequiredMixin/auth_required.
    """
    return queryset.filter(**{lookup: request.tenant_slug})


def get_tenant(request):
    """Resuelve el objeto Tenant real a partir de request.tenant_slug (el
    JWT solo trae el slug, no el id). Se cachea en el propio request para
    no repetir la consulta si se llama varias veces en el mismo ciclo.

    Lanza core.models.Tenant.DoesNotExist si el slug del JWT ya no
    corresponde a ningun tenant (sesion obsoleta); las vistas que la usan
    deben traducir eso a 401, igual que MeView.
    """
    if not hasattr(request, "_tenant_cache"):
        from core.models import Tenant

        request._tenant_cache = Tenant.objects.get(slug=request.tenant_slug)
    return request._tenant_cache
