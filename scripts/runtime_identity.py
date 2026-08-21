"""Which runtime is this, and may two processes build it at once?

Three defects lived in the old provisioning, and they compound.

The runtime was keyed by ``py{major}{minor}`` alone. Two interpreters can share
a major/minor and be incompatible in every way that matters: x86 and x64,
CPython and PyPy, two installs whose wheels are not interchangeable. They all
resolved to one directory and poisoned each other. The plugin version and the
dependency lock were not in the key either, so upgrading the plugin reused a
venv built for the previous one.

Validation was ``import mcp``. An interpreter with ``mcp 3.0`` installed — a
major the server cannot use, which is why the requirement says ``<3`` — imports
perfectly and then fails at the first call.

And two clients starting together both saw a missing venv and both ran ``pip
install`` into the same directory. The loser wrote half a package tree over the
winner's.

Everything here is pure except :func:`probe_versions`, so the rules can be
asserted without building a venv or racing a real process.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

# Bumped when the SHAPE of a runtime changes — a new required package, a
# different layout — so an existing runtime is rebuilt rather than reused with
# a manifest that no longer describes it.
RUNTIME_FORMAT = 3

# Written last, and the only file that means "this runtime is usable". A venv
# directory that exists proves somebody started; only READY proves somebody
# finished.
READY_NAME = "READY"
MANIFEST_NAME = "runtime-manifest.json"
LOCK_NAME = ".provision.lock"
STAGING_PREFIX = ".staging-"


class RuntimeError_(RuntimeError):
    """A runtime that must not be used, with a machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------- identity


def interpreter_fingerprint(
    *,
    executable: str | None = None,
    version: tuple[int, int, int] | None = None,
    implementation: str | None = None,
    bits: int | None = None,
    machine: str | None = None,
) -> dict[str, str]:
    """Everything about an interpreter that makes wheels incompatible.

    The executable path is included because two installs of the same version
    are still two installs: one may have a patched stdlib, a different SSL
    build, or site-packages the other cannot see.
    """
    info = sys.version_info
    return {
        "executable": str(executable if executable is not None else sys.executable),
        "version": ".".join(str(p) for p in (version or (info.major, info.minor, info.micro))),
        "implementation": implementation or platform.python_implementation(),
        # 8 * struct.calcsize("P") is the pointer width, which is what decides
        # whether a wheel loads — `platform.architecture()` reads the
        # executable's header and lies inside a venv.
        "bits": str(bits if bits is not None else 8 * struct.calcsize("P")),
        "machine": machine or platform.machine(),
        "platform": sys.platform,
    }


def lock_digest(requirements: Iterable[str]) -> str:
    """A stable digest of the exact dependency set.

    Sorted, so the order a requirements file happens to list things in does
    not change the identity of the runtime it produces.
    """
    material = "\n".join(sorted(str(r).strip() for r in requirements if str(r).strip()))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def runtime_key(
    *,
    plugin_version: str,
    requirements: Iterable[str],
    interpreter: dict[str, str] | None = None,
) -> str:
    """The directory name for one exact runtime.

    A hash rather than a readable path: the executable path is part of the
    identity and a personal path must never appear in anything published. The
    readable parts stay in the manifest INSIDE the runtime, which is local.
    """
    info = interpreter or interpreter_fingerprint()
    material = json.dumps(
        {
            "format": RUNTIME_FORMAT,
            "plugin": str(plugin_version),
            "lock": lock_digest(requirements),
            "interpreter": info,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    # A readable prefix helps a human recognise the directory; the digest is
    # what makes it correct.
    return f"py{info['version'].split('.')[0]}{info['version'].split('.')[1]}-{info['bits']}-{digest}"


# ------------------------------------------------------------- validation


@dataclass(frozen=True)
class Requirement:
    """One dependency and the versions this build accepts."""

    distribution: str
    module: str
    minimum: tuple[int, ...] | None = None
    below: tuple[int, ...] | None = None

    def accepts(self, version: str) -> bool:
        parsed = parse_version(version)
        if parsed is None:
            return False
        if self.minimum is not None and parsed < self.minimum:
            return False
        if self.below is not None and parsed >= self.below:
            return False
        return True

    def describe(self) -> str:
        parts = []
        if self.minimum is not None:
            parts.append(">=" + ".".join(str(p) for p in self.minimum))
        if self.below is not None:
            parts.append("<" + ".".join(str(p) for p in self.below))
        return self.distribution + (",".join(parts) if parts else "")


def parse_version(text: str) -> tuple[int, ...] | None:
    """The numeric release part of a version string, or nothing.

    Deliberately small: only the release segment is compared, so ``2.0.0rc1``
    reads as ``(2, 0, 0)``. That is enough to answer "is this the major we
    refuse?", which is the question being asked, and it avoids pulling in a
    packaging library the launcher must run without.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    head = text.strip().split("+", 1)[0]
    numbers: list[int] = []
    for part in head.replace("-", ".").split("."):
        digits = ""
        for char in part:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        numbers.append(int(digits))
    return tuple(numbers) or None


def check_versions(
    requirements: Iterable[Requirement],
    installed: dict[str, str | None],
) -> list[str]:
    """Which requirements the installed set fails, and why.

    ``installed`` maps distribution name to version, with ``None`` meaning "not
    installed at all". Importability is a separate question the caller asks
    separately — a module that imports and reports a version outside the range
    is exactly the case ``import mcp`` could not see.
    """
    problems: list[str] = []
    for requirement in requirements:
        version = installed.get(requirement.distribution)
        if version is None:
            problems.append(f"{requirement.distribution}: no está instalado")
            continue
        if not requirement.accepts(version):
            problems.append(
                f"{requirement.distribution} {version} está fuera del rango "
                f"{requirement.describe()}"
            )
    return problems


# ------------------------------------------------------------------ lock


@dataclass
class LockRecord:
    """Who is building, so a stale lock can be told from a live one."""

    pid: int
    started: str
    key: str
    acquired: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "process_started": self.started,
            "runtime_key": self.key,
            "acquired_at": self.acquired,
        }


def lock_is_stale(
    record: dict[str, Any],
    *,
    now: float,
    timeout: float,
    liveness: str,
) -> tuple[bool, str]:
    """Whether a lock may be broken. Three-valued liveness, on purpose.

    A lock whose owner is ALIVE is never stale, however old — a first
    provisioning on a slow machine legitimately takes minutes. A lock whose
    owner cannot be queried is never stale either: ACCESS_DENIED means the
    process exists and we may not ask about it, and breaking the lock there
    would let two builders into the same directory, which is the whole thing
    being prevented.

    Only a confirmed absence, or an age past the timeout with no owner
    information at all, is stale.
    """
    if liveness == "alive":
        return False, "el proceso que construye sigue vivo"
    if liveness == "unknown":
        return False, (
            "no se puede confirmar el proceso dueño del lock "
            "(¿permisos?): no se rompe"
        )

    age = now - float(record.get("acquired_at") or 0.0)
    if liveness == "dead":
        return True, f"el proceso {record.get('pid')} ya no existe"
    if age > timeout:
        return True, f"el lock lleva {age:.0f} s sin dueño identificable"
    return False, "todavía dentro del tiempo de espera"


# --------------------------------------------------------------- manifest


def build_manifest(
    *,
    key: str,
    plugin_version: str,
    requirements: Iterable[str],
    installed: dict[str, str | None],
    interpreter: dict[str, str] | None = None,
) -> dict[str, Any]:
    """What this runtime is, written inside it."""
    return {
        "format": RUNTIME_FORMAT,
        "runtime_key": key,
        "plugin_version": plugin_version,
        "lock_digest": lock_digest(requirements),
        "requirements": sorted(str(r) for r in requirements),
        "installed": {k: v for k, v in sorted(installed.items())},
        "interpreter": interpreter or interpreter_fingerprint(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def manifest_matches(
    manifest: dict[str, Any],
    *,
    key: str,
    plugin_version: str,
    requirements: Iterable[str],
) -> tuple[bool, str]:
    """Whether an existing runtime still describes what is being asked for.

    Checked even though the key already encodes all of it: the key names a
    directory, and a directory can be copied, restored from a backup, or left
    behind by a version that wrote a different manifest format. The manifest
    is the runtime's own account of itself, and disagreeing with it is reason
    enough to rebuild.
    """
    if not isinstance(manifest, dict):
        return False, "el manifest no es un objeto"
    if manifest.get("format") != RUNTIME_FORMAT:
        return False, (
            f"el manifest es del formato {manifest.get('format')} y este build "
            f"usa {RUNTIME_FORMAT}"
        )
    if manifest.get("runtime_key") != key:
        return False, "el manifest describe otro runtime"
    if str(manifest.get("plugin_version")) != str(plugin_version):
        return False, (
            f"el runtime se construyó para el plugin {manifest.get('plugin_version')} "
            f"y este es {plugin_version}"
        )
    if manifest.get("lock_digest") != lock_digest(requirements):
        return False, "las dependencias declaradas cambiaron desde que se construyó"
    return True, ""


def runtime_is_ready(root: Path) -> tuple[bool, str]:
    """Whether a runtime directory is complete.

    READY is written last, so its absence means a build started and did not
    finish — a partial venv that must never be used, however plausible its
    contents look.
    """
    if not root.is_dir():
        return False, "el runtime no existe"
    if not (root / READY_NAME).is_file():
        return False, "falta READY: la construcción no terminó"
    if not (root / MANIFEST_NAME).is_file():
        return False, "falta el manifest del runtime"
    return True, ""
