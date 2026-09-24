# A09 — preflight de Azure dev y reparación de sucursales

Fecha: 2026-09-24. Alcance: lectura de Azure dev, backups de `pos_fifo_dev` y
`tnt_demo`, y ensayos sobre copias PostgreSQL **locales**. No se modificó Azure,
`develop`, staging ni producción durante este preflight.

## Estado observado

- Dev API y job de migración están `Succeeded`, todavía con la imagen
  `pos-fifo-backend:45ca23afcdc5811d9e4d94556c1c05fc99949890`.
- El push a `develop` despliega dev automáticamente. La variable
  `RUN_MIGRATIONS_ON_DEPLOY=true` activa `migrate_cloud` antes de cambiar la API.
- Terraform dev: `init -reconfigure` y `validate` PASS. No se ejecutó `apply`.
- El control plane `pos_fifo_dev` tiene **1 tenant activo**, `demo`, con base
  `tnt_demo`. El servidor PostgreSQL 16 es compartido por ambientes: no usar
  un comando de servidor global para restaurar o limpiar.
- `migrate --plan` de lectura sobre el control plane, con código candidato y
  `settings_development` apuntando a la configuración de BD dev, mostró **21
  migraciones pendientes**. Log local:
  `C:\Proyectos\_lab_a09_rig_20260924\dev_migrate_plan_762193c.log`,
  SHA-256 `310D4BD2A70E1D42398BE489F3F502527CAE14B7FFD2F2122890DE5DA48A3F57`.
  Se usó `settings_development` porque el venv local no contiene las dependencias
  exclusivas de cloud; el CI Docker sí valida el entorno cloud. No se ejecutó
  ninguna migración contra Azure.

## Backup y restore aislado

`pg_dump` 16.11 produjo dos archivos custom con usuario de dev, sin alterar las
bases fuente. `pg_restore --list` pudo leer ambos catálogos. Los dumps contienen
datos del entorno y permanecen fuera de Git en
`C:\Proyectos\_lab_a09_rig_20260924\dev_backup_20260924`.

| Fuente | Bytes | SHA-256 | Objetos TOC |
| --- | ---: | --- | ---: |
| `pos_fifo_dev` | 72.143 | `53F6F6BA5128CF51EB71EE1B490A9985934D574C53C3D50E43F6A80676FA71CC` | 185 |
| `tnt_demo` | 330.890 | `15EFF68B1E89A27BD55A14A0DF87D1D30BA84B4EF61EA7F6826B72759FDF87DD` | 783 |

Restauración con `pg_restore --exit-on-error --no-owner --no-privileges` en dos
bases locales nuevas. Solo en la copia del control plane se remapearon
`tenant_key`, `db_name` y `media_prefix` a los destinos locales. Los destinos
finales del segundo ensayo son `pos_a09_dev_repair2_20260924` y
`tnt_a09_demo_repair2_20260924`; no apuntan a Azure. Las cardinalidades del
tenant antes/después coinciden para categorías, productos, clientes, ventas y
turnos de caja: `0/0/0/0/2`. Las migraciones registradas subieron de 134 a 155.

## Bloqueador detectado y corrección ensayada

En la copia fresca, `migrate_cloud --noinput` se detuvo **antes de migrar**:
`sucursales.0001–0003` figuraban aplicadas en `django_migrations`, pero no
existía `sucursales_sucursal` en el control plane. El reparador anterior quitaba
esas tres filas y luego fallaba con `InconsistentMigrationHistory` porque
`auditoria.0002` ya depende de `sucursales.0001`. Los tests previos del
reparador solo simulaban el comando con mocks y no cubrían ese historial real.

La corrección A09 materializa **únicamente** la tabla faltante con el modelo
histórico correspondiente al último registro de `sucursales`, conserva todas
las filas de migración y después ejecuta `migrate sucursales`. Acepta solo el
prefijo de migraciones conocido y falla sin escribir ante un historial distinto.
En una segunda restauración limpia, `--dry-run` enumeró exactamente tres filas;
`--apply` dejó la tabla presente y aplicó `sucursales.0004`; finalmente
`migrate_cloud --noinput` aplicó las 21 migraciones del control plane y terminó
con **1/1 tenant OK**, sin tablas faltantes. Log local:
`C:\Proyectos\_lab_a09_rig_20260924\dev_repair2_migrate.log`.

La reparación sobre el entorno dev real **sigue pendiente**. Antes del push a
`develop`, ejecutar con el código corregido el dry-run contra `pos_fifo_dev`,
comparar el ledger con esta foto y aplicar el mismo target solo si coincide;
conservar el ledger. Entonces verificar backup reciente y dejar que el workflow
ejecute `migrate_cloud`, inspeccionar su resultado por base y hacer smoke de API.
No ejecutar el reparador antiguo sobre Azure: desregistraría migraciones ya
referenciadas y bloquearía el despliegue.
