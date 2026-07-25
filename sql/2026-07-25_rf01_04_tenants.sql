-- ============================================================================
-- NovaERP — RF-01..04: Administracion de Multi-tenencia (Modulo 1)
-- Fecha: 2026-07-25
--
-- Habilitado por la fundacion de sesion del SysAdmin (2026-07-25). Estas cuatro
-- RF son la superficie que el SysAdmin opera desde el portal de plataforma.
--
-- Piezas de esquema nuevas (decisiones aprobadas por el usuario):
--   1) Estado 'pendiente' del tenant (RF-01 postcondicion + cascada de
--      activacion). El enum solo tenia (activo, suspendido, baja_logica).
--   2) core.dominio_reservado : lista de dominios/palabras reservadas
--      (RF-01/RN07/CA10). Tabla, editable sin redeploy.
--   3) core.plan_modulo : que modulos incluye cada plan (RF-01/RN05). El nucleo
--      (fase 0) va en todos los planes (RN06); los modulos de negocio (fase 1)
--      segun el plan.
--   4) core.modulo_dependencia : dependencias funcionales modulo->modulo
--      (RF-03/RN05/RN07 cascada). Se siembra con las dependencias reales entre
--      los modulos EN ALCANCE (ventas y compras mueven inventario), no con los
--      ejemplos de la ERS que son de modulos fuera de alcance (Facturacion,
--      Nomina).
--
-- Idempotente.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) Estado 'pendiente' del tenant. ALTER TYPE ADD VALUE no puede usarse en la
--    misma transaccion en que se agrega; aqui solo se agrega, no se usa.
-- ----------------------------------------------------------------------------
ALTER TYPE core.tenant_estado ADD VALUE IF NOT EXISTS 'pendiente';


-- ----------------------------------------------------------------------------
-- 2) Dominios/palabras reservadas (RF-01/RN07/CA10). CITEXT: la comparacion es
--    case-insensitive, como el slug del tenant.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.dominio_reservado (
  palabra CITEXT PRIMARY KEY
);

INSERT INTO core.dominio_reservado (palabra) VALUES
  ('admin'), ('api'), ('www'), ('app'), ('mail'), ('smtp'), ('imap'), ('ftp'),
  ('root'), ('system'), ('sys'), ('sysadmin'), ('superadmin'), ('novaerp'),
  ('static'), ('cdn'), ('assets'), ('portal'), ('auth'), ('login'), ('logout'),
  ('dashboard'), ('status'), ('support'), ('help'), ('billing'), ('test'),
  ('staging'), ('dev'), ('internal')
ON CONFLICT (palabra) DO NOTHING;


-- ----------------------------------------------------------------------------
-- 3) Modulos incluidos por plan (RF-01/RN05).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.plan_modulo (
  plan_id   SMALLINT NOT NULL REFERENCES core.plan_comercial(id) ON DELETE CASCADE,
  modulo_id SMALLINT NOT NULL REFERENCES core.modulo(id) ON DELETE CASCADE,
  PRIMARY KEY (plan_id, modulo_id)
);

-- Nucleo (fase 0) en TODOS los planes: nunca puede deshabilitarse (RN06).
INSERT INTO core.plan_modulo (plan_id, modulo_id)
SELECT p.id, m.id
  FROM core.plan_comercial p
  JOIN core.modulo m ON m.fase = 0
ON CONFLICT DO NOTHING;

-- STARTER: nucleo + INVENTARIO.
INSERT INTO core.plan_modulo (plan_id, modulo_id)
SELECT p.id, m.id
  FROM core.plan_comercial p
  JOIN core.modulo m ON m.codigo = 'INVENTARIO'
 WHERE p.codigo = 'STARTER'
ON CONFLICT DO NOTHING;

-- BUSINESS y ENTERPRISE: nucleo + toda la fase 1 (ventas, compras, inventario).
-- Los modulos de fase 2 (RRHH, Finanzas, Proyectos, BPM, Reglas, BI) estan
-- FUERA DE ALCANCE (RF-65..93): no se incluyen en ningun plan todavia.
INSERT INTO core.plan_modulo (plan_id, modulo_id)
SELECT p.id, m.id
  FROM core.plan_comercial p
  JOIN core.modulo m ON m.fase = 1
 WHERE p.codigo IN ('BUSINESS', 'ENTERPRISE')
ON CONFLICT DO NOTHING;


-- ----------------------------------------------------------------------------
-- 4) Dependencias funcionales entre modulos (RF-03/RN05/RN07).
--    depende_de = modulo base que debe estar activo para activar el modulo.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.modulo_dependencia (
  modulo_id     SMALLINT NOT NULL REFERENCES core.modulo(id) ON DELETE CASCADE,
  depende_de_id SMALLINT NOT NULL REFERENCES core.modulo(id) ON DELETE CASCADE,
  PRIMARY KEY (modulo_id, depende_de_id),
  CHECK (modulo_id <> depende_de_id)
);

-- Dependencias reales entre los modulos EN ALCANCE: tanto Ventas (factura y
-- descuenta stock) como Compras (la recepcion genera movimientos de entrada)
-- dependen funcionalmente de Inventario.
INSERT INTO core.modulo_dependencia (modulo_id, depende_de_id)
SELECT m.id, d.id
  FROM core.modulo m, core.modulo d
 WHERE (m.codigo, d.codigo) IN (('VENTAS', 'INVENTARIO'), ('COMPRAS', 'INVENTARIO'))
ON CONFLICT DO NOTHING;
