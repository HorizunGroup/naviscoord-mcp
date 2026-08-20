"""Builds the wheel and sdist with LICENSE and NOTICE actually inside them.

setuptools refuses to reach outside the project directory for files, and both
licence texts live at the repository root — one level above `server/`. Told
nothing, it produces a wheel with no licence at all, which is the single thing
a redistributable must never do and the single thing nobody checks by hand.

So: copy them in, build, verify by reading the archives back, clean up. The
verification is the point — a build step that copies files and trusts the
result is how the problem returns.

    python scripts/build_artifacts.py            # build and verify
    python scripts/build_artifacts.py --check    # verify existing artifacts
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import shutil
import subprocess
import sys
import tarfile
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


def stage_legal() -> list[Path]:
    """Copies the root licence files and README into the package directory."""
    staged: list[Path] = []
    collisions = [SERVER / name for name in STAGED if (SERVER / name).exists()]
    if collisions:
        names = ", ".join(str(path) for path in collisions)
        raise SystemExit(
            "no se prepara el build porque ya existen archivos locales que "
            f"serían sobrescritos y después borrados: {names}"
        )
    for name in STAGED:
        source = ROOT / name
        if not source.is_file():
            raise SystemExit(f"falta {name} en la raíz del repositorio")
        target = SERVER / name
        shutil.copy2(source, target)
        staged.append(target)
    return staged


def unstage(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


def build() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    staged = stage_legal()
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = source_date_epoch()
    print(f"SOURCE_DATE_EPOCH={env['SOURCE_DATE_EPOCH']}")
    try:
        subprocess.run(
            [sys.executable, "-m", "build", "--outdir", str(DIST)],
            cwd=SERVER, check=True, env=env,
        )
    finally:
        # Always removed: a stray copy at server/LICENSE looks like a second
        # source of truth and drifts from the real one.
        unstage(staged)

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
        if any("/tests/" in n or n.endswith("/tests") for n in names):
            problems.append(
                f"{sdist.name}: incluye tests internos sin sus recursos de repositorio"
            )

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="solo verificar dist/ existente")
    args = parser.parse_args()

    if not args.check:
        build()
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
