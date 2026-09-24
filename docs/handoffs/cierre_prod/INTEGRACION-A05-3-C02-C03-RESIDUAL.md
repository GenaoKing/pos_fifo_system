# Integración local — A05.3 y residuales C02/C03

Estado: **PASS para integración aislada**. Fecha: **2026-09-17**. Este es un
candidato local, no una autorización para mover `develop`, publicar CT-04,
hacer push, desplegar, tocar cloud o mutar datos operativos.

## Base, candidatos y resultado

- Base: `integration/cierre-prod-A05-C03@9d33248`.
- Integrados serialmente: COM-012 (`78bec9a`/`a33f2b1`), CFG-010 (`0db8f57`),
  CFG-018/019/020 (`1a7b767`), SUS-019 (`0548384`), la reconciliación documental
  (`5e1e843`/`4d1ffc9`) y A05.3 (`7e5535d`).
- Código integrado previo conservado: Chart.js offline (`1009ba3`) y A05.2a
  corregido (`a395788`).
- El contenido integrado quedó en `b335223` antes de este handoff; la rama
  `integration/cierre-prod-A05-C03` permanece separada de `develop`.

## Revisión de integración

- COM-012 limita `standard_table()` a 5.000 filas y conserva la validación de
  forma/aviso visible sin materializar un iterador ilimitado.
- CFG-010 agrega FK nullable `sucursal` para accesos rápidos; `NULL` mantiene
  compatibilidad legacy hasta backfill explícito, y el endpoint filtra la
  sucursal actual más los legacy. Se preservó y corrigió una nota obsoleta que
  decía erróneamente que ese alcance seguía fuera de la entrega.
- CFG-018/019/020 borran el logo reemplazado, retiran dos decoradores sin
  consumidores y validan la gramática real del formato de código de barras.
- SUS-019 solo agrega regresiones del guard de degradación por plan y `activa`.
- A05.3 agrega receptor/emisor `master.mutation.v1`, CAS, RBAC vigente, ledger
  UUID concurrente, auditoría CT-01 y lease/ACK separado de `EventoSync`.

## Migraciones y aislamiento

- `configuracion.0011_accesorapidopos_sucursal` aplicada en BD aislada.
- A05.3 conserva `productos.0013` y `sync.0013`/`0014`, todas aditivas; el
  `migrate` final informó que no quedaban migraciones pendientes.
- La prueba de aislamiento multi-BD creó y destruyó el control plane y dos
  tenants efímeros bajo el namespace `a053_20260916`; no se accedió a tenants
  reales ni a BDs operativas.

## Gates ejecutados

Settings `config.settings_development`, intérprete aislado, ejecución serial:

- 111 C02/C03/SUS focales; 82 A05/productos; 122 sync/auditoría/permisos.
- 31 maestros API, 34 clientes/stub, 20 API sync y 32 ventas/FIFO.
- Paquetes completos: configuración 153, reportes 57, cotizaciones 50 y
  exportaciones CxC 3.
- Tenancy multi-BD 2/2 y e-CF por pytest separado 72/72.
- `migrate`, `makemigrations --check --dry-run`, `check`, `pip check`,
  `compileall`, `git diff --check` y `findstatic js/chart.min.js` pasaron.

Los warnings de HTTP, imágenes, PDF, conflicto/sync y FIFO son rutas negativas
afirmadas por las suites. Algunos grupos `--keepdb` imprimieron
`suscripciones.W001` después de vaciar la DB efímera con un `TransactionTestCase`;
los gates `check`, configuración y tenancy se ejecutaron después sin issues.

## Límites y siguiente gate

CT-04 sigue **sin publicar**: A05.4 es quien lo formaliza y desbloquea C04. A06,
la resolución/UI de conflictos, CFG-007 y SUS-014 siguen fuera. El rollback de
esquema es forward-safe: no revertir migraciones si hay escrituras; preservar
ledger/auditoría y corregir hacia adelante. El siguiente paso es revisión final
del candidato y, solo con autorización expresa, decidir si se mueve `develop`.
