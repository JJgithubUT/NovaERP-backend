-- ============================================================================
-- NovaERP — RBAC: catálogo maestro de permisos y módulos faltantes
-- Sprint RF-10 a RF-15 · Fecha: 2026-07-24
--
-- Alcance: RF-01 a RF-64 únicamente. No se siembra ningún permiso de los
-- dominios rrhh, proyectos, bpm, bi ni reglas (RF-65..RF-93).
--
-- No crea tablas ni columnas: core.modulo, core.permiso, core.rol_permiso y
-- core.tenant_modulo ya existen. Esto solo completa su contenido.
--
-- Idempotente: puede re-ejecutarse sin duplicar filas.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) Módulos faltantes de la ERS
-- ----------------------------------------------------------------------------
-- core.modulo tenía 8 de los 10 módulos del alcance: faltaban el Módulo 6
-- (Configuración de Seguridad del Tenant, RF-22) y el Módulo 7 (Reportería
-- Básica y Notificaciones, RF-23..RF-25). Ambos son fase 0 como el resto del
-- núcleo.
INSERT INTO core.modulo (codigo, nombre, fase) VALUES
  ('SEGURIDAD',  'Configuración de Seguridad del Tenant', 0),
  ('REPORTERIA', 'Reportería Básica y Notificaciones',    0)
ON CONFLICT (codigo) DO NOTHING;

-- Los tenants existentes ya tenían activos todos los módulos de fase 0 y 1;
-- se les activan también los dos nuevos para no dejarlos con el núcleo
-- incompleto (RF-01/RN06: el núcleo nunca puede estar deshabilitado).
INSERT INTO core.tenant_modulo (tenant_id, modulo_id)
SELECT t.id, m.id
  FROM core.tenant t
  CROSS JOIN core.modulo m
 WHERE m.codigo IN ('SEGURIDAD', 'REPORTERIA')
   AND EXISTS (
     SELECT 1 FROM core.tenant_modulo tm
      JOIN core.modulo m0 ON m0.id = tm.modulo_id
     WHERE tm.tenant_id = t.id AND m0.fase = 0 AND tm.activo
   )
ON CONFLICT (tenant_id, modulo_id) DO NOTHING;


-- ----------------------------------------------------------------------------
-- 2) Renombrado definitivo: compras:ordenes_compra -> compras:ordenes
-- ----------------------------------------------------------------------------
-- La ERS nombra el permiso como "compras:ordenes:crear" en el actor de RF-47,
-- pero el seed original usaba el recurso "ordenes_compra". Se unifica al
-- nombre de la ERS. core.permiso.codigo es GENERATED ALWAYS ... STORED, así
-- que se regenera solo al cambiar el recurso.
--
-- Va ANTES de los INSERT: si primero se insertara compras:ordenes:aprobar,
-- el renombrado chocaría contra la restricción única de codigo.
--
-- Seguro respecto a datos existentes: core.rol_permiso referencia permiso.id,
-- no el código, así que ninguna asignación se pierde.
UPDATE core.permiso
   SET recurso = 'ordenes'
 WHERE dominio = 'compras'
   AND recurso = 'ordenes_compra'
   AND NOT EXISTS (
     SELECT 1 FROM core.permiso p2
      WHERE p2.dominio = 'compras' AND p2.recurso = 'ordenes'
        AND p2.accion = core.permiso.accion
   );


-- ----------------------------------------------------------------------------
-- 3) Catálogo maestro (RF-10/RN02: no se pueden inventar permisos vía API)
-- ----------------------------------------------------------------------------
-- Nomenclatura dominio:recurso:accion (RF-10/RN01, acción atómica sobre
-- recurso). El dominio determina el módulo que lo puede volver inerte
-- (RF-10/RN04): dominio 'core' pertenece al núcleo y nunca se desactiva
-- (RF-01/RN06); el resto mapea a core.modulo por upper(dominio).
INSERT INTO core.permiso (dominio, recurso, accion, descripcion) VALUES
  -- ---------------- Núcleo: Módulos 2 a 7 (RF-05..RF-25) --------------------
  ('core', 'usuarios',     'crear',     'RF-05 Registrar usuario dentro del tenant'),
  ('core', 'usuarios',     'leer',      'RF-06 Consultar directorio de usuarios'),
  ('core', 'usuarios',     'editar',    'RF-07 Editar usuario'),
  ('core', 'usuarios',     'suspender', 'RF-08 Suspender o reactivar usuario'),

  ('core', 'roles',        'crear',     'RF-10 Registrar rol personalizado'),
  ('core', 'roles',        'leer',      'RF-11 Consultar catálogo de roles y permisos'),
  ('core', 'roles',        'editar',    'RF-12 Editar rol (modificar permisos)'),
  ('core', 'roles',        'eliminar',  'RF-13 Eliminar o desactivar rol'),

  ('core', 'asignaciones', 'crear',     'RF-14 Asignar roles a usuario'),
  ('core', 'asignaciones', 'eliminar',  'RF-15 Revocar rol de usuario'),

  ('core', 'sesiones',     'revocar',   'RF-19 Forzar el cierre de sesiones de un usuario'),

  ('core', 'bitacora',     'leer',      'RF-21 Consultar bitácora de auditoría'),
  ('core', 'bitacora',     'exportar',  'RF-24 Exportar bitácora de auditoría a archivo'),
  ('core', 'reportes',     'leer',      'RF-23 Generar reporte de actividad de usuarios'),

  ('core', 'politicas',    'leer',      'RF-22 Consultar políticas de seguridad del tenant'),
  ('core', 'politicas',    'editar',    'RF-22 Configurar políticas de seguridad del tenant'),

  -- ---------------- Módulo 8: Ventas / CRM (RF-26..RF-44) -------------------
  ('ventas', 'clientes',      'crear',    'RF-26 Registrar cliente'),
  ('ventas', 'clientes',      'leer',     'RF-27 Consultar / buscar clientes'),
  ('ventas', 'clientes',      'editar',   'RF-28 Editar cliente'),
  ('ventas', 'clientes',      'eliminar', 'RF-29 Dar de baja lógica a cliente'),

  ('ventas', 'oportunidades', 'crear',    'RF-30 Registrar oportunidad de venta'),
  ('ventas', 'oportunidades', 'leer',     'RF-31 Consultar pipeline de oportunidades'),
  ('ventas', 'oportunidades', 'editar',   'RF-32 Actualizar etapa de oportunidad'),
  ('ventas', 'oportunidades', 'cerrar',   'RF-33 Cerrar oportunidad (ganada / perdida)'),

  ('ventas', 'cotizaciones',  'crear',    'RF-34 Generar cotización'),
  ('ventas', 'cotizaciones',  'leer',     'RF-35 Consultar cotizaciones'),
  ('ventas', 'cotizaciones',  'editar',   'RF-36 Editar cotización'),
  ('ventas', 'cotizaciones',  'aprobar',  'RF-37 Aprobar / rechazar cotización'),

  ('ventas', 'pedidos',       'crear',    'RF-38 Registrar pedido de venta'),
  ('ventas', 'pedidos',       'leer',     'RF-39 Consultar pedidos de venta'),
  ('ventas', 'pedidos',       'editar',   'RF-40 Editar pedido de venta'),
  ('ventas', 'pedidos',       'cancelar', 'RF-41 Cancelar pedido de venta'),

  ('ventas', 'facturas',      'crear',    'RF-42 Generar factura de venta'),
  ('ventas', 'facturas',      'leer',     'RF-43 Consultar facturas de venta'),
  ('ventas', 'facturas',      'cancelar', 'RF-44 Cancelar factura / generar nota de crédito'),

  -- ---------------- Módulo 9: Compras (RF-45..RF-52) ------------------------
  ('compras', 'proveedores',       'crear',    'RF-45 Registrar proveedor'),
  ('compras', 'proveedores',       'leer',     'RF-46/RF-51 Consultar proveedor e historial de compras'),
  ('compras', 'proveedores',       'editar',   'RF-46 Editar proveedor'),
  ('compras', 'proveedores',       'eliminar', 'RF-46 Dar de baja proveedor'),

  ('compras', 'ordenes',           'crear',    'RF-47 Registrar orden de compra'),
  ('compras', 'ordenes',           'leer',     'RF-48 Consultar orden de compra'),
  ('compras', 'ordenes',           'editar',   'RF-48 Editar orden de compra'),
  ('compras', 'ordenes',           'cancelar', 'RF-48 Cancelar orden de compra'),
  ('compras', 'ordenes',           'aprobar',  'RF-52 Aprobar orden de compra sobre el umbral'),

  ('compras', 'recepciones',       'crear',    'RF-49 Registrar recepción de mercancía'),
  ('compras', 'recepciones',       'leer',     'RF-49 Consultar recepciones de mercancía'),

  ('compras', 'cuentas_por_pagar', 'leer',     'RF-50 Consultar cuentas por pagar generadas'),

  ('compras', 'config_aprobacion', 'leer',     'RF-52 Consultar umbral de aprobación de compras'),
  ('compras', 'config_aprobacion', 'editar',   'RF-52 Configurar umbral de aprobación de compras'),

  -- ---------------- Módulo 10: Inventario (RF-53..RF-64) --------------------
  ('inventario', 'productos',      'crear',    'RF-53 Registrar producto / artículo'),
  ('inventario', 'productos',      'leer',     'RF-54 Consultar catálogo de productos'),
  ('inventario', 'productos',      'editar',   'RF-55 Editar producto'),
  ('inventario', 'productos',      'eliminar', 'RF-56 Dar de baja / descontinuar producto'),

  ('inventario', 'almacenes',      'crear',    'RF-57 Registrar almacén / bodega'),
  ('inventario', 'almacenes',      'leer',     'RF-57 Consultar almacenes'),
  ('inventario', 'almacenes',      'editar',   'RF-57 Editar almacén'),
  ('inventario', 'almacenes',      'eliminar', 'RF-57 Dar de baja almacén'),

  ('inventario', 'movimientos',    'crear',    'RF-58 Registrar movimiento manual de inventario'),
  ('inventario', 'movimientos',    'leer',     'RF-58 Consultar movimientos de inventario'),

  ('inventario', 'stock',          'leer',     'RF-59 Consultar stock actual / disponibilidad'),

  ('inventario', 'ajustes',        'crear',    'RF-60 Registrar ajuste de inventario'),
  ('inventario', 'ajustes',        'leer',     'RF-60 Consultar ajustes de inventario'),

  ('inventario', 'transferencias', 'crear',    'RF-61 Registrar transferencia entre almacenes'),
  ('inventario', 'transferencias', 'leer',     'RF-61 Consultar transferencias entre almacenes'),

  ('inventario', 'kardex',         'leer',     'RF-62 Consultar kardex / historial de movimientos'),

  ('inventario', 'alertas',        'leer',     'RF-63 Consultar alertas de stock mínimo'),
  ('inventario', 'alertas',        'notificar','RF-63 Marcar alerta de stock mínimo como notificada'),

  ('inventario', 'valuacion',      'leer',     'RF-64 Consultar valuación de inventario'),

  -- ---------------- Finanzas dentro del alcance vigente ---------------------
  -- Único permiso del dominio: lo exige RF-38/RN02 (excepción al límite de
  -- crédito del cliente). El resto del Módulo Finanzas es RF-65..RF-93.
  ('finanzas', 'credito', 'autorizar', 'RF-38 Autorizar excepción de límite de crédito')
ON CONFLICT (codigo) DO NOTHING;
