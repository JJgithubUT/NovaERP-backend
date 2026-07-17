import json

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.models import Tenant
from core.utils.auth import UNAUTHORIZED, LoginRequiredMixin, get_tenant, tenant_scoped
from core.utils.errors import BusinessRuleError
from core.utils.pagination import paginate

TRUE_VALUES = {"1", "true", "si", "sí"}


def _apply_filters(qs, request, filter_fields, date_field):
    """Filtros exactos ?<campo>=valor por cada entrada de filter_fields, mas
    un rango ?desde=&hasta= sobre date_field si esta definido. Compartido
    entre ListCreateView y ReadOnlyListView."""
    for field in filter_fields:
        value = request.GET.get(field)
        if value:
            qs = qs.filter(**{field: value})

    if date_field:
        desde = request.GET.get("desde")
        hasta = request.GET.get("hasta")
        if desde:
            qs = qs.filter(**{f"{date_field}__gte": desde})
        if hasta:
            qs = qs.filter(**{f"{date_field}__lte": hasta})

    return qs


@method_decorator(csrf_exempt, name="dispatch")
class CatalogListCreateView(LoginRequiredMixin, View):
    """Base reutilizable para GET (lista paginada + busqueda + filtro
    ?activo=) y POST (alta) de un catalogo maestro. Cada app conecta su
    modelo y las funciones de su services/catalogo_service.py:

        class ProductoListCreateView(CatalogListCreateView):
            model = Producto
            search_fields = ("sku", "nombre")
            ordering = ("sku",)
            serialize_fn = staticmethod(svc.serialize_producto)
            create_fn = staticmethod(svc.crear_producto)
    """

    model = None
    search_fields = ()
    ordering = ("id",)
    serialize_fn = None
    create_fn = None

    def get(self, request):
        qs = tenant_scoped(self.model.objects.all(), request).order_by(*self.ordering)

        search = request.GET.get("search", "").strip()
        if search and self.search_fields:
            condition = Q()
            for field in self.search_fields:
                condition |= Q(**{f"{field}__icontains": search})
            qs = qs.filter(condition)

        activo = request.GET.get("activo")
        if activo is not None:
            qs = qs.filter(activo=activo.strip().lower() in TRUE_VALUES)

        return JsonResponse(paginate(qs, request, self.serialize_fn))

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            obj = self.create_fn(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

        return JsonResponse(self.serialize_fn(obj), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class CatalogDetailView(LoginRequiredMixin, View):
    """Base reutilizable para PATCH (edicion parcial) y DELETE (baja
    logica) de un registro puntual, siempre acotado al tenant del JWT."""

    model = None
    serialize_fn = None
    edit_fn = None
    deactivate_fn = None

    def _get_object(self, request, pk):
        return tenant_scoped(self.model.objects.all(), request).get(pk=pk)

    def patch(self, request, pk):
        try:
            obj = self._get_object(request, pk)
        except (self.model.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            obj = self.edit_fn(obj, data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        return JsonResponse(self.serialize_fn(obj))

    def delete(self, request, pk):
        try:
            obj = self._get_object(request, pk)
        except (self.model.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        try:
            obj = self.deactivate_fn(obj, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        return JsonResponse({"id": str(obj.pk), "activo": obj.activo})


@method_decorator(csrf_exempt, name="dispatch")
class ListCreateView(LoginRequiredMixin, View):
    """Base reutilizable para GET (lista paginada + filtros exactos) y POST
    (alta) de registros transaccionales acotados al tenant (movimientos,
    ajustes, transferencias, etc). A diferencia de CatalogListCreateView no
    asume busqueda de texto libre ni ?activo=; en su lugar usa
    filter_fields (igualdad exacta) y, opcionalmente, date_field para un
    rango ?desde=&hasta=:

        class AjusteListCreateView(ListCreateView):
            model = AjusteInventario
            serialize_fn = staticmethod(svc.serialize_ajuste)
            create_fn = staticmethod(svc.crear_ajuste)
            filter_fields = ("producto_id", "almacen_id")
            date_field = "created_at"
    """

    model = None
    ordering = ("-id",)
    serialize_fn = None
    create_fn = None
    filter_fields = ()
    date_field = None

    def get(self, request):
        qs = tenant_scoped(self.model.objects.all(), request).order_by(*self.ordering)
        qs = _apply_filters(qs, request, self.filter_fields, self.date_field)
        return JsonResponse(paginate(qs, request, self.serialize_fn))

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            obj = self.create_fn(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

        return JsonResponse(self.serialize_fn(obj), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class ReadOnlyListView(LoginRequiredMixin, View):
    """Base reutilizable para GET sobre modelos de solo lectura (vistas SQL
    como VKardex/VStockDisponible) o catalogos sin alta via API. Las vistas
    SQL de reportes exponen tenant_id crudo (no una FK `tenant`), asi que
    tenant_via_id=True filtra por id en vez de tenant_scoped (que espera
    `tenant__slug`)."""

    model = None
    ordering = ("-id",)
    serialize_fn = None
    filter_fields = ()
    date_field = None
    tenant_via_id = False

    def get(self, request):
        try:
            if self.tenant_via_id:
                qs = self.model.objects.filter(tenant_id=get_tenant(request).id)
            else:
                qs = tenant_scoped(self.model.objects.all(), request)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

        qs = qs.order_by(*self.ordering)
        qs = _apply_filters(qs, request, self.filter_fields, self.date_field)
        return JsonResponse(paginate(qs, request, self.serialize_fn))
