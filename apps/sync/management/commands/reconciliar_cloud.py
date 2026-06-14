"""
apps/sync/management/commands/reconciliar_cloud.py

Bootstrap UNICO de maestros LOCAL -> CLOUD.

Sube el catalogo que hoy vive en la sucursal (categorias, productos y clientes)
al cloud de produccion, para que despues el flujo correcto sea el normal:
el portal cloud autora los maestros y la sucursal los baja (pull).

NO es parte del ciclo de sync. Es una operacion manual que se corre UNA VEZ,
desde la PC de la sucursal, apuntando al cloud de produccion.

  - PUSH de eventos (ventas) -> sigue siendo el daemon `sincronizar`.
  - PULL de maestros (cloud -> local) -> sigue siendo el daemon `sincronizar`.
  - Este comando solo siembra el cloud con lo que ya existe local.

Idempotente: por defecto solo CREA lo que falta en el cloud (busca por clave
natural: categoria.nombre, producto.sku, cliente.cedula_rnc/nombre). Con
--actualizar tambien hace PATCH de lo que ya existe.

Autenticacion: usa un token DRF de un usuario **SYSADMIN/ADMIN** del cloud
(no el token de sucursal, que es de solo lectura). Generarlo en el cloud con:
    python manage.py crear_tokens_api --usuario <sysadmin>

Uso (desde la sucursal):
    set CLOUD_ADMIN_TOKEN=<token-sysadmin-del-cloud>
    python manage.py reconciliar_cloud --cloud-url https://<cloud-prod> --dry-run
    python manage.py reconciliar_cloud --cloud-url https://<cloud-prod>
    python manage.py reconciliar_cloud --solo categorias,productos --actualizar

Si no se pasa --cloud-url se usa settings.CLOUD_API_URL.
Si no se pasa --token se usa la env var CLOUD_ADMIN_TOKEN.
"""
import os
import time
from decimal import Decimal

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

ENTIDADES_VALIDAS = ('categorias', 'productos', 'clientes')


class Command(BaseCommand):
    help = 'Sube los maestros locales (categorias, productos, clientes) al cloud (bootstrap unico).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--cloud-url',
            default=None,
            help='URL base del cloud. Default: settings.CLOUD_API_URL.',
        )
        parser.add_argument(
            '--token',
            default=None,
            help='Token DRF de un usuario SYSADMIN/ADMIN del cloud. '
                 'Default: env CLOUD_ADMIN_TOKEN.',
        )
        parser.add_argument(
            '--solo',
            default=None,
            help='Lista separada por comas: categorias,productos,clientes. '
                 'Default: las tres.',
        )
        parser.add_argument(
            '--actualizar',
            action='store_true',
            help='Ademas de crear lo que falta, hace PATCH de lo ya existente.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No escribe nada en el cloud; solo reporta que haria.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Procesa como maximo N registros por entidad (para pruebas).',
        )
        parser.add_argument(
            '--http-timeout',
            type=int,
            default=60,
            help='Timeout HTTP por request en segundos. Default 60; util para el '
                 'cold-start de staging (Container App escala a cero). No toca '
                 'SYNC_HTTP_TIMEOUT del daemon.',
        )

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        self.cloud_url = (
            opts.get('cloud_url') or getattr(settings, 'CLOUD_API_URL', '')
        ).rstrip('/')
        self.token = opts.get('token') or os.environ.get('CLOUD_ADMIN_TOKEN', '')
        self.actualizar = opts['actualizar']
        self.dry_run = opts['dry_run']
        self.limit = opts['limit']
        self.timeout = opts.get('http_timeout') or getattr(settings, 'SYNC_HTTP_TIMEOUT', 10)
        # La API de maestros aplica throttling (scope 'maestros', ~60/min).
        # Un catalogo grande supera ese ritmo, asi que reintentamos ante 429
        # respetando Retry-After. Es un bootstrap unico: la lentitud no importa.
        self.max_429_retries = 8

        if not self.cloud_url:
            raise CommandError(
                'Falta la URL del cloud. Pasa --cloud-url o define CLOUD_API_URL.'
            )
        if not self.token:
            raise CommandError(
                'Falta el token admin. Pasa --token o define CLOUD_ADMIN_TOKEN. '
                'Generalo en el cloud: python manage.py crear_tokens_api --usuario <sysadmin>'
            )

        solo = opts.get('solo')
        if solo:
            entidades = [e.strip() for e in solo.split(',') if e.strip()]
            for e in entidades:
                if e not in ENTIDADES_VALIDAS:
                    raise CommandError(
                        f'Entidad invalida: {e}. Validas: {", ".join(ENTIDADES_VALIDAS)}'
                    )
        else:
            entidades = list(ENTIDADES_VALIDAS)

        self._preflight()

        modo = 'DRY-RUN (no escribe)' if self.dry_run else 'ESCRITURA'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Reconciliacion LOCAL -> CLOUD  [{modo}]'
        ))
        self.stdout.write(f'  Cloud:   {self.cloud_url}')
        self.stdout.write(f'  Entidades: {", ".join(entidades)}')
        self.stdout.write(f'  Actualizar existentes: {"si" if self.actualizar else "no (solo crea)"}')
        self.stdout.write('')

        # El orden importa: categorias antes que productos (FK obligatoria).
        self._cat_map = {}  # nombre -> id en cloud
        if 'categorias' in entidades:
            self._reconciliar_categorias()
        if 'productos' in entidades:
            self._reconciliar_productos()
        if 'clientes' in entidades:
            self._reconciliar_clientes()

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Reconciliacion finalizada.'))
        if self.dry_run:
            self.stdout.write(
                'Fue DRY-RUN: no se escribio nada. Quita --dry-run para aplicar.'
            )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    @property
    def _headers(self):
        return {
            'Authorization': f'Token {self.token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _url(self, path):
        return f"{self.cloud_url}/{path.lstrip('/')}"

    def _retry_after(self, resp):
        """Segundos a esperar ante un 429. Usa el header Retry-After si viene;
        si no, un default conservador. Capado para no colgarse demasiado."""
        raw = resp.headers.get('Retry-After')
        segundos = None
        if raw:
            try:
                segundos = int(float(raw))
            except (TypeError, ValueError):
                segundos = None
        if segundos is None:
            segundos = 10
        return max(1, min(segundos + 1, 65))

    def _request(self, method, url, **kwargs):
        """HTTP con reintento automatico ante 429 (throttling). Convierte
        errores de red en _CloudError para que un fallo puntual no tumbe todo."""
        kwargs.setdefault('timeout', self.timeout)
        kwargs.setdefault('headers', self._headers)
        intentos = 0
        while True:
            try:
                resp = requests.request(method, url, **kwargs)
            except requests.RequestException as exc:
                raise _CloudError(f'red: {exc}')
            if resp.status_code == 429 and intentos < self.max_429_retries:
                intentos += 1
                espera = self._retry_after(resp)
                self.stdout.write(self.style.WARNING(
                    f'  throttled (429); esperando {espera}s y reintentando '
                    f'({intentos}/{self.max_429_retries})...'
                ))
                time.sleep(espera)
                continue
            return resp

    def _preflight(self):
        # 1) Warm-up del health: el Container App de staging escala a cero, asi
        #    que la 1a request puede tardar ~60-90s. Reintentamos antes de fallar.
        intentos = 6
        ultimo = None
        for i in range(1, intentos + 1):
            try:
                r = requests.get(self._url('/api/v1/health/'), timeout=self.timeout)
                if r.status_code == 200:
                    break
                ultimo = f'HTTP {r.status_code}'
            except requests.RequestException as exc:
                ultimo = str(exc)
            if i < intentos:
                self.stdout.write(self.style.WARNING(
                    f'  Cloud no responde aun ({ultimo}); despertando, '
                    f'intento {i}/{intentos}...'
                ))
                time.sleep(5)
        else:
            raise CommandError(
                f'No hay conexion al cloud ({self.cloud_url}) tras {intentos} intentos: {ultimo}'
            )

        # 2) el token autentica y puede leer maestros
        try:
            r = requests.get(
                self._url('/api/v1/maestros/categorias/'),
                params={'search': '__preflight_no_match__'},
                headers=self._headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CommandError(f'Error consultando el cloud: {exc}')
        if r.status_code in (401, 403):
            raise CommandError(
                f'El token fue rechazado (HTTP {r.status_code}). Usa un token de un '
                'usuario SYSADMIN/ADMIN del cloud (crear_tokens_api).'
            )
        if r.status_code >= 400:
            raise CommandError(f'Cloud respondio HTTP {r.status_code}: {r.text[:300]}')

    def _buscar(self, endpoint, search, match):
        """GET endpoint?search=... y devuelve el primer item para el que
        match(item) sea True, o None. `match` recibe el dict del item."""
        try:
            r = self._request('GET', self._url(endpoint), params={'search': search})
        except _CloudError as exc:
            self.stderr.write(self.style.WARNING(f'  busqueda fallo ({search}): {exc}'))
            return None
        if r.status_code >= 400:
            self.stderr.write(self.style.WARNING(
                f'  busqueda HTTP {r.status_code} ({search}): {r.text[:200]}'
            ))
            return None
        data = r.json()
        items = data['results'] if isinstance(data, dict) and 'results' in data else data
        for item in items:
            if match(item):
                return item
        return None

    def _crear(self, endpoint, body):
        if self.dry_run:
            # id centinela truthy: permite que productos resuelvan la categoria
            # (que se "crearia") sin escribir nada.
            return {'id': '(dry)'}
        r = self._request('POST', self._url(endpoint), json=body)
        if r.status_code >= 400:
            raise _CloudError(f'HTTP {r.status_code}: {r.text[:300]}')
        return r.json()

    def _actualizar_remoto(self, endpoint, item_id, body):
        if self.dry_run:
            return {'id': item_id}
        r = self._request('PATCH', self._url(f'{endpoint}{item_id}/'), json=body)
        if r.status_code >= 400:
            raise _CloudError(f'HTTP {r.status_code}: {r.text[:300]}')
        return r.json()

    # ------------------------------------------------------------------
    # Categorias
    # ------------------------------------------------------------------
    def _reconciliar_categorias(self):
        from apps.productos.models import Categoria

        endpoint = '/api/v1/maestros/categorias/'
        qs = Categoria.objects.all().order_by('nombre')
        if self.limit:
            qs = qs[:self.limit]

        creados = actualizados = existentes = errores = 0
        self.stdout.write(self.style.HTTP_INFO('== Categorias =='))

        for cat in qs:
            nombre = (cat.nombre or '').strip()
            if not nombre:
                continue
            remoto = self._buscar(
                endpoint, nombre,
                lambda it: (it.get('nombre') or '').strip().lower() == nombre.lower(),
            )
            body = {
                'nombre': nombre,
                'descripcion': cat.descripcion or '',
                'activa': cat.activa,
                'tipo_negocio': getattr(cat, 'tipo_negocio', '') or '',
                'atributos_configurados': getattr(cat, 'atributos_configurados', {}) or {},
            }
            try:
                if remoto:
                    self._cat_map[nombre] = remoto.get('id')
                    if self.actualizar:
                        self._actualizar_remoto(endpoint, remoto['id'], body)
                        actualizados += 1
                        self._line('~', nombre)
                    else:
                        existentes += 1
                else:
                    creada = self._crear(endpoint, body)
                    self._cat_map[nombre] = creada.get('id')
                    creados += 1
                    self._line('+', nombre)
            except _CloudError as exc:
                errores += 1
                self.stderr.write(self.style.ERROR(f'  ! {nombre}: {exc}'))

        self._resumen('Categorias', creados, actualizados, existentes, 0, errores)

    # ------------------------------------------------------------------
    # Productos
    # ------------------------------------------------------------------
    def _reconciliar_productos(self):
        from apps.productos.models import Producto

        endpoint = '/api/v1/maestros/productos/'
        qs = Producto.objects.select_related('categoria').all().order_by('sku')
        if self.limit:
            qs = qs[:self.limit]

        creados = actualizados = existentes = omitidos = errores = 0
        self.stdout.write(self.style.HTTP_INFO('== Productos =='))

        for prod in qs:
            sku = (prod.sku or '').strip()
            if not sku:
                omitidos += 1
                continue

            # precio_venta debe ser > 0 (lo exige el write serializer del cloud)
            precio = prod.precio_venta or Decimal('0')
            if precio <= 0:
                omitidos += 1
                self._line('o', f'{sku} (precio<=0, omitido)')
                continue

            # Resolver la categoria en el cloud (FK obligatoria)
            cat_nombre = (prod.categoria.nombre or '').strip() if prod.categoria_id else ''
            cat_id = self._cat_id_cloud(cat_nombre)
            if cat_id is None:
                omitidos += 1
                self._line('o', f'{sku} (categoria "{cat_nombre}" no esta en cloud)')
                continue

            remoto = self._buscar(
                endpoint, sku,
                lambda it: (it.get('sku') or '').strip().lower() == sku.lower(),
            )
            body = {
                'sku': sku,
                'nombre': prod.nombre or '',
                'descripcion': prod.descripcion or '',
                'precio_venta': str(precio),
                'codigo_barras': prod.codigo_barras or '',
                'categoria': cat_id,
                'activo': prod.activo,
                'estado': prod.estado or 'nuevo',
                'marca': prod.marca or '',
                'stock_minimo': prod.stock_minimo if prod.stock_minimo is not None else 5,
                'atributos': getattr(prod, 'atributos', {}) or {},
            }
            try:
                if remoto:
                    if self.actualizar:
                        # sku no se cambia en update; el cloud lo ignora igual.
                        self._actualizar_remoto(endpoint, remoto['id'], body)
                        actualizados += 1
                        self._line('~', sku)
                    else:
                        existentes += 1
                else:
                    self._crear(endpoint, body)
                    creados += 1
                    self._line('+', sku)
            except _CloudError as exc:
                errores += 1
                self.stderr.write(self.style.ERROR(f'  ! {sku}: {exc}'))

        self._resumen('Productos', creados, actualizados, existentes, omitidos, errores)

    def _cat_id_cloud(self, nombre):
        """Devuelve el id de la categoria en el cloud (mapa cacheado, o GET)."""
        if not nombre:
            return None
        if nombre in self._cat_map:
            return self._cat_map[nombre]
        remoto = self._buscar(
            '/api/v1/maestros/categorias/', nombre,
            lambda it: (it.get('nombre') or '').strip().lower() == nombre.lower(),
        )
        cid = remoto.get('id') if remoto else None
        self._cat_map[nombre] = cid
        return cid

    # ------------------------------------------------------------------
    # Clientes
    # ------------------------------------------------------------------
    def _reconciliar_clientes(self):
        from apps.clientes.models import Cliente

        endpoint = '/api/v1/maestros/clientes/'
        qs = Cliente.objects.all().order_by('nombre')
        if self.limit:
            qs = qs[:self.limit]

        creados = actualizados = existentes = omitidos = errores = 0
        self.stdout.write(self.style.HTTP_INFO('== Clientes =='))

        for cli in qs:
            # El cliente CONTADO generico es interno; el cloud lo rechaza.
            if (cli.tipo or '').upper() == 'CONTADO':
                omitidos += 1
                continue

            nombre = (cli.nombre or '').strip()
            cedula = (cli.cedula_rnc or '').strip()
            if not nombre:
                omitidos += 1
                continue

            if cedula:
                remoto = self._buscar(
                    endpoint, cedula,
                    lambda it: (it.get('cedula_rnc') or '').strip() == cedula,
                )
            else:
                remoto = self._buscar(
                    endpoint, nombre,
                    lambda it: (it.get('nombre') or '').strip().lower() == nombre.lower()
                    and (it.get('tipo') or '') == (cli.tipo or ''),
                )

            body = {
                'tipo': cli.tipo or 'PERSONAL',
                'nombre': nombre,
                'cedula_rnc': cedula or None,
                'telefono': cli.telefono or None,
                'direccion': cli.direccion or None,
                'limite_credito': str(cli.limite_credito or Decimal('0.00')),
                'plazo_credito_dias': cli.plazo_credito_dias or 30,
                'condiciones_pago': cli.condiciones_pago or None,
                'notas': cli.notas or None,
                'activo': cli.activo,
            }
            try:
                if remoto:
                    if self.actualizar:
                        self._actualizar_remoto(endpoint, remoto['id'], body)
                        actualizados += 1
                        self._line('~', nombre)
                    else:
                        existentes += 1
                else:
                    self._crear(endpoint, body)
                    creados += 1
                    self._line('+', nombre)
            except _CloudError as exc:
                errores += 1
                self.stderr.write(self.style.ERROR(f'  ! {nombre}: {exc}'))

        self._resumen('Clientes', creados, actualizados, existentes, omitidos, errores)

    # ------------------------------------------------------------------
    # Salida
    # ------------------------------------------------------------------
    def _line(self, simbolo, texto):
        if self.limit or self.dry_run:
            self.stdout.write(f'  {simbolo} {texto}')

    def _resumen(self, titulo, creados, actualizados, existentes, omitidos, errores):
        self.stdout.write(
            f'  {titulo}: creados={creados} actualizados={actualizados} '
            f'ya_existian={existentes} omitidos={omitidos} errores={errores}'
        )
        self.stdout.write('')


class _CloudError(Exception):
    """Error de escritura contra el cloud (no aborta todo el comando)."""
    pass
