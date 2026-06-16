from pathlib import Path

from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from apps.configuracion.models import ConfiguracionNegocio
from apps.productos.models import Producto
from apps.tenancy.management.base import TenantCommandMixin
from apps.tenancy.media import normalize_media_name


class Command(TenantCommandMixin, BaseCommand):
    help = 'Migra media local legacy hacia rutas prefijadas por tenant.'

    def add_arguments(self, parser):
        self.add_tenant_argument(parser)
        parser.add_argument(
            '--source-media-root',
            required=True,
            help='Directorio local que contiene rutas legacy como productos/ y config/.',
        )
        parser.add_argument(
            '--only',
            choices=['productos', 'logos', 'all'],
            default='all',
            help='Tipo de media a migrar.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Ejecuta copias/uploads y actualiza la BD. Sin esto solo reporta.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Modo lectura explicito. No puede combinarse con --apply.',
        )

    def handle(self, *args, **options):
        if options['apply'] and options['dry_run']:
            raise CommandError('Use --apply o --dry-run, no ambos.')

        source_root = Path(options['source_media_root']).expanduser().resolve()
        if not source_root.is_dir():
            raise CommandError(f'El directorio source-media-root no existe: {source_root}')

        tenant = self.get_tenant(options['tenant'])
        dry_run = not options['apply']
        stats = {
            'uploaded': 0,
            'updated': 0,
            'missing': 0,
            'already_prefixed': 0,
            'skipped': 0,
        }

        def migrate():
            if options['only'] in {'productos', 'all'}:
                self._migrate_queryset(
                    Producto.objects.exclude(imagen='').exclude(imagen__isnull=True),
                    'imagen',
                    tenant.media_prefix,
                    source_root,
                    dry_run,
                    stats,
                )
            if options['only'] in {'logos', 'all'}:
                self._migrate_queryset(
                    ConfiguracionNegocio.objects.exclude(logo='').exclude(logo__isnull=True),
                    'logo',
                    tenant.media_prefix,
                    source_root,
                    dry_run,
                    stats,
                )

        self.stdout.write(
            f'Migrando media tenant={tenant.tenant_key} '
            f'prefix={tenant.media_prefix} mode={"DRY-RUN" if dry_run else "APPLY"}'
        )
        self.run_in_tenant(tenant, migrate)

        for key, value in stats.items():
            self.stdout.write(f'{key}: {value}')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN: no se copiaron archivos ni se actualizo BD.'))
        else:
            self.stdout.write(self.style.SUCCESS('Media migrada.'))

    def _migrate_queryset(self, queryset, field_name, media_prefix, source_root, dry_run, stats):
        prefix = normalize_media_name(media_prefix)
        prefix = f'{prefix}/' if prefix else ''

        for obj in queryset.iterator():
            field_file = getattr(obj, field_name)
            current_name = normalize_media_name(field_file.name)
            if not current_name:
                stats['skipped'] += 1
                continue

            if prefix and current_name.startswith(prefix):
                stats['already_prefixed'] += 1
                continue

            destination_name = f'{prefix}{current_name}' if prefix else current_name
            source_path = self._source_path(source_root, current_name)
            if not source_path.is_file():
                stats['missing'] += 1
                self.stdout.write(f'MISSING {current_name} -> {destination_name}')
                continue

            if dry_run:
                self.stdout.write(f'DRY-RUN {current_name} -> {destination_name}')
                continue

            if not default_storage.exists(destination_name):
                with source_path.open('rb') as source_file:
                    # save() puede sanitizar/renombrar; usar el nombre REAL
                    # devuelto para que el campo no apunte a un blob inexistente.
                    saved_name = default_storage.save(destination_name, File(source_file))
                stats['uploaded'] += 1
            else:
                saved_name = destination_name
                stats['skipped'] += 1

            setattr(obj, field_name, saved_name)
            obj.save(update_fields=[field_name])
            stats['updated'] += 1
            self.stdout.write(f'OK {current_name} -> {saved_name}')

    def _source_path(self, source_root, relative_name):
        safe_name = normalize_media_name(relative_name)
        return source_root.joinpath(*safe_name.split('/')).resolve()
