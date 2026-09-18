# Conciliación de avances Claude / Codex — 2026-09-18

Estado: **revisión de ramas y documentación completada; candidato local sin
publicar.** Esta conciliación no mueve `develop`, no publica, no despliega ni
consulta o modifica datos operativos.

## Punta comparada

- `develop@fffd02bd384d8078928fa24761c550df34b711b5`: intacto.
- Base integrada previa: `integration/cierre-prod-A05-C03@609c98f`.
- Candidato consolidado: `integration/cierre-prod-A06-C04-C05@51946ef`.
  Incluye A06 (`d965fc1`), C05/CT-04 (`6380c44`), C05 p6 completo
  (`2db885e`) y el registro Codex de productores CT-01 (`51946ef`).
- Frontend C04 es otro repositorio: `pos-cloud-dashboard`,
  `claude/cierre-prod-C04@e319058`; sigue fuera del grafo Git backend por
  diseño.

Los worktrees de ambos agentes y el frontend C04 estaban limpios al revisar.

## Resultado de todas las ramas Claude

| Familia / punta revisada | Resultado frente a `51946ef` | Tratamiento |
| --- | --- | --- |
| C01 `157b7ed`, C02 `45e0f73`, C02 chart `f085d77`, C02 COM-012 `a33f2b1` | Ancestros del candidato | Ya integrados mediante la base A05/C03. |
| C03 parte2 `29bd07f`, parte2-fixes `9e3d8c7`, cfg010 `0db8f57`, menores `1a7b767`, SUS-019 `0548384`, ledger `4d1ffc9` y residual `r2` `1092756` | Ancestros del candidato | Ya integrados; la evidencia de CFG-012 está presente en las fixtures actuales. |
| C03 residual original `3d29772` | No es ancestro, pero fue reemplazado por el residual `r2` integrado como `ca68ad6` | No mezclar ni cherry-pickear la rama vieja. |
| C03 final `312e78c` | Un commit no integrado de documentación/tests CT-03, basado antes de A05 | No aporta código de producción. Requiere rebase y revisión específica antes de reutilizar su fixture/test documental. |
| C04 handoff backend `7817c0a`, `0c7a070` | Parches equivalentes ya aplicados como `de927dc`, `6ac4a3e` | Integrado documentalmente; no duplicar. |
| C04 frontend `e319058` | Correcto y limpio, pero en repositorio separado | Build, lint y 109 tests ya verdes; falta únicamente el smoke HTTP autenticado contra A06. |
| C05 base `fffd02b` y selectores CT-04 `0208a56` | Ancestros | Selectores integrados como `6380c44`. |
| C05 p6 auditoría CT-01 `bbb5246` | Ancestro | Integrado como `2db885e`; sus 12 acciones están registradas en `MATRIZ_V1` por `51946ef`. |
| SUS-014 postcondición `9c36dd9` | **No integrado** | Candidato pequeño y válido: agrega detección pura de `PLAN_DRIFT`, pero todavía no hay consumidor en `divergencias_identidad`. |
| Corrección de ledger SUS-014 `cc9a314` | **No integrada** | Es documentación de apoyo al candidato SUS-014; no debe adelantar su estado hasta integrar y cablear el código. |

## Estado Codex

Las puntas `A00`, `A01`, `A02`, `A03`, `A04`, `A05`, `A05-2a`, `A05-3`,
`A05-4`, `A06` y `tenant-migration-graph` son ancestros de `51946ef`. No se
detectó código Codex pendiente fuera del candidato consolidado.

## Único código pendiente de integración identificado

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

No está cerrada porque falta el único consumidor deliberadamente reservado a
Codex: extender `apps.tenancy.services.divergencias_identidad(...)` dentro del
mismo `tenant_context`, y agregar una prueba del comando
`verificar_identidad_tenant` que vea `PLAN_DRIFT`. Hasta entonces SUS-014 queda
**PENDIENTE**, con prevención de nuevas divergencias ya integrada y detección
lista pero sin cablear.

## Gates actuales, en orden

1. Smoke HTTP autenticado C04 ↔ A06: schema real, cursor, 403 y CAS 409.
2. Preflight read-only `OPS-PRO-007`; no corrige productos bajo categorías
   inactivas.
3. Si se asigna SUS-014, integrar su candidato y el consumidor tenancy como un
   bloque pequeño con pruebas de comando multi-DB; no promocionarlo por la sola
   prueba unitaria de Claude.
4. Con esos resultados, A07 reconcilia el inventario y evidencia vigente; no
   autoriza A08, publicación ni despliegue.
