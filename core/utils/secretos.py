"""Cifrado de secretos en reposo (RF-16: mfa_secret "cifrado a nivel de
aplicacion", segun el comentario del esquema).

Usa Fernet de `cryptography`, que YA es dependencia del proyecto (no introduce
una libreria nueva). La clave se deriva de settings.SECRET_KEY, la misma raiz
de confianza que ya firma los JWT; rotar SECRET_KEY invalida los secretos
cifrados (aceptable: obliga a re-enrolar, mismo efecto que perder el token).
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    # Fernet exige una clave de 32 bytes en base64 urlsafe; se deriva por
    # SHA-256 de SECRET_KEY para no exigir una variable de entorno adicional.
    clave = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(clave)


def cifrar(texto):
    return _fernet().encrypt(texto.encode("utf-8")).decode("ascii")


def descifrar(token):
    """Devuelve el texto en claro, o None si el token no es descifrable (clave
    rotada o dato corrupto): el llamador lo trata como 'sin secreto' y fuerza
    re-enrolamiento, en vez de romper el login."""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
