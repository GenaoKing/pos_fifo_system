from decimal import Decimal

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('clientes', '0001_initial'),
        ('sucursales', '0001_initial'),
        ('ventas', '0006_venta_condicion_pago_pago_credito'),
    ]

    operations = [
        migrations.CreateModel(
            name='MetodoPlazoCredito',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=100, unique=True)),
                ('tipo', models.CharField(choices=[('VENCIMIENTO_UNICO', 'Vencimiento unico'), ('CUOTAS', 'Cuotas')], default='VENCIMIENTO_UNICO', max_length=20)),
                ('dias_vencimiento', models.PositiveIntegerField(default=30, validators=[django.core.validators.MinValueValidator(1)])),
                ('cantidad_cuotas', models.PositiveIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)])),
                ('frecuencia', models.CharField(choices=[('DIAS', 'Cada N dias'), ('SEMANAL', 'Semanal'), ('QUINCENAL', 'Quincenal'), ('MENSUAL', 'Mensual')], default='MENSUAL', max_length=20)),
                ('inicial_minima_porcentaje', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5, validators=[django.core.validators.MinValueValidator(Decimal('0.00')), django.core.validators.MaxValueValidator(Decimal('100.00'))])),
                ('activo', models.BooleanField(default=True)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('fecha_modificacion', models.DateTimeField(auto_now=True)),
                ('sucursal', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='metodos_plazo_credito', to='sucursales.sucursal')),
            ],
            options={
                'verbose_name': 'Metodo de plazo de credito',
                'verbose_name_plural': 'Metodos de plazo de credito',
                'ordering': ['nombre'],
            },
        ),
        migrations.CreateModel(
            name='CuentaPorCobrar',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('total', models.DecimalField(decimal_places=2, max_digits=12)),
                ('monto_inicial', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('saldo', models.DecimalField(decimal_places=2, max_digits=12)),
                ('estado', models.CharField(choices=[('ABIERTA', 'Abierta'), ('PARCIAL', 'Parcial'), ('PAGADA', 'Pagada'), ('VENCIDA', 'Vencida'), ('ANULADA', 'Anulada')], db_index=True, default='ABIERTA', max_length=20)),
                ('fecha_emision', models.DateField(default=django.utils.timezone.localdate)),
                ('fecha_limite', models.DateField(db_index=True)),
                ('motivo_override', models.CharField(blank=True, max_length=250)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('fecha_modificacion', models.DateTimeField(auto_now=True)),
                ('cliente', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cuentas_por_cobrar', to='clientes.cliente')),
                ('creado_por', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cuentas_cxc_creadas', to=settings.AUTH_USER_MODEL)),
                ('metodo_plazo', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cuentas', to='cuentas_por_cobrar.metodoplazocredito')),
                ('override_autorizado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='overrides_credito_autorizados', to=settings.AUTH_USER_MODEL)),
                ('sucursal', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='cuentas_por_cobrar', to='sucursales.sucursal')),
                ('venta', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='cuenta_por_cobrar', to='ventas.venta')),
            ],
            options={
                'verbose_name': 'Cuenta por cobrar',
                'verbose_name_plural': 'Cuentas por cobrar',
                'ordering': ['-fecha_emision', '-id'],
            },
        ),
        migrations.CreateModel(
            name='PagoCxC',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('metodo', models.CharField(choices=[('EFECTIVO', 'Efectivo'), ('TRANSFERENCIA', 'Transferencia'), ('TARJETA', 'Tarjeta')], max_length=20)),
                ('monto', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('referencia', models.CharField(blank=True, max_length=100)),
                ('fecha_pago', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('estado', models.CharField(choices=[('APLICADO', 'Aplicado'), ('ANULADO', 'Anulado')], default='APLICADO', max_length=20)),
                ('aplicaciones', models.JSONField(blank=True, default=list)),
                ('notas', models.TextField(blank=True)),
                ('cuenta', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pagos_cxc', to='cuentas_por_cobrar.cuentaporcobrar')),
                ('registrado_por', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pagos_cxc_registrados', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Pago CxC',
                'verbose_name_plural': 'Pagos CxC',
                'ordering': ['-fecha_pago'],
            },
        ),
        migrations.CreateModel(
            name='CuotaCxC',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('numero', models.PositiveIntegerField()),
                ('monto', models.DecimalField(decimal_places=2, max_digits=12)),
                ('saldo', models.DecimalField(decimal_places=2, max_digits=12)),
                ('fecha_vencimiento', models.DateField(db_index=True)),
                ('estado', models.CharField(choices=[('PENDIENTE', 'Pendiente'), ('PARCIAL', 'Parcial'), ('PAGADA', 'Pagada'), ('VENCIDA', 'Vencida'), ('ANULADA', 'Anulada')], db_index=True, default='PENDIENTE', max_length=20)),
                ('fecha_pago', models.DateTimeField(blank=True, null=True)),
                ('cuenta', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cuotas', to='cuentas_por_cobrar.cuentaporcobrar')),
            ],
            options={
                'verbose_name': 'Cuota CxC',
                'verbose_name_plural': 'Cuotas CxC',
                'ordering': ['cuenta', 'numero'],
            },
        ),
        migrations.AddIndex(
            model_name='metodoplazocredito',
            index=models.Index(fields=['activo', 'tipo'], name='cuentas_por_activo_283c03_idx'),
        ),
        migrations.AddIndex(
            model_name='metodoplazocredito',
            index=models.Index(fields=['sucursal', 'activo'], name='cuentas_por_sucursa_9047da_idx'),
        ),
        migrations.AddIndex(
            model_name='cuentaporcobrar',
            index=models.Index(fields=['cliente', 'estado'], name='cuentas_por_cliente_244c6d_idx'),
        ),
        migrations.AddIndex(
            model_name='cuentaporcobrar',
            index=models.Index(fields=['estado', 'fecha_limite'], name='cuentas_por_estado_2075a0_idx'),
        ),
        migrations.AddIndex(
            model_name='cuentaporcobrar',
            index=models.Index(fields=['sucursal', 'estado'], name='cuentas_por_sucursa_80ecfe_idx'),
        ),
        migrations.AddIndex(
            model_name='pagocxc',
            index=models.Index(fields=['metodo', 'fecha_pago'], name='cuentas_por_metodo_544275_idx'),
        ),
        migrations.AddIndex(
            model_name='pagocxc',
            index=models.Index(fields=['registrado_por', 'fecha_pago'], name='cuentas_por_registr_2bb9aa_idx'),
        ),
        migrations.AddIndex(
            model_name='pagocxc',
            index=models.Index(fields=['cuenta', 'estado'], name='cuentas_por_cuenta__a8e34a_idx'),
        ),
        migrations.AddIndex(
            model_name='cuotacxc',
            index=models.Index(fields=['estado', 'fecha_vencimiento'], name='cuentas_por_estado_8c1c90_idx'),
        ),
        migrations.AddIndex(
            model_name='cuotacxc',
            index=models.Index(fields=['cuenta', 'estado'], name='cuentas_por_cuenta__c10a43_idx'),
        ),
        migrations.AddConstraint(
            model_name='cuotacxc',
            constraint=models.UniqueConstraint(fields=('cuenta', 'numero'), name='unique_cuota_por_cuenta_numero'),
        ),
    ]
