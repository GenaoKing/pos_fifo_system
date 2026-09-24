# Conciliación de avances Claude / Codex — 2026-09-18

Estado: **revisión de ramas y documentación completada; candidato local sin
publicar.** Esta conciliación no mueve `develop`, no publica, no despliega ni
consulta o modifica datos operativos.

## Punta comparada

- `develop@fffd02bd384d8078928fa24761c550df34b711b5`: intacto.
- Base integrada previa: `integration/cierre-prod-A05-C03@609c98f`.
- Candidato consolidado: `integration/cierre-prod-A06-C04-C05@dfb1dfc`.
  Incluye A06 (`d965fc1`), C05/CT-04 (`6380c44`), C05 p6 completo
  (`2db885e`), el registro Codex de productores CT-01 (`51946ef`), SUS-014
  cableado (`3a16e38`, `dfb1dfc`) y CT-03 fixture/test rebasados (`dfb1dfc`).
- Frontend C04 es otro repositorio: `pos-cloud-dashboard`,
  `claude/cierre-prod-C04@e319058`; sigue fuera del grafo Git backend por
  diseño.

Los worktrees de ambos agentes y el frontend C04 estaban limpios al revisar.

## Resultado de todas las ramas Claude

| Familia / punta revisada | Resultado frente a `dfb1dfc` | Tratamiento |
| --- | --- | --- |
| C01 `157b7ed`, C02 `45e0f73`, C02 chart `f085d77`, C02 COM-012 `a33f2b1` | Ancestros del candidato | Ya integrados mediante la base A05/C03. |
| C03 parte2 `29bd07f`, parte2-fixes `9e3d8c7`, cfg010 `0db8f57`, menores `1a7b767`, SUS-019 `0548384`, ledger `4d1ffc9` y residual `r2` `1092756` | Ancestros del candidato | Ya integrados; la evidencia de CFG-012 está presente en las fixtures actuales. |
| C03 residual original `3d29772` | No es ancestro, pero fue reemplazado por el residual `r2` integrado como `ca68ad6` | No mezclar ni cherry-pickear la rama vieja. |
| C03 final `312e78c` | La documentación larga estaba basada antes de A05, pero su fixture/test CT-03 era reutilizable | Se rebasó de forma acotada como fixture canónico y 5 pruebas en `dfb1dfc`; no se incorporaron sus afirmaciones históricas obsoletas. |
| C04 handoff backend `7817c0a`, `0c7a070` | Parches equivalentes ya aplicados como `de927dc`, `6ac4a3e` | Integrado documentalmente; no duplicar. |
| C04 frontend `e319058` | Correcto y limpio, pero en repositorio separado | Build, lint y 109 tests verdes; el smoke HTTP autenticado C04↔A06 reportó 23/23. Pendiente UI a >200 filas. |
| C05 base `fffd02b` y selectores CT-04 `0208a56` | Ancestros | Selectores integrados como `6380c44`. |
| C05 p6 auditoría CT-01 `bbb5246` | Ancestro | Integrado como `2db885e`; sus 12 acciones están registradas en `MATRIZ_V1` por `51946ef`. |
| SUS-014 postcondición `9c36dd9` | Integrado por cherry-pick como `3a16e38` | `divergencias_plan_operativo()` quedó conectado a `divergencias_identidad()` en `dfb1dfc`, sin escrituras ni reconciliación automática. |
| Corrección de ledger SUS-014 `cc9a314` | Contenido documental incorporado/adaptado | El handoff SUS-014 y los mapas de apps reflejan el estado integrado; no se cherry-pickeó prosa que afirmara una pendiente ya cerrada. |

## Estado Codex

Las puntas `A00`, `A01`, `A02`, `A03`, `A04`, `A05`, `A05-2a`, `A05-3`,
`A05-4`, `A06` y `tenant-migration-graph` son ancestros de `dfb1dfc`. No se
detectó código Codex pendiente fuera del candidato consolidado.

## Integraciones concluidas tras la revisión

`claude/cierre-prod-SUS014-postcondicion@9c36dd9` agrega
`divergencias_plan_operativo(tenant_plan_slug, negocio)`: una comprobación de
solo lectura que reporta `PLAN_DRIFT` si el `Tenant.plan_slug` del control plane
no coincide con la suscripción operativa tenant. No lleva migración ni efecto
por sí sola.

La verificación independiente en el worktree de Claude, con base de prueba
desechable, ejecutó 17 pruebas y terminó OK:

```powershell
apps.suscripciones.tests.test_sus014_divergencias_plan
apps.suscripciones.tests.test_sus014_validar_plan_slug
apps.tenancy.tests.test_bootstrap_tenant_plan_slug
# Ran 17 tests in 0.477s — OK; la base de prueba fue destruida.
```

Codex aplicó el candidato como `3a16e38`, extendió
`apps.tenancy.services.divergencias_identidad(...)` dentro del mismo
`tenant_context` y agregó la prueba que verifica `PLAN_DRIFT` sin alterar la
suscripción. La matriz focal posterior dio 43 pruebas OK y la suite completa de
`apps.suscripciones`, 89 OK (2 skips esperados). SUS-014 queda
**ACREDITADO_LOCAL**, sin promoción ni corrección automática de datos.

## Gates actuales, en orden

1. C04 p6: UI real de conflictos a más de 200 filas contra A06 vivo.
2. Bloque Codex CT-03: SUS-007 + CFG-007 en sync/API, sin inventar un cambio
   de cursor o schema si el análisis exige uno nuevo.
3. Preflight read-only `OPS-PRO-007`; no corrige productos bajo categorías
   inactivas.
4. Con los dos bloques y sus pruebas cruzadas, A07 reconcilia el inventario y evidencia vigente; no
   autoriza A08, publicación ni despliegue.
