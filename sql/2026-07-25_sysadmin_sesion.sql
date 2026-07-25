-- ============================================================================
-- NovaERP — Fundacion de sesion del SysAdmin (portal de plataforma)
-- Fecha: 2026-07-25
--
-- El ERS define al actor SysAdmin (Administrador Global, fuera de todo tenant)
-- pero NINGUN RF del 01 al 64 define como se autentica ni como se administra su
-- sesion. RF-01..04 (gestion de tenants) lo tienen como precondicion, asi que
-- esta es infraestructura habilitante, no un RF.
--
-- Piezas:
--   1) core.sesion_sysadmin  : sesion persistida SIN tenant ni core.usuario
--      (core.sesion no admite filas sin tenant_id/usuario_id). Fuente de verdad
--      de la revocacion (logout), igual que core.sesion lo es para los tenants.
--   2) core.intentar_login_sysadmin : validador PURO de credenciales, espejo de
--      core.intentar_login. No muta estado; devuelve (resultado, id, mensaje).
--
-- Decisiones (aprobadas): login de una fase (sin MFA por ahora; la columna
-- core.sysadmin.mfa_secret queda reservada). Sin bloqueo por intentos (RN02):
-- core.sysadmin no tiene columnas intentos_fallidos/bloqueado_hasta; los fallos
-- se AUDITAN (LOGIN_FAILED) pero no se cuentan todavia (diferido documentado).
--
-- La sesion_sysadmin NO lleva RLS por tenant ni el trigger central fn_auditar:
-- es una tabla global de plataforma (como core.sysadmin, ver 05_rls_multitenant)
-- y sus eventos LOGIN/LOGOUT se registran como eventos de plataforma manuales
-- desde el servicio (usuario_id NULL, tenant_id NULL, entidad='sysadmin').
--
-- Idempotente.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) Sesion del SysAdmin. Espejo de core.sesion pero apuntando a core.sysadmin
--    y sin tenant_id: el SysAdmin no pertenece a ningun tenant.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.sesion_sysadmin (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sysadmin_id   UUID NOT NULL REFERENCES core.sysadmin(id) ON DELETE CASCADE,
  jwt_id        TEXT NOT NULL UNIQUE,          -- jti del JWT emitido
  ip_origen     INET,
  user_agent    TEXT,
  emitida_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expira_en     TIMESTAMPTZ NOT NULL,          -- configurable por settings (default 8h)
  revocada_en   TIMESTAMPTZ,
  revocada_por  UUID REFERENCES core.sysadmin(id)
);
CREATE INDEX IF NOT EXISTS idx_sesion_sysadmin_activa
  ON core.sesion_sysadmin(sysadmin_id) WHERE revocada_en IS NULL;


-- ----------------------------------------------------------------------------
-- 2) Validador PURO de credenciales del SysAdmin. Mismo patron que
--    core.intentar_login (RF-16): valida y devuelve un codigo de resultado; no
--    escribe nada. La verificacion del hash vive en Postgres (crypt), como en
--    todo el sistema.
--      resultado in ('ok','credenciales','inactivo')
--      · 'credenciales' con id NULL -> correo inexistente (no revela existencia)
--      · 'credenciales' con id      -> password incorrecta
--      · 'inactivo'                 -> cuenta existe pero activo = FALSE
-- ----------------------------------------------------------------------------
DROP FUNCTION IF EXISTS core.intentar_login_sysadmin(citext, text);

CREATE FUNCTION core.intentar_login_sysadmin(p_correo CITEXT, p_password TEXT)
RETURNS TABLE(resultado TEXT, sysadmin_id UUID, mensaje TEXT) AS $$
DECLARE
  v_admin core.sysadmin%ROWTYPE;
BEGIN
  SELECT * INTO v_admin FROM core.sysadmin WHERE correo = p_correo;

  IF NOT FOUND THEN
    -- No revela existencia; sin id, la app registra el fallo con el correo
    -- intentado como referencia (visibilidad de fuerza bruta).
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
