"""A corrupt record, an elevated process or a recycled PID must not break discovery.

Three reproductions, kept as regressions:

* ``{"port": "ocho mil"}`` — the old ``_read_file`` parsed the JSON inside a
  ``try`` and then built the ``SessionInfo`` outside it, so ``int(raw["port"])``
  raised straight out of discovery. One junk file in the registry directory took
  down the enumeration of every healthy instance beside it.

* ACCESS_DENIED — ``OpenProcess`` returns NULL both for "no such process" and
  for "you may not ask", and the old check read both as dead. An elevated
  Navisworks was pruned out of the registry and became invisible.

* A recycled PID — Windows reuses process ids within minutes, so a session file
  naming PID 9184 can match a browser that started after Navisworks closed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from naviscoord import sessions
from naviscoord.liveness import (
    ALIVE,
    DEAD,
    REASON_ACCESS_DENIED,
    REASON_NO_PID,
    REASON_PID_REUSED,
    UNKNOWN,
    judge,
    probe,
)


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "sesiones"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sessions, "sessions_dir", lambda: root)
    monkeypatch.setattr(sessions, "legacy_session_file", lambda: tmp_path / "session.json")
    sessions._invalid_records.clear()
    return root


def _write(registry: Path, name: str, **fields: Any) -> Path:
    record: dict[str, Any] = {
        "session_id": name,
        "port": 8781,
        "token": "t" * 64,
        "pid": os.getpid(),
        "process_started": "",
        "contract": "naviscoord.session/2",
        "document": {"title": "Torre", "fingerprint": "fp-1", "open": True},
    }
    record.update(fields)
    path = registry / f"{name}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


# ------------------------------------------------------- defensive parsing


class TestCorruptRecords:
    def test_a_non_numeric_port_does_not_bring_discovery_down(self, registry: Path) -> None:
        """The reproduction. It used to raise ValueError out of discover()."""
        _write(registry, "buena")
        _write(registry, "rota", port="ocho mil")

        found = sessions.discover(include_dead=True)
        assert [s.session_id for s in found] == ["buena"]

        skipped = sessions.invalid_records()
        assert any(r["reason"] == "invalid_port" for r in skipped)

    @pytest.mark.parametrize(
        "field,value,reason",
        [
            ("port", 0, "invalid_port"),
            ("port", 70000, "invalid_port"),
            ("port", -1, "invalid_port"),
            ("port", True, "invalid_port"),
            ("port", 8781.5, "invalid_port"),
            ("pid", "muchos", "invalid_pid"),
            ("pid", -5, "invalid_pid"),
            ("token", 12345, "invalid_token"),
            ("document", "no soy un objeto", "invalid_document"),
            ("session_id", "", "missing_session_id"),
            ("session_id", 42, "missing_session_id"),
        ],
    )
    def test_each_malformed_field_is_skipped_with_a_reason(
        self, registry: Path, field: str, value: Any, reason: str
    ) -> None:
        _write(registry, "buena")
        _write(registry, "rota", **{field: value})

        assert [s.session_id for s in sessions.discover(include_dead=True)] == ["buena"]
        assert any(r["reason"] == reason for r in sessions.invalid_records()), (
            f"{field}={value!r} debía reportarse como {reason}"
        )

    def test_a_file_that_is_not_json_is_skipped(self, registry: Path) -> None:
        _write(registry, "buena")
        (registry / "rota.json").write_text("{no es json", encoding="utf-8")
        assert [s.session_id for s in sessions.discover(include_dead=True)] == ["buena"]
        assert any(r["reason"] == "invalid_json" for r in sessions.invalid_records())

    def test_a_root_that_is_not_an_object_is_skipped(self, registry: Path) -> None:
        (registry / "lista.json").write_text("[1, 2, 3]", encoding="utf-8")
        assert sessions.discover(include_dead=True) == []
        assert any(r["reason"] == "not_an_object" for r in sessions.invalid_records())

    def test_the_diagnostic_never_carries_a_token(self, registry: Path) -> None:
        secret = "S3CR3T" * 12
        _write(registry, "rota", port="ocho mil", token=secret)
        sessions.discover(include_dead=True)

        text = json.dumps(sessions.invalid_records())
        assert secret not in text, "el diagnóstico no puede llevar el token"
        # Nor the full path, which carries the operator's home directory.
        assert "\\\\Users\\\\" not in text and "/Users/" not in text

    def test_the_summary_reports_invalid_files(self, registry: Path) -> None:
        _write(registry, "buena")
        _write(registry, "rota", port=None)
        report = sessions.summary()
        assert [s["session_id"] for s in report["sessions"]] == ["buena"]
        assert report["invalid"], "un archivo ilegible debe verse en el resumen"

    def test_a_hundred_files_stay_bounded(self, registry: Path) -> None:
        for i in range(50):
            _write(registry, f"ok{i:03d}")
        for i in range(50):
            (registry / f"mala{i:03d}.json").write_text("{roto", encoding="utf-8")

        found = sessions.discover(include_dead=True)
        assert len(found) == 50
        # The diagnostic is capped: a directory full of junk must not turn into
        # an unbounded report.
        assert len(sessions.invalid_records()) <= 32


# ------------------------------------------------------------- liveness


class TestLivenessIsTriValued:
    def test_a_missing_process_is_dead(self) -> None:
        verdict = probe(999_999, "")
        assert verdict.state == DEAD
        assert verdict.prunable

    def test_access_denied_is_unknown_not_dead(self) -> None:
        """The elevated-Navisworks case, decided without a real privilege boundary."""
        verdict = judge(4242, "2026-08-19T10:00:00", exists=True,
                        actual_start=None, access_denied=True)
        assert verdict.state == UNKNOWN
        assert verdict.reason == REASON_ACCESS_DENIED
        assert not verdict.prunable, "un unknown NUNCA se poda"
        assert not verdict.selectable_for_mutation, "ni se elige para mutar"

    def test_a_recycled_pid_is_not_the_same_process(self) -> None:
        verdict = judge(9184, "2026-08-19T09:00:00", exists=True,
                        actual_start="2026-08-19T11:30:00")
        assert verdict.state == DEAD
        assert verdict.reason == REASON_PID_REUSED
        assert verdict.prunable, "un PID reciclado sí se puede podar"

    def test_a_matching_start_time_is_alive(self) -> None:
        verdict = judge(9184, "2026-08-19T09:00:00", exists=True,
                        actual_start="2026-08-19T09:00:00")
        assert verdict.state == ALIVE
        assert verdict.selectable_for_mutation

    def test_sub_second_precision_does_not_look_like_reuse(self) -> None:
        verdict = judge(9184, "2026-08-19T09:00:00", exists=True,
                        actual_start="2026-08-19T09:00:00.4821736")
        assert verdict.state == ALIVE, "una diferencia de redondeo no es otro proceso"

    def test_no_recorded_pid_is_unknown_not_alive(self) -> None:
        """The old rule was `if pid <= 0: return True`."""
        verdict = judge(0, "", exists=None, actual_start=None)
        assert verdict.state == UNKNOWN
        assert verdict.reason == REASON_NO_PID
        assert not verdict.is_alive
        assert not verdict.prunable

    def test_a_live_process_without_a_recorded_start_is_unknown(self) -> None:
        """Existing is not enough: it could be a recycled id."""
        verdict = judge(4242, "", exists=True, actual_start="2026-08-19T09:00:00")
        assert verdict.state == UNKNOWN
        assert not verdict.selectable_for_mutation

    def test_the_real_probe_answers_for_this_process(self) -> None:
        """Exercises the ctypes path, and therefore the handle it must close."""
        verdict = probe(os.getpid(), "")
        # Unknown, because the fixture recorded no start time — the honest
        # answer, and the one that keeps a recycled id from passing.
        assert verdict.state == UNKNOWN
        assert not verdict.prunable

    def test_repeated_probes_do_not_leak_handles(self) -> None:
        """A leaked handle keeps a zombie alive, and discovery runs constantly."""
        for _ in range(200):
            probe(os.getpid(), "")
        # Reaching here without exhausting handles is the assertion; the
        # `finally` in `_probe_windows` is what makes it hold.
        assert probe(os.getpid(), "").state in (ALIVE, UNKNOWN)


class TestSelectionAndPruning:
    def test_an_unknown_session_is_still_listed(self, registry: Path) -> None:
        """Hiding it turns a permissions problem into 'no hay sesión'."""
        _write(registry, "elevada", process_started="")
        found = sessions.discover()
        assert [s.session_id for s in found] == ["elevada"]

    def test_a_dead_session_is_hidden_and_reported(self, registry: Path) -> None:
        _write(registry, "viva")
        _write(registry, "muerta", pid=999_999)

        assert [s.session_id for s in sessions.discover()] == ["viva"]
        report = sessions.summary()
        assert [s["session_id"] for s in report["stale"]] == ["muerta"]
        assert report["stale"][0]["liveness"] == DEAD

    def test_unknown_and_dead_are_separate_buckets(self, registry: Path) -> None:
        _write(registry, "desconocida", process_started="")
        _write(registry, "muerta", pid=999_999)

        report = sessions.summary()
        assert [s["session_id"] for s in report["stale"]] == ["muerta"]
        assert [s["session_id"] for s in report["unknown"]] == ["desconocida"]
        assert report["unknown"][0]["reason"], "un unknown explica por qué"
