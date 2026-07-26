-- ============================================================================
-- NovaERP — RF-30..44: Ventas / CRM transaccional (Modulo 8)
-- Fecha: 2026-07-25
--
-- El esquema de ventas (oportunidad, cotizacion, pedido, factura, nota_credito
-- y sus lineas, con enums de estado y los triggers validar_cantidad_facturable
-- y validar_limite_credito) YA existe. Esta migracion solo agrega lo que falta
-- para la capa de servicios:
--
--   1) ventas.config_ventas : configuracion de ventas por tenant (impuestos,
--      descuento maximo, backorder). Decision del usuario = tabla.
--   2) ventas.oportunidad.fecha_cierre_estimada : la CA de RF-30 valida "fecha
--      de cierre anterior a hoy -> rechazada". Decision del usuario = agregar
--      la columna (la probabilidad se deriva de la etapa, sin columna).
--   3) Dos permisos que faltaban en el catalogo: ventas:pipeline:ver_todo
--      (RF-31, ver oportunidades de otros) y ventas:cotizaciones:ajustar_precio
--      (RF-34/RN01, ajuste manual de precio auditado).
--
-- Idempotente.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) Config de ventas por tenant.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ventas.config_ventas (
  tenant_id         UUID PRIMARY KEY REFERENCES core.tenant(id) ON DELETE CASCADE,
  iva_pct           NUMERIC(5,2) NOT NULL DEFAULT 16 CHECK (iva_pct BETWEEN 0 AND 100),      -- RF-42 impuestos
  descuento_max_pct NUMERIC(5,2) NOT NULL DEFAULT 100 CHECK (descuento_max_pct BETWEEN 0 AND 100), -- RF-34/RN03 (100 = sin tope)
  permite_backorder BOOLEAN NOT NULL DEFAULT FALSE,  -- RF-38/RN03 (por defecto se bloquea)
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RLS por tenant, consistente con el resto de tablas de ventas (defensa en
-- profundidad; el rol de la app en prod no debe tener BYPASSRLS).
ALTER TABLE ventas.config_ventas ENABLE ROW LEVEL SECURITY;
ALTER TABLE ventas.config_ventas FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ventas.config_ventas;
CREATE POLICY tenant_isolation ON ventas.config_ventas
  USING (core.is_sysadmin() OR tenant_id = core.current_tenant_id())
  WITH CHECK (core.is_sysadmin() OR tenant_id = core.current_tenant_id());

-- Fila por defecto para cada tenant existente (los servicios usan get_or_create
-- para tenants nuevos, pero sembrar los actuales evita la primera escritura).
INSERT INTO ventas.config_ventas (tenant_id)
SELECT id FROM core.tenant
ON CONFLICT (tenant_id) DO NOTHING;


-- ----------------------------------------------------------------------------
-- 2) Fecha estimada de cierre de la oportunidad (RF-30).
-- ----------------------------------------------------------------------------
ALTER TABLE ventas.oportunidad ADD COLUMN IF NOT EXISTS fecha_cierre_estimada DATE;


-- ----------------------------------------------------------------------------
-- 2b) Reserva de stock del pedido (RF-38/41/42). stock_actual.reservado es un
--     agregado por (producto, almacen); para reservar/liberar/consumir con
--     precision se rastrea por pedido: el almacen de surtido (fijado al
--     confirmar) y la cantidad reservada por linea.
-- ----------------------------------------------------------------------------
ALTER TABLE ventas.pedido_venta ADD COLUMN IF NOT EXISTS almacen_id UUID REFERENCES inventario.almacen(id);
ALTER TABLE ventas.pedido_linea ADD COLUMN IF NOT EXISTS cantidad_reservada NUMERIC(12,3) NOT NULL DEFAULT 0;


-- ----------------------------------------------------------------------------
-- 3) Permisos faltantes del catalogo maestro (dominio 'ventas' -> modulo VENTAS).
-- ----------------------------------------------------------------------------
INSERT INTO core.permiso (dominio, recurso, accion, descripcion) VALUES
  ('ventas','pipeline','ver_todo','Ver el pipeline de oportunidades de todos los vendedores'),
  ('ventas','cotizaciones','ajustar_precio','Ajustar manualmente el precio de una linea de cotizacion')
ON CONFLICT (codigo) DO NOTHING;
