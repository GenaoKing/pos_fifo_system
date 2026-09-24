# Handoff A01 — baseline de ejecución y dependencias

Estado: **VALIDADO para integración local; no publicado en remoto**. Fecha:
**2026-09-10**. No hubo push, despliegue ni lectura/escritura de datos
operativos.

## SHAs y alcance

- Entrada A00/local `develop`: `bbc055b`.
- Implementación A01: `a8b5e47` (`build(baseline): fijar Django 5.2 y locks
  reproducibles`).
- Cierre documental: el commit que contiene este handoff.
- Rama/worktree de Codex: `codex/cierre-prod-A01` en
  `C:\Proyectos\pos_fifo_system_cierre_codex`.
- El worktree de Claude se observó en `5ce0f3f` (C01) y **no fue modificado ni
  integrado por Codex**.

Este bloque cambia únicamente superficies asignadas a A: requirements/locks,
Docker/CI, `config/settings*`, pruebas API del cargador y una deprecación Django
en el admin de auditoría. No cambia `deploy/**`, launchers, frontend, Terraform,
migraciones ni apps de propiedad de Claude.

## Baseline publicado

| Destino | Runtime | Lock | SHA-256 |
| --- | --- | --- | --- |
| POS Windows | CPython 3.11.14 x64 | `requirements.txt` | `211A4BA5A93640C7554FA7EA9FF9FA7E1AA8E529EB001D116AA01E4A2FC00081` |
| Desarrollo Windows | CPython 3.11.14 x64 | `requirements-dev.txt` | `D6138EF0BAC9337CA8D87709A56F9FE146C4A16D3112029D46D5E3386382347A` |
| Cloud Linux | CPython 3.12.14 x64 | `requirements_cloud.txt` | `64525AD0836F8526D7A7F5BD91D54969F088F7FEEA2D8CDEBF37B6A2183D2587` |
| CI Linux | CPython 3.12.14 x64 | `requirements_ci.txt` | `77477E1BB688172C97D454F7C9A8252C4820352981B3CD43CE6E5B62AEF1F896` |

- Django pasa de 5.0.8 a **5.2.17 LTS**; el mismo parche queda fijado en los
  cuatro destinos.
- pip queda fijado en 26.0.1 y Docker/CI instalan con `--require-hashes`.
- Inputs humanos y herramienta: `requirements/*.in` y
  `requirements/lock-tools.txt` (`pip-tools==7.6.1`).
- Los cuatro locks resultaron byte-a-byte estables en una segunda generación.
- Docker fija la imagen base por digest:
  `python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254`.
- `requirements_actual.txt` y `requirements1.txt` dejaron de exponer snapshots
  con rutas conda de una máquina y ahora remiten a la matriz soportada.
- La receta reproducible y el contrato del wheelhouse están en
  `requirements/README.md`. ReportLab, Pillow, python-dotenv y dependencias de
  impresión Windows están fijadas para C01/C02.

## Contrato `POS_ENV_FILE` para C01

Implementado en `config/env_loader.py` y publicado en `CONTRATOS.md`:

1. variables del proceso > archivo `POS_ENV_FILE` > defaults;
2. C01 registra una ruta absoluta; `load_dotenv` usa `override=False` y UTF-8;
3. ruta explícita vacía, relativa, ausente, no-archivo o ilegible falla temprano
   con `ImproperlyConfigured`, sin fallback silencioso;
4. sin variable explícita, `deploy/env_cliente.env` sigue siendo el default
   opcional para compatibilidad;
5. el cargador devuelve la ruta absoluta leída o `None` y nunca registra
   valores/secretos.

Los cuatro casos automatizados cubren default ausente, ruta relativa, ruta
explícita ausente, precedencia del proceso y lectura UTF-8.

## Evidencia ejecutada

### Windows 3.11 aislado

- Venv limpio fuera del repo:
  `C:\Proyectos\.venvs\pos_cierre_codex_a01_20260910`; no se actualizó el
  conda compartido.
- Instalación `requirements-dev.txt` con hashes: OK; `pip check`: OK.
- `manage.py check`: 0 issues.
- `makemigrations --check --dry-run`: `No changes detected`.
- Suite Django, e-CF excluido: **1.173 OK en 426.280 s** contra
  `test_pos_cierre_codex`; la BD de tests fue destruida al terminar.
- e-CF separado: **72 passed en 17.61 s**.
- Regresión focal tras ajustar el admin/cargador: **35 OK**.

### Linux 3.12.14 / forma de CI aislada

- Build final local de Docker con lock cloud y `collectstatic`: OK, 169 archivos
  copiados y 158 postprocesados. No se publicó la imagen.
- `pip check` dentro de la imagen: OK; `manage.py check --settings=config.settings_cloud`:
  0 issues.
- PostgreSQL 16 efímero, red/contenedor propios y sin bind a ninguna BD real.
- Suite Django: **1.173 OK en 450.845 s**.
- e-CF separado: **72 passed en 19.24 s**.
- El contenedor PostgreSQL, su volumen anónimo y la red de laboratorio se
  eliminaron al finalizar.

La primera corrida Linux usó una imagen creada antes de cambiar las pruebas
nuevas de `unittest.TestCase` a `django.test.SimpleTestCase`: terminó con 1.171
casos efectivos y dos errores de harness (`assertRaisesMessage` ausente). Se
reconstruyó la imagen y se repitió la suite completa; los números verdes de
arriba corresponden exclusivamente a la imagen final.

## Observaciones entregadas a Claude

- C01/C02 deben repetir su aceptación si comenzaron antes de `a8b5e47`; no
  editar los locks a mano. Si necesitan otro pin, lo solicitan a A.
- C01 construye fuera de Git el wheelhouse CPython 3.11 x64, registra SHA-256 de
  lock/wheels/ZIP y excluye `.env`, dumps y credenciales.
- Bajo `-Wa` quedan avisos no bloqueantes en superficies de B: llamadas
  `format_html()` sin argumentos en `apps/ventas/admin.py` y un
  `ResourceWarning` por un PDF de cierre no cerrado. Se pasan a C02; A corrigió
  las ocurrencias equivalentes de `apps/auditoria/admin.py`.
- Que cloud no instale `python-escpos` es deliberado: el driver permanece solo
  en el lock Windows; las pruebas cloud verificaron el fallback.
- Los alias tenant `tnt_*` todavía no tienen namespace por agente. Esta corrida
  se hizo en serial; mantener esa contención hasta TEN-016/A02.

## Publicación local y siguiente paso

El cierre fast-forward de este bloque deja `develop` local como base consumible.
Claude debe
esperar a que su worktree C01 esté limpio y luego integrar ese `develop`; Codex
no reescribe ni fuerza su rama. A02/A03 pueden comenzar sobre el mismo tip usando
CT-01/CT-02 publicados en `eb5f6b0`.

No se hizo push porque `develop` dispara el pipeline de dev. Tampoco se inició
workflow, job de migración, servicio POS/sync ni operación Azure. G1-G4 siguen
pendientes.

## Rollback

- Código/dependencias: `git revert` de los commits A01, conservando los handoffs.
- No hay rollback de datos: no se aplicaron migraciones ni se escribieron BDs
  operativas.
- El venv aislado y la imagen local son artefactos de laboratorio; no forman
  parte del candidato A08/C06.
