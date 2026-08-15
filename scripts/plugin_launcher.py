"""Plugin entry point: runs the MCP server, provisioning its runtime if needed.

Both Claude Code and Codex launch a plugin's MCP server with a bare `python`
and no setup step, so the launcher has to make the process viable on its own.
The engine itself is dependency-free; only the MCP transport and the PDF need
anything, and those go into a plugin-local virtual environment rather than
the user's site-packages — a plugin should not mutate the interpreter it was
handed.

To be precise about what that means: this does NOT ship or install a Python.
It creates a venv from whatever interpreter it was started with, and if that
interpreter is too old it says so and stops, because there is nothing else it
honestly can do.

If the runtime cannot be provisioned, the launcher does NOT die. A dead server
shows up in both clients as an unexplained absence, so it degrades into a
minimal MCP exposing one tool that reports exactly what failed and how to fix
it. An error you can read beats a server that is silently missing.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"

# Kept in step with server/pyproject.toml. Both mcp majors are supported;
# the <3 bound stands because 2.0 showed a major can remove the entry point
# this server imports.
REQUIREMENTS = ["mcp>=1.9,<3", "reportlab>=4.0,<6", "pillow>=10.0"]

# Every module that must import for the server to actually work, mapped to
# the distribution that provides it. Checking only `mcp` was the bug: an
# interpreter with mcp but no reportlab passed the check, started the real
# server, and failed on the first navis_pdf_report with a bare ImportError —
# after the user had already run a twenty-minute analysis.
RUNTIME_MODULES = {
    "mcp": "mcp",
    "reportlab": "reportlab",
    "PIL": "pillow",
}

# Matches requires-python in server/pyproject.toml. 3.10 is the floor because
# the codebase uses PEP 604 unions (`X | None`) at runtime in dataclass
# annotations, which 3.9 raises on.
MIN_PYTHON = (3, 10)

PROTOCOL_VERSION = "2025-06-18"


def runtime_dir() -> Path:
    """Plugin-local runtime, keyed by interpreter version.

    Wheels built for 3.12 are not importable from 3.13, and a plugin shared
    between two Python installs would otherwise poison itself.
    """
    tag = f"py{sys.version_info.major}{sys.version_info.minor}"
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "NavisCoord" / "runtime" / tag


def venv_dir() -> Path:
    return runtime_dir() / "venv"


def venv_python(venv: Path | None = None) -> Path:
    target = venv or venv_dir()
    return target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(command: list[str], timeout: int = 900) -> tuple[int, str]:
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            # stdin must never be inherited: this process speaks JSON-RPC
            # over it, and a prompt would corrupt the stream.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return 1, f"«{command[0]}» superó {timeout}s."
    except Exception as exc:  # noqa: BLE001 - reported verbatim
        return 1, f"No se pudo ejecutar: {type(exc).__name__}: {exc}"
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-8:]
        return done.returncode, "\n".join(tail)
    return 0, ""


# ------------------------------------------------------------- inspection


def python_is_supported(version: tuple[int, ...] | None = None) -> tuple[bool, str]:
    """Whether this interpreter can run the server at all."""
    current = version or sys.version_info[:2]
    if tuple(current) >= MIN_PYTHON:
        return True, ""
    wanted = ".".join(str(p) for p in MIN_PYTHON)
    running = ".".join(str(p) for p in current)
    return False, (
        f"NavisCoord necesita Python {wanted} o superior y este intérprete es {running} "
        f"({sys.executable}). No puedo instalar un Python por ti: instala uno compatible y "
        "vuelve a registrar el plugin apuntando a él."
    )


def missing_modules(interpreter: Path | None = None) -> list[str]:
    """Which required distributions are absent from an interpreter.

    Probed by importing in a subprocess when the interpreter is not this one,
    because a venv's packages are not on this process's sys.path.
    """
    if interpreter is None or Path(interpreter).resolve() == Path(sys.executable).resolve():
        missing = []
        for module, distribution in RUNTIME_MODULES.items():
            try:
                __import__(module)
            except ImportError:
                missing.append(distribution)
        return missing

    probe = (
        "import json,sys\n"
        "missing=[]\n"
        f"for module, dist in {RUNTIME_MODULES!r}.items():\n"
        "    try:\n"
        "        __import__(module)\n"
        "    except ImportError:\n"
        "        missing.append(dist)\n"
        "sys.stdout.write(json.dumps(missing))\n"
    )
    try:
        done = subprocess.run(
            [str(interpreter), "-c", probe],
            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
        )
        if done.returncode != 0:
            return sorted(RUNTIME_MODULES.values())
        return list(json.loads(done.stdout or "[]"))
    except Exception:  # noqa: BLE001
        return sorted(RUNTIME_MODULES.values())


def runtime_is_private(path: Path) -> tuple[bool, str]:
    """Whether a runtime directory is safe to execute from.

    An interpreter other users can write to is an interpreter other users
    can replace, and this one is launched automatically at every session
    start. On POSIX that is a mode check. On Windows the equivalent question
    is answered by the ACL, and rather than parse one here the directory is
    created under the per-user LOCALAPPDATA — which the add-in also hardens
    with an explicit DACL — so the honest answer is "as private as that".
    """
    if not path.exists():
        return True, ""

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or ""
        try:
            inside = local and Path(path).resolve().is_relative_to(Path(local).resolve())
        except (OSError, ValueError):
            inside = False
        if not inside:
            return False, (
                f"El runtime está en «{path}», fuera de %LOCALAPPDATA%. Ahí no puedo garantizar "
                "que otro usuario no pueda reemplazar el intérprete que se ejecuta solo en cada "
                "sesión. Borra esa carpeta y deja que se recree, o define LOCALAPPDATA."
            )
        return True, ""

    try:
        mode = path.stat().st_mode
    except OSError as exc:
        return False, f"No se pudieron leer los permisos de «{path}»: {exc}"

    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False, (
            f"«{path}» es escribible por otros usuarios ({stat.filemode(mode)}). Un intérprete "
            "que otro puede reemplazar se ejecuta solo en cada arranque. Corrígelo con "
            f"`chmod 700 {path}` o bórralo para que se recree."
        )
    return True, ""


def _harden(path: Path) -> None:
    """0700 on POSIX. On Windows LOCALAPPDATA already scopes it per user."""
    if os.name == "nt":
        return
    try:
        path.chmod(0o700)
    except OSError:
        pass


# ------------------------------------------------------------ provisioning


def ensure_runtime() -> tuple[Path | None, str]:
    """Returns (interpreter to run the server with, failure message).

    A real virtual environment, not `pip install --target`. Targeted installs
    look simpler and then fail in ways that have nothing to do with this
    project: the directory lands on sys.path before it exists so the import
    system caches it as absent, and packages with post-install steps —
    pywin32 above all — never place their DLLs and die on `pywintypes`. A
    venv is what the packaging ecosystem actually supports.
    """
    supported, why = python_is_supported()
    if not supported:
        return None, why

    if str(SERVER) not in sys.path:
        sys.path.insert(0, str(SERVER))

    # Already viable in-process: every required module imports, not just mcp.
    if not missing_modules():
        return Path(sys.executable), ""

    base = runtime_dir()
    private, problem = runtime_is_private(base)
    if not private:
        return None, problem

    venv = venv_dir()
    python = venv_python(venv)

    if not python.exists():
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return None, f"No se pudo crear «{base}»: {exc}"
        _harden(base)
        code, detail = _run([sys.executable, "-m", "venv", str(venv)], timeout=300)
        if code != 0:
            return None, f"No se pudo crear el entorno virtual en «{venv}»:\n{detail}"
        _harden(venv)

    absent = missing_modules(python)
    if absent:
        code, detail = _run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check",
             "--no-input", *REQUIREMENTS]
        )
        if code != 0:
            return None, f"pip falló al instalar el runtime en «{venv}»:\n{detail}"

    # Verified by importing, not by trusting pip's exit code.
    still_absent = missing_modules(python)
    if still_absent:
        return None, (
            "El runtime se instaló pero estos módulos siguen sin importar: "
            + ", ".join(still_absent)
            + f".\nIntérprete: {python}"
        )

    return python, ""


# ------------------------------------------------------- degraded fallback


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def failure_report(reason: str) -> dict[str, Any]:
    """What the one degraded tool returns.

    Built separately from the loop so it can be asserted without driving a
    JSON-RPC conversation — and because building it inside the loop is how it
    came to reference a `lib_dir()` that had been deleted, turning the whole
    fallback into a NameError. The degraded server crashed instead of
    explaining, which is the one thing it exists not to do.
    """
    return {
        "error": "runtime_no_disponible",
        "detail": reason,
        "interprete": sys.executable,
        "python": ".".join(str(p) for p in sys.version_info[:3]),
        "minimo_requerido": ".".join(str(p) for p in MIN_PYTHON),
        "runtime": str(venv_dir()),
        "dependencias": REQUIREMENTS,
        "arreglo": (
            "Crea el entorno a mano:\n"
            f'  "{sys.executable}" -m venv "{venv_dir()}"\n'
            f'  "{venv_python()}" -m pip install ' + " ".join(f'"{r}"' for r in REQUIREMENTS)
        ),
        "nota": (
            "NavisCoord no instala un Python propio: crea un entorno virtual a partir del "
            "intérprete con el que lo arrancó el cliente. Si ese intérprete no sirve, hay que "
            "cambiarlo en la configuración del plugin."
        ),
    }


DEGRADED_TOOL = {
    "name": "navis_install_status",
    "description": "Por qué NavisCoord no pudo arrancar y cómo arreglarlo.",
    "inputSchema": {"type": "object", "properties": {}},
}


def handle_message(message: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC turn of the degraded server. Pure, so it is testable."""
    request_id = message.get("id")
    if request_id is None:
        return None  # notification

    method = message.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "horizun-navis-mcp", "version": _server_version()},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [DEGRADED_TOOL]}}
    if method == "tools/call":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "content": [{"type": "text", "text": json.dumps(detail, ensure_ascii=False)}],
            "isError": True,
        }}
    return {"jsonrpc": "2.0", "id": request_id, "result": {}}


def _server_version() -> str:
    """The real version, read from the package, with a literal fallback.

    Hardcoding "0.1.0" here is how the degraded server came to announce a
    version the project had left behind two releases earlier.
    """
    try:
        sys.path.insert(0, str(SERVER))
        from naviscoord import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"


def serve_failure(reason: str) -> int:
    """A minimal MCP whose only job is to explain why the real one is absent."""
    detail = failure_report(reason)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_message(message, detail)
        if response is not None:
            _send(response)
    return 1


def main() -> int:
    python, failure = ensure_runtime()
    if failure or python is None:
        return serve_failure(failure or "runtime no disponible")

    if python.resolve() != Path(sys.executable).resolve():
        # Hand the stream to the runtime interpreter. Handles are inherited,
        # so the JSON-RPC conversation flows straight through this shim; it
        # only waits and forwards the exit code. os.execv would be tidier but
        # is unreliable with inherited pipes on Windows.
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(SERVER)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
        )
        return subprocess.run([str(python), __file__], env=env).returncode

    from naviscoord.mcp_server import main as run_server
    run_server()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
