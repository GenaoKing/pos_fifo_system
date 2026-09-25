# Grafo de migraciones cloud/tenant: `Sucursal` dual-home

Fecha: **2026-09-15**
Base: `c95b9aff86fa6ef67d2bb90fe91d5a78b1b679b3`
Rama: `codex/cierre-prod-tenant-migration-graph`
Estado: **entrega aislada; no integrada, no publicable por si sola**.

> **Corrección A09, 2026-09-24:** el procedimiento original de `--apply`
> desregistraba `sucursales.0001–0003`; en el control plane dev eso produjo
> `InconsistentMigrationHistory` porque `auditoria.0002` ya dependía de
> `sucursales.0001`. El comando vigente materializa únicamente la tabla ausente
> según el último estado histórico registrado, conserva las filas de
> `django_migrations` y aplica las migraciones pendientes. Véase el ensayo
> sobre copia restaurada en `A09-dev-preflight-2026-09-24.md`.

Esta entrega no autoriza `develop`, push, staging, produccion, CT-03, CT-04 ni
A05.2--A05.4. Corrige solo el grafo cloud que quedaba bloqueado antes de crear
un tenant.

## Diagnostico confirmado

`auditoria` ya es dual-home y `auditoria.0002_auditoria_sucursal_and_more`
declara una FK nullable a `sucursales.Sucursal`. Por tanto `sucursales` tiene
que vivir en la misma base que `auditoria`: control plane sin contexto y la BD
activa con contexto tenant.

Antes, el router excluia `sucursales` de `default`. Django registraba
`sucursales.0001_initial` como aplicada aunque no ejecutaba su `CreateModel`.
Luego `auditoria.0002` intentaba crear su FK sobre una tabla inexistente. Un
`migrate` verde no era prueba de esquema materializado.

## Cambio entregado

- `sucursales` entra a `DUAL_HOME_APPS`.
- `sucursales.0004_sucursal_dual_home_preflight` es una guardia sin escritura:
  confirma que `sucursales_sucursal` existe. No crea tablas heredadas por su
  cuenta y no corre al arrancar la aplicacion.
- `migrate_cloud` y `migrate_tenants` inspeccionan la combinacion real de
  `django_migrations` y tabla antes de migrar. Si encuentran el historial
  fantasma, terminan sin migrar y muestran el comando de reparacion exacto.
- `reparar_sucursales_dual_home` exige un solo destino (`--database` o
  `--tenant`). Sin `--apply` es dry-run por defecto y emite un `LEDGER` JSON
  con alias, filas registradas y estado. Con `--apply`, materializa la tabla
  faltante en el estado histórico registrado **sin borrar migraciones**,
  aplica lo pendiente y verifica que la tabla exista.

La app no tiene un `run_before` nuevo sobre una migracion historica de
`auditoria`: hacerlo volveria inconsistente cualquier control plane donde
`auditoria.0002` ya este aplicada. En una instalacion limpia la dependencia
historica `auditoria.0002 -> sucursales.0001` ya ordena correctamente la
creacion de la tabla, ahora que ambas bases permiten `sucursales`.

## Procedimiento para una base heredada

Esto es una operacion de cambio, no un paso de arranque ni un autorepair.
Tomar primero un backup verificado de la base objetivo y guardar la salida
completa `LEDGER` en el acta de cambio.

1. Detenerse ante el fallo de preflight de `migrate_cloud` o `migrate_tenants`.
   No editar `django_migrations` por SQL. Confirmar que se usa el reparador
   corregido en A09; el anterior falla con dependencias ya aplicadas.
2. Ejecutar y revisar el dry-run del destino exacto:

   ```powershell
   python manage.py reparar_sucursales_dual_home --database default --dry-run
   python manage.py reparar_sucursales_dual_home --tenant <tenant_key> --dry-run
   ```

   El primero corresponde solo al control plane; el segundo a una sola BD
   tenant, incluso si el tenant esta inactivo por un provisioning fallido.
   Debe mostrar `tabla_presente: false` y las filas concretas de
   `migraciones_registradas`. Si no es ese caso, detenerse y diagnosticar: el
   reparador no es un editor general de esquemas.
3. Repetir exactamente el mismo comando con `--apply`. El resultado esperado
   termina con `resultado: "REPARADA"`, `tabla_presente: true` y las cuatro
   migraciones `sucursales.0001` a `0004`.
4. Completar la cadena normal, sin saltar verificaciones:

   ```powershell
   python manage.py migrate_cloud --noinput
   python manage.py migrate_tenants --tenant <tenant_key> --incluir-inactivos --noinput
   python manage.py verificar_identidad_tenant --tenant <tenant_key>
   python manage.py with_tenant --tenant <tenant_key> verificar_sync -- --json
   ```

   `migrate_tenants` verifica `tablas_faltantes == []` al terminar. Si queda
   alguna tabla, falla y no declara el tenant migrado.

## Rollback

Antes de `--apply`, el rollback es no aplicar el cambio: conservar el dry-run,
corregir el diagnostico y salir sin escritura. En la rama aislada se puede
revertir el commit de esta entrega antes de integrarlo.

Despues de `--apply`, **no** hay rollback automatico de esquema y no se deben
eliminar `sucursales_sucursal` ni alterar `django_migrations`: cualquiera de
ambas acciones revive el estado que causo el incidente y podria borrar
sucursales creadas despues. Si el procedimiento no
puede continuar, conservar el `LEDGER`, detener la promocion y restaurar solo
desde el backup verificado tomado antes de la operacion, siguiendo el proceso
operativo aprobado. Para un fallo de migracion reintentable, la via preferida
es reparar hacia adelante con el mismo target exacto y volver a ejecutar
`migrate_cloud`/`migrate_tenants`.

## Evidencia local aislada

Con Django 5.2.17 del lockfile (`requirements-dev.txt`), en bases PostgreSQL
creadas para esta prueba y eliminadas al cerrar el trabajo:

- `migrate_cloud --noinput` desde control plane vacio: OK; aplico
  `sucursales.0001` antes de `auditoria.0002`.
- `bootstrap_tenant` real para `miggraphr1`: OK; creo y migro
  `tnt_miggraphr1`.
- `migrate_tenants --tenant miggraphr1 --noinput`: OK, 1/1; el segundo pase no
  tuvo migraciones pendientes.
- `tablas_faltantes('tnt_miggraphr1') == []`; existe `sync_eventosync`, hay una
  sucursal semilla; `verificar_identidad_tenant` y `verificar_sync --json`
  fueron verdes, y `manage.py check` no reporto incidencias.
- Se simulo en una tercera base el estado exacto `sucursales.0001` registrado
  sin tabla. Dry-run no escribio; `--apply` desregistro una sola fila,
  reaplico `0001`--`0004` y emitio `REPARADA`.
- Focales: 21 pruebas de router, verificador de tablas y reparador, OK.
  Bateria relevante: 159 pruebas de tenancy y sync, 2 skips esperados, OK.
  `makemigrations --check --dry-run`, `compileall` y `pip check`, OK.

Hallazgo no incluido: `bootstrap_tenant` acepta un `tenant_key` con `_`, pero
lo interpolan en el dominio del email de servicio y `full_clean()` lo rechaza.
La primera prueba con `miggraph_20260915_r1` llego a migrar su BD y fallo al
sembrar por ese email; se repitio con la clave valida `miggraphr1`. No se
amplio esta entrega para cambiar la politica de tenant keys.

## Integracion posterior

Cuando lleguen ambos SHA, verificar objeto, ancestro de la base acordada,
diff y limpieza de ambos worktrees. Fusionar exclusivamente esos SHAs en la
rama de integracion y ejecutar serialmente los gates focales, migracion limpia
cloud/tenant, TEN-016, discovery Django y e-CF por pytest. Solo todos verdes
permiten considerar CT-03; CT-04, A05.2--A05.4, `develop`, push y despliegues
permanecen fuera de alcance.
