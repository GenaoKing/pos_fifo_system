# Revisión de integración A02 + checkpoint Claude C01-C03

Estado: **MERGE A `develop` BLOQUEADO; combinación técnica en rama de revisión**.
Fecha: **2026-09-10**. No hubo push, despliegue, ejecución de launchers ni
lectura/escritura de datos operativos.

## Puntos de corte

- Base segura integrada: `develop@ef17e2a` (A00-A02 de Codex).
- Checkpoint de Claude revisado: `claude/cierre-prod-C03@21616ce`; contiene las
  entregas acumuladas C01, C02 y C03 parcial y estaba limpio al tomar el corte.
- Combinación de revisión: `integration/cierre-prod-A02-C03-review@1f0e531`.
- `develop` **no** contiene todavía los commits de Claude.
- El worktree de staging permaneció detached en `89b30c4` y no se tocó.

La simulación y el merge real en la rama de revisión no produjeron conflictos
de texto ni solapamientos de propiedad con A02. La suite focal combinada pasó
**431 tests, 1 skip esperado**. Esto acredita compatibilidad inicial, no aptitud
de despliegue: los siguientes puntos deben cerrarse en las superficies de C.

## Bloqueadores para integrar la línea acumulada de Claude

### MERGE-C01-ENVONLY — instalación solo `.env` rechazada

`deploy/actualizar.bat:68` exige siempre que exista `env_cliente.bat` antes de
comprobar `env_cliente.env`. Contradice la aceptación C01: BAT legado, solo
`.env` o ambos. Claude debe cubrir las tres variantes y confirmar que el caso
solo `.env` llega al preflight sin depender del BAT.

### MERGE-C01-SECRET-CAMPO — lector genérico imprime secretos

`deploy/preflight_actualizar.py:66` imprime cualquier clave solicitada y el
parser acepta un `nombre` arbitrario en la línea 134. Aunque el BAT actual solo
pide campos no sensibles, una invocación directa con `DB_PASSWORD`,
`CLOUD_API_TOKEN` o `DJANGO_SECRET_KEY` los envía a stdout. C01 exige no imprimir
secretos. Restringir `campo` a una allowlist explícita de valores no sensibles y
probar que las claves sensibles fallan sin aparecer en stdout/stderr.

### MERGE-C03-ALIAS-ATOMIC — atomicidad abierta en la BD equivocada

`apps/suscripciones/seed.py:176` y el comando en
`apps/suscripciones/management/commands/bootstrap_suscripciones.py:35` usan
`transaction.atomic()` sin alias. Bajo `with_tenant`, los managers pueden
escribir en `tnt_*` mientras el bloque atómico se abre sobre `default`. Los
wrappers de A02 hoy protegen algunos llamadores con un `atomic(using=alias)`
externo, pero el comando standalone y cualquier consumidor nuevo no heredan esa
garantía. La firma debe recibir/resolver `using`, aplicar `.using(using)` a las
consultas y abrir `transaction.atomic(using=using)`. Aceptación: fallo inyectado
en una BD tenant revierte módulos, planes, suscripción y overrides en esa misma
BD, sin escribir `default`.

## Observaciones no bloqueantes pero obligatorias antes de C06

- C01 y C02 se declaran parciales en sus propios handoffs. El actualizador aún
  detiene servicios antes del backup y puede dejar el install mezclado tras
  fallos posteriores; recuperación por interrupción, wheelhouse y paquete final
  siguen abiertos. No ejecutar `actualizar.bat` sobre una instalación real.
- El handoff C02 registra degradación de logo mediante logging; los eventos
  CT-01 y gates CT-02 de impresión siguen pendientes de su siguiente entrega.
- El handoff C03 fue escrito sobre una base donde CT-01 figuraba pendiente. Al
  consumir A02 debe actualizar esa dependencia y repetir provisioning/tenancy.
- La mitad A de SUS-007 (snapshot efectivo en pull de sync) pertenece a Codex y
  se agenda después de aceptar CT-03; no debe resolverse modificando archivos A
  desde el worktree de Claude.

## Secuencia acordada para el merge

1. Claude corrige los tres bloqueadores en su rama, completa el handoff del
   checkpoint y deja su worktree limpio.
2. Con el worktree limpio, Claude incorpora `develop@ef17e2a` a su rama; no se
   fuerza ni reescribe historia y no se arrastran cambios de staging.
3. Repetir en la combinación final: focal C01-C03 + A02, suite Django completa,
   e-CF separada, `check`, `makemigrations --check`, build/check cloud y pruebas
   de alias tenant indicadas arriba.
4. Solo si todo pasa, integrar por merge explícito a `develop` local. No hacer
   push: `develop` activa automatización de dev. Staging/producción y datos
   operativos permanecen fuera de alcance.

## Rollback

La rama de revisión puede abandonarse sin revertir datos: solo contiene commits
Git y una BD de tests desechable que Django destruyó. `develop@ef17e2a` sigue
siendo el punto seguro hasta cerrar este handoff.
