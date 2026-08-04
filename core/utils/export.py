"""Exportacion tabular a CSV y PDF, compartida por todos los reportes.

Nacio dentro de core/services/auditoria_service.py para RF-23/RF-24 (bitacora y
reporte de actividad). Al llegar los reportes de ventas (RV-01..06) se extrajo
aqui: un modulo de negocio no debe importar del servicio de auditoria para
generar un CSV.

Un reporte que exporta necesita tres cosas, y las tres estan aqui:

    filtros = filtros_de(request, ("desde", "hasta", "cliente_id"))
    meta    = metadatos(request, tenant, ahora, filtros)
    return entregar(formato, "Ventas por periodo - NovaERP", meta,
                    COLUMNAS, filas, nombre_archivo(tenant, "ventas", ahora, formato))
"""

import csv
from io import BytesIO, StringIO

from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FORMATOS_EXPORT = ("csv", "pdf")

_ESTILO_CELDA = ParagraphStyle("celda", fontName="Helvetica", fontSize=6, leading=7)
_ESTILO_CABECERA = ParagraphStyle("cab", fontName="Helvetica-Bold", fontSize=6, leading=7, textColor=colors.white)
_ESTILO_META = ParagraphStyle("meta", fontName="Helvetica", fontSize=8, leading=11)
_ESTILO_TITULO = ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=14, leading=18)


def pdf_tabla(titulo, metadatos, columnas, filas):
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


def csv_bytes(titulo, metadatos, columnas, filas):
    """CSV con el titulo y los metadatos como lineas de comentario (#) antes de
    la cabecera, para que el archivo sea autoexplicativo fuera del sistema."""
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


def respuesta_archivo(contenido, content_type, nombre):
    resp = HttpResponse(contenido, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return resp


def filtros_de(request, campos):
    """Filtros efectivos de la peticion (solo los presentes), para dejar
    constancia en los metadatos del archivo de con que criterios se genero."""
    return {c: request.GET.get(c) for c in campos if request.GET.get(c)}


def metadatos(request, tenant, ahora, filtros):
    """Cabecera de trazabilidad exigida por RF-24/CA02: quien exporto, cuando,
    de que tenant y con que filtros."""
    return [
        f"Tenant: {tenant.slug}",
        f"Generado por: {request.usuario_id}",
        f"Fecha: {ahora.isoformat()}",
        f"Filtros: {filtros or 'ninguno'}",
    ]


def nombre_archivo(tenant, base, ahora, formato):
    return f"{base}_{tenant.slug}_{ahora:%Y%m%d_%H%M%S}.{formato}"


def entregar(formato, titulo, metadatos, columnas, filas, nombre):
    """Despacha al generador segun el formato ya validado por la vista. Un
    formato desconocido cae a CSV en vez de reventar: la validacion del
    parametro es responsabilidad de la vista (400), no de esta capa."""
    if formato == "pdf":
        return respuesta_archivo(
            pdf_tabla(titulo, metadatos, columnas, filas), "application/pdf", nombre
        )
    return respuesta_archivo(
        csv_bytes(titulo, metadatos, columnas, filas), "text/csv; charset=utf-8", nombre
    )
