# NovaERP — Recetas (casos de uso end-to-end)

Secuencias completas de llamadas para los flujos más comunes. Cada receta indica
qué mandar y **qué capturar de cada respuesta** para la siguiente llamada. Todas
asumen `Authorization: Bearer <token>` salvo donde se indique “público”.

> Base: `http://localhost:8000` (dev). Detalle de cada endpoint en [openapi.yaml](./openapi.yaml).

---

## Receta 0 — Autenticarse y armar la sesión

```http
### 1. Fase 1 — password (público)
POST /api/auth/login/
{ "tenant_slug": "acme", "correo": "admin@acme.com", "password": "…" }
# → { reto, mfa, secret?, otpauth_uri? }   ← guarda `reto`
#   Si mfa="enroll": muestra el QR con `otpauth_uri` (primer login).

### 2. Fase 2 — código TOTP (público)
POST /api/auth/otp/
{ "reto": "{{reto}}", "codigo": "123456" }
# → { token }   ← guárdalo en almacenamiento seguro

### 3. Contexto para la UI
GET /api/core/me/     Authorization: Bearer {{token}}
# → { usuario, tenant, roles, es_admin, permisos[], modulos[] }
#   Pinta el menú con `modulos` y las acciones con `permisos`.
```

Regla: cualquier `401` posterior = sesión terminada → repite la Receta 0.

---

## Receta 1 — Vender de punta a punta

Cliente → cotización → aprobación → pedido → confirmación (reserva stock) →
factura → (nota de crédito).

```http
### 1. Cliente
POST /api/ventas/clientes/
{ "razon_social": "Cliente Uno", "rfc_o_id_fiscal": "XAXX010101000", "limite_credito": "500000" }
# → { id }   ← clienteId

### 2. Cotización (precio del catálogo)
POST /api/ventas/cotizaciones/
{ "cliente_id": "{{clienteId}}", "descuento_pct": "0",
  "lineas": [ { "producto_id": "{{productoId}}", "cantidad": "5" } ] }
# → { id, estado:"borrador", total }   ← cotizacionId

### 3. Aprobar la cotización
POST /api/ventas/cotizaciones/{{cotizacionId}}/resolver/
{ "decision": "aprobar" }
# → { estado:"aprobada" }

### 4. Pedido desde la cotización aprobada
POST /api/ventas/pedidos/
{ "cotizacion_id": "{{cotizacionId}}" }
# → { id, estado:"borrador" }   ← pedidoId

### 5. Confirmar (reserva stock en un almacén; valida crédito)
POST /api/ventas/pedidos/{{pedidoId}}/confirmar/
{ "almacen_id": "{{almacenId}}" }
# → { estado:"confirmado" }
#   Si excede el crédito → 422; reintenta con "autorizar_credito": true
#   (requiere permiso finanzas:credito:autorizar).
#   Sin stock y con backorder → "pendiente_surtido".

### 6. Facturar (parcial o total). Sin "lineas" factura todo lo pendiente.
POST /api/ventas/facturas/
{ "pedido_id": "{{pedidoId}}" }
# → { id, estado:"emitida", subtotal, impuestos, total, cxc_saldo }   ← facturaId
#   El pedido pasa a facturado_parcial / facturado_total; el stock sale.

### 7. (Opcional) Nota de crédito
POST /api/ventas/facturas/{{facturaId}}/nota-credito/
{ "motivo": "Devolución", "monto": "{{total}}", "reingresar_stock": true }
# → { estado:"con_nota_credito", cxc_saldo:"0.00" }
```

---

## Receta 2 — Dar de alta un empleado (usuario)

```http
### 1. El admin crea el usuario (nace 'pendiente')
POST /api/core/usuarios/
{ "correo": "empleado@acme.com", "nombre_completo": "Empleado Uno",
  "roles": ["{{rolId}}"], "puesto": "Vendedor", "departamento": "Ventas" }
# → { id, estado:"pendiente", activacion_token }   ← el token va por correo (o en dev, aquí)

### 2. El empleado activa su cuenta (público, con el token)
POST /api/auth/activar/
{ "token": "{{activacion_token}}", "password": "SuContraseña123" }
# → { estado:"activo" }

### 3. Primer login del empleado → enrola MFA (ver Receta 0, mfa="enroll")

### (Después) Ajustar roles
POST   /api/core/usuarios/{{id}}/roles/           { "roles": ["{{otroRolId}}"] }
DELETE /api/core/usuarios/{{id}}/roles/{{rolId}}/
```

Para crear el rol primero: `GET /api/core/permisos/` (catálogo) → `POST /api/core/roles/
{ nombre, permisos:[ "ventas:pedidos:crear", … ] }`.

---

## Receta 3 — Comprar y reabastecer inventario

```http
### 1. Proveedor
POST /api/compras/proveedores/
{ "rfc_o_id_fiscal": "PRO010101AAA", "razon_social": "Proveedor Uno" }
# → { id }   ← proveedorId

### 2. Orden de compra
POST /api/compras/ordenes/
{ "proveedor_id": "{{proveedorId}}",
  "lineas": [ { "producto_id": "{{productoId}}", "cantidad": "100", "costo_unitario": "50" } ] }
# → { id, estado }   ← ordenId
#   Si el total supera el umbral → estado "pendiente_aprobacion".

### 3. (Si aplica) Aprobar la orden → enviada
PATCH /api/compras/ordenes/{{ordenId}}/
{ "estado": "enviada" }

### 4. Recepción (suma stock + crea cuenta por pagar)
POST /api/compras/recepciones/
{ "orden_id": "{{ordenId}}", "almacen_id": "{{almacenId}}",
  "lineas": [ { "orden_compra_linea_id": "{{lineaId}}", "cantidad": "100" } ] }
# → { id }   La orden pasa a recibida_parcial/total; el stock del almacén sube 100.
```

---

## Receta 4 — Ajustar y transferir inventario

```http
### Ajuste por conteo físico (cantidad + o −)
POST /api/inventario/ajustes/
{ "motivo": "conteo_fisico", "producto_id": "{{productoId}}", "almacen_id": "{{almacenId}}", "cantidad": "-3" }

### Transferencia entre almacenes (atómica)
POST /api/inventario/transferencias/
{ "producto_id": "{{productoId}}", "almacen_origen_id": "{{a1}}", "almacen_destino_id": "{{a2}}", "cantidad": "10" }

### Consultar disponible
GET /api/inventario/stock-disponible/?producto_id={{productoId}}
# → results[] con { cantidad, reservado, disponible }
```

---

## Receta 5 — Onboarding de una organización (SysAdmin)

Superficie de **plataforma** (`/api/admin/`), token propio del SysAdmin.

```http
### 1. Login del SysAdmin (una fase)
POST /api/admin/login/
{ "correo": "root@novaerp.local", "password": "…" }
# → { token }   (typ:"sysadmin")

### 2. Registrar el tenant
POST /api/admin/tenants/     Authorization: Bearer {{tokenSysadmin}}
{ "slug": "nueva-org", "razon_social": "Nueva Org SA", "correo": "admin@nueva-org.com",
  "nombre_completo": "Admin Nueva Org", "plan": "BUSINESS" }
# → { id, admin_inicial, activacion_token }   ← el token va al admin inicial

### 3. El admin inicial activa (público — app de tenant, NO el SysAdmin)
POST /api/auth/activar-tenant/
{ "token": "{{activacion_token}}", "password": "ClaveDelAdmin123" }
# → { estado:"activo" }   El tenant y su admin quedan activos.

### 4. El admin inicial ya entra por la app de tenant (Receta 0, con tenant_slug="nueva-org")
```

---

## Errores comunes al encadenar

| Síntoma | Causa típica | Qué hacer |
|---|---|---|
| `401` a mitad del flujo | token expirado/revocado | rehacer la Receta 0 |
| `403` con `permiso_requerido` | al usuario le falta ese permiso | usar otra cuenta o ajustar el rol |
| `422` con `campo` | dato inválido o regla de negocio | corregir el campo señalado |
| Pedido no factura | no está confirmado, o nada pendiente/reservado | confirma primero; revisa `pendiente_facturar` |
| Recepción rechazada | orden en `pendiente_aprobacion` | aprobar la orden (→ `enviada`) antes |
