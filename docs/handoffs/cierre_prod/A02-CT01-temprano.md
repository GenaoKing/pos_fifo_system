# Handoff temprano A02 — CT-01 implementado

Fecha: 2026-09-10  
Propietario: **A / Codex**  
Consumidores: **C01–C05 / Claude y dominios A**  
Interfaz: `audit.event.v1`  
Commit de implementación: `cd8a3b4`

## Qué puede consumir Claude

```python
from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion

with transaction.atomic(using=alias):
    # mutación de dominio en `alias`
    registrar_mutacion(
        accion='configuracion.sucursal.actualizada',
        actor=request.user,
        entidad=configuracion,
        antes=antes,
        despues=despues,
        resultado=Auditoria.Resultado.SUCCEEDED,
        canal=Auditoria.Canal.PORTAL_API,
        tenant=tenant_key,
        sucursal=sucursal,
        correlacion_id=correlacion_id,
        idempotencia_key=idempotencia_key,
        metadata={'reason': motivo},
        using=alias,
    )
```

Reglas que el código ya impone:

- `using` es obligatorio y debe coincidir con la BD de `entidad`, actor local,
  sucursal y contexto tenant;
- un resultado `SUCCEEDED` fuera de `transaction.atomic(using=...)` falla con
  `AUDIT_CONTEXT_INVALID`;
- `DENIED`/`FAILED` pueden registrarse después del rollback;
- actor, tenant, sucursal y entidad quedan en referencias opacas y snapshots;
- password, token, Authorization, cookie, API/private keys, VAPID, credenciales
  en URL y estructuras anidadas se redactan sin opt-out;
- la acción nueva sigue `dominio.recurso.operacion` en minúscula;
- `Auditoria.registrar(...)` queda solo como adaptador legacy y no acredita
  integración CT-01.

## Esquema y migración

`auditoria.0008` agrega el envelope transportable, índices por
tenant/sucursal/fecha, entidad y correlación. `apps.auditoria` pasa a dual-home:
eventos de dominio viven en cada BD tenant y eventos de control plane en
`default`.

La migración contempla el caso BUG-F: si `default` marcó `auditoria.0001–0007`
como aplicadas mientras el router excluía la app, crea primero la tabla en su
estado histórico y luego agrega CT-01. Solo rellena datos derivables de filas
legacy; no inventa tenant, sucursal ni actor estable.

## Evidencia temprana

Ejecutado con Python 3.11.14, Django 5.2.17 y PostgreSQL 16 aislado:

```text
python manage.py makemigrations --check --dry-run
No changes detected

python manage.py test apps.auditoria.tests.test_ct01 \
  apps.auditoria.tests.test_auditoria_auditoria \
  --settings=config.settings_development --noinput
Ran 41 tests in 5.698s — OK
```

Los casos cubren envelope, snapshots, redacción adversarial, rollback en ambos
sentidos, fallo del writer, control plane en `default`, taxonomía, consulta
scoped e inmutabilidad.

## Integración / límites

- Claude debe integrar el commit `cd8a3b4` antes de marcar un productor CT-01
  como terminado y repetir sus tests de dominio.
- No se hizo push ni despliegue. No se ejecutó la migración contra ninguna BD
  operativa.
- TEN-016 quedó implementado y validado en `583863f`; el cierre completo y la
  evidencia de dos BDs físicas están en `A02-auditoria-identidad-tenancy.md`.
