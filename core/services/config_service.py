import re

from django.utils import timezone

from core.models import ConfigSeguridadTenant
from core.utils.audit import audit_context
from core.utils.auth import get_tenant
from core.utils.errors import BusinessRuleError

# RF-22/RN01/CA01: minimos y maximos que impone la plataforma. Un tenant puede
# endurecer su politica dentro de estos limites, nunca ablandarla por debajo del
# minimo (ej. longitud minima de contrasena nunca < 8).
LIMITES = {
    "politica_password_min_len": (8, 128),
    "jwt_expiracion_horas": (1, 720),      # hasta 30 dias
    "intentos_max_ventana": (3, 20),
    "ventana_minutos": (1, 1440),
    "bloqueo_minutos": (1, 1440),
}

# Alineados con el DEFAULT de core.config_seguridad_tenant, para el caso (raro)
# de un tenant sin fila de configuracion todavia.
DEFAULTS = {
    "politica_password_min_len": 12,
    "politica_password_regex": None,
    "jwt_expiracion_horas": 8,
    "intentos_max_ventana": 5,
    "ventana_minutos": 15,
    "bloqueo_minutos": 30,
}


def serialize_config(cfg):
    if cfg is None:
        return dict(DEFAULTS)
    return {
        "politica_password_min_len": cfg.politica_password_min_len,
        "politica_password_regex": cfg.politica_password_regex,
        "jwt_expiracion_horas": cfg.jwt_expiracion_horas,
        "intentos_max_ventana": cfg.intentos_max_ventana,
        "ventana_minutos": cfg.ventana_minutos,
        "bloqueo_minutos": cfg.bloqueo_minutos,
        "updated_at": cfg.updated_at.isoformat(),
        "limites_plataforma": {k: {"min": v[0], "max": v[1]} for k, v in LIMITES.items()},
    }


def obtener_config(request):
    return ConfigSeguridadTenant.objects.filter(tenant=get_tenant(request)).first()


def actualizar_config(data, request):
    """RF-22: el TENANT_ADMIN endurece su politica dentro de los limites de
    plataforma (RN01/CA01). El cambio NO invalida retroactivamente contrasenas
    ni sesiones activas (RN02/CA02): la politica de contrasena se lee al fijar
    la contrasena y la expiracion al emitir la sesion, no despues. La auditoria
    de valores anterior/nuevo (CA03) la hace el trigger fn_auditar sobre el
    UPDATE, dentro del audit_context."""
    tenant = get_tenant(request)
    cfg, _ = ConfigSeguridadTenant.objects.get_or_create(tenant=tenant, defaults=DEFAULTS)

    for campo, (minimo, maximo) in LIMITES.items():
        if campo not in data:
            continue
        try:
            valor = int(data[campo])
        except (TypeError, ValueError):
            raise BusinessRuleError(f"{campo} debe ser un entero.", campo=campo)
        if not (minimo <= valor <= maximo):
            raise BusinessRuleError(
                f"{campo} debe estar entre {minimo} y {maximo} (limite de plataforma).",
                campo=campo,
                extra={"min": minimo, "max": maximo},
            )
        setattr(cfg, campo, valor)

    if "politica_password_regex" in data:
        regex = data["politica_password_regex"] or None
        if regex:
            try:
                re.compile(regex)
            except re.error:
                raise BusinessRuleError(
                    "politica_password_regex no es una expresion regular valida.",
                    campo="politica_password_regex",
                )
        cfg.politica_password_regex = regex

    with audit_context(request, tenant_id=tenant.id):
        cfg.updated_at = timezone.now()
        cfg.save()

    return cfg
