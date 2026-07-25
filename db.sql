-- ============================================================================
-- NovaERP — Modelo de datos PostgreSQL
-- 01_core_fase0.sql
-- Núcleo transversal (Fase 0): Multi-tenencia, Usuarios, RBAC, Autenticación,
-- Auditoría, Configuración de seguridad.
-- Basado en ERS_NovaERP_IEEE830 (RF-01 a RF-25, RNF-01 a RNF-19)
-- ============================================================================

DROP SCHEMA IF EXISTS bi CASCADE;
DROP SCHEMA IF EXISTS reglas CASCADE;
DROP SCHEMA IF EXISTS bpm CASCADE;
DROP SCHEMA IF EXISTS proyectos CASCADE;
DROP SCHEMA IF EXISTS finanzas CASCADE;
DROP SCHEMA IF EXISTS rrhh CASCADE;
DROP SCHEMA IF EXISTS inventario CASCADE;
DROP SCHEMA IF EXISTS compras CASCADE;
DROP SCHEMA IF EXISTS ventas CASCADE;
DROP SCHEMA IF EXISTS core CASCADE;

-- ----------------------------------------------------------------------------
-- 0. EXTENSIONES Y CONFIGURACIÓN GENERAL
-- ----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid(), digest()
CREATE EXTENSION IF NOT EXISTS citext;      -- correos case-insensitive únicos
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- búsquedas ILIKE rápidas (RF-02,RF-06,RF-27...)

CREATE SCHEMA IF NOT EXISTS core;
SET search_path TO core, public;

-- Función genérica de "updated_at" (se reutiliza en todos los módulos)
CREATE OR REPLACE FUNCTION core.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ----------------------------------------------------------------------------
-- 1. MÓDULO 1 — ADMINISTRACIÓN DE MULTI-TENENCIA (RF-01..RF-04)
-- ----------------------------------------------------------------------------
CREATE TYPE core.tenant_estado AS ENUM ('activo', 'suspendido', 'baja_logica');

CREATE TABLE core.plan_comercial (
  id            SMALLINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  codigo        TEXT NOT NULL UNIQUE,            -- ej. 'STARTER','BUSINESS','ENTERPRISE'
  nombre        TEXT NOT NULL,
  licencias_max INTEGER NOT NULL CHECK (licencias_max > 0),
  activo        BOOLEAN NOT NULL DEFAULT TRUE
);

-- Catálogo maestro de módulos activables por plan/tenant (feature-flags, 2.1)
CREATE TABLE core.modulo (
  id        SMALLINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  codigo    TEXT NOT NULL UNIQUE,   -- 'VENTAS','COMPRAS','INVENTARIO','RRHH','FINANZAS','PROYECTOS','BPM','REGLAS','BI'
  nombre    TEXT NOT NULL,
  fase      SMALLINT NOT NULL CHECK (fase IN (0,1,2,3))
);

CREATE TABLE core.tenant (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              CITEXT NOT NULL UNIQUE,        -- subdominio: {slug}.novaerp.com (RF-16/RN06)
  razon_social      TEXT NOT NULL,
  dominio_comercial TEXT NOT NULL,
  plan_id           SMALLINT NOT NULL REFERENCES core.plan_comercial(id),
  estado            core.tenant_estado NOT NULL DEFAULT 'activo',
  motivo_suspension TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (razon_social, dominio_comercial)          -- unicidad de alta (RF-01/precondición)
);
CREATE TRIGGER trg_tenant_updated BEFORE UPDATE ON core.tenant
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();

-- Activación de módulos por tenant (RF-03)
CREATE TABLE core.tenant_modulo (
  tenant_id   UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  modulo_id   SMALLINT NOT NULL REFERENCES core.modulo(id),
  activo      BOOLEAN NOT NULL DEFAULT TRUE,
  activado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, modulo_id)
);

-- ----------------------------------------------------------------------------
-- 2. MÓDULO 2/3 — USUARIOS Y RBAC (RF-05..RF-15)
-- ----------------------------------------------------------------------------
CREATE TYPE core.usuario_estado AS ENUM ('pendiente', 'activo', 'suspendido', 'pendiente_verificacion');

-- SysAdmin (administración global de la plataforma) vive fuera del tenant.
CREATE TABLE core.sysadmin (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  correo        CITEXT NOT NULL UNIQUE,           -- único a nivel de plataforma (RF-16/RN06)
  password_hash TEXT NOT NULL,                    -- Argon2id/bcrypt (RNF-01)
  mfa_secret    TEXT,                              -- TOTP, cifrado a nivel de aplicación
  activo        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE core.usuario (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  correo              CITEXT NOT NULL,
  nombre_completo     TEXT NOT NULL,
  telefono            TEXT,                        -- RF-07 (dato personal)
  puesto              TEXT,                        -- RF-06/CA02 (busqueda del directorio)
  departamento        TEXT,                        -- RF-06/CA03 (filtro del directorio)
  password_hash       TEXT,                        -- NULL hasta activación (RF-05 flujo)
  mfa_secret          TEXT,
  mfa_enrolado        BOOLEAN NOT NULL DEFAULT FALSE,
  estado              core.usuario_estado NOT NULL DEFAULT 'pendiente',
  token_activacion     TEXT,
  token_activacion_exp TIMESTAMPTZ,
  intentos_fallidos    SMALLINT NOT NULL DEFAULT 0,
  bloqueado_hasta      TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, correo)                        -- correo único DENTRO del tenant (RF-05/RN06)
);
CREATE TRIGGER trg_usuario_updated BEFORE UPDATE ON core.usuario
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();
CREATE INDEX idx_usuario_tenant ON core.usuario(tenant_id);
CREATE INDEX idx_usuario_nombre_trgm ON core.usuario USING gin (nombre_completo gin_trgm_ops);

-- Catálogo maestro de permisos (acción atómica sobre recurso, ej. ventas:cotizaciones:crear)
CREATE TABLE core.permiso (
  id       SERIAL PRIMARY KEY,
  dominio  TEXT NOT NULL,          -- 'ventas','inventario','finanzas', etc.
  recurso  TEXT NOT NULL,          -- 'cotizaciones','ordenes_compra', etc.
  accion   TEXT NOT NULL,          -- 'crear','leer','editar','eliminar','aprobar', etc.
  codigo   TEXT NOT NULL UNIQUE GENERATED ALWAYS AS (dominio || ':' || recurso || ':' || accion) STORED,
  descripcion TEXT
);

CREATE TABLE core.rol (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID REFERENCES core.tenant(id) ON DELETE CASCADE,  -- NULL = rol de sistema (TENANT_ADMIN)
  nombre        TEXT NOT NULL,
  es_sistema    BOOLEAN NOT NULL DEFAULT FALSE,     -- TENANT_ADMIN: no editable/eliminable (RN02)
  activo        BOOLEAN NOT NULL DEFAULT TRUE,       -- baja lógica (RF-13/RN01)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, nombre)                          -- único dentro del tenant, repetible entre tenants (RN03)
);

CREATE TABLE core.rol_permiso (
  rol_id     UUID NOT NULL REFERENCES core.rol(id) ON DELETE CASCADE,
  permiso_id INTEGER NOT NULL REFERENCES core.permiso(id),
  PRIMARY KEY (rol_id, permiso_id)
);

CREATE TABLE core.usuario_rol (
  usuario_id UUID NOT NULL REFERENCES core.usuario(id) ON DELETE CASCADE,
  rol_id     UUID NOT NULL REFERENCES core.rol(id),
  asignado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  asignado_por UUID REFERENCES core.usuario(id),
  PRIMARY KEY (usuario_id, rol_id)
);
-- RN04 (todo usuario conserva >=1 rol) y RN06 (no eliminar último TENANT_ADMIN)
-- se aplican con triggers en 05_triggers_negocio.sql (no expresables como CHECK simple).

-- ----------------------------------------------------------------------------
-- 3. MÓDULO 4 — AUTENTICACIÓN Y SESIÓN (RF-16..RF-19)
-- ----------------------------------------------------------------------------
CREATE TABLE core.sesion (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  usuario_id    UUID NOT NULL REFERENCES core.usuario(id) ON DELETE CASCADE,
  jwt_id        TEXT NOT NULL UNIQUE,        -- jti del JWT emitido
  ip_origen     INET,
  user_agent    TEXT,
  emitida_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expira_en     TIMESTAMPTZ NOT NULL,        -- configurable por tenant, default 8h (RN03)
  revocada_en   TIMESTAMPTZ,
  revocada_por  UUID REFERENCES core.usuario(id)
);
CREATE INDEX idx_sesion_usuario_activa ON core.sesion(usuario_id) WHERE revocada_en IS NULL;

-- Sesion del SysAdmin (portal de plataforma). El SysAdmin vive fuera de todo
-- tenant y no tiene fila en core.usuario, asi que no cabe en core.sesion
-- (tenant_id/usuario_id NOT NULL). Tabla propia, misma semantica de revocacion.
-- Ver sql/2026-07-25_sysadmin_sesion.sql. Tabla global de plataforma: sin RLS
-- por tenant (como core.sysadmin) y sin trigger fn_auditar (sus eventos
-- LOGIN/LOGOUT se registran como eventos de plataforma manuales).
CREATE TABLE core.sesion_sysadmin (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sysadmin_id   UUID NOT NULL REFERENCES core.sysadmin(id) ON DELETE CASCADE,
  jwt_id        TEXT NOT NULL UNIQUE,
  ip_origen     INET,
  user_agent    TEXT,
  emitida_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expira_en     TIMESTAMPTZ NOT NULL,
  revocada_en   TIMESTAMPTZ,
  revocada_por  UUID REFERENCES core.sysadmin(id)
);
CREATE INDEX idx_sesion_sysadmin_activa ON core.sesion_sysadmin(sysadmin_id) WHERE revocada_en IS NULL;

-- ----------------------------------------------------------------------------
-- 4. MÓDULO 5 — AUDITORÍA Y CUMPLIMIENTO (RF-20..RF-21)
-- ----------------------------------------------------------------------------
-- Append-only, inmutable, retención >= 5 años (RNF-07 / ISO 27001).
CREATE TABLE core.log_auditoria (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     UUID REFERENCES core.tenant(id),    -- NULL para eventos de plataforma (SysAdmin)
  usuario_id    UUID REFERENCES core.usuario(id),
  entidad       TEXT NOT NULL,        -- 'usuario','rol','cotizacion', etc.
  entidad_id    TEXT NOT NULL,
  operacion     TEXT NOT NULL,        -- 'CREATE','UPDATE','DELETE','LOGIN','LOGIN_FAILED', etc.
  valores_antes JSONB,
  valores_despues JSONB,
  criticidad    TEXT NOT NULL DEFAULT 'NORMAL' CHECK (criticidad IN ('BAJA','NORMAL','ALTA')),
  ip_origen     INET,
  ocurrido_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_auditoria_tenant_fecha ON core.log_auditoria(tenant_id, ocurrido_en DESC);
CREATE INDEX idx_auditoria_entidad ON core.log_auditoria(entidad, entidad_id);

-- Regla append-only: bloquea UPDATE/DELETE a nivel de motor (RNF-07)
CREATE OR REPLACE FUNCTION core.bloquear_mutacion_auditoria()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'log_auditoria es append-only: % no permitido', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_auditoria_no_update
  BEFORE UPDATE OR DELETE ON core.log_auditoria
  FOR EACH ROW EXECUTE FUNCTION core.bloquear_mutacion_auditoria();

-- ----------------------------------------------------------------------------
-- 5. MÓDULO 6 — CONFIGURACIÓN DE SEGURIDAD DEL TENANT (RF-22)
-- ----------------------------------------------------------------------------
CREATE TABLE core.config_seguridad_tenant (
  tenant_id                 UUID PRIMARY KEY REFERENCES core.tenant(id) ON DELETE CASCADE,
  politica_password_min_len SMALLINT NOT NULL DEFAULT 12,
  politica_password_regex   TEXT,
  jwt_expiracion_horas      SMALLINT NOT NULL DEFAULT 8,
  intentos_max_ventana      SMALLINT NOT NULL DEFAULT 5,    -- RN02: 5 intentos / 15 min
  ventana_minutos           SMALLINT NOT NULL DEFAULT 15,
  bloqueo_minutos           SMALLINT NOT NULL DEFAULT 30,
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_config_seg_updated BEFORE UPDATE ON core.config_seguridad_tenant
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();

-- ----------------------------------------------------------------------------
-- 6. MÓDULO 7 — REPORTERÍA BÁSICA Y NOTIFICACIONES (RF-23..RF-25)
-- ----------------------------------------------------------------------------
CREATE TABLE core.notificacion (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID REFERENCES core.tenant(id) ON DELETE CASCADE,
  usuario_id   UUID REFERENCES core.usuario(id),
  canal        TEXT NOT NULL DEFAULT 'email' CHECK (canal IN ('email','webhook','slack')),
  asunto       TEXT NOT NULL,
  cuerpo       TEXT,
  estado       TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','enviada','en_cola_reintento','fallida')),
  intentos     SMALLINT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  enviada_en   TIMESTAMPTZ
);
CREATE INDEX idx_notificacion_pendientes ON core.notificacion(estado) WHERE estado IN ('pendiente','en_cola_reintento');





-- ============================================================================
-- NovaERP — 02_fase1_comercial.sql
-- Fase 1 — MVP Comercial: Ventas/CRM, Compras, Inventario
-- (RF-26..RF-64 / CU-26..CU-64)
-- ============================================================================
SET search_path TO core, public;
CREATE SCHEMA IF NOT EXISTS ventas;
CREATE SCHEMA IF NOT EXISTS compras;
CREATE SCHEMA IF NOT EXISTS inventario;

-- ----------------------------------------------------------------------------
-- MÓDULO 8 — VENTAS / CRM (RF-26..RF-44)
-- ----------------------------------------------------------------------------
CREATE TABLE ventas.cliente (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  rfc_o_id_fiscal  TEXT,
  razon_social     TEXT NOT NULL,
  correo           CITEXT,
  telefono         TEXT,
  limite_credito   NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (limite_credito >= 0),  -- RF-38/RN02
  activo           BOOLEAN NOT NULL DEFAULT TRUE,          -- baja lógica (RF-29)
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, rfc_o_id_fiscal)
);
CREATE TRIGGER trg_cliente_updated BEFORE UPDATE ON ventas.cliente
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();
CREATE INDEX idx_cliente_tenant ON ventas.cliente(tenant_id);
CREATE INDEX idx_cliente_razon_trgm ON ventas.cliente USING gin (razon_social gin_trgm_ops);

CREATE TYPE ventas.oportunidad_estado AS ENUM
  ('abierta','ganada','perdida');
CREATE TYPE ventas.oportunidad_etapa AS ENUM
  ('prospeccion','calificacion','propuesta','negociacion','cierre');

CREATE TABLE ventas.oportunidad (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  cliente_id     UUID NOT NULL REFERENCES ventas.cliente(id),
  nombre         TEXT NOT NULL,
  valor_estimado NUMERIC(14,2) CHECK (valor_estimado >= 0),
  etapa          ventas.oportunidad_etapa NOT NULL DEFAULT 'prospeccion',  -- RF-32
  estado         ventas.oportunidad_estado NOT NULL DEFAULT 'abierta',     -- RF-33
  motivo_perdida TEXT,   -- obligatorio si estado='perdida' (RN01), validado por trigger
  responsable_id UUID REFERENCES core.usuario(id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_motivo_perdida CHECK (
    (estado <> 'perdida') OR (motivo_perdida IS NOT NULL)
  )
);
CREATE TRIGGER trg_oportunidad_updated BEFORE UPDATE ON ventas.oportunidad
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();
CREATE INDEX idx_oportunidad_tenant_estado ON ventas.oportunidad(tenant_id, estado);

CREATE TYPE ventas.cotizacion_estado AS ENUM
  ('borrador','pendiente_aprobacion','aprobada','rechazada');

CREATE TABLE ventas.cotizacion (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  folio          TEXT NOT NULL,
  cliente_id     UUID NOT NULL REFERENCES ventas.cliente(id),
  oportunidad_id UUID REFERENCES ventas.oportunidad(id),   -- origen "Generar cotización" (RF-33/RN02)
  estado         ventas.cotizacion_estado NOT NULL DEFAULT 'borrador',
  subtotal       NUMERIC(14,2) NOT NULL DEFAULT 0,
  descuento_pct  NUMERIC(5,2)  NOT NULL DEFAULT 0 CHECK (descuento_pct BETWEEN 0 AND 100),
  total          NUMERIC(14,2) NOT NULL DEFAULT 0,
  vigente_hasta  DATE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, folio)
);
CREATE TRIGGER trg_cotizacion_updated BEFORE UPDATE ON ventas.cotizacion
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();

CREATE TABLE ventas.cotizacion_linea (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cotizacion_id  UUID NOT NULL REFERENCES ventas.cotizacion(id) ON DELETE CASCADE,
  producto_id    UUID NOT NULL,  -- FK a inventario.producto (se agrega en 03_fk_cruzadas.sql)
  descripcion    TEXT NOT NULL,
  cantidad       NUMERIC(12,3) NOT NULL CHECK (cantidad > 0),
  precio_unitario NUMERIC(14,4) NOT NULL CHECK (precio_unitario >= 0),
  importe        NUMERIC(14,2) GENERATED ALWAYS AS (cantidad * precio_unitario) STORED
);

CREATE TYPE ventas.pedido_estado AS ENUM
  ('borrador','confirmado','pendiente_surtido','cancelado','facturado_parcial','facturado_total');

CREATE TABLE ventas.pedido_venta (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  folio          TEXT NOT NULL,
  cliente_id     UUID NOT NULL REFERENCES ventas.cliente(id),
  cotizacion_id  UUID REFERENCES ventas.cotizacion(id),   -- RN01: origina de cotización aprobada, u opcional
  estado         ventas.pedido_estado NOT NULL DEFAULT 'borrador',
  total          NUMERIC(14,2) NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, folio)
);
CREATE TRIGGER trg_pedido_updated BEFORE UPDATE ON ventas.pedido_venta
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();

CREATE TABLE ventas.pedido_linea (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pedido_id       UUID NOT NULL REFERENCES ventas.pedido_venta(id) ON DELETE CASCADE,
  producto_id     UUID NOT NULL,   -- FK cruzada (03)
  cantidad        NUMERIC(12,3) NOT NULL CHECK (cantidad > 0),
  cantidad_facturada NUMERIC(12,3) NOT NULL DEFAULT 0,   -- controla "pendiente de facturar" (RF-42/RN01)
  precio_unitario NUMERIC(14,4) NOT NULL CHECK (precio_unitario >= 0),
  CONSTRAINT ck_cant_facturada CHECK (cantidad_facturada <= cantidad)
);

CREATE TYPE ventas.factura_estado AS ENUM ('emitida','cancelada','con_nota_credito');

CREATE TABLE ventas.factura_venta (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  folio         TEXT NOT NULL,
  pedido_id     UUID NOT NULL REFERENCES ventas.pedido_venta(id),
  cliente_id    UUID NOT NULL REFERENCES ventas.cliente(id),
  estado        ventas.factura_estado NOT NULL DEFAULT 'emitida',
  subtotal      NUMERIC(14,2) NOT NULL,
  impuestos     NUMERIC(14,2) NOT NULL DEFAULT 0,
  total         NUMERIC(14,2) NOT NULL,
  fecha_emision TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, folio)
);

CREATE TABLE ventas.factura_linea (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  factura_id  UUID NOT NULL REFERENCES ventas.factura_venta(id) ON DELETE CASCADE,
  pedido_linea_id UUID NOT NULL REFERENCES ventas.pedido_linea(id),
  cantidad    NUMERIC(12,3) NOT NULL CHECK (cantidad > 0),
  precio_unitario NUMERIC(14,4) NOT NULL,
  importe     NUMERIC(14,2) GENERATED ALWAYS AS (cantidad * precio_unitario) STORED
);

CREATE TABLE ventas.nota_credito (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  factura_id  UUID NOT NULL REFERENCES ventas.factura_venta(id),
  motivo      TEXT NOT NULL,
  monto       NUMERIC(14,2) NOT NULL CHECK (monto > 0),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- MÓDULO 9 — COMPRAS (RF-45..RF-52)
-- ----------------------------------------------------------------------------
CREATE TABLE compras.proveedor (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  rfc_o_id_fiscal TEXT NOT NULL,   -- único dentro del tenant (RN01)
  razon_social    TEXT NOT NULL,
  correo          CITEXT,
  telefono        TEXT,
  activo          BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, rfc_o_id_fiscal)
);
CREATE TRIGGER trg_proveedor_updated BEFORE UPDATE ON compras.proveedor
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();

CREATE TYPE compras.orden_estado AS ENUM
  ('borrador','pendiente_aprobacion','enviada','recibida_parcial','recibida_total','cancelada');

-- Umbral de aprobación de compras por monto (RF-52), parametrizable por tenant
CREATE TABLE compras.config_aprobacion (
  tenant_id      UUID PRIMARY KEY REFERENCES core.tenant(id) ON DELETE CASCADE,
  umbral_monto   NUMERIC(14,2) NOT NULL DEFAULT 0
);

CREATE TABLE compras.orden_compra (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  folio          TEXT NOT NULL,
  proveedor_id   UUID NOT NULL REFERENCES compras.proveedor(id),
  estado         compras.orden_estado NOT NULL DEFAULT 'borrador',
  total          NUMERIC(14,2) NOT NULL DEFAULT 0,   -- dispara flujo aprobación si > umbral (RN01)
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, folio)
);
CREATE TRIGGER trg_orden_compra_updated BEFORE UPDATE ON compras.orden_compra
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();

CREATE TABLE compras.orden_compra_linea (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id      UUID NOT NULL REFERENCES compras.orden_compra(id) ON DELETE CASCADE,
  producto_id   UUID NOT NULL,    -- FK cruzada (03)
  cantidad      NUMERIC(12,3) NOT NULL CHECK (cantidad > 0),
  cantidad_recibida NUMERIC(12,3) NOT NULL DEFAULT 0,
  costo_unitario NUMERIC(14,4) NOT NULL CHECK (costo_unitario >= 0),
  CONSTRAINT ck_cant_recibida CHECK (cantidad_recibida <= cantidad)
);

CREATE TABLE compras.recepcion_mercancia (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  orden_id     UUID NOT NULL REFERENCES compras.orden_compra(id),
  almacen_id   UUID NOT NULL,     -- FK cruzada (03)
  recibido_por UUID REFERENCES core.usuario(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE compras.recepcion_linea (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  recepcion_id       UUID NOT NULL REFERENCES compras.recepcion_mercancia(id) ON DELETE CASCADE,
  orden_compra_linea_id UUID NOT NULL REFERENCES compras.orden_compra_linea(id),
  cantidad           NUMERIC(12,3) NOT NULL CHECK (cantidad > 0),
  costo_unitario     NUMERIC(14,4) NOT NULL   -- actualiza costo de referencia del producto (RN02)
);

-- ----------------------------------------------------------------------------
-- MÓDULO 10 — INVENTARIO (RF-53..RF-64)
-- ----------------------------------------------------------------------------
CREATE TABLE inventario.producto (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  sku              TEXT NOT NULL,
  nombre           TEXT NOT NULL,
  descripcion      TEXT,
  costo_referencia NUMERIC(14,4) NOT NULL DEFAULT 0,   -- actualizado por recepción (RF-49/RN02)
  precio_venta     NUMERIC(14,4) NOT NULL DEFAULT 0,
  stock_minimo     NUMERIC(12,3) NOT NULL DEFAULT 0,    -- umbral de alerta (RF-63)
  activo           BOOLEAN NOT NULL DEFAULT TRUE,        -- baja/descontinuar (RF-56)
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, sku)
);
CREATE TRIGGER trg_producto_updated BEFORE UPDATE ON inventario.producto
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();
CREATE INDEX idx_producto_nombre_trgm ON inventario.producto USING gin (nombre gin_trgm_ops);

CREATE TABLE inventario.almacen (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  nombre     TEXT NOT NULL,
  ubicacion  TEXT,
  activo     BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (tenant_id, nombre)
);

CREATE TYPE inventario.movimiento_tipo AS ENUM
  ('entrada','salida','ajuste_positivo','ajuste_negativo','transferencia_salida','transferencia_entrada');

-- Bitácora inmutable de movimientos = Kardex (RF-62). Es la fuente de verdad del stock.
CREATE TABLE inventario.movimiento (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id      UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  producto_id    UUID NOT NULL REFERENCES inventario.producto(id),
  almacen_id     UUID NOT NULL REFERENCES inventario.almacen(id),
  tipo           inventario.movimiento_tipo NOT NULL,
  cantidad       NUMERIC(12,3) NOT NULL CHECK (cantidad > 0),
  costo_unitario NUMERIC(14,4),
  referencia_tipo TEXT,     -- 'factura_venta','recepcion_compra','ajuste','transferencia'
  referencia_id   TEXT,
  creado_por      UUID REFERENCES core.usuario(id),  -- NULL si automatizado
  ocurrido_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_movimiento_producto_almacen ON inventario.movimiento(tenant_id, producto_id, almacen_id, ocurrido_en);

-- Snapshot de stock cacheado, alto uso (RF-59: "debe cachearse"), mantenido por trigger.
CREATE TABLE inventario.stock_actual (
  tenant_id    UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  producto_id  UUID NOT NULL REFERENCES inventario.producto(id),
  almacen_id   UUID NOT NULL REFERENCES inventario.almacen(id),
  cantidad     NUMERIC(12,3) NOT NULL DEFAULT 0,
  reservado    NUMERIC(12,3) NOT NULL DEFAULT 0,    -- reservas de pedidos confirmados (RF-38)
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, producto_id, almacen_id)
);

CREATE TABLE inventario.ajuste_inventario (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  almacen_id   UUID NOT NULL REFERENCES inventario.almacen(id),
  producto_id  UUID NOT NULL REFERENCES inventario.producto(id),
  motivo       TEXT NOT NULL CHECK (motivo IN ('conteo_fisico','merma','otro')),
  cantidad     NUMERIC(12,3) NOT NULL,   -- +/- según ajuste_positivo/negativo
  aprobado_por UUID REFERENCES core.usuario(id),   -- puede requerir aprobación vía BPM (RF-84)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE inventario.transferencia (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  producto_id     UUID NOT NULL REFERENCES inventario.producto(id),
  almacen_origen  UUID NOT NULL REFERENCES inventario.almacen(id),
  almacen_destino UUID NOT NULL REFERENCES inventario.almacen(id),
  cantidad        NUMERIC(12,3) NOT NULL CHECK (cantidad > 0),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (almacen_origen <> almacen_destino)
);

CREATE TABLE inventario.alerta_stock_minimo (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id   UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  producto_id UUID NOT NULL REFERENCES inventario.producto(id),
  almacen_id  UUID NOT NULL REFERENCES inventario.almacen(id),
  cantidad_al_disparo NUMERIC(12,3) NOT NULL,
  disparada_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  notificada   BOOLEAN NOT NULL DEFAULT FALSE
);





-- ============================================================================
-- NovaERP — 03_fase2_consolidacion.sql
-- Fase 2 — Consolidación: RRHH/Nómina, Finanzas Avanzada, Proyectos,
-- Motor de Workflow (BPM), Motor de Reglas de Negocio, Reportería/BI
-- (RF-65..RF-93 / CU-65..CU-93)
-- ============================================================================
SET search_path TO core, public;
CREATE SCHEMA IF NOT EXISTS rrhh;
CREATE SCHEMA IF NOT EXISTS finanzas;
CREATE SCHEMA IF NOT EXISTS proyectos;
CREATE SCHEMA IF NOT EXISTS bpm;
CREATE SCHEMA IF NOT EXISTS reglas;
CREATE SCHEMA IF NOT EXISTS bi;

-- ----------------------------------------------------------------------------
-- MÓDULO 11 — RRHH (RF-65..RF-72)
-- ----------------------------------------------------------------------------
CREATE TABLE rrhh.empleado (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  usuario_id     UUID REFERENCES core.usuario(id),   -- vínculo opcional si comparte correo (RN01)
  nombre_completo TEXT NOT NULL,
  puesto         TEXT,
  salario_base   NUMERIC(14,2) NOT NULL CHECK (salario_base >= 0),
  clabe          TEXT,                                -- puede ser inválida sin bloquear alta (Notas RF-65..72)
  fecha_ingreso  DATE NOT NULL,
  fecha_baja     DATE,
  activo         BOOLEAN NOT NULL DEFAULT TRUE,        -- RF-67
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_empleado_updated BEFORE UPDATE ON rrhh.empleado
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();

CREATE TABLE rrhh.asistencia (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id    UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  empleado_id  UUID NOT NULL REFERENCES rrhh.empleado(id),
  tipo         TEXT NOT NULL CHECK (tipo IN ('entrada','salida')),
  registrado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_asistencia_empleado_fecha ON rrhh.asistencia(tenant_id, empleado_id, registrado_en);

CREATE TYPE rrhh.nomina_estado AS ENUM ('calculada','aprobada','dispersada');

CREATE TABLE rrhh.periodo_nomina (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  periodo_inicio DATE NOT NULL,
  periodo_fin    DATE NOT NULL,
  estado         rrhh.nomina_estado NOT NULL DEFAULT 'calculada',
  aprobado_por   UUID REFERENCES core.usuario(id),      -- RF-71
  aprobado_en    TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, periodo_inicio, periodo_fin)
);

CREATE TABLE rrhh.recibo_nomina (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  periodo_id     UUID NOT NULL REFERENCES rrhh.periodo_nomina(id) ON DELETE CASCADE,
  empleado_id    UUID NOT NULL REFERENCES rrhh.empleado(id),
  percepciones   NUMERIC(14,2) NOT NULL DEFAULT 0,
  deducciones    NUMERIC(14,2) NOT NULL DEFAULT 0,
  neto_pagar     NUMERIC(14,2) GENERATED ALWAYS AS (percepciones - deducciones) STORED,
  bloqueado_por_clabe_invalida BOOLEAN NOT NULL DEFAULT FALSE,  -- RF-70 nota
  UNIQUE (periodo_id, empleado_id)
);

-- ----------------------------------------------------------------------------
-- MÓDULO 12 — FINANZAS AVANZADA: CxC / CxP / Cierre contable (RF-73..RF-79)
-- ----------------------------------------------------------------------------
CREATE TABLE finanzas.cuenta_por_cobrar (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  cliente_id   UUID NOT NULL,      -- FK cruzada -> ventas.cliente
  factura_id   UUID NOT NULL,      -- FK cruzada -> ventas.factura_venta (creada automáticamente, RF-42/RN03)
  monto_original NUMERIC(14,2) NOT NULL CHECK (monto_original >= 0),
  saldo        NUMERIC(14,2) NOT NULL CHECK (saldo >= 0),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE finanzas.abono_cxc (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cxc_id       UUID NOT NULL REFERENCES finanzas.cuenta_por_cobrar(id),
  monto        NUMERIC(14,2) NOT NULL CHECK (monto > 0),
  metodo_pago  TEXT,
  registrado_por UUID REFERENCES core.usuario(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE finanzas.cuenta_por_pagar (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  proveedor_id   UUID NOT NULL,     -- FK cruzada -> compras.proveedor
  origen_tipo    TEXT NOT NULL CHECK (origen_tipo IN ('recepcion_mercancia','factura_proveedor')),
  origen_id      UUID NOT NULL,     -- RF-75, disparado desde RF-49/RF-50
  monto_original NUMERIC(14,2) NOT NULL CHECK (monto_original >= 0),
  saldo          NUMERIC(14,2) NOT NULL CHECK (saldo >= 0),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE finanzas.pago_proveedor (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cxp_id       UUID NOT NULL REFERENCES finanzas.cuenta_por_pagar(id),
  monto        NUMERIC(14,2) NOT NULL CHECK (monto > 0),
  metodo_pago  TEXT,
  registrado_por UUID REFERENCES core.usuario(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE finanzas.cierre_estado AS ENUM ('en_proceso','cerrado');

CREATE TABLE finanzas.cierre_contable (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  periodo_inicio DATE NOT NULL,
  periodo_fin    DATE NOT NULL,
  estado         finanzas.cierre_estado NOT NULL DEFAULT 'en_proceso',
  ejecutado_por  UUID REFERENCES core.usuario(id),
  cerrado_en     TIMESTAMPTZ,
  UNIQUE (tenant_id, periodo_inicio, periodo_fin)
);
-- Balance general / estado de resultados (RF-79) se resuelven como vistas
-- agregadas sobre movimientos, no como tablas propias (ver 06_vistas_reportes.sql).

-- ----------------------------------------------------------------------------
-- MÓDULO 13 — GESTIÓN DE PROYECTOS (RF-80..RF-83)
-- ----------------------------------------------------------------------------
CREATE TYPE proyectos.proyecto_estado AS ENUM ('planeado','en_curso','cerrado');

CREATE TABLE proyectos.proyecto (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  nombre         TEXT NOT NULL,
  cliente_id     UUID,     -- FK cruzada opcional -> ventas.cliente (RF-80)
  fecha_inicio_est DATE,
  fecha_fin_est    DATE,
  presupuesto      NUMERIC(14,2) CHECK (presupuesto >= 0),
  responsable_id   UUID REFERENCES core.usuario(id),
  estado           proyectos.proyecto_estado NOT NULL DEFAULT 'planeado',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_proyecto_updated BEFORE UPDATE ON proyectos.proyecto
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();

CREATE TABLE proyectos.tarea (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  proyecto_id  UUID NOT NULL REFERENCES proyectos.proyecto(id) ON DELETE CASCADE,
  nombre       TEXT NOT NULL,
  asignado_a   UUID REFERENCES core.usuario(id),
  estado       TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','en_curso','completada')),
  fecha_limite DATE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- MÓDULO 14 — MOTOR DE WORKFLOW / BPM (RF-84..RF-88)
-- Mecanismo único que centraliza aprobaciones de cotizaciones, ajustes de
-- inventario, órdenes de compra, etc. (Notas RF-84)
-- ----------------------------------------------------------------------------
CREATE TABLE bpm.flujo_aprobacion (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  nombre       TEXT NOT NULL,
  entidad_objetivo TEXT NOT NULL,   -- 'orden_compra','cotizacion','ajuste_inventario', etc.
  definicion   JSONB NOT NULL,      -- pasos, condiciones, aprobadores (motor parametrizable)
  activo       BOOLEAN NOT NULL DEFAULT TRUE,   -- desactivar, no eliminar si hay instancias (RF-85/RN)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, nombre)
);
CREATE TRIGGER trg_flujo_updated BEFORE UPDATE ON bpm.flujo_aprobacion
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();

CREATE TYPE bpm.instancia_estado AS ENUM ('en_curso','aprobada','rechazada','cancelada');

CREATE TABLE bpm.instancia_flujo (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  flujo_id      UUID NOT NULL REFERENCES bpm.flujo_aprobacion(id),
  entidad_objetivo_id TEXT NOT NULL,   -- id de la orden_compra/cotizacion/etc. referenciada
  estado        bpm.instancia_estado NOT NULL DEFAULT 'en_curso',
  paso_actual   SMALLINT NOT NULL DEFAULT 1,
  iniciado_por  UUID REFERENCES core.usuario(id),
  iniciado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finalizado_en TIMESTAMPTZ
);
CREATE INDEX idx_instancia_flujo_objetivo ON bpm.instancia_flujo(flujo_id, entidad_objetivo_id);

CREATE TABLE bpm.paso_instancia (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instancia_id   UUID NOT NULL REFERENCES bpm.instancia_flujo(id) ON DELETE CASCADE,
  numero_paso    SMALLINT NOT NULL,
  aprobador_id   UUID REFERENCES core.usuario(id),
  decision       TEXT CHECK (decision IN ('aprobado','rechazado')),
  comentario     TEXT,
  decidido_en    TIMESTAMPTZ,
  UNIQUE (instancia_id, numero_paso)
);

-- ----------------------------------------------------------------------------
-- MÓDULO 15 — MOTOR DE REGLAS DE NEGOCIO (RF-89..RF-90)
-- Alimenta condiciones de activación del BPM y validaciones de crédito (RF-38)
-- ----------------------------------------------------------------------------
CREATE TABLE reglas.regla_negocio (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  nombre       TEXT NOT NULL,
  dominio      TEXT NOT NULL,        -- 'ventas','compras','inventario', etc.
  condicion    JSONB NOT NULL,       -- expresión parametrizable (ej. {"campo":"monto","op":">","valor":50000})
  accion       JSONB NOT NULL,       -- ej. {"disparar_flujo":"aprobacion_compras"}
  activa       BOOLEAN NOT NULL DEFAULT TRUE,   -- desactivada no se elimina (trazabilidad, RF-90/RN)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, nombre)
);
CREATE TRIGGER trg_regla_updated BEFORE UPDATE ON reglas.regla_negocio
  FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();

-- ----------------------------------------------------------------------------
-- MÓDULO 16 — REPORTERÍA Y BUSINESS INTELLIGENCE (RF-91..RF-93)
-- ----------------------------------------------------------------------------
CREATE TABLE bi.indicador_kpi (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  nombre       TEXT NOT NULL,
  fuente_consulta TEXT NOT NULL,   -- referencia a vista/consulta agregada
  tipo_visual  TEXT NOT NULL DEFAULT 'numero' CHECK (tipo_visual IN ('numero','linea','barra','pastel')),
  creado_por   UUID REFERENCES core.usuario(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, nombre)
);

CREATE TABLE bi.dashboard (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
  nombre      TEXT NOT NULL,
  usuario_id  UUID REFERENCES core.usuario(id),  -- dueño / "mi panel"
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, nombre)
);

CREATE TABLE bi.dashboard_indicador (
  dashboard_id UUID NOT NULL REFERENCES bi.dashboard(id) ON DELETE CASCADE,
  indicador_id UUID NOT NULL REFERENCES bi.indicador_kpi(id) ON DELETE CASCADE,
  posicion     SMALLINT NOT NULL DEFAULT 0,
  PRIMARY KEY (dashboard_id, indicador_id)
);




-- ============================================================================
-- NovaERP — 04_fk_cruzadas.sql
-- Foreign keys entre módulos que se crean después de tener todas las tablas
-- (evita problemas de orden de creación entre esquemas ventas/compras/
-- inventario/finanzas/proyectos).
-- ============================================================================
SET search_path TO core, public;

-- Ventas -> Inventario
ALTER TABLE ventas.cotizacion_linea
  ADD CONSTRAINT fk_cotlinea_producto FOREIGN KEY (producto_id) REFERENCES inventario.producto(id);
ALTER TABLE ventas.pedido_linea
  ADD CONSTRAINT fk_pedlinea_producto FOREIGN KEY (producto_id) REFERENCES inventario.producto(id);

-- Compras -> Inventario
ALTER TABLE compras.orden_compra_linea
  ADD CONSTRAINT fk_oclinea_producto FOREIGN KEY (producto_id) REFERENCES inventario.producto(id);
ALTER TABLE compras.recepcion_mercancia
  ADD CONSTRAINT fk_recepcion_almacen FOREIGN KEY (almacen_id) REFERENCES inventario.almacen(id);

-- Finanzas -> Ventas / Compras
ALTER TABLE finanzas.cuenta_por_cobrar
  ADD CONSTRAINT fk_cxc_cliente FOREIGN KEY (cliente_id) REFERENCES ventas.cliente(id),
  ADD CONSTRAINT fk_cxc_factura FOREIGN KEY (factura_id) REFERENCES ventas.factura_venta(id);
ALTER TABLE finanzas.cuenta_por_pagar
  ADD CONSTRAINT fk_cxp_proveedor FOREIGN KEY (proveedor_id) REFERENCES compras.proveedor(id);

-- Proyectos -> Ventas
ALTER TABLE proyectos.proyecto
  ADD CONSTRAINT fk_proyecto_cliente FOREIGN KEY (cliente_id) REFERENCES ventas.cliente(id);

-- Todas las tablas tenant-scoped comparten (tenant_id) coherente con core.tenant;
-- ya está garantizado por cada FK individual declarada en los scripts 01-03.



-- ============================================================================
-- NovaERP — 05_rls_multitenant.sql
-- Aislamiento de datos por tenant_id A NIVEL DE MOTOR DE BASE DE DATOS (RLS),
-- no solo a nivel de aplicación. Requisito explícito: RNF-02 / restricción 2.2.2.
--
-- Patrón: la aplicación, al abrir cada conexión/transacción, ejecuta:
--   SET app.current_tenant_id = '<uuid-del-tenant-de-la-sesión>';
--   SET app.is_sysadmin       = 'false';   -- 'true' solo para el portal SysAdmin
-- y todas las políticas filtran contra esa variable de sesión.
-- ============================================================================

-- Función helper: castea la GUC de forma segura (si no está seteada, es NULL,
-- por lo que ninguna fila hace match => fail-closed, no fail-open).
CREATE OR REPLACE FUNCTION core.current_tenant_id() RETURNS UUID AS $$
  SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::UUID;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION core.is_sysadmin() RETURNS BOOLEAN AS $$
  SELECT COALESCE(current_setting('app.is_sysadmin', true), 'false')::BOOLEAN;
$$ LANGUAGE sql STABLE;

-- Rol de aplicación (sin BYPASSRLS) usado por el pool de conexiones de la API.
-- (Ajustar nombre/credenciales según el entorno de despliegue real.)
-- CREATE ROLE novaerp_app LOGIN PASSWORD '...';

-- Macro conceptual aplicada tabla por tabla:
--   ENABLE ROW LEVEL SECURITY;
--   FORCE ROW LEVEL SECURITY;  -- aplica incluso al dueño de la tabla
--   CREATE POLICY tenant_isolation ON <tabla>
--     USING (core.is_sysadmin() OR tenant_id = core.current_tenant_id())
--     WITH CHECK (tenant_id = core.current_tenant_id());

DO $$
DECLARE
  tabla RECORD;
BEGIN
  FOR tabla IN
    SELECT table_schema, table_name
    FROM information_schema.columns
    WHERE column_name = 'tenant_id'
      AND table_schema IN ('core','ventas','compras','inventario','rrhh','finanzas','proyectos','bpm','reglas','bi')
  LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY;', tabla.table_schema, tabla.table_name);
    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY;', tabla.table_schema, tabla.table_name);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I.%I
         USING (core.is_sysadmin() OR tenant_id = core.current_tenant_id())
         WITH CHECK (core.is_sysadmin() OR tenant_id = core.current_tenant_id());',
      tabla.table_schema, tabla.table_name
    );
  END LOOP;
END $$;

-- ----------------------------------------------------------------------------
-- Tablas hijas sin tenant_id propio (heredan aislamiento vía su padre con
-- políticas basadas en EXISTS, ya que RLS no propaga automáticamente por FK).
-- ----------------------------------------------------------------------------
ALTER TABLE ventas.cotizacion_linea ENABLE ROW LEVEL SECURITY;
ALTER TABLE ventas.cotizacion_linea FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ventas.cotizacion_linea
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM ventas.cotizacion c
    WHERE c.id = cotizacion_linea.cotizacion_id AND c.tenant_id = core.current_tenant_id()));

ALTER TABLE ventas.pedido_linea ENABLE ROW LEVEL SECURITY;
ALTER TABLE ventas.pedido_linea FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ventas.pedido_linea
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM ventas.pedido_venta p
    WHERE p.id = pedido_linea.pedido_id AND p.tenant_id = core.current_tenant_id()));

ALTER TABLE ventas.factura_linea ENABLE ROW LEVEL SECURITY;
ALTER TABLE ventas.factura_linea FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ventas.factura_linea
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM ventas.factura_venta f
    WHERE f.id = factura_linea.factura_id AND f.tenant_id = core.current_tenant_id()));

ALTER TABLE compras.orden_compra_linea ENABLE ROW LEVEL SECURITY;
ALTER TABLE compras.orden_compra_linea FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON compras.orden_compra_linea
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM compras.orden_compra o
    WHERE o.id = orden_compra_linea.orden_id AND o.tenant_id = core.current_tenant_id()));

ALTER TABLE compras.recepcion_linea ENABLE ROW LEVEL SECURITY;
ALTER TABLE compras.recepcion_linea FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON compras.recepcion_linea
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM compras.recepcion_mercancia r
    WHERE r.id = recepcion_linea.recepcion_id AND r.tenant_id = core.current_tenant_id()));

ALTER TABLE rrhh.recibo_nomina ENABLE ROW LEVEL SECURITY;
ALTER TABLE rrhh.recibo_nomina FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rrhh.recibo_nomina
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM rrhh.periodo_nomina pn
    WHERE pn.id = recibo_nomina.periodo_id AND pn.tenant_id = core.current_tenant_id()));

ALTER TABLE finanzas.abono_cxc ENABLE ROW LEVEL SECURITY;
ALTER TABLE finanzas.abono_cxc FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON finanzas.abono_cxc
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM finanzas.cuenta_por_cobrar c
    WHERE c.id = abono_cxc.cxc_id AND c.tenant_id = core.current_tenant_id()));

ALTER TABLE finanzas.pago_proveedor ENABLE ROW LEVEL SECURITY;
ALTER TABLE finanzas.pago_proveedor FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON finanzas.pago_proveedor
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM finanzas.cuenta_por_pagar c
    WHERE c.id = pago_proveedor.cxp_id AND c.tenant_id = core.current_tenant_id()));

ALTER TABLE proyectos.tarea ENABLE ROW LEVEL SECURITY;
ALTER TABLE proyectos.tarea FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON proyectos.tarea
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM proyectos.proyecto p
    WHERE p.id = tarea.proyecto_id AND p.tenant_id = core.current_tenant_id()));

ALTER TABLE bpm.paso_instancia ENABLE ROW LEVEL SECURITY;
ALTER TABLE bpm.paso_instancia FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON bpm.paso_instancia
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM bpm.instancia_flujo i
    WHERE i.id = paso_instancia.instancia_id AND i.tenant_id = core.current_tenant_id()));

ALTER TABLE core.usuario_rol ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.usuario_rol FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON core.usuario_rol
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM core.usuario u
    WHERE u.id = usuario_rol.usuario_id AND u.tenant_id = core.current_tenant_id()));

ALTER TABLE core.rol_permiso ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.rol_permiso FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON core.rol_permiso
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM core.rol r
    WHERE r.id = rol_permiso.rol_id AND (r.tenant_id = core.current_tenant_id() OR r.tenant_id IS NULL)));

ALTER TABLE bi.dashboard_indicador ENABLE ROW LEVEL SECURITY;
ALTER TABLE bi.dashboard_indicador FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON bi.dashboard_indicador
  USING (core.is_sysadmin() OR EXISTS (
    SELECT 1 FROM bi.dashboard d
    WHERE d.id = dashboard_indicador.dashboard_id AND d.tenant_id = core.current_tenant_id()));

-- Catálogos globales de plataforma (sin tenant_id): plan_comercial, modulo,
-- permiso, sysadmin -- NO llevan RLS por tenant (son compartidos/globales).
-- Se protegen únicamente vía permisos de rol de base de datos (GRANT/REVOKE).


-- ============================================================================
-- NovaERP — 06_triggers_negocio.sql
-- Reglas de negocio (RN) que no se expresan con CHECK/UNIQUE simples.
-- ============================================================================
SET search_path TO core, public;

-- ----------------------------------------------------------------------------
-- RF-07/RN04: todo usuario conserva al menos un rol tras la edición.
-- RF-07/RN06: no puede eliminarse el rol TENANT_ADMIN del último admin activo.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.validar_usuario_rol_minimo()
RETURNS TRIGGER AS $$
DECLARE
  v_usuario_id UUID := COALESCE(OLD.usuario_id, NEW.usuario_id);
  v_roles_restantes INTEGER;
  v_era_admin BOOLEAN;
  v_admins_activos_restantes INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_roles_restantes
  FROM core.usuario_rol WHERE usuario_id = v_usuario_id
    AND (TG_OP <> 'DELETE' OR rol_id <> OLD.rol_id);

  IF TG_OP = 'DELETE' AND v_roles_restantes = 0 THEN
    RAISE EXCEPTION 'RN04: el usuario debe conservar al menos un rol asignado';
  END IF;

  IF TG_OP = 'DELETE' THEN
    SELECT r.es_sistema INTO v_era_admin FROM core.rol r WHERE r.id = OLD.rol_id;
    IF v_era_admin THEN
      SELECT COUNT(*) INTO v_admins_activos_restantes
      FROM core.usuario_rol ur
      JOIN core.rol r ON r.id = ur.rol_id AND r.es_sistema
      JOIN core.usuario u ON u.id = ur.usuario_id AND u.estado = 'activo'
      WHERE ur.rol_id = OLD.rol_id AND ur.usuario_id <> OLD.usuario_id;

      IF v_admins_activos_restantes = 0 THEN
        RAISE EXCEPTION 'RN06: no puede eliminarse el rol TENANT_ADMIN del último administrador activo del tenant';
      END IF;
    END IF;
  END IF;

  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_usuario_rol_minimo
  BEFORE DELETE ON core.usuario_rol
  FOR EACH ROW EXECUTE FUNCTION core.validar_usuario_rol_minimo();

-- ----------------------------------------------------------------------------
-- RF-13/RN01: un rol con usuarios asignados no puede eliminarse (solo baja lógica).
-- RF-13/RN02: roles de sistema (TENANT_ADMIN) no pueden eliminarse ni desactivarse.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.validar_baja_rol()
RETURNS TRIGGER AS $$
DECLARE
  v_usuarios_asignados INTEGER;
BEGIN
  IF NEW.es_sistema AND (NOT NEW.activo) THEN
    RAISE EXCEPTION 'RN02: los roles de sistema (TENANT_ADMIN) no pueden desactivarse';
  END IF;

  IF OLD.activo AND NOT NEW.activo THEN
    SELECT COUNT(*) INTO v_usuarios_asignados FROM core.usuario_rol WHERE rol_id = NEW.id;
    IF v_usuarios_asignados > 0 THEN
      -- Regla real es baja lógica, no bloqueo total: se permite desactivar,
      -- pero se deja constancia; el bloqueo real es sobre DELETE físico.
      NULL;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_rol_baja_logica
  BEFORE UPDATE ON core.rol
  FOR EACH ROW EXECUTE FUNCTION core.validar_baja_rol();

CREATE OR REPLACE FUNCTION core.prevenir_delete_rol_sistema()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.es_sistema THEN
    RAISE EXCEPTION 'RN02: los roles de sistema (TENANT_ADMIN) no pueden eliminarse';
  END IF;
  IF EXISTS (SELECT 1 FROM core.usuario_rol WHERE rol_id = OLD.id) THEN
    RAISE EXCEPTION 'RN01: el rol tiene usuarios asignados; reasígnelos o desactive el rol en lugar de eliminarlo';
  END IF;
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_prevenir_delete_rol
  BEFORE DELETE ON core.rol
  FOR EACH ROW EXECUTE FUNCTION core.prevenir_delete_rol_sistema();

-- ----------------------------------------------------------------------------
-- RF-16/RN02: bloqueo de cuenta tras 5 intentos fallidos en ventana de 15 min.
-- Función que la capa de aplicación invoca en cada intento de login fallido.
-- ----------------------------------------------------------------------------
-- Devuelve el bloqueo resultante para que el servicio de autenticacion
-- (RF-16) detecte en una sola llamada si este intento disparo el bloqueo.
-- La invoca la capa de app, nunca core.intentar_login (validador puro).
CREATE OR REPLACE FUNCTION core.registrar_intento_fallido(p_usuario_id UUID)
RETURNS TIMESTAMPTZ AS $$
DECLARE
  v_config  RECORD;
  v_bloqueo TIMESTAMPTZ;
BEGIN
  SELECT * INTO v_config FROM core.config_seguridad_tenant cst
    JOIN core.usuario u ON u.tenant_id = cst.tenant_id
    WHERE u.id = p_usuario_id;

  UPDATE core.usuario
     SET intentos_fallidos = intentos_fallidos + 1,
         bloqueado_hasta = CASE
           WHEN intentos_fallidos + 1 >= COALESCE(v_config.intentos_max_ventana, 5)
             THEN now() + make_interval(mins => COALESCE(v_config.bloqueo_minutos, 30))
           ELSE bloqueado_hasta
         END
   WHERE id = p_usuario_id
   RETURNING bloqueado_hasta INTO v_bloqueo;

  RETURN v_bloqueo;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION core.reset_intentos_fallidos(p_usuario_id UUID)
RETURNS VOID AS $$
  UPDATE core.usuario SET intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = p_usuario_id;
$$ LANGUAGE sql;

-- ----------------------------------------------------------------------------
-- INVENTARIO: mantenimiento automático de inventario.stock_actual a partir
-- de inventario.movimiento (kardex), y validación de que no exista stock
-- negativo (protege reservas e integridad de RF-38/RF-59).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION inventario.aplicar_movimiento()
RETURNS TRIGGER AS $$
DECLARE
  v_signo NUMERIC;
BEGIN
  v_signo := CASE
    WHEN NEW.tipo IN ('entrada','ajuste_positivo','transferencia_entrada') THEN 1
    ELSE -1
  END;

  INSERT INTO inventario.stock_actual (tenant_id, producto_id, almacen_id, cantidad, actualizado_en)
  VALUES (NEW.tenant_id, NEW.producto_id, NEW.almacen_id, v_signo * NEW.cantidad, now())
  ON CONFLICT (tenant_id, producto_id, almacen_id)
  DO UPDATE SET cantidad = inventario.stock_actual.cantidad + v_signo * NEW.cantidad,
                actualizado_en = now();

  -- Alerta de stock mínimo (RF-63)
  INSERT INTO inventario.alerta_stock_minimo (tenant_id, producto_id, almacen_id, cantidad_al_disparo)
  SELECT NEW.tenant_id, NEW.producto_id, NEW.almacen_id, sa.cantidad
  FROM inventario.stock_actual sa
  JOIN inventario.producto p ON p.id = NEW.producto_id
  WHERE sa.tenant_id = NEW.tenant_id AND sa.producto_id = NEW.producto_id AND sa.almacen_id = NEW.almacen_id
    AND sa.cantidad <= p.stock_minimo;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_movimiento_aplicar
  AFTER INSERT ON inventario.movimiento
  FOR EACH ROW EXECUTE FUNCTION inventario.aplicar_movimiento();

-- ----------------------------------------------------------------------------
-- VENTAS/RF-33/RN01: motivo obligatorio si oportunidad se marca "perdida"
-- ya está cubierto por CHECK ck_motivo_perdida en 02_fase1_comercial.sql.
--
-- VENTAS/RF-42/RN01: no facturar más de lo pendiente por línea de pedido.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ventas.validar_cantidad_facturable()
RETURNS TRIGGER AS $$
DECLARE
  v_pendiente NUMERIC;
BEGIN
  SELECT (cantidad - cantidad_facturada) INTO v_pendiente
  FROM ventas.pedido_linea WHERE id = NEW.pedido_linea_id;

  IF NEW.cantidad > v_pendiente THEN
    RAISE EXCEPTION 'RN01 (RF-42): cantidad a facturar (%) excede el máximo facturable pendiente (%)', NEW.cantidad, v_pendiente;
  END IF;

  UPDATE ventas.pedido_linea
     SET cantidad_facturada = cantidad_facturada + NEW.cantidad
   WHERE id = NEW.pedido_linea_id;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_factura_linea_validar
  BEFORE INSERT ON ventas.factura_linea
  FOR EACH ROW EXECUTE FUNCTION ventas.validar_cantidad_facturable();

-- ----------------------------------------------------------------------------
-- AUDITORÍA (RF-20): core.fn_redactar() + core.fn_auditar() + los 37 triggers
-- AFTER INSERT/UPDATE/DELETE viven en sql/2026-07-23_rf20_auditoria.sql.
--
-- Ese script es la definición autoritativa y DEBE ejecutarse después de este
-- archivo en cualquier instalación limpia. No se duplica aquí a propósito:
-- la versión que vivía en esta sección solo soportaba tablas con columna
-- tenant_id y PK simple "id", no registraba ip_origen (RF-20/RN01) y volcaba
-- password_hash / mfa_secret / token_activacion en claro a una tabla
-- append-only que nadie puede depurar (violaba RF-20/CA03).
-- ----------------------------------------------------------------------------



-- ============================================================================
-- NovaERP — 07_vistas_reportes.sql
-- Vistas de consulta agregada para Reportería/BI (RF-79, RF-91..RF-93) y
-- consultas de alto uso (RF-59: stock; RF-62: kardex).
-- Todas heredan el aislamiento por RLS de las tablas base subyacentes.
-- ============================================================================
SET search_path TO core, public;

-- RF-59: disponibilidad de stock por producto/almacén (ya cacheada en tabla,
-- esta vista añade nombre/sku para consumo directo desde UI).
CREATE OR REPLACE VIEW inventario.v_stock_disponible AS
SELECT
  s.tenant_id,
  p.sku,
  p.nombre AS producto,
  a.nombre AS almacen,
  s.cantidad,
  s.reservado,
  (s.cantidad - s.reservado) AS disponible
FROM inventario.stock_actual s
JOIN inventario.producto p ON p.id = s.producto_id
JOIN inventario.almacen a ON a.id = s.almacen_id;

-- RF-62: kardex / historial de movimientos con datos legibles.
CREATE OR REPLACE VIEW inventario.v_kardex AS
SELECT
  m.tenant_id,
  m.id AS movimiento_id,
  p.sku,
  p.nombre AS producto,
  a.nombre AS almacen,
  m.tipo,
  m.cantidad,
  m.costo_unitario,
  m.referencia_tipo,
  m.referencia_id,
  m.ocurrido_en
FROM inventario.movimiento m
JOIN inventario.producto p ON p.id = m.producto_id
JOIN inventario.almacen a ON a.id = m.almacen_id;

-- RF-64: valuación de inventario (costo de referencia x cantidad actual).
CREATE OR REPLACE VIEW inventario.v_valuacion_inventario AS
SELECT
  s.tenant_id,
  p.sku,
  p.nombre AS producto,
  a.nombre AS almacen,
  s.cantidad,
  p.costo_referencia,
  (s.cantidad * p.costo_referencia) AS valor_total
FROM inventario.stock_actual s
JOIN inventario.producto p ON p.id = s.producto_id
JOIN inventario.almacen a ON a.id = s.almacen_id;

-- RF-74: estado de cuenta de cliente (CxC) — facturas, abonos, saldo corrido.
CREATE OR REPLACE VIEW finanzas.v_estado_cuenta_cliente AS
SELECT
  c.tenant_id,
  cl.razon_social AS cliente,
  f.folio AS factura,
  c.monto_original,
  c.saldo,
  COALESCE(SUM(a.monto), 0) AS total_abonado,
  c.created_at AS fecha_factura
FROM finanzas.cuenta_por_cobrar c
JOIN ventas.cliente cl ON cl.id = c.cliente_id
JOIN ventas.factura_venta f ON f.id = c.factura_id
LEFT JOIN finanzas.abono_cxc a ON a.cxc_id = c.id
GROUP BY c.tenant_id, cl.razon_social, f.folio, c.monto_original, c.saldo, c.created_at;

-- RF-77: estado de cuenta de proveedor (CxP), mismo patrón inverso.
CREATE OR REPLACE VIEW finanzas.v_estado_cuenta_proveedor AS
SELECT
  c.tenant_id,
  pr.razon_social AS proveedor,
  c.origen_tipo,
  c.monto_original,
  c.saldo,
  COALESCE(SUM(pg.monto), 0) AS total_pagado,
  c.created_at AS fecha_origen
FROM finanzas.cuenta_por_pagar c
JOIN compras.proveedor pr ON pr.id = c.proveedor_id
LEFT JOIN finanzas.pago_proveedor pg ON pg.cxp_id = c.id
GROUP BY c.tenant_id, pr.razon_social, c.origen_tipo, c.monto_original, c.saldo, c.created_at;

-- RF-79: reportes financieros agregados por periodo (base para estado de
-- resultados / balance general; se afina con catálogo contable en iteración
-- futura, ver "Pendientes explícitos" del ERS).
CREATE OR REPLACE VIEW finanzas.v_resumen_periodo AS
SELECT
  cc.tenant_id,
  cc.periodo_inicio,
  cc.periodo_fin,
  cc.estado,
  (SELECT COALESCE(SUM(f.total), 0) FROM ventas.factura_venta f
     WHERE f.tenant_id = cc.tenant_id
       AND f.fecha_emision BETWEEN cc.periodo_inicio AND cc.periodo_fin
       AND f.estado = 'emitida') AS ingresos_periodo,
  (SELECT COALESCE(SUM(oc.total), 0) FROM compras.orden_compra oc
     WHERE oc.tenant_id = cc.tenant_id
       AND oc.created_at::date BETWEEN cc.periodo_inicio AND cc.periodo_fin
       AND oc.estado IN ('recibida_total','recibida_parcial')) AS egresos_compras_periodo
FROM finanzas.cierre_contable cc;

-- RF-23/RF-93: reporte de actividad de usuarios (núcleo + KPIs operativos).
CREATE OR REPLACE VIEW core.v_actividad_usuarios AS
SELECT
  u.tenant_id,
  u.id AS usuario_id,
  u.nombre_completo,
  COUNT(*) FILTER (WHERE la.operacion = 'LOGIN') AS logins,
  MAX(la.ocurrido_en) AS ultima_actividad
FROM core.usuario u
LEFT JOIN core.log_auditoria la ON la.usuario_id = u.id
GROUP BY u.tenant_id, u.id, u.nombre_completo;

-- RF-69: reporte de asistencia con filtros por empleado/rango (consumido con
-- WHERE adicional desde la aplicación).
CREATE OR REPLACE VIEW rrhh.v_reporte_asistencia AS
SELECT
  a.tenant_id,
  e.nombre_completo AS empleado,
  a.tipo,
  a.registrado_en
FROM rrhh.asistencia a
JOIN rrhh.empleado e ON e.id = a.empleado_id;

-- RF-31: pipeline de oportunidades por etapa (para tablero kanban, 3.3.1).
CREATE OR REPLACE VIEW ventas.v_pipeline_oportunidades AS
SELECT
  o.tenant_id,
  o.etapa,
  COUNT(*) AS num_oportunidades,
  SUM(o.valor_estimado) AS valor_total_etapa
FROM ventas.oportunidad o
WHERE o.estado = 'abierta'
GROUP BY o.tenant_id, o.etapa;




-- ============================================================================
-- NovaERP — 08_ejemplos_uso.sql
-- Datos semilla mínimos + ejemplos de código: cómo la capa de aplicación
-- usa el modelo (sesión con contexto de tenant, altas, flujos completos).
-- ============================================================================
SET search_path TO core, public;

-- ----------------------------------------------------------------------------
-- 1) SEED: catálogos base
-- ----------------------------------------------------------------------------
INSERT INTO core.plan_comercial (codigo, nombre, licencias_max) VALUES
  ('STARTER', 'Starter', 10),
  ('BUSINESS', 'Business', 50),
  ('ENTERPRISE', 'Enterprise', 500);

INSERT INTO core.modulo (codigo, nombre, fase) VALUES
  ('MULTITENANCIA', 'Administración de Multi-tenencia', 0),
  ('USUARIOS', 'Gestión de Usuarios', 0),
  ('RBAC', 'Roles y Permisos', 0),
  ('AUTH', 'Autenticación y Sesión', 0),
  ('AUDITORIA', 'Auditoría y Cumplimiento', 0),
  ('VENTAS', 'Ventas / CRM', 1),
  ('COMPRAS', 'Compras', 1),
  ('INVENTARIO', 'Inventario', 1),
  ('RRHH', 'RRHH / Nómina', 2),
  ('FINANZAS', 'Finanzas Avanzada', 2),
  ('PROYECTOS', 'Gestión de Proyectos', 2),
  ('BPM', 'Motor de Workflow', 2),
  ('REGLAS', 'Motor de Reglas de Negocio', 2),
  ('BI', 'Reportería / BI', 2);

INSERT INTO core.permiso (dominio, recurso, accion, descripcion) VALUES
  ('ventas', 'cotizaciones', 'crear', 'Generar cotización'),
  ('ventas', 'cotizaciones', 'leer', 'Consultar cotizaciones'),
  ('ventas', 'clientes', 'crear', 'Registrar cliente'),
  ('inventario', 'movimientos', 'crear', 'Registrar movimiento manual de inventario'),
  ('finanzas', 'credito', 'autorizar', 'Autorizar excepción de límite de crédito'),
  ('compras', 'ordenes_compra', 'aprobar', 'Aprobar orden de compra sobre el umbral');

-- ----------------------------------------------------------------------------
-- 2) SEED: un tenant de ejemplo con su TENANT_ADMIN
-- ----------------------------------------------------------------------------
SET app.is_sysadmin = 'true';

WITH nuevo_tenant AS (
  INSERT INTO core.tenant (slug, razon_social, dominio_comercial, plan_id, estado)
  SELECT 'acme', 'ACME Corp S.A. de C.V.', 'Manufactura', id, 'activo'
  FROM core.plan_comercial WHERE codigo = 'BUSINESS'
  RETURNING id
),
config AS (
  INSERT INTO core.config_seguridad_tenant (tenant_id)
  SELECT id FROM nuevo_tenant
),
rol_admin AS (
  INSERT INTO core.rol (tenant_id, nombre, es_sistema)
  SELECT id, 'TENANT_ADMIN', TRUE FROM nuevo_tenant
  RETURNING id, tenant_id
)
INSERT INTO core.usuario (tenant_id, correo, nombre_completo, estado, password_hash, mfa_enrolado)
SELECT tenant_id, 'admin@acme.com', 'Administradora ACME', 'activo',
       crypt('CambiarEnPrimerLogin!', gen_salt('bf', 12)), TRUE   -- Argon2id/bcrypt (RNF-01)
FROM rol_admin;

-- Asigna el rol TENANT_ADMIN al usuario recién creado
INSERT INTO core.usuario_rol (usuario_id, rol_id)
SELECT u.id, r.id
FROM core.usuario u
JOIN core.rol r ON r.tenant_id = u.tenant_id AND r.nombre = 'TENANT_ADMIN'
WHERE u.correo = 'admin@acme.com';

-- Activa módulos de Fase 0 y Fase 1 para el tenant de ejemplo
INSERT INTO core.tenant_modulo (tenant_id, modulo_id)
SELECT t.id, m.id
FROM core.tenant t, core.modulo m
WHERE t.slug = 'acme' AND m.fase IN (0,1);

-- ----------------------------------------------------------------------------
-- 3) PATRÓN DE APLICACIÓN: cómo se abre una transacción con contexto de
--    tenant antes de cualquier operación (esto lo ejecuta el backend/API
--    en cada request autenticado, usando el tenant_id resuelto del JWT).
-- ----------------------------------------------------------------------------
-- BEGIN;
--   SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000000'; -- del JWT
--   SET LOCAL app.current_user_id   = '11111111-1111-1111-1111-111111111111'; -- del JWT
--   SET LOCAL app.is_sysadmin       = 'false';
--   -- ... consultas/DML normales, ya filtradas automáticamente por RLS ...
-- COMMIT;

-- Establece contexto de tenant para el resto del script (session-level)
DO $$
DECLARE
  v_tenant_id UUID;
  v_user_id UUID;
BEGIN
  SELECT id INTO v_tenant_id FROM core.tenant WHERE slug = 'acme';
  SELECT id INTO v_user_id FROM core.usuario WHERE correo = 'admin@acme.com';

  PERFORM set_config('app.current_tenant_id', v_tenant_id::TEXT, false);
  PERFORM set_config('app.current_user_id', v_user_id::TEXT, false);
  -- is_sysadmin se resetea al final, después de todos los ejemplos de seed
END $$;

-- ----------------------------------------------------------------------------
-- 4) EJEMPLO — RF-53/RF-57: registrar producto y almacén, con stock inicial
-- ----------------------------------------------------------------------------
WITH t AS (SELECT id FROM core.tenant WHERE slug = 'acme'),
prod AS (
  INSERT INTO inventario.producto (tenant_id, sku, nombre, costo_referencia, precio_venta, stock_minimo)
  SELECT id, 'SKU-001', 'Motor eléctrico 1HP', 850.00, 1450.00, 10 FROM t
  RETURNING id, tenant_id
),
alm AS (
  INSERT INTO inventario.almacen (tenant_id, nombre, ubicacion)
  SELECT id, 'Almacén Central', 'Planta Puebla' FROM t
  RETURNING id, tenant_id
)
INSERT INTO inventario.movimiento (tenant_id, producto_id, almacen_id, tipo, cantidad, costo_unitario, referencia_tipo, referencia_id)
SELECT prod.tenant_id, prod.id, alm.id, 'entrada', 100, 850.00, 'carga_inicial', 'SEED'
FROM prod, alm;

-- ----------------------------------------------------------------------------
-- 5) EJEMPLO — RF-26/RF-30/RF-34: cliente -> oportunidad ganada -> cotización
-- ----------------------------------------------------------------------------
WITH t AS (SELECT id FROM core.tenant WHERE slug = 'acme'),
cli AS (
  INSERT INTO ventas.cliente (tenant_id, rfc_o_id_fiscal, razon_social, correo, limite_credito)
  SELECT id, 'XAXX010101000', 'Distribuidora del Centro', 'compras@distcentro.com', 200000
  FROM t
  RETURNING id, tenant_id
),
opp AS (
  INSERT INTO ventas.oportunidad (tenant_id, cliente_id, nombre, valor_estimado, etapa, estado)
  SELECT cli.tenant_id, cli.id, 'Suministro anual de motores', 150000, 'cierre', 'ganada'
  FROM cli
  RETURNING id, tenant_id, cliente_id
)
INSERT INTO ventas.cotizacion (tenant_id, folio, cliente_id, oportunidad_id, estado)
SELECT tenant_id, 'COT-0001', cliente_id, id, 'borrador'
FROM opp;

-- Línea de cotización referenciando el producto sembrado arriba
INSERT INTO ventas.cotizacion_linea (cotizacion_id, producto_id, descripcion, cantidad, precio_unitario)
SELECT c.id, p.id, p.nombre, 20, p.precio_venta
FROM ventas.cotizacion c, inventario.producto p
WHERE c.folio = 'COT-0001' AND p.sku = 'SKU-001';

-- ----------------------------------------------------------------------------
-- 6) EJEMPLO — RF-63: consultar productos por debajo de stock mínimo
--    (consulta típica que dispara notificaciones proactivas)
-- ----------------------------------------------------------------------------
-- SELECT sku, producto, almacen, disponible
-- FROM inventario.v_stock_disponible
-- WHERE disponible <= (SELECT stock_minimo FROM inventario.producto WHERE sku = inventario.v_stock_disponible.sku);

-- ----------------------------------------------------------------------------
-- 7) RF-16: VALIDADOR PURO de credenciales. Respeta RN01 (hash), reporta el
--    bloqueo de RN02 y aplica RN04 (mensaje genérico). NO modifica estado: el
--    conteo de intentos (registrar/reset) lo ejecuta el servicio de
--    autenticación de la app, dentro del audit_context, para que la bitácora
--    (RF-20) atribuya esas escrituras. Devuelve un código de resultado para
--    que la app oriente los efectos sin parsear el mensaje del cliente:
--    resultado ∈ ('ok','credenciales','bloqueado','inactivo'); 'credenciales'
--    con usuario_id NULL = tenant/usuario inexistente (no se cuenta, RN04).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.intentar_login(p_tenant_slug TEXT, p_correo CITEXT, p_password TEXT)
RETURNS TABLE(resultado TEXT, usuario_id UUID, mensaje TEXT) AS $$
DECLARE
  v_usuario core.usuario%ROWTYPE;
  v_tenant  core.tenant%ROWTYPE;
BEGIN
  SELECT * INTO v_tenant FROM core.tenant WHERE slug = p_tenant_slug;
  IF NOT FOUND OR v_tenant.estado <> 'activo' THEN
    RETURN QUERY SELECT 'credenciales', NULL::UUID, 'Credenciales incorrectas';  -- RN04
    RETURN;
  END IF;

  SELECT * INTO v_usuario FROM core.usuario
    WHERE tenant_id = v_tenant.id AND correo = p_correo;

  IF NOT FOUND THEN
    RETURN QUERY SELECT 'credenciales', NULL::UUID, 'Credenciales incorrectas';
    RETURN;
  END IF;

  IF v_usuario.bloqueado_hasta IS NOT NULL AND v_usuario.bloqueado_hasta > now() THEN
    RETURN QUERY SELECT 'bloqueado', v_usuario.id,
      format('Cuenta bloqueada, intente después de %s', v_usuario.bloqueado_hasta);
    RETURN;
  END IF;

  IF v_usuario.estado = 'suspendido' THEN
    RETURN QUERY SELECT 'inactivo', v_usuario.id,
      'Su cuenta ha sido suspendida. Contacte al administrador.';  -- RF-08/CA03
    RETURN;
  END IF;

  IF v_usuario.estado <> 'activo' THEN
    RETURN QUERY SELECT 'inactivo', v_usuario.id, 'Active su cuenta primero';
    RETURN;
  END IF;

  IF v_usuario.password_hash IS NULL OR NOT (v_usuario.password_hash = crypt(p_password, v_usuario.password_hash)) THEN
    RETURN QUERY SELECT 'credenciales', v_usuario.id, 'Credenciales incorrectas';
    RETURN;
  END IF;

  RETURN QUERY SELECT 'ok', v_usuario.id, 'OK, continuar con validación de OTP (MFA)';
END;
$$ LANGUAGE plpgsql;

-- Uso:
-- SELECT * FROM core.intentar_login('acme', 'admin@acme.com', 'CambiarEnPrimerLogin!');

-- Validador PURO de credenciales del SysAdmin (portal de plataforma). Espejo de
-- core.intentar_login pero contra core.sysadmin (sin tenant). No muta estado.
-- Ver sql/2026-07-25_sysadmin_sesion.sql.
CREATE OR REPLACE FUNCTION core.intentar_login_sysadmin(p_correo CITEXT, p_password TEXT)
RETURNS TABLE(resultado TEXT, sysadmin_id UUID, mensaje TEXT) AS $$
DECLARE
  v_admin core.sysadmin%ROWTYPE;
BEGIN
  SELECT * INTO v_admin FROM core.sysadmin WHERE correo = p_correo;

  IF NOT FOUND THEN
    RETURN QUERY SELECT 'credenciales', NULL::UUID, 'Credenciales incorrectas';
    RETURN;
  END IF;

  IF NOT v_admin.activo THEN
    RETURN QUERY SELECT 'inactivo', v_admin.id, 'Cuenta de administrador desactivada';
    RETURN;
  END IF;

  IF v_admin.password_hash IS NULL
     OR NOT (v_admin.password_hash = crypt(p_password, v_admin.password_hash)) THEN
    RETURN QUERY SELECT 'credenciales', v_admin.id, 'Credenciales incorrectas';
    RETURN;
  END IF;

  RETURN QUERY SELECT 'ok', v_admin.id, 'OK';
END;
$$ LANGUAGE plpgsql;

-- ----------------------------------------------------------------------------
-- 8) EJEMPLO — RF-38/RN02: validar límite de crédito antes de confirmar pedido
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ventas.validar_limite_credito(p_cliente_id UUID, p_monto_pedido NUMERIC)
RETURNS BOOLEAN AS $$
DECLARE
  v_limite NUMERIC;
  v_saldo_actual NUMERIC;
BEGIN
  SELECT limite_credito INTO v_limite FROM ventas.cliente WHERE id = p_cliente_id;
  SELECT COALESCE(SUM(saldo), 0) INTO v_saldo_actual FROM finanzas.cuenta_por_cobrar WHERE cliente_id = p_cliente_id;

  RETURN (v_saldo_actual + p_monto_pedido) <= v_limite;
END;
$$ LANGUAGE plpgsql STABLE;

-- Uso típico antes de UPDATE ventas.pedido_venta SET estado = 'confirmado':
-- SELECT ventas.validar_limite_credito('<cliente_id>', 45000.00);

-- ----------------------------------------------------------------------------
-- 9) EJEMPLO — RF-59: consulta de stock disponible (alto uso, vista cacheada)
-- ----------------------------------------------------------------------------
-- SELECT * FROM inventario.v_stock_disponible WHERE tenant_id = core.current_tenant_id();

-- ----------------------------------------------------------------------------
-- 10) EJEMPLO — RF-92: dashboard con KPI de pipeline de ventas
-- ----------------------------------------------------------------------------
WITH t AS (SELECT id FROM core.tenant WHERE slug = 'acme'),
u AS (SELECT id FROM core.usuario WHERE correo = 'admin@acme.com'),
ind AS (
  INSERT INTO bi.indicador_kpi (tenant_id, nombre, fuente_consulta, tipo_visual, creado_por)
  SELECT t.id, 'Pipeline por etapa', 'ventas.v_pipeline_oportunidades', 'barra', u.id
  FROM t, u
  RETURNING id, tenant_id
),
dash AS (
  INSERT INTO bi.dashboard (tenant_id, nombre, usuario_id)
  SELECT t.id, 'Panel Comercial', u.id FROM t, u
  RETURNING id
)
INSERT INTO bi.dashboard_indicador (dashboard_id, indicador_id)
SELECT dash.id, ind.id FROM dash, ind;

SET app.is_sysadmin = 'false';

-- Consulta final que consumiría el frontend para pintar el widget:
-- SELECT * FROM ventas.v_pipeline_oportunidades WHERE tenant_id = core.current_tenant_id();