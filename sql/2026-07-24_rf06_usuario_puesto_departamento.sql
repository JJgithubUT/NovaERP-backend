-- ============================================================================
-- NovaERP — RF-06: consultar directorio de usuarios
-- Fecha: 2026-07-24
--
-- RF-06/CA02 exige busqueda por "puesto" y CA03 filtro por "departamento".
-- core.usuario no tenia esas columnas. Se agregan como TEXT opcional; las fija
-- el TENANT_ADMIN en el alta (RF-05) o la edicion (RF-07) — son datos de la
-- organizacion, no personales, asi que no los edita el propio usuario.
--
-- Idempotente. Datos existentes intactos (columnas nacen NULL).
-- ============================================================================

ALTER TABLE core.usuario ADD COLUMN IF NOT EXISTS puesto TEXT;
ALTER TABLE core.usuario ADD COLUMN IF NOT EXISTS departamento TEXT;

COMMENT ON COLUMN core.usuario.puesto IS 'RF-06/CA02: cargo del usuario; busqueda del directorio.';
COMMENT ON COLUMN core.usuario.departamento IS 'RF-06/CA03: departamento; filtro del directorio.';
