from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('sucursales', '0003_sucursal_negocio'),
        ('sync', '0004_alter_eventosync_tipo_evento'),
    ]

    operations = [
        migrations.AlterField(
            model_name='eventosync',
            name='tipo_evento',
            field=models.CharField(
                choices=[
                    ('VENTA_CREADA', 'Venta creada'),
                    ('VENTA_ANULADA', 'Venta anulada'),
                    ('APERTURA_CAJA', 'Apertura de caja'),
                    ('MOVIMIENTO_CAJA', 'Movimiento de caja (retiro/gasto/ingreso)'),
                    ('CIERRE_CAJA', 'Cierre de caja'),
                    ('AJUSTE_INVENTARIO', 'Ajuste de inventario'),
                    ('COMPRA_REGISTRADA', 'Compra registrada'),
                    ('INVENTARIO_MOVIMIENTO_REGISTRADO', 'Movimiento de inventario registrado'),
                    ('INVENTARIO_SNAPSHOT', 'Snapshot de inventario'),
                    ('COTIZACION_CREADA', 'Cotizacion creada'),
                    ('COTIZACION_CONVERTIDA', 'Cotizacion convertida'),
                    ('CXC_CREADA', 'Cuenta por cobrar creada'),
                    ('CXC_PAGO_REGISTRADO', 'Pago de cuenta por cobrar registrado'),
                    ('CXC_PAGO_ANULADO', 'Pago de cuenta por cobrar anulado'),
                    ('CXC_ANULADA', 'Cuenta por cobrar anulada'),
                ],
                db_index=True,
                max_length=32,
                verbose_name='Tipo de evento',
            ),
        ),
        migrations.AlterField(
            model_name='versionmaestro',
            name='tabla',
            field=models.CharField(
                choices=[
                    ('productos', 'Productos'),
                    ('categorias', 'Categorias'),
                    ('clientes', 'Clientes'),
                    ('configuracion', 'Configuracion'),
                    ('metodos_credito', 'Metodos de credito'),
                    ('roles', 'Roles'),
                    ('asignaciones', 'Asignaciones'),
                ],
                max_length=32,
                unique=True,
                verbose_name='Tabla',
            ),
        ),
        migrations.CreateModel(
            name='InventarioMovimientoSync',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(db_index=True, max_length=32, verbose_name='Tipo')),
                ('movimiento_id_local', models.PositiveIntegerField(blank=True, null=True, verbose_name='ID local del movimiento')),
                ('referencia_tipo', models.CharField(blank=True, default='', max_length=50)),
                ('referencia_id', models.PositiveIntegerField(blank=True, null=True)),
                ('producto_sku', models.CharField(db_index=True, max_length=50)),
                ('producto_nombre', models.CharField(blank=True, default='', max_length=200)),
                ('lote_numero', models.CharField(blank=True, default='', max_length=50)),
                ('cantidad', models.IntegerField()),
                ('cantidad_anterior', models.IntegerField(blank=True, null=True)),
                ('cantidad_nueva', models.IntegerField(blank=True, null=True)),
                ('costo_unitario', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('usuario_username', models.CharField(blank=True, default='', max_length=150)),
                ('notas', models.TextField(blank=True, default='')),
                ('fecha_movimiento', models.DateTimeField(db_index=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sucursal', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='movimientos_inventario_sync', to='sucursales.sucursal', verbose_name='Sucursal')),
            ],
            options={
                'verbose_name': 'Movimiento de inventario sincronizado',
                'verbose_name_plural': 'Movimientos de inventario sincronizados',
                'ordering': ['-fecha_movimiento', '-id'],
            },
        ),
        migrations.CreateModel(
            name='InventarioSucursalSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('producto_sku', models.CharField(db_index=True, max_length=50)),
                ('producto_nombre', models.CharField(blank=True, default='', max_length=200)),
                ('stock_actual', models.IntegerField(default=0)),
                ('stock_minimo', models.IntegerField(default=0)),
                ('bajo_stock', models.BooleanField(db_index=True, default=False)),
                ('valor_fifo', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('timestamp', models.DateTimeField(db_index=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sucursal', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventario_snapshots', to='sucursales.sucursal', verbose_name='Sucursal')),
            ],
            options={
                'verbose_name': 'Snapshot de inventario por sucursal',
                'verbose_name_plural': 'Snapshots de inventario por sucursal',
                'ordering': ['sucursal', 'producto_sku'],
            },
        ),
        migrations.AddIndex(
            model_name='inventariomovimientosync',
            index=models.Index(fields=['sucursal', 'producto_sku'], name='sync_invent_sucursa_4b46bd_idx'),
        ),
        migrations.AddIndex(
            model_name='inventariomovimientosync',
            index=models.Index(fields=['referencia_tipo', 'referencia_id'], name='sync_invent_referen_f042fa_idx'),
        ),
        migrations.AddConstraint(
            model_name='inventariomovimientosync',
            constraint=models.UniqueConstraint(fields=('sucursal', 'movimiento_id_local'), name='unique_movimiento_inventario_sync_local'),
        ),
        migrations.AddIndex(
            model_name='inventariosucursalsnapshot',
            index=models.Index(fields=['sucursal', 'bajo_stock'], name='sync_invent_sucursa_33f7cc_idx'),
        ),
        migrations.AddIndex(
            model_name='inventariosucursalsnapshot',
            index=models.Index(fields=['producto_sku', 'timestamp'], name='sync_invent_product_2c7572_idx'),
        ),
        migrations.AddConstraint(
            model_name='inventariosucursalsnapshot',
            constraint=models.UniqueConstraint(fields=('sucursal', 'producto_sku'), name='unique_snapshot_inventario_sucursal_producto'),
        ),
    ]
