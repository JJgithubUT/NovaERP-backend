import json

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.models import Tenant
from core.utils.auth import UNAUTHORIZED
from core.utils.errors import BusinessRuleError
from core.utils.permissions import PermissionDeniedError, PermissionRequiredMixin
from core.utils.views import CatalogDetailView, CatalogListCreateView
from ventas.models import Cliente, Cotizacion, FacturaVenta, Oportunidad, PedidoVenta
from ventas.services import catalogo_service as svc
from ventas.services import cotizacion_service as cot_svc
from ventas.services import factura_service as fac_svc
from ventas.services import oportunidad_service as op_svc
from ventas.services import pedido_service as ped_svc


class ClienteListCreateView(CatalogListCreateView):
    model = Cliente
    permisos = {"GET": "ventas:clientes:leer", "POST": "ventas:clientes:crear"}
    search_fields = ("razon_social", "rfc_o_id_fiscal")
    ordering = ("razon_social",)
    serialize_fn = staticmethod(svc.serialize_cliente)
    create_fn = staticmethod(svc.crear_cliente)


class ClienteDetailView(CatalogDetailView):
    model = Cliente
    permisos = {"PATCH": "ventas:clientes:editar", "DELETE": "ventas:clientes:eliminar"}
    serialize_fn = staticmethod(svc.serialize_cliente)
    edit_fn = staticmethod(svc.editar_cliente)
    deactivate_fn = staticmethod(svc.dar_de_baja_cliente)


# ============================================================ RF-30..33 Oportunidades

def _json(request):
    return json.loads(request.body)


@method_decorator(csrf_exempt, name="dispatch")
class OportunidadListCreateView(PermissionRequiredMixin, View):
    """RF-31 (GET, lista tabular acotada a las propias / todas) y RF-30 (POST)."""

    permisos = {"GET": "ventas:oportunidades:leer", "POST": "ventas:oportunidades:crear"}

    def get(self, request):
        try:
            return JsonResponse(op_svc.listar_oportunidades(request))
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

    def post(self, request):
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            op = op_svc.crear_oportunidad(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)
        return JsonResponse(op_svc.serialize_oportunidad(op), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class OportunidadPipelineView(PermissionRequiredMixin, View):
    """RF-31: pipeline kanban por etapa (conteo, valor total y ponderado)."""

    permiso_requerido = "ventas:oportunidades:leer"

    def get(self, request):
        try:
            return JsonResponse(op_svc.pipeline(request))
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)


@method_decorator(csrf_exempt, name="dispatch")
class OportunidadDetailView(PermissionRequiredMixin, View):
    """GET detalle + PATCH edicion de datos (solo abierta, solo responsable/ver_todo)."""

    permisos = {"GET": "ventas:oportunidades:leer", "PATCH": "ventas:oportunidades:editar"}

    def get(self, request, pk):
        try:
            op = op_svc.get_oportunidad(request, pk)
        except (Oportunidad.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        return JsonResponse(op_svc.serialize_oportunidad(op))

    def patch(self, request, pk):
        try:
            op = op_svc.get_oportunidad(request, pk)
        except (Oportunidad.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            op = op_svc.editar_oportunidad(op, data, request)
        except PermissionDeniedError as e:
            return JsonResponse(e.to_dict(), status=403)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(op_svc.serialize_oportunidad(op))


@method_decorator(csrf_exempt, name="dispatch")
class OportunidadEtapaView(PermissionRequiredMixin, View):
    """RF-32: avanzar la etapa a la siguiente."""

    permiso_requerido = "ventas:oportunidades:editar"

    def post(self, request, pk):
        try:
            op = op_svc.get_oportunidad(request, pk)
        except (Oportunidad.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            op = op_svc.actualizar_etapa(op, data, request)
        except PermissionDeniedError as e:
            return JsonResponse(e.to_dict(), status=403)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(op_svc.serialize_oportunidad(op))


@method_decorator(csrf_exempt, name="dispatch")
class OportunidadCerrarView(PermissionRequiredMixin, View):
    """RF-33: cerrar como ganada/perdida."""

    permiso_requerido = "ventas:oportunidades:cerrar"

    def post(self, request, pk):
        try:
            op = op_svc.get_oportunidad(request, pk)
        except (Oportunidad.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            op = op_svc.cerrar_oportunidad(op, data, request)
        except PermissionDeniedError as e:
            return JsonResponse(e.to_dict(), status=403)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(op_svc.serialize_oportunidad(op))


# ============================================================ RF-34..37 Cotizaciones

@method_decorator(csrf_exempt, name="dispatch")
class CotizacionListCreateView(PermissionRequiredMixin, View):
    """RF-35 (GET, listado) y RF-34 (POST, generar)."""

    permisos = {"GET": "ventas:cotizaciones:leer", "POST": "ventas:cotizaciones:crear"}

    def get(self, request):
        try:
            return JsonResponse(cot_svc.listar_cotizaciones(request))
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

    def post(self, request):
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            cot = cot_svc.crear_cotizacion(data, request)
        except PermissionDeniedError as e:
            return JsonResponse(e.to_dict(), status=403)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)
        return JsonResponse(cot_svc.serialize_cotizacion(cot), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class CotizacionDetailView(PermissionRequiredMixin, View):
    """GET detalle + PATCH edicion (RF-36, solo borrador/pendiente_aprobacion)."""

    permisos = {"GET": "ventas:cotizaciones:leer", "PATCH": "ventas:cotizaciones:editar"}

    def get(self, request, pk):
        try:
            cot = cot_svc.get_cotizacion(request, pk)
        except (Cotizacion.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        return JsonResponse(cot_svc.serialize_cotizacion(cot))

    def patch(self, request, pk):
        try:
            cot = cot_svc.get_cotizacion(request, pk)
        except (Cotizacion.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            cot = cot_svc.editar_cotizacion(cot, data, request)
        except PermissionDeniedError as e:
            return JsonResponse(e.to_dict(), status=403)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(cot_svc.serialize_cotizacion(cot))


@method_decorator(csrf_exempt, name="dispatch")
class CotizacionResolverView(PermissionRequiredMixin, View):
    """RF-37: aprobar / rechazar cotizacion."""

    permiso_requerido = "ventas:cotizaciones:aprobar"

    def post(self, request, pk):
        try:
            cot = cot_svc.get_cotizacion(request, pk)
        except (Cotizacion.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            cot = cot_svc.resolver_cotizacion(cot, data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(cot_svc.serialize_cotizacion(cot))


# ============================================================ RF-38..41 Pedidos

@method_decorator(csrf_exempt, name="dispatch")
class PedidoListCreateView(PermissionRequiredMixin, View):
    """RF-39 (GET, listado) y RF-38 (POST, crear borrador)."""

    permisos = {"GET": "ventas:pedidos:leer", "POST": "ventas:pedidos:crear"}

    def get(self, request):
        try:
            return JsonResponse(ped_svc.listar_pedidos(request))
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

    def post(self, request):
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            p = ped_svc.crear_pedido(data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)
        return JsonResponse(ped_svc.serialize_pedido(p), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class PedidoDetailView(PermissionRequiredMixin, View):
    """GET detalle + PATCH edicion (RF-40, solo borrador)."""

    permisos = {"GET": "ventas:pedidos:leer", "PATCH": "ventas:pedidos:editar"}

    def get(self, request, pk):
        try:
            p = ped_svc.get_pedido(request, pk)
        except (PedidoVenta.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        return JsonResponse(ped_svc.serialize_pedido(p))

    def patch(self, request, pk):
        try:
            p = ped_svc.get_pedido(request, pk)
        except (PedidoVenta.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            p = ped_svc.editar_pedido(p, data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(ped_svc.serialize_pedido(p))


@method_decorator(csrf_exempt, name="dispatch")
class PedidoConfirmarView(PermissionRequiredMixin, View):
    """RF-38: confirmar pedido (reserva stock, valida credito)."""

    permiso_requerido = "ventas:pedidos:editar"

    def post(self, request, pk):
        try:
            p = ped_svc.get_pedido(request, pk)
        except (PedidoVenta.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            p = ped_svc.confirmar_pedido(p, data, request)
        except PermissionDeniedError as e:
            return JsonResponse(e.to_dict(), status=403)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(ped_svc.serialize_pedido(p))


@method_decorator(csrf_exempt, name="dispatch")
class PedidoCancelarView(PermissionRequiredMixin, View):
    """RF-41: cancelar pedido (libera stock reservado)."""

    permiso_requerido = "ventas:pedidos:cancelar"

    def post(self, request, pk):
        try:
            p = ped_svc.get_pedido(request, pk)
        except (PedidoVenta.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            p = ped_svc.cancelar_pedido(p, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(ped_svc.serialize_pedido(p))


# ============================================================ RF-42..44 Facturas

@method_decorator(csrf_exempt, name="dispatch")
class FacturaListCreateView(PermissionRequiredMixin, View):
    """RF-43 (GET, listado) y RF-42 (POST, generar factura de un pedido)."""

    permisos = {"GET": "ventas:facturas:leer", "POST": "ventas:facturas:crear"}

    def get(self, request):
        try:
            return JsonResponse(fac_svc.listar_facturas(request))
        except Tenant.DoesNotExist:
            return JsonResponse(UNAUTHORIZED, status=401)

    def post(self, request):
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            pedido = ped_svc.get_pedido(request, data.get("pedido_id"))
        except (PedidoVenta.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "Pedido no encontrado."}, status=404)
        try:
            factura = fac_svc.generar_factura(pedido, data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(fac_svc.serialize_factura(factura), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class FacturaDetailView(PermissionRequiredMixin, View):
    """RF-43: detalle de factura."""

    permiso_requerido = "ventas:facturas:leer"

    def get(self, request, pk):
        try:
            f = fac_svc.get_factura(request, pk)
        except (FacturaVenta.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        return JsonResponse(fac_svc.serialize_factura(f))


@method_decorator(csrf_exempt, name="dispatch")
class FacturaNotaCreditoView(PermissionRequiredMixin, View):
    """RF-44: emitir nota de credito sobre una factura."""

    permiso_requerido = "ventas:facturas:cancelar"

    def post(self, request, pk):
        try:
            f = fac_svc.get_factura(request, pk)
        except (FacturaVenta.DoesNotExist, Tenant.DoesNotExist):
            return JsonResponse({"detail": "No encontrado"}, status=404)
        try:
            data = _json(request)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "JSON invalido."}, status=400)
        try:
            f = fac_svc.crear_nota_credito(f, data, request)
        except BusinessRuleError as e:
            return JsonResponse(e.to_dict(), status=422)
        return JsonResponse(fac_svc.serialize_factura(f))
