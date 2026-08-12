import datetime
import uuid
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Q, Sum
from django.utils import timezone

from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError
from core.utils import filtros
from core.utils.pagination import paginate
from core.utils.permissions import PermissionDeniedError
from ventas.models import Cliente, Oportunidad
from ventas.services.atribucion import PERMISO_VER_TODO, ve_todo

# Orden de las etapas (RF-32/RN01): solo se avanza a la SIGUIENTE, no se salta ni
# se retrocede. 'ganada'/'perdida' son estados terminales (RF-33), no etapas.
ETAPAS = ["prospeccion", "calificacion", "propuesta", "negociacion", "cierre"]

# Probabilidad sugerida por etapa (RF-30/RN02). Derivada, sin columna: al cerrar,
# 'ganada' = 100% y 'perdida' = 0%.
PROB_POR_ETAPA = {
    "prospeccion": 10, "calificacion": 30, "propuesta": 50, "negociacion": 75, "cierre": 90,
}

# Catalogo de motivos de perdida (RF-33/RN01).
MOTIVOS_PERDIDA = {"Precio", "Competencia", "Tiempo", "Presupuesto", "Sin respuesta", "Otro"}

def _probabilidad(op):
    if op.estado == "ganada":
        return 100
    if op.estado == "perdida":
        return 0
    return PROB_POR_ETAPA.get(op.etapa, 0)


def _scope(qs, request):
    """Acota a las oportunidades propias salvo permiso de ver todo (RF-31)."""
    if ve_todo(request):
        return qs
    return qs.filter(responsable_id=request.usuario_id)


def _exigir_operar(request, op):
    """Mutar una oportunidad ajena exige ver_todo (o ser admin)."""
    if str(op.responsable_id) != str(request.usuario_id) and not ve_todo(request):
        raise PermissionDeniedError(PERMISO_VER_TODO, "Solo puede operar sus propias oportunidades.")


def _to_decimal(value, campo):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise BusinessRuleError(f"{campo} debe ser un numero valido.", campo=campo)


def serialize_oportunidad(op):
    prob = _probabilidad(op)
    valor = op.valor_estimado or Decimal("0")
    return {
        "id": str(op.id),
        "cliente_id": str(op.cliente_id),
        "nombre": op.nombre,
        "valor_estimado": str(op.valor_estimado) if op.valor_estimado is not None else None,
        "fecha_cierre_estimada": op.fecha_cierre_estimada.isoformat() if op.fecha_cierre_estimada else None,
        "etapa": op.etapa,
        "estado": op.estado,
        "probabilidad": prob,
        "valor_ponderado": str((valor * prob / 100).quantize(Decimal("0.01"))),
        "motivo_perdida": op.motivo_perdida,
        "responsable_id": str(op.responsable_id) if op.responsable_id else None,
        "created_at": op.created_at.isoformat(),
        "updated_at": op.updated_at.isoformat(),
    }


# --------------------------------------------------------------- RF-30

def crear_oportunidad(data, request):
    """RF-30: registra una oportunidad ligada a un cliente activo (RN01). Nace en
    etapa 'prospeccion' / estado 'abierta'; el responsable es el usuario que la
    crea. CA: la fecha estimada de cierre no puede ser anterior a hoy."""
    tenant = get_tenant(request)

    cliente_id = data.get("cliente_id")
    if not cliente_id:
        raise BusinessRuleError("cliente_id es obligatorio.", campo="cliente_id")
    try:
        cliente = Cliente.objects.get(tenant=tenant, id=cliente_id, activo=True)
    except (Cliente.DoesNotExist, ValueError):
        raise BusinessRuleError("Cliente no encontrado o dado de baja.", campo="cliente_id")

    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        raise BusinessRuleError("nombre es obligatorio.", campo="nombre")

    valor = None
    if data.get("valor_estimado") is not None:
        valor = _to_decimal(data["valor_estimado"], "valor_estimado")
        if valor < 0:
            raise BusinessRuleError("valor_estimado debe ser >= 0.", campo="valor_estimado")

    fecha = _parse_fecha_futura(data.get("fecha_cierre_estimada"))

    now = timezone.now()
    with audit_context(request, tenant_id=tenant.id):
        op = Oportunidad.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            cliente=cliente,
            nombre=nombre,
            valor_estimado=valor,
            fecha_cierre_estimada=fecha,
            etapa="prospeccion",
            estado="abierta",
            responsable_id=request.usuario_id,
            created_at=now,
            updated_at=now,
        )
    return op


def _parse_fecha_futura(valor):
    if not valor:
        return None
    try:
        fecha = datetime.date.fromisoformat(str(valor))
    except ValueError:
        raise BusinessRuleError(
            "fecha_cierre_estimada debe tener formato YYYY-MM-DD.", campo="fecha_cierre_estimada"
        )
    if fecha < timezone.now().date():
        raise BusinessRuleError(
            "La fecha estimada de cierre no puede ser anterior a hoy.",
            campo="fecha_cierre_estimada",
        )
    return fecha


# --------------------------------------------------------------- RF-31

def listar_oportunidades(request):
    """RF-31: lista tabular paginada, acotada a las propias salvo ver_todo.
    Filtros por cliente, etapa, estado, responsable y rango de fecha de cierre."""
    tenant = get_tenant(request)
    qs = _scope(Oportunidad.objects.filter(tenant=tenant), request)

    for campo, val in filtros.filtros_validados(
        Oportunidad, request, ("cliente_id", "etapa", "estado", "responsable_id")
    ).items():
        qs = qs.filter(**{campo: val})

    desde, hasta = filtros.rango_validado(request)
    if desde:
        qs = qs.filter(fecha_cierre_estimada__gte=desde)
    if hasta:
        qs = qs.filter(fecha_cierre_estimada__lte=hasta)

    qs = qs.order_by("-created_at")
    return paginate(qs, request, serialize_oportunidad)


def pipeline(request):
    """RF-31: vista kanban por etapa (solo oportunidades abiertas) con conteo,
    valor total y valor ponderado (valor x probabilidad). Respeta el alcance
    propias/todas."""
    tenant = get_tenant(request)
    qs = _scope(Oportunidad.objects.filter(tenant=tenant, estado="abierta"), request)

    agg = {
        row["etapa"]: row
        for row in qs.values("etapa").annotate(num=Count("id"), valor=Sum("valor_estimado"))
    }
    etapas = []
    total_ponderado = Decimal("0")
    for etapa in ETAPAS:
        row = agg.get(etapa, {})
        valor = row.get("valor") or Decimal("0")
        prob = PROB_POR_ETAPA[etapa]
        ponderado = (valor * prob / 100).quantize(Decimal("0.01"))
        total_ponderado += ponderado
        etapas.append({
            "etapa": etapa,
            "num_oportunidades": row.get("num", 0),
            "valor_total": str(valor),
            "probabilidad": prob,
            "valor_ponderado": str(ponderado),
        })
    return {"etapas": etapas, "valor_ponderado_total": str(total_ponderado)}


# --------------------------------------------------------------- edicion / RF-32 / RF-33

def get_oportunidad(request, pk):
    """Devuelve la oportunidad del tenant o lanza Oportunidad.DoesNotExist (la
    vista lo traduce a 404). ValueError (pk malformado) se normaliza a lo mismo."""
    tenant = get_tenant(request)
    try:
        return Oportunidad.objects.select_related("cliente").get(tenant=tenant, id=pk)
    except ValueError:
        raise Oportunidad.DoesNotExist


CAMPOS_EDITABLES = ("nombre", "valor_estimado", "fecha_cierre_estimada")


def editar_oportunidad(op, data, request):
    """Edita datos de una oportunidad ABIERTA (una cerrada es terminal, RF-33/RN02).
    Solo el responsable o quien tenga ver_todo."""
    _exigir_operar(request, op)
    if op.estado != "abierta":
        raise BusinessRuleError(
            "Una oportunidad cerrada (ganada/perdida) no es editable.", campo="estado"
        )

    if "nombre" in data:
        nombre = (data["nombre"] or "").strip()
        if not nombre:
            raise BusinessRuleError("nombre no puede quedar vacio.", campo="nombre")
        op.nombre = nombre
    if "valor_estimado" in data:
        if data["valor_estimado"] is None:
            op.valor_estimado = None
        else:
            valor = _to_decimal(data["valor_estimado"], "valor_estimado")
            if valor < 0:
                raise BusinessRuleError("valor_estimado debe ser >= 0.", campo="valor_estimado")
            op.valor_estimado = valor
    if "fecha_cierre_estimada" in data:
        op.fecha_cierre_estimada = _parse_fecha_futura(data["fecha_cierre_estimada"])

    op.updated_at = timezone.now()
    with audit_context(request, tenant_id=op.tenant_id):
        op.save()
    return op


def actualizar_etapa(op, data, request):
    """RF-32: avanza la etapa a la SIGUIENTE en la secuencia (RN01: no se salta ni
    se retrocede). Solo mientras la oportunidad este abierta (RN02)."""
    _exigir_operar(request, op)
    if op.estado != "abierta":
        raise BusinessRuleError(
            "No se puede cambiar la etapa de una oportunidad cerrada.", campo="estado"
        )

    destino = (data.get("etapa") or "").strip()
    if destino not in ETAPAS:
        raise BusinessRuleError(
            "etapa destino invalida.", campo="etapa", extra={"etapas": ETAPAS}
        )

    actual_i = ETAPAS.index(op.etapa)
    destino_i = ETAPAS.index(destino)
    if destino_i != actual_i + 1:
        raise BusinessRuleError(
            f"No se puede saltar de {op.etapa} a {destino}.", campo="etapa"
        )

    op.etapa = destino
    op.updated_at = timezone.now()
    with audit_context(request, tenant_id=op.tenant_id):
        op.save(update_fields=["etapa", "updated_at"])
    return op


def cerrar_oportunidad(op, data, request):
    """RF-33: cierra la oportunidad como 'ganada' o 'perdida' (estados terminales).
    'perdida' exige un motivo del catalogo (RN01). Una vez cerrada no se reabre:
    reabrir = crear una nueva (RN02). 'ganada' habilita generar cotizacion (RF-34,
    afordance del frontend)."""
    _exigir_operar(request, op)
    if op.estado != "abierta":
        raise BusinessRuleError(
            "La oportunidad ya esta cerrada.", campo="estado", extra={"estado_actual": op.estado}
        )

    resultado = (data.get("estado") or "").strip()
    if resultado not in ("ganada", "perdida"):
        raise BusinessRuleError("estado debe ser 'ganada' o 'perdida'.", campo="estado")

    motivo = None
    if resultado == "perdida":
        motivo = (data.get("motivo_perdida") or "").strip()
        if not motivo:
            raise BusinessRuleError(
                "Debe capturar un motivo de perdida.", campo="motivo_perdida",
                extra={"motivos": sorted(MOTIVOS_PERDIDA)},
            )
        if motivo not in MOTIVOS_PERDIDA:
            raise BusinessRuleError(
                "motivo_perdida no esta en el catalogo.", campo="motivo_perdida",
                extra={"motivos": sorted(MOTIVOS_PERDIDA)},
            )

    op.estado = resultado
    op.motivo_perdida = motivo
    op.updated_at = timezone.now()
    with audit_context(request, tenant_id=op.tenant_id):
        op.save(update_fields=["estado", "motivo_perdida", "updated_at"])
    return op
