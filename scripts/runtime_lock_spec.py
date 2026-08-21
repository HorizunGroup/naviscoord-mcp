"""The exact versions the launcher provisions, and how they are verified.

``pyproject.toml`` declares public RANGES: what the package is compatible with,
which is the right thing for a library to publish. The launcher provisions a
runtime, which is a different question — it needs one exact answer, the same
one every time, so that two machines running the same plugin version run the
same code.

Resolving ranges at first start meant every user got whatever PyPI had that
morning. A release tested against ``mcp 1.28`` could hand the next user
``mcp 2.4`` on a Tuesday, and nothing recorded that they differed.

Regenerating this file is a deliberate, separate operation — see
``python scripts/refresh_runtime_lock.py`` — and never something the launcher
does on its own. Tests read it as a fixture and never reach the network.

Standard library only: this is imported during bootstrap, before anything is
installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOCK_PATH = Path(__file__).resolve().parent / "runtime-lock.json"

LOCK_SCHEMA = "naviscoord.runtime-lock/1"


@dataclass(frozen=True)
class Pinned:
    """One distribution at one exact version, with the hashes it may come as."""

    name: str
    version: str
    # SHA-256 of every wheel/sdist accepted for this pin. Empty means the lock
    # was generated without hash information, which `verify` reports rather
    # than silently tolerating.
    hashes: tuple[str, ...] = ()
    # PEP 508 marker, when a pin only applies to some interpreters.
    marker: str = ""

    def requirement(self) -> str:
        base = f"{self.name}=={self.version}"
        return f"{base}; {self.marker}" if self.marker else base


@dataclass(frozen=True)
class RuntimeLockFile:
    schema: str
    generated_utc: str
    python_requires: str
    packages: tuple[Pinned, ...]

    @property
    def direct(self) -> tuple[Pinned, ...]:
        return tuple(p for p in self.packages if p.name in DIRECT)

    def requirements(self) -> list[str]:
        return [p.requirement() for p in self.packages]

    def digest_material(self) -> list[str]:
        """What the runtime key hashes. Order-independent by construction."""
        return sorted(
            f"{p.name}=={p.version}#" + ",".join(sorted(p.hashes)) for p in self.packages
        )


# The distributions this project imports directly. Everything else in the lock
# is transitive and is pinned so the tree cannot drift underneath them.
DIRECT = ("mcp", "reportlab", "pillow")


def load(path: Path | None = None) -> RuntimeLockFile:
    """Read the lock. Raises rather than returning a partial one."""
    target = path or LOCK_PATH
    raw = json.loads(target.read_text(encoding="utf-8"))
    if raw.get("schema") != LOCK_SCHEMA:
        raise ValueError(f"lock con esquema inesperado: {raw.get('schema')!r}")

    packages = []
    for entry in raw.get("packages", []):
        packages.append(Pinned(
            name=str(entry["name"]).lower(),
            version=str(entry["version"]),
            hashes=tuple(sorted(str(h) for h in entry.get("hashes", []))),
            marker=str(entry.get("marker") or ""),
        ))
    return RuntimeLockFile(
        schema=raw["schema"],
        generated_utc=str(raw.get("generated_utc") or ""),
        python_requires=str(raw.get("python_requires") or ""),
        packages=tuple(sorted(packages, key=lambda p: p.name)),
    )


def satisfies_range(version: str, specifier: str) -> bool:
    """Whether a pinned version falls inside a public range.

    A deliberately small comparator over ``>=``, ``>``, ``<``, ``<=``, ``==``
    and ``!=``, because this runs before ``packaging`` could be installed. It
    handles what ``pyproject.toml`` actually writes; anything it cannot parse
    it refuses rather than assumes.
    """
    parsed = _release(version)
    if parsed is None:
        return False
    for clause in specifier.split(","):
        clause = clause.strip()
        if not clause:
            continue
        for op in ("<=", ">=", "==", "!=", "<", ">"):
            if clause.startswith(op):
                bound = _release(clause[len(op):].strip())
                if bound is None:
                    return False
                if not _compare(parsed, op, bound):
                    return False
                break
        else:
            return False
    return True


def _release(text: str) -> tuple[int, ...] | None:
    numbers: list[int] = []
    for part in str(text).strip().split("+", 1)[0].replace("-", ".").split("."):
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


def _compare(left: tuple[int, ...], op: str, right: tuple[int, ...]) -> bool:
    # Pad so (1, 9) and (1, 9, 0) compare equal, which is what a version
    # comparison means and what tuple comparison alone gets wrong.
    width = max(len(left), len(right))
    a = left + (0,) * (width - len(left))
    b = right + (0,) * (width - len(right))
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    return a < b


def install_arguments(lock: RuntimeLockFile) -> list[str]:
    """The pip arguments that install exactly this lock and nothing else.

    ``--no-deps`` is load-bearing: the lock already contains the transitive
    closure, and letting pip resolve again would reintroduce the drift the
    lock exists to remove. ``--require-hashes`` refuses any artifact whose
    digest is not listed.
    """
    args = ["--no-deps", "--only-binary", ":all:", "--disable-pip-version-check", "--no-input"]
    if all(p.hashes for p in lock.packages):
        args.append("--require-hashes")
    return args


def requirements_text(lock: RuntimeLockFile) -> str:
    """The lock as a pip requirements file, hashes included."""
    lines = [
        f"# {LOCK_SCHEMA} generado {lock.generated_utc}",
        "# No editar a mano: usar scripts/refresh_runtime_lock.py",
        "",
    ]
    for package in lock.packages:
        entry = package.requirement()
        if package.hashes:
            entry += "".join(f" \\\n    --hash=sha256:{h}" for h in package.hashes)
        lines.append(entry)
    return "\n".join(lines) + "\n"


def verify(lock: RuntimeLockFile, public_ranges: dict[str, str]) -> list[str]:
    """Problems with the lock itself, before anything is installed.

    The important one is the last: a pin outside the range the package
    publishes means the launcher would provision something the project does
    not claim to support, and nothing else in the system would notice.
    """
    problems: list[str] = []
    if lock.schema != LOCK_SCHEMA:
        problems.append(f"esquema inesperado: {lock.schema}")

    seen: set[str] = set()
    for package in lock.packages:
        if package.name in seen:
            problems.append(f"{package.name} aparece dos veces en el lock")
        seen.add(package.name)
        if not _release(package.version):
            problems.append(f"{package.name}: versión ilegible «{package.version}»")

    for name in DIRECT:
        if name not in seen:
            problems.append(f"falta la dependencia directa {name} en el lock")

    for name, specifier in public_ranges.items():
        pin = next((p for p in lock.packages if p.name == name.lower()), None)
        if pin is None:
            continue
        if not satisfies_range(pin.version, specifier):
            problems.append(
                f"{name} {pin.version} está fuera del rango público «{specifier}» "
                "declarado en pyproject.toml")
    return problems
