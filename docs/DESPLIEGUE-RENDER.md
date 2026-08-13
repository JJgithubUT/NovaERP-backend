# NovaERP — Desplegar en Render

Cómo llevar `docs/DESPLIEGUE.md` (la guía genérica) a Render en concreto. No
repite lo que ya no cambia (rol de PostgreSQL sin `BYPASSRLS`, HSTS por
etapas, el problema de `SECRET_KEY` §7.1): solo lo que Render hace distinto.

> Documentos hermanos: [`DESPLIEGUE.md`](DESPLIEGUE.md) (guía genérica,
> léala primero) · [`DESPLIEGUE-CORREO.md`](DESPLIEGUE-CORREO.md) (detalle del
> worker) · [`render.yaml`](../render.yaml) (el blueprint que implementa esta
> guía).

---

## 0. Qué resuelve Render y qué no

| Bloqueante de §0 de `DESPLIEGUE.md` | En Render |
|---|---|
| B1/B3 (`requirements.txt` con basura de dev) | Igual de real: el build de Render corre `pip install -r requirements-prod.txt` (ya existe en el repo) |
| B2 (falta servidor WSGI) | Igual de real: `startCommand` usa gunicorn, Render no lo pone solo |
| B4 (rotar `SECRET_KEY` bloquea el sistema) | Sin cambios — sigue siendo un problema de la aplicación, no de la plataforma. Lea §7.1 de `DESPLIEGUE.md` antes de tocarla |
| R1 (endpoint de salud) | Render lo usa de verdad: `healthCheckPath` en el blueprint decide si un deploy se considera sano |
| R2 (Dockerfile/systemd/nginx) | Render los reemplaza: no hace falta ninguno de los tres |

Lo que Render **sí** resuelve solo: TLS del dominio `*.onrender.com`, el
reverse proxy, los reinicios ante caída, y el *scheduler* del worker de correo
(sin cron ni Task Scheduler propios).

---

## 1. Arquitectura

Dos servicios de Render + una base de datos, o tres servicios si la base ya
vive en otro proveedor:

```mermaid
flowchart LR
    subgraph Render
      W["Web Service<br/>novaerp-backend<br/>gunicorn"]
      C["Cron Job<br/>novaerp-enviar-notificaciones<br/>* * * * * (UTC)"]
      DB[("Postgres<br/>novaerp-db")]
    end
    F["Frontend Angular<br/>(Render Static Site / Vercel / Netlify)"] -->|HTTPS + JWT| W
    W -->|RLS, rol sin BYPASSRLS| DB
    C -->|drena core.notificacion| DB
    C -->|SMTP| SMTP[("Proveedor de correo<br/>Brevo, SendGrid...")]
```

`W` y `C` son **procesos independientes** (dos builds, dos instancias). Ninguno
depende del otro para arrancar, pero ambos necesitan las mismas variables de
entorno — de ahí el grupo compartido en §5.

---

## 2. Antes de empezar

- [ ] Cuenta de Render con el repo de GitHub/GitLab conectado
- [ ] `requirements-prod.txt` presente y validado (ya está en el repo, §1 de `DESPLIEGUE.md`)
- [ ] Credenciales SMTP de producción probadas ([`DESPLIEGUE-CORREO.md`](DESPLIEGUE-CORREO.md))
- [ ] Dominio del frontend en producción decidido (para `CORS_ALLOWED_ORIGINS`
      y `FRONTEND_URL`) — aunque sea el `*.onrender.com` provisional del
      primer despliegue
- [ ] `SECRET_KEY` de producción generada aparte:
      `python -c "import secrets; print(secrets.token_urlsafe(64))"`

---

## 3. Base de datos

### 3.1 Crear la instancia

Render Postgres administrado es la opción simple (backups automáticos,
integración con `fromDatabase` en el blueprint). El `render.yaml` del repo la
declara como `novaerp-db`.

⚠️ **El plan `free` de Postgres expira a los 30 días y se borra.** No lo use
para nada que deba sobrevivir; el blueprint usa `basic-1gb` como piso
razonable — ajuste el `plan` según el volumen real de datos.

Si prefiere una base externa (Supabase, Neon, RDS, un Postgres propio), quite
el bloque `databases:` del blueprint y ponga `DATABASE_URL` directamente en el
grupo de variables (§5) — el código ya la entiende (ver §3.3).

### 3.2 Aplicar el esquema

Render no da una consola SQL con privilegios de superusuario para correr
scripts largos desde el dashboard; hágalo desde su máquina, apuntando a la
**External Connection URL** que Render muestra en la página de la base de
datos (Dashboard → `novaerp-db` → Connect):

```bash
psql "<External Connection URL>" -v ON_ERROR_STOP=1 -f db.sql
for f in $(ls sql/*.sql | sort); do psql "<External Connection URL>" -v ON_ERROR_STOP=1 -f "$f"; done
```

Mismo `ON_ERROR_STOP=1` obligatorio que en la guía genérica (§3.1 de
`DESPLIEGUE.md`): sin él, un error a mitad de script deja el esquema a medias
sin avisar.

### 3.3 El rol de aplicación — verifíquelo, no lo asuma

`DESPLIEGUE.md` §3.2 es categórico: **si la aplicación se conecta como un rol
que puede saltarse RLS, el aislamiento entre tenants no existe**, sin ningún
síntoma visible. La documentación pública de Render no dice explícitamente si
el usuario que crea por default tiene `BYPASSRLS`. No lo dé por sentado:

```sql
-- Conectado con las credenciales que va a usar la aplicación:
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
-- rolsuper y rolbypassrls DEBEN ser 'f'. Si alguna es 't', NO despliegue
-- así: cree un rol restringido aparte (mismo procedimiento de DESPLIEGUE.md
-- §3.2, CREATE ROLE ... sin BYPASSRLS) y use ESE en DATABASE_URL/DB_USER.
```

Si el usuario por default resulta tener privilegios de superusuario o
`BYPASSRLS`, cree el rol restringido con el mismo procedimiento de
`DESPLIEGUE.md` §3.2 (conectado con el usuario administrador de Render) y
apunte `DATABASE_URL` a ese rol, no al que Render generó por default.

### 3.3.1 `DATABASE_URL` — por qué existe esta variable nueva

`settings.py` originalmente solo aceptaba `DB_NAME`/`DB_USER`/`DB_PASSWORD`/
`DB_HOST`/`DB_PORT` sueltas. Render (como Heroku, Railway, etc.) solo expone
una cadena de conexión (`postgresql://usuario:password@host:puerto/nombre`).
Se agregó soporte nativo para `DATABASE_URL` — sin dependencias nuevas, solo
`urllib.parse` — que rellena esas cinco variables. Si además declara alguna
`DB_*` suelta, esa gana sobre lo que traiga la URL (útil para pisar un solo
campo, p. ej. forzar `DB_SSLMODE=require` sin tocar la URL completa). El
blueprint usa `fromDatabase: {property: connectionString}`, que es
exactamente esta variable.

---

## 4. El blueprint (`render.yaml`)

El archivo [`render.yaml`](../render.yaml) en la raíz del repo declara los dos
servicios y la base de datos.

### 4.1 Por qué las variables se repiten en los dos servicios

`settings.py` valida `SECRET_KEY`/`ALLOWED_HOSTS`/`CORS_ALLOWED_ORIGINS`/la
base de datos/`FRONTEND_URL` con `_exigir(...)` **al importar el módulo**,
condicionado solo a `DEBUG=False` — no a que el proceso sirva HTTP. El cron
nunca recibe una petición, pero carga el mismo `settings.py` que el web para
poder llamar `send_mail`, así que si le falta cualquiera de esas variables
**no arranca**, aunque nunca las use. Por eso el blueprint declara cada
variable en los dos servicios, no en un lugar compartido.

(Sí se omiten del cron `USE_X_FORWARDED_FOR`, `SECURE_SSL_REDIRECT` y
`CSRF_TRUSTED_ORIGINS`: esas **sí** tienen un valor por defecto utilizable en
`settings.py`, así que no hace falta declararlas donde no se usan.)

### 4.2 Por qué no hay un grupo de variables compartido en el blueprint

La primera versión de este archivo intentaba compartir los secretos con un
`Environment Group` referenciado desde el blueprint. Se descartó: el
[Blueprint Spec](https://render.com/docs/blueprint-spec) de Render documenta
con claridad el campo `sync: false` (ver §4.3) pero no deja igual de claro
cómo debe un servicio enganchar un grupo ya existente — mejor no apostar la
configuración de producción a una sintaxis que no pude verificar con certeza.

Si quiere evitar repetir los valores a mano en cada actualización, puede
crear un **Environment Group** desde el dashboard (no desde `render.yaml`)
después del primer deploy y enlazar ambos servicios a él ahí mismo — eso sí
lo hace la interfaz por usted, sin depender de la sintaxis del blueprint.

### 4.3 Aplicar el blueprint

1. Dashboard de Render → **New → Blueprint** → seleccione el repo → Render lee
   `render.yaml` y propone crear `novaerp-db`, `novaerp-backend` y
   `novaerp-enviar-notificaciones`.
2. Revise los planes propuestos (`starter`/`basic-1gb`) antes de confirmar —
   son ajustables después, pero conviene no arrancar en `free` si el objetivo
   final es producción (ver §9 sobre lo que implica el free tier).
3. Render pide, **una vez por servicio**, el valor de cada variable marcada
   `sync: false` en el yaml — esta es la tabla completa (aparece en ambos
   servicios salvo que se indique lo contrario):

   | Variable | Valor de ejemplo | Nota |
   |---|---|---|
   | `SECRET_KEY` | *(generada aparte, 64+ caracteres)* | **La misma cadena en los dos servicios** — ver §4.4 |
   | `ALLOWED_HOSTS` | `novaerp-backend.onrender.com` (o su dominio propio) | Sin `https://`, solo el host |
   | `CORS_ALLOWED_ORIGINS` | `https://app.suempresa.com` | Solo en el web — esquema + host, sin barra final ni ruta |
   | `CSRF_TRUSTED_ORIGINS` | `https://app.suempresa.com` | Solo en el web |
   | `FRONTEND_URL` | `https://app.suempresa.com` | Sin barra final — arma los enlaces clicables de los correos con token |
   | `EMAIL_HOST` | `smtp-relay.brevo.com` | Según el proveedor, ver `DESPLIEGUE-CORREO.md` §4 |
   | `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | — | Credenciales SMTP |
   | `DEFAULT_FROM_EMAIL` | `no-reply@suempresa.com` | Buzón autorizado por el proveedor SMTP |

4. Confirme. Render crea los tres recursos y dispara el primer build de cada
   servicio.

### 4.4 `SECRET_KEY` tiene que ser idéntica en los dos servicios

Si genera una `SECRET_KEY` distinta para `novaerp-backend` y para
`novaerp-enviar-notificaciones`, cada uno queda con una clave distinta para el
mismo `settings.py`. El worker de correo no valida JWT hoy, así que no falla
de inmediato, pero es el mismo mecanismo que cifra `mfa_secret`
(`DESPLIEGUE.md` §7.1) — no vale la pena dejar dos claves reales sueltas por
accidente cuando debería haber una sola. Genere el valor una vez y péguelo
igual en los dos prompts del paso 3.

---

## 5. Desplegar

Cada servicio corre su propio build:

```
pip install -r requirements-prod.txt
```

y arranca con su `startCommand` (`gunicorn ...` para el web, `python
manage.py enviar_notificaciones` para el cron). No hay `collectstatic` en el
flujo: la API no sirve UI ni admin de Django (ver `DESPLIEGUE.md` R1), así que
`STATIC_ROOT` queda vacío y es inofensivo.

Tras el primer deploy, cree el sysadmin igual que en bare-metal, desde el
**Shell** del servicio web (Dashboard → `novaerp-backend` → Shell):

```bash
python manage.py crear_sysadmin --email root@suempresa.com
```

---

## 6. El worker de correo como Cron Job — lo que cambia de verdad

Esto es lo que motivó volver a este documento: **sin el worker programado, la
cola de `core.notificacion` crece en silencio y ningún correo sale nunca**
(pasó ya una vez en desarrollo — la causa raíz fue exactamente esta: nada
corría `enviar_notificaciones`). En Render el mecanismo es un servicio de tipo
`cron`, no un systemd timer ni un Task Scheduler:

| Aspecto | Detalle |
|---|---|
| **Horario** | Expresión cron estándar. `render.yaml` usa `* * * * *` (cada minuto). **Todo el horario de Render es UTC** — para "cada minuto" no importa, pero si algún día programa algo a una hora específica, conviértala a UTC primero |
| **Costo** | Sin nivel gratis: factura desde ~US$1/mes, prorrateado por segundo de ejecución real. Un `manage.py enviar_notificaciones` que tarda &lt;1 s por corrida, 1440 veces al día, es barato pero no gratis — inclúyalo en el presupuesto |
| **Solapamiento** | Render garantiza que no corren dos ejecuciones del mismo cron a la vez; si una corrida se alarga, la siguiente espera. Esto es justo lo que pide `DESPLIEGUE.md` §7 ("el worker no tiene bloqueo entre instancias... programe el worker en una sola") — Render lo da gratis, no tiene que auto-implementarlo |
| **Build propio** | Cada corrida parte de un build (mismo `requirements-prod.txt`); Render cachea capas sin cambios, así que el costo extra por build es bajo pero no cero |
| **Límite de 12 h** | Render mata cualquier corrida que pase de 12 h. Irrelevante aquí — el comando procesa la cola y termina en segundos — pero si alguna vez agrega `--limite` con un valor enorme sobre una cola de miles, tenga esto presente |

**No duplique este cron job.** Si en algún momento escala a mostrar el mismo
blueprint en dos entornos (staging + producción apuntando a la misma base por
error), tendría dos schedulers drenando la misma cola — el aviso de
`DESPLIEGUE.md` §6 sobre no correr el worker en más de una máquina aplica
igual entre servicios de Render.

### Verificar que corre

Dashboard → `novaerp-enviar-notificaciones` → **Logs**: cada minuto debe
aparecer una línea con el resumen (`Notificaciones enviadas: N, reencoladas/
fallidas: M`). Si no aparece nada, el cron no se está disparando — revise que
el servicio esté `Active` y no pausado.

Por SQL, igual que en bare-metal:

```sql
SELECT estado, count(*), max(created_at) FROM core.notificacion GROUP BY estado;
```

Si `pendiente` solo crece, el cron no está corriendo o `DATABASE_URL` en ese
servicio no apunta a la misma base que el web.

---

## 7. HTTPS, `ALLOWED_HOSTS` y el proxy

Render termina TLS por usted y reenvía `X-Forwarded-Proto` — el mismo
mecanismo que nginx en bare-metal, así que aplican las mismas reglas de
`DESPLIEGUE.md` §4:

- `USE_X_FORWARDED_FOR=True` (ya está en el grupo compartido) — sin esto,
  `SECURE_SSL_REDIRECT=True` entra en el mismo bucle de redirección descrito
  ahí: Django ve HTTP, redirige a HTTPS, Render vuelve a entregar HTTP tal
  como lo ve internamente.
- `ALLOWED_HOSTS` debe incluir el host que de verdad va a recibir tráfico:
  `novaerp-backend.onrender.com` en el primer despliegue, o su dominio propio
  en cuanto lo conecte (Dashboard → `novaerp-backend` → Settings → Custom
  Domains — Render emite el certificado solo).
- HSTS: mismo criterio por etapas de `DESPLIEGUE.md` §5. Empiece en
  `SECURE_HSTS_SECONDS=0` (ya es el default si no la declara) y súbala cuando
  el dominio propio lleve 24 h estable.

---

## 8. Verificación posterior

Reutilice la lista de `DESPLIEGUE.md` §6 completa; lo único que cambia es
**cómo** se ejecuta cada paso:

| Paso de §6 | En bare-metal | En Render |
|---|---|---|
| `check --deploy` | `systemctl` + shell local | Shell del servicio web (Dashboard → Shell) |
| Arranque y proxy | `curl -I http://...` | `curl -I https://novaerp-backend.onrender.com/api/health/` — debe dar 200 directo, sin redirección visible desde fuera |
| Aislamiento entre tenants | SQL directo al servidor | SQL contra la External Connection URL de `novaerp-db` |
| Worker de correo | `systemctl list-timers` | Logs del servicio `novaerp-enviar-notificaciones` (§6) |

---

## 9. Errores comunes específicos de Render

| Síntoma | Causa probable |
|---|---|
| El build falla con un error de `pywin32` o de paquetes de desarrollo | `buildCommand` quedó apuntando a `requirements.txt` en vez de `requirements-prod.txt` |
| 400 con HTML de Django al entrar por el dominio propio | El dominio no está en `ALLOWED_HOSTS` — Render enruta el tráfico antes de que Django lo vea, pero Django igual valida el header `Host` |
| Bucle de redirección HTTPS | `USE_X_FORWARDED_FOR` en `False` con `SECURE_SSL_REDIRECT=True` (§7) |
| El cron corre pero la cola no baja | `DATABASE_URL` del servicio cron apunta a una base distinta a la del web — revise que ambos usen el mismo `fromDatabase: novaerp-db` |
| Los correos con token no traen enlace, solo el texto plano | `FRONTEND_URL` quedó sin valor en alguno de los dos servicios, o con una barra final (`.../` en vez de `...`) |
| El cron ni siquiera arranca: `ImproperlyConfigured: Falta ALLOWED_HOSTS` (o `SECRET_KEY`, `CORS_ALLOWED_ORIGINS`, `DB_NAME`...) | Al servicio `novaerp-enviar-notificaciones` le falta una de las variables `sync: false` del blueprint. No es opcional para el cron aunque nunca sirva HTTP — ver §4.1: `settings.py` valida todo el bloque de producción con solo mirar `DEBUG`, no el tipo de proceso |
| Primera petición del día muy lenta | Solo aplica si el plan del web service es `free`: Render lo apaga tras inactividad y el primer request lo "despierta". Con el plan `starter` de pago esto no ocurre. Si se queda en `free`, revise la cadena de timeouts de `DESPLIEGUE.md` §8.3 — el cold start puede superar el `receiveTimeout` del cliente |
| `ImproperlyConfigured: FRONTEND_URL` al arrancar | Es intencional (`settings.py` la exige fuera de `DEBUG`, para no repetir el bug de correos sin enlace) — complete la variable en el servicio que la reporta, no la rodee |
