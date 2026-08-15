"""What the client actually puts on the wire, and what it refuses to.

The add-in's own tests cover the job state machine, the mutation envelope and
the save policy in C#. These cover the Python half: that the guards are sent,
that a capability is checked before a route is used, and that a retry carries
the key that makes it harmless.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from naviscoord.bridge import Bridge, CapabilityError, _guarded
from test_targeting import write_session


class RecordingBridge(Bridge):
    """A Bridge that records calls instead of making them."""

    def __init__(self, capabilities: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.reply: dict[str, Any] = {"status": "completed"}
        self._capabilities = capabilities or {
            "routes": [
                "health", "document/save", "document/save_as", "job/submit",
                "workflow/run", "workflow/configure", "profile/info",
            ]
        }

    def call(self, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((route, payload or {}))
        return dict(self.reply)

    @property
    def last(self) -> tuple[str, dict[str, Any]]:
        return self.calls[-1]


@pytest.fixture
def bridge(tmp_path: Path) -> RecordingBridge:
    from naviscoord import sessions

    write_session(sessions.sessions_dir(), "aaa")
    return RecordingBridge()


class TestMutationGuards:
    def test_empty_guards_are_omitted_rather_than_sent_blank(self) -> None:
        """An old addin should not have to ignore fields it never knew about."""
        assert _guarded({"a": 1}, "", "") == {"a": 1}

    def test_present_guards_are_sent(self) -> None:
        payload = _guarded({"a": 1}, "fp-1", "key-1")
        assert payload["expected_document_fingerprint"] == "fp-1"
        assert payload["idempotency_key"] == "key-1"

    def test_apply_groups_carries_both_guards(self, bridge: RecordingBridge) -> None:
        bridge.apply_groups([{"name": "g", "clash_guids": ["x"]}], False,
                            fingerprint="fp-1", idempotency_key="k")
        route, payload = bridge.last
        assert route == "clash/group"
        assert payload["expected_document_fingerprint"] == "fp-1"
        assert payload["idempotency_key"] == "k"
        assert payload["dry_run"] is False

    def test_set_status_carries_both_guards(self, bridge: RecordingBridge) -> None:
        bridge.set_status(["g1"], "Approved", True, fingerprint="fp-1", idempotency_key="k")
        _, payload = bridge.last
        assert payload["status"] == "Approved"
        assert payload["expected_document_fingerprint"] == "fp-1"

    def test_viewpoints_carry_both_guards(self, bridge: RecordingBridge) -> None:
        bridge.save_viewpoints([], False, fingerprint="fp-1", idempotency_key="k")
        _, payload = bridge.last
        assert payload["expected_document_fingerprint"] == "fp-1"

    def test_the_same_key_twice_is_the_callers_signal_of_a_retry(
        self, bridge: RecordingBridge
    ) -> None:
        """The client sends it; the addin is what collapses the duplicate."""
        for _ in range(2):
            bridge.apply_groups([], False, fingerprint="fp-1", idempotency_key="misma")
        assert [p["idempotency_key"] for _, p in bridge.calls] == ["misma", "misma"]


class TestSaveWiring:
    def test_save_demands_a_fingerprint_on_the_wire(self, bridge: RecordingBridge) -> None:
        bridge.save("fp-1", dry_run=False)
        route, payload = bridge.last
        assert route == "document/save"
        assert payload["expected_document_fingerprint"] == "fp-1"

    def test_save_as_sends_path_fingerprint_and_overwrite(self, bridge: RecordingBridge) -> None:
        bridge.save_as(r"D:\out\x.nwf", "fp-1", overwrite=True, dry_run=False)
        route, payload = bridge.last
        assert route == "document/save_as"
        assert payload["path"] == r"D:\out\x.nwf"
        assert payload["expected_document_fingerprint"] == "fp-1"
        assert payload["overwrite"] is True

    def test_save_is_refused_when_the_addin_lacks_the_route(self, tmp_path: Path) -> None:
        from naviscoord import sessions

        write_session(sessions.sessions_dir(), "aaa")
        old = RecordingBridge(capabilities={"routes": ["health", "census"]})
        with pytest.raises(CapabilityError) as caught:
            old.save_as(r"D:\out\x.nwf", "fp-1")
        assert "document/save_as" in str(caught.value)
        assert old.calls == [], "no debe llegar nada al puente si la capacidad falta"


class TestJobWiring:
    def test_submit_wraps_the_route_and_its_payload(self, bridge: RecordingBridge) -> None:
        bridge.submit_job("workflow/run", {"expected_document_fingerprint": "fp-1"})
        route, payload = bridge.last
        assert route == "job/submit"
        assert payload["route"] == "workflow/run"
        assert payload["payload"]["expected_document_fingerprint"] == "fp-1"

    def test_submit_passes_the_pinned_target(self, bridge: RecordingBridge) -> None:
        bridge.target_id = "torre4"
        bridge.submit_job("workflow/run", {})
        _, payload = bridge.last
        assert payload["target_id"] == "torre4"

    def test_status_list_and_cancel_use_their_own_routes(self, bridge: RecordingBridge) -> None:
        bridge.job_status("job-1")
        assert bridge.last == ("job/status", {"job_id": "job-1"})
        bridge.job_list()
        assert bridge.last[0] == "job/list"
        bridge.job_cancel("job-1")
        assert bridge.last == ("job/cancel", {"job_id": "job-1"})

    def test_submit_is_refused_by_an_addin_without_jobs(self, tmp_path: Path) -> None:
        from naviscoord import sessions

        write_session(sessions.sessions_dir(), "aaa")
        old = RecordingBridge(capabilities={"routes": ["health", "clash/run"]})
        with pytest.raises(CapabilityError):
            old.submit_job("workflow/run", {})
        assert old.calls == []


class TestWorkflowWiring:
    def test_each_step_maps_to_its_route(self, bridge: RecordingBridge) -> None:
        bridge.workflow("run", {"a": 1})
        assert bridge.last == ("workflow/run", {"a": 1})

    def test_an_unknown_step_is_refused_before_the_call(self, bridge: RecordingBridge) -> None:
        with pytest.raises(CapabilityError):
            bridge.workflow("inventado", {})
        assert bridge.calls == []

    def test_capabilities_are_cached_and_refreshable(self, tmp_path: Path) -> None:
        from naviscoord import sessions

        write_session(sessions.sessions_dir(), "aaa")
        client = RecordingBridge()
        client._capabilities = None
        client.reply = {"routes": ["health"]}

        client.capabilities()
        client.capabilities()
        assert [r for r, _ in client.calls] == ["capabilities"], "debe cachearse"

        client.capabilities(refresh=True)
        assert [r for r, _ in client.calls] == ["capabilities", "capabilities"]

    def test_pinning_a_target_drops_the_cached_capabilities(self, tmp_path: Path) -> None:
        """A different instance may be running a different addin build."""
        from naviscoord import sessions

        write_session(sessions.sessions_dir(), "aaa")
        write_session(sessions.sessions_dir(), "bbb", started="2026-08-14T12:00:00Z")
        client = RecordingBridge()
        client.capabilities()
        client.pin("bbb")
        assert client._capabilities is None
