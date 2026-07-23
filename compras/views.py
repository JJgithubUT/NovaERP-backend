import json

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from compras.models import OrdenCompra, Proveedor, RecepcionMercancia
from compras.services import aprobacion_service as aprobacion_svc
from compras.services import catalogo_service as svc
from compras.services import historial_service as historial_svc
from compras.services import orden_service as orden_svc
from compras.services import recepcion_service as recepcion_svc
from core.models import Tenant
from core.utils.auth import LoginRequiredMixin, UNAUTHORIZED, tenant_scoped
from core.utils.errors import BusinessRuleError
from core.utils.views import CatalogDetailView, CatalogListCreateView, ListCreateView, ReadOnlyListView
from finanzas.models import CuentaPorPagar


class ProveedorListCreateView(CatalogListCreateView):
    model = Proveedor
    search_fields = ("razon_social", "rfc_o_id_fiscal")
    ordering = ("razon_social",)
    serialize_fn = staticmethod(svc.serialize_proveedor)
    create_fn = staticmethod(svc.crear_proveedor)


class ProveedorDetailView(CatalogDetailView):
    model = Proveedor
    serialize_fn = staticmethod(svc.serialize_proveedor)
    edit_fn = staticmethod(svc.editar_proveedor)
    deactivate_fn = staticmethod(svc.dar_de_baja_proveedor)


# ---------------------------------------------------------------- RF-51

@method_decorator(csrf_exempt, name="dispatch")
class ProveedorHistorialView(LoginRequiredMixin, View):
    def get(self, request, pk):
        try:
            proveedor = tenant_scoped(Proveedor.objects.all(), request).get(pk=pk)
        except (Proveedor.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        return JsonResponse(historial_svc.historial_por_proveedor(proveedor))


# ---------------------------------------------------------------- RF-47

class OrdenCompraListCreateView(ListCreateView):
    model = OrdenCompra
    ordering = ("-created_at",)
    serialize_fn = staticmethod(orden_svc.serialize_orden)
    create_fn = staticmethod(orden_svc.crear_orden_compra)
    filter_fields = ("proveedor_id", "estado")
    date_field = "created_at"


# ---------------------------------------------------------------- RF-48

@method_decorator(csrf_exempt, name="dispatch")
class OrdenCompraDetailView(LoginRequiredMixin, View):
    def _get_object(self, request, pk):
        return tenant_scoped(OrdenCompra.objects.all(), request).get(pk=pk)

    def get(self, request, pk):
        try:
            orden = self._get_object(request, pk)
        except (OrdenCompra.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        return JsonResponse(orden_svc.serialize_orden(orden))

    def patch(self, request, pk):
        try:
            orden = self._get_object(request, pk)
        except (OrdenCompra.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            orden = orden_svc.editar_orden_compra(orden, data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        return JsonResponse(orden_svc.serialize_orden(orden))


@method_decorator(csrf_exempt, name="dispatch")
class OrdenCompraCancelarView(LoginRequiredMixin, View):
    def post(self, request, pk):
        try:
            orden = tenant_scoped(OrdenCompra.objects.all(), request).get(pk=pk)
        except (OrdenCompra.DoesNotExist, ValidationError, ValueError):
            return JsonResponse({"detail": "No encontrado"}, status=404)

        try:
            orden = orden_svc.cancelar_orden_compra(orden, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)

        return JsonResponse(orden_svc.serialize_orden(orden))


# ---------------------------------------------------------------- RF-49 / RF-50

class RecepcionListCreateView(ListCreateView):
    model = RecepcionMercancia
    ordering = ("-created_at",)
    serialize_fn = staticmethod(recepcion_svc.serialize_recepcion)
    create_fn = staticmethod(recepcion_svc.crear_recepcion)
    filter_fields = ("orden_id", "almacen_id")
    date_field = "created_at"


class CuentaPorPagarListView(ReadOnlyListView):
    model = CuentaPorPagar
    ordering = ("-created_at",)
    serialize_fn = staticmethod(historial_svc.serialize_cuenta_por_pagar)
    filter_fields = ("proveedor_id",)
    date_field = "created_at"


# ---------------------------------------------------------------- RF-52

@method_decorator(csrf_exempt, name="dispatch")
class ConfigAprobacionView(LoginRequiredMixin, View):
    def get(self, request):
        try:
            config = aprobacion_svc.obtener_config(request)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

        return JsonResponse(aprobacion_svc.serialize_config(config))

    def put(self, request):
        return self._guardar(request)

    def patch(self, request):
        return self._guardar(request)

    def _guardar(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)

        try:
            config = aprobacion_svc.actualizar_umbral(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

        return JsonResponse(aprobacion_svc.serialize_config(config))
