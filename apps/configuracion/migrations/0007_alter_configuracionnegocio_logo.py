import apps.tenancy.media
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('configuracion', '0006_accesorapidopos'),
    ]

    operations = [
        migrations.AlterField(
            model_name='configuracionnegocio',
            name='logo',
            field=models.ImageField(
                blank=True,
                help_text='Logo para tickets, PDFs y cotizaciones',
                null=True,
                upload_to=apps.tenancy.media.config_logo_upload_to,
                verbose_name='Logo',
            ),
        ),
    ]
