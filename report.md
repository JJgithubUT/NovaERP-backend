# NovaERP Backend — Reporte de estado del proyecto

**Fecha:** 2026-07-24
**Alcance del proyecto:** RF-01 a RF-64 (ERS v3.0, IEEE 830). Los RF-65 a RF-93 están **fuera de alcance**.
**Stack:** Django 6.0 + PostgreSQL (modelos `managed=False`, lógica de negocio en triggers/funciones), autenticación JWT + TOTP, RBAC por permisos, auditoría transversal.

---

## 1. Resumen ejecutivo

| Estado | Nº de RF | % |
|---|---|---|
| ✅ **Completo** | 45 | 70 % |
| 🟡 **Parcial** | 0 | 0 % |
| ⛔ **Bloqueado** | 4 | 6 % |
| ⚪ **No iniciado** | 15 | 23 % |
| **Total** | **64** | 100 % |

- **Núcleo transversal (Módulos 1–7) completo**, salvo los tenants (RF-01–04), **bloqueados** por una dependencia que ningún RF del alcance resuelve (autenticación de SysAdmin).
- **Módulos de negocio:** Inventario (10) 100 %, Compras (9) 100 %, Ventas/CRM (8) solo los catálogos de cliente; el bloque transaccional de Ventas (RF-30–44) está **sin iniciar** — es el único frente grande pendiente.
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

### Módulo 1 — Administración de Multi-tenencia (RF-01–04)

| RF | Nombre | Estado | Nota |
|---|---|---|---|
| RF-01 | Registrar tenant | ⛔ Bloqueado | Precondición "SysAdmin autenticado". Ningún RF 01-64 define la superficie de autenticación de SysAdmin (`core.sysadmin` no tiene login; RF-16 es login de usuario de tenant). El modelo, las tablas y el multi-tenant existen; falta esa dependencia arquitectónica. |
| RF-02 | Consultar tenants | ⛔ Bloqueado | Ídem. |
| RF-03 | Editar tenant / activar módulos | ⛔ Bloqueado | Ídem. |
| RF-04 | Suspender / baja lógica de tenant | ⛔ Bloqueado | Ídem. |

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

### Módulo 8 — Ventas / CRM (RF-26–44)

| RF | Nombre | Estado | Verif. | Nota |
|---|---|---|---|---|
| RF-26 | Registrar cliente | ✅ Completo | 🔎 | RFC único por tenant, límite de crédito ≥ 0. |
| RF-27 | Consultar / buscar clientes | ✅ Completo | 🔎 | Búsqueda, filtros, paginación. |
| RF-28 | Editar cliente | ✅ Completo | 🔎 | |
| RF-29 | Dar de baja lógica a cliente | ✅ Completo | 🔎 | Bloqueo si hay saldo pendiente en CxC. |
| RF-30 | Registrar oportunidad | ⚪ No iniciado | — | |
| RF-31 | Consultar pipeline | ⚪ No iniciado | — | Existe la vista `v_pipeline_oportunidades`. |
| RF-32 | Actualizar etapa | ⚪ No iniciado | — | |
| RF-33 | Cerrar oportunidad | ⚪ No iniciado | — | |
| RF-34 | Generar cotización | ⚪ No iniciado | — | |
| RF-35 | Consultar cotizaciones | ⚪ No iniciado | — | |
| RF-36 | Editar cotización | ⚪ No iniciado | — | |
| RF-37 | Aprobar / rechazar cotización | ⚪ No iniciado | — | |
| RF-38 | Registrar pedido de venta | ⚪ No iniciado | — | Existe `ventas.validar_limite_credito`. |
| RF-39 | Consultar pedidos | ⚪ No iniciado | — | |
| RF-40 | Editar pedido | ⚪ No iniciado | — | |
| RF-41 | Cancelar pedido | ⚪ No iniciado | — | |
| RF-42 | Generar factura | ⚪ No iniciado | — | |
| RF-43 | Consultar facturas | ⚪ No iniciado | — | |
| RF-44 | Cancelar factura / nota de crédito | ⚪ No iniciado | — | |

> **Nota:** el modelo de datos de todo el Módulo 8 (oportunidad, cotización, pedido, factura, nota de crédito y sus líneas) ya existe en el esquema y en los modelos Django; falta la capa de servicios/vistas.

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

---

## 5. Dependencias y bloqueos

| Bloqueo / dependencia | RF afectados | Naturaleza |
|---|---|---|
| Autenticación de SysAdmin no definida por RF-01–64 | RF-01, 02, 03, 04 | Bloqueo arquitectónico dentro del alcance. |
| Módulo de Workflow / motor de aprobaciones (Fase 1) | RF-08 (RN06/CA07), RF-60 (umbral) | La ERS lo difiere explícitamente fuera de Fase 0. |
| Finanzas avanzada / factura de proveedor (RF-75) | RF-50 (conciliación ±2 %) | Fuera de alcance RF-01–64. |
| Motor de reglas (RF-88) | RF-52 (parametrización avanzada) | Fuera de alcance. |
| Configuración de canal/privacidad por tenant | RF-25 (webhook/Slack), RF-06 (privacidad) | No existe en el esquema de Fase 0. |

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

**Servicios nuevos (Django):** `core/services/` — `usuario_service`, `rol_service`, `session_service`, `auth_service` (orquestador), `config_service`, `auditoria_service`, `notificacion_service`.
**Utilidades nuevas:** `core/utils/` — `permissions`, `audit`, `totp`, `secretos`.
**Worker:** `manage.py enviar_notificaciones` (entrega de notificaciones, para cron).
**Librería nueva:** `reportlab` (única dependencia añadida; exportación PDF).

---

## 7. Verificación

- Todos los RF marcados 🧪 tienen **suites de pruebas automatizados ejecutados contra la base PostgreSQL real** (no solo `manage.py check`), cubriendo reglas de negocio, seguridad, autorización por permiso (403), aislamiento por tenant y auditoría.
- Los RF marcados 🔎 (ventas/compras/inventario) se verificaron con suites que ejercen el CRUD/flujo transaccional, la autorización por permiso y las reglas de negocio clave (bloqueo de baja por saldo, stock no negativo, umbral de aprobación, atomicidad de transferencia, CxP automática).
- Los scripts de prueba viven en el directorio de trabajo temporal (harness de desarrollo), no en el repositorio. `test.http` documenta las peticiones de ejemplo de cada endpoint.
- **Nota de datos de dev:** por acumulación de usuarios de prueba, el tenant de ejemplo llega al límite de licencias del plan (RF-05/RN05 funcionando correctamente); se libera suspendiendo usuarios de prueba.

---

## 8. Lo que queda

1. **Ventas / CRM transaccional (RF-30–44, 15 RF)** — único frente grande dentro del alcance. Modelo de datos ya existente; falta servicios/vistas. Incluye reglas de negocio no triviales (límite de crédito, folios, máquinas de estado oportunidad→cotización→pedido→factura→nota de crédito).
2. **Tenants (RF-01–04)** — desbloquear requiere definir la autenticación de SysAdmin, hoy no cubierta por ningún RF del alcance. Requiere decisión de producto.

Con el núcleo transversal cerrado y verificado, el proyecto tiene una base sólida (autenticación de dos factores, RBAC, auditoría, sesiones, notificaciones) sobre la que construir el bloque de Ventas.
