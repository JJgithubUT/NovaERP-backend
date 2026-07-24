from contextlib import contextmanager

from django.conf import settings
from django.db import connection, transaction

# GUCs que lee el trigger core.fn_auditar para completar los 7 campos
# obligatorios de RF-20/RN01 que no puede deducir de la fila modificada.
_GUC_USUARIO = "app.current_user_id"
_GUC_TENANT = "app.current_tenant_id"
_GUC_IP = "app.current_ip"


def client_ip(request):
    """IP de origen para RF-20/RN01. Por defecto REMOTE_ADDR, que es la unica
    que el cliente no puede falsear. X-Forwarded-For solo se lee si
    settings.USE_X_FORWARDED_FOR esta activo, es decir cuando hay un reverse
    proxy de confianza que reescribe la cabecera; sin el, confiar en ella
    permitiria a cualquiera elegir la IP que queda registrada en la bitacora.
    """
    if settings.USE_X_FORWARDED_FOR:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or ""


def set_audit_user(usuario_id):
    """Re-publica app.current_user_id dentro de una transaccion ya abierta por
    audit_context. Lo necesita el login (RF-16): la transaccion se abre sin
    usuario (aun no esta autenticado) para que las escrituras internas de
    core.intentar_login hereden tenant e IP; una vez que la DB devuelve quien
    es, se fija el actor para que la creacion de la sesion quede atribuida.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config(%s, %s, true)", [_GUC_USUARIO, str(usuario_id or "")]
        )


@contextmanager
def audit_context(request, tenant_id=None, usuario_id=None):
    """Abre una transaccion y publica en ella el contexto que el trigger
    core.fn_auditar necesita para registrar QUIEN, DESDE DONDE y EN QUE TENANT
    se hizo cada escritura (RF-20). Sustituye a transaction.atomic() en los
    servicios:

        with audit_context(request, tenant_id=tenant.id):
            Cliente.objects.create(...)

    Los tres valores se publican con set_config(..., is_local=true), asi que
    viven exactamente lo que dura la transaccion y no se filtran a la
    siguiente peticion que reutilice la conexion del pool. Por eso esto abre
    la transaccion en vez de asumir que ya hay una: fuera de transaccion, en
    autocommit, el set_config se descartaria antes del INSERT auditado.

    tenant_id/usuario_id se pasan explicitamente cuando no salen del JWT: el
    tenant porque el servicio ya lo resolvio con get_tenant() y no vale la
    pena repetir la consulta, y el usuario en el flujo de activacion de RF-05,
    donde el responsable es el propio usuario que aun no tiene sesion.
    """
    if usuario_id is None:
        usuario_id = getattr(request, "usuario_id", None)

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config(%s, %s, true),"
                "       set_config(%s, %s, true),"
                "       set_config(%s, %s, true)",
                [
                    _GUC_USUARIO, str(usuario_id or ""),
                    _GUC_TENANT, str(tenant_id or ""),
                    _GUC_IP, client_ip(request) if request is not None else "",
                ],
            )
        yield
