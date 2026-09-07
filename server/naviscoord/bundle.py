"""The handoff as one generation, published or not at all.

Each artifact was already written atomically — staged next to its destination
and renamed into place — so no consumer could ever read a truncated CSV. What
nothing protected was the *set*. ``write_handoff`` publishes six files in
sequence, and a failure on the third leaves two files from this run and four
from the last one. Power BI joins ``fct_issues.csv`` to
``brg_issue_elements.csv`` on an issue id, so a mixed generation is not a stale
report: it is a report whose rows do not line up, and nothing in it says so.

Replacing a directory atomically is not available on Windows, so the shape is
immutable generations plus one small pointer:

    output/
      generations/
        20260819T143012-8f3a1c/
          coordination_handoff.json
          revit_worklist.json
          fct_issues.csv
          brg_issue_elements.csv
          manifest.json
      current.json          <- the only file that is ever replaced

``current.json`` is written last and atomically. Everything a consumer needs to
know is reachable from it, and until it moves, the previous generation is what
the world sees.
"""

from __future__ import annotations

import hashlib
import errno
import json
import os
import re
import shutil
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

BUNDLE_SCHEMA = "naviscoord.bundle/1"

GENERATIONS_DIR = "generations"
CURRENT_NAME = "current.json"
MANIFEST_NAME = "manifest.json"
STAGING_PREFIX = ".staging-"

# How many finished generations survive. The previous one has to outlive the
# new one being built, so anything below two would break the guarantee this
# module exists for.
DEFAULT_RETENTION = 5

# A generation id is ours, never the model's. A directory named after a
# document title is a directory named by data — and a document called
# "..\\..\\etc" is a directory traversal with a friendly face.
_ID_SHAPE = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")


class BundleError(RuntimeError):
    """Publication refused. The previous generation is untouched."""

    def __init__(self, message: str, *, stage: str = "", detail: Any = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.detail = detail

    def to_json(self) -> dict[str, Any]:
        return {
            "error": "bundle_incomplete",
            "stage": self.stage,
            "detail": str(self),
            "note": "No se publicó nada: la generación anterior sigue vigente.",
        }


def new_generation_id(now: datetime | None = None) -> str:
    """A sortable, unique, data-free identifier."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def is_generation_id(value: str) -> bool:
    """Whether a string is one of ours.

    Checked before it is ever joined to a path. An id that arrives from
    outside — from a stale ``current.json``, from a caller — is a path
    component, and a path component that came from elsewhere is exactly how a
    traversal happens.
    """
    return bool(_ID_SHAPE.match(value or ""))


@dataclass
class Artifact:
    """One file to produce, and how."""

    name: str
    write: Callable[[Path], None]
    kind: str = "json"
    rows: int | None = None


@dataclass
class Published:
    """What a successful publication produced."""

    generation_id: str
    directory: Path
    manifest: dict[str, Any]
    current: Path
    replaced: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "directory": str(self.directory),
            "current": str(self.current),
            "manifest": self.manifest,
            "replaced_generation": self.replaced,
            "complete": True,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_current(root: Path) -> dict[str, Any] | None:
    """The pointer, or nothing. Never raises on a damaged one."""
    pointer = root / CURRENT_NAME
    try:
        raw = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_atomic(path: Path, text: str) -> None:
    """Write via a sibling temp and one rename.

    ``os.replace`` is atomic on Windows and POSIX alike for a single file in
    the same directory, which is the whole reason ``current.json`` is a file
    and not a directory.
    """
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    try:
        # fsync on the WRITE handle. Doing it on a re-opened read handle is an
        # EBADF on Windows, and the point is to get the bytes on the platter
        # before the rename makes them visible.
        with temp.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        # Bounded retry around the rename. The write lock keeps two WRITERS
        # apart, but a READER — another publish taking its "previous
        # generation", a consumer following the pointer — can hold the file
        # open for the instant the replace needs it, and Windows answers that
        # with ACCESS_DENIED rather than blocking. It is transient by
        # construction: nobody holds `current.json` for longer than one small
        # read.
        deadline = time.monotonic() + 5.0
        while True:
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.005)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def publish(
    root: Path,
    artifacts: Iterable[Artifact],
    *,
    metadata: dict[str, Any] | None = None,
    retention: int = DEFAULT_RETENTION,
    generation_id: str | None = None,
) -> Published:
    """Build a whole generation in staging, verify it, then point at it.

    The order is the contract, and each step is where a failure leaves the
    previous generation intact:

    1. build every artifact in a staging directory beside ``generations/``;
    2. verify each file exists and is non-empty where it should be;
    3. hash every file;
    4. write the manifest and re-verify it against the files;
    5. rename staging into ``generations/<id>`` — still invisible to anyone;
    6. replace ``current.json``.

    Only step 6 changes what a consumer sees, and it is one atomic rename of
    one small file.
    """
    root = Path(root)
    generations = root / GENERATIONS_DIR
    generations.mkdir(parents=True, exist_ok=True)

    # `None` means "mint one"; an empty string means the caller passed
    # something and it was wrong. Collapsing the two would silently accept a
    # blank id from a damaged `current.json`.
    gen_id = new_generation_id() if generation_id is None else generation_id
    if not is_generation_id(gen_id):
        raise BundleError(
            f"identificador de generación con forma inesperada: {gen_id!r}",
            stage="generation_id")

    staging = root / f"{STAGING_PREFIX}{gen_id}"
    stage_locks = ExitStack()
    owns_staging = False

    try:
        # Register ownership before a concurrent collector can see the stage.
        # An OS lock survives long builds and is released automatically on crash.
        with _pointer_lock(root):
            if staging.exists() or (generations / gen_id).exists():
                raise BundleError("la generación ya existe", stage="generation_id")
            staging.mkdir(parents=True)
            owns_staging = True
            stage_locks.enter_context(_file_lock(staging / ".writer.lock"))

        # --- 1. build
        planned = list(artifacts)
        for artifact in planned:
            if "/" in artifact.name or "\\" in artifact.name or ".." in artifact.name:
                raise BundleError(
                    f"nombre de artefacto inseguro: {artifact.name!r}", stage="artifact_name")
            artifact.write(staging / artifact.name)

        # --- 2 & 3. verify and hash
        files: list[dict[str, Any]] = []
        for artifact in planned:
            path = staging / artifact.name
            if not path.exists():
                raise BundleError(
                    f"el artefacto {artifact.name} no se escribió", stage="verify")
            size = path.stat().st_size
            entry: dict[str, Any] = {
                "name": artifact.name,
                "kind": artifact.kind,
                "bytes": size,
                "sha256": _sha256(path),
                "encoding": "utf-8-sig" if artifact.kind == "csv" else "utf-8",
            }
            if artifact.rows is not None:
                entry["rows"] = artifact.rows
            files.append(entry)

        # --- 4. manifest, then check it describes what is on disk
        manifest = {
            "schema": BUNDLE_SCHEMA,
            "generation_id": gen_id,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "complete": True,
            "files": files,
        }
        manifest.update(metadata or {})
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        for entry in files:
            path = staging / entry["name"]
            if _sha256(path) != entry["sha256"]:
                raise BundleError(
                    f"{entry['name']} cambió entre el hash y la verificación",
                    stage="manifest_verify")

        # Promotion and the pointer change share the collector's lock. Otherwise
        # retention can remove a completed directory before its pointer lands.
        final = generations / gen_id

        # --- 6. the only visible change
        pointer = {
            "schema": BUNDLE_SCHEMA,
            "generation_id": gen_id,
            # Relative, so the bundle can be copied to a share without every
            # path inside it naming somebody's home directory.
            "path": f"{GENERATIONS_DIR}/{gen_id}",
            "manifest": f"{GENERATIONS_DIR}/{gen_id}/{MANIFEST_NAME}",
            "manifest_sha256": _sha256(staging / MANIFEST_NAME),
            "published_at": manifest["created_at"],
        }
        with _pointer_lock(root):
            previous = (read_current(root) or {}).get("generation_id", "")
            stage_locks.close()
            (staging / ".writer.lock").unlink()
            os.replace(staging, final)
            _write_atomic(root / CURRENT_NAME,
                          json.dumps(pointer, ensure_ascii=False, indent=2))

        published = Published(
            generation_id=gen_id,
            directory=final,
            manifest=manifest,
            current=root / CURRENT_NAME,
            replaced=previous,
        )
    except BundleError:
        stage_locks.close()
        if owns_staging:
            _discard(staging)
        raise
    except Exception as exc:  # noqa: BLE001 - any failure keeps the old generation
        stage_locks.close()
        if owns_staging:
            _discard(staging)
        raise BundleError(
            f"la generación falló y no se publicó: {exc}", stage="build", detail=str(exc)
        ) from exc

    # Retention runs AFTER the pointer moved, and a failure here is not a
    # failure of the publication: the new generation is already current.
    try:
        prune(root, retention=retention)
    except (OSError, BundleError):
        pass
    return published


@contextmanager
def _pointer_lock(root: Path, *, timeout: float = 30.0):
    """Serialize promotion, pointer replacement, retention and verification."""
    with _file_lock(root / (CURRENT_NAME + ".lock"), timeout=timeout):
        yield


@contextmanager
def _file_lock(path: Path, *, timeout: float = 30.0):
    """Cross-process OS lock; never delete or steal a live owner's lock file.

    The persistent file is not evidence of ownership: the kernel lock is.
    Process death releases it, including after a crash during publication.
    """
    if os.name == "nt":
        import msvcrt
    else:
        import fcntl
    deadline = time.monotonic() + timeout
    with path.open("a+b") as handle:
        while True:
            try:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise BundleError("otra operación mantiene el bloqueo",
                                      stage="pointer_lock") from exc
                time.sleep(0.01)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _discard(staging: Path) -> None:
    """Remove a staging directory. Only ever one we created."""
    if not staging.name.startswith(STAGING_PREFIX):
        # Never a recursive delete on a path we did not name.
        return
    try:
        if staging.exists():
            shutil.rmtree(staging)
    except OSError:
        pass


def prune(root: Path, *, retention: int = DEFAULT_RETENTION) -> list[str]:
    """Keep the newest N generations and the current one. Never the current one alone."""
    root = Path(root)
    if not (root / GENERATIONS_DIR).is_dir():
        return []
    with _pointer_lock(root):
        return _prune_locked(root, retention=retention)


def _prune_locked(root: Path, *, retention: int) -> list[str]:
    retention = max(2, int(retention))
    generations = Path(root) / GENERATIONS_DIR
    if not generations.is_dir():
        return []

    current = (read_current(Path(root)) or {}).get("generation_id", "")
    # Only directories whose names WE minted. Anything else in there was not
    # produced by this module and is not ours to delete.
    ours = sorted(
        (d for d in generations.iterdir() if d.is_dir() and is_generation_id(d.name)),
        key=lambda d: d.name,
        reverse=True,
    )

    # The current generation is kept FIRST and counted against the budget.
    # Skipping it inside the loop left one extra directory behind whenever it
    # fell outside the newest N — which happens whenever several generations
    # land in the same second, because the id sorts by uuid after that point.
    keep: list[str] = [current] if current else []
    for directory in ours:
        if len(keep) >= retention:
            break
        if directory.name not in keep:
            keep.append(directory.name)

    removed: list[str] = []
    for directory in ours:
        if directory.name in keep:
            continue
        try:
            shutil.rmtree(directory)
            removed.append(directory.name)
        except OSError:
            continue

    # Orphaned staging directories from a crashed run.
    for entry in Path(root).iterdir():
        if (entry.is_dir() and entry.name.startswith(STAGING_PREFIX)
                and is_generation_id(entry.name[len(STAGING_PREFIX):])):
            try:
                with _file_lock(entry / ".writer.lock", timeout=0):
                    pass
            except (BundleError, OSError):
                continue  # A live writer owns it, regardless of its age.
            _discard(entry)
            if not entry.exists():
                removed.append(entry.name)
    return removed


def verify(root: Path) -> dict[str, Any]:
    """Whether what ``current.json`` points at is intact.

    A consumer's precondition, and the thing that turns a silent mixed
    generation into a refusal: every file named by the manifest must exist and
    hash to what the manifest says.
    """
    root = Path(root)
    if not root.is_dir():
        return {"ok": False, "error": "no_current", "detail": "no hay current.json legible"}
    with _pointer_lock(root):
        return _verify_locked(root)


def _verify_locked(root: Path) -> dict[str, Any]:
    pointer = read_current(root)
    if not pointer:
        return {"ok": False, "error": "no_current", "detail": "no hay current.json legible"}

    gen_id = str(pointer.get("generation_id") or "")
    if not is_generation_id(gen_id):
        return {"ok": False, "error": "bad_generation_id", "detail": gen_id}

    directory = root / GENERATIONS_DIR / gen_id
    manifest_path = directory / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": "manifest_unreadable", "detail": str(exc)}

    declared = str(pointer.get("manifest_sha256") or "")
    if declared and _sha256(manifest_path) != declared:
        return {"ok": False, "error": "manifest_altered",
                "detail": "el manifest no coincide con el hash que declara current.json"}

    problems: list[str] = []
    for entry in manifest.get("files", []):
        path = directory / str(entry.get("name", ""))
        if not path.exists():
            problems.append(f"falta {entry.get('name')}")
            continue
        if _sha256(path) != entry.get("sha256"):
            problems.append(f"{entry.get('name')} no coincide con su hash")

    return {
        "ok": not problems,
        "generation_id": gen_id,
        "directory": str(directory),
        "files": len(manifest.get("files", [])),
        "problems": problems,
        **({"error": "files_altered"} if problems else {}),
    }
