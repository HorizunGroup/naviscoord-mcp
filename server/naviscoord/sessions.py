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
from datetime import datetime, timezone
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
    process_started: str = ""
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
            "process_started": self.process_started,
            "heartbeat": self.heartbeat,
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
            process_started=str(raw.get("process_started", "")),
            heartbeat=str(raw.get("heartbeat", raw.get("started", ""))),
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
        if not isinstance(raw, dict) or not raw.get("port"):
            return None
        session = SessionInfo.from_json(raw, source=str(path))
        if not 1 <= session.port <= 65535:
            return None
        if session.pid < 0:
            return None
        return session
    except (OSError, ValueError, TypeError, OverflowError):
        return None


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

    return _windows_session_alive(session)


def _windows_session_alive(session: SessionInfo) -> bool:
    live, actual_started = _windows_process_info(session.pid)
    if live is False:
        return False
    if live is None:
        # Access denied means the process may be elevated; it is not evidence
        # that the session is dead and must not silently remove the target.
        return True
    if session.process_started and actual_started:
        recorded = _parse_utc(session.process_started)
        actual = _parse_utc(actual_started)
        if recorded is not None and actual is not None:
            # Windows reuses PIDs. A different creation time means this is a
            # different process wearing the old session's numeric id.
            if abs((recorded - actual).total_seconds()) > 5:
                return False
    return True


def _parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError):
        return None


def _windows_process_info(pid: int) -> tuple[bool | None, str]:
    """Return liveness and creation time; ``None`` means access/OS unknown."""
    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return (None, "") if ctypes.get_last_error() == 5 else (False, "")
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None, ""
            if code.value != 259:
                return False, ""

            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user)
            ):
                return True, ""
            ticks = (created.high << 32) | created.low
            unix_seconds = (ticks - 116_444_736_000_000_000) / 10_000_000
            started = datetime.fromtimestamp(unix_seconds, timezone.utc).isoformat()
            return True, started
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError, OverflowError):
        return None, ""


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
