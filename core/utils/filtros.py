"""Validacion de los parametros de consulta antes de que lleguen a la base.

Postgres es estricto con sus tipos y Django no cuela el valor de un filtro por
ninguna validacion previa: `?estado=basura` sobre una columna ENUM aborta la
consulta con DataError y `?producto_id=basura` sobre un uuid con ValidationError.
En los dos casos la vista respondia 500 a una peticion que solo estaba mal
escrita.

Aqui se traduce eso a ParametroInvalido (400) senalando el campo, que es lo que
el contrato ya prometia para "parametro de consulta mal formado". El 400 se
distingue del 422: el 422 es una peticion bien formada que rompe una regla de
negocio.

El tipo se deduce del propio campo del modelo, no de una lista mantenida a mano:
asi un filtro nuevo en `filter_fields` queda validado sin tocar este modulo.
"""

import datetime
import uuid as uuid_lib

from django.core.exceptions import FieldDoesNotExist
from django.db import models

from core.utils.errors import ParametroInvalido


def uuid_o_400(valor, campo):
    try:
        uuid_lib.UUID(str(valor))
    except (ValueError, AttributeError, TypeError):
        raise ParametroInvalido(f"{campo} debe ser un UUID valido.", campo=campo)
    return valor


def fecha_o_400(valor, campo):
    """Valida y devuelve el valor TAL CUAL, sin convertirlo a date: las consultas
    ya comparan el string ISO contra la columna y cambiar el tipo aqui cambiaria
    la semantica de los rangos sobre timestamptz."""
    try:
        datetime.date.fromisoformat(str(valor))
    except (ValueError, TypeError):
        raise ParametroInvalido(
            f"{campo} debe ser una fecha ISO (AAAA-MM-DD).", campo=campo
        )
    return valor


def entero_o_400(valor, campo):
    try:
        int(valor)
    except (ValueError, TypeError):
        raise ParametroInvalido(f"{campo} debe ser un entero.", campo=campo)
    return valor


def opcion_o_400(valor, campo, opciones):
    """Las opciones se enumeran en el orden en que vienen (el del ENUM en la base
    o el declarado en el mapa de ordenamiento), no alfabetizadas: es el mismo
    orden que ve el lector del contrato."""
    if valor not in opciones:
        raise ParametroInvalido(
            f"{campo} debe ser uno de: {', '.join(opciones)}.", campo=campo
        )
    return valor


def clave_orden(request, mapa, default, param="orden"):
    """Valida ?orden= contra el mapa de campos permitidos y devuelve su clave.

    Antes un valor desconocido caia al orden por defecto en silencio: el cliente
    creia estar ordenando por algo que la API ignoraba. El mapa sigue siendo la
    unica fuente de campos ordenables (nunca un ORDER BY del cliente), pero ahora
    pedir uno que no existe es 400, igual que en los reportes de ventas.
    """
    valor = (request.GET.get(param) or default).strip()
    return opcion_o_400(valor, param, list(mapa))


def _campo_de(model, nombre):
    """Resuelve el campo del modelo para un nombre de filtro. Acepta la forma
    `<fk>_id` (que es como se declaran los filtros) devolviendo la FK. Devuelve
    None si no se puede resolver: ahi no hay nada que validar."""
    try:
        return model._meta.get_field(nombre)
    except FieldDoesNotExist:
        pass
    if nombre.endswith("_id"):
        try:
            return model._meta.get_field(nombre[:-3])
        except FieldDoesNotExist:
            return None
    return None


def validar_filtro(model, nombre, valor):
    """Valida el valor de un filtro segun el tipo del campo destino. Un campo
    que no se puede resolver, o de texto libre, pasa sin tocar."""
    campo = _campo_de(model, nombre)
    if campo is None:
        return valor

    if isinstance(campo, models.ForeignKey):
        campo = campo.target_field

    if getattr(campo, "choices", None):
        return opcion_o_400(valor, nombre, [c[0] for c in campo.choices])
    if isinstance(campo, models.UUIDField):
        return uuid_o_400(valor, nombre)
    if isinstance(campo, models.DateField):  # cubre DateTimeField
        return fecha_o_400(valor, nombre)
    if isinstance(campo, models.IntegerField):  # cubre BigInteger/SmallInteger
        return entero_o_400(valor, nombre)
    return valor


def filtros_validados(model, request, campos):
    """{campo: valor} de los `campos` presentes en la query string, ya validados.
    Pensado para sustituir el bucle `for campo in (...): request.GET.get(campo)`
    de los servicios que filtran a mano."""
    validos = {}
    for campo in campos:
        valor = request.GET.get(campo)
        if valor:
            validos[campo] = validar_filtro(model, campo, valor)
    return validos


def rango_validado(request, campo_desde="desde", campo_hasta="hasta"):
    """Par (desde, hasta) de la query string, validados como fecha ISO. Devuelve
    None en el que no venga. No exige que vengan ni que esten ordenados: eso es
    regla de negocio de cada reporte, no forma del parametro."""
    desde = request.GET.get(campo_desde)
    hasta = request.GET.get(campo_hasta)
    return (
        fecha_o_400(desde, campo_desde) if desde else None,
        fecha_o_400(hasta, campo_hasta) if hasta else None,
    )
