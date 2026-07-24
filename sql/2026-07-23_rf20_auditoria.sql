-- ============================================================================
-- NovaERP — RF-20: Registrar bitácora de auditoría (automatizado)
-- Fecha: 2026-07-23
--
-- Alcance: RF-20 únicamente. No incluye RF-21 (consulta de bitácora) ni
-- ningún endpoint; esto es solo la infraestructura de captura.
--
-- Qué hace:
--   1. core.fn_redactar()  — enmascara secretos en el payload diferencial.
--   2. core.fn_auditar()   — REEMPLAZA la versión previa. Ahora resuelve
--      genéricamente PK compuesta, tablas sin tenant_id, e ip_origen.
--   3. 37 triggers AFTER INSERT/UPDATE/DELETE, generados en un solo bloque.
--
-- No crea tablas ni columnas: core.log_auditoria ya tiene los 7 campos
-- obligatorios de RF-20/RN01; hasta ahora solo se llenaban 5.
--
-- Idempotente: puede re-ejecutarse sin efectos secundarios.
--
-- ----------------------------------------------------------------------------
-- LIMITACIÓN CONOCIDA — datos históricos
-- ----------------------------------------------------------------------------
-- Las filas de core.log_auditoria anteriores a esta migración contienen
-- password_hash y mfa_secret sin enmascarar en valores_antes/valores_despues.
-- NO se corrigen: la tabla es append-only por diseño (RF-20/RN02, trigger
-- core.bloquear_mutacion_auditoria) y el propio ERS prohíbe que ningún rol,
-- incluido el SysAdmin, edite o elimine registros de auditoría. El
-- enmascaramiento aplica solo a registros nuevos.
--
-- Mitigación recomendada fuera de este script: rotar las contraseñas de las
-- cuentas cuyos hashes quedaron expuestos, ya que el log no puede depurarse.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) Enmascaramiento de información sensible (RF-20/CA03)
-- ----------------------------------------------------------------------------
-- Lista de nombres EXACTOS de columna, no un patrón LIKE '%password%': un
-- patrón enmascararía también politica_password_min_len,
-- politica_password_regex y token_activacion_exp, que no son secretos — y
-- redactar los dos primeros rompería la CA03 de RF-22, que exige auditar el
-- valor anterior/nuevo de la política de seguridad.
--
-- Un valor NULL se deja en NULL en vez de redactarse, para poder distinguir
-- "la cuenta no tenía contraseña" de "tenía una" sin revelar cuál.
--
-- STRICT: fn_redactar(NULL) devuelve NULL sin ejecutar el cuerpo, que es el
-- caso de valores_antes en un INSERT y valores_despues en un DELETE.
CREATE OR REPLACE FUNCTION core.fn_redactar(p_datos JSONB)
RETURNS JSONB AS $$
DECLARE
  v_salida JSONB := p_datos;
  v_campo  TEXT;
BEGIN
  FOREACH v_campo IN ARRAY ARRAY[
    'password_hash',
    'mfa_secret',
    'token_activacion',
    'jwt_id'
  ] LOOP
    IF v_salida ? v_campo AND jsonb_typeof(v_salida -> v_campo) <> 'null' THEN
      v_salida := jsonb_set(v_salida, ARRAY[v_campo], '"[REDACTED]"'::JSONB);
    END IF;
  END LOOP;

  RETURN v_salida;
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT;


-- ----------------------------------------------------------------------------
-- 2) Captura transversal (RF-20/RN01: los 7 campos obligatorios)
-- ----------------------------------------------------------------------------
-- Reemplaza la versión previa, que solo servía para tablas con columna
-- tenant_id y PK simple llamada "id" — es decir, para 2 de las 37 tablas del
-- alcance RF-01..64.
--
-- Contexto que publica la aplicación por transacción (core/utils/audit.py):
--   app.current_user_id   -> usuario responsable
--   app.current_tenant_id -> tenant, para tablas hijas sin columna propia
--   app.current_ip        -> IP de origen de la petición
-- Una escritura hecha fuera de la aplicación (psql, scripts de mantenimiento)
-- deja esos campos en NULL; la fila de auditoría se registra igual.
CREATE OR REPLACE FUNCTION core.fn_auditar()
RETURNS TRIGGER AS $$
DECLARE
  v_registro   JSONB;
  v_entidad_id TEXT;
  v_columna    TEXT;
  v_partes     TEXT[] := '{}';
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_registro := to_jsonb(OLD);
  ELSE
    v_registro := to_jsonb(NEW);
  END IF;

  -- Identificador del registro afectado. Cada trigger recibe en TG_ARGV las
  -- columnas de su clave primaria, resueltas desde el catálogo al crearlo,
  -- de modo que las PK compuestas (usuario_rol, rol_permiso, tenant_modulo,
  -- stock_actual, config_*) no necesitan lógica propia.
  IF TG_NARGS = 0 THEN
    v_entidad_id := v_registro ->> 'id';
  ELSE
    FOREACH v_columna IN ARRAY TG_ARGV LOOP
      v_partes := v_partes || COALESCE(v_registro ->> v_columna, '');
    END LOOP;
    v_entidad_id := array_to_string(v_partes, '|');
  END IF;

  INSERT INTO core.log_auditoria (
    tenant_id, usuario_id, entidad, entidad_id, operacion,
    valores_antes, valores_despues, ip_origen
  ) VALUES (
    -- Las tablas de detalle (cotizacion_linea, orden_compra_linea, abono_cxc,
    -- usuario_rol, ...) no llevan tenant_id propio: se toma del contexto de
    -- la transacción, que es el mismo tenant del JWT que originó la escritura.
    COALESCE(
      (v_registro ->> 'tenant_id')::UUID,
      NULLIF(current_setting('app.current_tenant_id', true), '')::UUID
    ),
    NULLIF(current_setting('app.current_user_id', true), '')::UUID,
    -- Se conserva TG_TABLE_NAME sin calificar por esquema para no romper la
    -- compatibilidad con las filas ya escritas ('usuario', 'rol'). No hay
    -- colisión de nombres entre los 5 esquemas del alcance RF-01..64.
    TG_TABLE_NAME,
    v_entidad_id,
    TG_OP,
    CASE WHEN TG_OP IN ('UPDATE','DELETE') THEN core.fn_redactar(to_jsonb(OLD)) END,
    CASE WHEN TG_OP IN ('UPDATE','INSERT') THEN core.fn_redactar(to_jsonb(NEW)) END,
    NULLIF(current_setting('app.current_ip', true), '')::INET
  );

  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- 3) Instalación de triggers (RF-20/CA01: todo CUD de cualquier módulo)
-- ----------------------------------------------------------------------------
-- Un solo bloque en vez de 37 CREATE TRIGGER escritos a mano: las columnas de
-- PK se leen del catálogo y se pasan como argumentos del trigger.
--
-- Fuera de la lista, a propósito:
--   · core.log_auditoria            -> recursión infinita.
--   · core.permiso / plan_comercial / modulo -> catálogos maestros de
--     plataforma, sembrados por DDL; ningún RF del 01 al 64 los muta vía API.
--   · rrhh, proyectos, bpm, bi, reglas -> pertenecen a RF-65..RF-93, fuera
--     del alcance vigente.
DO $$
DECLARE
  v_tabla   TEXT;
  v_schema  TEXT;
  v_nombre  TEXT;
  v_args    TEXT;
BEGIN
  FOREACH v_tabla IN ARRAY ARRAY[
    -- Módulos 1-7 (núcleo: multi-tenencia, usuarios, RBAC, sesión, seguridad)
    'core.tenant',
    'core.tenant_modulo',
    'core.sysadmin',
    'core.usuario',
    'core.rol',
    'core.rol_permiso',
    'core.usuario_rol',
    'core.sesion',
    'core.config_seguridad_tenant',
    'core.notificacion',
    -- Módulo 8: Ventas / CRM
    'ventas.cliente',
    'ventas.oportunidad',
    'ventas.cotizacion',
    'ventas.cotizacion_linea',
    'ventas.pedido_venta',
    'ventas.pedido_linea',
    'ventas.factura_venta',
    'ventas.factura_linea',
    'ventas.nota_credito',
    -- Módulo 9: Compras
    'compras.proveedor',
    'compras.config_aprobacion',
    'compras.orden_compra',
    'compras.orden_compra_linea',
    'compras.recepcion_mercancia',
    'compras.recepcion_linea',
    -- Módulo 10: Inventario
    'inventario.producto',
    'inventario.almacen',
    'inventario.movimiento',
    'inventario.stock_actual',
    'inventario.ajuste_inventario',
    'inventario.transferencia',
    'inventario.alerta_stock_minimo',
    -- Finanzas: solo las entidades que RF-42/RF-44 (CxC) y RF-50 (CxP)
    -- escriben desde el alcance vigente.
    'finanzas.cuenta_por_cobrar',
    'finanzas.abono_cxc',
    'finanzas.cuenta_por_pagar',
    'finanzas.pago_proveedor',
    'finanzas.cierre_contable'
  ] LOOP
    v_schema := split_part(v_tabla, '.', 1);
    v_nombre := split_part(v_tabla, '.', 2);

    SELECT string_agg(quote_literal(a.attname), ', ' ORDER BY k.ord)
      INTO v_args
      FROM pg_index i
      JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON TRUE
      JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
     WHERE i.indrelid = v_tabla::REGCLASS
       AND i.indisprimary;

    IF v_args IS NULL THEN
      RAISE EXCEPTION 'La tabla % no tiene clave primaria; no se puede auditar', v_tabla;
    END IF;

    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I ON %I.%I',
      'trg_auditar_' || v_nombre, v_schema, v_nombre
    );
    EXECUTE format(
      'CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I.%I'
      ' FOR EACH ROW EXECUTE FUNCTION core.fn_auditar(%s)',
      'trg_auditar_' || v_nombre, v_schema, v_nombre, v_args
    );
  END LOOP;
END $$;
