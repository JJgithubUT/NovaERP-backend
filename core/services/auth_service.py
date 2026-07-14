from django.db import connection


class LoginError(Exception):
    pass


def intentar_login(tenant_slug: str, correo: str, password: str) -> dict:
    """Delega la autenticacion a core.intentar_login: la DB valida el hash
    de password y las reglas de bloqueo/intentos fallidos."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT ok, usuario_id, mensaje FROM core.intentar_login(%s, %s, %s)",
            [tenant_slug, correo, password],
        )
        row = cursor.fetchone()
        columns = [col[0] for col in cursor.description]
        result = dict(zip(columns, row))

    if not result["ok"]:
        raise LoginError(result["mensaje"])

    return result
