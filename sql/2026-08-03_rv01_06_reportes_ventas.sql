-- ============================================================================
-- NovaERP — RV-01..06: Reportes de Ventas (extension post-ERS)
-- Fecha: 2026-08-03
--
-- Sprint de reporteria del dominio Ventas. Ver docs/SPRINT-REPORTES-VENTAS.md.
-- Esta migracion no crea tablas nuevas: los reportes agregan al vuelo sobre las
-- tablas transaccionales existentes (decision D5 del plan). Aporta tres cosas:
--
--   1) ventas.{cotizacion,pedido_venta,factura_venta}.vendedor_id : atribucion
--      comercial explicita (decision D1). Hoy el unico responsable es
--      oportunidad.responsable_id y la cadena factura -> pedido -> cotizacion ->
--      oportunidad tiene dos FK nulables, asi que derivar la atribucion dejaria
--      un cubo "sin asignar" grande e inestable. Nulable a proposito: el
--      historico anterior a este sprint no tiene atribucion y debe seguir
--      siendo legible.
--   2) Dos permisos de catalogo: ventas:reportes:leer y :exportar (decision D3).
--   3) Los indices que los reportes por rango de fechas necesitan: hoy estas
--      tablas no tienen NINGUNO mas alla de la PK y el unique de folio, asi que
--      cualquier agregado por periodo hace seq scan.
--
-- Idempotente.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) Atribucion de vendedor (D1 / RN-06).
-- ----------------------------------------------------------------------------
ALTER TABLE ventas.cotizacion    ADD COLUMN IF NOT EXISTS vendedor_id UUID REFERENCES core.usuario(id);
ALTER TABLE ventas.pedido_venta  ADD COLUMN IF NOT EXISTS vendedor_id UUID REFERENCES core.usuario(id);
ALTER TABLE ventas.factura_venta ADD COLUMN IF NOT EXISTS vendedor_id UUID REFERENCES core.usuario(id);

-- Backfill best-effort por la cadena existente. Solo rellena lo que esta en
-- NULL, asi que re-ejecutar no pisa una atribucion ya fijada por la app.
-- Lo que quede en NULL se reporta como 'sin_asignar' en RV-06; no se reparte
-- ni se imputa a nadie.

-- 1.a) La cotizacion hereda el responsable de su oportunidad.
UPDATE ventas.cotizacion c
   SET vendedor_id = o.responsable_id
  FROM ventas.oportunidad o
 WHERE o.id = c.oportunidad_id
   AND c.vendedor_id IS NULL
   AND o.responsable_id IS NOT NULL;

-- 1.b) El pedido hereda de su cotizacion.
UPDATE ventas.pedido_venta p
   SET vendedor_id = c.vendedor_id
  FROM ventas.cotizacion c
 WHERE c.id = p.cotizacion_id
   AND p.vendedor_id IS NULL
   AND c.vendedor_id IS NOT NULL;

-- 1.c) La factura hereda de su pedido.
UPDATE ventas.factura_venta f
   SET vendedor_id = p.vendedor_id
  FROM ventas.pedido_venta p
 WHERE p.id = f.pedido_id
   AND f.vendedor_id IS NULL
   AND p.vendedor_id IS NOT NULL;


-- ----------------------------------------------------------------------------
-- 2) Permisos del catalogo maestro (dominio 'ventas' -> modulo VENTAS).
--    Leer y exportar van separados: exportar saca datos del sistema y se audita
--    como evento propio EXPORT, mismo criterio que RF-24.
-- ----------------------------------------------------------------------------
INSERT INTO core.permiso (dominio, recurso, accion, descripcion) VALUES
  ('ventas','reportes','leer',     'RV-01..06 Consultar los reportes de ventas'),
  ('ventas','reportes','exportar', 'RV-01..06 Exportar los reportes de ventas a CSV/PDF')
ON CONFLICT (codigo) DO NOTHING;


-- ----------------------------------------------------------------------------
-- 3) Indices de soporte de los reportes.
--    Todos empiezan por tenant_id porque cada consulta esta acotada al tenant
--    del JWT antes que por cualquier otro criterio.
--    Nota: sin CONCURRENTLY (no admitido dentro de una transaccion). En una
--    base grande, ejecutar este bloque aparte y con CONCURRENTLY.
-- ----------------------------------------------------------------------------

-- RV-01/02/06: agregados de facturacion por periodo, cliente y vendedor.
CREATE INDEX IF NOT EXISTS idx_factura_venta_tenant_fecha
  ON ventas.factura_venta(tenant_id, fecha_emision);
CREATE INDEX IF NOT EXISTS idx_factura_venta_tenant_cliente
  ON ventas.factura_venta(tenant_id, cliente_id);
CREATE INDEX IF NOT EXISTS idx_factura_venta_tenant_vendedor
  ON ventas.factura_venta(tenant_id, vendedor_id);

-- RV-03: el ranking de productos recorre las lineas de las facturas del rango.
CREATE INDEX IF NOT EXISTS idx_factura_linea_factura
  ON ventas.factura_linea(factura_id);

-- RV-01/02: las notas de credito se agregan por su propia fecha (RN-02).
CREATE INDEX IF NOT EXISTS idx_nota_credito_tenant_fecha
  ON ventas.nota_credito(tenant_id, created_at);

-- RV-04: conteos del embudo por estado y periodo.
CREATE INDEX IF NOT EXISTS idx_cotizacion_tenant_estado
  ON ventas.cotizacion(tenant_id, estado, created_at);
CREATE INDEX IF NOT EXISTS idx_pedido_tenant_estado
  ON ventas.pedido_venta(tenant_id, estado, created_at);

-- RV-05: la cartera solo mira cuentas con saldo vivo; indice parcial.
CREATE INDEX IF NOT EXISTS idx_cxc_tenant_pendiente
  ON finanzas.cuenta_por_cobrar(tenant_id) WHERE saldo > 0;
