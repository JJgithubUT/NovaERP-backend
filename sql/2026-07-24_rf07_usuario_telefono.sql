-- ============================================================================
-- NovaERP — RF-07: Editar usuario
-- Sprint 4 · Fecha: 2026-07-24
--
-- Unica columna que falta para cerrar RF-07: la ERS lista al actor "Usuario
-- propietario (solo datos personales: nombre, telefono)", y core.usuario no
-- tenia donde guardar el telefono.
--
-- NO se agregan aqui core.usuario.puesto ni core.usuario.departamento, que
-- exigen la CA02 y la CA03 de RF-06: pertenecen a ese RF y entran con el.
--
-- Idempotente. No altera datos existentes: la columna nace NULL y es
-- opcional, asi que ningun INSERT ni UPDATE previo se ve afectado.
-- ============================================================================

ALTER TABLE core.usuario ADD COLUMN IF NOT EXISTS telefono TEXT;

COMMENT ON COLUMN core.usuario.telefono IS
  'RF-07: dato personal editable por el propio usuario. RF-06/RN03 preve '
  'ocultarlo a roles no autorizados segun la politica de privacidad del '
  'tenant; esa politica llega con RF-06.';
