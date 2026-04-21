"""
apps/api/management/commands/crear_tokens_api.py
Crea tokens de API para usuarios ADMIN y SYSADMIN.

Uso:
    python manage.py crear_tokens_api
    python manage.py crear_tokens_api --usuario santiago
    python manage.py crear_tokens_api --regenerar

Notas:
    - Por defecto, solo crea tokens para usuarios que no tienen uno
    - Con --regenerar, elimina y recrea el token del usuario especificado
    - El token se muestra en consola una sola vez
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

User = get_user_model()


class Command(BaseCommand):
    help = 'Crea tokens de API para usuarios ADMIN y SYSADMIN'

    def add_arguments(self, parser):
        parser.add_argument(
            '--usuario',
            type=str,
            help='Username específico (default: todos los ADMIN/SYSADMIN)',
        )
        parser.add_argument(
            '--regenerar',
            action='store_true',
            help='Eliminar y recrear token existente',
        )

    def handle(self, *args, **options):
        usuario_especifico = options.get('usuario')
        regenerar = options.get('regenerar', False)

        if usuario_especifico:
            usuarios = User.objects.filter(username=usuario_especifico)
            if not usuarios.exists():
                self.stderr.write(
                    self.style.ERROR(f'Usuario "{usuario_especifico}" no encontrado.')
                )
                return
        else:
            usuarios = User.objects.filter(rol__in=['ADMIN', 'SYSADMIN'], activo=True)

        if not usuarios.exists():
            self.stdout.write(
                self.style.WARNING('No se encontraron usuarios ADMIN/SYSADMIN activos.')
            )
            return

        self.stdout.write('')
        self.stdout.write('=' * 60)
        self.stdout.write('  TOKENS DE API')
        self.stdout.write('=' * 60)

        creados = 0
        existentes = 0

        for user in usuarios:
            token_existente = Token.objects.filter(user=user).first()

            if token_existente and regenerar:
                token_existente.delete()
                token_existente = None
                self.stdout.write(
                    self.style.WARNING(f'  Token anterior de {user.username} eliminado.')
                )

            if token_existente:
                existentes += 1
                self.stdout.write(
                    f'  {user.username} ({user.rol}): ya tiene token — '
                    f'{token_existente.key[:8]}...'
                )
            else:
                token = Token.objects.create(user=user)
                creados += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  {user.username} ({user.rol}): {token.key}'
                    )
                )

        self.stdout.write('')
        self.stdout.write(f'  Creados: {creados} | Ya existentes: {existentes}')
        self.stdout.write('=' * 60)

        if creados > 0:
            self.stdout.write('')
            self.stdout.write(
                self.style.WARNING(
                    '  ⚠ Guarda estos tokens — no se pueden recuperar después.'
                )
            )
            self.stdout.write(
                '  Uso: Authorization: Token <key>'
            )
            self.stdout.write('')