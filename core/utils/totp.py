"""Segundo factor TOTP (RFC 6238) con solo biblioteca estandar.

No se usa pyotp ni ninguna dependencia externa: HMAC-SHA1 + truncamiento
dinamico son suficientes. Parametros de facto de los autenticadores
(Google Authenticator, Authy, etc.): SHA1, 6 digitos, periodo 30 s.
"""
import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote, urlencode

DIGITOS = 6
PERIODO = 30  # segundos
ISSUER = "NovaERP"

# ±1 paso: tolera hasta 30 s de desfase de reloj entre servidor y dispositivo.
VENTANA_PASOS = 1


def _hotp(clave_bytes, contador):
    """HOTP (RFC 4226): HMAC-SHA1 + truncamiento dinamico -> codigo de N digitos."""
    mensaje = struct.pack(">Q", contador)
    h = hmac.new(clave_bytes, mensaje, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    binario = (
        (h[offset] & 0x7F) << 24
        | (h[offset + 1] & 0xFF) << 16
        | (h[offset + 2] & 0xFF) << 8
        | (h[offset + 3] & 0xFF)
    )
    return str(binario % (10 ** DIGITOS)).zfill(DIGITOS)


def _codigo(clave_bytes, momento=None):
    if momento is None:
        momento = time.time()
    return _hotp(clave_bytes, int(momento // PERIODO))


def generar_secreto():
    """Secreto base32 de 160 bits (20 bytes), el tamano recomendado por RFC 4226."""
    return base64.b32encode(os.urandom(20)).decode("ascii")


def codigo_actual(secreto_base32, momento=None):
    """Codigo TOTP vigente para un secreto. Lo usa quien posee el secreto (el
    dispositivo del usuario); en el servidor solo se usa para pruebas."""
    clave = base64.b32decode(secreto_base32, casefold=True)
    return _codigo(clave, momento)


def verificar_codigo(secreto_base32, codigo, momento=None):
    """True si `codigo` es valido para `secreto_base32` dentro de la ventana de
    tolerancia. Comparacion en tiempo constante (hmac.compare_digest) para no
    filtrar informacion por temporizacion."""
    if not secreto_base32 or not codigo:
        return False
    codigo = str(codigo).strip()
    if len(codigo) != DIGITOS or not codigo.isdigit():
        return False
    try:
        clave = base64.b32decode(secreto_base32, casefold=True)
    except (ValueError, base64.binascii.Error):
        return False

    if momento is None:
        momento = time.time()
    paso = int(momento // PERIODO)
    for delta in range(-VENTANA_PASOS, VENTANA_PASOS + 1):
        if hmac.compare_digest(_hotp(clave, paso + delta), codigo):
            return True
    return False


def otpauth_uri(secreto_base32, correo, issuer=ISSUER):
    """URI otpauth:// que el cliente convierte en codigo QR. El servidor no
    genera la imagen (no se usa qrcode); solo entrega la URI/secreto una vez."""
    etiqueta = quote(f"{issuer}:{correo}")
    params = urlencode(
        {
            "secret": secreto_base32,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": DIGITOS,
            "period": PERIODO,
        }
    )
    return f"otpauth://totp/{etiqueta}?{params}"
