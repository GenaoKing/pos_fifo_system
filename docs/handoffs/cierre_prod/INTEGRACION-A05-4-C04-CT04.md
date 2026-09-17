# Integración A05.4 + C04 — CT-04 maestros offline

Estado: **PARCIAL, SIN PUBLICACIÓN**. Fecha: **2026-09-17**.

Esta integración incorpora el productor y contrato CT-04 al candidato backend
y registra el primer consumidor C04. No integra el código frontend, que requiere
una corrección descendiente antes de ser aceptado. No mueve `develop`, hace
push, despliega, ni consulta/modifica datos operativos.

## SHAs y topología

- Base: `integration/cierre-prod-A05-C03@695b36cda73f67c5d4c448fc65fcb9c6762ae1d4`.
- A05.4 revisado: `codex/cierre-prod-A05-4@21f53b69a55b6f34b31b5a7f42e1ee926b851bf7`.
- Merge A05.4: `312a12f` (`merge(a05): integrar publicacion CT-04`).
- Handoff C04: `claude/cierre-prod-C04-conflictos@58d2ab0cf16f5cb78c07553099d9be69440bdca2`.
- Merge de ese handoff: `cc77452` (`merge(c04): integrar handoff de consumidor CT-04`).
- Frontend candidato, no fusionado: repo `pos-cloud-dashboard`,
  `claude/cierre-prod-C04@d4b3b5d`, basado en `origin/develop@239da82`.
- `develop` backend permanece en `fffd02b`.

## Gates realizados

En el worktree aislado del candidato backend, con Python 3.11.14 y settings de
desarrollo, se ejecutó serialmente:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.sync.tests.test_ct04_contrato apps.api.tests.test_mutaciones_maestro_a053 apps.sync.tests.test_mutaciones_maestro_a053 apps.productos.tests.test_mutaciones_maestro_a052a apps.productos.tests.test_identidad_a05 apps.sync.tests.test_identidad_maestros_a05 apps.sync.tests.test_engine --settings=config.settings_development --noinput --verbosity 1
# 54 OK; System check: 0 issues
```

El runner reutilizó y destruyó únicamente `test_pos_cierre_codex`. Las warnings
de rutas negativas, conflictos de adopción e imagen `DummyResponse` pertenecen
a casos afirmados por esta suite; no hubo datos operativos involucrados.

El candidato C04 pasó `npm run build`, `npm run lint` y `npm run test -- --run`
(101/101), pero esos gates no prueban conformidad completa de CT-04.

## Bloqueo de integración frontend

`src/lib/maestrosConflictos.ts` delega el payload de resolución sin incluir
`schema_version: "master.conflict-resolution.v1"`, aunque el fixture CT-04 lo
define como obligatorio. También tipa las respuestas de listado/ítem, pero no
las valida en runtime: un schema desconocido sería aceptado por TypeScript como
datos de Axios. Esto contradice la regla de compatibilidad de CT-04.

El correctivo de Claude debe:

1. Enviar siempre `schema_version: "master.conflict-resolution.v1"` al resolver.
2. Validar el envelope `master.conflict-list.v1` y cada ítem
   `master.conflict.v1` antes de renderizar; rechazar las versiones desconocidas.
3. Añadir pruebas negativas para ambas validaciones y actualizar la expectativa
   del POST.

Hasta recibir un SHA descendiente con esos gates, el frontend no se fusiona ni
se declara integrado.

## Decisión de secuencia

No se deben implementar GET de listado ni POST de resolución dentro de A05.4:
el fixture y el contrato los reservan explícitamente para A06. El siguiente
bloque backend es A06, desde `cc77452`, en un worktree/BD aislados. Puede avanzar
en paralelo con el correctivo C04 siempre que produzca exactamente CT-04; la
aceptación final exige repetir las pruebas del consumidor contra el backend
real.

## Reversión

Antes de cualquier publicación, revertir los merges `cc77452` y `312a12f` con
`git revert -m 1` en orden inverso dentro de esta rama de integración. No hay
migración ni estado remoto asociado a esta integración.
