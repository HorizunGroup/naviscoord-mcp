"""Which Navisworks, which document, and what happens when either changes.

A coordinator routinely has two instances open. Everything here is about the
consequences of that: choosing one, refusing to choose silently for a write,
and noticing when the document under a cached analysis has been swapped.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from naviscoord import sessions
from naviscoord.bridge import Bridge, BridgeError, CapabilityError
from naviscoord.profile import Profile
from naviscoord.sessions import SESSION_ENV, TargetError
from naviscoord.state import SessionState, StateError, profile_checksum


def write_session(
    directory: Path,
    session_id: str,
    *,
    pid: int | None = None,
    port: int = 8781,
    title: str = "Torre A",
    fingerprint: str = "fp-a",
    started: str = "2026-08-14T10:00:00Z",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "contract": sessions.CONTRACT,
        "api_version": "naviscoord.api/2",
        "session_id": session_id,
        "port": port,
        "token": "t" * 64,
        "pid": os.getpid() if pid is None else pid,
        "product": "Navisworks Manage 2026",
        "addin_version": "0.1.2",
        "document": {"open": True, "title": title, "fingerprint": fingerprint},
        "started": started,
        "heartbeat": started,
    }
    path = directory / f"{record['pid']}-{session_id}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """The sessions directory, already redirected by the autouse fixture."""
    return sessions.sessions_dir()


class TestDiscovery:
    def test_no_sessions_is_an_explained_refusal(self, registry: Path) -> None:
        with pytest.raises(TargetError, match="ninguna sesión activa"):
            sessions.select()

    def test_the_refusal_says_where_it_looked(self, registry: Path) -> None:
        try:
            sessions.select()
        except TargetError as exc:
            assert str(registry) in exc.hint
            assert SESSION_ENV in exc.hint

    def test_one_session_needs_no_choosing(self, registry: Path) -> None:
        write_session(registry, "aaa")
        assert sessions.select().session_id == "aaa"
        assert sessions.select(for_mutation=True).session_id == "aaa"

    def test_two_sessions_are_both_listed(self, registry: Path) -> None:
        write_session(registry, "aaa", port=8781, title="Torre A")
        write_session(registry, "bbb", port=8782, title="Torre B", started="2026-08-14T11:00:00Z")
        found = sessions.discover()
        assert {s.session_id for s in found} == {"aaa", "bbb"}
        assert {s.port for s in found} == {8781, 8782}

    def test_dead_sessions_are_hidden_but_reported_as_stale(self, registry: Path) -> None:
        write_session(registry, "viva")
        write_session(registry, "muerta", pid=999_999)
        live = sessions.discover()
        assert [s.session_id for s in live] == ["viva"]

        summary = sessions.summary()
        assert [s["session_id"] for s in summary["stale"]] == ["muerta"]

    def test_corrupt_entry_does_not_hide_the_others(self, registry: Path) -> None:
        write_session(registry, "buena")
        registry.mkdir(parents=True, exist_ok=True)
        (registry / "rota.json").write_text("{no es json", encoding="utf-8")
        assert [s.session_id for s in sessions.discover()] == ["buena"]

    def test_legacy_single_file_still_works(self, tmp_path: Path) -> None:
        """An addin nobody has updated writes one shared session.json."""
        legacy = sessions.legacy_session_file()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(
            json.dumps({"port": 8781, "token": "x" * 64, "pid": os.getpid()}),
            encoding="utf-8",
        )
        found = sessions.discover()
        assert len(found) == 1
        assert found[0].port == 8781

    def test_dead_legacy_pointer_is_not_treated_as_a_live_session(self) -> None:
        legacy = sessions.legacy_session_file()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(
            json.dumps({"port": 8781, "token": "x" * 64, "pid": 999_999}),
            encoding="utf-8",
        )

        assert sessions.discover() == []
        report = sessions.summary()
        assert report["sessions"] == []
        assert [entry["pid"] for entry in report["stale"]] == [999_999]

    @pytest.mark.skipif(os.name != "nt", reason="comprueba creación de proceso Win32")
    def test_a_recycled_pid_is_not_the_recorded_process(self, registry: Path) -> None:
        path = write_session(registry, "recycled", pid=os.getpid())
        record = json.loads(path.read_text(encoding="utf-8"))
        record["process_started"] = "2100-01-01T00:00:00+00:00"
        path.write_text(json.dumps(record), encoding="utf-8")
        assert sessions.discover() == []

    def test_env_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The documented escape hatch for a non-interactive deployment."""
        elsewhere = tmp_path / "otro"
        path = write_session(elsewhere, "override", port=9999)
        monkeypatch.setenv(SESSION_ENV, str(path))
        found = sessions.discover()
        assert len(found) == 1 and found[0].port == 9999

    def test_env_override_accepts_a_directory(self, tmp_path: Path, monkeypatch) -> None:
        elsewhere = tmp_path / "otro"
        write_session(elsewhere, "a")
        write_session(elsewhere, "b", started="2026-08-14T12:00:00Z")
        monkeypatch.setenv(SESSION_ENV, str(elsewhere))
        assert len(sessions.discover()) == 2

    def test_summary_reports_the_override_is_active(self, tmp_path: Path, monkeypatch) -> None:
        path = write_session(tmp_path / "o", "x")
        monkeypatch.setenv(SESSION_ENV, str(path))
        assert sessions.summary()["override_active"] is True


class TestTargetSelection:
    """The rule: reads may pick, writes may not."""

    def test_a_read_with_two_instances_picks_the_newest_one(self, registry: Path) -> None:
        write_session(registry, "vieja", started="2026-08-14T09:00:00Z")
        write_session(registry, "nueva", started="2026-08-14T11:00:00Z")
        assert sessions.select().session_id == "nueva"

    def test_a_mutation_with_two_instances_is_refused(self, registry: Path) -> None:
        write_session(registry, "torre-a", title="Torre A", started="2026-08-14T09:00:00Z")
        write_session(registry, "torre-b", title="Torre B", started="2026-08-14T11:00:00Z")

        with pytest.raises(TargetError) as caught:
            sessions.select(for_mutation=True)

        assert "no voy a elegir por ti" in str(caught.value).lower()
        assert "navis_target" in caught.value.hint
        assert {c["session_id"] for c in caught.value.candidates} == {"torre-a", "torre-b"}

    def test_an_explicit_target_makes_a_mutation_unambiguous(self, registry: Path) -> None:
        write_session(registry, "torre-a", title="Torre A")
        write_session(registry, "torre-b", title="Torre B", started="2026-08-14T11:00:00Z")
        chosen = sessions.select("torre-a", for_mutation=True)
        assert chosen.document_title == "Torre A"

    def test_an_unknown_target_lists_the_real_ones(self, registry: Path) -> None:
        write_session(registry, "torre-a")
        with pytest.raises(TargetError) as caught:
            sessions.select("torre9")
        assert "navis_sessions" in caught.value.hint
        assert caught.value.candidates

    def test_a_target_can_be_given_as_a_pid(self, registry: Path) -> None:
        write_session(registry, "aaa")
        assert sessions.select(str(os.getpid())).session_id == "aaa"

    def test_bridge_pin_fixes_the_instance(self, registry: Path) -> None:
        write_session(registry, "torre-a", title="Torre A")
        write_session(registry, "torre-b", title="Torre B", started="2026-08-14T11:00:00Z")

        bridge = Bridge()
        with pytest.raises(BridgeError):
            bridge.resolve(for_mutation=True)

        bridge.pin("torre-a")
        assert bridge.resolve(for_mutation=True).document_title == "Torre A"

        bridge.unpin()
        with pytest.raises(BridgeError):
            bridge.resolve(for_mutation=True)


class TestCapabilityNegotiation:
    """An old addin must produce an actionable message, not `unknown_route`."""

    def test_a_missing_route_names_the_fix(self, registry: Path) -> None:
        write_session(registry, "aaa")
        bridge = Bridge()
        bridge._capabilities = {"routes": ["health", "census"]}

        with pytest.raises(CapabilityError) as caught:
            bridge.require("document/save_as", since="0.1.2")

        assert "document/save_as" in str(caught.value)
        assert "0.1.2" in caught.value.hint
        assert "install.ps1" in caught.value.hint

    def test_a_present_route_passes(self, registry: Path) -> None:
        write_session(registry, "aaa")
        bridge = Bridge()
        bridge._capabilities = {"routes": ["document/save_as"]}
        bridge.require("document/save_as")

    def test_an_addin_without_capabilities_is_treated_as_too_old(self, registry: Path) -> None:
        write_session(registry, "aaa")
        bridge = Bridge()

        def explode(*_: Any, **__: Any) -> dict[str, Any]:
            raise BridgeError("unknown_route")

        bridge.call = explode  # type: ignore[method-assign]
        with pytest.raises(CapabilityError) as caught:
            bridge.require("document/save")
        assert "anterior a esta versión" in str(caught.value)

    def test_capability_error_is_a_bridge_error(self) -> None:
        """So existing handlers keep catching it, with a more specific branch."""
        assert issubclass(CapabilityError, BridgeError)


class TestExitVerification:
    def _bridge(self, monkeypatch: pytest.MonkeyPatch) -> Bridge:
        session = sessions.SessionInfo(
            session_id="exit-test",
            port=8781,
            token="x" * 64,
            pid=os.getpid(),
        )
        bridge = Bridge(session=session)
        monkeypatch.setattr(bridge, "require", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(bridge, "resolve", lambda **_kwargs: session)
        monkeypatch.setattr(
            bridge,
            "call",
            lambda *_args, **_kwargs: {
                "status": "accepted",
                "exit_requested": True,
                "application_exit_verified": False,
            },
        )
        return bridge

    def test_exit_is_completed_only_after_the_pid_is_gone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bridge = self._bridge(monkeypatch)
        monkeypatch.setattr("naviscoord.bridge.is_alive", lambda _session: False)
        result = bridge.exit_application(
            "require_clean", "fp", dry_run=False, verify_timeout=0.1
        )
        assert result["status"] == "completed"
        assert result["application_exit_verified"] is True
        assert result["verification_source"] == "process_liveness"

    def test_a_process_that_stays_open_is_reported_partial(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bridge = self._bridge(monkeypatch)
        monkeypatch.setattr("naviscoord.bridge.is_alive", lambda _session: True)
        moments = iter((0.0, 1.0))
        monkeypatch.setattr("naviscoord.bridge.time.monotonic", lambda: next(moments))
        result = bridge.exit_application(
            "discard", "fp", dry_run=False, verify_timeout=0.1
        )
        assert result["status"] == "partial"
        assert result["application_exit_verified"] is False
        assert "sigue abierto" in result["warnings"][0]


class TestDocumentScopedState:
    """Cached analysis belongs to the document it came from."""

    def stub_state(self, registry: Path, fingerprint: str, title: str) -> SessionState:
        write_session(registry, "aaa", fingerprint=fingerprint, title=title)
        state = SessionState()
        state.bind(fingerprint, title)
        state.result = object()  # stands in for an AnalysisResult
        state.group_key = "ORG_DISCIPLINA"
        state.group_roles = {"MEP": "HVAC"}
        return state

    def test_same_document_keeps_the_analysis(self, registry: Path) -> None:
        state = self.stub_state(registry, "fp-a", "Torre A")
        assert state.require_fresh_result() is state.result

    def test_a_swapped_document_discards_the_analysis(self, registry: Path) -> None:
        state = self.stub_state(registry, "fp-a", "Torre A")

        # The operator closed Torre A and opened Torre B.
        for entry in registry.glob("*.json"):
            entry.unlink()
        write_session(registry, "bbb", fingerprint="fp-b", title="Torre B")

        with pytest.raises(StateError) as caught:
            state.require_fresh_result()

        assert "otro documento" in str(caught.value)
        assert "Torre B" in str(caught.value)
        assert state.result is None, "el análisis obsoleto debe descartarse, no conservarse"

    def test_binding_a_different_document_clears_derived_state(self, registry: Path) -> None:
        state = self.stub_state(registry, "fp-a", "Torre A")
        changed = state.bind("fp-b", "Torre B")
        assert changed is True
        assert state.result is None
        assert state.group_key == ""
        assert state.group_roles == {}

    def test_binding_the_same_document_keeps_everything(self, registry: Path) -> None:
        state = self.stub_state(registry, "fp-a", "Torre A")
        assert state.bind("fp-a", "Torre A") is False
        assert state.result is not None
        assert state.group_key == "ORG_DISCIPLINA"

    def test_a_mutation_against_the_wrong_fingerprint_is_refused(self, registry: Path) -> None:
        state = self.stub_state(registry, "fp-a", "Torre A")
        with pytest.raises(StateError) as caught:
            state.require_mutable("fp-de-otro-modelo")
        assert "no es el que esperabas" in str(caught.value)
        assert "navis_target" in caught.value.hint

    def test_a_mutation_against_the_right_fingerprint_proceeds(self, registry: Path) -> None:
        state = self.stub_state(registry, "fp-a", "Torre A")
        assert state.require_mutable("fp-a") == "fp-a"

    def test_a_mutation_with_two_instances_and_no_target_is_refused(self, registry: Path) -> None:
        write_session(registry, "torre-a", fingerprint="fp-a", title="Torre A")
        write_session(registry, "torre-b", fingerprint="fp-b", title="Torre B",
                      started="2026-08-14T11:00:00Z")
        state = SessionState()
        with pytest.raises(BridgeError, match="No voy a elegir por ti"):
            state.require_mutable()

    def test_clear_grouping_drops_a_previous_models_key(self, registry: Path) -> None:
        """A stale grouping key is worse than none: every set comes back empty."""
        state = self.stub_state(registry, "fp-a", "Torre A")
        state.clear_grouping()
        assert state.group_key == ""
        assert state.group_roles == {}
        assert state.discovery is None


class TestProfileIdentity:
    def test_a_profile_change_invalidates_derived_state(self) -> None:
        state = SessionState()
        state.result = object()
        state.overrides = {"a.rvt": "EST"}

        other = Profile.load()
        other.raw["severity"] = {"weights": {"penetration": 1.0}}
        other.name = "otro"

        assert state.set_profile(other) is True
        assert state.result is None
        assert state.overrides == {}

    def test_reloading_the_same_profile_keeps_the_analysis(self) -> None:
        state = SessionState()
        state.result = object()
        assert state.set_profile(Profile.load()) is False
        assert state.result is not None

    def test_checksum_ignores_comments(self) -> None:
        """Rewording a note must not invalidate every cached result."""
        first = Profile.load()
        second = Profile.load()
        second.raw["_comment"] = "una nota completamente distinta"
        assert profile_checksum(first) == profile_checksum(second)

    def test_checksum_moves_when_criteria_move(self) -> None:
        first = Profile.load()
        second = Profile.load()
        second.raw.setdefault("clustering", {})["eps_m"] = 99.0
        assert profile_checksum(first) != profile_checksum(second)

    def test_state_stamp_carries_the_provenance(self, tmp_path: Path) -> None:
        state = SessionState()
        state.bind("fp-1", "Torre A", "aaa")
        stamp = state.stamp()
        assert stamp["document_fingerprint"] == "fp-1"
        assert stamp["document_title"] == "Torre A"
        assert stamp["target_id"] == "aaa"
        assert stamp["profile_checksum"] == state.profile_id


class TestErrorShapeConsistency:
    """The same condition must reach the caller in ONE shape.

    `TargetError` emits `{"error": "target_ambiguous", "detail": msg,
    "candidates": [...]}`. Wrapping it in a `BridgeError` used to flatten that
    into `{"error": msg, "detail": [candidates]}` — so a caller could not
    reliably answer "which instance did you mean?" without knowing where the
    failure had been raised.
    """

    def test_wrapped_target_error_keeps_its_shape(self, registry: Path) -> None:
        write_session(registry, "a", title="Torre A", started="2026-08-14T09:00:00Z")
        write_session(registry, "b", title="Torre B", started="2026-08-14T11:00:00Z")

        raw = None
        try:
            sessions.select(for_mutation=True)
        except TargetError as exc:
            raw = exc.to_json()

        wrapped = None
        try:
            Bridge().resolve(for_mutation=True)
        except BridgeError as exc:
            wrapped = exc.to_json()

        assert raw["error"] == wrapped["error"] == "target_ambiguous"
        assert raw["detail"] == wrapped["detail"]
        assert len(wrapped["candidates"]) == 2
        assert {c["session_id"] for c in wrapped["candidates"]} == {"a", "b"}
        assert wrapped["hint"] == raw["hint"]

    def test_an_ordinary_bridge_error_is_unchanged(self, registry: Path) -> None:
        """Adding the code must not reshape every other failure."""
        plain = BridgeError("algo falló", hint="reintenta", detail={"x": 1}).to_json()
        assert plain["error"] == "algo falló"
        assert plain["detail"] == {"x": 1}
        assert "candidates" not in plain
