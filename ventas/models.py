from django.db import models



class Cliente(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='ventas_cliente_tenant_set')
    rfc_o_id_fiscal = models.TextField(db_column='rfc_o_id_fiscal', blank=True, null=True)
    razon_social = models.TextField(db_column='razon_social')
    correo = models.EmailField(db_column='correo', max_length=255, blank=True, null=True)  # citext en PostgreSQL: comparaciones case-insensitive resueltas por la DB
    telefono = models.TextField(db_column='telefono', blank=True, null=True)
    limite_credito = models.DecimalField(db_column='limite_credito', max_digits=14, decimal_places=2)
    activo = models.BooleanField(db_column='activo')
    created_at = models.DateTimeField(db_column='created_at')
    updated_at = models.DateTimeField(db_column='updated_at')

    class Meta:
        managed = False
        db_table = '"ventas"."cliente"'
        constraints = [
            models.UniqueConstraint(fields=['tenant_id', 'rfc_o_id_fiscal'], name='cliente_tenant_id_rfc_o_id_fiscal_key'),
        ]

class Cotizacion(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='ventas_cotizacion_tenant_set')
    folio = models.TextField(db_column='folio')
    cliente = models.ForeignKey('ventas.Cliente', on_delete=models.DO_NOTHING, db_column='cliente_id', related_name='ventas_cotizacion_cliente_set')
    oportunidad = models.ForeignKey('ventas.Oportunidad', on_delete=models.DO_NOTHING, db_column='oportunidad_id', related_name='ventas_cotizacion_oportunidad_set', blank=True, null=True)
    estado = models.CharField(db_column='estado', max_length=20, choices=[('borrador', 'borrador'), ('pendiente_aprobacion', 'pendiente_aprobacion'), ('aprobada', 'aprobada'), ('rechazada', 'rechazada')])  # ENUM Postgres 'cotizacion_estado'
    subtotal = models.DecimalField(db_column='subtotal', max_digits=14, decimal_places=2)
    descuento_pct = models.DecimalField(db_column='descuento_pct', max_digits=5, decimal_places=2)
    total = models.DecimalField(db_column='total', max_digits=14, decimal_places=2)
    vigente_hasta = models.DateField(db_column='vigente_hasta', blank=True, null=True)
    vendedor = models.ForeignKey('core.Usuario', on_delete=models.DO_NOTHING, db_column='vendedor_id', related_name='ventas_cotizacion_vendedor_set', blank=True, null=True)  # RV-01..06
    created_at = models.DateTimeField(db_column='created_at')
    updated_at = models.DateTimeField(db_column='updated_at')

    class Meta:
        managed = False
        db_table = '"ventas"."cotizacion"'
        constraints = [
            models.UniqueConstraint(fields=['tenant_id', 'folio'], name='cotizacion_tenant_id_folio_key'),
        ]

class CotizacionLinea(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    cotizacion = models.ForeignKey('ventas.Cotizacion', on_delete=models.DO_NOTHING, db_column='cotizacion_id', related_name='ventas_cotizacion_linea_cotizacion_set')
    producto = models.ForeignKey('inventario.Producto', on_delete=models.DO_NOTHING, db_column='producto_id', related_name='ventas_cotizacion_linea_producto_set')
    descripcion = models.TextField(db_column='descripcion')
    cantidad = models.DecimalField(db_column='cantidad', max_digits=12, decimal_places=3)
    precio_unitario = models.DecimalField(db_column='precio_unitario', max_digits=14, decimal_places=4)
    importe = models.DecimalField(db_column='importe', max_digits=14, decimal_places=2, blank=True, null=True)

    class Meta:
        managed = False
        db_table = '"ventas"."cotizacion_linea"'

class FacturaLinea(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    factura = models.ForeignKey('ventas.FacturaVenta', on_delete=models.DO_NOTHING, db_column='factura_id', related_name='ventas_factura_linea_factura_set')
    pedido_linea = models.ForeignKey('ventas.PedidoLinea', on_delete=models.DO_NOTHING, db_column='pedido_linea_id', related_name='ventas_factura_linea_pedido_linea_set')
    cantidad = models.DecimalField(db_column='cantidad', max_digits=12, decimal_places=3)
    precio_unitario = models.DecimalField(db_column='precio_unitario', max_digits=14, decimal_places=4)
    importe = models.DecimalField(db_column='importe', max_digits=14, decimal_places=2, blank=True, null=True)

    class Meta:
        managed = False
        db_table = '"ventas"."factura_linea"'

class FacturaVenta(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='ventas_factura_venta_tenant_set')
    folio = models.TextField(db_column='folio')
    pedido = models.ForeignKey('ventas.PedidoVenta', on_delete=models.DO_NOTHING, db_column='pedido_id', related_name='ventas_factura_venta_pedido_set')
    cliente = models.ForeignKey('ventas.Cliente', on_delete=models.DO_NOTHING, db_column='cliente_id', related_name='ventas_factura_venta_cliente_set')
    estado = models.CharField(db_column='estado', max_length=16, choices=[('emitida', 'emitida'), ('cancelada', 'cancelada'), ('con_nota_credito', 'con_nota_credito')])  # ENUM Postgres 'factura_estado'
    subtotal = models.DecimalField(db_column='subtotal', max_digits=14, decimal_places=2)
    impuestos = models.DecimalField(db_column='impuestos', max_digits=14, decimal_places=2)
    total = models.DecimalField(db_column='total', max_digits=14, decimal_places=2)
    fecha_emision = models.DateTimeField(db_column='fecha_emision')
    vendedor = models.ForeignKey('core.Usuario', on_delete=models.DO_NOTHING, db_column='vendedor_id', related_name='ventas_factura_venta_vendedor_set', blank=True, null=True)  # RV-01..06

    class Meta:
        managed = False
        db_table = '"ventas"."factura_venta"'
        constraints = [
            models.UniqueConstraint(fields=['tenant_id', 'folio'], name='factura_venta_tenant_id_folio_key'),
        ]

class NotaCredito(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='ventas_nota_credito_tenant_set')
    factura = models.ForeignKey('ventas.FacturaVenta', on_delete=models.DO_NOTHING, db_column='factura_id', related_name='ventas_nota_credito_factura_set')
    motivo = models.TextField(db_column='motivo')
    monto = models.DecimalField(db_column='monto', max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(db_column='created_at')

    class Meta:
        managed = False
        db_table = '"ventas"."nota_credito"'

class ConfigVentas(models.Model):
    # RF-30..44: configuracion de ventas por tenant. Ver sql/2026-07-25_rf30_44_ventas.sql.
    tenant = models.OneToOneField('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', primary_key=True, related_name='ventas_config_ventas')
    iva_pct = models.DecimalField(db_column='iva_pct', max_digits=5, decimal_places=2)
    descuento_max_pct = models.DecimalField(db_column='descuento_max_pct', max_digits=5, decimal_places=2)
    permite_backorder = models.BooleanField(db_column='permite_backorder')
    updated_at = models.DateTimeField(db_column='updated_at')

    class Meta:
        managed = False
        db_table = '"ventas"."config_ventas"'

class Oportunidad(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='ventas_oportunidad_tenant_set')
    cliente = models.ForeignKey('ventas.Cliente', on_delete=models.DO_NOTHING, db_column='cliente_id', related_name='ventas_oportunidad_cliente_set')
    nombre = models.TextField(db_column='nombre')
    valor_estimado = models.DecimalField(db_column='valor_estimado', max_digits=14, decimal_places=2, blank=True, null=True)
    fecha_cierre_estimada = models.DateField(db_column='fecha_cierre_estimada', blank=True, null=True)  # RF-30
    etapa = models.CharField(db_column='etapa', max_length=12, choices=[('prospeccion', 'prospeccion'), ('calificacion', 'calificacion'), ('propuesta', 'propuesta'), ('negociacion', 'negociacion'), ('cierre', 'cierre')])  # ENUM Postgres 'oportunidad_etapa'
    estado = models.CharField(db_column='estado', max_length=7, choices=[('abierta', 'abierta'), ('ganada', 'ganada'), ('perdida', 'perdida')])  # ENUM Postgres 'oportunidad_estado'
    motivo_perdida = models.TextField(db_column='motivo_perdida', blank=True, null=True)
    responsable = models.ForeignKey('core.Usuario', on_delete=models.DO_NOTHING, db_column='responsable_id', related_name='ventas_oportunidad_responsable_set', blank=True, null=True)
    created_at = models.DateTimeField(db_column='created_at')
    updated_at = models.DateTimeField(db_column='updated_at')

    class Meta:
        managed = False
        db_table = '"ventas"."oportunidad"'

class PedidoLinea(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    pedido = models.ForeignKey('ventas.PedidoVenta', on_delete=models.DO_NOTHING, db_column='pedido_id', related_name='ventas_pedido_linea_pedido_set')
    producto = models.ForeignKey('inventario.Producto', on_delete=models.DO_NOTHING, db_column='producto_id', related_name='ventas_pedido_linea_producto_set')
    cantidad = models.DecimalField(db_column='cantidad', max_digits=12, decimal_places=3)
    cantidad_facturada = models.DecimalField(db_column='cantidad_facturada', max_digits=12, decimal_places=3)
    cantidad_reservada = models.DecimalField(db_column='cantidad_reservada', max_digits=12, decimal_places=3)  # RF-38
    precio_unitario = models.DecimalField(db_column='precio_unitario', max_digits=14, decimal_places=4)

    class Meta:
        managed = False
        db_table = '"ventas"."pedido_linea"'

class PedidoVenta(models.Model):
    id = models.UUIDField(db_column='id', primary_key=True)
    tenant = models.ForeignKey('core.Tenant', on_delete=models.DO_NOTHING, db_column='tenant_id', related_name='ventas_pedido_venta_tenant_set')
    folio = models.TextField(db_column='folio')
    cliente = models.ForeignKey('ventas.Cliente', on_delete=models.DO_NOTHING, db_column='cliente_id', related_name='ventas_pedido_venta_cliente_set')
    cotizacion = models.ForeignKey('ventas.Cotizacion', on_delete=models.DO_NOTHING, db_column='cotizacion_id', related_name='ventas_pedido_venta_cotizacion_set', blank=True, null=True)
    estado = models.CharField(db_column='estado', max_length=17, choices=[('borrador', 'borrador'), ('confirmado', 'confirmado'), ('pendiente_surtido', 'pendiente_surtido'), ('cancelado', 'cancelado'), ('facturado_parcial', 'facturado_parcial'), ('facturado_total', 'facturado_total')])  # ENUM Postgres 'pedido_estado'
    total = models.DecimalField(db_column='total', max_digits=14, decimal_places=2)
    almacen = models.ForeignKey('inventario.Almacen', on_delete=models.DO_NOTHING, db_column='almacen_id', related_name='ventas_pedido_venta_almacen_set', blank=True, null=True)  # RF-38
    vendedor = models.ForeignKey('core.Usuario', on_delete=models.DO_NOTHING, db_column='vendedor_id', related_name='ventas_pedido_venta_vendedor_set', blank=True, null=True)  # RV-01..06
    created_at = models.DateTimeField(db_column='created_at')
    updated_at = models.DateTimeField(db_column='updated_at')

    class Meta:
        managed = False
        db_table = '"ventas"."pedido_venta"'
        constraints = [
            models.UniqueConstraint(fields=['tenant_id', 'folio'], name='pedido_venta_tenant_id_folio_key'),
        ]

class VPipelineOportunidades(models.Model):
    # pk sintetica ('tenant_id'): la vista no garantiza unicidad real por fila
    tenant_id = models.UUIDField(db_column='tenant_id', primary_key=True)
    etapa = models.CharField(db_column='etapa', max_length=12, choices=[('prospeccion', 'prospeccion'), ('calificacion', 'calificacion'), ('propuesta', 'propuesta'), ('negociacion', 'negociacion'), ('cierre', 'cierre')], blank=True, null=True)  # ENUM Postgres 'oportunidad_etapa'
    num_oportunidades = models.BigIntegerField(db_column='num_oportunidades', blank=True, null=True)
    valor_total_etapa = models.DecimalField(db_column='valor_total_etapa', max_digits=10, decimal_places=5, blank=True, null=True)  # max_digits and decimal_places have been guessed, as this database handles decimal fields as float

    class Meta:
        managed = False
        db_table = '"ventas"."v_pipeline_oportunidades"'
        # Generado a partir de una vista de solo lectura. No borrar.
