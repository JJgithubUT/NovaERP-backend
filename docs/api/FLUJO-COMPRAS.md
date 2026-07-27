# NovaERP — Flujo de la aplicación: Compras

Flujo del abastecimiento: proveedor → orden de compra → recepción de mercancía →
cuenta por pagar. La **recepción** es el paso que mueve el inventario y genera la
deuda automáticamente.

> Prerrequisitos: [GUIA-FRONTEND.md](./GUIA-FRONTEND.md) · Referencia: [openapi.yaml](./openapi.yaml).
> Todas las rutas cuelgan de `/api/compras/`.

---

## 1. La cadena de abastecimiento de un vistazo

```mermaid
flowchart LR
  PR[Proveedor] --> O[Orden de compra]
  O -->|total > umbral| PA[pendiente_aprobacion]
  PA -->|aprobar| EN[enviada]
  O -->|total <= umbral| EN
  EN -->|recepción| RP[recibida_parcial]
  RP -->|recepción| RT[recibida_total]
  EN -->|recepción total| RT
  RP --> CxP[Cuenta por pagar]
  RT --> CxP
```

---

## 2. Proveedores (catálogo base)

| Acción | Endpoint | Permiso |
|---|---|---|
| Listar / buscar | `GET /proveedores/` | `compras:proveedores:leer` |
| Registrar | `POST /proveedores/` | `compras:proveedores:crear` |
| Editar / baja | `PATCH`·`DELETE /proveedores/{id}/` | `compras:proveedores:editar` / `:eliminar` |
| Historial de compras | `GET /proveedores/{id}/historial/` | `compras:proveedores:leer` |

`rfc_o_id_fiscal` es único por tenant.

---

## 3. Órdenes de compra

```mermaid
stateDiagram-v2
  [*] --> pendiente_aprobacion: crear (total > umbral)
  [*] --> enviada: crear (total <= umbral)
  pendiente_aprobacion --> enviada: aprobar (PATCH estado)
  enviada --> recibida_parcial: recepción parcial
  recibida_parcial --> recibida_total: recepción del resto
  enviada --> recibida_total: recepción total
  pendiente_aprobacion --> cancelada: cancelar
  enviada --> cancelada: cancelar
  recibida_parcial --> cancelada: cancelar (cierra el remanente)
```

- **Folio autogenerado** (`OC-000123`). El cuerpo lleva `proveedor_id` + `lineas`
  (`producto_id`, `cantidad`, `costo_unitario`).
- **Umbral de aprobación (RF-52):** si el **total supera el umbral** del tenant
  (`config-aprobacion`), la orden nace en `pendiente_aprobacion` y **no puede recibirse**
  hasta pasar a `enviada`. La aprobación es un **PATCH** que cambia `estado` a `enviada`
  (no hay motor de workflow: es una aprobación manual).
- **Editar** solo mientras no tenga recepciones y no esté cerrada/cancelada.
- **Cancelar** cierra la orden para no admitir más recepciones; lo ya recibido queda intacto.

| Acción | Endpoint | Permiso |
|---|---|---|
| Listar | `GET /ordenes/` | `compras:ordenes:leer` |
| Crear | `POST /ordenes/` | `compras:ordenes:crear` |
| Editar / aprobar (→ enviada) | `PATCH /ordenes/{id}/` | `compras:ordenes:editar` (aprobar: `:aprobar`) |
| Detalle | `GET /ordenes/{id}/` | `compras:ordenes:leer` |
| Cancelar | `POST /ordenes/{id}/cancelar/` | `compras:ordenes:cancelar` |

Cada línea expone `cantidad` y `cantidad_recibida` (para mostrar lo pendiente por recibir).

---

## 4. Recepción de mercancía (el paso que mueve todo)

```mermaid
sequenceDiagram
  participant FE as Frontend
  participant API
  FE->>API: POST /recepciones/ {orden_id, almacen_id, lineas[{orden_compra_linea_id, cantidad}]}
  Note right of API: Valida cantidad <= pendiente por línea.<br/>Genera movimiento de ENTRADA (suma stock).<br/>Crea la Cuenta por Pagar.
  API-->>FE: 201 {recepcion}
  Note over FE: La orden pasa a recibida_parcial / recibida_total
```

- Solo se recibe contra una orden **`enviada`** o **`recibida_parcial`**.
- La cantidad por línea **no puede exceder lo pendiente** (→ `422`).
- Efectos automáticos: **suma stock** (movimiento de entrada en inventario) y crea la
  **cuenta por pagar** vinculada a la orden y al proveedor.
- La orden avanza a `recibida_parcial` o `recibida_total` según la cobertura de las líneas.

| Acción | Endpoint | Permiso |
|---|---|---|
| Listar | `GET /recepciones/` | `compras:recepciones:leer` |
| Registrar | `POST /recepciones/` | `compras:recepciones:crear` |

---

## 5. Cuentas por pagar y configuración

| Acción | Endpoint | Permiso |
|---|---|---|
| Listar cuentas por pagar | `GET /cuentas-por-pagar/` | `compras:cuentas_por_pagar:leer` |
| Ver umbral de aprobación | `GET /config-aprobacion/` | `compras:config_aprobacion:leer` |
| Editar umbral | `PATCH /config-aprobacion/` | `compras:config_aprobacion:editar` |

- Las **CxP se crean solas** en cada recepción; este listado es de solo lectura desde
  Compras (la gestión de pagos es de Finanzas, fuera del alcance actual).

---

## 6. Reglas que el FE debe reflejar

- **La recepción es irreversible en su efecto de stock:** suma inventario y crea deuda. No
  hay “deshacer recepción”; una corrección se hace con un ajuste de inventario.
- **Umbral → aprobación manual:** si una orden nace `pendiente_aprobacion`, muestra el botón
  “Aprobar” (PATCH a `enviada`) para quien tenga `compras:ordenes:aprobar`, y **bloquea
  recepciones** hasta entonces.
- **Costos como string** (decimales): parséalos con librería decimal.
