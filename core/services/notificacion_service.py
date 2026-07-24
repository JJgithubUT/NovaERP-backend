import uuid

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from core.models import Notificacion, Usuario

# CA03: se reintenta de forma asincrona. Tras MAX_INTENTOS un envio fallido
# pasa a 'fallida' y deja de reintentarse.
MAX_INTENTOS = 3
ESTADOS_PENDIENTES = ("pendiente", "en_cola_reintento")


def _admins_del_tenant(tenant):
    """RF-25: destinatarios de las alertas de seguridad = los TENANT_ADMIN
    activos del tenant (usuarios con un rol de sistema activo)."""
    return Usuario.objects.filter(
        tenant=tenant,
        estado="activo",
        core_usuario_rol_usuario_set__rol__es_sistema=True,
        core_usuario_rol_usuario_set__rol__activo=True,
    ).distinct()


def encolar(tenant, usuario, asunto, cuerpo, canal="email", now=None):
    """Crea una notificacion en la cola (estado 'pendiente'). El envio real lo
    hace el worker (procesar_pendientes); asi un fallo de envio nunca bloquea la
    operacion que la disparo (RF-25/CA03)."""
    return Notificacion.objects.create(
        id=uuid.uuid4(),
        tenant=tenant,
        usuario=usuario,
        canal=canal,
        asunto=asunto,
        cuerpo=cuerpo,
        estado="pendiente",
        intentos=0,
        created_at=now or timezone.now(),
    )


def notificar_bloqueo(request, tenant, usuario_bloqueado, bloqueado_hasta, now=None):
    """RF-25 + RF-16/RN02: evento de seguridad "cuenta bloqueada". Notifica al
    propio usuario (RF-16) y a los TENANT_ADMIN del tenant (RF-25), sin
    duplicar destinatarios. CA02: el cuerpo nunca incluye credenciales ni
    tokens, solo la descripcion del evento y la referencia a la bitacora. RN02:
    no es desactivable (siempre se encola). Se llama dentro del audit_context
    del login."""
    now = now or timezone.now()
    destinatarios = {usuario_bloqueado.id: usuario_bloqueado}
    for admin in _admins_del_tenant(tenant):
        destinatarios.setdefault(admin.id, admin)

    asunto = "Alerta de seguridad: cuenta bloqueada"
    cuerpo = (
        f"Se bloqueo la cuenta de {usuario_bloqueado.correo} tras varios intentos "
        f"fallidos de inicio de sesion. Podra intentar de nuevo despues de "
        f"{bloqueado_hasta}. Consulte la bitacora de auditoria del evento "
        f"(entidad=usuario, id={usuario_bloqueado.id})."
    )
    for u in destinatarios.values():
        encolar(tenant, u, asunto, cuerpo, now=now)


def procesar_pendientes(limite=None):
    """Worker de entrega (RF-25). Drena la cola: envia por correo (backend de
    Django) las notificaciones 'pendiente'/'en_cola_reintento' y actualiza su
    estado. Un fallo incrementa intentos y reencola (o marca 'fallida' tras
    MAX_INTENTOS), sin propagar la excepcion (CA03). Lo invoca el management
    command `enviar_notificaciones`, que un scheduler (cron) corre cada minuto
    (CA01, cadencia de ops).

    Canal 'email' via django.core.mail (sin librerias nuevas; backend
    configurable). 'webhook'/'slack' quedan pendientes: el esquema de Fase 0 no
    tiene configuracion de canal por tenant (RN01, "si esta configurado").
    """
    qs = Notificacion.objects.filter(estado__in=ESTADOS_PENDIENTES).order_by("created_at")
    if limite:
        qs = qs[:limite]

    enviadas = fallidas = 0
    for n in list(qs):
        try:
            if n.canal != "email":
                raise RuntimeError(f"canal '{n.canal}' no soportado (requiere config de tenant)")
            destino = n.usuario.correo if n.usuario_id else None
            if not destino:
                raise RuntimeError("notificacion sin destinatario")
            send_mail(
                n.asunto, n.cuerpo,
                settings.DEFAULT_FROM_EMAIL, [destino],
                fail_silently=False,
            )
            n.estado = "enviada"
            n.enviada_en = timezone.now()
            n.save(update_fields=["estado", "enviada_en"])
            enviadas += 1
        except Exception:
            n.intentos = (n.intentos or 0) + 1
            n.estado = "fallida" if n.intentos >= MAX_INTENTOS else "en_cola_reintento"
            n.save(update_fields=["estado", "intentos"])
            fallidas += 1

    return enviadas, fallidas
