import uuid

from django.utils import timezone

from compras.models import Proveedor
from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError


def serialize_proveedor(proveedor):
    return {
        "id": str(proveedor.id),
        "rfc_o_id_fiscal": proveedor.rfc_o_id_fiscal,
        "razon_social": proveedor.razon_social,
        "correo": proveedor.correo,
        "telefono": proveedor.telefono,
        "activo": proveedor.activo,
    }


def crear_proveedor(data, request):
    rfc = (data.get("rfc_o_id_fiscal") or "").strip()
    razon_social = (data.get("razon_social") or "").strip()
    if not rfc:
        raise BusinessRuleError("rfc_o_id_fiscal es obligatorio.", campo="rfc_o_id_fiscal")
    if not razon_social:
        raise BusinessRuleError("razon_social es obligatorio.", campo="razon_social")

    tenant = get_tenant(request)
    if Proveedor.objects.filter(tenant=tenant, rfc_o_id_fiscal=rfc).exists():
        raise BusinessRuleError("RFC ya registrado en este tenant.", campo="rfc_o_id_fiscal")

    now = timezone.now()
    with audit_context(request, tenant_id=tenant.id):
        proveedor = Proveedor.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            rfc_o_id_fiscal=rfc,
            razon_social=razon_social,
            correo=(data.get("correo") or None),
            telefono=(data.get("telefono") or None),
            activo=True,
            created_at=now,
            updated_at=now,
        )
    return proveedor


def editar_proveedor(proveedor, data, request):
    if "rfc_o_id_fiscal" in data:
        nuevo_rfc = (data["rfc_o_id_fiscal"] or "").strip()
        if not nuevo_rfc:
            raise BusinessRuleError(
                "rfc_o_id_fiscal no puede quedar vacio.", campo="rfc_o_id_fiscal"
            )
        if nuevo_rfc != proveedor.rfc_o_id_fiscal and Proveedor.objects.filter(
            tenant=proveedor.tenant, rfc_o_id_fiscal=nuevo_rfc
        ).exists():
            raise BusinessRuleError("RFC ya registrado en este tenant.", campo="rfc_o_id_fiscal")
        proveedor.rfc_o_id_fiscal = nuevo_rfc

    if "razon_social" in data:
        razon_social = (data["razon_social"] or "").strip()
        if not razon_social:
            raise BusinessRuleError("razon_social no puede quedar vacio.", campo="razon_social")
        proveedor.razon_social = razon_social

    if "correo" in data:
        proveedor.correo = data["correo"] or None

    if "telefono" in data:
        proveedor.telefono = data["telefono"] or None

    with audit_context(request, tenant_id=proveedor.tenant_id):
        proveedor.save()
    return proveedor


def dar_de_baja_proveedor(proveedor, request):
    proveedor.activo = False
    with audit_context(request, tenant_id=proveedor.tenant_id):
        proveedor.save(update_fields=["activo"])
    return proveedor
