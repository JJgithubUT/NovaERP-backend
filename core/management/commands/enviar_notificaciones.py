from django.core.management.base import BaseCommand

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
        enviadas, fallidas = notificacion_service.procesar_pendientes(limite=options.get("limite"))
        self.stdout.write(
            self.style.SUCCESS(f"Notificaciones enviadas: {enviadas} · reencoladas/fallidas: {fallidas}")
        )
