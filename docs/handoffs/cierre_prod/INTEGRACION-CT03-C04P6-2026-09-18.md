# Integracion local — CT-03 sync y C04 p6

Fecha: **2026-09-18**. Estado: **integrado, validado y aceptado localmente por
el responsable.** No hubo push, despliegue, cambio de `develop` ni acceso a
datos operativos.

## Puntas integradas

| Superficie | Base / fuente | Resultado local |
| --- | --- | --- |
| Backend | `integration/cierre-prod-A06-C04-C05@f1d4337` + `codex/cierre-prod-CT03-sync@9c6a698` | `integration/cierre-prod-A06-C04-C05@6c74d163b41d32a3e3a94ec15c19b90260d6bcde` |
| Frontend C04 | `claude/cierre-prod-C04@e319058` + `claude/cierre-prod-C04-p6-volumen@a241b7c` | `claude/cierre-prod-C04@f0e6c2d0ad579b43ec5d445a3ff4f1e43da6ebd1` |

El merge backend incorpora SUS-007 y CFG-007: flags legacy `modulo_*`
derivados de capacidades efectivas, revisión incremental de la configuración y
rechazo fail-closed de payload inválido sin diferido ni avance de cursor. El
merge frontend incorpora exclusivamente la cobertura a volumen y el handoff de
C04 p6; no altera el contrato CT-04 ni el backend.

## Aceptacion local repetida

En el worktree backend, usando Django 5.2.17 y una base de prueba aislada
`test_pos_fifo_a06_c04_c05_ct03` creada y destruida por el runner:

```powershell
$env:DB_NAME = 'pos_fifo_a06_c04_c05_ct03'
$env:TENANT_TEST_DB_NAMESPACE = 'a06_c04_c05_ct03_20260918'
& 'C:\Proyectos\pos_fifo_system_a06\.venv\Scripts\python.exe' manage.py test <matriz A06/C05 + CT-03> --settings=config.settings_development --noinput --verbosity 1
# 398 tests, 200.501 s — OK
```

Tambien pasaron `manage.py check`, `makemigrations --check --dry-run` (sin
cambios; el aviso esperado fue que la BD base aislada no existe) y `git diff
--check`.

En `pos_cloud_dashboard_cierre_claude@f0e6c2d` pasaron `npm run build`, `npm
run lint` y `npm run test:run`: **17 archivos, 120 tests OK**. El handoff p6
conserva su ejercicio contra A06 en una BD desechable: 201 conflictos por cursor
en cinco páginas, aislamiento de tenant y resolución CAS, **24/24 PASS**.

No se repitió ese servidor/seed HTTP: los scripts reproducibles quedaron en el
scratchpad de la sesión p6 y no fueron versionados. Esta nota no los sustituye
ni afirma una nueva ejecución de navegador.

## Límites y siguiente orden

- No hay click-through de navegador: el frontend no tiene Playwright/Cypress.
  La cobertura p6 usa la pantalla real en jsdom con `QueryClient` real, más el
  contrato HTTP vivo por separado. Un E2E Playwright mínimo es trabajo futuro
  separado, no parte de esta integración.
- El smoke p6 no ejerció JWT tenant-aware; usó sesión Django + CSRF en una BD
  single-DB desechable. Esa cobertura sigue perteneciendo a tenancy/auth.
- CFG-007 bloquea una configuración fiscal inválida; edición/borrado directo
  de `SucursalModuloOverride` por Admin/ORM aún no tiene revisión durable.
- Antes de cualquier entorno real sigue siendo obligatorio `OPS-PRO-007`
  read-only y autorización explícita. A08/C06 y el preflight real no entran en
  este bloque.

Siguiente: ambas integraciones fueron aceptadas el 2026-09-18. A07 ya inició la
reconciliación backend; C04 p5 puede abrirse en un worktree frontend nuevo. Los
límites operativos de esta nota siguen intactos.
