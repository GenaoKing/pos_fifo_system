# Baseline reproducible de Python

| Destino | Runtime | Input humano | Lock consumido |
| --- | --- | --- | --- |
| POS Windows | CPython 3.11 x64 | `windows-py311.in` | `../requirements.txt` |
| Desarrollo Windows | CPython 3.11 x64 | `windows-dev-py311.in` | `../requirements-dev.txt` |
| Cloud Linux | CPython 3.12 x64 | `cloud-py312.in` | `../requirements_cloud.txt` |
| CI Linux | CPython 3.12 x64 | `cloud-ci-py312.in` | `../requirements_ci.txt` |

Los `.in` fijan las dependencias directas aprobadas. Los cuatro locks incluyen
transitivas y hashes. Instaladores, Docker y CI consumen los locks; no se instala
desde los `.in` en un entorno de release.

Para un entorno conda nuevo, `environment.yml` fija solo Python/pip y evita los
paths de la maquina que tenia el snapshot anterior. Instalar despues el lock:

```powershell
conda env create --file environment.yml --name pos_fifo_a01
conda run --name pos_fifo_a01 python -m pip install --require-hashes `
  --requirement requirements.txt
```

No se actualiza en sitio el entorno conda compartido.

## Regenerar

Usar un venv de herramientas aislado con `lock-tools.txt` y pip 26.0.1. Los
locks Windows se generan bajo CPython 3.11 x64:

```powershell
python -m pip install --upgrade "pip==26.0.1"
python -m pip install -r requirements/lock-tools.txt

pip-compile --generate-hashes --allow-unsafe --no-strip-extras `
  --index-url https://pypi.org/simple `
  --output-file requirements.txt requirements/windows-py311.in
pip-compile --generate-hashes --allow-unsafe --no-strip-extras `
  --index-url https://pypi.org/simple `
  --output-file requirements-dev.txt requirements/windows-dev-py311.in
```

Los locks cloud/CI se generan dentro de `python:3.12-slim-bookworm`, no desde
el Python de Windows. El gate compara que una segunda compilacion no cambie los
archivos. La imagen de generacion registrada en A01 es
`python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254`.

## Wheelhouse Windows offline

Claude/C01 construye el wheelhouse fuera de Git, sin `.env` ni dumps:

```powershell
python -m pip download --require-hashes --only-binary=:all: `
  --dest wheelhouse/windows-py311 --requirement requirements.txt
python -m pip install --no-index --find-links wheelhouse/windows-py311 `
  --require-hashes --requirement requirements.txt
```

El manifiesto del paquete registra SHA-256 de `requirements.txt`, cada wheel y
el ZIP final. Un wheelhouse de otra version de Python/arquitectura no es valido.

## Politica de actualizacion

- El baseline anterior era Django 5.0.8/Python 3.11. El nuevo fija
  **Django 5.2.17 LTS** en Windows 3.11 y cloud/CI 3.12. La serie 5.2 soporta
  ambos runtimes; el salto exige checks, warnings y suite completa antes de
  promover. Ver las notas oficiales de
  [Django 5.2](https://docs.djangoproject.com/en/5.2/releases/5.2/) y
  [5.2.17](https://docs.djangoproject.com/en/5.2/releases/5.2.17/).
- `5.2.18` esta anunciado para octubre de 2026 y no entra anticipadamente.
- Actualizar una dependencia directa requiere editar su `.in`, regenerar todos
  los locks afectados y repetir Windows 3.11/cloud 3.12.
- `requirements_actual.txt` y `requirements1.txt` eran snapshots con rutas de
  conda locales; no son locks ni entradas soportadas.
