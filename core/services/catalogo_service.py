from core.models import DominioReservado, Modulo, ModuloDependencia, PlanComercial, PlanModulo

# Catalogo maestro de plataforma: planes comerciales, modulos y el grafo de
# dependencias entre ellos. Son tablas de semilla (se administran por SQL, no
# por API), pero el portal del SysAdmin las necesita para pintar el alta de
# tenant (RF-01) y el editor de modulos (RF-03) sin duplicar el seed en el
# frontend.
#
# Solo lectura y sin estado por tenant: lo que un tenant concreto tiene ACTIVO
# vive en core.tenant_modulo y lo devuelve el detalle de tenant (RF-02/CA05).

# RF-01/RN06: los modulos de fase 0 son el nucleo (identidad, usuarios, RBAC,
# auth, auditoria, seguridad, reporteria). Estan en todos los planes y nunca
# pueden desactivarse.
NUCLEO_FASE = 0


def mapa_dependencias():
    """codigo_modulo -> {codigos de los que depende} y su inverso (quien depende
    de cada modulo). Fuente unica del grafo de RF-03/RN05/RN07: lo consumen la
    validacion de activacion/cascada y este catalogo, para que el frontend
    anticipe la misma cascada que el backend va a aplicar."""
    deps, inverso = {}, {}
    for modulo, depende_de in ModuloDependencia.objects.values_list(
        "modulo__codigo", "depende_de__codigo"
    ):
        deps.setdefault(modulo, set()).add(depende_de)
        inverso.setdefault(depende_de, set()).add(modulo)
    return deps, inverso


def catalogos():
    """Catalogo completo en una sola respuesta: cuatro consultas, sin N+1.

    Se devuelven TODOS los planes, incluidos los que tienen activo=false: un
    tenant puede seguir contratado en un plan retirado y el detalle tiene que
    poder nombrarlo. El selector de alta debe filtrar por `activo`, que es lo
    que valida crear_tenant (un plan no vigente responde 422).

    Un modulo con `planes: []` no esta incluido en ningun plan y por tanto no
    puede activarse en ningun tenant: hoy es el caso de la fase 2 (RRHH,
    Finanzas, Proyectos, BPM, Reglas, BI), fuera de alcance.
    """
    deps, inverso = mapa_dependencias()

    # Una sola consulta para las dos direcciones de la relacion plan<->modulo.
    por_plan, por_modulo = {}, {}
    for plan, modulo in PlanModulo.objects.values_list("plan__codigo", "modulo__codigo"):
        por_plan.setdefault(plan, set()).add(modulo)
        por_modulo.setdefault(modulo, set()).add(plan)

    planes = [
        {
            "codigo": plan.codigo,
            "nombre": plan.nombre,
            "licencias_max": plan.licencias_max,
            "activo": plan.activo,
            "modulos": sorted(por_plan.get(plan.codigo, ())),
        }
        # Escalera natural del mas chico al mas grande, no alfabetica.
        for plan in PlanComercial.objects.order_by("licencias_max", "codigo")
    ]

    modulos = [
        {
            "codigo": modulo.codigo,
            "nombre": modulo.nombre,
            "fase": modulo.fase,
            # El frontend bloquea su checkbox: RF-03/RN03 lo rechaza con 422.
            "nucleo": modulo.fase == NUCLEO_FASE,
            "depende_de": sorted(deps.get(modulo.codigo, ())),
            "requerido_por": sorted(inverso.get(modulo.codigo, ())),
            "planes": sorted(por_modulo.get(modulo.codigo, ())),
        }
        # Mismo orden que los modulos activos del detalle de tenant.
        for modulo in Modulo.objects.order_by("fase", "nombre")
    ]

    return {
        "planes": planes,
        "modulos": modulos,
        # RF-01/RN07/CA10: para validar el slug en el formulario antes de enviarlo.
        "dominios_reservados": sorted(
            DominioReservado.objects.values_list("palabra", flat=True)
        ),
    }
