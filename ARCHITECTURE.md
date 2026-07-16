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
- **Token**: Payload = `{usuario_id: UUID, tenant_slug: str, exp: int}`, firmado HS256
- **Middleware**: `JWTCustomMiddleware` inyecta `request.usuario_id` y `request.tenant_slug` en cada request

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

#### 6. **Auditoría a Nivel de Trigger**

Postgres trigger `fn_auditar()` en cada UPDATE/DELETE:

```sql
INSERT INTO core.log_auditoria (
  usuario_id, tabla, registro_id, accion, valores_anterior, valores_nuevo
) VALUES (...);
```

Django no tiene que ocuparse de ello — el trigger dispara automático.

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
│  ├─ core/urls.py          → /api/auth/login/, /api/core/me/
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
