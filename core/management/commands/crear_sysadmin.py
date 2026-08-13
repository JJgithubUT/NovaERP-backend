import getpass
import os
import sys

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import connection

from core.utils.audit import sysadmin_context

# Bootstrap del primer SysAdmin (Administrador Global). No hay ninguno sembrado
# en el esquema, asi que sin este comando no se puede acceder al portal de
# plataforma (ni, mas adelante, ejecutar RF-01..04).
#
# Seguridad:
#   · Credenciales por env (SYSADMIN_EMAIL / SYSADMIN_PASSWORD) para CI/despliegue
#     sin intervencion; fallback interactivo con getpass en dev.
#   · La contrasena NUNCA se acepta como flag (--password quedaria visible en
#     `ps` y en el historial de shell). Solo env o getpass.
#   · Hash en Postgres con crypt(gen_salt('bf',12)) — mismo algoritmo que verifica
#     core.intentar_login_sysadmin.
#   · Idempotente sin clobber: si el correo ya existe, no lo pisa; exige --force
#     para resetear la contrasena.

# Piso de longitud. El SysAdmin no tiene tenant, asi que no hay
# config_seguridad_tenant.politica_password_min_len que leer: se aplica un mimimo
# sensato para no sembrar el actor mas privilegiado con una contrasena trivial.
MIN_LEN = 12


class Command(BaseCommand):
    help = (
        "Crea (o resetea con --force) el SysAdmin de plataforma. Credenciales por "
        "SYSADMIN_EMAIL / SYSADMIN_PASSWORD, o de forma interactiva. La contrasena "
        "nunca se pasa como flag."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email", default=None,
            help="Correo del SysAdmin. Si se omite, se lee de SYSADMIN_EMAIL.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Si el correo ya existe, resetea su contrasena y lo reactiva.",
        )

    def handle(self, *args, **options):
        correo = (options.get("email") or os.getenv("SYSADMIN_EMAIL") or "").strip()
        if not correo:
            raise CommandError("Falta el correo: usa --email o la variable SYSADMIN_EMAIL.")
        try:
            validate_email(correo)
        except ValidationError:
            raise CommandError(f"Correo invalido: {correo!r}")

        password = self._obtener_password()
        if len(password) < MIN_LEN:
            raise CommandError(f"La contrasena debe tener al menos {MIN_LEN} caracteres.")

        existe = self._existe(correo)
        if existe and not options["force"]:
            raise CommandError(
                f"Ya existe un SysAdmin con el correo {correo}. Usa --force para "
                "resetear su contrasena (no se sobrescribe sin --force)."
            )

        if existe:
            self._resetear(correo, password)
            self.stdout.write(self.style.SUCCESS(f"SysAdmin {correo}: contrasena reseteada y cuenta activa."))
        else:
            self._crear(correo, password)
            self.stdout.write(self.style.SUCCESS(f"SysAdmin {correo} creado."))

    # ------------------------------------------------------------------ helpers

    def _obtener_password(self):
        """Env primero (CI/despliegue); si no, getpass con confirmacion (dev).
        Nunca desde argv ni desde stdin en claro."""
        env = os.getenv("SYSADMIN_PASSWORD")
        if env:
            return env
        if not sys.stdin.isatty():
            raise CommandError(
                "Sin SYSADMIN_PASSWORD y sin terminal interactiva: no hay forma "
                "segura de capturar la contrasena. Define SYSADMIN_PASSWORD."
            )
        p1 = getpass.getpass("Contrasena del SysAdmin: ")
        p2 = getpass.getpass("Confirmar contrasena: ")
        if p1 != p2:
            raise CommandError("Las contrasenas no coinciden.")
        return p1

    def _existe(self, correo):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM core.sysadmin WHERE correo = %s", [correo])
            return cursor.fetchone() is not None

    def _crear(self, correo, password):
        # INSERT en core.sysadmin dispara trg_auditar_sysadmin (fn_auditar), que
        # escribe en core.log_auditoria. Esa tabla tiene RLS: sin
        # sysadmin_context (que fija app.is_sysadmin='true'), el INSERT del
        # trigger viola la politica para cualquier rol que no sea superusuario
        # -- exactamente el rol restringido que exige DESPLIEGUE.md §3.2.
        with sysadmin_context(None):
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO core.sysadmin (correo, password_hash, activo) "
                    "VALUES (%s, crypt(%s, gen_salt('bf', 12)), TRUE)",
                    [correo, password],
                )

    def _resetear(self, correo, password):
        with sysadmin_context(None):
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE core.sysadmin "
                    "SET password_hash = crypt(%s, gen_salt('bf', 12)), activo = TRUE "
                    "WHERE correo = %s",
                    [password, correo],
                )
