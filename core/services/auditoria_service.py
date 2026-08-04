from django.db.models import Count, Max, OuterRef, Subquery
from django.utils import timezone

from core.models import LogAuditoria, Sesion, Usuario
from core.utils.audit import audit_context, client_ip
from core.utils.auth import get_tenant, tenant_scoped
from core.utils.export import FORMATOS_EXPORT, entregar, filtros_de, metadatos, nombre_archivo
from core.utils.pagination import paginate

# Operaciones CUD que cuentan como "acciones registradas" en el reporte (RF-23).
OPERACIONES_CUD = ("INSERT", "UPDATE", "DELETE")

# Reexportado a proposito: las vistas y las pruebas de RF-23/RF-24 ya lo
# importan desde aqui, y el formato valido es el mismo para todo el sistema.
__all__ = ["FORMATOS_EXPORT"]


# ------------------------------------------------------------- RF-21

def _bitacora_filtrada(request):
    """Queryset de la bitacora acotado al tenant del JWT (RN01: el TENANT_ADMIN
    solo ve su tenant; las filas de plataforma con tenant NULL quedan fuera por
    el propio filtro) y con los filtros de CA01."""
    qs = tenant_scoped(LogAuditoria.objects.all(), request)

    usuario_id = request.GET.get("usuario_id")
    if usuario_id:
        qs = qs.filter(usuario_id=usuario_id)

    operacion = request.GET.get("operacion")
    if operacion:
        qs = qs.filter(operacion=operacion)

    entidad = request.GET.get("entidad")
    if entidad:
        qs = qs.filter(entidad=entidad)

    desde, hasta = request.GET.get("desde"), request.GET.get("hasta")
    if desde:
        qs = qs.filter(ocurrido_en__gte=desde)
    if hasta:
        qs = qs.filter(ocurrido_en__lte=hasta)

    return qs.order_by("-ocurrido_en")


def serialize_evento(e):
    return {
        "id": e.id,
        "ocurrido_en": e.ocurrido_en.isoformat(),
        "usuario_id": str(e.usuario_id) if e.usuario_id else None,
        "entidad": e.entidad,
        "entidad_id": e.entidad_id,
        "operacion": e.operacion,
        "criticidad": e.criticidad,
        "ip_origen": e.ip_origen,
        "valores_antes": e.valores_antes,
        "valores_despues": e.valores_despues,
    }


def consultar_bitacora(request):
    """RF-21: consulta paginada de la bitacora. Solo lectura (CA02); la tabla es
    append-only a nivel de motor, asi que ni siquiera existe forma de mutarla."""
    return paginate(_bitacora_filtrada(request), request, serialize_evento)


# ------------------------------------------------------------- RF-24

COLUMNAS_BITACORA = ["ocurrido_en", "usuario_id", "entidad", "entidad_id", "operacion", "criticidad", "ip_origen"]


def _fila_bitacora(e):
    return [
        e.ocurrido_en.isoformat(),
        str(e.usuario_id) if e.usuario_id else "",
        e.entidad, e.entidad_id, e.operacion, e.criticidad, e.ip_origen or "",
    ]


def exportar_bitacora(request, formato):
    """RF-24: exporta la consulta (mismos filtros y alcance de RF-21, RN01) a
    CSV o PDF (CA01). Metadatos de generacion en el archivo (CA02). La
    exportacion se registra como evento propio EXPORT (RN02/CA03)."""
    tenant = get_tenant(request)
    qs = _bitacora_filtrada(request)
    ahora = timezone.now()

    filtros = filtros_de(request, ("usuario_id", "operacion", "entidad", "desde", "hasta"))
    meta = metadatos(request, tenant, ahora, filtros)
    filas = [_fila_bitacora(e) for e in qs.iterator()]

    # RN02/CA03: se audita ANTES de entregar el archivo, con el formato usado.
    with audit_context(request, tenant_id=tenant.id):
        LogAuditoria.objects.create(
            tenant=tenant,
            usuario_id=request.usuario_id,
            entidad="log_auditoria",
            entidad_id="export",
            operacion="EXPORT",
            criticidad="NORMAL",
            ip_origen=client_ip(request) or None,
            ocurrido_en=ahora,
            valores_despues={"filtros": filtros, "formato": formato},
        )

    return entregar(
        formato, "Bitacora de auditoria - NovaERP", meta, COLUMNAS_BITACORA, filas,
        nombre_archivo(tenant, "bitacora", ahora, formato),
    )


# ------------------------------------------------------------- RF-23

def _actividad_queryset(request):
    """Queryset del reporte de actividad (compartido por la vista JSON y las
    exportaciones)."""
    desde, hasta = request.GET.get("desde"), request.GET.get("hasta")

    sesiones_sub = Sesion.objects.filter(usuario_id=OuterRef("id"))
    cud_sub = LogAuditoria.objects.filter(
        usuario_id=OuterRef("id"), operacion__in=OPERACIONES_CUD
    )
    if desde:
        sesiones_sub = sesiones_sub.filter(emitida_en__gte=desde)
        cud_sub = cud_sub.filter(ocurrido_en__gte=desde)
    if hasta:
        sesiones_sub = sesiones_sub.filter(emitida_en__lte=hasta)
        cud_sub = cud_sub.filter(ocurrido_en__lte=hasta)

    ultimo_sub = (
        LogAuditoria.objects.filter(usuario_id=OuterRef("id"), operacion="LOGIN")
        .values("usuario_id")
        .annotate(m=Max("ocurrido_en"))
        .values("m")
    )

    qs = tenant_scoped(Usuario.objects.all(), request).annotate(
        num_sesiones=Subquery(sesiones_sub.values("usuario_id").annotate(n=Count("id")).values("n")),
        num_acciones=Subquery(cud_sub.values("usuario_id").annotate(n=Count("id")).values("n")),
        ultimo_acceso=Subquery(ultimo_sub),
    )

    departamento = request.GET.get("departamento")
    if departamento:
        qs = qs.filter(departamento=departamento)
    puesto = request.GET.get("puesto")
    if puesto:
        qs = qs.filter(puesto=puesto)

    return qs.order_by("nombre_completo")


def _serialize_actividad(u):
    return {
        "usuario_id": str(u.id),
        "nombre_completo": u.nombre_completo,
        "correo": u.correo,
        "departamento": u.departamento,
        "puesto": u.puesto,
        "estado": u.estado,
        "ultimo_acceso": u.ultimo_acceso.isoformat() if u.ultimo_acceso else None,
        "num_sesiones": u.num_sesiones or 0,
        "num_acciones_cud": u.num_acciones or 0,
    }


COLUMNAS_ACTIVIDAD = [
    "usuario_id", "nombre_completo", "correo", "departamento", "puesto",
    "estado", "ultimo_acceso", "num_sesiones", "num_acciones_cud",
]


def reporte_actividad(request):
    """RF-23: reporte consolidado de actividad por usuario del tenant (RN01).
    Por usuario (CA01): ultimo acceso, numero de sesiones, numero de acciones
    CUD auditadas y estado. Filtrable por rango de fechas y por
    departamento/puesto (CA02). Devuelve el sobre paginado (vista JSON)."""
    return paginate(_actividad_queryset(request), request, _serialize_actividad)


def exportar_actividad(request, formato):
    """RF-23/CA03: exporta el reporte completo a CSV o PDF."""
    tenant = get_tenant(request)
    ahora = timezone.now()
    filtros = filtros_de(request, ("desde", "hasta", "departamento", "puesto"))
    meta = metadatos(request, tenant, ahora, filtros)
    filas = [
        [d[c] for c in COLUMNAS_ACTIVIDAD]
        for d in (_serialize_actividad(u) for u in _actividad_queryset(request).iterator())
    ]

    return entregar(
        formato, "Reporte de actividad de usuarios - NovaERP", meta, COLUMNAS_ACTIVIDAD, filas,
        nombre_archivo(tenant, "actividad", ahora, formato),
    )
