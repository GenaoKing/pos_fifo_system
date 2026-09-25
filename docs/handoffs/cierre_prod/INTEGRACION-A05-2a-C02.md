# Integracion C02 + A05.2a

Fecha: **2026-09-16**
Estado: **INTEGRADA LOCALMENTE; no publicada, sin push, despliegue ni datos operativos.**

## Historia verificada

- Base comun: `integration/cierre-prod-A05-C03@614d741693f373686dffac43e53eaae6f6c6f509`.
- C02 Chart.js offline: candidato
  `f085d77c5d7a7d66597fb456450c9504b70f4b3b`, integrado antes como
  `1009ba3420caa369326c5916319c0c9bb1bfd2e9`.
- A05.2a corregido: candidato
  `359ea128e031ac786b52a04301157fe1aaf3572a`, descendiente de la revision
  revisada `3c6c0e7`.
- Merge explicito A05.2a: `a3957885f21061ae37852dfab0e3fbbefe967b3b`, con
  padre primero `1009ba3` y padre segundo `359ea12`.
- Worktree: `C:\Proyectos\pos_fifo_system_cierre_codex`.
- `develop` no se movio; no hubo push, staging, cloud ni produccion.

## Alcance integrado

- **C02:** `reportes:on_demand` carga el Chart.js v4.4.0 ya versionado en
  `static/js/chart.min.js`; no queda CDN de Chart.js en templates.
- **A05.2a:** `MutacionMaestro` es una cola local de Producto/Categoria,
  separada de `EventoSync`. Cada escritura POS persiste maestro,
  `audit.event.v1` y cola dentro de una unica transaccion, revalida CT-02 con
  la sucursal concreta y conserva UUID idempotente.
- El UUID HTTP es obligatorio y un replay exige el mismo actor, sucursal,
  entidad y operacion; las colisiones devuelven `409`. El admin local y el
  ViewSet maestro local no pueden abrir rutas que salten esa transaccion.
- `PENDIENTE` sigue vendible. `CONFLICTO` bloquea solamente ventas nuevas y
  conserva venta, FIFO y `EventoSync` historicos.
- `sync.0012_mutacion_maestro_local` es aditiva, sin backfill y sin cambiar
  `EventoSync`, `DiferidoSync` ni datos operativos.

## Evidencia serial

Se uso `C:\Proyectos\pos_fifo_system_cierre_codex\.venv\Scripts\python.exe`,
`config.settings_development` y solo bases creadas por el runner. No se uso
Azure ni una base de cliente.

| Gate | Resultado |
| --- | --- |
| DB temporal recreada, C02 y A05.2a | **22 OK**; incluye aplicacion limpia de `sync.0012` |
| Catalogo, A05.1, identidad, miniaturas y A05.2a | **53 OK**; luego **26 OK** y **49 OK** sin depender de `SUCURSAL_CODIGO` ambiente |
| API de maestros, incluyendo el bloqueo local | **65 OK** |
| Sync, `MASTER_*`, outbox y CT-01 | **71 OK** |
| Venta/FIFO e historial ante conflicto | **32 OK** |
| C02 staticfiles | `findstatic js/chart.min.js` resuelto |
| Estaticos | `pip check`, `manage.py check`, `makemigrations --check --dry-run`, `compileall` y `git diff --check`: correctos |

El aviso `suscripciones.W001` aparece solo al reutilizar la DB temporal con
`--keepdb` sin seed de modulos; no se modifico ningun dato para silenciarlo.

## Limites y rollback

- No se inicio A05.3, A05.4, CT-04, C04 ni ninguna operacion cloud.
- A05.2a no agrega receptor, push, ACK remoto, CAS cloud ni resolucion humana.
- Para retirar A05.2a y conservar C02, usar
  `git revert -m 1 a3957885f21061ae37852dfab0e3fbbefe967b3b` en una rama
  autorizada. No usar reset ni bajar `sync.0012` sobre una instalacion con
  datos; requeriria una migracion forward que conserve trazabilidad.
