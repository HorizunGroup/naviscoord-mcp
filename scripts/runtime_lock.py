"""One builder per runtime, across processes.

The defect this closes: two MCP clients starting together both saw a missing
venv and both ran ``pip install`` into the same directory. The loser wrote half
a package tree over the winner's, and the result imported well enough to look
finished.

A ``threading.Lock`` cannot help — the two builders are separate processes, and
often separate *clients*. Neither can "if the directory exists, someone else is
building": between the check and the create there is a window, and that window
is the whole bug. The primitive that has no window is an exclusive create, so
that is what this uses.

Ownership is three-valued for the same reason liveness is elsewhere in this
project. A lock whose owner cannot be queried — ACCESS_DENIED, a client running
elevated — is NOT stale: the process is there and we may not ask about it, and
breaking the lock would put two builders back in the same directory.

Standard library only. This runs before any dependency is installed, so it
cannot import one.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

LOCK_FORMAT = 1

# Why an acquisition failed. The caller shows a different sentence for each,
# because the actions are different: wait, ask an administrator, or repair.
BUSY_ALIVE = "builder_alive"
BUSY_UNKNOWN = "builder_unknown"
CORRUPT = "lock_corrupt"
TIMEOUT = "lock_timeout"
DENIED = "lock_denied"


class LockUnavailable(RuntimeError):
    """The lock could not be taken, with a reason the caller can act on."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Owner:
    """Who holds the lock. Enough to tell a live owner from a recycled PID."""

    pid: int
    started: str
    host: str
    nonce: str
    key: str
    plugin: str
    acquired: float

    def to_json(self) -> dict[str, Any]:
        return {
            "format": LOCK_FORMAT,
            "pid": self.pid,
            "process_started": self.started,
            "host": self.host,
            "nonce": self.nonce,
            "runtime_key": self.key,
            "plugin_version": self.plugin,
            "acquired_at": self.acquired,
            "acquired_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.acquired)),
        }

    @staticmethod
    def from_json(raw: Any) -> "Owner | None":
        if not isinstance(raw, dict) or raw.get("format") != LOCK_FORMAT:
            return None
        try:
            return Owner(
                pid=int(raw["pid"]),
                started=str(raw.get("process_started") or ""),
                host=str(raw.get("host") or ""),
                nonce=str(raw["nonce"]),
                key=str(raw.get("runtime_key") or ""),
                plugin=str(raw.get("plugin_version") or ""),
                acquired=float(raw.get("acquired_at") or 0.0),
            )
        except (KeyError, TypeError, ValueError):
            return None


def process_start_time(pid: int) -> str | None:
    """When a process started, or None when it cannot be read.

    The distinction that makes PID reuse detectable. ``None`` is not "it is
    dead" — it is "this question has no answer here", which the caller must
    treat as unknown.
    """
    if os.name != "nt":
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[-1].split()
            return fields[19].decode("ascii")   # starttime, in clock ticks
        except (OSError, IndexError):
            return None

    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return None

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        rest = [wintypes.FILETIME() for _ in range(3)]
        ok = kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), *[ctypes.byref(f) for f in rest])
        if not ok:
            return None
        return str((int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime))
    finally:
        kernel32.CloseHandle(handle)


def classify_owner(
    owner: Owner,
    *,
    host: str | None = None,
    start_of: Callable[[int], str | None] | None = None,
    exists: Callable[[int], bool | None] | None = None,
) -> str:
    """``alive`` / ``dead`` / ``unknown``. The injectable parts are for tests.

    A lock written on another machine — a shared drive, a roaming profile — is
    always ``unknown``: this host cannot see that process at all, and calling
    it dead would break a lock somebody else is holding.
    """
    this_host = host if host is not None else platform.node()
    if owner.host and this_host and owner.host != this_host:
        return "unknown"

    present = (exists or _pid_exists)(owner.pid)
    if present is None:
        return "unknown"
    if not present:
        return "dead"

    actual = (start_of or process_start_time)(owner.pid)
    if actual is None:
        # It exists and we cannot read its start time. Could be the same
        # process, could be a recycled id — and guessing either way is worse
        # than saying so.
        return "unknown"
    if not owner.started:
        return "unknown"
    return "alive" if actual == owner.started else "dead"


def _pid_exists(pid: int) -> bool | None:
    if pid <= 0:
        return None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
    return process_start_time(pid) is not None or _windows_pid_exists(pid)


def _windows_pid_exists(pid: int) -> bool | None:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return None
    ERROR_ACCESS_DENIED = 5
    ERROR_INVALID_PARAMETER = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    code = ctypes.get_last_error()
    if code == ERROR_ACCESS_DENIED:
        return True            # it exists; we may not ask about it
    if code == ERROR_INVALID_PARAMETER:
        return False
    return None


def mint_owner(key: str, plugin: str) -> Owner:
    pid = os.getpid()
    return Owner(
        pid=pid,
        started=process_start_time(pid) or "",
        host=platform.node(),
        nonce=secrets.token_hex(8),
        key=key,
        plugin=plugin,
        acquired=time.time(),
    )


def read_owner(path: Path) -> tuple[Owner | None, str]:
    """The lock's contents, or why they cannot be trusted."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "el lock ya no existe"
    except OSError as exc:
        return None, f"no se pudo leer el lock: {exc}"
    except ValueError:
        return None, "el lock no es JSON válido"
    owner = Owner.from_json(raw)
    return (owner, "") if owner else (None, "el lock no tiene la forma esperada")


class RuntimeLock:
    """Exclusive-create lock, released only by the process that took it."""

    def __init__(self, path: Path, key: str, plugin: str) -> None:
        self.path = Path(path)
        self.key = key
        self.plugin = plugin
        self.owner: Owner | None = None

    def acquire(self, *, timeout: float = 600.0, poll: float = 0.1) -> "RuntimeLock":
        """Take the lock, or explain precisely why not.

        The create is ``O_CREAT | O_EXCL``: it either creates the file or
        fails, with no moment in between where two processes both believe the
        path is free. An ``exists()`` check followed by a create has exactly
        that moment, which is the race being removed.
        """
        deadline = time.monotonic() + timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)

        while True:
            owner = mint_owner(self.key, self.plugin)
            payload = json.dumps(owner.to_json(), sort_keys=True).encode("utf-8")
            try:
                handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                self._consider_breaking(deadline)
                time.sleep(poll)
                continue
            except PermissionError:
                # Windows answers a file marked for deletion with
                # ACCESS_DENIED rather than "already exists". Contention, not
                # a permissions problem — but if it persists past the deadline
                # it is reported as denied rather than retried forever.
                if time.monotonic() > deadline:
                    raise LockUnavailable(
                        DENIED, f"no se pudo crear el lock en «{self.path}»") from None
                time.sleep(poll)
                continue

            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                self._force_remove()
                raise LockUnavailable(DENIED, f"no se pudo escribir el lock: {exc}") from exc

            self.owner = owner
            return self

    def _consider_breaking(self, deadline: float) -> None:
        """Break the lock only when its owner is demonstrably gone."""
        owner, problem = read_owner(self.path)
        if owner is None:
            if problem == "el lock ya no existe":
                return            # the holder finished; the next create wins
            # Unreadable — but not necessarily corrupt. `O_EXCL` creates the
            # file EMPTY and the payload is written immediately afterwards;
            # those two steps are not one atomic act, and a competitor that
            # looks in between reads zero bytes. Reporting that as corruption
            # turned the most ordinary interleaving there is — two clients
            # starting together — into "revísalo a mano", and it did it
            # instantly, without honouring the timeout the caller asked for.
            #
            # So an unreadable lock means KEEP WAITING. Only one that is still
            # unreadable when the deadline expires is genuinely corrupt, and
            # even then it is never deleted automatically: a lock nobody can
            # read may still be held by a live process whose write was
            # interrupted, and removing it would let a second builder in.
            if time.monotonic() > deadline:
                raise LockUnavailable(
                    CORRUPT,
                    f"el lock de «{self.path}» sigue ilegible tras esperar ({problem}). "
                    "No se elimina automáticamente: revísalo a mano.")
            return

        state = classify_owner(owner)
        if state == "dead":
            # The only automatic recovery. Age is not evidence — a first
            # provisioning on a slow machine legitimately takes minutes.
            self._force_remove()
            return
        if time.monotonic() > deadline:
            if state == "alive":
                raise LockUnavailable(
                    BUSY_ALIVE,
                    f"otro proceso ({owner.pid} en {owner.host}) lleva construyendo el "
                    f"runtime desde {owner.to_json()['acquired_utc']} y sigue vivo. "
                    "Espera a que termine.")
            raise LockUnavailable(
                BUSY_UNKNOWN,
                f"hay un lock del proceso {owner.pid} en {owner.host} y no se puede "
                "confirmar si sigue vivo (¿permisos, otra sesión?). No se rompe: "
                "hacerlo dejaría dos constructores sobre el mismo directorio.")

    def _force_remove(self, *, attempts: int = 20, pause: float = 0.01) -> bool:
        """Delete the lock file, insisting briefly. True if it is gone.

        One `unlink()` inside a bare `except OSError: pass` was not enough on
        Windows. A competitor polling the lock has it OPEN to read the owner,
        and a delete against an open handle either fails with a sharing
        violation or goes "delete pending" — the name stays visible until the
        last handle closes. The exception was swallowed, `release()` reported
        success, and the lock file outlived every process that knew about it.

        The next run recovers (the owner is dead, so the lock is broken), but
        an orphan lock is still a file nobody can explain sitting next to a
        runtime, and the operator meets it before the recovery does.

        A reader's handle lives for microseconds, so retrying briefly closes
        the window. It stays bounded: never blocking on something that is not
        going to clear.
        """
        for remaining in range(attempts, 0, -1):
            try:
                self.path.unlink()
                return True
            except FileNotFoundError:
                return True
            except OSError:
                if remaining == 1:
                    return not self.path.exists()
                time.sleep(pause)
        return not self.path.exists()

    def release(self) -> bool:
        """Give the lock back — only if it is still ours.

        The nonce check is what makes that provable. Without it, a builder
        whose lock had been broken (because it looked dead) would delete the
        lock of whoever took over, and both would run.
        """
        if self.owner is None:
            return False
        current, _ = read_owner(self.path)
        if current is None or current.nonce != self.owner.nonce:
            # Somebody else holds it now. Removing it would be removing THEIR
            # lock.
            self.owner = None
            return False
        removed = self._force_remove()
        self.owner = None
        # The return value now MEANS the file is gone. Reporting True while a
        # lock file survives is what let an orphan pass unnoticed.
        return removed

    def __enter__(self) -> "RuntimeLock":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()
