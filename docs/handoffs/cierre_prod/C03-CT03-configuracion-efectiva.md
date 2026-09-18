# CT-03 — configuración y capacidades efectivas

Estado: **contrato local versionado y probado en el candidato; consumidores de
sync aún parciales.** Fecha: **2026-09-18**.

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

- SUS-007 sigue pendiente: el pull de configuración todavía transporta los
  flags legacy `modulo_*`; debe derivarlos del engine sin romper clientes viejos
  ni dejar fuera cambios de plan/override del cursor incremental.
- CFG-007 sigue pendiente: el receptor debe rechazar payload de configuración
  inválido sin aplicarlo.
- Este contrato no autoriza despliegue ni cambia el alcance C04/C06.

## Verificación

```powershell
apps.suscripciones.tests.test_ct03_contrato
# 5 pruebas esperadas en el gate integrado.
```
