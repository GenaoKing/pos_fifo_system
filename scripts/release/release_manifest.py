#!/usr/bin/env python3
"""Construye y verifica el manifiesto reproducible de un candidato backend.

El manifiesto es un artefacto de CI, no configuracion de un ambiente. Solo
describe fuentes versionadas: SHA de Git, locks, receta Docker y migraciones.
No abre una conexion Django, no consulta tenants y no acepta secretos.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA = "posfifo.release-manifest.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DOCKER_BASE_RE = re.compile(
    r"^FROM\s+[^\s]+@sha256:([0-9a-f]{64})\s*$", re.MULTILINE
)
LOCK_PATHS = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements_cloud.txt",
    "requirements_ci.txt",
)
INPUT_PATHS = (
    "requirements/windows-py311.in",
    "requirements/windows-dev-py311.in",
    "requirements/cloud-py312.in",
    "requirements/cloud-ci-py312.in",
    "requirements/test.in",
    "requirements/lock-tools.txt",
)
BUILD_PATHS = ("Dockerfile", ".dockerignore")


class ManifestError(ValueError):
    """El manifiesto o alguno de sus inputs no es apto para un candidato."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_hashes(root: Path, paths: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in paths:
        candidate = root / relative
        if not candidate.is_file():
            raise ManifestError(f"Falta input obligatorio: {relative}")
        result[relative] = sha256_file(candidate)
    return result


def docker_base_image(root: Path) -> str:
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    match = DOCKER_BASE_RE.search(dockerfile)
    if match is None:
        raise ManifestError("Dockerfile debe fijar la imagen base con @sha256.")
    return next(
        line.split(maxsplit=1)[1]
        for line in dockerfile.splitlines()
        if line.startswith("FROM ")
    )


def declared_dependencies(source: str, path: Path) -> list[str]:
    """Devuelve la expresion declarada de `dependencies` sin importar Django.

    Algunas migraciones usan `migrations.swappable_dependency`, que no se puede
    resolver de forma segura sin cargar settings. `ast.unparse` preserva esa
    declaracion para que el hash del archivo sea la fuente de verdad.
    """

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - un archivo invalido ya es fallo
        raise ManifestError(f"Migracion invalida {path}: {exc}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "dependencies" for target in node.targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            return [ast.unparse(item) for item in node.value.elts]
        return [ast.unparse(node.value)]
    return []


def migration_manifest(root: Path) -> list[dict[str, Any]]:
    migrations: list[dict[str, Any]] = []
    for path in sorted((root / "apps").glob("*/migrations/[0-9][0-9][0-9][0-9]_*.py")):
        source = path.read_text(encoding="utf-8")
        migrations.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "declared_dependencies": declared_dependencies(source, path),
            }
        )
    if not migrations:
        raise ManifestError("No se encontraron migraciones Django en apps/*/migrations.")
    return migrations


def artifact_status(
    image_reference: str | None,
    image_digest: str | None,
    local_image_id: str | None,
) -> str:
    if image_digest and image_reference and "@sha256:" in image_reference:
        return "PROMOTABLE"
    if local_image_id:
        return "LOCAL_BUILD_EVIDENCE"
    return "PENDING_BUILD"


def build_manifest(
    root: Path,
    source_sha: str,
    image_reference: str | None = None,
    image_digest: str | None = None,
    local_image_id: str | None = None,
) -> dict[str, Any]:
    source_sha = source_sha.lower()
    if not GIT_SHA_RE.fullmatch(source_sha):
        raise ManifestError("source SHA debe ser el hash Git completo de 40 caracteres.")
    if image_digest is not None and not image_digest.startswith("sha256:"):
        raise ManifestError("El digest OCI debe empezar con sha256:.")
    if image_digest is not None and not SHA256_RE.fullmatch(image_digest.removeprefix("sha256:")):
        raise ManifestError("El digest OCI no tiene 64 caracteres hexadecimales.")
    if local_image_id is not None and not local_image_id.startswith("sha256:"):
        raise ManifestError("El image ID local debe empezar con sha256:.")
    if local_image_id is not None and not SHA256_RE.fullmatch(local_image_id.removeprefix("sha256:")):
        raise ManifestError("El image ID local no tiene 64 caracteres hexadecimales.")

    migrations = migration_manifest(root)
    return {
        "schema": SCHEMA,
        "source": {"git_sha": source_sha},
        "runtime": {
            "cloud_python": "3.12.14",
            "docker_base_image": docker_base_image(root),
        },
        "inputs": {
            "locks": relative_hashes(root, LOCK_PATHS),
            "direct_requirements": relative_hashes(root, INPUT_PATHS),
            "build_files": relative_hashes(root, BUILD_PATHS),
        },
        "migrations": {
            "scope": "source-tree-only; no database was queried or modified",
            "count": len(migrations),
            "files": migrations,
        },
        "artifact": {
            "backend_oci": {
                "image_reference": image_reference,
                "image_digest": image_digest,
                "local_image_id": local_image_id,
                "status": artifact_status(image_reference, image_digest, local_image_id),
                "promotion_rule": (
                    "Promote only a registry image addressed by immutable digest; "
                    "never rebuild an approved candidate."
                ),
            }
        },
        "contracts": {"release_artifact": "CT-05"},
        "safety": {
            "contains_secrets": False,
            "contains_operational_data": False,
            "database_operations": "none",
        },
    }


def assert_lock_structure(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in LOCK_PATHS:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        if "--generate-hashes" not in text[:600]:
            errors.append(f"{relative}: falta cabecera --generate-hashes de pip-compile.")
        if "--hash=sha256:" not in text:
            errors.append(f"{relative}: no contiene hashes de distribuciones.")
        if "\ndjango==5.2.17" not in f"\n{text.lower()}":
            errors.append(f"{relative}: no fija Django 5.2.17.")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    if "--require-hashes -r requirements_cloud.txt" not in dockerfile:
        errors.append("Dockerfile: la instalacion cloud no exige --require-hashes.")
    if DOCKER_BASE_RE.search(dockerfile) is None:
        errors.append("Dockerfile: la base no esta fijada por digest.")
    return errors


def verify_manifest(
    root: Path,
    manifest_path: Path,
    expected_source_sha: str | None,
    require_promotable: bool,
) -> list[str]:
    errors: list[str] = []
    try:
        actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"No se puede leer {manifest_path}: {exc}"]

    if not isinstance(actual, dict):
        return ["El manifiesto raiz debe ser un objeto JSON."]
    if actual.get("schema") != SCHEMA:
        errors.append(f"schema inesperado: {actual.get('schema')!r}")
    source = actual.get("source")
    source_sha = source.get("git_sha") if isinstance(source, dict) else None
    if not isinstance(source_sha, str) or not GIT_SHA_RE.fullmatch(source_sha):
        errors.append("source.git_sha no es un SHA completo valido.")
        return errors
    if expected_source_sha and source_sha != expected_source_sha.lower():
        errors.append(
            f"source.git_sha ({source_sha}) no coincide con el SHA esperado ({expected_source_sha})."
        )

    try:
        artifact = actual["artifact"]["backend_oci"]
        expected = build_manifest(
            root,
            source_sha,
            artifact.get("image_reference"),
            artifact.get("image_digest"),
            artifact.get("local_image_id"),
        )
    except (KeyError, AttributeError, ManifestError) as exc:
        errors.append(f"No se puede reconstruir el manifiesto: {exc}")
        return errors

    if actual != expected:
        errors.append("El manifiesto no coincide exactamente con los inputs versionados actuales.")
    errors.extend(assert_lock_structure(root))

    if require_promotable:
        if artifact.get("status") != "PROMOTABLE":
            errors.append("El artefacto no esta listo para promocion por digest de registry.")
        reference = artifact.get("image_reference")
        digest = artifact.get("image_digest")
        if not isinstance(reference, str) or not isinstance(digest, str) or not reference.endswith(digest):
            errors.append("El image_reference promocionable debe terminar en su image_digest.")
    return errors


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def assert_git_source(root: Path, source_sha: str) -> None:
    """Evita atribuir inputs sin commitear a un SHA que no los contiene."""

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().lower()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ManifestError("No se pudo confirmar el SHA contra un worktree Git limpio.") from exc
    if head != source_sha.lower():
        raise ManifestError(
            f"El SHA declarado ({source_sha}) no coincide con HEAD ({head})."
        )
    if status:
        raise ManifestError(
            "El worktree tiene cambios; commitear o limpiar antes de atribuir inputs a un SHA."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", type=Path, metavar="PATH", help="Escribe un manifiesto determinista.")
    group.add_argument("--verify", type=Path, metavar="PATH", help="Verifica un manifiesto existente.")
    parser.add_argument("--source-sha", help="SHA completo usado al crear el manifiesto.")
    parser.add_argument("--expect-source-sha", help="SHA completo que debe contener un manifiesto verificado.")
    parser.add_argument("--image-reference", help="Referencia OCI sin secretos.")
    parser.add_argument("--image-digest", help="Digest OCI sha256:... sin secretos.")
    parser.add_argument("--local-image-id", help="Image ID local de Docker; no es un digest de registry.")
    parser.add_argument(
        "--assert-git-source",
        action="store_true",
        help="Exige que source SHA sea HEAD y que el worktree no tenga cambios.",
    )
    parser.add_argument(
        "--require-promotable",
        action="store_true",
        help="Exige una referencia de registry terminada en @sha256:...",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    try:
        if args.write:
            if not args.source_sha:
                raise ManifestError("--source-sha es obligatorio con --write.")
            if args.assert_git_source:
                assert_git_source(root, args.source_sha)
            manifest = build_manifest(
                root,
                args.source_sha,
                args.image_reference,
                args.image_digest,
                args.local_image_id,
            )
            errors = assert_lock_structure(root)
            if errors:
                raise ManifestError("\n".join(errors))
            destination = args.write.resolve()
            write_manifest(destination, manifest)
            print(
                f"Manifiesto escrito: {destination} "
                f"({manifest['migrations']['count']} migraciones, "
                f"{manifest['artifact']['backend_oci']['status']})."
            )
            return 0

        errors = verify_manifest(
            root,
            args.verify.resolve(),
            args.expect_source_sha,
            args.require_promotable,
        )
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print(f"Manifiesto valido: {args.verify.resolve()}")
        return 0
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
