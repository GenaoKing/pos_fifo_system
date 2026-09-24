# CT-03 — configuración y capacidades efectivas

Estado: **contrato y consumidores de sync integrados localmente.**
Actualización: **2026-09-24**. El contrato original se fijó el 2026-09-18.

Esta es la integración rebasada del material C03 `312e78c`. El texto histórico
que afirmaba que CFG-012 era solo diseño no se incorporó: en este candidato
`ConfiguracionNegocio.load()` ya es lectura pura. La regla de capacidades se
fija sin reabrir los archivos de sync que siguen reservados a Codex.

## Contrato de capacidades

`fixtures/C03-ct03_capacidades_efectivas_v1.json` y
`apps.suscripciones.tests.test_ct03_contrato` fijan `capacidades.efectivas.v1`:
plan explícito, override de negocio, cierre de dependencias, módulos core y
override negativo por sucursal. Una modificación semántica del engine exige
nueva revisión de fixture y test.

La configuración efectiva mantiene la invariante CT-03 de lectura pura:
`ConfiguracionNegocio.load()` no crea filas; el bootstrap explícito sigue siendo
la única vía que materializa la configuración inicial.

## Límites pendientes

- SUS-007/CFG-007 están integrados desde `1019500`: el pull deriva los flags
  legacy del engine, incluye cambios oficiales de plan/override en su cursor y
  rechaza payloads inválidos antes de mutar. Ver `A-CT03-sync-SUS007-CFG007.md`.
- SUS-016 está integrado desde `f83f67d`: checkpoint de solo lectura para
  detectar bootstrap o sync parcial; no ejecuta reparaciones.
- Este contrato no autoriza despliegue ni cambia el alcance C04/C06.

## Verificación

```powershell
apps.suscripciones.tests.test_ct03_contrato
# 5 pruebas esperadas en el gate integrado.
```
