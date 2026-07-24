-- ============================================================================
-- NovaERP — RF-16/RN07 + RF-07: permiso para el reseteo de MFA
-- Fecha: 2026-07-24
--
-- RN07: el reseteo de MFA por perdida de dispositivo es una accion EXCLUSIVA
-- del TENANT_ADMIN. Se modela como permiso propio (no se reutiliza
-- core:usuarios:editar) para poder concederlo por separado de la edicion de
-- datos personales: es una accion de seguridad, mas sensible.
--
-- Idempotente. No crea tablas ni columnas.
-- ============================================================================

INSERT INTO core.permiso (dominio, recurso, accion, descripcion) VALUES
  ('core', 'usuarios', 'reset_mfa', 'RF-07/RN07 Resetear el segundo factor (MFA) de un usuario')
ON CONFLICT (codigo) DO NOTHING;
