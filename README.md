# NovaERP Backend — Guía de instalación

Tutorial paso a paso para levantar el backend de NovaERP en un entorno nuevo
(desarrollo). Al final tendrás la API corriendo, la base de datos con su esquema
completo y un tenant de ejemplo listo para probar.

> **Procedimiento verificado** contra un build limpio (PostgreSQL 16, Python
> 3.14, Django 6.0.7): `db.sql` + las migraciones de `sql/` cargan sin errores y
> `manage.py check` queda verde.

---

## 1. Requisitos previos

| Componente | Versión | Notas |
|---|---|---|
| **Python** | 3.12+ (probado en 3.14) | |
| **PostgreSQL** | 14+ (probado en 16) | Con `psql` en el equipo. Las extensiones `pgcrypto`, `citext` y `pg_trgm` deben estar disponibles (vienen con la instalación estándar de PostgreSQL). |
| **Git** | cualquiera | Para clonar el repositorio. |

> **Importante:** este proyecto **no usa migraciones de Django**. Todos los
> modelos son `managed = False` y el esquema (tablas, funciones, triggers, RLS)
> vive en PostgreSQL. **Nunca ejecutes `manage.py migrate` ni `makemigrations`.**
> La fuente de verdad del esquema es `db.sql` + los scripts de `sql/`.

---

## 2. Código y entorno de Python

```powershell
# Clonar (o copiar) el repositorio y entrar a la carpeta
git clone <URL-del-repo> novaerp-backend
cd novaerp-backend

# Crear y activar el entorno virtual (Windows PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1

# Instalar dependencias
pip install -r requirements.txt
```

En Linux/macOS el activador es `source venv/bin/activate`.

---

## 3. Crear la base de datos

Con un rol de PostgreSQL con permisos de creación (por defecto `postgres`):

```powershell
# Ajusta la ruta de psql a tu versión de PostgreSQL
$psql = "C:\Program Files\PostgreSQL\16\bin\psql.exe"
$env:PGPASSWORD = "TU_PASSWORD_DE_POSTGRES"

& $psql -U postgres -h localhost -c "CREATE DATABASE novaerp;"
```

> Las extensiones `pgcrypto`, `citext` y `pg_trgm` las crea el propio `db.sql`
> con `CREATE EXTENSION IF NOT EXISTS`. Esto requiere un rol con privilegio para
> crear extensiones (superusuario, o extensiones pre-instaladas por el DBA).

---

## 4. Cargar el esquema

**El orden importa:** primero `db.sql` (esquema base + seed del tenant de
ejemplo), luego los scripts de `sql/` en orden de nombre (auditoría, catálogo de
permisos/RBAC, y los incrementos de cada RF). Todos son idempotentes.

```powershell
$psql = "C:\Program Files\PostgreSQL\16\bin\psql.exe"
$env:PGPASSWORD = "TU_PASSWORD_DE_POSTGRES"

# 1) Esquema base + seed
& $psql -U postgres -h localhost -d novaerp -v ON_ERROR_STOP=1 -f final.sql

# 2) Migraciones incrementales, en orden de nombre
Get-ChildItem sql\*.sql | Sort-Object Name | ForEach-Object {
    Write-Host "Aplicando $($_.Name)"
    & $psql -U postgres -h localhost -d novaerp -v ON_ERROR_STOP=1 -f $_.FullName
}
```

En Linux/macOS (bash):

```bash
export PGPASSWORD="TU_PASSWORD_DE_POSTGRES"
psql -U postgres -h localhost -d novaerp -v ON_ERROR_STOP=1 -f db.sql
for f in $(ls sql/*.sql | sort); do
  echo "Aplicando $f"
  psql -U postgres -h localhost -d novaerp -v ON_ERROR_STOP=1 -f "$f"
done
```

**Qué queda instalado** (verificable): 63+ tablas en 10 esquemas, 37 triggers de
auditoría, 72 permisos en el catálogo RBAC, 16 módulos, 3 planes comerciales, y
un tenant de ejemplo **`acme`** con su administrador.

---

## 5. Configurar variables de entorno (`.env`)

Crea un archivo `.env` en la raíz del proyecto (no se versiona). Mínimo:

```dotenv
# Django
DEBUG=True
SECRET_KEY=cambia-esto-por-una-clave-larga-y-aleatoria
ALLOWED_HOSTS=localhost,127.0.0.1

# PostgreSQL
DB_NAME=novaerp
DB_USER=postgres
DB_PASSWORD=TU_PASSWORD_DE_POSTGRES
DB_HOST=localhost
DB_PORT=5432
```

Variables opcionales (tienen valores por defecto sensatos):

```dotenv
# Expiracion del token del portal de plataforma (SysAdmin), horas
SYSADMIN_JWT_EXPIRACION_HORAS=8

# Correo saliente. Por defecto usa el backend de CONSOLA (imprime, no envia).
# Para envio real, configura SMTP:
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.tu-proveedor.com
EMAIL_PORT=587
EMAIL_HOST_USER=usuario
EMAIL_HOST_PASSWORD=secreto
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=no-reply@tudominio.com

# Si hay un reverse proxy de confianza que reescribe X-Forwarded-For
USE_X_FORWARDED_FOR=False
```

> **`SECRET_KEY`**: firma los JWT. Si la cambias, invalidas todas las sesiones
> emitidas. Usa una clave larga y secreta en cualquier entorno real.

---

## 6. Verificar la instalación

```powershell
python manage.py check
```

Debe responder `System check identified no issues`. Esto valida modelos,
imports, middleware y la conexión a la base de datos.

---

## 7. Crear el primer SysAdmin (portal de plataforma)

El SysAdmin administra los tenants (RF-01..04) y **no se siembra** por defecto.
Créalo con el comando dedicado (la contraseña se pide de forma segura o por
variable de entorno; nunca se pasa como argumento):

```powershell
# Interactivo (pide la contraseña con getpass)
python manage.py crear_sysadmin --email root@novaerp.local

# O de forma no interactiva (CI/despliegue) por variables de entorno
$env:SYSADMIN_EMAIL = "root@novaerp.local"
$env:SYSADMIN_PASSWORD = "UnaClaveSeguraDeMin12Chars"
python manage.py crear_sysadmin
```

La contraseña debe tener al menos 12 caracteres. Es idempotente: si el correo ya
existe, no lo sobrescribe (usa `--force` para resetear la contraseña).

---

## 8. Levantar el servidor

```powershell
python manage.py runserver
```

La API queda en `http://127.0.0.1:8000/`.

---

## 9. Probar que funciona

### Tenant de ejemplo sembrado

`db.sql` deja listo el tenant **`acme`** con su administrador:

- **tenant_slug:** `acme`
- **correo:** `admin@acme.com`
- **contraseña:** `CambiarEnPrimerLogin!`

### Login de usuario de tenant (dos fases con MFA)

El login es de **dos fases** (password → segundo factor TOTP). En el **primer
login** el usuario **enrola su MFA**: la fase 1 devuelve un `secret` y una
`otpauth_uri` para configurar tu app autenticadora (Google Authenticator, etc.),
y luego confirmas con el código de 6 dígitos en la fase 2.

```bash
# Fase 1: devuelve un "reto" y, en el primer login, el "secret" para enrolar MFA
curl -X POST http://127.0.0.1:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"tenant_slug":"acme","correo":"admin@acme.com","password":"CambiarEnPrimerLogin!"}'

# Fase 2: envia el reto + el codigo TOTP de 6 digitos -> devuelve el token JWT
curl -X POST http://127.0.0.1:8000/api/auth/otp/ \
  -H "Content-Type: application/json" \
  -d '{"reto":"<reto-de-la-fase-1>","codigo":"123456"}'
```

### Login del portal de plataforma (SysAdmin, una fase)

```bash
curl -X POST http://127.0.0.1:8000/api/admin/login/ \
  -H "Content-Type: application/json" \
  -d '{"correo":"root@novaerp.local","password":"UnaClaveSeguraDeMin12Chars"}'
```

### Colección de peticiones

El archivo **`test.http`** (REST Client de VS Code) documenta ejemplos de todos
los endpoints: autenticación, núcleo, portal de plataforma, y el bloque de
Ventas (oportunidades, cotizaciones, pedidos, facturas).

---

## 10. Entrega de correos (opcional, para flujos de activación)

Los correos de activación, verificación y recuperación se **encolan** en la
tabla `core.notificacion`; un worker los entrega:

```powershell
python manage.py enviar_notificaciones
```

- En **desarrollo** el backend de correo por defecto es la **consola** (imprime
  el correo en la salida estándar; no envía nada real).
- Para entrega real: configura SMTP en `.env` (paso 5) y programa el worker con
  un scheduler (cron en Linux, Task Scheduler en Windows), p. ej. cada minuto.

> Nota: el alta de tenant (RF-01) y de usuario (RF-05) devuelven el token de
> activación en la respuesta del API (conveniencia de desarrollo), así que
> **puedes activar sin correo**. El restablecimiento de contraseña (RF-18) sí
> depende del correo (el token solo se encola).

---

## 11. Notas para producción

- **No uses `postgres` (superusuario) como rol de la aplicación.** El aislamiento
  por tenant se refuerza con Row Level Security (RLS) a nivel de motor, pero un
  superusuario **omite RLS** (`BYPASSRLS`). En producción crea un rol de
  aplicación sin ese privilegio (las extensiones y el esquema los instala un
  superusuario aparte). La app publica `app.current_tenant_id` / `app.is_sysadmin`
  en cada transacción para que las políticas de RLS entren en efecto.
- **`DEBUG=False`** y un `SECRET_KEY` fuerte y secreto.
- **`ALLOWED_HOSTS`** con tu dominio real.
- **TLS 1.2+** delante (reverse proxy); activa `USE_X_FORWARDED_FOR=True` solo si
  ese proxy reescribe la cabecera.
- Programa el worker de notificaciones y configura un **SMTP** real.

---

## Resumen rápido (chuleta)

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
& $psql -U postgres -c "CREATE DATABASE novaerp;"
& $psql -U postgres -d novaerp -v ON_ERROR_STOP=1 -f db.sql
Get-ChildItem sql\*.sql | Sort-Object Name | ForEach-Object { & $psql -U postgres -d novaerp -v ON_ERROR_STOP=1 -f $_.FullName }
# crear .env (paso 5)
python manage.py check
python manage.py crear_sysadmin --email root@novaerp.local
python manage.py runserver
```
