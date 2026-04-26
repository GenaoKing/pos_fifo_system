"""
apps/sucursales/management/commands/vincular_sucursal_token.py

Vincula una sucursal a un usuario de servicio con su DRF Token.

Uso:
    python manage.py vincular_sucursal_token --sucursal SD-001

Que hace:
    1. Busca la sucursal por codigo
    2. Si no tiene usuario_servicio asignado: crea uno con username
       `sucursal_service_<codigo>` (sin password utilizable — no puede
       iniciar sesion web)
    3. Crea/obtiene el Token DRF de ese usuario
    4. Imprime el token para copiar al .bat de la sucursal

El token es lo que va en CLOUD_API_TOKEN del ambiente de la sucursal.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework.authtoken.models import Token

from apps.sucursales.models import Sucursal


class Command(BaseCommand):
    help = 'Crea usuario de servicio + DRF Token para una sucursal, para auth de API sync.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sucursal',
            required=True,
            help='Codigo de la sucursal (ej: SD-001)',
        )
        parser.add_argument(
            '--regenerar',
            action='store_true',
            help='Si ya existe token, borralo y genera uno nuevo.',
        )

    def handle(self, *args, **opts):
        codigo = opts['sucursal']
        regenerar = opts['regenerar']

        try:
            sucursal = Sucursal.objects.get(codigo=codigo)
        except Sucursal.DoesNotExist:
            raise CommandError(f'Sucursal "{codigo}" no existe.')

        User = get_user_model()

        # 1. Obtener o crear usuario de servicio
        username = f'sucursal_service_{codigo}'
        user, user_creado = User.objects.get_or_create(
            username=username,
            defaults={
                'activo': True,
                'is_staff': False,
                'is_superuser': False,
                # Si tu modelo tiene first_name/last_name y lo requiere:
                'first_name': 'Sucursal',
                'last_name': codigo,
            },
        )
        if user_creado:
            # Password no utilizable: el user no puede hacer login web,
            # solo se autentica via token.
            user.set_unusable_password()
            user.save()
            # Si tu modelo Usuario tiene un campo 'rol', puedes asignarle uno
            # de servicio. Si no, ignorar este bloque.
            if hasattr(user, 'rol'):
                try:
                    # Si tienes un rol SERVICE, usalo. Si no, CAJERA es suficiente
                    # para que no pueda hacer nada raro en la web.
                    user.rol = 'SERVICE' if 'SERVICE' in dict(
                        getattr(user, 'ROLES', [])
                    ) else 'CAJERA'
                    user.save(update_fields=['rol'])
                except Exception:
                    pass
            self.stdout.write(self.style.SUCCESS(
                f'  [OK] Usuario de servicio creado: {username}'
            ))
        else:
            self.stdout.write(f'  [OK] Usuario de servicio existente: {username}')

        # 2. Vincular a la sucursal si no esta vinculado
        if not hasattr(sucursal, 'usuario_servicio'):
            raise CommandError(
                'El modelo Sucursal no tiene campo `usuario_servicio`. '
                'Aplica el patch a apps/sucursales/models.py y migra.'
            )

        if sucursal.usuario_servicio_id != user.id:
            sucursal.usuario_servicio = user
            sucursal.save(update_fields=['usuario_servicio'])
            self.stdout.write(self.style.SUCCESS(
                f'  [OK] Sucursal {codigo} vinculada al usuario {username}'
            ))

        # 3. Obtener o crear token
        if regenerar:
            Token.objects.filter(user=user).delete()
            self.stdout.write('  [OK] Token anterior eliminado (--regenerar)')

        token, token_creado = Token.objects.get_or_create(user=user)
        accion = 'creado' if token_creado else 'existente'

        # 4. Imprimir token bonito
        self.stdout.write('')
        self.stdout.write('=' * 64)
        self.stdout.write('  TOKEN DE SUCURSAL')
        self.stdout.write('=' * 64)
        self.stdout.write(f'  Sucursal:    {sucursal.codigo}  ({sucursal.nombre})')
        self.stdout.write(f'  Usuario:     {username}')
        self.stdout.write(f'  Token:       {token.key}')
        self.stdout.write(f'  Estado:      {accion}')
        self.stdout.write('=' * 64)
        self.stdout.write('')
        self.stdout.write('  Guarda este token — DRF no permite recuperarlo despues.')
        self.stdout.write('  Ponerlo en CLOUD_API_TOKEN del .bat de la sucursal.')
        self.stdout.write('')