"""Reportes de ventas (RV-01..06). Ver docs/SPRINT-REPORTES-VENTAS.md.

Todos los reportes comparten: rango obligatorio y acotado (RN-05), el mismo
sobre de respuesta, y una version exportable a CSV/PDF con los mismos filtros.
Ninguno crea tablas ni vistas: agregan al vuelo sobre las tablas
transaccionales (decision D5), apoyados en los indices de
sql/2026-08-03_rv01_06_reportes_ventas.sql.
"""

import datetime
from decimal import Decimal

from django.db.models import Count, Max, Sum
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek
from django.utils import timezone

from core.models import Usuario
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError, ParametroInvalido
from core.utils.filtros import opcion_o_400, uuid_o_400
from core.utils.export import entregar, filtros_de, metadatos, nombre_archivo
from core.utils.pagination import paginate
from inventario.models import Producto
from ventas.models import Cliente, FacturaLinea, FacturaVenta, NotaCredito
from ventas.services.atribucion import ve_todo

CENTAVO = Decimal("0.01")
CERO = Decimal("0")

# RN-01: 'cancelada' no es venta y queda fuera de todo agregado.
# 'con_nota_credito' SI lo es: la NC se resta aparte, y descartar la factura
# entera borraria tambien la parte de la venta que no se devolvio.
ESTADOS_VENTA = ("emitida", "con_nota_credito")

# RN-05: techo del rango. Un reporte sin tope barreria toda la historia del
# tenant en una sola peticion.
RANGO_MAX_DIAS = 366

AGRUPACIONES = {"dia": TruncDay, "semana": TruncWeek, "mes": TruncMonth}


# ----------------------------------------------------------------- parametros

def _fecha(valor, campo):
    try:
        return datetime.date.fromisoformat(valor)
    except (TypeError, ValueError):
        raise ParametroInvalido(f"{campo} debe ser una fecha ISO (AAAA-MM-DD).", campo=campo)


def rango(request):
    """RN-05: desde/hasta obligatorios, ordenados y de como mucho 366 dias.

    Devuelve el par de fechas mas los limites datetime que usan las consultas:
    comparar el timestamptz contra un instante permite usar el indice
    (tenant_id, fecha_emision), cosa que __date__gte no haria.
    """
    d = request.GET.get("desde")
    h = request.GET.get("hasta")
    if not d or not h:
        raise ParametroInvalido("desde y hasta son obligatorios.", campo="desde")

    desde, hasta = _fecha(d, "desde"), _fecha(h, "hasta")
    if desde > hasta:
        raise BusinessRuleError("desde no puede ser posterior a hasta.", campo="desde")
    if (hasta - desde).days + 1 > RANGO_MAX_DIAS:
        raise BusinessRuleError(
            f"El rango no puede exceder {RANGO_MAX_DIAS} dias.",
            campo="hasta", extra={"dias_solicitados": (hasta - desde).days + 1},
        )

    tz = timezone.get_current_timezone()
    inicio = timezone.make_aware(datetime.datetime.combine(desde, datetime.time.min), tz)
    # Limite superior exclusivo: incluye el dia 'hasta' completo.
    fin = timezone.make_aware(
        datetime.datetime.combine(hasta + datetime.timedelta(days=1), datetime.time.min), tz
    )
    return desde, hasta, inicio, fin


def agrupacion(request, default="mes"):
    valor = (request.GET.get("agrupar") or default).strip().lower()
    opcion_o_400(valor, "agrupar", list(AGRUPACIONES))
    return valor, AGRUPACIONES[valor]


def limite(request, default=10, maximo=100):
    valor = request.GET.get("limit")
    if valor is None:
        return default
    try:
        n = int(valor)
    except (TypeError, ValueError):
        raise ParametroInvalido("limit debe ser un entero.", campo="limit")
    if not (1 <= n <= maximo):
        raise ParametroInvalido(f"limit debe estar entre 1 y {maximo}.", campo="limit")
    return n


def cliente_filtro(request):
    """?cliente_id= validado como uuid (400 si no lo es), o None si no viene.
    Ninguna consulta de este modulo debe leer request.GET['cliente_id'] directo:
    un uuid mal formado abortaria la consulta en Postgres con un 500."""
    valor = request.GET.get("cliente_id")
    return uuid_o_400(valor, "cliente_id") if valor else None


def vendedor_efectivo(request):
    """RN-04: quien puede ver todo filtra por el ?vendedor_id= que pida; quien
    no, se ve solo a si mismo, pida lo que pida.

    No es un 403: el reporte existe para el vendedor, acotado a lo suyo. Es la
    misma regla que ya aplica el pipeline de oportunidades (RF-31), y por eso
    ninguna consulta de este modulo debe leer request.GET['vendedor_id']
    directamente.
    """
    if ve_todo(request):
        valor = request.GET.get("vendedor_id")
        return uuid_o_400(valor, "vendedor_id") if valor else None
    return str(request.usuario_id)


def sobre(request, desde, hasta, campos_filtro, totales, resultados, con_alcance=True):
    """Sobre comun de todos los reportes. No es el sobre paginado de los
    listados a proposito: un agregado no es una pagina.

    `alcance_vendedor` dice a quien quedo acotado el reporte de verdad (null =
    toda la organizacion). Sin el, un vendedor sin ver_todo veria sus propias
    cifras creyendo que son las de la empresa. `con_alcance=False` para los
    reportes que no tienen dimension de vendedor, donde el campo confundiria.
    """
    payload = {
        "rango": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "filtros": {c: request.GET.get(c) for c in campos_filtro},
        "totales": totales,
        "resultados": resultados,
        "generado_en": timezone.now().isoformat(),
    }
    if con_alcance:
        payload["alcance_vendedor"] = vendedor_efectivo(request)  # RN-04
    return payload


def _money(valor):
    return str((valor or CERO).quantize(CENTAVO))


def _participacion(valor, total):
    """Peso de una fila sobre el total del rango. Devuelve None cuando el total
    no es positivo (un rango que solo tuvo devoluciones): un porcentaje sobre
    una base cero o negativa no significa nada y es peor que no darlo."""
    if not total or total <= CERO:
        return None
    return str((valor * 100 / total).quantize(CENTAVO))


def _orden(request, permitidos, default):
    """Mismo criterio que el resto de la API (core.utils.filtros.clave_orden): un
    valor fuera del catalogo es 400, no un fallback silencioso."""
    valor = (request.GET.get("orden") or default).strip().lower()
    return opcion_o_400(valor, "orden", permitidos)


# ------------------------------------------------------------- bases comunes

def facturas_del_rango(tenant, inicio, fin, request=None):
    """Facturas que cuentan como venta en el rango (RN-01)."""
    qs = FacturaVenta.objects.filter(
        tenant=tenant, estado__in=ESTADOS_VENTA,
        fecha_emision__gte=inicio, fecha_emision__lt=fin,
    )
    if request is not None:
        cliente = cliente_filtro(request)
        if cliente:
            qs = qs.filter(cliente_id=cliente)
        vendedor = vendedor_efectivo(request)  # RN-04
        if vendedor:
            qs = qs.filter(vendedor_id=vendedor)
    return qs


def notas_credito_del_rango(tenant, inicio, fin, request=None):
    """RN-02: la NC se imputa por SU fecha, no por la de la factura que corrige,
    para no alterar retroactivamente un periodo ya reportado. Los filtros de
    cliente/vendedor viajan por la factura ligada."""
    qs = NotaCredito.objects.filter(
        tenant=tenant, created_at__gte=inicio, created_at__lt=fin,
    )
    if request is not None:
        cliente = cliente_filtro(request)
        if cliente:
            qs = qs.filter(factura__cliente_id=cliente)
        vendedor = vendedor_efectivo(request)  # RN-04
        if vendedor:
            qs = qs.filter(factura__vendedor_id=vendedor)
    return qs


# --------------------------------------------------------------------- RV-01

FILTROS_RV01 = ("cliente_id", "vendedor_id", "agrupar")

COLUMNAS_RV01 = [
    "periodo", "num_facturas", "subtotal", "impuestos", "total_facturado",
    "notas_credito", "venta_neta", "ticket_promedio",
]


def ventas_por_periodo(request):
    """RV-01: facturacion agregada por dia/semana/mes.

    Facturas y notas de credito se agregan en DOS consultas y se fusionan aqui:
    unirlas en una sola duplicaria los importes de una factura con mas de una
    nota de credito.
    """
    tenant = get_tenant(request)
    desde, hasta, inicio, fin = rango(request)
    nombre_agrup, trunc = agrupacion(request)

    facturas = (
        facturas_del_rango(tenant, inicio, fin, request)
        .annotate(periodo=trunc("fecha_emision"))
        .values("periodo")
        .annotate(
            num_facturas=Count("id"),
            subtotal=Sum("subtotal"),
            impuestos=Sum("impuestos"),
            total=Sum("total"),
        )
    )
    notas = (
        notas_credito_del_rango(tenant, inicio, fin, request)
        .annotate(periodo=trunc("created_at"))
        .values("periodo")
        .annotate(monto=Sum("monto"))
    )
    nc_por_periodo = {n["periodo"]: n["monto"] or CERO for n in notas}

    filas = {f["periodo"]: f for f in facturas}
    # Un periodo puede tener solo notas de credito (devolucion de una venta
    # anterior): tiene que aparecer, con venta neta negativa.
    for periodo in nc_por_periodo:
        filas.setdefault(periodo, {"periodo": periodo, "num_facturas": 0})

    resultados, tot = [], {
        "num_facturas": 0, "subtotal": CERO, "impuestos": CERO,
        "total_facturado": CERO, "notas_credito": CERO, "venta_neta": CERO,
    }
    for periodo in sorted(filas):
        f = filas[periodo]
        num = f.get("num_facturas") or 0
        subtotal = f.get("subtotal") or CERO
        impuestos = f.get("impuestos") or CERO
        total = f.get("total") or CERO
        nc = nc_por_periodo.get(periodo, CERO)
        neta = total - nc

        tot["num_facturas"] += num
        tot["subtotal"] += subtotal
        tot["impuestos"] += impuestos
        tot["total_facturado"] += total
        tot["notas_credito"] += nc
        tot["venta_neta"] += neta

        resultados.append({
            "periodo": periodo.date().isoformat(),
            "num_facturas": num,
            "subtotal": _money(subtotal),
            "impuestos": _money(impuestos),
            "total_facturado": _money(total),
            "notas_credito": _money(nc),
            "venta_neta": _money(neta),
            "ticket_promedio": _money(total / num) if num else _money(CERO),
        })

    totales = {k: (v if k == "num_facturas" else _money(v)) for k, v in tot.items()}
    totales["ticket_promedio"] = (
        _money(tot["total_facturado"] / tot["num_facturas"]) if tot["num_facturas"] else _money(CERO)
    )

    payload = sobre(request, desde, hasta, FILTROS_RV01, totales, resultados)
    payload["agrupado_por"] = nombre_agrup
    # Solo se devuelven periodos con movimiento: 366 filas en cero no informan.
    payload["nota"] = "Solo se listan periodos con movimiento. Venta neta = facturado - notas de credito."
    return payload


def exportar_ventas_por_periodo(request, formato):
    """RV-01 a CSV/PDF, con los mismos filtros que la vista JSON."""
    tenant = get_tenant(request)
    datos = ventas_por_periodo(request)
    ahora = timezone.now()

    filtros = filtros_de(request, ("desde", "hasta") + FILTROS_RV01)
    meta = metadatos(request, tenant, ahora, filtros) + [
        f"Total venta neta: {datos['totales']['venta_neta']}",
        "Venta neta = total facturado - notas de credito (RN-01).",
    ]
    filas = [[fila[c] for c in COLUMNAS_RV01] for fila in datos["resultados"]]

    return entregar(
        formato, "Ventas por periodo - NovaERP", meta, COLUMNAS_RV01, filas,
        nombre_archivo(tenant, "ventas_por_periodo", ahora, formato),
    )


# --------------------------------------------------------------------- RV-02

FILTROS_RV02 = ("vendedor_id", "orden", "limit")

COLUMNAS_RV02 = [
    "cliente_id", "razon_social", "num_facturas", "total_facturado",
    "notas_credito", "venta_neta", "participacion_pct", "ultima_compra",
]

ORDENES_RV02 = ("monto", "volumen")


def ranking_clientes(request):
    """RV-02: quien compra mas en el rango.

    `totales` agrega TODOS los clientes del rango, no solo los del top N: si no,
    las participaciones no sumarian 100 y el total del reporte cambiaria con el
    valor de ?limit=.
    """
    tenant = get_tenant(request)
    desde, hasta, inicio, fin = rango(request)
    orden = _orden(request, ORDENES_RV02, "monto")
    top = limite(request)

    facturas = (
        facturas_del_rango(tenant, inicio, fin, request)
        .values("cliente_id")
        .annotate(
            num_facturas=Count("id"),
            total=Sum("total"),
            ultima_compra=Max("fecha_emision"),
        )
    )
    notas = (
        notas_credito_del_rango(tenant, inicio, fin, request)
        .values("factura__cliente_id")
        .annotate(monto=Sum("monto"))
    )
    nc_por_cliente = {n["factura__cliente_id"]: n["monto"] or CERO for n in notas}

    filas = {f["cliente_id"]: f for f in facturas}
    # Un cliente puede aparecer solo por una devolucion de una compra anterior.
    for cliente_id in nc_por_cliente:
        filas.setdefault(cliente_id, {"cliente_id": cliente_id, "num_facturas": 0})

    nombres = dict(
        Cliente.objects.filter(tenant=tenant, id__in=list(filas))
        .values_list("id", "razon_social")
    )

    tot_facturado = sum((f.get("total") or CERO for f in filas.values()), CERO)
    tot_nc = sum(nc_por_cliente.values(), CERO)
    tot_neta = tot_facturado - tot_nc
    tot_facturas = sum(f.get("num_facturas") or 0 for f in filas.values())

    resultados = []
    for cliente_id, f in filas.items():
        total = f.get("total") or CERO
        nc = nc_por_cliente.get(cliente_id, CERO)
        neta = total - nc
        ultima = f.get("ultima_compra")
        resultados.append({
            "cliente_id": str(cliente_id),
            "razon_social": nombres.get(cliente_id, ""),
            "num_facturas": f.get("num_facturas") or 0,
            "total_facturado": _money(total),
            "notas_credito": _money(nc),
            "venta_neta": _money(neta),
            "participacion_pct": _participacion(neta, tot_neta),
            "ultima_compra": ultima.isoformat() if ultima else None,
            "_orden": neta if orden == "monto" else Decimal(f.get("num_facturas") or 0),
        })

    resultados.sort(key=lambda r: r["_orden"], reverse=True)
    for r in resultados:
        del r["_orden"]

    totales = {
        "num_clientes": len(filas),
        "num_facturas": tot_facturas,
        "total_facturado": _money(tot_facturado),
        "notas_credito": _money(tot_nc),
        "venta_neta": _money(tot_neta),
    }
    payload = sobre(request, desde, hasta, FILTROS_RV02, totales, resultados[:top])
    payload["orden"] = orden
    payload["limit"] = top
    payload["nota"] = (
        "Los totales agregan todos los clientes del rango, no solo los del top."
    )
    return payload


def exportar_ranking_clientes(request, formato):
    tenant = get_tenant(request)
    datos = ranking_clientes(request)
    ahora = timezone.now()

    filtros = filtros_de(request, ("desde", "hasta", "cliente_id") + FILTROS_RV02)
    meta = metadatos(request, tenant, ahora, filtros) + [
        f"Clientes en el rango: {datos['totales']['num_clientes']}"
        f" · Venta neta total: {datos['totales']['venta_neta']}",
        f"Se listan los {datos['limit']} primeros por {datos['orden']}.",
    ]
    filas = [[fila[c] for c in COLUMNAS_RV02] for fila in datos["resultados"]]

    return entregar(
        formato, "Ranking de clientes - NovaERP", meta, COLUMNAS_RV02, filas,
        nombre_archivo(tenant, "ranking_clientes", ahora, formato),
    )


# --------------------------------------------------------------------- RV-03

FILTROS_RV03 = ("cliente_id", "vendedor_id", "orden", "limit")

COLUMNAS_RV03 = [
    "producto_id", "sku", "nombre", "cantidad", "importe",
    "participacion_pct", "num_facturas",
]

ORDENES_RV03 = ("monto", "cantidad")

# RN-03: la nota de credito no tiene lineas, asi que una devolucion no se puede
# imputar a un producto. Este ranking es BRUTO y lo dice en la respuesta y en el
# archivo exportado, en vez de aparentar una precision que no tiene.
NOTA_RV03 = (
    "Importe facturado bruto: las notas de credito no tienen desglose por linea "
    "y no se descuentan de este ranking (RN-03)."
)


def ranking_productos(request):
    """RV-03: que se vende mas en el rango, por importe o por cantidad."""
    tenant = get_tenant(request)
    desde, hasta, inicio, fin = rango(request)
    orden = _orden(request, ORDENES_RV03, "monto")
    top = limite(request)

    # Se filtra por la factura (no por la linea): el rango, el estado y el
    # aislamiento por tenant viven ahi.
    qs = FacturaLinea.objects.filter(
        factura__tenant=tenant,
        factura__estado__in=ESTADOS_VENTA,
        factura__fecha_emision__gte=inicio,
        factura__fecha_emision__lt=fin,
    )
    cliente = cliente_filtro(request)
    if cliente:
        qs = qs.filter(factura__cliente_id=cliente)
    vendedor = vendedor_efectivo(request)  # RN-04
    if vendedor:
        qs = qs.filter(factura__vendedor_id=vendedor)

    agregado = qs.values("pedido_linea__producto_id").annotate(
        cantidad=Sum("cantidad"),
        importe=Sum("importe"),
        num_facturas=Count("factura_id", distinct=True),
    )
    filas = list(agregado)

    productos = {
        p["id"]: p
        for p in Producto.objects.filter(
            tenant=tenant, id__in=[f["pedido_linea__producto_id"] for f in filas]
        ).values("id", "sku", "nombre")
    }

    tot_importe = sum((f["importe"] or CERO for f in filas), CERO)
    tot_cantidad = sum((f["cantidad"] or CERO for f in filas), CERO)

    resultados = []
    for f in filas:
        pid = f["pedido_linea__producto_id"]
        prod = productos.get(pid, {})
        importe = f["importe"] or CERO
        resultados.append({
            "producto_id": str(pid),
            "sku": prod.get("sku", ""),
            "nombre": prod.get("nombre", ""),
            "cantidad": str(f["cantidad"] or CERO),
            "importe": _money(importe),
            "participacion_pct": _participacion(importe, tot_importe),
            "num_facturas": f["num_facturas"],
            "_orden": importe if orden == "monto" else (f["cantidad"] or CERO),
        })

    resultados.sort(key=lambda r: r["_orden"], reverse=True)
    for r in resultados:
        del r["_orden"]

    totales = {
        "num_productos": len(filas),
        "cantidad": str(tot_cantidad),
        "importe": _money(tot_importe),
    }
    payload = sobre(request, desde, hasta, FILTROS_RV03, totales, resultados[:top])
    payload["orden"] = orden
    payload["limit"] = top
    payload["nota"] = NOTA_RV03
    return payload


def exportar_ranking_productos(request, formato):
    tenant = get_tenant(request)
    datos = ranking_productos(request)
    ahora = timezone.now()

    filtros = filtros_de(request, ("desde", "hasta") + FILTROS_RV03)
    meta = metadatos(request, tenant, ahora, filtros) + [
        f"Productos en el rango: {datos['totales']['num_productos']}"
        f" · Importe total: {datos['totales']['importe']}",
        f"Se listan los {datos['limit']} primeros por {datos['orden']}.",
        NOTA_RV03,
    ]
    filas = [[fila[c] for c in COLUMNAS_RV03] for fila in datos["resultados"]]

    return entregar(
        formato, "Ranking de productos - NovaERP", meta, COLUMNAS_RV03, filas,
        nombre_archivo(tenant, "ranking_productos", ahora, formato),
    )


# --------------------------------------------------------------------- RV-04

FILTROS_RV04 = ("vendedor_id",)

COLUMNAS_RV04 = ["etapa", "conteo", "monto", "conversion_pct"]

# Estados de pedido que implican que el pedido llego a existir de verdad (no se
# quedo en borrador ni se cancelo).
PEDIDOS_VIVOS = ("confirmado", "pendiente_surtido", "facturado_parcial", "facturado_total")


def _tasa(actual, anterior):
    """Conversion de una etapa del embudo respecto de la anterior."""
    if not anterior:
        return None
    return str((Decimal(actual) * 100 / Decimal(anterior)).quantize(CENTAVO))


def embudo(request):
    """RV-04: cuanto se cae entre oportunidad, cotizacion, pedido y factura.

    Cada etapa cuenta los documentos CREADOS en el rango, no los que hoy estan
    en ese estado: el embudo mide el flujo del periodo, no una foto del
    inventario comercial. Por eso un documento puede contarse en una etapa y no
    en la siguiente aunque acabe convirtiendose mas tarde.
    """
    from ventas.models import Cotizacion, Oportunidad, PedidoVenta

    tenant = get_tenant(request)
    desde, hasta, inicio, fin = rango(request)
    vendedor = vendedor_efectivo(request)  # RN-04

    oportunidades = Oportunidad.objects.filter(
        tenant=tenant, created_at__gte=inicio, created_at__lt=fin
    )
    cotizaciones = Cotizacion.objects.filter(
        tenant=tenant, created_at__gte=inicio, created_at__lt=fin
    )
    pedidos = PedidoVenta.objects.filter(
        tenant=tenant, created_at__gte=inicio, created_at__lt=fin
    )
    facturas = facturas_del_rango(tenant, inicio, fin, request)

    if vendedor:
        # La oportunidad se atribuye por responsable_id; el resto por vendedor_id
        # (columna anadida en este sprint). facturas ya viene acotada.
        oportunidades = oportunidades.filter(responsable_id=vendedor)
        cotizaciones = cotizaciones.filter(vendedor_id=vendedor)
        pedidos = pedidos.filter(vendedor_id=vendedor)

    op = oportunidades.aggregate(n=Count("id"), monto=Sum("valor_estimado"))
    cot = cotizaciones.aggregate(n=Count("id"), monto=Sum("total"))
    ped = pedidos.filter(estado__in=PEDIDOS_VIVOS).aggregate(n=Count("id"), monto=Sum("total"))
    fac = facturas.aggregate(n=Count("id"), monto=Sum("total"))

    etapas_crudas = [
        ("oportunidades", op),
        ("cotizaciones", cot),
        ("pedidos", ped),
        ("facturas", fac),
    ]
    etapas, anterior = [], None
    for nombre, agg in etapas_crudas:
        conteo = agg["n"] or 0
        etapas.append({
            "etapa": nombre,
            "conteo": conteo,
            "monto": _money(agg["monto"] or CERO),
            "conversion_pct": _tasa(conteo, anterior) if anterior is not None else None,
        })
        anterior = conteo

    # Desenlace de las oportunidades del rango y por que se pierden.
    por_estado = {
        r["estado"]: r["n"]
        for r in oportunidades.values("estado").annotate(n=Count("id"))
    }
    ganadas, perdidas = por_estado.get("ganada", 0), por_estado.get("perdida", 0)
    cerradas = ganadas + perdidas

    por_estado_cot = {
        r["estado"]: r["n"]
        for r in cotizaciones.values("estado").annotate(n=Count("id"))
    }
    aprobadas = por_estado_cot.get("aprobada", 0)
    rechazadas = por_estado_cot.get("rechazada", 0)
    resueltas = aprobadas + rechazadas

    # 'vencida' es derivado de vigente_hasta (RF-35), no un estado almacenado.
    vencidas = cotizaciones.filter(
        estado__in=("borrador", "pendiente_aprobacion"), vigente_hasta__lt=hasta
    ).count()

    motivos = [
        {"motivo": r["motivo_perdida"] or "sin motivo", "conteo": r["n"]}
        for r in oportunidades.filter(estado="perdida")
        .values("motivo_perdida").annotate(n=Count("id")).order_by("-n")
    ]

    totales = {
        "oportunidades_abiertas": por_estado.get("abierta", 0),
        "oportunidades_ganadas": ganadas,
        "oportunidades_perdidas": perdidas,
        "tasa_cierre_ganado_pct": _tasa(ganadas, cerradas),
        "cotizaciones_aprobadas": aprobadas,
        "cotizaciones_rechazadas": rechazadas,
        "cotizaciones_vencidas": vencidas,
        "tasa_aprobacion_pct": _tasa(aprobadas, resueltas),
        "pedidos_cancelados": pedidos.filter(estado="cancelado").count(),
        "venta_facturada": _money(fac["monto"] or CERO),
    }

    payload = sobre(request, desde, hasta, FILTROS_RV04, totales, etapas)
    payload["motivos_perdida"] = motivos
    payload["nota"] = (
        "Cada etapa cuenta los documentos creados en el rango; la conversion es "
        "respecto de la etapa anterior, no un seguimiento documento a documento."
    )
    return payload


def exportar_embudo(request, formato):
    tenant = get_tenant(request)
    datos = embudo(request)
    ahora = timezone.now()

    filtros = filtros_de(request, ("desde", "hasta") + FILTROS_RV04)
    t = datos["totales"]
    meta = metadatos(request, tenant, ahora, filtros) + [
        f"Oportunidades ganadas: {t['oportunidades_ganadas']}"
        f" · perdidas: {t['oportunidades_perdidas']}"
        f" · tasa de cierre: {t['tasa_cierre_ganado_pct']}",
        f"Cotizaciones aprobadas: {t['cotizaciones_aprobadas']}"
        f" · rechazadas: {t['cotizaciones_rechazadas']}"
        f" · vencidas: {t['cotizaciones_vencidas']}",
        "Motivos de perdida: " + (
            ", ".join(f"{m['motivo']} ({m['conteo']})" for m in datos["motivos_perdida"]) or "ninguno"
        ),
    ]
    filas = [[fila[c] for c in COLUMNAS_RV04] for fila in datos["resultados"]]

    return entregar(
        formato, "Embudo comercial - NovaERP", meta, COLUMNAS_RV04, filas,
        nombre_archivo(tenant, "embudo", ahora, formato),
    )


# --------------------------------------------------------------------- RV-05

FILTROS_RV05 = ("corte", "cliente_id")

COLUMNAS_RV05 = [
    "cliente_id", "razon_social", "num_facturas", "saldo_total",
    "dias_0_30", "dias_31_60", "dias_61_90", "dias_90_mas",
]

CUBOS = ("dias_0_30", "dias_31_60", "dias_61_90", "dias_90_mas")

# D2: no existe fecha_vencimiento en cuenta_por_cobrar ni dias_credito en
# cliente, asi que la antiguedad cuenta dias DESDE LA EMISION, no dias vencidos.
# Se declara en la respuesta y en el archivo para que nadie lo lea como mora.
CRITERIO_RV05 = (
    "Antiguedad por dias transcurridos desde la emision de la factura, NO por "
    "dias vencidos: el esquema no modela fecha de vencimiento ni dias de credito."
)


def _cubo(dias):
    if dias <= 30:
        return "dias_0_30"
    if dias <= 60:
        return "dias_31_60"
    if dias <= 90:
        return "dias_61_90"
    return "dias_90_mas"


def cartera(request):
    """RV-05: antiguedad de saldos por cobrar a una fecha de corte.

    El saldo se reconstruye A LA FECHA DE CORTE (monto original menos los abonos
    registrados hasta esa fecha) en vez de usar cuenta_por_cobrar.saldo, que es
    el saldo de hoy: si no, pedir el corte del mes pasado devolveria cifras
    contaminadas por los cobros posteriores.
    """
    from django.db.models import Q
    from django.db.models.functions import Coalesce

    from finanzas.models import CuentaPorCobrar

    tenant = get_tenant(request)

    valor_corte = request.GET.get("corte")
    corte = _fecha(valor_corte, "corte") if valor_corte else timezone.localdate()
    fin = timezone.make_aware(
        datetime.datetime.combine(corte + datetime.timedelta(days=1), datetime.time.min),
        timezone.get_current_timezone(),
    )

    qs = CuentaPorCobrar.objects.filter(tenant=tenant, created_at__lt=fin)
    cliente = cliente_filtro(request)
    if cliente:
        qs = qs.filter(cliente_id=cliente)
    vendedor = vendedor_efectivo(request)  # RN-04
    if vendedor:
        qs = qs.filter(factura__vendedor_id=vendedor)

    qs = qs.annotate(
        abonado=Coalesce(
            Sum(
                "finanzas_abono_cxc_cxc_set__monto",
                filter=Q(finanzas_abono_cxc_cxc_set__created_at__lt=fin),
            ),
            CERO,
        )
    ).values("id", "cliente_id", "factura__folio", "monto_original", "abonado", "created_at")

    por_cliente, detalle = {}, []
    totales_cubos = {c: CERO for c in CUBOS}
    saldo_total = CERO

    for fila in qs:
        saldo = (fila["monto_original"] or CERO) - (fila["abonado"] or CERO)
        if saldo <= CERO:
            continue  # saldada a la fecha de corte
        dias = (corte - timezone.localtime(fila["created_at"]).date()).days
        cubo = _cubo(dias)

        c = por_cliente.setdefault(
            fila["cliente_id"],
            {"num_facturas": 0, "saldo_total": CERO, **{k: CERO for k in CUBOS}},
        )
        c["num_facturas"] += 1
        c["saldo_total"] += saldo
        c[cubo] += saldo
        totales_cubos[cubo] += saldo
        saldo_total += saldo

        detalle.append({
            "cxc_id": str(fila["id"]),
            "cliente_id": str(fila["cliente_id"]),
            "factura_folio": fila["factura__folio"],
            "fecha_factura": timezone.localtime(fila["created_at"]).date().isoformat(),
            "dias": dias,
            "cubo": cubo,
            "monto_original": _money(fila["monto_original"]),
            "abonado": _money(fila["abonado"]),
            "saldo": _money(saldo),
        })

    nombres = dict(
        Cliente.objects.filter(tenant=tenant, id__in=list(por_cliente))
        .values_list("id", "razon_social")
    )
    resultados = sorted(
        (
            {
                "cliente_id": str(cid),
                "razon_social": nombres.get(cid, ""),
                "num_facturas": c["num_facturas"],
                "saldo_total": _money(c["saldo_total"]),
                **{k: _money(c[k]) for k in CUBOS},
                "_orden": c["saldo_total"],
            }
            for cid, c in por_cliente.items()
        ),
        key=lambda r: r["_orden"], reverse=True,
    )
    for r in resultados:
        del r["_orden"]

    totales = {
        "num_clientes": len(por_cliente),
        "num_facturas": len(detalle),
        "saldo_total": _money(saldo_total),
        **{k: _money(v) for k, v in totales_cubos.items()},
    }

    payload = {
        "corte": corte.isoformat(),
        "filtros": {c: request.GET.get(c) for c in FILTROS_RV05},
        "alcance_vendedor": vendedor,
        "criterio": CRITERIO_RV05,
        "totales": totales,
        "resultados": resultados,
        "generado_en": timezone.now().isoformat(),
    }
    if (request.GET.get("detalle") or "").strip().lower() in ("1", "true", "si", "sí"):
        detalle.sort(key=lambda d: d["dias"], reverse=True)
        payload["detalle"] = paginate(detalle, request, lambda d: d)
    return payload


def exportar_cartera(request, formato):
    tenant = get_tenant(request)
    datos = cartera(request)
    ahora = timezone.now()

    filtros = filtros_de(request, FILTROS_RV05)
    t = datos["totales"]
    meta = metadatos(request, tenant, ahora, filtros) + [
        f"Corte: {datos['corte']} · Saldo total: {t['saldo_total']}"
        f" · {t['num_facturas']} facturas de {t['num_clientes']} clientes",
        CRITERIO_RV05,
    ]
    filas = [[fila[c] for c in COLUMNAS_RV05] for fila in datos["resultados"]]

    return entregar(
        formato, "Cartera por antiguedad - NovaERP", meta, COLUMNAS_RV05, filas,
        nombre_archivo(tenant, "cartera", ahora, formato),
    )


# --------------------------------------------------------------------- RV-06

FILTROS_RV06 = ("vendedor_id",)

COLUMNAS_RV06 = [
    "vendedor_id", "nombre", "oportunidades_ganadas", "oportunidades_perdidas",
    "tasa_conversion_pct", "cotizaciones_emitidas", "cotizaciones_aprobadas",
    "pedidos_confirmados", "num_facturas", "venta_neta",
]

# Clave del cubo de documentos sin atribucion. Existe porque el historico
# anterior a este sprint no tiene vendedor_id y el backfill solo alcanza a lo
# que venia de una oportunidad: se muestra aparte, nunca repartido entre los
# vendedores reales.
SIN_ASIGNAR = "sin_asignar"


def _clave(valor):
    return str(valor) if valor else SIN_ASIGNAR


def desempeno_vendedores(request):
    """RV-06: actividad y cierre por vendedor en el rango.

    Cruza cuatro fuentes por atribucion: oportunidades (responsable_id) y
    cotizaciones / pedidos / facturas (vendedor_id). Un vendedor aparece si
    tiene actividad en CUALQUIERA de ellas.
    """
    from django.db.models import Q

    from ventas.models import Cotizacion, Oportunidad, PedidoVenta

    tenant = get_tenant(request)
    desde, hasta, inicio, fin = rango(request)
    vendedor = vendedor_efectivo(request)  # RN-04

    oportunidades = Oportunidad.objects.filter(
        tenant=tenant, created_at__gte=inicio, created_at__lt=fin
    )
    cotizaciones = Cotizacion.objects.filter(
        tenant=tenant, created_at__gte=inicio, created_at__lt=fin
    )
    pedidos = PedidoVenta.objects.filter(
        tenant=tenant, created_at__gte=inicio, created_at__lt=fin
    )
    if vendedor:
        oportunidades = oportunidades.filter(responsable_id=vendedor)
        cotizaciones = cotizaciones.filter(vendedor_id=vendedor)
        pedidos = pedidos.filter(vendedor_id=vendedor)

    filas = {}

    def fila(clave):
        return filas.setdefault(clave, {
            "oportunidades_ganadas": 0, "oportunidades_perdidas": 0,
            "cotizaciones_emitidas": 0, "cotizaciones_aprobadas": 0,
            "pedidos_confirmados": 0, "num_facturas": 0,
            "facturado": CERO, "notas_credito": CERO,
        })

    for r in oportunidades.values("responsable_id").annotate(
        ganadas=Count("id", filter=Q(estado="ganada")),
        perdidas=Count("id", filter=Q(estado="perdida")),
    ):
        f = fila(_clave(r["responsable_id"]))
        f["oportunidades_ganadas"] = r["ganadas"]
        f["oportunidades_perdidas"] = r["perdidas"]

    for r in cotizaciones.values("vendedor_id").annotate(
        emitidas=Count("id"),
        aprobadas=Count("id", filter=Q(estado="aprobada")),
    ):
        f = fila(_clave(r["vendedor_id"]))
        f["cotizaciones_emitidas"] = r["emitidas"]
        f["cotizaciones_aprobadas"] = r["aprobadas"]

    for r in pedidos.values("vendedor_id").annotate(
        confirmados=Count("id", filter=Q(estado__in=PEDIDOS_VIVOS)),
    ):
        fila(_clave(r["vendedor_id"]))["pedidos_confirmados"] = r["confirmados"]

    for r in facturas_del_rango(tenant, inicio, fin, request).values("vendedor_id").annotate(
        num=Count("id"), total=Sum("total"),
    ):
        f = fila(_clave(r["vendedor_id"]))
        f["num_facturas"] = r["num"]
        f["facturado"] = r["total"] or CERO

    for r in notas_credito_del_rango(tenant, inicio, fin, request).values(
        "factura__vendedor_id"
    ).annotate(monto=Sum("monto")):
        fila(_clave(r["factura__vendedor_id"]))["notas_credito"] = r["monto"] or CERO

    nombres = dict(
        Usuario.objects.filter(tenant=tenant, id__in=[k for k in filas if k != SIN_ASIGNAR])
        .values_list("id", "nombre_completo")
    )
    nombres = {str(k): v for k, v in nombres.items()}

    resultados, tot = [], {
        "oportunidades_ganadas": 0, "oportunidades_perdidas": 0,
        "cotizaciones_emitidas": 0, "cotizaciones_aprobadas": 0,
        "pedidos_confirmados": 0, "num_facturas": 0, "venta_neta": CERO,
    }
    for clave, f in filas.items():
        neta = f["facturado"] - f["notas_credito"]
        cerradas = f["oportunidades_ganadas"] + f["oportunidades_perdidas"]
        for k in tot:
            if k != "venta_neta":
                tot[k] += f[k]
        tot["venta_neta"] += neta

        resultados.append({
            "vendedor_id": None if clave == SIN_ASIGNAR else clave,
            "nombre": nombres.get(clave, "Sin asignar" if clave == SIN_ASIGNAR else ""),
            "oportunidades_ganadas": f["oportunidades_ganadas"],
            "oportunidades_perdidas": f["oportunidades_perdidas"],
            "tasa_conversion_pct": _tasa(f["oportunidades_ganadas"], cerradas),
            "cotizaciones_emitidas": f["cotizaciones_emitidas"],
            "cotizaciones_aprobadas": f["cotizaciones_aprobadas"],
            "pedidos_confirmados": f["pedidos_confirmados"],
            "num_facturas": f["num_facturas"],
            "facturado": _money(f["facturado"]),
            "notas_credito": _money(f["notas_credito"]),
            "venta_neta": _money(neta),
            "_orden": neta,
        })

    resultados.sort(key=lambda r: r["_orden"], reverse=True)
    for r in resultados:
        del r["_orden"]

    totales = {k: (_money(v) if k == "venta_neta" else v) for k, v in tot.items()}
    totales["num_vendedores"] = len([k for k in filas if k != SIN_ASIGNAR])

    payload = sobre(request, desde, hasta, FILTROS_RV06, totales, resultados)
    payload["nota"] = (
        "Los documentos sin atribucion (historico previo a vendedor_id) se "
        "agrupan en una fila 'Sin asignar'; nunca se reparten entre vendedores."
    )
    return payload


def exportar_desempeno_vendedores(request, formato):
    tenant = get_tenant(request)
    datos = desempeno_vendedores(request)
    ahora = timezone.now()

    filtros = filtros_de(request, ("desde", "hasta") + FILTROS_RV06)
    t = datos["totales"]
    meta = metadatos(request, tenant, ahora, filtros) + [
        f"Vendedores con actividad: {t['num_vendedores']}"
        f" · Venta neta total: {t['venta_neta']}",
        "Las filas 'Sin asignar' son documentos sin vendedor atribuido.",
    ]
    filas = [[fila[c] for c in COLUMNAS_RV06] for fila in datos["resultados"]]

    return entregar(
        formato, "Desempeno de vendedores - NovaERP", meta, COLUMNAS_RV06, filas,
        nombre_archivo(tenant, "vendedores", ahora, formato),
    )
