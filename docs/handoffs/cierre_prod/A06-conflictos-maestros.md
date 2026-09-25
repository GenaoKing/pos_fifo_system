# A06 inicial — conflictos de maestros CT-04

> **Superado por** `A06-cierre-backend-pos.md` en `b25a3b7`. Esta fotografía
> conserva solamente el primer corte CT-04; no representa el candidato A06
> backend/POS final.

Estado: **EN_CURSO, CANDIDATO LOCAL SIN INTEGRAR NI PUBLICAR**. Fecha:
**2026-09-17**. Dueño: Codex.

Esta entrega inicia A06 por la frontera que desbloquea al consumidor C04: las
dos rutas CT-04 de conflictos y su resolución humana. No declara A06 completo,
no mueve `develop`, no hace push ni despliegue y no consulta/modifica datos
operativos.

## Base, aislamiento y resultado

- Base exacta: `integration/cierre-prod-A05-C03@609c98f44efd595e39bb9df128d09e57781c3dfc`.
- Worktree: `C:/Proyectos/pos_fifo_system_a06`.
- Rama: `codex/cierre-prod-A06`.
- Commit de implementación: `003cd41` (`feat(a06): resolver conflictos de maestros CT-04`).
- Venv aislado: Python 3.11.14, `requirements-dev.txt` con hashes, Django
  5.2.17.
- BD desechable: `pos_fifo_a06`. Se creó vacía, se migró desde cero y no
  comparte datos operativos ni BDs de otros worktrees.

## Alcance implementado

- `GET /api/v1/maestros/conflictos/` devuelve exactamente
  `master.conflict-list.v1` / `master.conflict.v1`, con filtros de estado,
  entidad y sucursal; cursor opaco `(creado_at, id)` y `page_size <= 100`.
- El listado se acota por `resolver_negocio(request)` y por el permiso de
  lectura de **cada fila** en su sucursal de origen: `productos.ver` o
  `categorias.ver`. No confía en IDs/textos aportados por el cliente.
- `POST /api/v1/maestros/conflictos/<mutacion_id>/resolver/` exige
  `master.conflict-resolution.v1`, `CONSERVAR_CLOUD`/`APLICAR_LOCAL`, motivo
  de 1–500 y `cloud_revision_observada`; exige `*.editar` vigente en la misma
  sucursal.
- La resolución bloquea propuesta y entidad cloud, vuelve a comparar CAS y
  devuelve `409`/actualiza el conflicto si el cloud cambió otra vez. Nunca usa
  last-write-wins. La aplicación local vuelve a pasar por los serializers del
  portal.
- `sync.0015_resolucionconflictomaestro` agrega un ledger separado
  `ResolucionConflictoMaestro`: conserva motivo, actor, revisión observada y
  resultante. El resultado negativo de `MutacionMaestro` se preserva para
  trazabilidad; la decisión resuelta se excluye solamente del listado activo.
- Cada desenlace se audita CT-01 con actor humano, sucursal, tenant y UUID de
  propuesta. La carrera de dos decisiones contra el mismo UUID deja una sola
  resolución y resultados `200`/`409`.

## Archivos

- `apps/api/views/maestros_conflictos.py` y `apps/api/urls.py`.
- `apps/sync/master_conflicts.py`, `apps/sync/models.py` y migración
  `sync.0015_resolucionconflictomaestro`.
- `apps/api/tests/test_maestros_conflictos_a06.py`.
- Mapas de `apps/api` y `apps/sync`; estado CT-04 en
  `docs/handoffs/cierre_prod/CONTRATOS.md`.

## Evidencia ejecutada

Todos los comandos corrieron serialmente en el worktree A06 con
`POS_ENV_FILE` del entorno local y `DB_NAME=pos_fifo_a06` únicamente.

```powershell
.\.venv\Scripts\python.exe manage.py migrate --settings=config.settings_development --noinput
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.settings_development
.\.venv\Scripts\python.exe manage.py test apps.api.tests.test_maestros_conflictos_a06 apps.sync.tests.test_ct04_contrato apps.api.tests.test_mutaciones_maestro_a053 apps.sync.tests.test_mutaciones_maestro_a053 apps.productos.tests.test_mutaciones_maestro_a052a apps.productos.tests.test_identidad_a05 apps.sync.tests.test_identidad_maestros_a05 apps.sync.tests.test_engine --settings=config.settings_development --noinput --verbosity 1
# 61 OK; System check: 0 issues

.\.venv\Scripts\python.exe -m pytest apps/facturacion_electronica/tests --ds=config.settings_development -q
# 72 passed
```

Verificación física posterior a `migrate` en `pos_fifo_a06`:

- `django_migrations` contiene `sync.0015_resolucionconflictomaestro`.
- Existen `sync_mutacionmaestro` y `sync_resolucionconflictomaestro`.
- `tablas_faltantes('default') == []`.

`showmigrations sync` sin contexto tenant puede mostrar falsos pendientes por
el router; no se usó como sustituto del ledger ni de las tablas físicas.

## Límites que continúan en A06

- UI POS local de conflictos y el resto de superficies PRO/CLI.
- Estado operativo efectivo, motivos de inactividad, baja/reactivación y
  filtros/paginación de maestros fuera de esta lista de conflictos.
- Prueba HTTP extremo a extremo portal C04 contra el backend integrado; C04 no
  se fusiona hasta recibir su SHA correctivo descendiente y repetir sus gates.
- Selectores C05/C06, hooks C02 y cualquier cambio de hechos financieros.

## Reversión y siguiente gate

Antes de integrar, revertir `003cd41` en esta rama elimina las rutas y el
modelo de candidato; no hay estado remoto. La migración es aditiva: una futura
publicación no autoriza rollback de esquema ni requiere una reversión física
automática.

El siguiente gate es revisión read-only del diff y del contrato por el otro
agente. Solo después de su PASS puede integrarse este candidato; luego C04 debe
probar su pantalla corregida contra estas rutas reales. No autoriza completar
el resto de A06, mover `develop`, fusionar frontend, push, despliegue ni tocar
datos operativos.
