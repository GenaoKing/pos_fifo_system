"""Reconciliacion y reparacion dirigida de ACK falsos historicos (BUG-K)."""
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.sync import registry


PLAN_SCHEMA = 'sync.bug_k.repair_plan.v1'
CLASIFICACIONES_REENCOLABLES = {
    'FALSO_ACK',
    'HECHO_SIN_EVENTO_CLOUD',
}


def _digest_plan(scope, acciones):
    material = json.dumps(
        {'scope': scope, 'acciones': acciones},
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return hashlib.sha256(material.encode('utf-8')).hexdigest()


class Command(BaseCommand):
    help = (
        'Cruza EventoSync CONFIRMADO con el cloud y produce un plan exacto. '
        'Dry-run por defecto; ejecutar exige archivo y digest aprobados.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=90)
        parser.add_argument('--json', action='store_true')
        parser.add_argument('--guardar-plan', type=str)
        parser.add_argument('--plan-aprobado', type=str)
        parser.add_argument('--confirmar-plan', type=str)
        parser.add_argument('--ejecutar', action='store_true')

    def handle(self, *args, **opts):
        if opts['ejecutar']:
            return self._aplicar_plan(opts)
        if opts['plan_aprobado'] or opts['confirmar_plan']:
            raise CommandError(
                '--plan-aprobado/--confirmar-plan solo se usan con --ejecutar.'
            )

        plan = self._construir_plan(opts['dias'])
        contenido = json.dumps(plan, indent=2, default=str, ensure_ascii=False)
        if opts['guardar_plan']:
            ruta = Path(opts['guardar_plan']).expanduser().resolve()
            if ruta.exists():
                raise CommandError(
                    f'El archivo ya existe y no se sobrescribe: {ruta}'
                )
            ruta.write_text(contenido + '\n', encoding='utf-8')
            self.stderr.write(f'Plan dry-run guardado en {ruta}')

        if opts['json']:
            self.stdout.write(contenido)
        else:
            self._imprimir_resumen(plan)

    @staticmethod
    def _sucursal_actual():
        from apps.sucursales.models import get_sucursal_actual

        sucursal = get_sucursal_actual()
        if sucursal is None:
            raise CommandError(
                'SUCURSAL_CODIGO no resuelve una sucursal local; se rehusa '
                'reconciliar sin ambito exacto.'
            )
        return sucursal

    @staticmethod
    def _sonda(evento):
        return {
            'event_id': str(evento.event_id),
            'tipo_evento': evento.tipo_evento,
            'payload': evento.payload,
            'hash_payload': evento.hash_payload,
            'objeto_referencia': evento.objeto_referencia,
        }

    def _consultar_cloud(self, eventos, sucursal):
        from apps.sync.engine import SyncEngine

        resultados = []
        scope = None
        engine = SyncEngine()
        for inicio in range(0, len(eventos), 100):
            data = engine.consultar_reconciliacion_eventos([
                self._sonda(evento)
                for evento in eventos[inicio:inicio + 100]
            ])
            recibido = data.get('scope') or {}
            if recibido.get('branch_code') != sucursal.codigo:
                raise CommandError(
                    'El cloud respondio para otra sucursal; no se genera plan.'
                )
            if scope is None:
                scope = recibido
            elif scope != recibido:
                raise CommandError('El scope cloud cambio entre paginas de la sonda.')
            resultados.extend(data['resultados'])
        return scope or {
            'tenant_key': '',
            'branch_code': sucursal.codigo,
            'branch_ref': str(sucursal.pk),
        }, resultados

    @staticmethod
    def _faltantes_locales(desde, sucursal):
        """Hechos primarios sin EventoSync: categoria distinta de falso ACK."""
        from apps.sync.models import EventoSync

        faltantes = []
        for clave, hecho in registry.hechos_backfilleables().items():
            modelo = hecho.modelo()
            if modelo is None:
                continue
            qs = hecho.queryset().filter(**{f'{hecho.campo_fecha}__gte': desde})
            campos = {field.name for field in modelo._meta.get_fields()}
            if 'sucursal' in campos:
                qs = qs.filter(sucursal=sucursal)
            elif hecho.clave in ('aperturas_caja', 'cierres_caja'):
                qs = qs.filter(caja__sucursal=sucursal)
            elif hecho.clave == 'movimientos_caja':
                qs = qs.filter(turno__caja__sucursal=sucursal)
            elif hecho.clave == 'ajustes_inventario':
                qs = qs.filter(lote__sucursal=sucursal)
            con_evento = EventoSync.objects.filter(
                sucursal=sucursal,
                tipo_evento=hecho.tipo_evento,
                objeto_id_local__isnull=False,
            ).values_list('objeto_id_local', flat=True)
            for obj in qs.exclude(pk__in=con_evento).order_by('pk'):
                referencia = (
                    str(getattr(obj, hecho.campo_ref))
                    if hecho.campo_ref and getattr(obj, hecho.campo_ref, None)
                    else f'pk={obj.pk}'
                )
                faltantes.append({
                    'clasificacion': 'FALTANTE_EVENTO_LOCAL',
                    'hecho': clave,
                    'tipo_evento': hecho.tipo_evento,
                    'objeto_id_local': obj.pk,
                    'objeto_referencia': referencia,
                    'accion': 'REVISAR_BACKFILL_DIRIGIDO',
                })
        return faltantes

    def _construir_plan(self, dias):
        from apps.sync.models import EventoSync

        if dias <= 0:
            raise CommandError('--dias debe ser positivo.')
        sucursal = self._sucursal_actual()
        desde = timezone.now() - timedelta(days=dias)
        eventos = list(
            EventoSync.objects.filter(
                sucursal=sucursal,
                estado='CONFIRMADO',
                created_at__gte=desde,
                payload__isnull=False,
            ).order_by('created_at', 'id')
        )
        scope, resultados = self._consultar_cloud(eventos, sucursal)
        por_identidad = {
            (item.get('event_id'), item.get('hash')): item
            for item in resultados
        }

        clasificados = []
        acciones = []
        for evento in eventos:
            item = por_identidad.get((str(evento.event_id), evento.hash_payload))
            if item is None:
                raise CommandError(
                    f'El cloud omitio event_id={evento.event_id}; plan incompleto.'
                )
            fila = {
                **item,
                'local_pk': evento.pk,
                'estado_local': evento.estado,
            }
            clasificados.append(fila)
            if item['clasificacion'] in CLASIFICACIONES_REENCOLABLES:
                acciones.append({
                    'accion': 'REENCOLAR_CONFIRMADO',
                    'event_id': str(evento.event_id),
                    'hash_payload': evento.hash_payload,
                    'tipo_evento': evento.tipo_evento,
                    'objeto_referencia': evento.objeto_referencia,
                    'clasificacion': item['clasificacion'],
                    'estado_esperado': 'CONFIRMADO',
                })

        acciones.sort(key=lambda item: (item['tipo_evento'], item['event_id']))
        plan_id = _digest_plan(scope, acciones)
        faltantes = self._faltantes_locales(desde, sucursal)
        resumen = {}
        for item in clasificados + faltantes:
            clave = item['clasificacion']
            resumen[clave] = resumen.get(clave, 0) + 1

        return {
            'schema_version': PLAN_SCHEMA,
            'plan_id': plan_id,
            'generado_at': timezone.now().isoformat(),
            'dry_run': True,
            'ventana_dias': dias,
            'scope': scope,
            'resumen': dict(sorted(resumen.items())),
            'eventos_confirmados': clasificados,
            'faltantes_evento_local': faltantes,
            'acciones': acciones,
            'advertencia': (
                'Solo REENCOLAR_CONFIRMADO es ejecutable. Divergencias y '
                'faltantes locales requieren revision separada.'
            ),
        }

    def _cargar_plan(self, opts):
        if not opts['plan_aprobado'] or not opts['confirmar_plan']:
            raise CommandError(
                '--ejecutar exige --plan-aprobado RUTA y --confirmar-plan DIGEST.'
            )
        ruta = Path(opts['plan_aprobado']).expanduser().resolve()
        try:
            plan = json.loads(ruta.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise CommandError(f'No se pudo leer el plan aprobado: {exc}') from exc
        if plan.get('schema_version') != PLAN_SCHEMA:
            raise CommandError('Schema de plan no soportado.')
        esperado = _digest_plan(plan.get('scope'), plan.get('acciones'))
        if plan.get('plan_id') != esperado or opts['confirmar_plan'] != esperado:
            raise CommandError('El digest del plan no coincide con la confirmacion.')
        return plan

    def _aplicar_plan(self, opts):
        from apps.sync.models import EventoSync, LogSync

        plan = self._cargar_plan(opts)
        sucursal = self._sucursal_actual()
        if plan['scope'].get('branch_code') != sucursal.codigo:
            raise CommandError('El plan aprobado pertenece a otra sucursal.')
        if LogSync.objects.filter(
            tipo='REPARACION', detalle__plan_id=plan['plan_id'],
        ).exists():
            raise CommandError('Este plan ya fue aplicado; no se repite.')

        acciones = plan.get('acciones') or []
        if any(
            accion.get('accion') != 'REENCOLAR_CONFIRMADO'
            or accion.get('clasificacion') not in CLASIFICACIONES_REENCOLABLES
            for accion in acciones
        ):
            raise CommandError('El plan contiene una accion no ejecutable.')

        event_ids = [accion['event_id'] for accion in acciones]
        actuales = list(EventoSync.objects.filter(
            sucursal=sucursal, event_id__in=event_ids,
        ))
        if len(actuales) != len(event_ids):
            raise CommandError('La lista local ya no coincide con el plan.')

        # Revalida el cloud inmediatamente antes de escribir: un plan viejo no
        # puede reencolar algo que ya aparecio por otra via.
        _, resultados = self._consultar_cloud(actuales, sucursal)
        clases = {
            (item.get('event_id'), item.get('hash')): item.get('clasificacion')
            for item in resultados
        }
        for evento in actuales:
            if clases.get((str(evento.event_id), evento.hash_payload)) not in (
                CLASIFICACIONES_REENCOLABLES
            ):
                raise CommandError(
                    f'event_id={evento.event_id} cambio en cloud; regenerar plan.'
                )

        esperadas = {accion['event_id']: accion for accion in acciones}
        with transaction.atomic():
            bloqueados = list(
                EventoSync.objects.select_for_update().filter(
                    sucursal=sucursal, event_id__in=event_ids,
                )
            )
            for evento in bloqueados:
                accion = esperadas[str(evento.event_id)]
                if (
                    evento.estado != accion['estado_esperado']
                    or evento.hash_payload != accion['hash_payload']
                    or evento.tipo_evento != accion['tipo_evento']
                ):
                    raise CommandError(
                        f'Precondicion local cambio para event_id={evento.event_id}.'
                    )
                evento.reactivar()

            LogSync.objects.create(
                tipo='REPARACION',
                resultado='EXITOSO',
                sucursal=sucursal,
                eventos_procesados=len(bloqueados),
                mensaje=f'Plan BUG-K {plan["plan_id"]} aplicado.',
                detalle={
                    'plan_id': plan['plan_id'],
                    'eventos': [
                        {
                            'event_id': str(evento.event_id),
                            'hash_payload': evento.hash_payload,
                            'antes': 'CONFIRMADO',
                            'despues': evento.estado,
                        }
                        for evento in bloqueados
                    ],
                },
            )

        self.stdout.write(self.style.SUCCESS(
            f'Plan {plan["plan_id"]} aplicado: {len(bloqueados)} evento(s) reencolado(s).'
        ))

    def _imprimir_resumen(self, plan):
        self.stdout.write('BUG-K - RECONCILIACION DRY-RUN')
        self.stdout.write(f'Plan: {plan["plan_id"]}')
        self.stdout.write(
            f'Ambito: tenant={plan["scope"].get("tenant_key") or "(local)"} '
            f'sucursal={plan["scope"].get("branch_code")}'
        )
        for clasificacion, total in plan['resumen'].items():
            self.stdout.write(f'  {clasificacion}: {total}')
        self.stdout.write(f'Acciones dirigidas propuestas: {len(plan["acciones"])}')
        self.stdout.write(self.style.WARNING(
            'Dry-run: no se cambio ningun EventoSync. Revise y guarde el JSON '
            'antes de cualquier ejecucion.'
        ))
