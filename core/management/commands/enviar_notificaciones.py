from django.core.management.base import BaseCommand
from django.db import connection

from core.services import notificacion_service


class Command(BaseCommand):
    help = (
        "RF-25: worker de entrega de notificaciones. Envia las notificaciones "
        "pendientes/en reintento por correo y actualiza su estado. Pensado para "
        "ejecutarse por cron cada minuto (CA01)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limite", type=int, default=None,
            help="Numero maximo de notificaciones a procesar en esta pasada.",
        )

    def handle(self, *args, **options):
        # La cola es cross-tenant por naturaleza: drena las notificaciones de
        # todas las empresas. core.notificacion tiene tenant_id, asi que RLS le
        # aplica la politica tenant_isolation, y un comando de consola no pasa
        # por el middleware que publica app.current_tenant_id: sin esto la
        # consulta no ve NINGUNA fila y el worker reporta "0 enviadas" sin
        # haber tocado nada. Mismo permiso que usa el portal de plataforma en
        # core.utils.audit.sysadmin_context.
        #
        # is_local=false (a nivel de sesion, no de transaccion) porque el
        # servicio guarda cada notificacion en su propio autocommit: con
        # is_local=true el GUC se descartaria antes del primer save(). Es
        # seguro aqui y NO lo seria en la API: este es un proceso dedicado y
        # efimero, con su propia conexion, que no se reutiliza entre
        # peticiones.
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.is_sysadmin', 'true', false)")

        enviadas, fallidas = notificacion_service.procesar_pendientes(limite=options.get("limite"))
        self.stdout.write(
            self.style.SUCCESS(f"Notificaciones enviadas: {enviadas} · reencoladas/fallidas: {fallidas}")
        )
