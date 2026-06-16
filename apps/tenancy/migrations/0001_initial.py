from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Identity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('password', models.CharField(max_length=128)),
                ('nombre', models.CharField(blank=True, max_length=200)),
                ('activo', models.BooleanField(default=True)),
                ('is_global', models.BooleanField(default=False)),
                ('fecha_creacion', models.DateTimeField(default=django.utils.timezone.now)),
                ('fecha_modificacion', models.DateTimeField(auto_now=True)),
                ('ultimo_acceso', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'tenancy_identities',
                'ordering': ['email'],
            },
        ),
        migrations.CreateModel(
            name='Tenant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tenant_key', models.SlugField(help_text='Identificador tecnico estable. No cambia sin migracion.', max_length=64, unique=True)),
                ('slug', models.SlugField(max_length=120, unique=True)),
                ('nombre', models.CharField(max_length=200)),
                ('rnc', models.CharField(blank=True, max_length=20)),
                ('db_name', models.CharField(blank=True, max_length=128, unique=True)),
                ('media_prefix', models.CharField(blank=True, max_length=160)),
                ('plan_slug', models.SlugField(blank=True, max_length=100)),
                ('activo', models.BooleanField(default=True)),
                ('fecha_creacion', models.DateTimeField(default=django.utils.timezone.now)),
                ('fecha_modificacion', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'tenancy_tenants',
                'ordering': ['nombre'],
            },
        ),
        migrations.CreateModel(
            name='Domain',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('domain', models.CharField(max_length=255, unique=True)),
                ('is_primary', models.BooleanField(default=True)),
                ('activo', models.BooleanField(default=True)),
                ('fecha_creacion', models.DateTimeField(default=django.utils.timezone.now)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='domains', to='tenancy.tenant')),
            ],
            options={
                'db_table': 'tenancy_domains',
                'ordering': ['domain'],
            },
        ),
        migrations.CreateModel(
            name='Membership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('username', models.CharField(max_length=150)),
                ('rol', models.CharField(default='ADMIN', max_length=20)),
                ('activo', models.BooleanField(default=True)),
                ('fecha_creacion', models.DateTimeField(default=django.utils.timezone.now)),
                ('fecha_modificacion', models.DateTimeField(auto_now=True)),
                ('identity', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='tenancy.identity')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='tenancy.tenant')),
            ],
            options={
                'db_table': 'tenancy_memberships',
                'ordering': ['identity__email', 'tenant__tenant_key'],
                'unique_together': {('identity', 'tenant')},
            },
        ),
        migrations.CreateModel(
            name='SyncToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('sucursal_codigo', models.CharField(max_length=20)),
                ('activo', models.BooleanField(default=True)),
                ('descripcion', models.CharField(blank=True, max_length=200)),
                ('fecha_creacion', models.DateTimeField(default=django.utils.timezone.now)),
                ('fecha_modificacion', models.DateTimeField(auto_now=True)),
                ('ultimo_uso', models.DateTimeField(blank=True, null=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sync_tokens', to='tenancy.tenant')),
            ],
            options={
                'db_table': 'tenancy_sync_tokens',
                'ordering': ['tenant__tenant_key', 'sucursal_codigo'],
                'unique_together': {('tenant', 'sucursal_codigo')},
            },
        ),
    ]
