"""Build a standalone Windows MCP runtime from the hash-locked environment.

Run with the build environment's Python. Dependencies must already match the
runtime lock; this command never resolves or installs them behind your back.
"""
from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("The Navisworks desktop runtime must be built on Windows.")
    lock = json.loads((ROOT / "scripts/runtime-lock.json").read_text())
    for package in lock["packages"]:
        actual = metadata.version(package["name"])
        if actual != package["version"] or not package.get("hashes"):
            raise SystemExit(f"Unverified dependency: {package['name']} {actual}")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir", "--console", "--name", "naviscoord-mcp",
        "--distpath", str(ROOT / "dist/portable"),
        "--workpath", str(ROOT / "dist/pyinstaller-work"),
        "--specpath", str(ROOT / "dist"),
        "--paths", str(ROOT / "server"),
        "--add-data", str(ROOT / "server/naviscoord/profiles") + ";naviscoord/profiles",
        "--collect-data", "reportlab", "--collect-submodules", "PIL",
        "--copy-metadata", "mcp", str(ROOT / "scripts/frozen_entry.py"),
    ], cwd=ROOT, check=True)
    report = {
        "schema": "naviscoord.runtime-build/1", "python": sys.version.split()[0],
        "pyinstaller": metadata.version("pyinstaller"),
        "packages": [{"name": p["name"], "version": p["version"]} for p in lock["packages"]],
    }
    (ROOT / "dist/portable/naviscoord-mcp/runtime-build.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    runtime = ROOT / "dist/portable/naviscoord-mcp"
    sys.path.insert(0, str(ROOT / "server"))
    from naviscoord import __version__
    files = {p.relative_to(runtime).as_posix(): hashlib.file_digest(p.open("rb"), "sha256").hexdigest()
             for p in sorted(runtime.rglob("*")) if p.is_file() and p.name != "runtime-files.json"}
    (runtime / "runtime-files.json").write_text(json.dumps({"version": __version__, "files": files}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
