"""Session snapshots, retry policy and profile resynchronisation.

Three failures that share one shape: the client believed something about the
add-in that was true a moment ago.

* ``Bridge.resolve()`` returned the new session while ``self._session`` still
  cached the old one, and ``call()`` used the cached one — so a fingerprint
  could be read from one Navisworks instance and the request sent to another.
* Every HTTP failure collapsed into the same ``BridgeError``, so a caller could
  not tell a malformed payload from a busy host, and a mutation could be
  replayed against a conflict.
* A restart left the server holding the operator's profile and the add-in
  holding its own default, and the next workflow ran the add-in's criteria
  while citing the server's checksum.
"""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from naviscoord.bridge import Bridge, BridgeError
from naviscoord.profilesync import (
    BLOCKED_BY_JOB,
    IN_SYNC,
    OUT_OF_SYNC,
    RESYNCED,
    UNREACHABLE,
    ensure_profile_in_sync,
)
from naviscoord.sessions import SessionInfo


def _session(session_id: str, port: int, token: str, target: str = "t-1",
             fingerprint: str = "fp-1") -> SessionInfo:
    return SessionInfo(
        session_id=session_id,
        pid=1234,
        port=port,
        token=token,
        document_fingerprint=fingerprint,
        raw={"target_id": target},
    )


class _Recorded(Bridge):
    """A bridge whose transport is scripted, and which records what it used."""

    def __init__(self, sessions: list[SessionInfo], outcomes: list[Any]) -> None:
        super().__init__()
        self._sessions = sessions
        self._outcomes = list(outcomes)
        self.used: list[tuple[int, str]] = []
        self.resolved = 0

    def resolve(self, *, for_mutation: bool = False) -> SessionInfo:
        self.resolved += 1
        # Every resolve returns the LATEST registry entry, which is what made
        # the old divergence possible: the cache kept the previous one.
        return self._sessions[-1]

    def _transport(self, request):
        self.used.append((request.full_url, request.headers.get("X-navischoord-token")
                          or request.headers.get("X-Naviscoord-token") or ""))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestSessionSnapshot:
    def test_endpoint_and_fingerprint_come_from_one_snapshot(self) -> None:
        old = _session("s-old", 8781, "tok-old", fingerprint="fp-old")
        new = _session("s-new", 8782, "tok-new", fingerprint="fp-new")

        bridge = Bridge(session=old)
        # The registry has moved on, but nothing has invalidated the cache yet.
        assert bridge.snapshot() is old
        assert bridge.snapshot().document_fingerprint == "fp-old"
        assert bridge.snapshot().port == 8781

        # After invalidating, ONE snapshot answers every question — never a
        # fresh fingerprint paired with a cached endpoint.
        bridge._session = new
        snap = bridge.snapshot()
        assert (snap.port, snap.token, snap.document_fingerprint) == (8782, "tok-new", "fp-new")

    def test_capabilities_are_dropped_when_the_session_changes(self) -> None:
        bridge = Bridge(session=_session("s-1", 8781, "tok-1"))
        bridge._capabilities = {"routes": ["health"]}
        bridge._capabilities_session = "s-1"

        # Same session: the cache stands.
        assert bridge._capabilities_session == "s-1"

        # A new session must not inherit the old manifest: a route the previous
        # add-in offered says nothing about this one.
        bridge.invalidate()
        assert bridge._capabilities is None
        assert bridge._capabilities_session == ""


class TestRetryPolicy:
    @pytest.mark.parametrize("status", [400, 403, 404, 409, 413, 422])
    def test_a_deliberate_refusal_is_never_retried(self, status: int) -> None:
        """Asking again cannot change a refusal the add-in meant."""
        bridge = _Recorded(
            [_session("s-1", 8781, "tok-1")],
            [urllib.error.HTTPError("u", status, "no", {}, None)],
        )
        with pytest.raises(BridgeError) as raised:
            bridge.call("clash/status", {"expected_document_fingerprint": "fp-1"})
        assert raised.value.status == status
        assert len(bridge.used) == 1, "no hubo reintento"

    def test_the_status_and_code_survive(self) -> None:
        import io

        body = io.BytesIO(b'{"error": "document_changed", "detail": "otro documento"}')
        error = urllib.error.HTTPError("u", 409, "conflict", {}, body)
        bridge = _Recorded([_session("s-1", 8781, "tok-1")], [error])

        with pytest.raises(BridgeError) as raised:
            bridge.call("clash/status", {"expected_document_fingerprint": "fp-1"})
        assert raised.value.status == 409
        assert raised.value.code == "document_changed"
        assert "otro documento" in str(raised.value)
        assert raised.value.to_json()["http_status"] == 409

    def test_a_mutation_without_a_key_is_not_replayed(self) -> None:
        """The request may have arrived; only the reply was lost."""
        old = _session("s-1", 8781, "tok-1")
        new = _session("s-2", 8782, "tok-2")
        bridge = _Recorded([old, new], [urllib.error.URLError("conexión caída")])
        bridge._session = old

        with pytest.raises(BridgeError):
            bridge.call("clash/status", {"expected_document_fingerprint": "fp-1"})
        assert len(bridge.used) == 1, "sin idempotency_key no se reenvía"

    def test_a_mutation_with_a_key_retries_only_on_the_same_target(self) -> None:
        old = _session("s-1", 8781, "tok-1", target="t-1", fingerprint="fp-1")
        elsewhere = _session("s-9", 8790, "tok-9", target="t-OTRO", fingerprint="fp-1")
        bridge = _Recorded([old, elsewhere], [urllib.error.URLError("caída")])
        bridge._session = old

        with pytest.raises(BridgeError):
            bridge.call("clash/group", {
                "expected_document_fingerprint": "fp-1",
                "idempotency_key": "k-1",
            })
        assert len(bridge.used) == 1, "no se salta a otra instancia aunque sea la más nueva"

    def test_a_read_may_retry_after_a_real_change(self) -> None:
        old = _session("s-1", 8781, "tok-1")
        new = _session("s-2", 8782, "tok-2")
        bridge = _Recorded([old, new], [urllib.error.URLError("caída"), {"ok": True}])
        bridge._session = old

        assert bridge.call("health") == {"ok": True}
        assert len(bridge.used) == 2, "una lectura sí reintenta"
        assert "8781" in bridge.used[0][0]
        assert "8782" in bridge.used[1][0], "y el reintento usa el snapshot nuevo entero"

    def test_a_read_does_not_retry_when_nothing_moved(self) -> None:
        same = _session("s-1", 8781, "tok-1")
        bridge = _Recorded([same], [urllib.error.URLError("caída")])
        bridge._session = same

        with pytest.raises(BridgeError):
            bridge.call("health")
        assert len(bridge.used) == 1, "reintentar contra el mismo endpoint muerto oculta la caída"


class TestProfileResync:
    CANONICAL = '{"schema":"naviscoord.profile/v1"}'

    def test_matching_checksums_continue(self) -> None:
        verdict = ensure_profile_in_sync(
            server_checksum="abc123",
            canonical=self.CANONICAL,
            read_addin=lambda: {"checksum": "abc123", "session_id": "s-1"},
            push=lambda *_: pytest.fail("no debía reinstalar nada"),
        )
        assert verdict.state == IN_SYNC
        assert verdict.may_run

    def test_a_restart_resynchronises_and_verifies(self) -> None:
        pushed: list[tuple[str, str]] = []

        def push(canonical: str, checksum: str) -> dict[str, Any]:
            pushed.append((canonical, checksum))
            return {"ok": True, "checksum": checksum}

        verdict = ensure_profile_in_sync(
            server_checksum="abc123",
            canonical=self.CANONICAL,
            # The add-in came back on its own default.
            read_addin=lambda: {"checksum": "default99", "session_id": "s-2"},
            push=push,
        )
        assert verdict.state == RESYNCED
        assert verdict.may_run
        assert verdict.resynced
        assert pushed == [(self.CANONICAL, "abc123")]

    def test_a_failed_resync_blocks_the_workflow(self) -> None:
        verdict = ensure_profile_in_sync(
            server_checksum="abc123",
            canonical=self.CANONICAL,
            read_addin=lambda: {"checksum": "default99"},
            push=lambda *_: {"ok": False, "error": "profile_invalid"},
        )
        assert verdict.state == OUT_OF_SYNC
        assert not verdict.may_run, "no se ejecuta con criterios mezclados"

    def test_a_resync_that_installs_something_else_blocks(self) -> None:
        verdict = ensure_profile_in_sync(
            server_checksum="abc123",
            canonical=self.CANONICAL,
            read_addin=lambda: {"checksum": "default99"},
            # Accepted, but the add-in reports a different identity.
            push=lambda *_: {"ok": True, "checksum": "otro456"},
        )
        assert verdict.state == OUT_OF_SYNC
        assert not verdict.may_run

    def test_an_active_job_blocks_resynchronisation(self) -> None:
        verdict = ensure_profile_in_sync(
            server_checksum="abc123",
            canonical=self.CANONICAL,
            read_addin=lambda: pytest.fail("no debía consultar al complemento"),
            push=lambda *_: pytest.fail("no debía reinstalar"),
            active_job=lambda: "job-abc",
        )
        assert verdict.state == BLOCKED_BY_JOB
        assert not verdict.may_run
        assert "job-abc" in verdict.detail

    def test_an_unreachable_bridge_is_its_own_state(self) -> None:
        def boom() -> dict[str, Any]:
            raise BridgeError("sin respuesta")

        verdict = ensure_profile_in_sync(
            server_checksum="abc123",
            canonical=self.CANONICAL,
            read_addin=boom,
            push=lambda *_: pytest.fail("no debía reinstalar"),
        )
        assert verdict.state == UNREACHABLE
        assert not verdict.may_run

    def test_no_pushed_profile_means_nothing_to_reconcile(self) -> None:
        verdict = ensure_profile_in_sync(
            server_checksum="",
            canonical="",
            read_addin=lambda: pytest.fail("no hay nada que comparar"),
            push=lambda *_: pytest.fail("ni que instalar"),
        )
        assert verdict.state == IN_SYNC
        assert verdict.may_run
