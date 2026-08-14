-- ============================================================================
-- NovaERP — Rutas publicas del frontend como dominios reservados
-- Fecha: 2026-08-14
--
-- El frontend cuelga toda la app de /:tenant (el slug va en la URL), pero antes
-- de esa ruta comodin declara un punado de rutas publicas de primer nivel:
-- /login, /recuperar, /restablecer, /activar, /activar-organizacion, /admin,
-- /403 y /404. La ruta explicita siempre gana al comodin, asi que un tenant
-- registrado con uno de esos slugs quedaria inalcanzable: su gente escribiria
-- /activar y caeria en la pantalla de activacion, no en su organizacion.
--
-- core.dominio_reservado (RF-01/RN07/CA10) ya cubria 'admin' y 'login'; faltaban
-- las demas. Se agregan aqui en vez de en el codigo porque la tabla existe
-- justamente para editarse sin redeploy.
--
-- '403' y '404' entran porque SLUG_RE es ^[a-z0-9-]{3,50}$: tres digitos son un
-- slug perfectamente valido.
--
-- Solo cierra la puerta hacia adelante (RF-01 valida el slug al registrar). Si
-- ya existiera un tenant con alguna de estas palabras, este script no lo toca:
-- habria que renombrarlo a mano. La consulta de verificacion queda al final,
-- comentada.
--
-- Idempotente.
-- ============================================================================

INSERT INTO core.dominio_reservado (palabra) VALUES
  ('activar'), ('activar-organizacion'), ('restablecer'), ('recuperar'),
  ('403'), ('404')
ON CONFLICT (palabra) DO NOTHING;


-- Verificacion: tenants ya registrados que colisionan con una palabra
-- reservada. Debe devolver cero filas.
--
--   SELECT t.slug, t.razon_social, t.estado
--     FROM core.tenant t
--     JOIN core.dominio_reservado d ON d.palabra = t.slug;
