"""Refuses source that reintroduces a forbidden internal marker.

Some identifiers belong to internal deployments and must never appear in this
repository: project codes, shared-parameter prefixes, deployment-specific
names. The realistic failure is not malice but a routine copy that carries one
across — and once pushed, that is public forever.

The obvious guard is a list of the forbidden words. That guard cannot be used
here, because **the list would itself publish the words**, in the repository,
in the workflow file, and in every CI log that echoes the step. A denylist in
plaintext is the leak it is trying to prevent.

So the markers are stored as **salted SHA-256 digests of their normalised
form**, in `.github/markers.denylist`, together with the length of the term.
The scanner reconstructs candidates from the text being checked and compares
digests. A digest cannot be read back into a word, and a failure names the
file and the line but never the value.

What is checked, per token:

* the token itself (`internalname`);
* its camelCase / PascalCase parts, so `InternalNameRibbon` is caught by the
  entry for `internalname`;
* its hyphen and underscore parts, so `some-internal-file` is caught;
* its prefixes at the lengths present in the denylist, which is how a
  parameter prefix like `XYZ_` is caught inside `XYZ_SomeField`.

A local, private denylist in PLAINTEXT may be supplied out of band for
convenience (`--plain FILE`, or `MARKERS_PLAIN_FILE`); it is never read from
the repository and never written to it.

    python scripts/check_markers.py                 # scan tracked files
    python scripts/check_markers.py --add-plain FOO # print the digest line
    python scripts/check_markers.py --selftest      # exercise the detection
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DENYLIST = ROOT / ".github" / "markers.denylist"

# A fixed, public salt. It does not need to be secret: its job is to stop a
# generic rainbow table of common words from resolving these digests, not to
# authenticate anything. Changing it invalidates the whole denylist.
SALT = b"naviscoord/markers/v1\x00"

TOKEN = re.compile(r"[A-Za-z0-9_-]+")
CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

# Binary and generated content is checked by the artifact guard instead; a
# digest scan over compiled output produces noise, not findings.
SKIP_SUFFIX = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".whl",
    ".dll", ".pdb", ".exe", ".nwd", ".nwf", ".nwc", ".rvt", ".ifc", ".dwg",
}
SKIP_PARTS = {".git", "dist", "obj", "bin", "_build", "node_modules", "__pycache__"}


def digest(value: str) -> str:
    return hashlib.sha256(SALT + value.strip().lower().encode("utf-8")).hexdigest()


def load_denylist(path: Path = DENYLIST) -> tuple[set[str], set[int]]:
    """Returns the digests and the term lengths they were built from."""
    digests: set[str] = set()
    lengths: set[int] = set()
    if not path.is_file():
        return digests, lengths
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise SystemExit(f"{path.name}: línea mal formada: se esperaba «<sha256> <longitud>»")
        digests.add(parts[0].lower())
        lengths.add(int(parts[1]))
    return digests, lengths


def load_plain(path: Path | None) -> set[str]:
    """An optional PRIVATE plaintext list, never read from the repository."""
    if path is None or not path.is_file():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def candidates(token: str, lengths: set[int]) -> set[str]:
    """Every form of a token a marker could be hiding in."""
    out: set[str] = {token}
    out.update(CAMEL.findall(token))
    out.update(p for p in re.split(r"[-_]", token) if p)
    for length in lengths:
        if len(token) >= length:
            out.add(token[:length])
    return {c.lower() for c in out if c}


def scan_text(text: str, digests: set[str], lengths: set[int],
              plain: set[str]) -> list[int]:
    """Line numbers carrying a forbidden marker. Values are never returned."""
    hits: list[int] = []
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        if any(term in lowered for term in plain):
            hits.append(number)
            continue
        for token in TOKEN.findall(line):
            if any(digest(c) in digests for c in candidates(token, lengths)):
                hits.append(number)
                break
    return hits


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                             capture_output=True, text=True, check=True).stdout
        files = [ROOT / p for p in out.split("\n") if p.strip()]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in ROOT.rglob("*") if p.is_file()]
    keep = []
    for p in files:
        if not p.is_file():
            continue
        if p.suffix.lower() in SKIP_SUFFIX:
            continue
        if SKIP_PARTS & set(p.relative_to(ROOT).parts):
            continue
        keep.append(p)
    return keep


def selftest() -> int:
    """Detection exercised with INVENTED markers, never the real ones."""
    fake = ["acmecorp", "zzq_", "internal-profile-name"]
    digests = {digest(f) for f in fake}
    lengths = {len(f) for f in fake}
    failures = 0

    must_hit = [
        "el nombre AcmeCorp aparece aquí",
        "class AcmeCorpRibbon : IPlugin",           # PascalCase compuesto
        "var x = ZZQ_Campo;",                       # prefijo dentro del token
        "ruta/internal-profile-name.json",          # token con guiones
        "ACMECORP en mayúsculas",
    ]
    must_miss = [
        "una línea perfectamente normal",
        "acme corporation no es el marcador",       # separado, no es el token
        "zzqx_ no es el prefijo",                   # prefijo distinto
        "NavisCoord 0.2.2 MIT",
    ]
    for text in must_hit:
        if not scan_text(text, digests, lengths, set()):
            print(f"FALLA  no detectó: {text!r}")
            failures += 1
        else:
            print(f"ok     detectado")
    for text in must_miss:
        if scan_text(text, digests, lengths, set()):
            print(f"FALLA  falso positivo: {text!r}")
            failures += 1
        else:
            print(f"ok     sin falso positivo")

    # A digest must not be reversible to its term by construction, and two
    # different terms must not collide into one entry.
    if len({digest(f) for f in fake}) != len(fake):
        print("FALLA  colisión entre marcadores distintos")
        failures += 1
    else:
        print("ok     cada marcador tiene su propio digest")

    print()
    print(f"Autoprueba: {len(must_hit) + len(must_miss) + 1} casos, {failures} fallos.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Marcadores internos prohibidos.")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--add-plain", metavar="TERM",
                        help="imprime la línea de denylist para un término (no lo guarda)")
    parser.add_argument("--plain", metavar="FILE",
                        help="lista privada en texto plano, fuera del repositorio")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    if args.add_plain:
        term = args.add_plain.strip().lower()
        print(f"{digest(term)} {len(term)}")
        return 0

    digests, lengths = load_denylist()
    plain_path = args.plain or os.environ.get("MARKERS_PLAIN_FILE")
    plain = load_plain(Path(plain_path) if plain_path else None)

    if not digests and not plain:
        print("check_markers: la denylist está vacía; no hay nada que comprobar.")
        return 0

    findings: list[tuple[Path, int]] = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for line in scan_text(text, digests, lengths, plain):
            findings.append((path, line))

    if findings:
        for path, line in findings:
            rel = path.relative_to(ROOT).as_posix()
            # The value is NEVER printed: a CI log is as public as the file.
            print(f"::error file={rel},line={line}::forbidden internal marker")
        print(f"\ncheck_markers: {len(findings)} hallazgo(s).")
        return 1

    print(f"ok  {len(tracked_files())} archivo(s) sin marcadores prohibidos "
          f"({len(digests)} entradas en la denylist).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
