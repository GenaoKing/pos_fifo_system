# Handoff C04 — Conflictos de maestros (primer consumidor de CT-04)

Estado: **Pantalla lista, maquetada contra el fixture de CT-04. Sin backend
real** — A06 no publicó `GET/POST /api/v1/maestros/conflictos/` todavía.
Fecha: **2026-09-17**. Agente: Claude.

## SHA base / resultado

- Backend consumido: CT-04 publicado por Codex en
  `codex/cierre-prod-A05-4@21f53b6` — sección CT-04 de
  `docs/handoffs/cierre_prod/CONTRATOS.md` y fixture
  `docs/handoffs/cierre_prod/fixtures/ct04_master_offline_v1.json`.
- Frontend: repo separado `pos-cloud-dashboard`, worktree
  `C:/Proyectos/pos_cloud_dashboard_cierre_claude`, rama `claude/cierre-prod-C04`,
  base `origin/develop@239da82` (sin cambios pendientes de main/staging en ese
  punto). Commit de esta entrega: **`d4b3b5d`**.
- **NO publicado, NO fusionado, NO deployado.** `develop` (ambos repos) no se
  tocó.

## Qué cubre esta entrega

Nueva pantalla `/maestros/conflictos` ("Conflictos de maestros"): lista
paginada por cursor de propuestas `CONFLICTO`/`RECHAZADA`, filtro por estado y
entidad, detalle con delta antes/después y ambas revisiones, y flujo de
resolución (Conservar cloud / Aplicar local) con motivo obligatorio (1–500) y
`cloud_revision_observada` como precondición CAS enviada automáticamente (no
editable por el usuario, para que no se pueda falsear).

Archivos nuevos:

- `src/lib/maestrosConflictos.ts` — tipos + `fetchConflictosMaestros` /
  `resolverConflictoMaestro` contra `/api/v1/maestros/conflictos/`.
- `src/hooks/useMaestrosConflictos.ts` — `useInfiniteQuery` (paginación por
  cursor, no por página: CT-04 define `next_cursor` opaco, no `count`/`page`
  como el resto del portal) + mutación de resolución.
- `src/pages/ConflictosMaestros.tsx` — pantalla completa.
- `src/lib/maestrosConflictos.test.ts` — contrato de fetch/resolve contra un
  objeto con la misma forma que `ct04_master_offline_v1.json`.

Archivos tocados (extensión mínima, retrocompatible):

- `src/components/ProtectedRoute.tsx` — nuevo prop `requiereAlguno?: string[]`
  (OR de permisos). Hace falta porque el listado mezcla `PRODUCTO` y
  `CATEGORIA`, cada uno con su propio permiso `ver`
  (`productos.ver`/`categorias.ver`); antes `ProtectedRoute` solo aceptaba un
  permiso único (`requiere`).
- `src/App.tsx` — ruta gateada con
  `requiereAlguno={['productos.ver', 'categorias.ver']}`.
- `src/components/layout/Sidebar.tsx` — mismo OR (`permisoAlguno`) para el
  ítem de navegación.
- `src/components/ProtectedRoute.test.tsx` — 2 tests nuevos para
  `requiereAlguno`.

Gating por fila: el botón "Resolver" (dentro del modal de detalle) exige el
permiso de **edición** de la entidad de esa fila (`productos.editar` /
`categorias.editar` — tabla `PERMISOS_POR_ENTIDAD` en
`lib/maestrosConflictos.ts`, hardcodeada porque es parte fija del contrato, no
algo que se descubra en runtime). Sin ese permiso, el modal muestra el detalle
en solo lectura. El propio endpoint de listado ya filtra por `ver` en el
servidor (responsabilidad de A06); el cliente no oculta filas por su cuenta.

## Por qué NO se declara "integrado"

El fixture es explícito: *"C04 puede maquetar contra este fixture, pero no
declarar integración backend ni reemplazar el fixture por un mock ad hoc"*.
Esta entrega lo respeta al pie de la letra:

- El código de producción llama al endpoint real documentado
  (`/api/v1/maestros/conflictos/`, `POST .../resolver/`); **no** hay mock,
  flag ni datos locales sustituyendo la respuesta en tiempo de ejecución.
- Hoy ese endpoint no existe (A06 no arrancó) — contra un backend real la
  pantalla mostrará el estado de error ("No se pudieron cargar los
  conflictos") hasta que A06 lo publique.
- El fixture solo se usa como **dato de test**
  (`maestrosConflictos.test.ts`), para congelar la forma que el consumidor
  espera y detectar cualquier deriva si CT-04 cambia antes de que A06
  implemente.

## Pruebas — comandos y resultado

```bash
cd pos_cloud_dashboard_cierre_claude
npm ci          # worktree nuevo, node_modules propio (no se comparte entre worktrees)
npx tsc -b       # limpio
npx eslint .     # limpio
npx vitest run   # 15 archivos, 101 tests OK (95 preexistentes + 6 nuevos)
```

## Límites y siguiente gate

- **No implementa lectura/escritura reales**: depende de que A06 publique
  `GET /api/v1/maestros/conflictos/` y `POST .../resolver/` con exactamente la
  forma de CT-04. Cuando eso pase, esta pantalla debería funcionar sin
  cambios de contrato — si no, es señal de que el backend real divergió del
  fixture congelado, y hay que repetir `maestrosConflictos.test.ts` contra la
  forma real antes de aceptar.
- **Filtro `sucursal_codigo` no expuesto en la UI todavía** (la fila ya
  muestra la sucursal de origen; se deja para cuando haya volumen real que lo
  justifique).
- Quedan sin tocar del encargo C04 completo (`docs/planes/CIERRE_PROD_CLAUDE.md`):
  pasos 2/3 (nuance "producto activo con categoría inactiva" en
  Products/Categories — investigado, ver nota abajo), paso 5 (superficies de
  admin más allá de roles/asignaciones, que ya existen: falta relevar qué le
  falta todavía al portal para no depender de `/admin/` cloud, p. ej.
  Credenciales físicas del runbook de descuentos) y paso 6 (auditoría de
  paginación/selectores >200 filas contra backend real, no solo fixtures).
- **Nota paso 2/3**: `fetchCategories()` en `src/lib/products.ts:106-112`
  fuerza `activa: true` a propósito (selector de "a qué categoría asigno este
  producto"). Para saber si la categoría de un producto puntual está inactiva
  hace falta una segunda lista de categorías sin ese filtro — no requiere
  ningún cambio de Codex/backend, es 100% resoluble en el repo frontend.

## Rollback

`git revert` de los commits de esta rama. No hay migraciones ni estado remoto
involucrado: todo el código nuevo es de solo lectura contra un endpoint que
todavía no existe.

## Deltas propuestos a documentos de seguimiento (no aplicados aquí)

- `docs/handoffs/cierre_prod/CONTRATOS.md` fila `CT-04`: agregar en la
  columna de consumidores que **C04 tiene un primer consumidor construido**
  (rama `claude/cierre-prod-C04` en el repo frontend, sin publicar), sin
  cambiar el estado de A06 (sigue `RESERVADA_A06`). No lo edito directamente
  acá porque ese archivo es propiedad de Codex.
