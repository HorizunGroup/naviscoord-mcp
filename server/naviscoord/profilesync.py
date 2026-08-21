"""Keeping both ends judging by the same criteria, across a restart.

The failure this exists for: the operator loads a custom profile, it is pushed
to the add-in, and then Navisworks restarts. The MCP server still holds the
profile in ``STATE.profile``; the new add-in came up with its own on-disk
default. Nothing announced the divergence, so the next ``workflow/rules`` ran
the add-in's criteria while the report cited the server's checksum — two
different answers to "what counts as serious", with one number claiming to
identify both.

The rule here is: check before running, not after. A workflow that discovers
the mismatch in its own result has already applied the wrong criteria.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# What the caller may be told about the two ends.
IN_SYNC = "in_sync"
RESYNCED = "resynced"
OUT_OF_SYNC = "profile_out_of_sync"
UNREACHABLE = "bridge_unavailable"
BLOCKED_BY_JOB = "profile_locked"


@dataclass(frozen=True)
class SyncVerdict:
    """What was found, and whether a profile-dependent route may proceed."""

    state: str
    detail: str = ""
    server_checksum: str = ""
    addin_checksum: str = ""
    session_id: str = ""
    resynced: bool = False

    @property
    def may_run(self) -> bool:
        """Only two states let a workflow start."""
        return self.state in (IN_SYNC, RESYNCED)

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "state": self.state,
            "may_run": self.may_run,
            "server_checksum": self.server_checksum,
            "addin_checksum": self.addin_checksum,
            "session_id": self.session_id,
            "resynced": self.resynced,
        }
        if self.detail:
            payload["detail"] = self.detail
        if not self.may_run:
            payload["error"] = self.state
        return payload


def ensure_profile_in_sync(
    *,
    server_checksum: str,
    canonical: str,
    read_addin: Callable[[], dict[str, Any]],
    push: Callable[[str, str], dict[str, Any]],
    active_job: Callable[[], str] | None = None,
) -> SyncVerdict:
    """Compare the two ends and, if they differ, reinstall what the user chose.

    The re-push is not a new decision: it reinstalls the profile the operator
    already authorised in this session, verifying the checksum afterwards. What
    it must never do is *quietly* run with whichever profile happens to be
    loaded, which is the behaviour being replaced.

    Blocked while a mutating job is pending or running — swapping the criteria
    under a job that was admitted against the old ones is the same defect in the
    other direction.
    """
    if not server_checksum:
        # No profile was ever pushed from here, so the add-in's own default is
        # the only criteria in play and there is nothing to reconcile.
        return SyncVerdict(state=IN_SYNC, detail="el servidor no impuso ningún perfil")

    if active_job is not None:
        busy = active_job()
        if busy:
            return SyncVerdict(
                state=BLOCKED_BY_JOB,
                detail=(
                    f"El trabajo {busy} sigue activo con el perfil vigente. "
                    "No se resincroniza mientras pueda estar mutando."
                ),
                server_checksum=server_checksum,
            )

    try:
        info = read_addin()
    except Exception as exc:  # noqa: BLE001 - the caller decides how to report it
        return SyncVerdict(
            state=UNREACHABLE,
            detail=str(exc),
            server_checksum=server_checksum,
        )

    addin_checksum = str(info.get("checksum") or "")
    session_id = str(info.get("session_id") or "")

    if addin_checksum == server_checksum:
        return SyncVerdict(
            state=IN_SYNC,
            server_checksum=server_checksum,
            addin_checksum=addin_checksum,
            session_id=session_id,
        )

    # They differ. Reinstall the authorised profile and verify, rather than
    # running and mentioning it afterwards.
    try:
        pushed = push(canonical, server_checksum)
    except Exception as exc:  # noqa: BLE001
        return SyncVerdict(
            state=OUT_OF_SYNC,
            detail=f"no se pudo reinstalar el perfil: {exc}",
            server_checksum=server_checksum,
            addin_checksum=addin_checksum,
            session_id=session_id,
        )

    installed = str(pushed.get("checksum") or "")
    if not pushed.get("ok") or installed != server_checksum:
        return SyncVerdict(
            state=OUT_OF_SYNC,
            detail=(
                "El complemento no aceptó el perfil reinstalado "
                f"(declaró {installed or 'nada'} frente a {server_checksum}). "
                "No se ejecuta nada con criterios mezclados."
            ),
            server_checksum=server_checksum,
            addin_checksum=installed or addin_checksum,
            session_id=session_id,
        )

    return SyncVerdict(
        state=RESYNCED,
        detail=(
            f"El complemento tenía {addin_checksum or 'otro perfil'}; se reinstaló "
            f"el que cargaste ({server_checksum})."
        ),
        server_checksum=server_checksum,
        addin_checksum=installed,
        session_id=session_id,
        resynced=True,
    )
