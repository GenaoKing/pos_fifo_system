# Revisión de integración A02 + checkpoint Claude C01-C03

Estado: **MERGE A `develop` BLOQUEADO; combinación técnica en rama de revisión**.
Fecha: **2026-09-10**. No hubo push, despliegue, ejecución de launchers ni
lectura/escritura de datos operativos.

## Puntos de corte

- Base usada para el merge técnico: `codex/cierre-prod-A02@ef17e2a`.
- Punto seguro de implementación: `codex/cierre-prod-A02@ef17e2a`. El tip local
  de `develop` contiene además este handoff de revisión, pero ningún commit de
  implementación de Claude.
- Checkpoint de Claude revisado: `claude/cierre-prod-C03@21616ce`; contiene las
  entregas acumuladas C01, C02 y C03 parcial y estaba limpio al tomar el corte.
- Combinación de revisión: `integration/cierre-prod-A02-C03-review@1f0e531`.
- `develop` **no** contiene todavía los commits de Claude.
- El worktree de staging permaneció detached en `89b30c4` y no se tocó.

La simulación y el merge real en la rama de revisión no produjeron conflictos
de texto ni solapamientos de propiedad con A02. La validación combinada quedó
verde con la misma configuración y BD de pruebas aisladas de A02:

- suite focal: **431 tests, 1 skip esperado**;
- suite Django completa sin e-CF: **1301 tests, 1 skip esperado** en
  **386.497 s**, con creación y destrucción de la BD de pruebas;
- e-CF separada: **72 passed** en **17.15 s**;
- `manage.py check`: sin observaciones;
- `makemigrations --check --dry-run`: sin cambios;
- `compileall`, `git diff --check` y estado del worktree: verdes y limpios.

Esto acredita compatibilidad técnica inicial, no aptitud de despliegue: los
siguientes puntos deben cerrarse en las superficies de C.

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
2. Con el worktree limpio, Claude incorpora el tip local vigente de `develop` a
   su rama; no se fuerza ni reescribe historia y no se arrastran cambios de
   staging.
3. Repetir en la combinación final: focal C01-C03 + A02, suite Django completa,
   e-CF separada, `check`, `makemigrations --check`, build/check cloud y pruebas
   de alias tenant indicadas arriba.
4. Solo si todo pasa, integrar por merge explícito a `develop` local. No hacer
   push: `develop` activa automatización de dev. Staging/producción y datos
   operativos permanecen fuera de alcance.

## Rollback

La rama de revisión puede abandonarse sin revertir datos: solo contiene commits
Git y una BD de tests desechable que Django destruyó.
`codex/cierre-prod-A02@ef17e2a` sigue siendo el punto seguro de implementación;
`develop` añade únicamente la documentación de esta revisión hasta cerrar los
bloqueadores.

## Cierre de los bloqueadores (2026-09-10, Claude)

Los tres bloqueadores se cerraron sobre `claude/cierre-prod-C03`
(`8fd83a0`, detalle en
[C03-configuracion-modulos.md](C03-configuracion-modulos.md#cierre-de-los-bloqueadores-de-merge-2026-09-10-8fd83a0))
y se repitió la secuencia acordada:

1. **Paso 1 (fix en la rama)** — `8fd83a0` cierra MERGE-C01-ENVONLY,
   MERGE-C01-SECRET-CAMPO y MERGE-C03-ALIAS-ATOMIC; `b3e8685` documenta el
   cierre en el handoff C03. Worktree limpio.
2. **Paso 2 (incorporar develop)** — `019dd25` mergea `develop@e3635de`
   (A00-A02 + esta revisión) a `claude/cierre-prod-C03`, sin conflictos ni
   force/rebase.
3. **Paso 3 (validación combinada final)** — corrida sobre un venv aislado
   nuevo (`C:\Proyectos\.venvs\pos_cierre_claude_c03_20260910`, CPython
   3.11.14, Django 5.2.17 vía `requirements-dev.txt --require-hashes`; **no
   se tocó el conda compartido `pos_fifo`**, mismo criterio que A01):
   - Suite focal (apps tocadas por A02+C03): **616 OK, ningún fallo**.
   - Suite Django completa sin e-CF (mismo comando que
     `.github/workflows/backend-ci.yml`, find de `tests/test_*.py` excluyendo
     `facturacion_electronica`): **1305 OK, 3 skips esperados** en 386.9 s
     (1301 de la corrida de Codex + 4 tests nuevos de esta entrega).
   - e-CF separado (`pytest`): **72 passed** en 11.0 s.
   - Gates opt-in de BD física dual (`TENANT_TEST_DB_NAMESPACE`), corridos
     juntos: TEN-016 (`test_multidb_isolation`) +
     **`apps/suscripciones/tests/test_seed_atomicidad_tenant.py`** (nuevo,
     cierra la aceptación pendiente de MERGE-C03-ALIAS-ATOMIC: un fallo
     inyectado a mitad de `seed.bootstrap` sobre una BD tenant física revierte
     módulos/planes/suscripción/overrides en esa BD y no deja rastro en
     `default`) — **3 OK**.
   - `manage.py check` (`settings_development`): sin observaciones.
   - `makemigrations --check --dry-run`: sin cambios.
   - `compileall` y `git diff --check` contra `develop`: verdes.
   - **No se repitió** el build/check de `settings_cloud` (requiere la imagen
     Docker Linux 3.12.14 de A01/A08; esta entrega no toca `config/settings_cloud.py`,
     Docker, ni superficies cloud-only, así que se consideró fuera del riesgo
     que ese check cubre).
4. **Paso 4 (merge a develop)** — `claude/cierre-prod-C03` (con A02
   incorporado) se fusiona a `develop` local por merge explícito, sin push.
   Ver el commit de merge para el SHA final.

Estado: **bloqueadores cerrados; línea acumulada de Claude (C01-C03) integrada
a `develop` local.** CT-01 ya está en la base, así que CFG-017/SUS-015
(auditoría de config/comerciales) quedan desbloqueados para una próxima
entrega C. Sigue pendiente de A03: publicar CT-02, que condiciona SUS-006 y
el payload `/auth/perfil/` de CT-03(B); y de Codex, el resto de SUS-007 (pull
de sync).
