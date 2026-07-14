import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError
from finanzas.models import CuentaPorCobrar
from ventas.models import Cliente


def _to_decimal(value, campo):
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise BusinessRuleError(f"{campo} debe ser un numero valido.", campo=campo)


def serialize_cliente(cliente):
    return {
        "id": str(cliente.id),
        "rfc_o_id_fiscal": cliente.rfc_o_id_fiscal,
        "razon_social": cliente.razon_social,
        "correo": cliente.correo,
        "telefono": cliente.telefono,
        "limite_credito": str(cliente.limite_credito),
        "activo": cliente.activo,
    }


def crear_cliente(data, request):
    razon_social = (data.get("razon_social") or "").strip()
    if not razon_social:
        raise BusinessRuleError("razon_social es obligatorio.", campo="razon_social")

    rfc = (data.get("rfc_o_id_fiscal") or "").strip() or None
    limite_credito = _to_decimal(data.get("limite_credito"), "limite_credito")
    if limite_credito < 0:
        raise BusinessRuleError("limite_credito debe ser >= 0.", campo="limite_credito")

    tenant = get_tenant(request)
    if rfc and Cliente.objects.filter(tenant=tenant, rfc_o_id_fiscal=rfc).exists():
        raise BusinessRuleError("RFC ya registrado en este tenant.", campo="rfc_o_id_fiscal")

    now = timezone.now()
    with transaction.atomic():
        cliente = Cliente.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            rfc_o_id_fiscal=rfc,
            razon_social=razon_social,
            correo=(data.get("correo") or None),
            telefono=(data.get("telefono") or None),
            limite_credito=limite_credito,
            activo=True,
            created_at=now,
            updated_at=now,
        )
    return cliente


def editar_cliente(cliente, data, request):
    if "rfc_o_id_fiscal" in data:
        nuevo_rfc = (data["rfc_o_id_fiscal"] or "").strip() or None
        if (
            nuevo_rfc
            and nuevo_rfc != cliente.rfc_o_id_fiscal
            and Cliente.objects.filter(tenant=cliente.tenant, rfc_o_id_fiscal=nuevo_rfc).exists()
        ):
            raise BusinessRuleError("RFC ya registrado en este tenant.", campo="rfc_o_id_fiscal")
        cliente.rfc_o_id_fiscal = nuevo_rfc

    if "razon_social" in data:
        razon_social = (data["razon_social"] or "").strip()
        if not razon_social:
            raise BusinessRuleError("razon_social no puede quedar vacio.", campo="razon_social")
        cliente.razon_social = razon_social

    if "correo" in data:
        cliente.correo = data["correo"] or None

    if "telefono" in data:
        cliente.telefono = data["telefono"] or None

    if "limite_credito" in data:
        limite = _to_decimal(data["limite_credito"], "limite_credito")
        if limite < 0:
            raise BusinessRuleError("limite_credito debe ser >= 0.", campo="limite_credito")
        cliente.limite_credito = limite

    cliente.save()
    return cliente


def dar_de_baja_cliente(cliente, request):
    saldo = (
        CuentaPorCobrar.objects.filter(tenant=cliente.tenant, cliente=cliente).aggregate(
            total=Sum("saldo")
        )["total"]
        or Decimal("0")
    )

    if saldo > 0:
        raise BusinessRuleError(
            "Cliente con saldo pendiente en CxC; no puede darse de baja.",
            extra={"saldo_pendiente": str(saldo)},
        )

    cliente.activo = False
    cliente.save(update_fields=["activo"])
    return cliente
