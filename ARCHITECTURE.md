# NovaERP — Stack de Tecnologías y Arquitectura

## 1. Backend — Django 6.0 + PostgreSQL

### Stack Tecnológico

| Componente | Tecnología | Versión | Propósito |
|---|---|---|---|
| **Framework** | Django | 6.0.7 | Web API REST |
| **Base de datos** | PostgreSQL | 14+ | Single source of truth para toda lógica de negocio |
| **Autenticación** | JWT (PyJWT) | - | Tokens firmados HS256, 8h de validez |
| **Lenguaje** | Python | 3.10+ | Backend Python nativo |
| **ORM** | Django ORM | 6.0 | Mapeo models → tablas (managed=False) |
| **CORS / HTTP** | django-cors-headers | - | CORS para frontend, headers de seguridad |
| **Async** | Django async views (opt) | - | Future-proof para tareas pesadas |

### Arquitectura — Principios Clave

#### 1. **Schema-per-App** (10 apps = 10 esquemas PostgreSQL)

```
PostgreSQL DB (novaerp)
├── core          → Autenticación, tenants, usuarios, roles, permisos
├── ventas        → Clientes, oportunidades, cotizaciones, pedidos, facturas
├── inventario    → Productos, almacenes, stock, movimientos, kardex
├── compras       → Proveedores, órdenes de compra, recepciones
├── finanzas      → Cuentas por cobrar/pagar, pagos, cierres contables
├── rrhh          → Empleados, asistencia, nómina, períodos
├── proyectos     → Proyectos, tareas
├── bpm           → Flujos de aprobación, instancias de workflows
├── bi            → Dashboards, KPIs, reportes
└── reglas        → Motor de reglas de negocio, configuraciones
```

Cada app en Django mapea a su esquema:
```python
class Meta:
    managed = False
    db_table = '"ventas"."cliente"'  # Schema explícito, no depende de search_path
```

#### 2. **Base de Datos es el Custodio de Lógica**

- **Triggers & Stored Procedures**: Postgres resuelve validaciones, cálculos y auditoria
  - `core.intentar_login()` → Valida password, bloqueos por intentos fallidos
  - `inventario.aplicar_movimiento()` → Trigger AFTER INSERT que actualiza stock
  - `ventas.validar_cantidad_facturable()` → Trigger BEFORE INSERT que valida límites
  - `core.set_updated_at()` → Trigger BEFORE UPDATE que mantiene timestamps

- **Django NO hace migraciones**: La DB es versionada afuera (en SQL scripts, control de versión manual)
  - `managed = False` en todos los models
  - Inspectdb genera models una sola vez (momento 0)
  - Future changes: SQL DDL scripts en la pipeline de infra, no en Django

#### 3. **Autenticación Descentralizada en Postgres**

```
Client → POST /api/auth/login/ → Django calls core.intentar_login() → Postgres
                                    ↓
                           Postgres validates:
                           • Hash vs password_hash
                           • Bloqueo por intentos fallidos
                           • Usuario.estado = 'activo'
                           • Tenant.estado = 'activo'
                                    ↓
                           Django issues JWT if ok=true
```

- **No hay `django.contrib.auth`**: Todo JWT, sin sesiones en cookies
- **Token** (contrato definitivo, Sprint 5): Payload = `{sub, tid, jti, iat, exp}`,
  firmado HS256. `sub`=usuario, `tid`=tenant slug, `jti`=id de sesión. El JWT solo
  **identifica**; no lleva permisos, roles ni estado — todo dato dinámico se
  resuelve desde la DB en cada petición.
- **Sesión persistida (`core.sesion`) = fuente de verdad**: el login inserta una
  sesión y `session_service` es su **único gestor** (crear, validar, listar,
  revocar). El `jti` liga token y sesión.
- **Logout y revocación (RF-17 / RF-19)**: revocar = escribir `revocada_en` +
  `revocada_por`; el middleware ya hace que el token dé 401 de inmediato. El
  middleware expone `request.session_jti` (identidad de sesión; sigue siendo
  solo-lectura). Endpoints:
  - `POST /api/auth/logout/` — cierra la sesión en curso (idempotente).
  - `GET /api/core/sesiones/` — el usuario lista **solo las suyas** (RN01), con
    flag `actual`.
  - `DELETE /api/core/sesiones/<jti>/` — cierra una sesión propia (CA02).
  - `POST /api/core/sesiones/cerrar-otras/` — cierra todas menos la actual (CA03).
  - `POST /api/core/usuarios/<pk>/cerrar-sesiones/` — el TENANT_ADMIN
    (`core:sesiones:revocar`) cierra **todas** las de un usuario; devuelve solo
    el conteo, **sin detalle** de dispositivos (RN02, privacidad).
  - **Desviación documentada (RF-19/CA01)**: `ultima_actividad` = `iniciada_en`
    (no se rastrea la actividad real) para no convertir el middleware en escritor
    ni ensuciar la bitácora de RF-20 con un UPDATE por minuto y sesión. La
    ubicación por IP (narrativa, no en CA01) queda fuera: exigiría GeoIP.
- **Recuperar / restablecer contraseña (RF-18)**: `POST /api/auth/recuperar/`
  (público) SIEMPRE responde el mismo mensaje genérico (RN02: no revela si el
  correo existe) y **no** devuelve el token — se encola por correo (RF-25). El
  token de un solo uso (1 h, RN01) se guarda hasheado en `token_activacion`, la
  misma columna de acción-de-un-solo-uso (limitación: una acción pendiente a la
  vez). `POST /api/auth/restablecer/` consume el token, fija la contraseña vía
  `crypt` en la DB y **revoca todas las sesiones previas** (RN03/CA03). Token
  expirado o reusado → mismo error (CA02).
- **RF-09** (`GET /api/core/me/`): expone `usuario.ultimo_acceso` (CA03), tomado
  del último evento `LOGIN` de la bitácora; nunca expone contraseña/hash/tokens
  (CA02); roles vigentes (CA04).
- **RF-22 políticas de seguridad** (`GET`/`PATCH`/`PUT /api/core/config-seguridad/`,
  permisos `core:politicas:leer` / `:editar`): el TENANT_ADMIN endurece su política
  dentro de los **límites de plataforma** (`config_service.LIMITES`; ej. longitud
  mínima de contraseña 8-128) — fuera de rango → 422 con el límite (RN01/CA01). El
  cambio **no** invalida retroactivamente contraseñas ni sesiones (RN02/CA02): la
  política de contraseña se lee al fijarla y la expiración al emitir la sesión, no
  después. La auditoría de valores anterior/nuevo (CA03) la da el trigger de RF-20
  sobre el UPDATE. Sin esquema nuevo (la tabla ya existía).
- **RF-06 directorio** (`GET /api/core/usuarios/`, permiso `core:usuarios:leer`):
  paginado y **siempre acotado al tenant** del JWT (RN01/CA05). Búsqueda por
  nombre/correo/puesto (CA02), filtros por estado/rol/departamento/rango de alta
  (CA03), orden por nombre/alta/último acceso (CA04, `ultimo_acceso` anotado
  desde los eventos `LOGIN`), mensaje de vacío (CA07). Se añadieron
  `usuario.puesto` y `usuario.departamento` (opcionales; el admin los fija en
  alta/edición). **Desviación (RN03/CA06)**: no hay enmascaramiento por política
  de privacidad ni auditoría condicional de lectura — el esquema de Fase 0 no
  tiene esa configuración (sería RF-22); se devuelven los campos a quien tiene
  el permiso y la lectura no se audita (default de RF-20).
- **RF-08 suspender/reactivar** (`POST /api/core/usuarios/<pk>/suspender/` y
  `/reactivar/`, permiso `core:usuarios:suspender`): suspender pone
  `estado='suspendido'`, **cierra todas las sesiones** (RN03/CA02, vía
  `session_service`) y bloquea el login (el validador devuelve el mensaje de
  CA03); no puede suspenderse al **último TENANT_ADMIN activo** (RN04, mismo
  criterio que RF-15). Reactivar conserva los roles (RN05). **Fuera de alcance
  (la ERS lo difiere al Módulo de Workflow, Fase 1)**: RN06/CA07, bloquear las
  aprobaciones pendientes del suspendido — no existe entidad de aprobación ni la
  bandera `is_blocked_by_suspension` en el esquema de Fase 0.
- **Middleware**: `JWTCustomMiddleware` responde una sola pregunta — ¿la sesión
  sigue viva? Valida firma+exp del token y luego `session_service.sesion_valida(jti)`;
  una sesión revocada o expirada en la DB invalida el token aunque su `exp`
  cripto no haya pasado. Expone `request.usuario_id` (`sub`) y `request.tenant_slug`
  (`tid`). Sin reglas de negocio, permisos ni auditoría.
- **Expiración configurable por tenant**: `core.sesion.expira_en` y el `exp` del
  JWT salen de `config_seguridad_tenant.jwt_expiracion_horas` (default 8). RF-16
  la **lee**; editarla es RF-22.
- **Login de dos fases con segundo factor (RF-16)**: `auth_service` es el único
  orquestador de ambas fases.
  - **Fase 1 — `POST /api/auth/login/`** (`autenticar`): valida password (SQL
    puro). Si es correcto **no** emite sesión: devuelve un *reto OTP* efímero
    (JWT ~5 min, `typ:"otp_challenge"`, **sin fila en `core.sesion`** → el
    middleware nunca lo acepta). Si el usuario no tiene segundo factor,
    lo **provisiona** (genera secreto TOTP, lo cifra y devuelve `secret` +
    `otpauth_uri` una sola vez).
  - **Fase 2 — `POST /api/auth/otp/`** (`validar_otp`): verifica el código TOTP
    contra el secreto; si es correcto confirma el enrolamiento pendiente,
    resetea el contador, crea la sesión y emite `LOGIN`. El token de sesión se
    entrega **aquí**.
  - **TOTP** (`core/utils/totp.py`): RFC 6238 con solo stdlib (`hmac`,
    `hashlib`, `base64`, `struct`, `time`), SHA1/6 dígitos/30 s, tolerancia ±1
    paso. El secreto se guarda cifrado (`core/utils/secretos.py`, Fernet de la
    `cryptography` ya presente; clave derivada de `SECRET_KEY`).
  - **Estados de MFA** en columnas existentes: `mfa_secret NULL` → sin enrolar;
    `mfa_secret` presente + `mfa_enrolado=False` → provisionado sin confirmar;
    ambos → listo. Sin tabla ni columna nuevas.
- **Eventos de autenticación (un solo mecanismo)**: `LOGIN`, `LOGIN_FAILED` y
  `ACCOUNT_LOCKED` se emiten desde el orquestador con un único helper, como
  `INSERT` en `core.log_auditoria` (eventos de dominio que el trigger CUD no
  cubre). `LOGIN` alimenta `v_actividad_usuarios` (último acceso, RF-06/RF-23).
- **Bloqueo (RF-16/RN02)**: `core.registrar_intento_fallido` (contador 5 / 30 min
  por `config_seguridad_tenant`) devuelve el bloqueo resultante; si este intento
  lo disparó, el orquestador emite `ACCOUNT_LOCKED` (criticidad **ALTA**) y
  **encola** una `Notificacion` de alerta (`pendiente`; la entrega es RF-25, no
  implementada). **Los fallos de password (fase 1) y de OTP (fase 2) alimentan
  el mismo contador** — una sola fuente de verdad, sin contadores paralelos.

##### Desviaciones documentadas de la ERS — RF-16

1. **RN07 — enrolamiento en el primer login, no en la activación.** RN07 sitúa
   el enrolamiento MFA en la activación (RF-01/RF-05). No se hizo así: los 29
   usuarios existentes y el admin sembrado no tienen secreto, y se descartó una
   migración masiva. En su lugar, cualquier usuario sin `mfa_secret` se enrola
   automáticamente en su siguiente login, **por el mismo camino y sin
   excepciones** (el admin incluido). Coste: el primer enrolamiento es
   *trust-on-first-use* — un atacante que ya tenga la contraseña podría enrolar
   su propio autenticador. Es inherente a enrolar en login vs. activación.
2. **Flujo 3a — "exactamente 3 intentos de OTP".** No se implementa un contador
   por-desafío (habría exigido una tabla nueva, descartada). El reto caduca por
   tiempo (~5 min) y los fallos de OTP cuentan al **bloqueo global** de RN02
   (5 intentos). El límite global es más estricto, así que ninguna CA
   obligatoria (CA01-CA04) se incumple.
3. **Reseteo de MFA (RN07, 2ª mitad)** — pérdida de dispositivo. Implementado
   como `POST /api/core/usuarios/<pk>/reset-mfa/`, acción **exclusiva del
   TENANT_ADMIN** (permiso `core:usuarios:reset_mfa`, sin el bypass de
   auto-edición: un usuario no se resetea el MFA a sí mismo). El reset pone
   `mfa_secret=NULL` + `mfa_enrolado=False`; **no persiste ningún "token de
   re-enrolamiento"** — la máquina de estados de RF-16 ya fuerza el re-enrolamiento
   en el próximo login (mfa_secret NULL → reto de enrolamiento antes de completar
   el acceso), cumpliendo RN07 sin almacenamiento nuevo. Las sesiones vivas no se
   revocan (eso es RF-17/RF-19); solo el próximo login exige el nuevo factor.

Con el reseteo implementado, **RF-16 queda Completo (con las desviaciones 1 y 2
documentadas arriba)**: todos sus CA y su RN07 funcional se cumplen.

#### 3b. **Autorización RBAC (RF-10 a RF-15)**

El JWT **no autoriza**: solo identifica usuario y tenant. Los permisos se
resuelven contra la base de datos en **cada petición**, porque RF-12/RN01 exige
que un cambio de permisos surta efecto en la siguiente petición sin esperar a
que expire el token. Los permisos que devuelve `/api/core/me/` son informativos
para pintar la UI.

```python
from core.utils.permissions import PermissionRequiredMixin

class ProductoListCreateView(CatalogListCreateView):
    permisos = {
        "GET":  "inventario:productos:leer",
        "POST": "inventario:productos:crear",
    }
```

- **Un solo mecanismo**: `PermissionResolver` resuelve el conjunto efectivo con
  **una consulta por petición**, cacheada en el `request`.
  `PermissionRequiredMixin` lo aplica a las vistas y `exigir_permiso(request,
  codigo)` a los servicios, cuando el permiso depende de los datos.
- **Falla cerrado**: un método de `GET/POST/PUT/PATCH/DELETE` sin permiso
  declarado responde 403. Olvidar la declaración cierra el endpoint, no lo abre.
  Los endpoints que la ERS define para "todos los usuarios autenticados"
  (`/me/`) usan `LoginRequiredMixin` directamente.
- **Bypass de rol de sistema**: un rol con `es_sistema=True` (TENANT_ADMIN)
  autoriza todo sin filas en `rol_permiso`, porque RF-12/RN02 lo define como rol
  protegido que no puede perder permisos. El bypass **no** atraviesa el
  aislamiento multi-tenant: la consulta acota los roles a `r.tenant_id`.
- **Permisos inertes**: un permiso cuyo módulo está desactivado en el tenant se
  excluye del conjunto efectivo, pero su fila en `rol_permiso` se conserva
  (RF-10/RN04). La regla vive en `codigos_aplicables()`, que consumen tanto el
  resolutor como la validación de alta y edición de roles.
- Nomenclatura `dominio:recurso:accion`. El dominio `core` es el núcleo
  (Módulos 1-7) y nunca queda inerte; el resto mapea a `core.modulo` por
  `upper(dominio)`. El catálogo vive en
  `sql/2026-07-24_rf10_rbac_catalogo.sql`.

##### Desviación documentada de la ERS — RF-13/CA02

La CA02 de RF-13 afirma que, al desactivar un rol, los usuarios que ya lo
tenían *"conservan sus permisos activos de forma inerte y pueden seguir
operando con normalidad"*.

**El sistema implementa lo contrario: un rol inactivo no concede permisos.**

La redacción de la ERS es inconsistente con el propio documento y con el modelo
de seguridad:

- Haría que desactivar un rol no tuviera ningún efecto de seguridad.
- Contradice RF-12/RN01, que exige que un cambio de permisos surta efecto en la
  siguiente petición sin esperar a que expire el JWT.
- Viola el principio de menor privilegio: un usuario conservaría acceso
  indefinidamente pese a que su rol fue dado de baja.

La primera mitad de la CA02 sí se cumple: un rol inactivo no puede asignarse a
nuevos usuarios, y las filas de `usuario_rol` no se borran, de modo que la baja
es reversible reactivando el rol. La tercera parte (exigir al TENANT_ADMIN
remover el rol inactivo la próxima vez que edite a ese usuario) corresponde a
RF-07.

Esta desviación es deliberada y está aprobada; debe reconciliarse en la próxima
revisión de la ERS.

#### 4. **Aislamiento de Tenant Obligatorio**

Cada usuario tiene `request.tenant_slug` del JWT. Toda query se filtra:

```python
# ❌ INCORRECTO
cliente = Cliente.objects.get(id=id)  # Riesgo: otros tenants ven esto

# ✅ CORRECTO
cliente = tenant_scoped(Cliente.objects.all(), request).get(id=id)
# Equivale a: Cliente.objects.get(id=id, tenant__slug=request.tenant_slug)
```

Un id de otro tenant responde `404`, no `403` (no confirma existencia entre tenants).

#### 5. **Soft Deletes (Baja Lógica)**

Nunca se borra físicamente. Todo registro marcable tiene `activo: BOOLEAN`.

```python
# Baja lógica de cliente
cliente.activo = False
cliente.save(update_fields=['activo'])

# No aparecerá en ?activo=true pero conserva historial y referencias
```

#### 6. **Auditoría a Nivel de Trigger (RF-20)**

`core.fn_auditar()` está instalado como trigger AFTER INSERT/UPDATE/DELETE en las
**37 tablas** de los esquemas `core`, `ventas`, `compras`, `inventario` y
`finanzas`. Escribe en `core.log_auditoria` los 7 campos obligatorios del ERS:
timestamp, tenant, usuario responsable, IP de origen, operación, entidad +
identificador, y el payload diferencial.

El trigger no puede deducir por sí solo el responsable, la IP ni el tenant de
las tablas de detalle, así que la aplicación los publica por transacción:

```python
from core.utils.audit import audit_context

with audit_context(request, tenant_id=tenant.id):
    Cliente.objects.create(...)
```

`audit_context` **sustituye a `transaction.atomic()`** en todos los servicios de
escritura: abre la transacción y publica `app.current_user_id`,
`app.current_tenant_id` y `app.current_ip` con `set_config(..., is_local=true)`,
de modo que el contexto muere con la transacción y no se filtra a la siguiente
petición que reutilice la conexión. Una escritura sin este contexto se audita
igual, pero con esos campos en NULL.

`core.fn_redactar()` enmascara `password_hash`, `mfa_secret`,
`token_activacion` y `jwt_id` como `[REDACTED]` antes de escribirlos.
**Limitación:** las filas anteriores al 2026-07-23 contienen esos valores sin
enmascarar y no pueden corregirse — la tabla es append-only por diseño.

**Residual del login — cerrado en el Sprint 5 · Paso 2a.** `core.intentar_login`
es ahora un **validador puro**: valida credenciales y devuelve un código de
resultado, sin modificar estado. El conteo de intentos
(`registrar_intento_fallido` / `reset_intentos_fallidos`) lo invoca el servicio
de autenticación **dentro del `audit_context` y con `set_audit_user(uid)` ya
fijado**, así que esas `UPDATE` quedan atribuidas con usuario, tenant e IP. Para
un usuario/tenant inexistente no hay ninguna escritura. Con esto **RF-20 es
Completo**: toda operación CUD nueva lleva los 7 campos de RN01.

Caveat de datos históricos (no corregible, append-only): las filas escritas
antes de esta migración —hashes bcrypt sin enmascarar (pre-2026-07-23) y
`UPDATE` de login sin actor (pre-Paso 2a)— permanecen como estaban. La bitácora
prohíbe UPDATE/DELETE por diseño; el criterio de RF-20 se cumple hacia adelante.

### Capas de la Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│  HTTP/REST Clients (Frontend, Postman, etc)                 │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP + JWT en Authorization header
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Django WSGI (runserver / gunicorn)                          │
│  ├─ JWTCustomMiddleware (inyecta usuario_id, tenant_slug)   │
│  ├─ CORS Middleware                                         │
│  └─ CsrfViewMiddleware (csrf_exempt en /api/auth y catálogos)
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  URLconf (novaerp_backend/urls.py)                          │
│  ├─ core/urls.py          → /api/auth/*, /api/core/{me,usuarios,roles,permisos}/
│  ├─ ventas/urls.py        → /api/ventas/clientes/
│  ├─ inventario/urls.py    → /api/inventario/{productos,almacenes}/
│  ├─ compras/urls.py       → /api/compras/proveedores/
│  └─ finanzas/urls.py      → /api/finanzas/... (futuro)
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Views (CBV con LoginRequiredMixin, @csrf_exempt)           │
│  ├─ LoginView              → POST login, emite JWT          │
│  ├─ MeView                 → GET perfil + contexto          │
│  ├─ PermissionRequiredMixin → 401 sin sesion, 403 sin permiso│
│  ├─ CatalogListCreateView  → GET listado, POST creación     │
│  └─ CatalogDetailView      → PATCH edición, DELETE baja     │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Services (Lógica de Negocio)                               │
│  ├─ core/services/auth_service.py                          │
│  ├─ ventas/services/catalogo_service.py                    │
│  ├─ inventario/services/catalogo_service.py                │
│  └─ compras/services/catalogo_service.py                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Models (managed=False)                                      │
│  └─ Mapean 1:1 a tablas existentes en Postgres              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  PostgreSQL Database (Fuente de Verdad)                     │
│  ├─ 10 Esquemas (core, ventas, inventario, compras, ...)   │
│  ├─ Triggers (validación, auditoria, stock)                │
│  ├─ Stored Procedures (intentar_login, aplicar_movimiento) │
│  └─ Log de Auditoría (inmutable, audit-trail)              │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Frontend — React 18 + TypeScript + Zustand

### Stack Tecnológico

| Componente | Tecnología | Versión | Propósito |
|---|---|---|---|
| **Framework** | React | 18+ | UI componentes, estado reactivo |
| **Lenguaje** | TypeScript | 5+ | Type-safe JavaScript |
| **Bundler** | Vite | 4+ | Build rápido, HMR dev server |
| **State Mgmt** | Zustand | 4+ | Global state (auth, tenant, sesión) |
| **Data Fetching** | React Query (TanStack) | 4+ | Caching, sync, refetch automático |
| **HTTP Client** | Axios | - | Request lib con interceptores JWT |
| **UI Routing** | React Router | 6+ | Navegación SPA con auth guards |
| **CSS** | Tailwind CSS o styled-components | - | Styling componentes |
| **Forms** | React Hook Form | - | Form validation, estado de formularios |
| **Progressive** | PWA | - | Offline-capable, installable |

### Arquitectura — Principios Clave

#### 1. **Autenticación Descentralizada en el Cliente**

```
Client (Browser)
  ├─ useAuthStore (Zustand)
  │  ├─ token (JWT, localStorage)
  │  ├─ usuario { id, nombre, correo, mfa_enrolado }
  │  ├─ tenant { slug, razon_social, plan }
  │  ├─ roles []
  │  ├─ permisos []
  │  └─ modulos [] (para menú dinámico)
  │
  └─ axios interceptor
     ├─ Cada request: agrega header Authorization: Bearer <token>
     └─ Cada 401 (token expirado): logout() + redirect /login
```

#### 2. **Flujo de Autenticación**

```
1. User entra a /login
2. Form POST /api/auth/login/ { tenant_slug, correo, password }
3. Backend: Postgres valida credenciales
4. Backend: Emite JWT
5. Frontend: Guarda JWT en localStorage + useAuthStore.token
6. Frontend: Fetcha GET /api/core/me/ con JWT
   → Recibe: { usuario, tenant, roles, permisos, modulos }
7. Frontend: Renderea sidebar dinámico desde modulos[]
8. Frontend: User puede navegar /ventas, /inventario, /compras según modulos
9. Si JWT expira: Axios interceptor logea out + /login
```

#### 3. **State Management con Zustand**

```typescript
// src/stores/authStore.ts
interface AuthStore {
  token: string | null;
  usuario: Usuario | null;
  tenant: Tenant | null;
  roles: string[];
  permisos: Permiso[];
  modulos: Modulo[];
  
  login: (tenant_slug, correo, password) => Promise<void>;
  logout: () => void;
  hydrate: () => void;  // Carga token de localStorage en app load
}

// Uso
const { token, usuario, login, logout } = useAuthStore();
```

- **Persistencia**: Token guardado en `localStorage`, sincronizado con `hydrate()` al montar
- **Global**: Accesible desde cualquier componente sin prop drilling

#### 4. **Data Fetching con React Query**

```typescript
// src/api/useProductos.ts
function useProductos(page = 1, search = "") {
  return useQuery(
    ["productos", page, search],
    () => axios.get("/api/inventario/productos/", {
      params: { page, search }
    }),
    {
      staleTime: 5 * 60 * 1000,  // 5m
      cacheTime: 10 * 60 * 1000, // 10m
      refetchOnWindowFocus: false,
      enabled: !!token,  // Solo fetch si autenticado
    }
  );
}

// Uso en componente
const { data, isLoading, error } = useProductos(1, "LAP");
```

- **Caché automático**: Mismo query key reutiliza datos sin refetch
- **Sincronización**: Si algo cambia, `invalidateQueries` refetcha
- **Optimista**: `useMutation` con `onMutate` actualiza UI antes de respuesta

#### 5. **Estructura de Carpetas (Scalable)**

```
src/
├── pages/
│   ├── Login.tsx
│   ├── Dashboard.tsx
│   ├── ventas/
│   │   ├── ClienteListPage.tsx
│   │   ├── ClienteFormPage.tsx
│   │   └── ClienteDetailPage.tsx
│   ├── inventario/
│   │   ├── ProductoListPage.tsx
│   │   └── AlmacenListPage.tsx
│   └── compras/
│       └── ProveedorListPage.tsx
│
├── components/
│   ├── common/
│   │   ├── Navbar.tsx
│   │   ├── Sidebar.tsx
│   │   ├── Layout.tsx
│   │   └── ProtectedRoute.tsx
│   ├── forms/
│   │   ├── ProductoForm.tsx
│   │   ├── ClienteForm.tsx
│   │   └── ProveedorForm.tsx
│   └── tables/
│       ├── ProductosTable.tsx
│       ├── ClientesTable.tsx
│       └── DataTable.tsx (reutilizable)
│
├── stores/
│   ├── authStore.ts        # Zustand: usuario, token, logout
│   ├── uiStore.ts          # UI state (sidebar abierto/cerrado, etc)
│   └── queryClient.ts      # Config global de React Query
│
├── api/
│   ├── client.ts           # Axios instance + interceptores
│   ├── auth.ts             # POST /api/auth/login/, GET /api/core/me/
│   ├── productos.ts        # CRUD productos
│   ├── clientes.ts         # CRUD clientes
│   └── proveedores.ts      # CRUD proveedores
│
├── hooks/
│   ├── useAuth.ts          # useAuthStore wrapper
│   ├── useProductos.ts     # useQuery + queryFn para productos
│   ├── useClientes.ts      # useQuery + queryFn para clientes
│   └── useForm.ts          # react-hook-form wrapper
│
├── types/
│   ├── api.ts              # Tipos de request/response de la API
│   ├── models.ts           # Usuario, Tenant, Producto, etc.
│   └── forms.ts            # FormData types
│
├── utils/
│   ├── formatters.ts       # Formatear decimales, fechas
│   ├── validators.ts       # Validar RFC, email, etc.
│   └── constants.ts        # Enums, mensajes de error
│
├── App.tsx                 # Router + ProtectedRoute wrapper
├── main.tsx                # Vite entry point
└── index.css               # Tailwind / global styles
```

#### 6. **Sidebary Dinámico desde Modulos**

```typescript
// src/components/common/Sidebar.tsx
function Sidebar() {
  const { modulos } = useAuthStore();
  
  return (
    <aside>
      {modulos
        .filter(m => [0, 1].includes(m.fase))  // Fase 0 + Fase 1
        .map(mod => (
          <NavLink key={mod.codigo} to={`/${mod.codigo.toLowerCase()}`}>
            {mod.nombre}
          </NavLink>
        ))}
    </aside>
  );
}
```

- Dinámico: Si backend activa nuevo módulo, frontend lo muestra sin redeploy
- Seguro: Requiere JWT + check backend (no hay XSS si confíamos en backend)

#### 7. **Manejo de Errores Multiforma**

Backend puede devolver dos formas de error (documentado en OpenAPI):

```typescript
// /api/auth/login/
{ "mensaje": "Credenciales incorrectas" }  // ⚠️ Clave "mensaje"

// Todos los demás
{ "detail": "No encontrado" }              // Clave "detail"
{ "detail": "SKU ya existe...", "campo": "sku" }  // Con campo
```

Frontend maneja ambas:

```typescript
function getErrorMessage(error: AxiosError): string {
  const data = error.response?.data as any;
  return data?.mensaje || data?.detail || "Error desconocido";
}
```

---

## 3. Flujo End-to-End: Crear un Producto

### Backend (Django + Postgres)

```
POST /api/inventario/productos/
Authorization: Bearer <JWT>
Content-Type: application/json

{
  "sku": "LAP-001",
  "nombre": "Laptop 15",
  "precio_venta": "13500",
  "costo_referencia": "9000",
  "stock_minimo": "5"
}
```

1. **JWTCustomMiddleware**: Decodifica JWT → `request.usuario_id`, `request.tenant_slug`
2. **LoginRequiredMixin**: Valida que ambos existan → 401 si no
3. **ProductoListCreateView.post()**:
   - Parsea JSON
   - Llama `inventario.services.crear_producto(data, request)`
4. **crear_producto()**:
   - `get_tenant(request)` → Resuelve Tenant object desde `request.tenant_slug`
   - Valida: sku unique por tenant, precio >= 0, nombre requerido
   - Genera `uuid.uuid4()` para ID
   - Genera `timezone.now()` para created_at/updated_at
   - `transaction.atomic()` → INSERT en `inventario.producto`
   - Devuelve producto objeto
5. **Response**: 201 + JSON serializado del producto

### Frontend (React)

```
1. User navega a /inventario/productos/
2. ProductosListPage carga (protegido por ProtectedRoute)
   - useProductos() → useQuery fetch GET /api/inventario/productos/
   - Renderiza tabla + botón "Nuevo"
3. User hace clic "Nuevo" → Modal/Form
4. Form local (react-hook-form)
   - Validación cliente: sku no vacío, precio >= 0
5. User hace clic "Guardar"
   - useMutation POST /api/inventario/productos/ + payload
   - Optimista: agrega fila a tabla local
   - Espera respuesta 201
   - Si 422 (SKU duplicado): `campo: "sku"` → resalta campo + error
   - Si 200: invalidateQueries(["productos"]) → refetch
6. Tabla actualizada, modal cierra
```

---

## 4. Patrones & Mejores Prácticas

### Backend

| Patrón | Implementación | Por qué |
|---|---|---|
| **Tenant Isolation** | `tenant_scoped(qs, request)` en cada queryset | Prevenir cross-tenant leaks |
| **Autorización** | `permisos = {...}` + `PermissionRequiredMixin` | Un solo motor, falla cerrado |
| **Soft Deletes** | `activo=False` nunca borra | Historial auditable |
| **Transactions** | `transaction.atomic()` en creates | ACID compliance |
| **Parameterized SQL** | `cursor.execute("...", [params])` | Prevent SQL injection |
| **Business Rule Errors** | `BusinessRuleError(detail, campo)` → 422 | Validación clara al FE |
| **No Migrations** | `managed=False` en todos los models | DB es el source of truth |
| **Triggers for Audit** | `fn_auditar()` dispara automático | Auditoria sin código Python |

### Frontend

| Patrón | Implementación | Por qué |
|---|---|---|
| **Protected Routes** | `<ProtectedRoute>` wrapper | Auth gates automático |
| **Request Interception** | Axios interceptor con JWT | No duplicar auth code |
| **Optimistic Updates** | `useMutation` con `onMutate` | UX responsivo |
| **Query Invalidation** | `invalidateQueries` after mutation | Data always in sync |
| **Type Safety** | TypeScript types para API | Catch errors at build time |
| **Error Boundaries** | React error boundary + fallback | Graceful degradation |
| **Lazy Loading** | `React.lazy()` + `Suspense` | Fast initial load |

---

## 5. Deployment & DevOps (Roadmap)

### Backend (Django)
- **Local**: `python manage.py runserver` → port 8000
- **Production**: Gunicorn + reverse proxy (Nginx)
- **Database**: PostgreSQL managed (RDS/Cloud SQL/Docker)
- **Secrets**: `.env` + container env vars, nunca en repo

### Frontend (React)
- **Local**: Vite dev server → HMR
- **Build**: `npm run build` → `/dist` estático
- **Hosting**: S3 + CloudFront / Vercel / Netlify
- **Secrets**: API base URL from `process.env.VITE_API_BASE_URL`

---

## 6. Seguridad (Arquitectura)

| Layer | Mecánica | Responsable |
|---|---|---|
| **Transport** | HTTPS only (cert + TLS 1.3+) | Infra |
| **Auth** | JWT (HS256) + 8h expiry, no refresh yet | Backend + Frontend |
| **Tenant Isolation** | FK + filter per tenant_slug en cada query | Backend (ORM + services) |
| **CSRF** | `csrf_exempt` en `/api/*` (JWT no vulnerable) | Django settings |
| **SQL Injection** | Parameterized queries always | Backend developers |
| **XSS** | React auto-escapes + CSP headers | Frontend + Infra |
| **Rate Limit** | Rate limiter middleware (future) | Infra |
| **Audit** | DB trigger log_auditoria | Postgres trigger |

---

## 7. Monitoreo & Observabilidad (Roadmap)

- **Logs**: Python logging → CloudWatch / ELK Stack
- **Metrics**: Prometheus + Grafana (request latency, error rates)
- **Tracing**: Jaeger / Datadog (distributed tracing across services)
- **Uptime**: Pingdom / Datadog monitors
- **DB Health**: Postgres slow query log + pg_stat_statements

---

## 8. CI/CD Pipeline (Roadmap)

```yaml
GitHub Actions / GitLab CI
├─ Lint & Format
│  ├─ Backend: flake8, black, isort
│  └─ Frontend: ESLint, Prettier
├─ Type Check
│  ├─ Backend: mypy (optional)
│  └─ Frontend: tsc
├─ Tests
│  ├─ Backend: pytest
│  └─ Frontend: Vitest + React Testing Library
├─ Security
│  ├─ Backend: bandit, safety
│  └─ Frontend: npm audit
└─ Build & Deploy
   ├─ Backend: Docker build + push ECR
   └─ Frontend: npm build + S3/CDN deploy
```

---

## Conclusión

NovaERP es una arquitectura **moderna, escalable, con seguridad como primera clase**:

- **Backend**: Django + Postgres con lógica de negocio en triggers/procedures
- **Frontend**: React + TypeScript con estado global (Zustand) + caché (React Query)
- **Autenticación**: JWT sin sesiones, tenant-scoped queries en cada request
- **Database**: Es el custodio; Django solo lee (managed=False, no migrations)
- **Auditoría**: Triggers de Postgres, inmutable log_auditoria

Esta arquitectura permite:
✅ Escalabilidad horizontal (stateless Django)
✅ Múltiples tenants con aislamiento seguro
✅ Auditoría 100% (triggers garantizan)
✅ API-first (OpenAPI 3.0 spec disponible)
✅ Type-safe frontend (TypeScript + REST codegen)
