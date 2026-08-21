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
import shutil
import secrets
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_identity import (  # noqa: E402
    MANIFEST_NAME,
    READY_NAME,
    RUNTIME_FORMAT,
    Requirement,
    build_manifest,
    check_versions,
    interpreter_fingerprint,
    lock_digest,
    manifest_matches,
    runtime_is_ready,
    runtime_key,
)
from runtime_lock import LockUnavailable, RuntimeLock  # noqa: E402
from runtime_lock_spec import LOCK_PATH as RUNTIME_LOCK_PATH  # noqa: E402
from runtime_lock_spec import install_arguments as lock_install_arguments  # noqa: E402
from runtime_lock_spec import load as load_runtime_lock  # noqa: E402
from runtime_lock_spec import requirements_text as lock_requirements_text  # noqa: E402

# Suffix of a staging directory. Checked before any recursive delete, so a
# cleanup can never reach a path this process did not create.
STAGING_SUFFIX = ".staging-"

# How long to wait for another process to finish provisioning. A first build
# on a slow machine legitimately takes minutes; past this the caller is told
# who is holding it rather than left waiting.
LOCK_TIMEOUT = 900.0

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"

# Kept in step with server/pyproject.toml. Both mcp majors are supported;
# the <3 bound stands because 2.0 showed a major can remove the entry point
# this server imports.
REQUIREMENTS = ["mcp>=1.9,<3", "reportlab>=4.0,<6", "pillow>=10.0"]

# The same bounds, in a form a version can be checked against. `import mcp`
# was the whole validation, and mcp 3.0 imports perfectly — it is a major the
# server cannot use, which is why the requirement says `<3`, and the failure
# arrived at the first real call instead of at startup.
VERSION_CONTRACT = [
    Requirement("mcp", "mcp", minimum=(1, 9), below=(3,)),
    Requirement("reportlab", "reportlab", minimum=(4, 0), below=(6,)),
    Requirement("pillow", "PIL", minimum=(10, 0)),
]

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
    """Plugin-local runtime, keyed by everything that makes one incompatible.

    The key used to be `py{major}{minor}` alone. Two interpreters can share a
    major/minor and be incompatible in every way that matters — x86 and x64,
    CPython and another implementation, two installs whose wheels are not
    interchangeable — and they all resolved to one directory and poisoned each
    other. The plugin version and the dependency set were not in it either, so
    upgrading the plugin reused a venv built for the previous one.

    The readable part is a courtesy; the digest is what makes it correct. It
    is a hash because the interpreter PATH is part of the identity and a
    personal path must never end up in a directory name that could be
    published.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    # Keyed by the LOCK, not by the public ranges. The ranges barely ever
    # change; the pinned versions do, and a runtime built for one set of pins
    # must not be reused for another.
    try:
        material = load_runtime_lock().digest_material()
    except (OSError, ValueError):
        # No readable lock: fall back to the ranges so the path stays
        # computable and `ensure_runtime` can report the real problem.
        material = REQUIREMENTS
    key = runtime_key(plugin_version=_server_version(), requirements=material)
    return Path(base) / "NavisCoord" / "runtime" / key


def installed_versions(python: "Path | None" = None) -> dict:
    """Distribution -> installed version, or None. Never raises.

    `importlib.metadata` rather than `module.__version__`: the dunder is
    optional, sometimes stale, and absent from exactly the packages whose
    version matters here. When a different interpreter is being inspected the
    question has to be asked inside it, which is why this shells out.
    """
    names = [r.distribution for r in VERSION_CONTRACT]
    if python is None:
        from importlib import metadata

        found = {}
        for name in names:
            try:
                found[name] = metadata.version(name)
            except Exception:  # noqa: BLE001 - absent is an answer, not an error
                found[name] = None
        return found

    probe = (
        "import json\n"
        "from importlib import metadata\n"
        "out = {}\n"
        f"for n in {names!r}:\n"
        "    try: out[n] = metadata.version(n)\n"
        "    except Exception: out[n] = None\n"
        "print(json.dumps(out))\n"
    )
    code, detail = _run([str(python), "-c", probe], timeout=60)
    if code != 0:
        return {name: None for name in names}
    try:
        return json.loads(detail.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {name: None for name in names}


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

    What changed, and why it is not a refactor: this used to create the venv
    directly on the final path and install floating ranges into it. Two MCP
    clients starting together both saw a missing venv and both ran pip into
    the same directory, and the loser wrote half a package tree over the
    winner's — a runtime that imported well enough to look finished. And with
    no lock file, every user got whatever the index held that morning.

    So the sequence is: take an exclusive lock, look again in case the other
    process already published, build in a staging directory nobody else can
    see, verify it there, write READY last, and publish with a single rename.
    A failure at any point leaves the previous runtime untouched and no READY
    anywhere.
    """
    supported, why = python_is_supported()
    if not supported:
        return None, why

    if str(SERVER) not in sys.path:
        sys.path.insert(0, str(SERVER))

    # Already viable in-process: every required module imports AND reports a
    # version inside its range. Importability alone let mcp 3 through.
    if not missing_modules():
        wrong = check_versions(VERSION_CONTRACT, installed_versions())
        if not wrong:
            return Path(sys.executable), ""

    try:
        lock_file = load_runtime_lock()
    except (OSError, ValueError) as exc:
        return None, (
            "No se pudo leer el lock de dependencias "
            f"({RUNTIME_LOCK_PATH}): {exc}.\n"
            "El launcher no instala rangos flotantes: sin lock no aprovisiona."
        )

    final = runtime_dir()
    plugin = _server_version()

    # Already published and still valid? Nothing to build.
    usable, _ = runtime_is_usable(final, plugin, lock_file)
    if usable:
        return venv_python(final / "venv"), ""

    guard = RuntimeLock(final.parent / f"{final.name}.lock", final.name, plugin)
    try:
        guard.acquire(timeout=LOCK_TIMEOUT)
    except LockUnavailable as exc:
        return None, (
            f"No se pudo aprovisionar el runtime: {exc.detail}\n"
            f"Código: {exc.code}\nRuntime: {final}"
        )

    try:
        # Looked at again INSIDE the lock. Between the check above and the
        # acquisition, the process we queued behind may have finished the
        # exact runtime we were about to build.
        usable, _ = runtime_is_usable(final, plugin, lock_file)
        if usable:
            return venv_python(final / "venv"), ""

        staging = final.parent / f"{final.name}{STAGING_SUFFIX}{secrets.token_hex(6)}"
        try:
            python, problem = _build_runtime(staging, final, plugin, lock_file)
            if problem:
                return None, problem
            return python, ""
        finally:
            # Only our own staging, never a sibling belonging to another run.
            _discard_staging(staging, final.parent)
    finally:
        guard.release()


def _build_runtime(
    staging: Path, final: Path, plugin: str, lock_file: Any
) -> tuple[Path | None, str]:
    """Builds, verifies and publishes one runtime. Lock already held."""
    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            return None, f"El staging «{staging}» ya existía: se aborta en vez de reutilizarlo."
        staging.mkdir(parents=True)
    except OSError as exc:
        return None, f"No se pudo crear «{staging}»: {exc}"
    _harden(staging)

    venv = staging / "venv"
    code, detail = _run([sys.executable, "-m", "venv", str(venv)], timeout=300)
    if code != 0:
        return None, f"No se pudo crear el entorno virtual en «{venv}»:\n{detail}"

    python = venv_python(venv)

    # Exactly the lock, and nothing the lock does not name. `--no-deps`
    # matters: the lock already carries the transitive closure, and letting
    # pip resolve again would reintroduce the drift the lock removes.
    requirements = staging / "requirements.txt"
    requirements.write_text(lock_requirements_text(lock_file), encoding="utf-8")
    code, detail = _run(
        [str(python), "-m", "pip", "install", *lock_install_arguments(lock_file),
         "-r", str(requirements)],
        timeout=1800,
    )
    if code != 0:
        return None, (
            f"pip falló instalando el lock en «{venv}»:\n{detail}\n"
            "El lock fija versiones exactas; si una no está disponible para este "
            "Python, regenera el lock con scripts/refresh_runtime_lock.py."
        )

    absent = missing_modules(python)
    if absent:
        return None, (
            "El runtime se instaló pero estos módulos siguen sin importar: "
            + ", ".join(absent) + f".\nIntérprete: {python}"
        )

    installed = installed_versions(python)
    wrong = check_versions(VERSION_CONTRACT, installed)
    if wrong:
        return None, (
            "El runtime tiene versiones incompatibles:\n  - " + "\n  - ".join(wrong)
            + f"\nIntérprete: {python}"
        )

    # READY last, and only now. A directory that exists proves somebody
    # started; only this proves somebody finished.
    # `digest_material()` and not `requirements()`: the material includes the
    # accepted hashes, so adding a hash to a pin invalidates the runtime the
    # way it should. Writing one formula here and comparing another on reuse
    # made every published runtime fail its own validation.
    manifest = build_manifest(
        key=final.name,
        plugin_version=plugin,
        requirements=lock_file.digest_material(),
        installed=installed,
        interpreter=interpreter_fingerprint(executable=str(python)),
    )
    (staging / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    (staging / READY_NAME).write_text(
        json.dumps({"format": RUNTIME_FORMAT, "runtime_key": final.name},
                   sort_keys=True), encoding="utf-8")

    # One rename. Until it lands, nothing outside this process can see the
    # runtime at all.
    try:
        os.replace(staging, final)
    except OSError as exc:
        if final.exists():
            # Somebody published while we built. Theirs wins; ours is
            # discarded rather than merged, because merging two package trees
            # is how a half-installed runtime is made.
            usable, why = runtime_is_usable(final, plugin, lock_file)
            if usable:
                return venv_python(final / "venv"), ""
            return None, (
                f"Otro proceso publicó un runtime en «{final}» que no es compatible "
                f"({why}). No se mezclan los dos árboles."
            )
        return None, f"No se pudo publicar el runtime en «{final}»: {exc}"

    # Re-read from its published location: the rename either produced a
    # complete runtime or it did not, and asking is cheaper than assuming.
    usable, why = runtime_is_usable(final, plugin, lock_file)
    if not usable:
        return None, f"El runtime publicado no valida: {why}"
    return venv_python(final / "venv"), ""


def runtime_is_usable(root: Path, plugin: str, lock_file: Any) -> tuple[bool, str]:
    """Whether an existing runtime may be reused, with the reason when not."""
    ready, why = runtime_is_ready(root)
    if not ready:
        return False, why

    try:
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"el manifest no se pudo leer: {exc}"

    matches, why = manifest_matches(
        manifest, key=root.name, plugin_version=plugin,
        requirements=lock_file.digest_material())
    if not matches:
        return False, why

    python = venv_python(root / "venv")
    if not python.exists():
        return False, "falta el intérprete del entorno virtual"

    # Names importing is not enough, and never was.
    wrong = check_versions(VERSION_CONTRACT, installed_versions(python))
    if wrong:
        return False, "; ".join(wrong)
    return True, ""


def _discard_staging(staging: Path, root: Path) -> None:
    """Removes OUR staging directory, and refuses anything else.

    Every condition is checked before a recursive delete: inside the runtime
    root, named by our own scheme, and not a reparse point somebody redirected
    somewhere else. A recursive delete on a path that fails any of them is how
    a cleanup removes a user's directory.
    """
    # El reparse se mira en la ruta SIN resolver: `resolve()` sigue el
    # junction y dejaría la comprobación mirando el destino del enlace.
    if staging.is_symlink() or _is_reparse_point(staging):
        return
    try:
        resolved = staging.resolve()
        expected = root.resolve()
    except OSError:
        return
    if not str(resolved).startswith(str(expected)):
        return
    if STAGING_SUFFIX not in resolved.name:
        return
    if resolved == expected:
        return
    try:
        if resolved.is_symlink() or _is_reparse_point(resolved):
            return
    except OSError:
        return
    shutil.rmtree(resolved, ignore_errors=True)


def _is_reparse_point(path: Path) -> bool:
    """Whether a directory is a junction or symlink. Windows-aware."""
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
        "dependencias_publicas": REQUIREMENTS,
        "lock": str(RUNTIME_LOCK_PATH),
        "arreglo": (
            # Desde el lock, no desde los rangos: instalar rangos es
            # exactamente lo que el lock existe para evitar, y aconsejarlo
            # aqui devolveria al usuario al problema original.
            "Crea el entorno a mano, desde el lock de versiones exactas:\n"
            f'  "{sys.executable}" -m venv "{venv_dir()}"\n'
            "  python scripts/refresh_runtime_lock.py --help   # como regenerarlo\n"
            f"  El lock vigente esta en: {RUNTIME_LOCK_PATH}"
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
