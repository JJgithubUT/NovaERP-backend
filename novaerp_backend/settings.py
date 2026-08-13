"""
Configuracion de NovaERP.

Un solo archivo para todos los entornos: lo que cambia entre desarrollo y
despliegue son las variables del `.env`, no el codigo. Ver `.env.example` para
la lista completa y `docs/DESPLIEGUE-CORREO.md` para el servicio de correo.

Principio: **defaults seguros**. Si una variable falta, el valor por defecto es
el de produccion (DEBUG apagado, sin claves inventadas), y arrancar sin lo
minimo indispensable revienta con ImproperlyConfigured en vez de servir una
instalacion insegura en silencio.

Referencia: https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Ruta explicita: load_dotenv() a secas depende del directorio de trabajo, y un
# servicio (systemd, Task Scheduler, cron) casi nunca arranca desde el proyecto.
load_dotenv(BASE_DIR / '.env')


# ----------------------------------------------------------------------------
# Lectura del entorno
# ----------------------------------------------------------------------------
# Django no interpreta tipos: todo lo que llega del .env es texto. Estos
# ayudantes centralizan la conversion para que "False", "0" o "" signifiquen lo
# mismo en todas las variables y no haya que recordarlo en cada linea.

_VERDADEROS = ('1', 'true', 'yes', 'si', 'sí', 'on')


def env(nombre, defecto=None):
    """Valor de entorno, con '' tratado como ausente (una variable declarada y
    vacia en el .env es lo mismo que no declararla)."""
    valor = os.getenv(nombre)
    if valor is None or valor.strip() == '':
        return defecto
    return valor.strip()


def env_bool(nombre, defecto=False):
    valor = env(nombre)
    return defecto if valor is None else valor.lower() in _VERDADEROS


def env_int(nombre, defecto):
    valor = env(nombre)
    if valor is None:
        return defecto
    try:
        return int(valor)
    except ValueError:
        raise ImproperlyConfigured(f"{nombre} debe ser un entero, se recibio {valor!r}.")


def env_list(nombre, defecto=()):
    """Lista separada por comas. Vacia -> el defecto."""
    valor = env(nombre)
    if valor is None:
        return list(defecto)
    return [item.strip() for item in valor.split(',') if item.strip()]


def _exigir(nombre, valor, motivo):
    """Solo se llama cuando DEBUG esta apagado: en desarrollo no estorba, en
    despliegue impide arrancar a medias."""
    if not valor:
        raise ImproperlyConfigured(
            f"Falta {nombre} en el entorno (.env). {motivo}"
        )
    return valor


# ----------------------------------------------------------------------------
# Nucleo
# ----------------------------------------------------------------------------
# DEBUG por defecto FALSE: olvidar la variable en el servidor no puede tener
# como consecuencia exponer tracebacks y consultas SQL.
DEBUG = env_bool('DEBUG', False)

# La SECRET_KEY firma los JWT de toda la API (core.services.session_service y
# sysadmin_session_service la usan como clave HS256), no solo las cookies de
# Django. Rotarla invalida TODAS las sesiones vivas de tenants y de plataforma:
# es el mecanismo de emergencia si se sospecha una filtracion, y la razon por la
# que nunca debe compartirse entre entornos.
SECRET_KEY = env('SECRET_KEY')
if not SECRET_KEY:
    if not DEBUG:
        _exigir('SECRET_KEY', None,
                'Genere una con: python -c "import secrets; print(secrets.token_urlsafe(64))"')
    # Solo desarrollo: clave estable y evidentemente insegura, para no obligar a
    # tener .env al clonar el repositorio.
    SECRET_KEY = 'django-insecure-solo-desarrollo-no-usar-en-despliegue'
elif not DEBUG and len(SECRET_KEY) < 50:
    raise ImproperlyConfigured(
        'SECRET_KEY es demasiado corta para un despliegue (minimo 50 caracteres). '
        'Genere una con: python -c "import secrets; print(secrets.token_urlsafe(64))"'
    )

# Sin comodines por defecto. En despliegue debe listar los dominios reales; con
# DEBUG apagado y la lista vacia, Django rechaza todas las peticiones.
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', ['localhost', '127.0.0.1'] if DEBUG else [])
if not DEBUG:
    _exigir('ALLOWED_HOSTS', ALLOWED_HOSTS,
            'Liste los dominios que sirven la API, separados por coma.')

# RF-20/RN01: la bitacora registra la IP de origen. Detras de un reverse proxy
# REMOTE_ADDR es la del proxy, no la del cliente. Activar SOLO cuando exista un
# proxy de confianza que reescriba la cabecera: si se confia en X-Forwarded-For
# sin proxy delante, cualquier cliente puede falsear la IP que queda auditada.
# Tambien es la senal de "hay un proxy delante" para el manejo de HTTPS de abajo.
USE_X_FORWARDED_FOR = env_bool('USE_X_FORWARDED_FOR', False)


# ----------------------------------------------------------------------------
# Aplicaciones y middleware
# ----------------------------------------------------------------------------
INSTALLED_APPS = [
    # Sin contenttypes/auth/sessions/admin/messages: no los necesitamos y
    # así nunca se crean tablas django_* en la base de datos.
    'django.contrib.staticfiles',
    'corsheaders',

    # Tus apps de dominio (una por esquema de Postgres)
    'core',
    'ventas',
    'inventario',
    'compras',
    'finanzas',
    'rrhh',
    'proyectos',
    'bpm',
    'bi',
    'reglas',
]

# Modifica los Middlewares quitando las sesiones y auth antiguas
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.auth_middleware.JWTCustomMiddleware',
]

ROOT_URLCONF = 'novaerp_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'novaerp_backend.wsgi.application'


# ----------------------------------------------------------------------------
# CORS
# ----------------------------------------------------------------------------
# El frontend corre en un origen distinto. CORS_ALLOWED_ORIGINS por .env,
# coma-separado y SIN barra final (un origen es esquema+host+puerto). El login
# manda el JWT en el header Authorization, no en cookies, asi que no se necesita
# CORS_ALLOW_CREDENTIALS.
#
# En desarrollo conviene listar los dos puertos habituales a la vez:
#   CORS_ALLOWED_ORIGINS=http://localhost:4200,http://localhost:5173
CORS_ALLOWED_ORIGINS = env_list(
    'CORS_ALLOWED_ORIGINS', ['http://localhost:4200'] if DEBUG else []
)
if not DEBUG:
    _exigir('CORS_ALLOWED_ORIGINS', CORS_ALLOWED_ORIGINS,
            'Liste el origen del frontend (https://…), separado por coma si hay varios.')


# ----------------------------------------------------------------------------
# Base de datos
# ----------------------------------------------------------------------------
# El esquema NO lo administra Django (los modelos son managed=False; la fuente
# de verdad esta en sql/). El usuario de la aplicacion no necesita permisos de
# DDL en despliegue: basta SELECT/INSERT/UPDATE/DELETE sobre los esquemas.
#
# DATABASE_URL es opcional: existe porque proveedores como Render solo dan una
# cadena de conexion (postgresql://usuario:password@host:puerto/nombre), no
# variables sueltas. Si esta presente, rellena los defaults de DB_*; cualquier
# DB_* explicito en el entorno sigue ganando (por si hay que pisar un solo
# campo sin tocar la URL completa).
_db_url = env('DATABASE_URL')
if _db_url:
    from urllib.parse import urlsplit
    _partes = urlsplit(_db_url)
    _DB_DEFAULTS = {
        'NAME': (_partes.path or '/').lstrip('/'),
        'USER': _partes.username or '',
        'PASSWORD': _partes.password or '',
        'HOST': _partes.hostname or 'localhost',
        'PORT': str(_partes.port or 5432),
    }
else:
    _DB_DEFAULTS = {'NAME': None, 'USER': None, 'PASSWORD': None, 'HOST': 'localhost', 'PORT': '5432'}

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('DB_NAME', _DB_DEFAULTS['NAME']),
        'USER': env('DB_USER', _DB_DEFAULTS['USER']),
        'PASSWORD': env('DB_PASSWORD', _DB_DEFAULTS['PASSWORD']),
        'HOST': env('DB_HOST', _DB_DEFAULTS['HOST']),
        'PORT': env('DB_PORT', _DB_DEFAULTS['PORT']),
        # Conexiones persistentes: evita el coste de abrir una conexion por
        # peticion. 0 = comportamiento anterior (abrir y cerrar cada vez).
        'CONN_MAX_AGE': env_int('DB_CONN_MAX_AGE', 0 if DEBUG else 60),
        'OPTIONS': {
            # 'require' obliga TLS contra la base. Con la base en otra maquina
            # deberia ser 'require' o superior; 'prefer' es el default de libpq.
            'sslmode': env('DB_SSLMODE', 'prefer'),
            # Corta una consulta colgada en vez de retener el worker web.
            'options': f"-c statement_timeout={env_int('DB_STATEMENT_TIMEOUT_MS', 30000)}",
        },
    }
}
if not DEBUG:
    # Revisa el valor YA RESUELTO (DB_* explicito o, en su defecto, lo que
    # trajo DATABASE_URL) -- no la variable de entorno cruda, o esta
    # validacion rechazaria un despliegue configurado solo con DATABASE_URL
    # (el caso de Render) aunque la conexion este completa.
    for _campo in ('NAME', 'USER', 'PASSWORD'):
        _exigir(f'DB_{_campo}', DATABASES['default'][_campo],
                'La conexion a PostgreSQL no puede quedar a medias '
                '(defina DB_* sueltas o DATABASE_URL).')


# ----------------------------------------------------------------------------
# Correo (RF-25)
# ----------------------------------------------------------------------------
# Entrega de notificaciones. Solo se usa el framework de email de Django, sin
# librerias nuevas. La cola vive en core.notificacion y la drena el worker
# `manage.py enviar_notificaciones` (ver docs/DESPLIEGUE-CORREO.md).
#
# El default imprime a consola: en desarrollo el correo se "envia" al terminal y
# la notificacion queda marcada como enviada, sin depender de un SMTP real.
EMAIL_BACKEND = env('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', 'no-reply@novaerp.local')
# Remitente de los errores que Django reporta por correo (distinto del anterior
# a proposito: si el buzon de la aplicacion falla, este puede seguir saliendo).
SERVER_EMAIL = env('SERVER_EMAIL', DEFAULT_FROM_EMAIL)

EMAIL_HOST = env('EMAIL_HOST', 'localhost')
EMAIL_PORT = env_int('EMAIL_PORT', 25)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', '')
# STARTTLS (puerto 587) vs TLS implicito (puerto 465). Son excluyentes.
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', False)
EMAIL_USE_SSL = env_bool('EMAIL_USE_SSL', False)
# Sin timeout, un SMTP que no responde cuelga la pasada del worker
# indefinidamente y la cola deja de drenarse.
EMAIL_TIMEOUT = env_int('EMAIL_TIMEOUT', 20)
# EMAIL_SUBJECT_PREFIX no se define: Django solo lo aplica a mail_admins() /
# mail_managers(), no a send_mail(), que es lo que usa el worker de RF-25. El
# asunto de cada notificacion lo arma notificacion_service.

if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured(
        'EMAIL_USE_TLS y EMAIL_USE_SSL son excluyentes: use TLS con el puerto 587 '
        'o SSL con el 465, no ambos.'
    )
if not DEBUG and EMAIL_BACKEND.endswith('smtp.EmailBackend'):
    _exigir('EMAIL_HOST', env('EMAIL_HOST'), 'El backend SMTP necesita un servidor.')

# Origen del frontend, para armar el enlace clicable de los correos con token
# (activacion, verificacion de correo, restablecimiento). Sin barra final, igual
# que CORS_ALLOWED_ORIGINS.
FRONTEND_URL = env('FRONTEND_URL', 'http://localhost:4200' if DEBUG else '')
if not DEBUG:
    _exigir('FRONTEND_URL', FRONTEND_URL,
            'Los correos con token necesitan saber a que origen enlazar (https://…).')


# ----------------------------------------------------------------------------
# Sesion del portal de plataforma
# ----------------------------------------------------------------------------
# El SysAdmin no pertenece a ningun tenant, asi que no hay
# config_seguridad_tenant.jwt_expiracion_horas que leer: su expiracion se
# configura por entorno (default 8 h, como el default de los tenants).
SYSADMIN_JWT_EXPIRACION_HORAS = env_int('SYSADMIN_JWT_EXPIRACION_HORAS', 8)


# ----------------------------------------------------------------------------
# Endurecimiento HTTP (solo con DEBUG apagado)
# ----------------------------------------------------------------------------
# En desarrollo estas opciones estorban (redirigen a https://localhost, que no
# existe). En despliegue son la diferencia entre servir la API por HTTPS de
# verdad o depender de que nadie escriba http:// a mano.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'

if not DEBUG:
    # Con un proxy delante (nginx, IIS, un balanceador), Django ve HTTP aunque
    # el cliente hable HTTPS. Esta cabecera es como se entera; solo se confia en
    # ella bajo la misma premisa que X-Forwarded-For: hay un proxy de confianza.
    if USE_X_FORWARDED_FOR:
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', True)
    # HSTS: empiece en 0 y subalo (3600 -> 86400 -> 31536000) solo cuando el
    # certificado este estable. Un valor alto con HTTPS roto deja el dominio
    # inaccesible durante todo ese tiempo, y los navegadores ya lo recordaron.
    SECURE_HSTS_SECONDS = env_int('SECURE_HSTS_SECONDS', 0)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
    SECURE_HSTS_PRELOAD = env_bool('SECURE_HSTS_PRELOAD', False)
    # No hay sesiones de Django (la autenticacion es por JWT en Authorization),
    # pero el middleware de CSRF sigue montado y emite su cookie.
    CSRF_COOKIE_SECURE = True
    # Origenes que pueden mandar formularios/POST con cookie CSRF. Django 4+ los
    # exige con esquema. Si el frontend vive en otro dominio, listelo aqui.
    CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', CORS_ALLOWED_ORIGINS)


# ----------------------------------------------------------------------------
# Internacionalizacion
# ----------------------------------------------------------------------------
LANGUAGE_CODE = env('LANGUAGE_CODE', 'es-mx')

# Las marcas de tiempo se guardan en UTC (USE_TZ=True); TIME_ZONE solo afecta
# como se interpretan y muestran. Cambiarlo no reescribe nada de lo ya grabado.
TIME_ZONE = env('TIME_ZONE', 'UTC')

USE_I18N = True
USE_TZ = True


# ----------------------------------------------------------------------------
# Archivos estaticos
# ----------------------------------------------------------------------------
STATIC_URL = 'static/'
# Destino de `manage.py collectstatic`. La API no sirve UI, pero el comando se
# ejecuta en el despliegue y necesita un destino definido.
STATIC_ROOT = env('STATIC_ROOT', str(BASE_DIR / 'staticfiles'))


# ----------------------------------------------------------------------------
# Registro (logging)
# ----------------------------------------------------------------------------
# A consola: quien recoja la salida (systemd, Docker, Task Scheduler, el propio
# terminal) decide donde termina. NO sustituye a la bitacora de auditoria
# (RF-20), que vive en core.log_auditoria y es la fuente legal de los eventos;
# esto es diagnostico de operacion.
LOG_LEVEL = env('LOG_LEVEL', 'DEBUG' if DEBUG else 'INFO')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'estandar': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'consola': {
            'class': 'logging.StreamHandler',
            'formatter': 'estandar',
        },
    },
    'root': {
        'handlers': ['consola'],
        'level': LOG_LEVEL,
    },
    'loggers': {
        # Los 4xx/5xx de la API. En produccion, un 500 aqui es lo primero que
        # se mira cuando el frontend reporta un fallo.
        'django.request': {
            'handlers': ['consola'],
            'level': 'ERROR',
            'propagate': False,
        },
        # Cada consulta SQL. Ensordecedor: solo bajo peticion explicita.
        'django.db.backends': {
            'handlers': ['consola'],
            'level': env('LOG_LEVEL_SQL', 'WARNING'),
            'propagate': False,
        },
    },
}

# Nota sobre AUTH_PASSWORD_VALIDATORS: no se define a proposito. Los validadores
# de Django solo actuan a traves de django.contrib.auth, que no esta instalado.
# La politica de contrasenas real es por tenant y configurable (RF-22): vive en
# core.config_seguridad_tenant y la aplica core.services.usuario_service.
