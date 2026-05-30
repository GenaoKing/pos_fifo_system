from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('configuracion', '0005_configuracionnegocio_permitir_inventario_negativo'),
        ('productos', '0006_producto_estado_producto_marca'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccesoRapidoPOS',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('etiqueta', models.CharField(blank=True, help_text='Texto visible en el boton. Si se deja vacio, se usa el nombre del producto o categoria.', max_length=80, verbose_name='Etiqueta')),
                ('tipo', models.CharField(choices=[('producto', 'Producto'), ('categoria', 'Categoria')], default='producto', max_length=20, verbose_name='Tipo')),
                ('color', models.CharField(choices=[('azul', 'Azul'), ('verde', 'Verde'), ('ambar', 'Ambar'), ('gris', 'Gris')], default='azul', max_length=20, verbose_name='Color')),
                ('orden', models.PositiveIntegerField(default=0, help_text='Menor numero aparece primero.', verbose_name='Orden')),
                ('activo', models.BooleanField(default=True, verbose_name='Activo')),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('fecha_modificacion', models.DateTimeField(auto_now=True)),
                ('categoria', models.ForeignKey(blank=True, help_text='Requerida cuando el tipo es Categoria.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='accesos_rapidos_pos', to='productos.categoria')),
                ('producto', models.ForeignKey(blank=True, help_text='Requerido cuando el tipo es Producto.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='accesos_rapidos_pos', to='productos.producto')),
            ],
            options={
                'verbose_name': 'Acceso rapido POS',
                'verbose_name_plural': 'Accesos rapidos POS',
                'ordering': ['orden', 'id'],
            },
        ),
    ]
