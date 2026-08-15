"""Finding, choosing and validating a Navisworks instance to talk to.

A coordinator routinely has two Navisworks open at once: 2024 holding the
archived model and 2026 holding the live one, or two projects side by side.
The add-in now writes one session file per instance under
``%LOCALAPPDATA%\\NavisCoord\\sessions``; this reads that registry.

The rule this module exists to enforce: **with more than one live instance
and no target chosen, a mutation is refused.** Silently taking the newest one
is how a plan computed against Torre A lands on Torre B, and the operator
only finds out when the groups appear in the wrong file. Reads may still pick
a single unambiguous instance, because reading the wrong model is recoverable
and being unable to look at anything is not.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTRACT = "naviscoord.session/2"

SESSION_ENV = "NAVISCOORD_SESSION"


def runtime_root() -> Path:
    """Where the add-in publishes its handshakes.

    Resolved from ``%LOCALAPPDATA%`` exactly as the add-in resolves it, so a
    redirected profile does not leave the two halves looking in different
    directories.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "NavisCoord"


def sessions_dir() -> Path:
    return runtime_root() / "sessions"


def legacy_session_file() -> Path:
    return runtime_root() / "session.json"


@dataclass(slots=True)
class SessionInfo:
    """One live (or recently live) Navisworks bridge."""

    session_id: str
    port: int
    token: str = ""
    pid: int = 0
    product: str = ""
    addin_version: str = ""
    contract: str = ""
    api_version: str = ""
    document_title: str = ""
    document_fingerprint: str = ""
    document_open: bool = False
    started: str = ""
    heartbeat: str = ""
    process_started: str = ""
    source_file: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def target_id(self) -> str:
        """What a caller passes to `navis_target`.

        The session id rather than the PID: a PID is reused by Windows and
        means nothing after a restart, whereas the session id is minted per
        bridge and appears in every response the bridge produces.
        """
        return self.session_id or f"pid-{self.pid}"

    def to_json(self, *, include_token: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target_id": self.target_id,
            "session_id": self.session_id,
            "pid": self.pid,
            "port": self.port,
            "navisworks": self.product,
            "addin_version": self.addin_version,
            "contract": self.contract,
            "api_version": self.api_version,
            "document_open": self.document_open,
            "document_title": self.document_title,
            "document_fingerprint": self.document_fingerprint,
            "started": self.started,
            "heartbeat": self.heartbeat,
            "process_started": self.process_started,
            "session_file": self.source_file,
        }
        if include_token:
            payload["token"] = self.token
        return payload

    @classmethod
    def from_json(cls, raw: dict[str, Any], source: str = "") -> SessionInfo:
        document = raw.get("document") or {}
        return cls(
            session_id=str(raw.get("session_id", "")),
            port=int(raw.get("port", 0) or 0),
            token=str(raw.get("token", "")),
            pid=int(raw.get("pid", 0) or 0),
            product=str(raw.get("product", "")),
            addin_version=str(raw.get("addin_version", "")),
            contract=str(raw.get("contract", "")),
            api_version=str(raw.get("api_version", "")),
            document_title=str(document.get("title", "")),
            document_fingerprint=str(document.get("fingerprint", "")),
            document_open=bool(document.get("open", False)),
            started=str(raw.get("started", "")),
            heartbeat=str(raw.get("heartbeat", raw.get("started", ""))),
            process_started=str(raw.get("process_started", "")),
            source_file=source,
            raw=raw,
        )


class TargetError(RuntimeError):
    """Raised when no unambiguous instance can be chosen."""

    def __init__(self, message: str, *, hint: str = "", candidates: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.hint = hint
        self.candidates = candidates or []

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": "target_ambiguous", "detail": str(self)}
        if self.hint:
            payload["hint"] = self.hint
        if self.candidates:
            payload["candidates"] = self.candidates
        return payload


def discover(include_dead: bool = False) -> list[SessionInfo]:
    """Every session file on disk, newest first.

    ``NAVISCOORD_SESSION`` short-circuits this entirely and returns that one
    file. It is the documented override for a deployment where the MCP server
    does not run as the interactive user that owns Navisworks — a service
    account, a container, a sandboxed test harness — and without it the server
    reports "no active session" with nowhere else to look.
    """
    override = os.environ.get(SESSION_ENV, "").strip()
    if override:
        path = Path(override)
        if path.is_dir():
            return _read_dir(path, include_dead)
        session = _read_file(path)
        return [session] if session else []

    found = _read_dir(sessions_dir(), include_dead)
    if found:
        return found

    # Pre-registry add-in: one shared session.json. Read it so a new server
    # keeps working against an add-in nobody has updated yet.
    legacy = _read_file(legacy_session_file())
    if legacy is None:
        return []
    # The compatibility pointer is not part of the registry, but it still
    # names a process and must obey the same liveness rule.  A crashed old
    # add-in can leave this file behind forever; treating it as live made a
    # fresh client connect to a dead PID and report a bridge failure instead
    # of the truthful "no active session".  Keep it visible to
    # ``include_dead`` so navis_sessions can diagnose the stale record.
    return [legacy] if include_dead or is_alive(legacy) else []


def _read_dir(directory: Path, include_dead: bool) -> list[SessionInfo]:
    sessions: list[SessionInfo] = []
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError:
        return sessions

    for entry in entries:
        session = _read_file(entry)
        if session is None:
            continue
        if not include_dead and not is_alive(session):
            continue
        sessions.append(session)

    sessions.sort(key=lambda s: (s.heartbeat or s.started), reverse=True)
    return sessions


def _read_file(path: Path) -> SessionInfo | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not raw.get("port"):
        return None
    return SessionInfo.from_json(raw, source=str(path))


def is_alive(session: SessionInfo) -> bool:
    """Whether the owning process still exists.

    PID liveness rather than a heartbeat age: a healthy instance can spend
    twenty minutes appending a federated model on the UI thread, and evicting
    it for missing a heartbeat is exactly the moment you least want the
    handshake to vanish. When the PID is unknown the session is assumed live —
    an old add-in did not record one, and refusing to talk to it would be a
    regression dressed as a safety check.
    """
    if session.pid <= 0:
        return True
    if os.name != "nt":
        try:
            os.kill(session.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True

    # Windows: no os.kill(0). OpenProcess via ctypes is the cheap check that
    # does not need psutil.
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, session.pid)
        if not handle:
            return False
        try:
            if session.process_started and not _same_windows_process(
                kernel32, handle, session.process_started
            ):
                return False
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) == 0:
                return True
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        # Unable to check is not evidence of death: a session whose liveness
        # cannot be established is reported as alive, so a transient ctypes
        # or permission failure never silently deletes a running instance
        # from the registry. Narrow rather than bare, so a genuine bug in
        # this function still surfaces instead of being read as "alive".
        return True


def _same_windows_process(kernel32: Any, handle: Any, recorded: str) -> bool:
    """Reject a recycled PID by comparing its creation timestamp.

    Session records created before the field existed remain compatible.  If
    Windows refuses the timestamp query, liveness still falls back to the PID
    check: inability to prove a mismatch is not evidence that a live bridge
    died.
    """
    try:
        import ctypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ]
        kernel32.GetProcessTimes.restype = ctypes.c_int

        created = FILETIME()
        exited = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ) == 0:
            return True

        expected = datetime.fromisoformat(recorded.replace("Z", "+00:00")).timestamp()
        ticks = (created.high << 32) | created.low
        actual = (ticks - 116_444_736_000_000_000) / 10_000_000
        # Filesystem/JSON formatting and Win32 use different precision.  A
        # two-second window distinguishes process generations without
        # rejecting a timestamp rounded by an older add-in.
        return abs(actual - expected) <= 2.0
    except (OSError, TypeError, ValueError, OverflowError, AttributeError):
        return True


def select(target_id: str = "", *, for_mutation: bool = False) -> SessionInfo:
    """The instance to talk to, or a refusal that names the alternatives.

    `for_mutation` is the whole point of the two modes. Reading the wrong
    model wastes a minute; writing to it costs a coordination cycle, so a
    mutation with two live instances and no chosen target is refused.
    """
    sessions = discover()
    if not sessions:
        raise TargetError(
            "No encuentro ninguna sesión activa de NavisCoord.",
            hint=(
                "Abre Navisworks Manage 2024-2026: el complemento arranca solo. Si el servidor "
                f"MCP corre con otro usuario, apunta {SESSION_ENV} al archivo de sesión real. "
                f"Se buscó en {sessions_dir()}."
            ),
        )

    if target_id:
        wanted = target_id.strip().lower()
        for session in sessions:
            if wanted in {session.target_id.lower(), session.session_id.lower(), str(session.pid)}:
                return session
        raise TargetError(
            f"No hay ninguna sesión con target '{target_id}'.",
            hint="Llama navis_sessions para ver las disponibles.",
            candidates=[s.to_json() for s in sessions],
        )

    if len(sessions) == 1:
        return sessions[0]

    if not for_mutation:
        # Reads pick the most recent and SAY they did, rather than failing on
        # something a human can shrug at.
        return sessions[0]

    raise TargetError(
        f"Hay {len(sessions)} instancias de Navisworks activas y ninguna seleccionada. "
        "No voy a elegir por ti en una operación que modifica el documento.",
        hint=(
            "Elige con navis_target(target_id=...) — los ids están en navis_sessions — "
            "o cierra las que no vayas a usar."
        ),
        candidates=[s.to_json() for s in sessions],
    )


def summary() -> dict[str, Any]:
    """What `navis_sessions` returns."""
    live = discover()
    dead = [s for s in discover(include_dead=True) if not is_alive(s)]
    return {
        "sessions": [s.to_json() for s in live],
        "count": len(live),
        "stale": [s.to_json() for s in dead],
        "registry": str(sessions_dir()),
        "override_env": SESSION_ENV,
        "override_active": bool(os.environ.get(SESSION_ENV, "").strip()),
        "note": (
            "Varias instancias activas: elige una con navis_target antes de mutar."
            if len(live) > 1
            else "Una sola instancia activa: no hace falta elegir target."
            if live
            else "Ninguna instancia activa."
        ),
    }
