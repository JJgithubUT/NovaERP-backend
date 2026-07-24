from django.db import models



class ConfigSeguridadTenant(models.Model):
    tenant = models.OneToOneField('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='core_config_seguridad_tenant', primary_key=True)
    politica_password_min_len = models.SmallIntegerField(db_column='politica_password_min_len')
    politica_password_regex = models.TextField(db_column='politica_password_regex', blank=True, null=True)
    jwt_expiracion_horas = models.SmallIntegerField(db_column='jwt_expiracion_horas')
    intentos_max_ventana = models.SmallIntegerField(db_column='intentos_max_ventana')
    ventana_minutos = models.SmallIntegerField(db_column='ventana_minutos')
    bloqueo_minutos = models.SmallIntegerField(db_column='bloqueo_minutos')
    updated_at = models.DateTimeField(db_column='updated_at')

    class Meta:
        managed = False
        db_table = '"core"."config_seguridad_tenant"'

class LogAuditoria(models.Model):
    id = models.BigAutoField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='core_log_auditoria_tenant_set', blank=True, null=True)
    usuario = models.ForeignKey('core.Usuario', on_delete=models.DO_NOTHING, db_column='usuario_id', related_name='core_log_auditoria_usuario_set', blank=True, null=True)
    entidad = models.TextField(db_column='entidad')
    entidad_id = models.TextField(db_column='entidad_id')
    operacion = models.TextField(db_column='operacion')
    valores_antes = models.JSONField(db_column='valores_antes', blank=True, null=True)
    valores_despues = models.JSONField(db_column='valores_despues', blank=True, null=True)
    criticidad = models.TextField(db_column='criticidad')
    ip_origen = models.GenericIPAddressField(db_column='ip_origen', blank=True, null=True)
    ocurrido_en = models.DateTimeField(db_column='ocurrido_en')

    class Meta:
        managed = False
        db_table = '"core"."log_auditoria"'

class Modulo(models.Model):
    id = models.SmallAutoField(db_column='id', primary_key=True)
    codigo = models.TextField(db_column='codigo', unique=True)
    nombre = models.TextField(db_column='nombre')
    fase = models.SmallIntegerField(db_column='fase')

    class Meta:
        managed = False
        db_table = '"core"."modulo"'

class Notificacion(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='core_notificacion_tenant_set', blank=True, null=True)
    usuario = models.ForeignKey('core.Usuario', on_delete=models.DO_NOTHING, db_column='usuario_id', related_name='core_notificacion_usuario_set', blank=True, null=True)
    canal = models.TextField(db_column='canal')
    asunto = models.TextField(db_column='asunto')
    cuerpo = models.TextField(db_column='cuerpo', blank=True, null=True)
    estado = models.TextField(db_column='estado')
    intentos = models.SmallIntegerField(db_column='intentos')
    created_at = models.DateTimeField(db_column='created_at')
    enviada_en = models.DateTimeField(db_column='enviada_en', blank=True, null=True)

    class Meta:
        managed = False
        db_table = '"core"."notificacion"'

class Permiso(models.Model):
    id = models.AutoField(db_column='id', primary_key=True)
    dominio = models.TextField(db_column='dominio')
    recurso = models.TextField(db_column='recurso')
    accion = models.TextField(db_column='accion')
    codigo = models.TextField(db_column='codigo', unique=True)
    descripcion = models.TextField(db_column='descripcion', blank=True, null=True)

    class Meta:
        managed = False
        db_table = '"core"."permiso"'

class PlanComercial(models.Model):
    id = models.SmallAutoField(db_column='id', primary_key=True)
    codigo = models.TextField(db_column='codigo', unique=True)
    nombre = models.TextField(db_column='nombre')
    licencias_max = models.IntegerField(db_column='licencias_max')
    activo = models.BooleanField(db_column='activo')

    class Meta:
        managed = False
        db_table = '"core"."plan_comercial"'

class Rol(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='core_rol_tenant_set', blank=True, null=True)
    nombre = models.TextField(db_column='nombre')
    es_sistema = models.BooleanField(db_column='es_sistema')
    activo = models.BooleanField(db_column='activo')
    created_at = models.DateTimeField(db_column='created_at')

    class Meta:
        managed = False
        db_table = '"core"."rol"'
        constraints = [
            models.UniqueConstraint(fields=['tenant_id', 'nombre'], name='rol_tenant_id_nombre_key'),
        ]

class RolPermiso(models.Model):
    pk = models.CompositePrimaryKey('rol_id', 'permiso_id')
    rol = models.ForeignKey('core.Rol', on_delete=models.DO_NOTHING, db_column='rol_id', related_name='core_rol_permiso_rol_set')
    permiso = models.ForeignKey('core.Permiso', on_delete=models.DO_NOTHING, db_column='permiso_id', related_name='core_rol_permiso_permiso_set')

    class Meta:
        managed = False
        db_table = '"core"."rol_permiso"'

class Sesion(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='core_sesion_tenant_set')
    usuario = models.ForeignKey('core.Usuario', on_delete=models.DO_NOTHING, db_column='usuario_id', related_name='core_sesion_usuario_set')
    jwt_id = models.TextField(db_column='jwt_id', unique=True)
    ip_origen = models.GenericIPAddressField(db_column='ip_origen', blank=True, null=True)
    user_agent = models.TextField(db_column='user_agent', blank=True, null=True)
    emitida_en = models.DateTimeField(db_column='emitida_en')
    expira_en = models.DateTimeField(db_column='expira_en')
    revocada_en = models.DateTimeField(db_column='revocada_en', blank=True, null=True)
    revocada_por = models.ForeignKey('core.Usuario', on_delete=models.DO_NOTHING, db_column='revocada_por', related_name='core_sesion_revocada_por_set', blank=True, null=True)

    class Meta:
        managed = False
        db_table = '"core"."sesion"'

class Sysadmin(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    correo = models.EmailField(db_column='correo', max_length=255, unique=True)  # citext en PostgreSQL: comparaciones case-insensitive resueltas por la DB
    password_hash = models.TextField(db_column='password_hash')
    mfa_secret = models.TextField(db_column='mfa_secret', blank=True, null=True)
    activo = models.BooleanField(db_column='activo')
    created_at = models.DateTimeField(db_column='created_at')

    class Meta:
        managed = False
        db_table = '"core"."sysadmin"'

class Tenant(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    slug = models.CharField(db_column='slug', max_length=255, unique=True)  # citext en PostgreSQL: comparaciones case-insensitive resueltas por la DB
    razon_social = models.TextField(db_column='razon_social')
    dominio_comercial = models.TextField(db_column='dominio_comercial')
    plan = models.ForeignKey('core.PlanComercial', on_delete=models.DO_NOTHING, db_column='plan_id', related_name='core_tenant_plan_set')
    estado = models.CharField(db_column='estado', max_length=11, choices=[('activo', 'activo'), ('suspendido', 'suspendido'), ('baja_logica', 'baja_logica')])  # ENUM Postgres 'tenant_estado'
    motivo_suspension = models.TextField(db_column='motivo_suspension', blank=True, null=True)
    created_at = models.DateTimeField(db_column='created_at')
    updated_at = models.DateTimeField(db_column='updated_at')

    class Meta:
        managed = False
        db_table = '"core"."tenant"'
        constraints = [
            models.UniqueConstraint(fields=['razon_social', 'dominio_comercial'], name='tenant_razon_social_dominio_comercial_key'),
        ]

class TenantModulo(models.Model):
    pk = models.CompositePrimaryKey('tenant_id', 'modulo_id')
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='core_tenant_modulo_tenant_set')
    modulo = models.ForeignKey('core.Modulo', on_delete=models.DO_NOTHING, db_column='modulo_id', related_name='core_tenant_modulo_modulo_set')
    activo = models.BooleanField(db_column='activo')
    activado_en = models.DateTimeField(db_column='activado_en')

    class Meta:
        managed = False
        db_table = '"core"."tenant_modulo"'

class Usuario(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='core_usuario_tenant_set')
    correo = models.EmailField(db_column='correo', max_length=255)  # citext en PostgreSQL: comparaciones case-insensitive resueltas por la DB
    nombre_completo = models.TextField(db_column='nombre_completo')
    telefono = models.TextField(db_column='telefono', blank=True, null=True)
    puesto = models.TextField(db_column='puesto', blank=True, null=True)
    departamento = models.TextField(db_column='departamento', blank=True, null=True)
    password_hash = models.TextField(db_column='password_hash', blank=True, null=True)
    mfa_secret = models.TextField(db_column='mfa_secret', blank=True, null=True)
    mfa_enrolado = models.BooleanField(db_column='mfa_enrolado')
    estado = models.CharField(db_column='estado', max_length=22, choices=[('pendiente', 'pendiente'), ('activo', 'activo'), ('suspendido', 'suspendido'), ('pendiente_verificacion', 'pendiente_verificacion')])  # ENUM Postgres 'usuario_estado'
    token_activacion = models.TextField(db_column='token_activacion', blank=True, null=True)
    token_activacion_exp = models.DateTimeField(db_column='token_activacion_exp', blank=True, null=True)
    intentos_fallidos = models.SmallIntegerField(db_column='intentos_fallidos')
    bloqueado_hasta = models.DateTimeField(db_column='bloqueado_hasta', blank=True, null=True)
    created_at = models.DateTimeField(db_column='created_at')
    updated_at = models.DateTimeField(db_column='updated_at')

    class Meta:
        managed = False
        db_table = '"core"."usuario"'
        constraints = [
            models.UniqueConstraint(fields=['tenant_id', 'correo'], name='usuario_tenant_id_correo_key'),
        ]

class UsuarioRol(models.Model):
    pk = models.CompositePrimaryKey('usuario_id', 'rol_id')
    usuario = models.ForeignKey('core.Usuario', on_delete=models.DO_NOTHING, db_column='usuario_id', related_name='core_usuario_rol_usuario_set')
    rol = models.ForeignKey('core.Rol', on_delete=models.DO_NOTHING, db_column='rol_id', related_name='core_usuario_rol_rol_set')
    asignado_en = models.DateTimeField(db_column='asignado_en')
    asignado_por = models.ForeignKey('core.Usuario', on_delete=models.DO_NOTHING, db_column='asignado_por', related_name='core_usuario_rol_asignado_por_set', blank=True, null=True)

    class Meta:
        managed = False
        db_table = '"core"."usuario_rol"'

class VActividadUsuarios(models.Model):
    # pk sintetica ('usuario_id'): la vista no garantiza unicidad real por fila
    tenant_id = models.UUIDField(db_column='tenant_id', blank=True, null=True)
    usuario_id = models.UUIDField(db_column='usuario_id', primary_key=True)
    nombre_completo = models.TextField(db_column='nombre_completo', blank=True, null=True)
    logins = models.BigIntegerField(db_column='logins', blank=True, null=True)
    ultima_actividad = models.DateTimeField(db_column='ultima_actividad', blank=True, null=True)

    class Meta:
        managed = False
        db_table = '"core"."v_actividad_usuarios"'
        # Generado a partir de una vista de solo lectura. No borrar.
