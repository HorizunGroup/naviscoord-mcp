#!/usr/bin/env python
"""¿Se basta el sdist a sí mismo, y produce el mismo wheel?

Un sdist es la fuente que alguien reconstruye cuando no confía en el wheel
—una distribución empaquetando, una auditoría, cualquiera sin acceso al
repositorio—. Que exista y pese bytes no demuestra nada: si le falta el
``pyproject.toml``, el perfil por defecto o el README que ``project.readme``
nombra, se descarga bien y falla al construir, meses después y en la máquina
de otro.

Así que se comprueba haciéndolo: extraer el sdist en un temporal, construir el
wheel **desde ahí**, y comparar con el wheel que salió del repositorio. Dos
respuestas distintas, y las dos importan:

  * mismos MIEMBROS  → el sdist es autosuficiente.
  * mismos BYTES     → además es reproducible desde la fuente publicada.

La segunda puede fallar por razones legítimas (otra versión de setuptools
escribe otro ``WHEEL``), así que se informa por separado en vez de mezclarse
con la primera. Lo que nunca se hace es declarar reproducible algo que no se
comparó.

    python scripts/verify_sdist.py           # exige miembros iguales
    python scripts/verify_sdist.py --strict  # exige además bytes iguales

Nunca toca la red: usa ``--no-isolation``, que construye con lo que ya está
instalado. Si falta el backend, lo dice y sale con 2 en vez de fingir.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "server" / "dist"
MARKER = ".naviscoord-sdist-check"


def newest(pattern: str) -> Path:
    found = sorted(DIST.glob(pattern))
    if not found:
        raise SystemExit(f"no encuentro {pattern} en {DIST}; corre antes "
                         "python scripts/build_artifacts.py")
    return found[-1].resolve()


def source_date_epoch() -> str:
    """La misma fecha que usó el build original, o el mismo suelo."""
    existing = os.environ.get("SOURCE_DATE_EPOCH")
    if existing:
        return existing
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%ct"],
                             capture_output=True, text=True, check=True).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.CalledProcessError):
        pass
    return "315532800"


def backend_available() -> bool:
    for module in ("build", "setuptools"):
        if subprocess.run([sys.executable, "-c", f"import {module}"],
                          capture_output=True).returncode != 0:
            return False
    return True


def discard(workspace: Path, nonce: str) -> None:
    """Borrado con las mismas cuatro condiciones que el resto del repositorio."""
    try:
        resolved = workspace.resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return
    if resolved == temp_root or temp_root not in resolved.parents:
        return
    try:
        if (resolved / MARKER).read_text(encoding="utf-8").strip() != nonce:
            return
    except (OSError, ValueError):
        return
    if resolved.is_symlink():
        return
    shutil.rmtree(resolved, ignore_errors=True)


def verify(strict: bool = False) -> int:
    sdist = newest("*.tar.gz")
    original = newest("*.whl")

    if not backend_available():
        print("falta 'build' o 'setuptools' en este intérprete: no se puede "
              "reconstruir sin red. Instálalos y repite.", file=sys.stderr)
        return 2

    nonce = secrets.token_hex(8)
    work = Path(tempfile.mkdtemp(prefix=f"naviscoord-sdist-{nonce}-"))
    (work / MARKER).write_text(nonce, encoding="utf-8")
    try:
        with tarfile.open(sdist) as tar:
            # `filter="data"` rechaza miembros con rutas absolutas o que
            # escapan del destino. Un sdist es un archivo descargable: se
            # extrae con la misma desconfianza que cualquier otro.
            tar.extractall(work, filter="data")

        roots = [p for p in work.iterdir() if p.is_dir()]
        if len(roots) != 1:
            print(f"el sdist no trae un único directorio raíz: {roots}", file=sys.stderr)
            return 1
        extracted = roots[0]

        env = dict(os.environ)
        env["SOURCE_DATE_EPOCH"] = source_date_epoch()
        out = work / "out"
        built = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation",
             "--outdir", str(out)],
            cwd=str(extracted), env=env, capture_output=True, text=True)
        if built.returncode != 0:
            print("el sdist NO se basta a sí mismo: el build falló desde la "
                  "fuente extraída.", file=sys.stderr)
            print((built.stderr or built.stdout)[-2000:], file=sys.stderr)
            return 1

        rebuilt = sorted(out.glob("*.whl"))[-1]
        members_a = sorted(zipfile.ZipFile(original).namelist())
        members_b = sorted(zipfile.ZipFile(rebuilt).namelist())

        print(f"sdist : {sdist.name}")
        print(f"wheel : {original.name}")
        if members_a != members_b:
            print("los dos wheels NO contienen lo mismo:", file=sys.stderr)
            for name in sorted(set(members_a) - set(members_b)):
                print(f"  solo en el original    : {name}", file=sys.stderr)
            for name in sorted(set(members_b) - set(members_a)):
                print(f"  solo en el reconstruido: {name}", file=sys.stderr)
            return 1
        print(f"ok  autosuficiente: {len(members_a)} miembros idénticos")

        digest_a = hashlib.sha256(original.read_bytes()).hexdigest()
        digest_b = hashlib.sha256(rebuilt.read_bytes()).hexdigest()
        if digest_a == digest_b:
            print(f"ok  reproducible desde el sdist: sha256 {digest_a[:16]}…")
            return 0

        # Honestidad: se dice que los bytes difieren aunque el contenido
        # coincida, y NO se llama reproducible a esto.
        print("aviso  mismos miembros, bytes distintos:")
        print(f"       original    {digest_a}")
        print(f"       desde sdist {digest_b}")
        print("       (suele ser otra versión del backend escribiendo otro WHEEL)")
        return 1 if strict else 0
    finally:
        discard(work, nonce)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="falla también si los bytes difieren")
    return verify(strict=parser.parse_args().strict)


if __name__ == "__main__":
    raise SystemExit(main())
