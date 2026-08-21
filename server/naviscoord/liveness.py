"""Is that Navisworks still there? Three answers, not two.

The old check returned a bool, and both of its failure modes were wrong in the
same direction:

* ``OpenProcess`` returning ``NULL`` was read as "dead". It also returns NULL
  for ACCESS_DENIED, which is what happens when the operator runs one
  Navisworks elevated and the MCP server is not. The session was pruned, and
  the running instance became invisible.

* A live PID was read as "the same process". PIDs are recycled within minutes
  on Windows; a session file naming PID 9184 for a Navisworks that closed can
  match a browser that started afterwards, and the client then tries to talk
  to it.

So liveness is ``alive`` / ``dead`` / ``unknown``, and identity is PID *plus*
process creation time. Unknown is a real answer with real consequences: it is
never pruned, never chosen for a mutation, and always shown with its reason.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

ALIVE = "alive"
DEAD = "dead"
UNKNOWN = "unknown"

# Why an answer is what it is. Reported so an operator can act: "access
# denied" and "no such process" both used to arrive as a missing session.
REASON_RUNNING = "process_running"
REASON_NO_PROCESS = "process_not_found"
REASON_EXITED = "process_exited"
REASON_ACCESS_DENIED = "access_denied"
REASON_PID_REUSED = "stale_pid_reused"
REASON_NO_PID = "no_pid_recorded"
REASON_UNSUPPORTED = "cannot_query"
REASON_START_TIME_UNREADABLE = "start_time_unreadable"


@dataclass(frozen=True)
class Liveness:
    """What is known about the process behind a session, and how well."""

    state: str
    reason: str
    detail: str = ""

    @property
    def is_alive(self) -> bool:
        return self.state == ALIVE

    @property
    def prunable(self) -> bool:
        """Only a confirmed absence may be deleted.

        Unknown is never prunable. Deleting a session because the answer could
        not be obtained is how an elevated Navisworks disappears from a
        non-elevated client — the one case where the operator most needs to be
        told what is wrong rather than told there is nothing there.
        """
        return self.state == DEAD

    @property
    def selectable_for_mutation(self) -> bool:
        """Only a confirmed live process may be mutated.

        Reads can proceed against an unknown with a diagnostic; a mutation
        cannot. "Probably the right Navisworks" is not a basis for rebuilding
        somebody's clash tests.
        """
        return self.state == ALIVE

    def to_json(self) -> dict[str, Any]:
        payload = {"liveness": self.state, "reason": self.reason}
        if self.detail:
            payload["liveness_detail"] = self.detail
        return payload


def judge(
    pid: int,
    recorded_start: str,
    *,
    exists: bool | None,
    actual_start: str | None,
    access_denied: bool = False,
) -> Liveness:
    """The decision, with no operating system in it.

    Split from the ctypes work so the rules can be asserted without a real
    process, a real privilege boundary, or a PID that happens to be recycled
    while the test runs.

    ``exists`` is tri-valued on purpose: ``None`` means the question could not
    be answered, which is different from ``False``.
    """
    if pid <= 0:
        # A pre-registry add-in recorded no PID. It is not evidence of life,
        # and it used to be treated as such — `if session.pid <= 0: return
        # True` — so a crashed old add-in stayed "alive" forever.
        return Liveness(UNKNOWN, REASON_NO_PID,
                        "el registro no trae PID: no se puede confirmar el proceso")

    if access_denied:
        return Liveness(UNKNOWN, REASON_ACCESS_DENIED,
                        f"el proceso {pid} existe pero no se pueden leer sus datos "
                        "(¿Navisworks elevado?)")

    if exists is None:
        return Liveness(UNKNOWN, REASON_UNSUPPORTED,
                        f"no se pudo consultar el proceso {pid} en esta plataforma")

    if not exists:
        return Liveness(DEAD, REASON_NO_PROCESS, f"el proceso {pid} ya no existe")

    # It exists. Whether it is the SAME process is a separate question, and
    # the one the old check never asked.
    if not recorded_start:
        return Liveness(UNKNOWN, REASON_START_TIME_UNREADABLE,
                        f"el proceso {pid} existe, pero el registro no guardó su hora "
                        "de arranque: no se puede descartar un PID reciclado")
    if actual_start is None:
        return Liveness(UNKNOWN, REASON_START_TIME_UNREADABLE,
                        f"el proceso {pid} existe, pero no se pudo leer su hora de arranque")

    if _normalise(actual_start) != _normalise(recorded_start):
        return Liveness(
            DEAD, REASON_PID_REUSED,
            f"el PID {pid} está en uso por un proceso que arrancó en {actual_start}, "
            f"no el de {recorded_start}: es otro proceso")

    return Liveness(ALIVE, REASON_RUNNING, f"el proceso {pid} sigue siendo el registrado")


def _normalise(stamp: str) -> str:
    """Compare timestamps as text, tolerating trailing-precision differences."""
    text = (stamp or "").strip().rstrip("Z")
    # Windows FILETIME has 100 ns resolution; an ISO string written by the
    # add-in may carry fewer digits. Comparing to whole seconds is enough to
    # tell two different processes apart and avoids a false "reused" from a
    # rounding difference.
    if "." in text:
        text = text.split(".", 1)[0]
    return text


# ------------------------------------------------------------- the real one


def probe(pid: int, recorded_start: str) -> Liveness:
    """Ask the operating system, then hand the answer to :func:`judge`."""
    if pid <= 0:
        return judge(pid, recorded_start, exists=None, actual_start=None)

    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return judge(pid, recorded_start, exists=True,
                         actual_start=recorded_start or None)
        except ProcessLookupError:
            return judge(pid, recorded_start, exists=False, actual_start=None)
        except PermissionError:
            return judge(pid, recorded_start, exists=True, actual_start=None,
                         access_denied=True)
        except OSError:
            return judge(pid, recorded_start, exists=None, actual_start=None)

    return _probe_windows(pid, recorded_start)


def _probe_windows(pid: int, recorded_start: str) -> Liveness:
    """OpenProcess with the least privilege that answers the question.

    ``PROCESS_QUERY_LIMITED_INFORMATION`` is deliberate: it is granted across
    integrity levels far more often than ``PROCESS_QUERY_INFORMATION``, so an
    ordinary client can still see an elevated Navisworks. When even that is
    refused the answer is unknown, not dead.

    The handle is closed on every path, including the ones that throw.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001 - no ctypes is not a dead process
        return judge(pid, recorded_start, exists=None, actual_start=None)

    ERROR_ACCESS_DENIED = 5
    ERROR_INVALID_PARAMETER = 87
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    # `use_last_error=True` is load-bearing: without it `get_last_error()`
    # returns whatever ctypes last cached, which is usually zero, and the
    # ACCESS_DENIED that distinguishes "elevated Navisworks" from "no such
    # process" is lost. The two answers are opposite and the difference is
    # exactly what this module exists to preserve.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        code = ctypes.get_last_error()
        if code == ERROR_ACCESS_DENIED:
            # The process EXISTS — that is what access denied means — and the
            # old code called this dead.
            return judge(pid, recorded_start, exists=True, actual_start=None,
                         access_denied=True)
        if code == ERROR_INVALID_PARAMETER:
            return judge(pid, recorded_start, exists=False, actual_start=None)
        return judge(pid, recorded_start, exists=None, actual_start=None)

    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel_time), ctypes.byref(user_time),
        )
        if not ok:
            return judge(pid, recorded_start, exists=True, actual_start=None)
        return judge(pid, recorded_start, exists=True,
                     actual_start=_filetime_to_iso(creation))
    finally:
        # Always. A leaked handle keeps a zombie process object alive, and
        # discovery runs on every call.
        kernel32.CloseHandle(handle)


def _filetime_to_iso(filetime: Any) -> str:
    """FILETIME (100 ns since 1601-01-01 UTC) as an ISO-8601 second."""
    import datetime

    value = (int(filetime.dwHighDateTime) << 32) | int(filetime.dwLowDateTime)
    if value <= 0:
        return ""
    seconds = value / 10_000_000
    epoch = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
    stamp = epoch + datetime.timedelta(seconds=seconds)
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "")
