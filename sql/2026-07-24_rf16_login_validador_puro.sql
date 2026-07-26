-- ============================================================================
-- NovaERP — RF-16 (Paso 2a): core.intentar_login como validador puro
-- Fecha: 2026-07-24
--
-- Cierra el residual de RF-20. Antes, core.intentar_login modificaba estado
-- (PERFORM registrar_intento_fallido / reset_intentos_fallidos) desde dentro
-- de la funcion, antes de que se conociera el usuario en la capa de app, asi
-- que esas escrituras quedaban sin usuario_id en la bitacora.
--
-- Ahora:
--   · core.intentar_login SOLO valida y devuelve el resultado. Cero escrituras.
--   · registrar_intento_fallido / reset_intentos_fallidos las invoca el
--     servicio de autenticacion (core/services/auth_service.py), dentro del
--     audit_context y con app.current_user_id ya fijado -> filas atribuidas.
--
-- Contrato nuevo de intentar_login: devuelve un CODIGO de resultado en vez de
-- un booleano, para que la app oriente los efectos (contar intento, emitir
-- evento) sin parsear el mensaje destinado al cliente.
--   resultado ∈ ('ok','credenciales','bloqueado','inactivo')
--   · 'credenciales' con usuario_id NULL  -> tenant/usuario inexistente (RN04)
--   · 'credenciales' con usuario_id        -> password incorrecta (se cuenta)
--
-- Idempotente. No crea tablas ni columnas.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) Validador puro. El cambio de columnas de salida obliga a DROP + CREATE.
-- ----------------------------------------------------------------------------
DROP FUNCTION IF EXISTS core.intentar_login(text, citext, text);

CREATE FUNCTION core.intentar_login(p_tenant_slug TEXT, p_correo CITEXT, p_password TEXT)
RETURNS TABLE(resultado TEXT, usuario_id UUID, mensaje TEXT) AS $$
DECLARE
  v_usuario core.usuario%ROWTYPE;
  v_tenant  core.tenant%ROWTYPE;
BEGIN
  SELECT * INTO v_tenant FROM core.tenant WHERE slug = p_tenant_slug;
  IF NOT FOUND OR v_tenant.estado <> 'activo' THEN
    -- RN04: no revela existencia; sin usuario_id la app no cuenta intento (CA04).
    RETURN QUERY SELECT 'credenciales', NULL::UUID, 'Credenciales incorrectas';
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

  -- RF-08/CA03: mensaje especifico para cuenta suspendida (incluido aqui para
  -- que el replay de migraciones sea independiente del orden; misma logica que
  -- 2026-07-24_rf08_login_mensaje_suspendido.sql y que db.sql).
  IF v_usuario.estado = 'suspendido' THEN
    RETURN QUERY SELECT 'inactivo', v_usuario.id,
      'Su cuenta ha sido suspendida. Contacte al administrador.';
    RETURN;
  END IF;

  IF v_usuario.estado <> 'activo' THEN
    RETURN QUERY SELECT 'inactivo', v_usuario.id, 'Active su cuenta primero';
    RETURN;
  END IF;

  IF v_usuario.password_hash IS NULL
     OR NOT (v_usuario.password_hash = crypt(p_password, v_usuario.password_hash)) THEN
    -- Password incorrecta de un usuario real: la app contara el intento.
    RETURN QUERY SELECT 'credenciales', v_usuario.id, 'Credenciales incorrectas';
    RETURN;
  END IF;

  RETURN QUERY SELECT 'ok', v_usuario.id, 'OK, continuar con validación de OTP (MFA)';
END;
$$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- 2) registrar_intento_fallido devuelve el bloqueo resultante, para que el
--    servicio detecte en una sola llamada si ESTE intento disparo el bloqueo
--    (RN02) y emita ACCOUNT_LOCKED + notificacion. Solo se invoca cuando el
--    validador no reporto bloqueo previo, asi que bloqueado_hasta > now()
--    implica que acaba de dispararse.
-- ----------------------------------------------------------------------------
DROP FUNCTION IF EXISTS core.registrar_intento_fallido(uuid);

CREATE FUNCTION core.registrar_intento_fallido(p_usuario_id UUID)
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

-- reset_intentos_fallidos no cambia: sigue siendo un mutador void que ahora
-- invoca el servicio de autenticacion en el exito del login.
