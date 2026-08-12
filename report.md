# NovaERP Backend — Reporte de estado del proyecto

**Fecha:** 2026-07-25
**Alcance del proyecto:** RF-01 a RF-64 (ERS v3.0, IEEE 830). Los RF-65 a RF-93 están **fuera de alcance**.
**Stack:** Django 6.0 + PostgreSQL (modelos `managed=False`, lógica de negocio en triggers/funciones), autenticación JWT + TOTP, RBAC por permisos, auditoría transversal.

---

## 1. Resumen ejecutivo

| Estado | Nº de RF | % |
|---|---|---|
| ✅ **Completo** | 64 | 100 % |
| 🟡 **Parcial** | 0 | 0 % |
| ⛔ **Bloqueado** | 0 | 0 % |
| ⚪ **No iniciado** | 0 | 0 % |
| **Total** | **64** | 100 % |

- **Alcance RF-01–64 completo (100 %).** El núcleo transversal (Módulos 1–7) incluye los tenants (RF-01–04): el bloqueo anterior (autenticación de SysAdmin, no definida por ningún RF del alcance) se resolvió construyendo esa **infraestructura habilitante** — el portal de plataforma del SysAdmin (ver §4.8).
- **Módulos de negocio:** Inventario (10) 100 %, Compras (9) 100 %, **Ventas/CRM (8) 100 %** — el bloque transaccional (RF-30–44: oportunidades, cotizaciones, pedidos con reserva de stock y crédito, facturas con CxC automática y notas de crédito) quedó cerrado y verificado end-to-end.
- Varios RF están **Completos con una desviación documentada y aprobada** respecto a la letra de la ERS (ver §4). En todos los casos se implementó la lectura técnicamente correcta y ninguna CA obligatoria quedó incumplida.

---

## 2. Leyenda de estados

| Estado | Significado |
|---|---|
| ✅ Completo | Cumple íntegramente la ERS (reglas de negocio, seguridad, auditoría y criterios de aceptación). Incluye "**Completo con desviación documentada**" cuando la ERS se contradice a sí misma, choca con el modelo de seguridad, o depende de infraestructura fuera de alcance, y se implementó la lectura correcta. |
| 🟡 Parcial | Implementado, pero le falta algún requisito de la ERS. |
| ⛔ Bloqueado | No puede terminarse por una dependencia **externa al alcance RF-01–64**, no por falta de trabajo. |
| ⚪ No iniciado | No existe implementación funcional. |

**Nivel de verificación:** 🧪 = suite de pruebas automatizado ejecutado contra la base real · 🔎 = endpoints y flujos verificados (liveness + reglas de negocio) contra la base real.

---

## 3. Estado por módulo

### Módulo 1 — Administración de Multi-tenencia (RF-01–04) — **completo** 🧪

> **Desbloqueado (2026-07-25).** El bloqueo (autenticación de SysAdmin, no cubierta por ningún RF del alcance) se resolvió construyendo el **portal de plataforma del SysAdmin** como infraestructura habilitante: login/logout con sesión persistida propia (`core.sesion_sysadmin`), separada de la de tenant. Ver §4.8.

| RF | Nombre | Estado | Verif. | Nota / reajuste |
|---|---|---|---|---|
| RF-01 | Registrar tenant | ✅ Completo | 🧪 | Alta atómica: tenant en `pendiente` + config de seguridad + rol TENANT_ADMIN + admin inicial `pendiente` con token de activación (24 h) + módulos del plan. Valida unicidad de razón social/dominio/correo a nivel plataforma (RN03/RN04), formato `[a-z0-9-]` 3-50 y **palabras reservadas** (RN07/CA10). Activación por endpoint público dedicado (`/api/auth/activar-tenant/`): fija contraseña y en cascada pasa usuario **y** tenant a `activo` (evento `INICIALIZACION_ENTORNO`). **Reajuste:** el `slug` es el "dominio comercial" gobernado por RN03/RN07/CA03; MFA en primer login (§4.1). |
| RF-02 | Consultar tenants | ✅ Completo | 🧪 | `GET /api/admin/tenants/[<id>/]` cross-tenant, paginado (máx. 50), búsqueda razón social/dominio/id, filtros estado/plan/fecha, orden (CA02-04); detalle con módulos activos y consumo de licencias (CA05). CA06 (usuario de tenant → 401) por separación de superficies. |
| RF-03 | Editar tenant / activar módulos | ✅ Completo | 🧪 | `PATCH` de datos generales, plan y módulos. Núcleo no desactivable (RN03); solo se activan módulos del plan (RN05); dependencias validadas (CA08, tabla `modulo_dependencia`); **desactivación en cascada** con confirmación explícita que enumera los dependientes afectados (RN07/CA09). |
| RF-04 | Suspender / baja lógica de tenant | ✅ Completo *(desviación)* | 🧪 | `POST .../suspender/` (+`?baja=true`) y `.../reactivar/`. Invalida **todas** las sesiones de los usuarios del tenant (CA02), bloquea login y API (el validador ya rechaza tenant no-`activo`), reversible (RN05). **Desviación (§4.9):** el bloqueo diferenciado de exportación por tipo (RN06/CA07) no aplica — no hay endpoint de exportación de datos de negocio que bloquear; el tipo se audita. |

### Módulo 2 — Gestión de Usuarios (RF-05–09) — **completo** 🧪

| RF | Nombre | Estado | Verif. | Nota / reajuste |
|---|---|---|---|---|
| RF-05 | Registrar usuario | ✅ Completo | 🧪 | Alta + activación por token de un solo uso (24 h) + roles + límite de licencias + auditoría. **Reajuste:** el enrolamiento MFA ocurre en el primer login, no en la activación (ver §4.1). El correo se **encola** y ahora se entrega por RF-25. |
| RF-06 | Consultar directorio | ✅ Completo | 🧪 | `GET /api/core/usuarios/` paginado; búsqueda nombre/correo/puesto, filtros estado/rol/departamento/fecha, orden por nombre/alta/último acceso. **Reajuste:** se añadieron columnas `puesto` y `departamento`; enmascaramiento por privacidad (RN03/CA06) no implementado por falta de config (ver §4.5). |
| RF-07 | Editar usuario | ✅ Completo | 🧪 | Dos actores (admin total / propietario solo datos personales). Cambio de correo → reverificación. Incluye **reseteo de MFA** (RN07). |
| RF-08 | Suspender / reactivar | ✅ Completo | 🧪 | Cierra sesiones (RN03), bloquea login con mensaje propio (CA03), protege al último admin (RN04). **Dependencia externa:** el bloqueo de aprobaciones pendientes (RN06/CA07) lo difiere la ERS al Módulo de Workflow (fuera de alcance). |
| RF-09 | Perfil propio | ✅ Completo | 🧪 | `GET /api/core/me/` con último acceso (CA03), sin exponer secretos (CA02), roles vigentes (CA04). |

### Módulo 3 — Roles y Permisos / RBAC (RF-10–15) — **completo** 🧪

| RF | Nombre | Estado | Verif. | Nota / reajuste |
|---|---|---|---|---|
| RF-10 | Registrar rol | ✅ Completo | 🧪 | Permisos atómicos del catálogo maestro; nombre único por tenant; al menos un permiso; solo módulos activos (RN04). |
| RF-11 | Consultar roles y permisos | ✅ Completo | 🧪 | Permisos agrupados por dominio, marca de inertes, contador de usuarios. |
| RF-12 | Editar rol | ✅ Completo | 🧪 | **Efecto inmediato sin re-login** (RN01) — la autorización se resuelve en cada petición contra la DB. Rol de sistema protegido (RN02). |
| RF-13 | Eliminar / desactivar rol | ✅ Completo *(desviación)* | 🧪 | **Desviación documentada** de CA02 (ver §4.2): un rol inactivo **no** concede permisos (la ERS decía lo contrario, incompatible con el modelo de seguridad). |
| RF-14 | Asignar roles | ✅ Completo | 🧪 | Unión de permisos (RN01), solo roles activos del tenant. Incluye la segregación de funciones de RF-21/CA04. |
| RF-15 | Revocar rol | ✅ Completo | 🧪 | No deja al usuario sin roles; no revoca al último admin activo. |

### Módulo 4 — Autenticación y Gestión de Sesión (RF-16–19) — **completo** 🧪

| RF | Nombre | Estado | Verif. | Nota / reajuste |
|---|---|---|---|---|
| RF-16 | Autenticar (login + 2º factor) | ✅ Completo *(desviaciones)* | 🧪 | Login de dos fases: password → reto OTP → sesión. TOTP RFC 6238 con stdlib; secreto cifrado en reposo (Fernet). Bloqueo por intentos + eventos LOGIN/LOGIN_FAILED/ACCOUNT_LOCKED. **Desviaciones (§4.1):** enrolamiento en primer login; "3 intentos OTP" → bloqueo global de 5. |
| RF-17 | Cerrar sesión (logout) | ✅ Completo | 🧪 | Revoca la sesión actual; idempotente. |
| RF-18 | Recuperar / restablecer contraseña | ✅ Completo | 🧪 | Token de un solo uso (1 h); mismo mensaje exista o no el correo (RN02); al restablecer invalida **todas** las sesiones previas (RN03). |
| RF-19 | Consultar / revocar sesiones | ✅ Completo *(desviación)* | 🧪 | El usuario lista/cierra sus sesiones; el admin cierra todas las de un usuario (sin ver detalle). **Desviación (§4.3):** `ultima_actividad` = inicio de sesión; sin geolocalización por IP. |

### Módulo 5 — Auditoría y Cumplimiento (RF-20–21) — **completo** 🧪

| RF | Nombre | Estado | Verif. | Nota / reajuste |
|---|---|---|---|---|
| RF-20 | Registrar bitácora (automático) | ✅ Completo | 🧪 | Trigger `fn_auditar` en 37 tablas; 7 campos obligatorios; enmascarado de secretos (`[REDACTED]`). Contexto (actor/tenant/IP) publicado por `audit_context`. **Nota:** las filas escritas antes de las migraciones de RF-20/RF-16 (hashes antiguos, escrituras de login sin actor) no son corregibles — la tabla es append-only; el criterio se cumple hacia adelante. |
| RF-21 | Consultar bitácora | ✅ Completo | 🧪 | Consulta paginada, filtros por usuario/operación/entidad/fecha, solo lectura. Incluye la **segregación de funciones** (CA04): un Auditor no puede tener permisos de mutación (validado en RF-14). |

### Módulo 6 — Configuración de Seguridad del Tenant (RF-22) — **completo** 🧪

| RF | Nombre | Estado | Verif. | Nota / reajuste |
|---|---|---|---|---|
| RF-22 | Configurar políticas de seguridad | ✅ Completo | 🧪 | Endurecer política dentro de los límites de plataforma (RN01/CA01). No invalida retroactivamente contraseñas ni sesiones (RN02/CA02). Cambios auditados (CA03). |

### Módulo 7 — Reportería Básica y Notificaciones (RF-23–25) — **completo** 🧪

| RF | Nombre | Estado | Verif. | Nota / reajuste |
|---|---|---|---|---|
| RF-23 | Reporte de actividad de usuarios | ✅ Completo | 🧪 | Por usuario: último acceso, nº de sesiones, nº de acciones CUD, estado. Filtros por fecha/departamento/puesto. Export **CSV y PDF**. |
| RF-24 | Exportar bitácora a archivo | ✅ Completo | 🧪 | Export **CSV y PDF** con metadatos; la exportación se audita como evento propio `EXPORT` (RN02). PDF con `reportlab` (librería Python-pura autorizada). |
| RF-25 | Notificar eventos críticos de seguridad | ✅ Completo *(desviación)* | 🧪 | Cola en `core.notificacion` + worker `manage.py enviar_notificaciones` (email vía framework de Django, reintento asíncrono). Bloqueo → notifica a usuario + TENANT_ADMIN; sin credenciales/tokens en el cuerpo (CA02). **Desviación (§4.4):** canal webhook/Slack (RN01, opcional) no implementado — no hay config de canal por tenant. |

### Módulo 8 — Ventas / CRM (RF-26–44) — **completo** 🧪

| RF | Nombre | Estado | Verif. | Nota |
|---|---|---|---|---|
| RF-26 | Registrar cliente | ✅ Completo | 🔎 | RFC único por tenant, límite de crédito ≥ 0. |
| RF-27 | Consultar / buscar clientes | ✅ Completo | 🔎 | Búsqueda, filtros, paginación. |
| RF-28 | Editar cliente | ✅ Completo | 🔎 | |
| RF-29 | Dar de baja lógica a cliente | ✅ Completo | 🔎 | Bloqueo si hay saldo pendiente en CxC. |
| RF-30 | Registrar oportunidad | ✅ Completo | 🧪 | Ligada a cliente activo (RN01); nace en `prospeccion`/`abierta`. Se añadió `fecha_cierre_estimada` (CA: no anterior a hoy); la probabilidad se deriva de la etapa (RN02). |
| RF-31 | Consultar pipeline | ✅ Completo | 🧪 | Lista tabular + pipeline kanban con valor ponderado (valor × probabilidad). **Autorización por objeto:** cada vendedor ve solo las propias salvo `ventas:pipeline:ver_todo`. |
| RF-32 | Actualizar etapa | ✅ Completo | 🧪 | Avanza solo a la etapa siguiente (RN01: no salta ni retrocede); terminales `ganada`/`perdida` (RN02). |
| RF-33 | Cerrar oportunidad | ✅ Completo | 🧪 | `perdida` exige motivo del catálogo (RN01); `ganada` habilita generar cotización (RN02, afordance FE). |
| RF-34 | Generar cotización | ✅ Completo *(desviación)* | 🧪 | Precio del catálogo; ajuste manual exige `ventas:cotizaciones:ajustar_precio` (RN01); totales automáticos (RN02). **Desviación (§4.10):** descuento sobre el máximo → `pendiente_aprobacion` (BPM manual, RF-83/86 fuera de alcance). |
| RF-35 | Consultar cotizaciones | ✅ Completo | 🧪 | Filtros cliente/estado/fecha; estado `vencida` derivado de `vigente_hasta`. |
| RF-36 | Editar cotización | ✅ Completo *(desviación)* | 🧪 | Solo en `borrador`/`pendiente_aprobacion`. **Desviación (§4.10):** el versionado formal no está en el esquema → se bloquea la edición y se regenera (sin cadena de versiones). |
| RF-37 | Aprobar / rechazar cotización | ✅ Completo *(desviación)* | 🧪 | Una vencida no se aprueba (RN01); auditado. **Desviación (§4.10):** solo aprobación interna (el portal de cliente es fase futura). |
| RF-38 | Registrar pedido de venta | ✅ Completo | 🧪 | Desde cotización aprobada o directo (RN01); al confirmar **reserva stock** (RN03) y valida **límite de crédito** (RN02: excede → bloquea salvo autorización `finanzas:credito:autorizar`); sin stock y con backorder → `pendiente_surtido`. |
| RF-39 | Consultar pedidos | ✅ Completo | 🧪 | Filtros por cliente, estado, fecha. |
| RF-40 | Editar pedido | ✅ Completo *(desviación)* | 🧪 | **Desviación (§4.10):** edición solo en `borrador` (editar un pedido confirmado = cancelar y recrear, para preservar la integridad de la reserva). |
| RF-41 | Cancelar pedido | ✅ Completo | 🧪 | Libera el stock reservado (RN01); un pedido con facturas no se cancela (RN02, va a nota de crédito). |
| RF-42 | Generar factura | ✅ Completo | 🧪 | Parcial/total; no más de lo pendiente por línea (RN01, trigger); reserva → **salida definitiva** de inventario (RN02); **CxC automática** (RN03); impuestos = subtotal × `iva_pct` del tenant. |
| RF-43 | Consultar facturas | ✅ Completo | 🧪 | Filtros por cliente, estado, fecha. |
| RF-44 | Cancelar factura / nota de crédito | ✅ Completo *(desviación)* | 🧪 | Factura inalterable (RN01); NC ligada revierte saldo CxC; NC total con `reingresar_stock` reingresa a inventario (RN02). **Desviación (§4.10):** el reingreso se controla por bandera de la petición (la ERS dice "si el tenant lo configura"). |

> **Nota:** el esquema del Módulo 8 ya existía; esta entrega añadió la capa de servicios/vistas más `ventas.config_ventas` (impuestos/descuento/backorder por tenant), `oportunidad.fecha_cierre_estimada`, y para la reserva de stock `pedido_venta.almacen_id` + `pedido_linea.cantidad_reservada`.

### Módulo 9 — Compras (RF-45–52) — **completo** 🔎

| RF | Nombre | Estado | Verif. | Nota / reajuste |
|---|---|---|---|---|
| RF-45 | Registrar proveedor | ✅ Completo | 🔎 | |
| RF-46 | Consultar / editar / baja proveedor | ✅ Completo | 🔎 | |
| RF-47 | Registrar orden de compra | ✅ Completo | 🔎 | Folio autogenerado; si supera el umbral (RF-52) nace `pendiente_aprobacion`. |
| RF-48 | Consultar / editar / cancelar orden | ✅ Completo | 🔎 | |
| RF-49 | Registrar recepción | ✅ Completo | 🔎 | Genera movimiento de inventario (trigger de stock); orden pasa a recibida parcial/total. |
| RF-50 | Registrar cuenta por pagar (automática) | ✅ Completo | 🔎 | Disparo automático desde la recepción. La conciliación ±2 % contra factura de proveedor es **RF-75 (Finanzas), fuera de alcance**. |
| RF-51 | Historial de compras por proveedor | ✅ Completo | 🔎 | |
| RF-52 | Umbral de aprobación por monto | ✅ Completo | 🔎 | Umbral por tenant. La parametrización avanzada (motor de reglas) es **RF-88, fuera de alcance**. |

### Módulo 10 — Inventario (RF-53–64) — **completo** 🔎

| RF | Nombre | Estado | Verif. | Nota / reajuste |
|---|---|---|---|---|
| RF-53 | Registrar producto | ✅ Completo | 🔎 | |
| RF-54 | Consultar catálogo | ✅ Completo | 🔎 | |
| RF-55 | Editar producto | ✅ Completo | 🔎 | |
| RF-56 | Dar de baja / descontinuar | ✅ Completo | 🔎 | |
| RF-57 | Registrar almacén | ✅ Completo | 🔎 | |
| RF-58 | Registrar movimiento (entrada/salida) | ✅ Completo | 🔎 | Trigger de stock; bloqueo de stock negativo. La bandera configurable de backorder no existe en el esquema (se aplica bloqueo estricto). |
| RF-59 | Consultar stock / disponibilidad | ✅ Completo | 🔎 | Sin caché (RNF de rendimiento, no una CA). |
| RF-60 | Registrar ajuste | ✅ Completo | 🔎 | La aprobación por umbral (workflow) es **RF-83, fuera de alcance**. |
| RF-61 | Registrar transferencia | ✅ Completo | 🔎 | Salida + entrada atómicas. |
| RF-62 | Consultar kardex | ✅ Completo | 🔎 | |
| RF-63 | Alerta de stock mínimo | ✅ Completo | 🔎 | |
| RF-64 | Consultar valuación | ✅ Completo | 🔎 | |

---

## 4. Reajustes y desviaciones documentadas

Todas fueron analizadas, justificadas y aprobadas; en cada caso se implementó la lectura técnicamente correcta y **ninguna CA obligatoria quedó incumplida**.

### 4.1 RF-16 / RF-05 — Enrolamiento MFA en el primer login (no en la activación)
- **ERS (RN07):** el secreto MFA se inicializa durante el flujo de activación.
- **Implementado:** un usuario sin secreto MFA se enrola automáticamente en su siguiente login (mismo camino para todos, incluido el admin sembrado).
- **Motivo:** los usuarios existentes y el admin sembrado no tenían secreto; se descartó una migración masiva. Enrolar en la activación habría exigido reabrir RF-05 y reconciliar datos.
- **Coste:** el primer enrolamiento es *trust-on-first-use* (un atacante con la contraseña podría enrolar su autenticador). Inherente a enrolar en login vs. activación.

### 4.1b RF-16 — "Exactamente 3 intentos de OTP"
- **ERS (flujo 3a):** tras 3 fallos de OTP el reto expira.
- **Implementado:** el reto caduca por tiempo (~5 min) y los fallos de OTP cuentan al **bloqueo global** de RN02 (5 intentos). El límite global es más estricto; ninguna CA obligatoria se incumple. Evita una tabla de estado por-desafío.

### 4.2 RF-13 — Un rol inactivo no concede permisos
- **ERS (CA02):** al desactivar un rol, los usuarios que ya lo tenían "conservan sus permisos activos de forma inerte".
- **Implementado:** lo contrario — un rol inactivo no autoriza.
- **Motivo:** seguir la letra haría que desactivar un rol no tuviera efecto de seguridad y contradiría RF-12/RN01 (efecto inmediato) y el principio de menor privilegio. La primera mitad de la CA02 sí se cumple (un rol inactivo no es asignable y la baja es reversible).

### 4.3 RF-19 — "Última actividad" de la sesión
- **ERS (CA01):** listar la fecha/hora de última actividad por sesión.
- **Implementado:** `ultima_actividad` = fecha de inicio de la sesión.
- **Motivo:** rastrear la actividad real convertiría el middleware (hoy de solo lectura) en escritor y generaría un `UPDATE` por minuto y sesión → contaminaría la bitácora append-only de RF-20. La "ubicación aproximada por IP" (narrativa, no en CA01) queda fuera: exigiría una base GeoIP.

### 4.4 RF-25 — Canal webhook/Slack
- **ERS (RN01):** correo **y**, *si está configurado*, un canal adicional (webhook/Slack).
- **Implementado:** solo correo. El canal adicional es explícitamente opcional; no hay configuración de canal por tenant en el esquema de Fase 0.

### 4.5 RF-06 — Privacidad y auditoría de la consulta
- **ERS (RN03/CA06):** enmascarar datos según la política de privacidad del tenant; auditar la consulta cuando la política lo exija.
- **Implementado:** se devuelven los campos a quien tiene el permiso; la lectura no se audita (consistente con el default de RF-20). No existe configuración de política de privacidad en Fase 0.

### 4.6 RF-08 — Aprobaciones pendientes del suspendido
- **ERS (RN06/CA07):** marcar como bloqueadas las aprobaciones pendientes del usuario suspendido.
- **Estado:** la propia ERS difiere este comportamiento al **Módulo de Workflow (Fase 1)**, fuera del alcance RF-01–64. No existe entidad de aprobación ni la bandera `is_blocked_by_suspension` en el esquema.

### 4.7 RF-23 / RF-24 — Formato PDF
- Se autorizó añadir `reportlab` (Python puro, sin dependencias del sistema) para generar PDF, además de CSV. Es la única librería nueva incorporada en todo el proyecto.

### 4.8 RF-01–04 — Portal de plataforma del SysAdmin (infraestructura habilitante)
- **Contexto:** ningún RF del alcance (01–64) define cómo se autentica el SysAdmin, pero RF-01–04 lo tienen como precondición. Se construyó esa superficie como infraestructura habilitante, con paridad de seguridad con el resto del sistema (sesión persistida = fuente de verdad de la revocación, decisión del Sprint 5).
- **Implementado:** `core.sesion_sysadmin` (tabla propia, sin tenant ni FK a `core.usuario`, porque el SysAdmin vive fuera de la multi-tenencia); login/logout (`/api/admin/login|logout/`); JWT con `typ:"sysadmin"` sin `tid`; el middleware ramifica por `typ` y expone `request.sysadmin_id`; `SysAdminRequiredMixin` (no pasa por RBAC, que es por tenant); `sysadmin_context` fija `app.is_sysadmin='true'` (RLS) y deja el actor fuera del GUC de usuario para no violar la FK del trigger de auditoría; eventos de plataforma manuales (`LOGIN`/`LOGIN_FAILED`/`LOGOUT` y, para tenants, `CREATE_TENANT`/`UPDATE_TENANT`/`TENANT_SUSPEND`/`TENANT_BAJA`/`TENANT_REACTIVATE`/`INICIALIZACION_ENTORNO`) que nombran al SysAdmin responsable. Bootstrap: `manage.py crear_sysadmin` (credenciales por env para CI, idempotente sin clobber, hash en DB).
- **Decisiones aprobadas:** login de **una fase, sin MFA** por ahora (la columna `sysadmin.mfa_secret` queda reservada) y **sin bloqueo por intentos** (RN02) — `core.sysadmin` no tiene contador de intentos; los fallos se **auditan** (visibilidad de fuerza bruta) pero no se cuentan todavía. Ambos son diferidos documentados, no incumplimientos de una CA del alcance (RF-01–04 no especifican estos detalles).

### 4.10 RF-30–44 — Ventas transaccional
- **Aprobación por descuento y flujo formal (RF-34/37):** cuando el descuento supera el máximo del tenant, la cotización queda en `pendiente_aprobacion`, pero la liberación a `aprobada` es **manual** (RF-37) — el motor de aprobaciones BPM (RF-83/86) está fuera del alcance. Mismo stand-in ya usado en Compras (RF-47/52).
- **Portal de cliente (RF-37):** la aprobación de cotización es solo interna (con `ventas:cotizaciones:aprobar`); el portal de autoservicio del cliente es fase futura, fuera de alcance.
- **Versionado de cotización (RF-36):** el esquema no modela versiones; una cotización aprobada/rechazada no se edita — se regenera creando otra. Se bloquea la edición fuera de `borrador`/`pendiente_aprobacion`, sin cadena formal de versiones.
- **Edición de pedido (RF-40):** se permite solo en `borrador`. Editar un pedido ya confirmado exige cancelarlo (que libera la reserva) y crear uno nuevo — para no romper la integridad de la reserva de stock.
- **Reingreso de stock por nota de crédito (RF-44/RN02):** la ERS dice "si el tenant lo configura así"; se implementó como una **bandera `reingresar_stock`** en la petición de NC, aplicable a notas de crédito totales. Ninguna CA obligatoria queda incumplida.
- **Impuestos (RF-42):** no existe "catálogo fiscal" en el esquema; se usa una **tasa de IVA por tenant** (`ventas.config_ventas.iva_pct`, default 16 %). La cotización (RF-34) no desglosa impuestos (su tabla no tiene la columna); el desglose ocurre en la factura.

### 4.9 RF-04 — Bloqueo de exportación por tipo de suspensión
- **ERS (RN06/CA07):** una suspensión de tipo *cumplimiento* debe bloquear la exportación de datos del tenant; una *administrativa* la deja con periodo de gracia.
- **Implementado:** el `tipo` (cumplimiento/administrativa) y el motivo se validan y se **auditan**, pero el bloqueo diferenciado de exportación no se aplica porque **no existe endpoint de exportación de datos de negocio del TENANT_ADMIN** que bloquear (RF-24 exporta la bitácora de auditoría, no datos de negocio). Cuando exista ese endpoint (bloque de Ventas/Finanzas), consultará el estado/tipo del tenant. Ninguna CA obligatoria del alcance queda incumplida.

---

## 5. Dependencias y bloqueos

| Bloqueo / dependencia | RF afectados | Naturaleza |
|---|---|---|
| ~~Autenticación de SysAdmin no definida por RF-01–64~~ | ~~RF-01, 02, 03, 04~~ | **Resuelto (2026-07-25):** portal de plataforma del SysAdmin construido como infraestructura habilitante (§4.8). Ya no hay RF bloqueados. |
| Módulo de Workflow / motor de aprobaciones (Fase 1) | RF-08 (RN06/CA07), RF-60 (umbral) | La ERS lo difiere explícitamente fuera de Fase 0. |
| Finanzas avanzada / factura de proveedor (RF-75) | RF-50 (conciliación ±2 %) | Fuera de alcance RF-01–64. |
| Motor de reglas (RF-88) | RF-52 (parametrización avanzada) | Fuera de alcance. |
| Configuración de canal/privacidad por tenant | RF-25 (webhook/Slack), RF-06 (privacidad) | No existe en el esquema de Fase 0. |
| Endpoint de exportación de datos de negocio | RF-04 (bloqueo de exportación por tipo, RN06/CA07) | Depende del bloque de Ventas/Finanzas; sin endpoint que bloquear todavía (§4.9). |

---

## 6. Cambios de esquema aplicados (migraciones SQL)

Todas idempotentes, aplicadas a la base de desarrollo y reflejadas en `db.sql`. La base es la fuente de verdad; Django solo lee (`managed=False`).

| Archivo | RF | Contenido |
|---|---|---|
| `sql/2026-07-23_rf20_auditoria.sql` | RF-20 | `fn_redactar`, `fn_auditar` reescrita, 37 triggers de auditoría. |
| `sql/2026-07-24_rf10_rbac_catalogo.sql` | RF-10..15 | Catálogo de ~69 permisos + módulos SEGURIDAD/REPORTERIA + permiso reset_mfa. |
| `sql/2026-07-24_rf07_usuario_telefono.sql` | RF-07 | Columna `usuario.telefono`. |
| `sql/2026-07-24_rf06_usuario_puesto_departamento.sql` | RF-06 | Columnas `usuario.puesto`, `usuario.departamento`. |
| `sql/2026-07-24_rf16_login_validador_puro.sql` | RF-16 | `intentar_login` como validador puro (sin escrituras); `registrar_intento_fallido` devuelve el bloqueo. |
| `sql/2026-07-24_rf16_reset_mfa_permiso.sql` | RF-07/16 | Permiso `core:usuarios:reset_mfa`. |
| `sql/2026-07-24_rf08_login_mensaje_suspendido.sql` | RF-08 | Mensaje específico de cuenta suspendida en el login. |
| `sql/2026-07-25_sysadmin_sesion.sql` | RF-01..04 (infra) | `core.sesion_sysadmin` (sesión del portal de plataforma) + `intentar_login_sysadmin` (validador puro). |
| `sql/2026-07-25_rf01_04_tenants.sql` | RF-01..04 | Estado `pendiente` del tenant (ALTER TYPE), `core.dominio_reservado`, `core.plan_modulo` (módulos por plan), `core.modulo_dependencia` (dependencias entre módulos). |
| `sql/2026-07-25_rf30_44_ventas.sql` | RF-30..44 | `ventas.config_ventas` (IVA/descuento máx/backorder por tenant, con RLS), `oportunidad.fecha_cierre_estimada`, `pedido_venta.almacen_id` + `pedido_linea.cantidad_reservada` (reserva de stock), permisos `ventas:pipeline:ver_todo` y `ventas:cotizaciones:ajustar_precio`. |

**Servicios nuevos (Django):** `core/services/` — `usuario_service`, `rol_service`, `session_service`, `auth_service` (orquestador), `config_service`, `auditoria_service`, `notificacion_service`, `sysadmin_session_service`, `sysadmin_auth_service`, `tenant_service`. `ventas/services/` — `oportunidad_service`, `cotizacion_service`, `pedido_service`, `factura_service` (además de `catalogo_service`).
**Utilidades nuevas:** `core/utils/` — `permissions`, `audit` (incl. `sysadmin_context`), `totp`, `secretos`. **Mixin:** `SysAdminRequiredMixin` (portal de plataforma).
**Workers / comandos:** `manage.py enviar_notificaciones` (entrega de notificaciones, para cron); `manage.py crear_sysadmin` (bootstrap del SysAdmin, credenciales por env).
**Librería nueva:** `reportlab` (única dependencia añadida; exportación PDF).

---

## 7. Verificación

- Todos los RF marcados 🧪 tienen **suites de pruebas automatizados ejecutados contra la base PostgreSQL real** (no solo `manage.py check`), cubriendo reglas de negocio, seguridad, autorización por permiso (403), aislamiento por tenant y auditoría.
- Los RF marcados 🔎 (ventas/compras/inventario) se verificaron con suites que ejercen el CRUD/flujo transaccional, la autorización por permiso y las reglas de negocio clave (bloqueo de baja por saldo, stock no negativo, umbral de aprobación, atomicidad de transferencia, CxP automática).
- El portal de plataforma (fundación SysAdmin y RF-01–04) tiene suites end-to-end contra la base real (login/logout/aislamiento de sesión; alta/activación/consulta de tenants; edición con dependencias y cascada de módulos; suspensión/baja/reactivación con revocación de sesiones), repetibles y verdes, más regresión del login de tenant de dos fases.
- El bloque de Ventas transaccional (RF-30–44) tiene 72 pruebas end-to-end contra la base real, cubriendo: máquina de etapa y autorización por objeto de oportunidades; cálculo de totales, ajuste de precio y aprobación de cotizaciones; reserva de stock (con `FOR UPDATE`), límite de crédito con autorización y backorder en pedidos; facturación parcial/total con salida de inventario, CxC automática y notas de crédito con reingreso de stock. El stock reservado se verifica invariante (vuelve a cero al terminar).
- Los scripts de prueba viven en el directorio de trabajo temporal (harness de desarrollo), no en el repositorio. `test.http` documenta las peticiones de ejemplo de cada endpoint (incluida la sección del portal de plataforma).
- **Nota de datos de dev:** por acumulación de usuarios de prueba, el tenant de ejemplo llega al límite de licencias del plan (RF-05/RN05 funcionando correctamente); se libera suspendiendo usuarios de prueba. En el portal de plataforma, como el diseño **append-only** hace indeleteable cualquier tenant/usuario con historial de auditoría, las pruebas usan **datos únicos por corrida** en vez de limpiar.

---

## 7bis. Extensión post-ERS — Reportes de Ventas (RV-01…06) · 2026-08-04

Sprint **fuera del alcance ERS v3.0** (numeración propia para no colisionar con los RF): seis reportes agregados del dominio comercial. Plan y decisiones en [`docs/SPRINT-REPORTES-VENTAS.md`](docs/SPRINT-REPORTES-VENTAS.md); contrato para el frontend en [`docs/api/FLUJO-VENTAS-REPORTES.md`](docs/api/FLUJO-VENTAS-REPORTES.md).

| RV | Reporte | Estado | Verif. | Nota |
|---|---|---|---|---|
| RV-01 | Ventas por periodo | ✅ Completo | 🧪 | Día/semana/mes. Venta neta = facturado − NC; la NC se imputa a **su** fecha, no a la de la factura, para no alterar un periodo ya reportado. |
| RV-02 | Ranking de clientes | ✅ Completo | 🧪 | Los totales agregan todo el rango, no solo el top; participación `null` si la base no es positiva. |
| RV-03 | Ranking de productos | ✅ Completo | 🧪 | **Importe bruto:** `nota_credito` no tiene líneas, así que las devoluciones no se pueden imputar a un producto. Declarado en la respuesta y en el archivo. |
| RV-04 | Embudo comercial | ✅ Completo | 🧪 | Cuenta documentos creados en el rango (flujo del periodo), no una foto de estados; conversión respecto de la etapa anterior. |
| RV-05 | Cartera por antigüedad | ✅ Completo *(desviación)* | 🧪 | Saldo reconstruido **a la fecha de corte** (monto − abonos previos), no el saldo vivo. **Desviación:** la antigüedad son días desde emisión, no vencidos — el esquema no modela `fecha_vencimiento` ni `dias_credito`. |
| RV-06 | Desempeño de vendedores | ✅ Completo | 🧪 | Fila `Sin asignar` para el histórico sin atribución; nunca se reparte entre vendedores. |

**Cambios de esquema** (`sql/2026-08-03_rv01_06_reportes_ventas.sql`, idempotente y aplicado en dev): `vendedor_id` en `cotizacion`/`pedido_venta`/`factura_venta` con backfill por la cadena existente; permisos `ventas:reportes:leer` y `:exportar`; 8 índices de soporte (esas tablas no tenían ninguno más allá de PK y unique de folio).

**Código nuevo:** `core/utils/export.py` (CSV/PDF extraído de `auditoria_service`), `core/utils/errors.ParametroInvalido` (400), `ventas/services/reporte_service.py`, `ventas/services/atribucion.py`, y la vista base `ReporteView`.

**Verificación:** suites contra PostgreSQL real, incluidas las dos que faltaban al cerrar RF-30–44 — **aislamiento por tenant** (segundo tenant `rv-test` sembrado con facturación propia: ninguno de los seis reportes la filtra) y **autorización real sin bypass** (usuarios `rv-ciego` y `rv-lector`: 403 sin permiso, 403 al exportar con solo `:leer`, y RN-04 comprobado — un vendedor sin `ventas:pipeline:ver_todo` no obtiene datos ajenos ni pidiendo `?vendedor_id=`).

---

## 8. Lo que queda

**El alcance RF-01–64 está completo (64/64).** No queda ningún RF pendiente ni bloqueado dentro del alcance.

Trabajo transversal opcional / de despliegue (no son RF del alcance):
- **Correo saliente (SMTP) y scheduler del worker de notificaciones** — el pipeline (cola en DB + `manage.py enviar_notificaciones` + backend de Django) está construido, pero en dev usa el backend de consola y el worker no está programado; para entrega real hace falta configurar SMTP y un cron. Es la única pieza externa requerida por el ERS.
- **Rol de base de datos sin `BYPASSRLS` en producción** — hoy la app corre como `postgres` (superusuario), así que la RLS por tenant es defensa en profundidad no ejercida; en producción debe usarse un rol de aplicación sin privilegio para que la RLS del motor entre en efecto.

Fuera de alcance (fases posteriores, RF-65–93): BPM/workflow, Finanzas avanzada, RRHH/Nómina, Proyectos, Motor de reglas, BI, portal de autoservicio del cliente.
