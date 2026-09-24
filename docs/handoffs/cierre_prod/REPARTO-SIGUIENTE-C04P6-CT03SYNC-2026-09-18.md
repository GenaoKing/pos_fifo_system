# Reparto siguiente — C04 p6 y consumidores sync CT-03

Estado: **dos bloques independientes, listos para arrancar en paralelo; sin
publicar ni desplegar.** Fecha: **2026-09-18**.

## Punto de partida comprobado

- Backend funcional: `integration/cierre-prod-A06-C04-C05@dfb1dfc`; `develop`
  permanece `fffd02b` y no se mueve.
- Frontend: `pos-cloud-dashboard`, `claude/cierre-prod-C04@e319058`, separado
  por diseño del grafo backend.
- Ya acreditado: matriz backend focal 363 OK, frontend build/lint/109 tests y
  smoke HTTP C04↔A06 reportado 23/23 con BD desechable. Este último cubrió
  sesión + CSRF, no el minteo JWT tenant-aware.
- No iniciar el preflight `OPS-PRO-007` contra datos reales sin autorización
  explícita; no es sustituto una BD de humo.

## Claude — C04 p6: UI de conflictos a volumen

**Por qué ahora:** el contrato HTTP real ya está cerrado. El hueco restante de
CT-04/C04 es que la pantalla complete el recorrido real con más de 200 filas,
no que vuelva a probar el GET/POST unitariamente.

**Base/worktree:** crear `claude/cierre-prod-C04-p6-volumen` desde
`claude/cierre-prod-C04@e319058` en un worktree frontend nuevo y limpio. No
modificar el repo backend ni sus ramas, salvo ejecutar un servidor desechable
contra el SHA backend indicado.

**Alcance:**

1. Levantar A06 desde `dfb1dfc` con venv, puerto y BD propios; sembrar al menos
   201 conflictos del mismo tenant y una fila ajena para verificar aislamiento.
2. Recorrer la pantalla real de C04 contra ese backend, no solo MSW: cursor
   siguiente/anterior si la UI lo expone, conteos/filas, cambio de selector,
   estado vacío, error HTTP y permiso de solo lectura (sin ofrecer o sin poder
   completar resolución).
3. Resolver una fila autorizada y comprobar que se actualiza el listado sin
   duplicar, saltar ni conservar una selección obsoleta. Cubrir schema
   desconocido como error visible/fail-closed si llega a la UI.
4. Mantener `master.conflict-list.v1`, `master.conflict.v1` y
   `master.conflict-resolution.v1` tal cual; no inventar paginación por offset,
   permisos frontend paralelos ni rutas backend.

**Aceptación:** pruebas frontend nuevas pertinentes, `npm run build`,
`npm run lint`, `npm run test:run`, y evidencia separada del recorrido vivo.
Registrar SHA frontend, SHA backend, BD eliminada/servidor detenido y la
limitación JWT si no se configura ese transporte. No fusionar `main` ni
publicar.

**Después, no en paralelo:** C04 p5 (superficies admin de roles, configuración
y operaciones sin `/admin/` cloud) empieza solo después de aceptar p6, para no
mezclar dos superficies grandes del mismo frontend.

### Mensaje listo para Claude

> Lee AGENTS.md, CLAUDE.md, docs/PROJECT_STATUS.md, docs/PLAN_CIERRE_PROD.md,
> docs/planes/CIERRE_PROD_CLAUDE.md y este reparto. Trabaja en un worktree nuevo
> `claude/cierre-prod-C04-p6-volumen` desde `claude/cierre-prod-C04@e319058`.
> Ejecuta C04 p6: UI real de conflictos contra A06 `dfb1dfc` con más de 200
> filas y BD/puerto propios. No edites backend ni contratos, no uses solo mocks,
> no publiques ni despliegues. Entrega commit frontend, handoff con SHAs y
> evidencia de build/lint/tests más el recorrido vivo; deja explícito si JWT no
> se ejercita.

## Codex — CT-03: SUS-007 + CFG-007 en sync/API

**Por qué ahora:** CT-03 tiene fixture y prueba canónicos, y ambos pendientes
son de la propiedad backend/sync. Es un bloque acotado que mantiene ocupado a
Codex mientras Claude ejecuta el recorrido UI, sin tocar el mismo repositorio
ni los mismos archivos.

**Base/worktree:** partir de `integration/cierre-prod-A06-C04-C05@dfb1dfc` en
`codex/cierre-prod-CT03-sync`; venv, aliases tenant y BD de prueba exclusivos.

**Alcance:**

1. Localizar el pull que aún expone `ConfiguracionNegocio.modulo_*` y derivar
   los flags legacy desde el engine de capacidades efectivas (SUS-007),
   conservando forma y compatibilidad de payload para POS instalado.
2. Demostrar que cambios de plan/override se hacen visibles al cliente del pull
   incremental. Si esto exige cambiar cursor, evento o schema versionado, no
   inventarlo dentro del fix: documentar la alternativa aditiva y devolver el
   contrato para decisión antes de implementarla.
3. Aplicar CFG-007: validar configuración entrante antes de mutar; un payload
   inválido falla cerrado, no deja estado parcial y conserva la compatibilidad
   explícita de payloads anteriores que ya soporta el receptor.
4. Limitar los cambios a `apps/sync/**`, rutas sync de `apps/api/**`, pruebas
   propias y los contratos/handoff de Codex. No modificar engine/modelos de
   suscripciones ni configuración de Claude sin una solicitud puntual.

**Aceptación:** pruebas negativas/compatibilidad, cursor incremental y cambio
plan/override; `manage.py check`, `makemigrations --check --dry-run`, suite
sync/API focal y `git diff --check`. Informar si el análisis encuentra un
contrato nuevo requerido en vez de convertir una decisión de protocolo en una
suposición. Sin migraciones reales, push ni despliegue.

### Mensaje listo para Codex

> Lee AGENTS.md, CLAUDE.md, docs/PROJECT_STATUS.md, docs/PLAN_CIERRE_PROD.md,
> CONTRATOS.md y este reparto. Crea `codex/cierre-prod-CT03-sync` desde
> `integration/cierre-prod-A06-C04-C05@dfb1dfc`, con venv/BD/tenants aislados.
> Implementa solamente SUS-007 y CFG-007 en sync/API: flags legacy derivados
> del engine, visibilidad incremental de plan/override y rechazo fail-closed de
> configuración inválida. Conserva compatibilidad; si cursor/evento/schema
> exige contrato nuevo, detente y entrega la propuesta en lugar de inventarlo.
> No modifiques apps de Claude ni publiques/despliegues. Entrega commit, tests,
> handoff y deltas documentales.

## Reencuentro y ruta posterior

Cuando ambos bloques estén entregados, integrar/revisar sin mover `develop` y
repetir los gates cruzados. Solo entonces corresponde A07: reconciliar cada fila
del inventario, separar código local de preflight operativo y decidir los
restantes productores CT-01 fuera de p6. A08/C06 y cualquier preflight sobre
datos reales permanecen fuera de esta asignación.
