from decimal import Decimal, InvalidOperation

from compras.models import ConfigAprobacion
from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError


def serialize_config(config):
    if config is None:
        return {"tenant_id": None, "umbral_monto": None}
    return {
        "tenant_id": str(config.tenant_id),
        "umbral_monto": str(config.umbral_monto),
    }


def obtener_config(request):
    tenant = get_tenant(request)
    return ConfigAprobacion.objects.filter(tenant=tenant).first()


def actualizar_umbral(data, request):
    """RF-52: umbral unico por tenant. El ERS menciona diferenciacion por
    categoria de producto, pero el esquema real (compras.config_aprobacion)
    solo tiene una columna umbral_monto por tenant, sin categoria; se
    implementa ese alcance hasta que se amplie el modelo de datos."""
    tenant = get_tenant(request)

    raw = data.get("umbral_monto")
    if raw is None:
        raise BusinessRuleError("umbral_monto es obligatorio.", campo="umbral_monto")
    try:
        umbral = Decimal(str(raw))
    except (InvalidOperation, TypeError):
        raise BusinessRuleError("umbral_monto debe ser un numero valido.", campo="umbral_monto")
    if umbral < 0:
        raise BusinessRuleError("umbral_monto debe ser >= 0.", campo="umbral_monto")

    with audit_context(request, tenant_id=tenant.id):
        config, _ = ConfigAprobacion.objects.update_or_create(
            tenant=tenant, defaults={"umbral_monto": umbral}
        )
    return config
