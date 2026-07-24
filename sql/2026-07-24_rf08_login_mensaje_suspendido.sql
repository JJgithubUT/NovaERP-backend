-- ============================================================================
-- NovaERP — RF-08/CA03: mensaje especifico para cuenta suspendida en el login
-- Fecha: 2026-07-24
--
-- El validador puro core.intentar_login devolvia 'Active su cuenta primero'
-- para cualquier estado != 'activo', lo que es enganoso para un usuario
-- suspendido. RF-08/CA03 exige "Su cuenta ha sido suspendida. Contacte al
-- administrador." La ERS (RF-16 flujo 2b) contempla mensajes de estado
-- diferenciados como excepcion deliberada a la genericidad de RN04.
--
-- Sigue siendo un validador PURO (sin escrituras). CREATE OR REPLACE: no
-- cambia la firma. Idempotente.
-- ============================================================================

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
    -- RF-08/CA03
    RETURN QUERY SELECT 'inactivo', v_usuario.id,
      'Su cuenta ha sido suspendida. Contacte al administrador.';
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
