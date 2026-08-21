#!/usr/bin/env python
"""Regenerates ``runtime-lock.json``. A deliberate act, never automatic.

Run this when a dependency needs to move, review the diff, and commit it. The
launcher must never do it: resolving ranges at first start is what gave every
user whatever the index happened to hold that morning, and left nothing
recording that two machines were running different code.

    python scripts/refresh_runtime_lock.py            # from what is installed
    python scripts/refresh_runtime_lock.py --with-hashes   # asks the index

``--with-hashes`` is the only mode that touches the network, and it is opt-in
precisely so that no test can reach it by accident.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_lock_spec import DIRECT, LOCK_PATH, LOCK_SCHEMA, load, verify  # noqa: E402

PUBLIC_RANGES = {"mcp": ">=1.9,<3", "reportlab": ">=4.0,<6", "pillow": ">=10.0"}


def installed_versions() -> dict[str, str]:
    out = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    found: dict[str, str] = {}
    for line in out.splitlines():
        if "==" in line:
            name, version = line.split("==", 1)
            found[name.strip().lower()] = version.strip()
    return found


def transitive_closure(installed: dict[str, str]) -> set[str]:
    """Every distribution the direct ones pull in, read from local metadata.

    From metadata rather than from a resolver: a resolver would go to the
    index, and the whole point of a lock is to describe what was actually
    tested here.
    """
    closure: set[str] = set()
    queue = list(DIRECT)
    while queue:
        name = queue.pop().lower()
        if name in closure:
            continue
        closure.add(name)
        try:
            requires = metadata.requires(name) or []
        except Exception:  # noqa: BLE001 - a distribution with no metadata is a leaf
            requires = []
        for requirement in requires:
            head = requirement
            if ";" in requirement:
                head, marker = requirement.split(";", 1)
                if "extra" in marker:
                    continue      # optional extras are not part of the runtime
            dep = head.split("[")[0]
            for token in ("<", ">", "=", "!", "~", " ", "("):
                dep = dep.split(token)[0]
            dep = dep.strip().lower()
            if dep and dep in installed and dep not in closure:
                queue.append(dep)
    return closure


def fetch_hashes(name: str, version: str) -> list[str]:
    """SHA-256 of every artifact the index offers for one pin. Network."""
    import urllib.request

    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return sorted(
        entry["digests"]["sha256"]
        for entry in payload.get("urls", [])
        if entry.get("digests", {}).get("sha256")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-hashes", action="store_true",
        help="consulta el índice para fijar los SHA-256 (única operación de red)")
    parser.add_argument("--output", type=Path, default=LOCK_PATH)
    args = parser.parse_args()

    installed = installed_versions()
    missing = [name for name in DIRECT if name not in installed]
    if missing:
        print(f"faltan dependencias directas en este intérprete: {', '.join(missing)}",
              file=sys.stderr)
        print("instálalas primero; este script congela lo que hay, no resuelve.",
              file=sys.stderr)
        return 2

    packages = []
    for name in sorted(transitive_closure(installed)):
        if name not in installed:
            continue
        entry = {"name": name, "version": installed[name], "hashes": []}
        if args.with_hashes:
            try:
                entry["hashes"] = fetch_hashes(name, installed[name])
            except Exception as exc:  # noqa: BLE001
                print(f"  aviso: sin hashes para {name}: {exc}", file=sys.stderr)
        packages.append(entry)

    document = {
        "schema": LOCK_SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_requires": ">=3.10",
        "_note": (
            "Versiones exactas que el launcher aprovisiona. Los rangos públicos viven "
            "en server/pyproject.toml y son otra cosa. Regenerar solo con "
            "scripts/refresh_runtime_lock.py; las pruebas lo leen como fixture."
        ),
        "packages": packages,
    }
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    problems = verify(load(args.output), PUBLIC_RANGES)
    for problem in problems:
        print(f"  PROBLEMA: {problem}", file=sys.stderr)
    print(f"{len(packages)} paquetes fijados en {args.output}")
    if not args.with_hashes:
        print("Sin hashes: --require-hashes queda desactivado. Usa --with-hashes "
              "para fijarlos (consulta el índice).")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
