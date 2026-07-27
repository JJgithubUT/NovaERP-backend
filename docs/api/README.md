# NovaERP — Documentación de API

Punto de entrada a la documentación para los equipos de **frontend** y **app móvil**.
Empieza por aquí y sigue el orden sugerido.

---

## 📍 Por dónde empezar

1. **[GUIA-APP.md](./GUIA-APP.md)** — *Empieza aquí.* Visión integral de la aplicación
   (arquitectura multi-tenant, autenticación, permisos/roles, convenciones, flujos de
   cada módulo y consideraciones específicas para móvil).
2. **[GUIA-FRONTEND.md](./GUIA-FRONTEND.md)** — El **contrato transversal** en detalle:
   login de dos fases + MFA, uso del token, errores, paginación, enumeraciones. Es la
   referencia rápida del día a día.
3. **[openapi.yaml](./openapi.yaml)** — La **referencia técnica** de todos los endpoints
   (98 operaciones). Ábrela en Swagger UI / Redoc, impórtala a Postman, o genera un
   cliente tipado (TypeScript / Kotlin / Swift).
4. **[../../test.http](../../test.http)** — Ejemplos **ejecutables** de cada endpoint
   (extensión REST Client de VS Code).

---

## 📚 Guías de flujo por dominio

Explican el **funcionamiento** (ciclo de vida, máquinas de estado, qué permiso gatea
cada acción), con diagramas:

- **[FLUJO-USUARIOS.md](./FLUJO-USUARIOS.md)** — alta, activación, MFA, gestión por el
  admin, autoservicio, roles y sesiones. *(También hay una versión HTML navegable.)*
- **[FLUJO-VENTAS.md](./FLUJO-VENTAS.md)** — oportunidad → cotización → pedido (reserva de
  stock y crédito) → factura → nota de crédito.
- **[FLUJO-COMPRAS.md](./FLUJO-COMPRAS.md)** — proveedor → orden (umbral de aprobación) →
  recepción (suma stock + cuenta por pagar).
- **[FLUJO-INVENTARIO.md](./FLUJO-INVENTARIO.md)** — movimientos, ajustes, transferencias,
  stock disponible, kardex, valuación y alertas.
- **[FLUJO-PLATAFORMA.md](./FLUJO-PLATAFORMA.md)** — SysAdmin: alta y administración de
  organizaciones (tenants), módulos y suspensión.

## 🧑‍🍳 Recetas (cookbook)

- **[RECETAS.md](./RECETAS.md)** — secuencias de llamadas end-to-end para los casos comunes:
  autenticarse, vender de punta a punta, dar de alta un empleado, comprar/reabastecer,
  onboarding de una organización.

---

## 🗺️ Mapa de la API

| Superficie | Prefijo | Documentado en |
|---|---|---|
| **App de tenant** | `/api/auth/`, `/api/core/`, `/api/ventas/`, `/api/compras/`, `/api/inventario/` | GUIA-APP · GUIA-FRONTEND · openapi.yaml |
| **Portal de plataforma (SysAdmin)** | `/api/admin/` | GUIA-APP §5.3 · openapi.yaml |

**Módulos con endpoints:** Núcleo (usuarios, roles, sesiones, seguridad, auditoría,
reportes), Plataforma (tenants), Ventas, Compras, Inventario.

---

## ⚡ Lo mínimo para arrancar

1. Implementa el **login de dos fases** (`/api/auth/login/` → `/api/auth/otp/`) y guarda
   el token.
2. Llama a **`/api/core/me/`** para armar el menú (por módulos) y las acciones (por permisos).
3. Trata cualquier **`401`** como fin de sesión → volver a login.
4. Lee montos/cantidades como **decimales**, nunca como float (llegan como string).

Usuario de prueba (base sembrada): organización `acme`, `admin@acme.com`.

---

## 🔧 Cómo abrir el OpenAPI

- **Swagger UI online:** pega el contenido de `openapi.yaml` en <https://editor.swagger.io>.
- **VS Code:** extensión *OpenAPI (Swagger) Editor* o *Redoc Preview*.
- **Postman / Insomnia:** *Import* → selecciona `openapi.yaml`.
- **Cliente tipado:** `openapi-typescript openapi.yaml -o api.d.ts` (TS), u `openapi-generator`.
