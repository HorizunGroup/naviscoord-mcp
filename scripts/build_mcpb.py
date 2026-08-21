"""Empaqueta NavisCoord como MCP Bundle (.mcpb) para Claude Desktop.

Un `.mcpb` es un zip con el servidor y un `manifest.json` que lo describe, y
es la única forma en que un servidor MCP **local** entra al directorio de
conectores de Anthropic: el portal de conectores solo acepta servidores
remotos, y NavisCoord necesita el Navisworks que está corriendo en ESTA
máquina.

Dos decisiones que no son obvias:

**No vendoriza las dependencias dentro del zip.** El bundle arranca
`scripts/plugin_launcher.py`, el mismo punto de entrada que ya usan los
plugins de Claude Code y Codex, que crea un entorno virtual propio y lo
instala desde `runtime-lock.json`. Meter reportlab y pillow dentro del zip
sería un segundo mecanismo de aprovisionamiento —con sus propias ruedas por
plataforma y su propia forma de quedar desactualizado— para resolver un
problema que ya está resuelto y probado. Un solo camino de arranque es
también un solo camino que puede fallar.

**La lista de tools se lee del servidor vivo, no se escribe a mano.** El
manifiesto declara las 50 tools con su descripción; una lista copiada a mano
queda desfasada en la primera release y nadie lo nota hasta que el revisor
compara. Aquí se importa el servidor y se listan de verdad.

Uso:

    python scripts/build_mcpb.py            # arma dist/mcpb/ y el manifiesto
    python scripts/build_mcpb.py --pack     # además empaqueta el .mcpb

`--pack` exige el CLI `mcpb` en el PATH (`npm install -g @anthropic-ai/mcpb`).
Si no está, el script deja el árbol listo y dice el comando exacto que falta,
en vez de instalar nada por su cuenta.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
STAGE = ROOT / "dist" / "mcpb"

REPO = "https://github.com/HorizunGroup/naviscoord-mcp"
PRIVACY = f"{REPO}/blob/main/docs/PRIVACY.md"

# Lo que el launcher necesita para arrancar, y nada más. El zip que se
# distribuye no es una copia del repositorio: los tests, el add-in y los
# scripts de release no tienen por qué viajar a la máquina de nadie.
LAUNCHER_FILES = [
    "scripts/plugin_launcher.py",
    "scripts/runtime_identity.py",
    "scripts/runtime_lock.py",
    "scripts/runtime_lock_spec.py",
    "scripts/runtime-lock.json",
]

LEGAL_FILES = [
    "README.md",
    "LICENSE",
    "NOTICE",
    "docs/PRIVACY.md",
    "assets/icon.png",
]


def version() -> str:
    """La versión del paquete, leída de donde vive de verdad."""
    sys.path.insert(0, str(SERVER))
    from naviscoord import __version__

    return __version__


def tools() -> list[dict[str, str]]:
    """Las tools registradas, tal como las anuncia el servidor.

    Se listan con su `title` de las anotaciones cuando lo tienen, porque es el
    texto pensado para que lo lea una persona; la descripción larga es para el
    modelo y no cabe en una ficha de directorio.
    """
    sys.path.insert(0, str(SERVER))
    from naviscoord.mcp_server import mcp

    listed = asyncio.run(mcp.list_tools())
    out = []
    for tool in sorted(listed, key=lambda t: t.name):
        annotations = getattr(tool, "annotations", None)
        title = (getattr(annotations, "title", "") or "").strip()
        summary = (tool.description or "").strip().splitlines()[0]
        out.append({"name": tool.name, "description": title or summary})
    return out


def manifest(release: str) -> dict:
    return {
        "manifest_version": "0.3",
        "name": "naviscoord",
        "display_name": "NavisCoord",
        "version": release,
        # La ficha del directorio la lee todo el mundo, así que va en inglés,
        # igual que el README. Las tools siguen describiéndose en español,
        # que es el idioma en que trabaja quien las usa.
        "description": (
            "Autodesk Navisworks coordination: prioritized clashes, root "
            "causes, work plans and reports."
        ),
        "long_description": (
            "NavisCoord connects an AI client to the Navisworks already open "
            "on this machine. It reads how the federated model is organized, "
            "builds search sets and a clash matrix from a company profile, "
            "runs the tests, and — this is what separates it from a list of "
            "clashes — filters expected contacts, collapses repeated clashes "
            "into real issues, scores each one with its justification, and "
            "identifies the systemic root causes behind them.\n\n"
            "From there come the work plan, the coordination matrix, the "
            "written report, and a PDF with a verified image per clash.\n\n"
            "The model never leaves the machine: the analysis runs locally "
            "and only the summary travels to the client. Every write to the "
            "document is two-step, requires the fingerprint of the document "
            "you meant, and reports only what it verified by reading back."
        ),
        "icon": "assets/icon.png",
        "author": {"name": "HorizunGroup", "url": "https://github.com/HorizunGroup"},
        "repository": {"type": "git", "url": f"{REPO}.git"},
        "homepage": REPO,
        "documentation": f"{REPO}#readme",
        "support": f"{REPO}/issues",
        "license": "MIT",
        "privacy_policies": [PRIVACY],
        "keywords": [
            "navisworks",
            "bim",
            "clash-detection",
            "coordination",
            "aec",
            "construction",
        ],
        "server": {
            "type": "python",
            "entry_point": "scripts/plugin_launcher.py",
            "mcp_config": {
                "command": "python",
                "args": ["${__dirname}/scripts/plugin_launcher.py"],
                "env": {
                    # Vacío significa «solo la carpeta de exportación por
                    # defecto», que es exactamente lo que hace paths.py con
                    # una variable vacía. No hace falta un caso especial.
                    "NAVISCOORD_OUTPUT_ROOTS": "${user_config.output_root}",
                    "PYTHONIOENCODING": "utf-8",
                },
            },
        },
        "user_config": {
            "output_root": {
                "type": "directory",
                "title": "Carpeta de salida",
                "description": (
                    "Dónde puede escribir NavisCoord los informes, imágenes y "
                    "exportaciones. Si lo dejas vacío usa "
                    "%LOCALAPPDATA%\\NavisCoord\\exports."
                ),
                "required": False,
            }
        },
        "tools": tools(),
        "compatibility": {
            # Navisworks Manage es solo Windows, y sin él este servidor no
            # tiene con qué hablar. Declararlo multiplataforma sería ofrecerle
            # a un usuario de macOS una instalación que no puede funcionar.
            "platforms": ["win32"],
            "runtimes": {"python": ">=3.10"},
        },
    }


def stage(release: str) -> Path:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "scripts").mkdir(parents=True)

    for relative in LAUNCHER_FILES + LEGAL_FILES:
        source = ROOT / relative
        destination = STAGE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    # El paquete Python entero, sin caches: un __pycache__ de otra versión de
    # intérprete dentro del zip es peso muerto en el mejor caso.
    shutil.copytree(
        SERVER / "naviscoord",
        STAGE / "server" / "naviscoord",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    (STAGE / "manifest.json").write_text(
        json.dumps(manifest(release), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return STAGE


def pack() -> int:
    """Empaqueta con el CLI oficial, si está. Si no, dice cuál falta."""
    executable = shutil.which("mcpb")
    if executable is None:
        print(
            "El CLI `mcpb` no está en el PATH, así que el árbol quedó listo "
            "pero sin empaquetar.\n"
            "Instálalo y vuelve a correr con --pack:\n"
            "    npm install -g @anthropic-ai/mcpb\n"
            f"    mcpb pack {STAGE}",
        )
        return 1
    return subprocess.call([executable, "pack", str(STAGE)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", action="store_true", help="empaquetar el .mcpb")
    arguments = parser.parse_args()

    release = version()
    staged = stage(release)
    listed = json.loads((staged / "manifest.json").read_text(encoding="utf-8"))
    print(f"NavisCoord {release}: {len(listed['tools'])} tools en {staged}")

    if arguments.pack:
        return pack()
    print("Sin empaquetar (usa --pack cuando quieras el .mcpb).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
