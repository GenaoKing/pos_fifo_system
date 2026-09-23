#!/usr/bin/env python3
"""
scripts/c06_construir_paquete.py

Laboratorio C06.1 — construye un paquete Windows offline y trazable al SHA
congelado del backend, a partir de un `git archive` del commit exacto (nunca
del working tree) mas el wheelhouse ya descargado con `pip download
--require-hashes`.

Por que `git archive` y no copiar el working tree: un archivo commiteado por
accidente (log, fixture, script con credenciales) igual viajaria si se
copiara el directorio a mano. `git archive` sobre un SHA fijo da un paquete
bit-a-bit atribuible a ese commit y automaticamente ausente de todo lo
gitignored (.env, media/, dumps, __pycache__); la lista `EXCLUSIONES_PAQUETE`
de abajo cubre lo que SI esta trackeado en git pero no debe viajar en un
paquete de cliente (herramientas de desarrollo, logs, fixtures de prueba,
credenciales de ensayo).

No se ejecuta contra ninguna instalacion real ni sustituye la migracion del
wheelhouse (`requirements/README.md`); solo empaqueta lo que esas dos fuentes
ya produjeron.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Rutas/prefijos trackeados en git que NO viajan en el paquete de cliente,
# con la razon de cada exclusion. No son secretos por definicion de .gitignore
# (ya los excluye git): son contenido SI trackeado que no pertenece a un
# paquete de instalacion/actualizacion.
EXCLUSIONES_PAQUETE = {
    'node_modules': 'tooling de frontend legado; el portal vive en un repo hermano',
    'package.json': 'tooling de frontend legado; el portal vive en un repo hermano',
    'package-lock.json': 'tooling de frontend legado; el portal vive en un repo hermano',
    'tailwind.config.js': 'tooling de frontend legado; el portal vive en un repo hermano',
    'tailwind.config.js.backup': 'archivo .backup residual, no es codigo activo',
    'cloud_debug.log.1': 'log de depuracion commiteado por accidente (~5 MB, queries SQL)',
    'script.py': 'script ad-hoc de prueba de impresora, no es parte del paquete',
    'prueba.bat': 'script ad-hoc con un JWT y una password de prueba en texto plano',
    'diagnostic': 'duplicado huerfano de utils/diagnostics/test_printer.py',
    'master_data.json': 'fixture de prueba sin ningun consumidor (loaddata) en el codigo',
    'requirements1.txt': 'snapshot legado de una maquina; no es un lock soportado',
    'requirements_actual.txt': 'snapshot legado de una maquina; no es un lock soportado',
}

# Patrones que NUNCA deben aparecer en el paquete final, aunque no esten en
# EXCLUSIONES_PAQUETE (defensa en profundidad ante un .gitignore incompleto o
# un archivo nuevo commiteado por error entre esta corrida y la proxima).
PATRONES_PROHIBIDOS = (
    '.env', 'env_cliente.env', 'env_cliente.bat',
    '.sqlite3', '.dump', '.bak', '.pgpass',
)
EXCEPCIONES_PROHIBIDOS = (
    '.env.example', 'env_cliente.env.template', 'env_cliente.bat.template',
)


def sha256_de(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, 'rb') as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b''):
            h.update(bloque)
    return h.hexdigest()


def git(*args, cwd):
    return subprocess.run(
        ['git', *args], cwd=cwd, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def construir_staging(repo_dir: Path, sha: str, staging: Path):
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    proc = subprocess.run(
        ['git', 'archive', '--format=tar', f'--prefix=pos_fifo_system/', sha],
        cwd=repo_dir, check=True, capture_output=True,
    )
    tar_path = staging / '_archive.tar'
    tar_path.write_bytes(proc.stdout)
    import tarfile
    with tarfile.open(tar_path) as tf:
        tf.extractall(staging)
    tar_path.unlink()
    return staging / 'pos_fifo_system'


def aplicar_exclusiones(paquete_root: Path):
    aplicadas = []
    for nombre, razon in EXCLUSIONES_PAQUETE.items():
        objetivo = paquete_root / nombre
        if objetivo.exists():
            if objetivo.is_dir():
                shutil.rmtree(objetivo)
            else:
                objetivo.unlink()
            aplicadas.append({'ruta': nombre, 'razon': razon})
    return aplicadas


def verificar_sin_prohibidos(paquete_root: Path):
    """Escanea el arbol final y falla duro si encuentra algo de
    PATRONES_PROHIBIDOS. Es la comprobacion verificable de exclusion: no
    confia solo en la lista de arriba ni en .gitignore, vuelve a mirar los
    archivos que efectivamente quedaron en disco."""
    hallazgos = []
    for ruta in paquete_root.rglob('*'):
        if not ruta.is_file():
            continue
        nombre = ruta.name
        if nombre in EXCEPCIONES_PROHIBIDOS:
            continue
        for patron in PATRONES_PROHIBIDOS:
            if patron in nombre:
                hallazgos.append(str(ruta.relative_to(paquete_root)))
                break
    return hallazgos


def copiar_wheelhouse(wheelhouse_src: Path, paquete_root: Path):
    destino = paquete_root / 'wheelhouse' / 'windows-py311'
    destino.mkdir(parents=True, exist_ok=True)
    wheels = []
    for whl in sorted(wheelhouse_src.glob('*.whl')):
        shutil.copy2(whl, destino / whl.name)
        wheels.append({
            'archivo': whl.name,
            'sha256': sha256_de(whl),
            'bytes': whl.stat().st_size,
        })
    return wheels


def zip_de(paquete_root: Path, destino_zip: Path):
    if destino_zip.exists():
        destino_zip.unlink()
    with zipfile.ZipFile(destino_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for ruta in sorted(paquete_root.rglob('*')):
            if ruta.is_file():
                zf.write(ruta, ruta.relative_to(paquete_root.parent))
    return destino_zip


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', required=True, help='Worktree del backend (branch propio C06.1)')
    ap.add_argument('--sha', required=True, help='SHA congelado a empaquetar')
    ap.add_argument('--wheelhouse', required=True, help='Carpeta con los .whl ya descargados')
    ap.add_argument('--lab', required=True, help='Carpeta de laboratorio (fuera de git) para staging/salida')
    ap.add_argument('--portal-ref-sha', default=None, help='SHA del portal, solo para la matriz de compatibilidad')
    ap.add_argument('--portal-ref-branch', default=None)
    args = ap.parse_args()

    repo_dir = Path(args.repo).resolve()
    lab = Path(args.lab).resolve()
    wheelhouse_src = Path(args.wheelhouse).resolve()
    lab.mkdir(parents=True, exist_ok=True)

    sha_real = git('rev-parse', args.sha, cwd=repo_dir)
    rama_actual = git('rev-parse', '--abbrev-ref', 'HEAD', cwd=repo_dir)

    staging = lab / 'paquete_staging'
    paquete_root = construir_staging(repo_dir, sha_real, staging)

    exclusiones = aplicar_exclusiones(paquete_root)
    wheels = copiar_wheelhouse(wheelhouse_src, paquete_root)

    prohibidos = verificar_sin_prohibidos(paquete_root)
    if prohibidos:
        print('[FALLO] Patrones prohibidos encontrados tras aplicar exclusiones:', file=sys.stderr)
        for p in prohibidos:
            print(f'  - {p}', file=sys.stderr)
        sys.exit(1)

    requirements_path = paquete_root / 'requirements.txt'
    requirements_sha = sha256_de(requirements_path)

    paquete_dir = lab / 'paquete'
    paquete_dir.mkdir(exist_ok=True)
    nombre_zip = f'pos_fifo_system_C06.1_{sha_real[:12]}.zip'
    zip_path = paquete_dir / nombre_zip
    zip_de(paquete_root, zip_path)
    zip_sha = sha256_de(zip_path)
    zip_bytes = zip_path.stat().st_size

    manifiesto = {
        'schema_version': 'paquete.windows.c06_1.v1',
        'generado_utc': datetime.now(timezone.utc).isoformat(),
        'ct05_referencia': 'CT-05 (CONTRATOS.md) — artefacto/actualizacion, EN_CURSO',
        'estado': 'CANDIDATO LOCAL — NO PROMOTABLE',
        'backend': {
            'sha': sha_real,
            'rama_base': 'integration/cierre-prod-A06-C04-C05',
            'rama_trabajo': rama_actual,
        },
        'runtime': {'python': 'CPython 3.11.14 x64', 'pip_requerido': '26.0.1'},
        'lock': {'archivo': 'requirements.txt', 'sha256': requirements_sha},
        'wheelhouse': {'destino': 'wheelhouse/windows-py311', 'wheels': wheels, 'cantidad': len(wheels)},
        'exclusiones_aplicadas_sobre_git': exclusiones,
        'exclusiones_ya_cubiertas_por_gitignore': [
            '.env / deploy/*.env (deploy/*.env.template SI viaja)',
            'media/', 'docs/dumps/', '__pycache__/', 'documentos financieros privados',
        ],
        'verificacion_prohibidos': 'OK: 0 coincidencias de patrones prohibidos en el arbol final',
        'zip_final': {'archivo': nombre_zip, 'sha256': zip_sha, 'bytes': zip_bytes},
        'portal_referencia_solo_matriz_compatibilidad': {
            'rama': args.portal_ref_branch,
            'sha': args.portal_ref_sha,
            'nota': 'No se integra ni se instala; solo referencia para la matriz de compatibilidad backend/portal.',
        },
    }
    (paquete_dir / f'MANIFIESTO_C06.1_{sha_real[:12]}.json').write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding='utf-8',
    )

    print(json.dumps(manifiesto, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
