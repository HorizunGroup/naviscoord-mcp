"""Builds the wheel and sdist with LICENSE and NOTICE actually inside them.

setuptools refuses to reach outside the project directory for files, and both
licence texts live at the repository root — one level above `server/`. Told
nothing, it produces a wheel with no licence at all, which is the single thing
a redistributable must never do and the single thing nobody checks by hand.

So: copy the SOURCE somewhere else, put the licences in that copy, build
there, and verify by reading the archives back. The verification is the point
— a build step that copies files and trusts the result is how the problem
returns.

Why a copy rather than staging into `server/`: the previous version wrote
LICENSE, NOTICE and README.md into the source tree and removed them in a
`finally`. A `finally` does not run when the process is killed, so an
interrupted build left three untracked files behind that look like a second
source of truth; two builds at once fought over them; and a build from a
checked-out tag mutated the very worktree whose cleanliness the release is
supposed to prove. Building the copy costs a directory copy and removes all
three.

    python scripts/build_artifacts.py                 # build and verify
    python scripts/build_artifacts.py --check         # verify existing artifacts
    python scripts/build_artifacts.py --reproducible  # build twice, compare bytes
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
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
SERVER = ROOT / "server"
DIST = SERVER / "dist"
LEGAL = ("LICENSE", "NOTICE")
# README joins them: `project.readme` names it, so without this the sdist is
# built with "standard file not found: should have one of README..." and the
# package publishes an empty description.
STAGED = (*LEGAL, "README.md")


def source_date_epoch() -> str:
    """A build date taken from the commit, not from the clock.

    Two clones of one commit have to produce the same archive, and the moment
    somebody ran `git clone` is not a property of the software. Honoured by
    setuptools for the timestamps it writes into the wheel, and by the ZIP
    packer in Build-Release.ps1 for the add-in. An existing value wins, which
    is the convention distributions rely on.
    """
    existing = os.environ.get("SOURCE_DATE_EPOCH")
    if existing:
        return existing
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%ct"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.CalledProcessError):
        pass
    # No git (a downloaded tarball). "I don't know" still has to be
    # deterministic, so it is the earliest date the ZIP format accepts.
    return "315532800"


# Written into every workspace this script creates, and checked again before
# the workspace is deleted. A recursive delete that trusts only the path it
# was handed deletes whatever it was handed.
OWNERSHIP_MARKER = ".naviscoord-build-workspace"

# Never copied into the build workspace: build output, caches and virtual
# environments are not sources, and copying `dist` in would package the
# previous release inside the next sdist.
EXCLUDED = {"dist", "build", "__pycache__", ".venv", "venv", ".pytest_cache",
            ".mypy_cache", ".ruff_cache", ".tox", ".eggs"}


def _ignore(directory: str, names: list[str]) -> set[str]:
    skipped = {n for n in names if n in EXCLUDED or n.endswith(".egg-info")}
    return skipped


def make_workspace() -> tuple[Path, str]:
    """A private copy of `server/` with the licences in it. Unique per run.

    Returns the workspace and the nonce that proves this run created it.
    """
    nonce = secrets.token_hex(8)
    workspace = Path(tempfile.mkdtemp(prefix=f"naviscoord-build-{nonce}-"))
    (workspace / OWNERSHIP_MARKER).write_text(nonce, encoding="utf-8")

    package = workspace / "server"
    shutil.copytree(SERVER, package, ignore=_ignore, symlinks=False)

    for name in STAGED:
        source = ROOT / name
        if not source.is_file():
            raise SystemExit(f"falta {name} en la raíz del repositorio")
        shutil.copy2(source, package / name)
    return workspace, nonce


def discard_workspace(workspace: Path, nonce: str) -> None:
    """Removes a workspace this run created, and refuses anything else.

    Absolute path, inside the system temp directory, carrying OUR marker with
    OUR nonce, and not a reparse point. A recursive delete that skips any of
    these is one bad variable away from removing a real directory.
    """
    # Antes de resolver nada: `resolve()` SIGUE un junction, así que mirar el
    # reparse en la ruta ya resuelta mira el destino y no el enlace. Una
    # comprobación puesta después de resolver da permiso para borrar
    # exactamente lo que el enlace apuntaba.
    if workspace.is_symlink() or _is_reparse_point(workspace):
        return
    try:
        resolved = workspace.resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return
    if resolved == temp_root or temp_root not in resolved.parents:
        return
    marker = resolved / OWNERSHIP_MARKER
    try:
        if marker.read_text(encoding="utf-8").strip() != nonce:
            return
    except (OSError, ValueError):
        return
    if resolved.is_symlink() or _is_reparse_point(resolved):
        return
    shutil.rmtree(resolved, ignore_errors=True)


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    try:
        return bool(os.stat(path, follow_symlinks=False).st_file_attributes
                    & FILE_ATTRIBUTE_REPARSE_POINT)
    except (OSError, AttributeError):
        return False


def build(*, isolated: bool = True) -> None:
    """Builds wheel and sdist. `isolated=False` uses what is already installed.

    Isolation is right for a release: the build backend is resolved fresh and
    pinned by `pyproject.toml`, so the artifact does not depend on whatever
    happened to be on the builder's machine. It is wrong for a test, because
    resolving the backend means reaching the network — and a test that
    downloads is a test that fails when the index is slow and passes for
    reasons that have nothing to do with the code.
    """
    if DIST.exists():
        shutil.rmtree(DIST)
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = source_date_epoch()
    print(f"SOURCE_DATE_EPOCH={env['SOURCE_DATE_EPOCH']}")

    command = [sys.executable, "-m", "build", "--outdir", str(DIST)]
    if not isolated:
        command.append("--no-isolation")

    workspace, nonce = make_workspace()
    try:
        subprocess.run(command, cwd=workspace / "server", check=True, env=env)
    finally:
        discard_workspace(workspace, nonce)

    _, sdist = artifacts()
    normalize_sdist(sdist, int(env["SOURCE_DATE_EPOCH"]))


def normalize_sdist(sdist: Path, epoch: int) -> None:
    """Rewrites the sdist so its bytes depend only on its contents.

    setuptools honours SOURCE_DATE_EPOCH for the wheel but not for the sdist:
    the tar entries keep the real modification time of each file on disk, and
    the files it GENERATES during the build — PKG-INFO, the root directory —
    are therefore stamped with the moment the build ran. Two builds of one
    commit produced archives with identical members, identical order and
    identical sizes, differing only in those stamps. The gzip wrapper adds its
    own timestamp on top.

    This is a repack, not a patch: every member is copied across with its
    content untouched and its metadata normalised. Nothing is rewritten inside
    a file, and the member list and its order are preserved exactly as
    setuptools produced them.
    """
    original = sdist.read_bytes()
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(fileobj=io.BytesIO(original), mode="r:gz") as archive:
        for info in archive.getmembers():
            payload = archive.extractfile(info)
            members.append((info, payload.read() if payload is not None else None))

    buffer = io.BytesIO()
    # mtime=0 in the gzip header: it records when compression happened, which
    # is not a property of the source.
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as gz:
        # GNU rather than the PAX default. PAX writes a `././@PaxHeader`
        # member carrying the fields ustar cannot hold, and the entry that
        # ends up in there is `mtime` WITH SUB-SECOND PRECISION — so the
        # record was one byte longer or shorter depending on how many decimals
        # the fractional part needed, and the archive changed size between two
        # builds of the same tree. Setting `mtime` alone did not help: the
        # float lives in `pax_headers`, which is copied along with the member.
        # GNU emits no such records and still handles long names.
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as archive:
            for info, payload in members:
                info.mtime = epoch
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                # Directories executable, files not. Whatever umask the build
                # machine had is not part of the release either.
                info.mode = 0o755 if info.isdir() else 0o644
                info.pax_headers.clear()
                archive.addfile(info, io.BytesIO(payload) if payload is not None else None)

    sdist.write_bytes(buffer.getvalue())


def artifacts() -> tuple[Path, Path]:
    wheels = sorted(DIST.glob("*.whl"))
    sdists = sorted(DIST.glob("*.tar.gz"))
    if not wheels or not sdists:
        raise SystemExit(f"no encuentro wheel y sdist en {DIST}")
    return wheels[-1], sdists[-1]


def verify() -> int:
    """Reads both archives back and asserts what must be inside them."""
    wheel, sdist = artifacts()
    problems: list[str] = []

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for name in LEGAL:
            if not any(n.endswith(f"/{name}") or n == name for n in names):
                problems.append(f"{wheel.name}: falta {name}")
        if not any(n.endswith("profiles/default.json") for n in names):
            problems.append(f"{wheel.name}: falta el perfil por defecto")
        if not any(n.endswith("naviscoord/mcp_server.py") for n in names):
            problems.append(f"{wheel.name}: falta el servidor MCP")

    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        for name in LEGAL:
            if not any(n.endswith(f"/{name}") for n in names):
                problems.append(f"{sdist.name}: falta {name}")
        # The README is what `project.readme` points at. Without it the build
        # emits a warning nobody reads and publishes an empty description.
        if not any(n.endswith("/README.md") for n in names):
            problems.append(f"{sdist.name}: falta README.md")
        # The sdist must be able to rebuild the wheel, and that needs the
        # packaged profile — not just the code.
        if not any(n.endswith("naviscoord/profiles/default.json") for n in names):
            problems.append(f"{sdist.name}: falta el perfil por defecto")

    # Neither archive may name the machine that built it. The add-in has its
    # own guard; this is the same rule for the Python half, where the usual
    # leak is an absolute path baked into generated metadata.
    for archive_path, names in (
        (wheel, zipfile.ZipFile(wheel).namelist()),
        (sdist, tarfile.open(sdist).getnames()),
    ):
        for entry in names:
            if entry.startswith("/") or ":" in entry.split("/")[0]:
                problems.append(f"{archive_path.name}: ruta absoluta en «{entry}»")

    for problem in problems:
        print("FALLA " + problem)
    if problems:
        return 1

    print(f"ok  {wheel.name}: LICENSE, NOTICE, perfil y servidor presentes")
    print(f"ok  {sdist.name}: LICENSE y NOTICE presentes")
    return 0


def digests() -> dict[str, str]:
    """SHA-256 de cada artefacto publicable que hay ahora en dist/."""
    found: dict[str, str] = {}
    for path in sorted([*DIST.glob("*.whl"), *DIST.glob("*.tar.gz")]):
        found[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def reproducible(*, isolated: bool = True) -> int:
    """Construye DOS veces desde cero y compara byte a byte.

    Un solo build no dice nada sobre reproducibilidad, y un rebuild
    incremental menos todavía: demuestra que no se recompiló. Esto borra
    `dist/` cada vez —lo hace `build()`— y compara los bytes resultantes.

    El sdist necesitó trabajo para llegar aquí: setuptools honra
    SOURCE_DATE_EPOCH en el wheel pero no en el tar, así que `normalize_sdist`
    reescribe los metadatos de cada miembro. Esta comprobación es la que dice
    si ese trabajo sigue funcionando; sin ella la normalización podía
    romperse y nadie se enteraba hasta comparar dos releases.
    """
    build(isolated=isolated)
    first = digests()
    build(isolated=isolated)
    second = digests()

    names = sorted(set(first) | set(second))
    if not names:
        print("no se generó ningún artefacto que comparar", file=sys.stderr)
        return 1

    differing = []
    for name in names:
        a, b = first.get(name), second.get(name)
        same = a is not None and a == b
        print(f"  {'ok  ' if same else 'DIFIERE'} {name}")
        if not same:
            differing.append(name)
            print(f"       build 1: {a}")
            print(f"       build 2: {b}")

    if differing:
        print(f"NO reproducible: {len(differing)} artefacto(s) difieren entre dos "
              "construcciones del mismo árbol.", file=sys.stderr)
        return 1
    print(f"reproducible: {len(names)} artefacto(s) idénticos byte a byte.")
    return verify()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="solo verificar dist/ existente")
    parser.add_argument("--reproducible", action="store_true",
                        help="construir dos veces y comparar los bytes")
    parser.add_argument("--no-isolation", action="store_true",
                        help="construir con el backend ya instalado, sin tocar la red")
    args = parser.parse_args()

    isolated = not args.no_isolation
    if args.reproducible:
        return reproducible(isolated=isolated)
    if not args.check:
        build(isolated=isolated)
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
