#!/usr/bin/env python
"""Construye un repositorio DESECHABLE que contiene el candidato completo.

El problema que resuelve: el código candidato no está commiteado, y un build
desde `HEAD` —por diseño— no lo incluye. Medido en esta máquina: `HEAD` tiene
37 fuentes C# y el árbol de trabajo 48, así que el DLL «desde ref» pesaba
246.272 B y el del árbol 348.160 B. El primero demostraba que el aislamiento
por ref funciona, y **al mismo tiempo** que nunca estaba compilando el
candidato. Ensayar la release sobre ese artefacto era ensayar la versión
anterior.

La salida es un clon temporal con un commit y un tag locales desechables que
contienen exactamente el árbol candidato. El repositorio real no se toca: ni
commit, ni tag, ni index, ni stash.

    python scripts/make_rehearsal_repo.py            # crea y describe
    python scripts/make_rehearsal_repo.py --json     # solo el resumen

Lo que NO viaja al clon: `dist`, `bin`, `obj`, entornos virtuales, cachés,
artefactos generados, locks de runtime y temporales. Un ensayo de release que
arrastra el `dist` anterior mide el `dist` anterior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ".naviscoord-rehearsal"

# Nunca se copian. Son salida, no fuente.
EXCLUDED_DIRS = {
    "dist", "build", "bin", "obj", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".eggs", "node_modules", ".vs", "TestResults", "graphify-out",
}
EXCLUDED_SUFFIXES = (
    ".pyc", ".pyo", ".dll", ".pdb", ".exe", ".zip", ".whl", ".tar.gz",
    ".nwd", ".nwf", ".nwc", ".rvt", ".rfa", ".ifc",
)
EXCLUDED_NAMES = {".env", ".env.local", "core.bundle"}


def run(args: list[str], cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(args, cwd=str(cwd or ROOT), capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise SystemExit(
            f"falló: {' '.join(args)}\n{result.stderr or result.stdout}")
    return result.stdout


def snapshot_status() -> str:
    """Huella del estado del repositorio real, para probar que no cambió."""
    return run(["git", "status", "--porcelain"])


def is_excluded(relative: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return True
    if relative.name in EXCLUDED_NAMES:
        return True
    return relative.name.endswith(EXCLUDED_SUFFIXES)


def untracked_sources() -> list[Path]:
    """Los no rastreados que SON fuente del candidato.

    `--exclude-standard` respeta `.gitignore`, así que `dist/` y compañía ya
    quedan fuera; el filtro adicional es el cinturón por si algo ignorado
    dejara de estarlo.
    """
    out = run(["git", "ls-files", "--others", "--exclude-standard"])
    found = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        relative = Path(line)
        if is_excluded(relative):
            continue
        if (ROOT / relative).is_file():
            found.append(relative)
    return sorted(found)


def build(keep: bool = False) -> dict:
    head = run(["git", "rev-parse", "HEAD"]).strip()
    before = snapshot_status()

    nonce = secrets.token_hex(6)
    workspace = Path(tempfile.mkdtemp(prefix=f"naviscoord-rehearsal-{nonce}-"))
    (workspace / MARKER).write_text(nonce, encoding="utf-8")
    clone = workspace / "repo"

    # `--no-hardlinks`: un clon local enlaza los objetos por defecto, y este
    # clon va a recibir un commit y un tag. Compartir el almacén de objetos
    # con el repositorio real es exactamente lo que no queremos.
    run(["git", "clone", "--quiet", "--no-hardlinks", "--no-local",
         str(ROOT), str(clone)])
    run(["git", "checkout", "--quiet", "--detach", head], cwd=clone)

    # 1. El diff de lo ya rastreado, en binario para no perder nada.
    patch = run(["git", "diff", "--binary", "HEAD"])
    patch_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    if patch.strip():
        patch_file = workspace / "candidate.patch"
        patch_file.write_text(patch, encoding="utf-8", newline="\n")
        applied = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_file)],
            cwd=str(clone), capture_output=True, text=True)
        if applied.returncode != 0:
            raise SystemExit("no se pudo aplicar el patch en el clon:\n"
                             + (applied.stderr or applied.stdout))

    # 2. Los archivos nuevos, que ningún diff contra HEAD contiene.
    carried = untracked_sources()
    for relative in carried:
        target = clone / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)

    # 3. Commit y tag desechables, SOLO en el clon.
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "NavisCoord Rehearsal",
        "GIT_AUTHOR_EMAIL": "rehearsal@localhost",
        "GIT_COMMITTER_NAME": "NavisCoord Rehearsal",
        "GIT_COMMITTER_EMAIL": "rehearsal@localhost",
        # Fecha fija: el ensayo debe poder repetirse y dar el mismo SHA.
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
    })
    subprocess.run(["git", "add", "-A"], cwd=str(clone), check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "--quiet", "-m",
                    "Ensayo de release: arbol candidato completo"],
                   cwd=str(clone), check=True, env=env, capture_output=True)
    tag = "v0.0.0-rehearsal"
    subprocess.run(["git", "tag", tag], cwd=str(clone), check=True,
                   capture_output=True)
    sha = run(["git", "rev-parse", "HEAD"], cwd=clone).strip()

    after = snapshot_status()
    if before != after:
        raise SystemExit("el repositorio REAL cambió durante el ensayo; se aborta")

    summary = {
        "workspace": str(workspace),
        "clone": str(clone),
        "nonce": nonce,
        "base_head": head,
        "rehearsal_sha": sha,
        "rehearsal_tag": tag,
        "patch_sha256": patch_digest,
        "patch_bytes": len(patch.encode("utf-8")),
        "untracked_carried": [str(p).replace("\\", "/") for p in carried],
        "real_repo_unchanged": True,
    }
    if not keep:
        summary["note"] = "el clon queda en disco; bórralo con discard()"
    return summary


def discard(workspace: Path, nonce: str) -> bool:
    """Borra un workspace de ensayo propio. Las cuatro condiciones de siempre."""
    try:
        resolved = workspace.resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return False
    # El reparse se mira ANTES de resolver: `resolve()` sigue el junction.
    if workspace.is_symlink():
        return False
    if resolved == temp_root or temp_root not in resolved.parents:
        return False
    try:
        if (resolved / MARKER).read_text(encoding="utf-8").strip() != nonce:
            return False
    except (OSError, ValueError):
        return False
    _rmtree_readonly(resolved)
    return not resolved.exists()


def _rmtree_readonly(path: Path) -> None:
    """`rmtree` que sobrevive a los objetos de git.

    Git marca los archivos de `.git/objects` como solo lectura, y en Windows
    eso hace fallar el borrado. Con `ignore_errors=True` el fallo era
    silencioso: `discard()` devolvia False y dejaba 120 archivos en %TEMP%
    cada vez. Limpiar el bit y reintentar es lo que convierte el borrado en
    algo que de verdad ocurre.
    """
    def clear_readonly(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=lambda f, t, e: clear_readonly(f, t, e))
    else:
        shutil.rmtree(path, onerror=clear_readonly)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="solo el resumen JSON")
    args = parser.parse_args()

    summary = build(keep=True)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"clon        : {summary['clone']}")
    print(f"base HEAD   : {summary['base_head']}")
    print(f"commit      : {summary['rehearsal_sha']}")
    print(f"tag         : {summary['rehearsal_tag']}")
    print(f"patch       : {summary['patch_bytes']} B, sha256 {summary['patch_sha256'][:16]}…")
    print(f"nuevos      : {len(summary['untracked_carried'])} archivo(s)")
    print("repositorio real sin cambios: sí")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
