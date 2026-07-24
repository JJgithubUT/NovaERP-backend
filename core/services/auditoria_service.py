import csv
from io import BytesIO

from django.db.models import Count, Max, OuterRef, Subquery
from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.models import LogAuditoria, Sesion, Usuario
from core.utils.audit import audit_context, client_ip
from core.utils.auth import get_tenant, tenant_scoped
from core.utils.pagination import paginate

# Operaciones CUD que cuentan como "acciones registradas" en el reporte (RF-23).
OPERACIONES_CUD = ("INSERT", "UPDATE", "DELETE")

FORMATOS_EXPORT = ("csv", "pdf")

_ESTILO_CELDA = ParagraphStyle("celda", fontName="Helvetica", fontSize=6, leading=7)
_ESTILO_CABECERA = ParagraphStyle("cab", fontName="Helvetica-Bold", fontSize=6, leading=7, textColor=colors.white)
_ESTILO_META = ParagraphStyle("meta", fontName="Helvetica", fontSize=8, leading=11)
_ESTILO_TITULO = ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=14, leading=18)


def _pdf_tabla(titulo, metadatos, columnas, filas):
    """PDF tabular con titulo + metadatos + tabla, en A4 apaisado. Cada celda va
    en un Paragraph para que el texto largo (UUIDs, JSON) haga wrap en su
    columna en vez de desbordar. reportlab pagina solo y repite la cabecera."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1 * cm, rightMargin=1 * cm, topMargin=1 * cm, bottomMargin=1 * cm,
    )
    elementos = [Paragraph(titulo, _ESTILO_TITULO), Spacer(1, 6)]
    for m in metadatos:
        elementos.append(Paragraph(m, _ESTILO_META))
    elementos.append(Spacer(1, 10))

    data = [[Paragraph(str(col), _ESTILO_CABECERA) for col in columnas]]
    for fila in filas:
        data.append([Paragraph("" if v is None else str(v), _ESTILO_CELDA) for v in fila])

    tabla = Table(data, repeatRows=1)
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    elementos.append(tabla)
    doc.build(elementos)
    return buf.getvalue()


def _respuesta_archivo(contenido, content_type, nombre):
    resp = HttpResponse(contenido, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return resp


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


def _csv_bytes(titulo, metadatos, columnas, filas):
    from io import StringIO

    s = StringIO()
    w = csv.writer(s)
    w.writerow([f"# {titulo}"])
    for m in metadatos:
        w.writerow([f"# {m}"])
    w.writerow([])
    w.writerow(columnas)
    for fila in filas:
        w.writerow(fila)
    return s.getvalue().encode("utf-8")


def exportar_bitacora(request, formato):
    """RF-24: exporta la consulta (mismos filtros y alcance de RF-21, RN01) a
    CSV o PDF (CA01). Metadatos de generacion en el archivo (CA02). La
    exportacion se registra como evento propio EXPORT (RN02/CA03)."""
    tenant = get_tenant(request)
    qs = _bitacora_filtrada(request)
    ahora = timezone.now()

    filtros = {
        k: request.GET.get(k)
        for k in ("usuario_id", "operacion", "entidad", "desde", "hasta")
        if request.GET.get(k)
    }
    metadatos = [
        f"Tenant: {tenant.slug}",
        f"Generado por: {request.usuario_id}",
        f"Fecha: {ahora.isoformat()}",
        f"Filtros: {filtros or 'ninguno'}",
    ]
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

    nombre = f"bitacora_{tenant.slug}_{ahora:%Y%m%d_%H%M%S}.{formato}"
    if formato == "pdf":
        pdf = _pdf_tabla("Bitacora de auditoria - NovaERP", metadatos, COLUMNAS_BITACORA, filas)
        return _respuesta_archivo(pdf, "application/pdf", nombre)
    return _respuesta_archivo(
        _csv_bytes("Bitacora de auditoria - NovaERP", metadatos, COLUMNAS_BITACORA, filas),
        "text/csv; charset=utf-8", nombre,
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
    filtros = {
        k: request.GET.get(k)
        for k in ("desde", "hasta", "departamento", "puesto")
        if request.GET.get(k)
    }
    metadatos = [
        f"Tenant: {tenant.slug}",
        f"Generado por: {request.usuario_id}",
        f"Fecha: {ahora.isoformat()}",
        f"Filtros: {filtros or 'ninguno'}",
    ]
    filas = [
        [d[c] for c in COLUMNAS_ACTIVIDAD]
        for d in (_serialize_actividad(u) for u in _actividad_queryset(request).iterator())
    ]

    nombre = f"actividad_{tenant.slug}_{ahora:%Y%m%d_%H%M%S}.{formato}"
    if formato == "pdf":
        pdf = _pdf_tabla("Reporte de actividad de usuarios - NovaERP", metadatos, COLUMNAS_ACTIVIDAD, filas)
        return _respuesta_archivo(pdf, "application/pdf", nombre)
    return _respuesta_archivo(
        _csv_bytes("Reporte de actividad de usuarios - NovaERP", metadatos, COLUMNAS_ACTIVIDAD, filas),
        "text/csv; charset=utf-8", nombre,
    )
