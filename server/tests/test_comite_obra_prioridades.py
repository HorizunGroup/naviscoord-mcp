"""Regressions from the recorded «Comité de obra» run of 2026-10-03.

With the exercise's profile — the add-in's format: sets, clash pairs, rules,
and no scoring section at all — the engine scored every one of the 1.434
issues 0.0, so all came out «low» and ``navis_color_by_priority`` painted
nothing; and the handoff traced 0 of 1.434 issues to a Revit ElementId,
because a local Revit NWC publishes it as ``Id`` and the code only looked for
``Element Id``. ``navis_set_disciplines`` with folder labels («Estructura»)
also slipped past every scoring table, which is keyed by code.

Each test below fails on the code before the fix.
"""

from __future__ import annotations

from typing import Any

from naviscoord import mcp_server as M
from naviscoord.analysis import analyze
from naviscoord.interop import build_handoff
from naviscoord.model import ClashExport
from naviscoord.profile import Profile
from naviscoord.state import SessionState

from test_comite_obra import COMITE_PROFILE, FILES, SetsBridge, comite_export

LABELS = {FILES["EST"]: "Estructura", FILES["ARQ"]: "Arquitectura", FILES["MEP"]: "Redes"}


def _deep_beam_clash(raw: dict[str, Any]) -> dict[str, Any]:
    """One duct 376 mm inside a beam, the comité's worst issue (viga 124796)."""
    clash = raw["clashes"][0]
    clash["distance_m"] = -0.376
    clash["a"].update(category="Structural Framing", source_file=FILES["EST"])
    clash["a"]["props"] = {"Category": "Structural Framing", "Id": "124796", "Type": "600 x 900"}
    clash["b"].update(category="Ducts", source_file=FILES["MEP"])
    clash["b"]["props"] = {"Category": "Ducts", "Id": "401681", "Type": "Mitered Elbows / Taps"}
    return raw


def _analysed(overrides: dict[str, str] | None = LABELS):
    profile = Profile.from_json(COMITE_PROFILE)
    export = ClashExport.from_json(_deep_beam_clash(comite_export(300)))
    return profile, export, analyze(export, profile, overrides)


class TestScoringOfAnAddinFormatProfile:
    def test_the_scoring_sections_come_from_the_default_profile(self) -> None:
        profile = Profile.from_json(COMITE_PROFILE)
        assert set(profile.inherited_sections) >= {"severity", "criticality_matrix", "movability", "interop"}
        assert profile.weight("penetration") > 0, "sin pesos todo puntúa 0"
        # The checksum the add-in agrees on is still the file's own.
        assert "severity" not in profile.raw

    def test_issues_are_not_all_low(self) -> None:
        _, _, result = _analysed()
        bands = result.by_priority()
        assert bands["low"] < len(result.issues), bands
        assert max(i.severity for i in result.issues) > 0

    def test_the_worst_issue_is_critical(self) -> None:
        _, _, result = _analysed()
        worst = max(result.issues, key=lambda i: i.max_penetration_m)
        assert worst.max_penetration_m >= 0.37
        assert worst.discipline_pair == ("EST", "MEP")
        assert worst.priority == "critical", (worst.severity, worst.priority)

    def test_deep_hits_are_ranked_by_depth_not_tied_on_the_floor(self) -> None:
        profile = Profile.from_json(COMITE_PROFILE)
        raw = _deep_beam_clash(comite_export(300))
        shallow = raw["clashes"][1]
        shallow.update(distance_m=-0.16)
        shallow["a"].update(category="Structural Framing", source_file=FILES["EST"])
        shallow["b"].update(category="Ducts", source_file=FILES["MEP"])
        shallow["point"] = [p + 50.0 for p in shallow["point"]]
        for side in ("a", "b"):
            for key in ("bbox_min", "bbox_max"):
                shallow[side][key] = [v + 50.0 for v in shallow[side][key]]
        result = analyze(ClashExport.from_json(raw), profile, LABELS)
        by_depth = {round(i.max_penetration_m, 2): i for i in result.issues}
        assert by_depth[0.38].severity > by_depth[0.16].severity

    def test_a_partition_in_a_slab_does_not_reach_critical(self) -> None:
        """Only a critical pair (structure × installations) crosses into critical."""
        _, _, result = _analysed()
        arq_est = [i for i in result.issues if set(i.discipline_pair) == {"ARQ", "EST"}]
        assert all(i.priority != "critical" for i in arq_est)

    def test_criticality_reads_either_order(self) -> None:
        default = Profile.load()
        assert default.criticality("ARQ", "EST") == default.criticality("EST", "ARQ") == 0.8

    def test_own_codes_borrow_the_rows_of_their_trades(self) -> None:
        profile = Profile.from_json(COMITE_PROFILE)
        assert profile.criticality("EST", "MEP") == 0.95
        assert profile.criticality("ARQ", "MEP") == 0.6
        assert profile.movability("MEP", "Ducts") < 0.5

    def test_a_profile_that_writes_its_scoring_keeps_it(self) -> None:
        raw = dict(COMITE_PROFILE, severity={"weights": {"penetration": 1.0}})
        profile = Profile.from_json(raw)
        assert profile.weight("criticality") == 0.0
        assert "severity" not in profile.inherited_sections


class TestHandoffCarriesElementIds:
    def test_every_issue_traces_to_revit_through_id(self) -> None:
        profile, export, result = _analysed()
        handoff = build_handoff(result, export, profile)
        assert handoff["totals"]["traceable_to_revit"] == len(result.issues)
        ids = {t["revit_element_id"] for i in handoff["issues"] for t in i["targets"]}
        assert {"124796", "401681"} <= ids
        assert not handoff["caveats"]


class TestLabelsAreReadAsCodes:
    def test_overrides_with_folder_labels_tag_by_code(self) -> None:
        _, _, result = _analysed()
        codes = {c for i in result.issues for c in i.discipline_pair}
        assert codes <= {"EST", "ARQ", "MEP"}, codes

    def test_set_disciplines_answers_with_the_code(self, monkeypatch) -> None:
        state = SessionState(bridge=SetsBridge([]))
        state.set_profile(Profile.from_json(COMITE_PROFILE))
        monkeypatch.setattr(M, "STATE", state)
        out = M.navis_set_disciplines({**LABELS, "OTRO.nwc": "Paisajismo"})
        assert out["overrides"][FILES["EST"]] == "EST"
        assert out["overrides"][FILES["MEP"]] == "MEP"
        assert out["normalised"][FILES["EST"]] == {"given": "Estructura", "code": "EST"}
        assert out["unknown_disciplines"] == ["Paisajismo"]


class TestColouringCarriesTheFingerprint:
    """The add-in refuses appearance/color and appearance/reset without one (409)."""

    def _bridge(self, monkeypatch):
        from naviscoord.bridge import Bridge
        from naviscoord.sessions import SessionInfo

        bridge = Bridge(session=SessionInfo(session_id="s", port=1, token="t" * 64))
        sent: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(bridge, "call", lambda route, payload, **_: sent.append((route, payload)) or {})
        return bridge, sent

    def test_color_sends_the_fingerprint(self, monkeypatch) -> None:
        bridge, sent = self._bridge(monkeypatch)
        bridge.color(["0/1"], (220, 30, 30), expected_document_fingerprint="fp-1")
        assert sent == [("appearance/color", {"path_ids": ["0/1"], "r": 220, "g": 30, "b": 30,
                                              "transparency": -1.0,
                                              "expected_document_fingerprint": "fp-1"})]

    def test_reset_sends_the_fingerprint(self, monkeypatch) -> None:
        bridge, sent = self._bridge(monkeypatch)
        bridge.reset_appearance(expected_document_fingerprint="fp-1")
        assert sent[0][1]["expected_document_fingerprint"] == "fp-1"
