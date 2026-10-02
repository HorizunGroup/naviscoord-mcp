"""Regressions from the real «Comité de obra» run of 2026-10-01.

Each class names the defect it pins down. The run's evidence lives in
``Curso/Comite de obra/PRUEBA.md`` (section F2); what matters here is the
mechanism, reproduced without Navisworks:

1. ``navis_analyze`` after ``navis_set_disciplines`` "hung" Navisworks. The
   transcript shows the first analyze took 59.6 s against a 60 s client
   timeout, every retry timed out at exactly 60 s, and each retry started a
   new full export beside the previous one. The engine itself takes well
   under a second; the minute was the extraction on the UI thread.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from naviscoord import mcp_server as M
from naviscoord.analysis import analyze
from naviscoord.bridge import Bridge, BridgeError
from naviscoord.model import ClashExport
from naviscoord.profile import Profile
from naviscoord.sessions import SessionInfo
from naviscoord.state import SessionState

FIXTURES = Path(__file__).parent / "fixtures"
FILES = {"EST": "MIR-EST-Mirador.nwc", "ARQ": "MIR-ARQ-Mirador.nwc", "MEP": "MIR-MEP-Mirador.nwc"}
MEP_CATEGORIES = {"ducts", "duct fittings", "pipes", "pipe fittings", "cable trays",
                  "mechanical equipment", "plumbing fixtures", "air terminals", "conduits"}

COMITE_PROFILE = {
    "$schema": "naviscoord.profile/v1",
    "name": "Comite de obra - prueba",
    "sets": {"folders": [
        {"folder": "Arquitectura", "scope_model_contains": "-ARQ-", "sets": [
            {"name": "ARQ - Muros", "conditions": [
                {"tab": "Element", "property": "Category", "test": "equals", "value": "Walls"}]}]},
        {"folder": "Estructura", "scope_model_contains": "-EST-", "sets": [
            {"name": "EST - Vigas", "conditions": [
                {"tab": "Element", "property": "Category", "test": "equals",
                 "value": "Structural Framing"}]}]},
        {"folder": "Redes", "scope_model_contains": "-MEP-", "sets": [
            {"name": "MEP - Ductos", "conditions": [
                {"tab": "Element", "property": "Category", "test": "equals", "value": "Ducts"}]}]},
    ]},
    "discipline_aliases": {},
    "freestanding_disciplines": [],
    "clash": {"name_format": "{0} VS {1}", "pairs": [
        {"a": "Estructura", "b": "Redes", "test": "Hard", "tolerance_m": 0.001, "name": "EST VS MEP 1mm"},
        {"a": "Arquitectura", "b": "Redes", "test": "Hard", "tolerance_m": 0.001, "name": "ARQ VS MEP 1mm"},
        {"a": "Arquitectura", "b": "Estructura", "test": "Hard", "tolerance_m": 0.010,
         "name": "ARQ VS EST 10mm"},
    ]},
    "reglas": {"rules": []},
}


def comite_export(count: int = 2221) -> dict[str, Any]:
    """The demo export, spread over three files the way the Comité one was."""
    base = json.loads((FIXTURES / "torre-demo-export.json").read_text(encoding="utf-8"))
    seed = base["clashes"]
    clashes = []
    for i in range(count):
        clash = copy.deepcopy(seed[i % len(seed)])
        shift = (i // len(seed)) * 3.7
        clash["guid"] = f"{i:08d}-0000-0000-0000-000000000000"
        clash["point"] = [clash["point"][0] + shift, clash["point"][1], clash["point"][2]]
        for side in ("a", "b"):
            element = clash[side]
            element["path_id"] = f"{i}/{side}/{element['path_id']}"
            element["parent_path_id"] = f"{i}/{side}/{element.get('parent_path_id', '')}"
            for key in ("bbox_min", "bbox_max"):
                element[key] = [element[key][0] + shift, element[key][1], element[key][2]]
            category = str(element.get("category", "")).lower()
            code = "MEP" if category in MEP_CATEGORIES else (
                "EST" if category.startswith("structural") or side == "a" else "ARQ")
            element["source_file"] = FILES[code]
        clashes.append(clash)
    base["clashes"] = clashes
    base["models"] = [{"index": i, "source_file": f, "source_path": f} for i, f in enumerate(FILES.values())]
    base["analysis_revision"] = "epoch:0:fp-live"
    base["document_fingerprint"] = "fp-live"
    base["penetration_inventory"] = {"scope": "not_configured", "complete": False,
                                     "elements": [], "errors": []}
    base["timings"] = {"clashes_ms": 1200.0, "penetration_inventory_ms": 0.0, "total_ms": 1200.0}
    return base


# ------------------------------------------------------------------ doubles


class AnalyzeBridge:
    """The surface navis_analyze uses, with a job that finishes on demand."""

    def __init__(self, *, job_routes: tuple[str, ...] = ("clash/export",), finish_after: int = 0,
                 terminal: str = "completed") -> None:
        self.job_routes = list(job_routes)
        self.finish_after = finish_after
        self.terminal = terminal
        self.export = comite_export(300)
        self.submitted: list[dict[str, Any]] = []
        self.sync_exports = 0
        self.polls = 0
        self.target_id = ""
        self.analysis_revision = ""

    def resolve(self, *, for_mutation: bool = False) -> SessionInfo:
        return SessionInfo(session_id="s", port=8781, document_title="Mirador-comite.nwf",
                           document_fingerprint="fp-live", document_open=True)

    def supports_job(self, route: str) -> bool:
        return route in self.job_routes

    export_payload = staticmethod(Bridge.export_payload)
    checked_export = staticmethod(Bridge.checked_export)

    def submit_job(self, route: str, payload: dict[str, Any] | None = None, target_id: str = "") -> dict[str, Any]:
        self.submitted.append({"route": route, "payload": payload})
        return {"accepted": True, "job_id": f"job-{len(self.submitted)}", "state": "queued"}

    def job_status(self, job_id: str) -> dict[str, Any]:
        self.polls += 1
        if self.polls <= self.finish_after:
            return {"job_id": job_id, "state": "running", "phase": "exportando cruces",
                    "progress": {"kind": "units", "done": 120, "total": 300, "percent": 40.0}}
        if self.terminal != "completed":
            return {"job_id": job_id, "state": self.terminal, "message": "detenido",
                    "result": {"status": self.terminal, "cancelled": True}}
        return {"job_id": job_id, "state": "completed", "result": self.export}

    def export_clashes(self, **kwargs: Any) -> dict[str, Any]:
        self.sync_exports += 1
        return Bridge.checked_export(self.export)

    def analysis_state(self) -> dict[str, Any]:
        return {"analysis_revision": self.export["analysis_revision"]}


@pytest.fixture
def state(monkeypatch) -> SessionState:
    fresh = SessionState(profile=Profile.from_json(COMITE_PROFILE), bridge=AnalyzeBridge())
    monkeypatch.setattr(M, "STATE", fresh)
    return fresh


# ------------------------------------------- defecto 1: navis_analyze se cuelga


class TestAnalyzeDoesNotHang:
    def test_engine_with_disciplines_is_not_the_bottleneck(self) -> None:
        """2,221 clashes with the discipline mapping analyse in well under a second.

        Measured at 0.15 s on the developer machine; the bound here is loose
        on purpose so a slow CI box does not flake. What it rules out is the
        hypothesis the run report started from — a quadratic step triggered by
        the mapping — which would cost minutes at this size, not seconds.
        """
        export = ClashExport.from_json(comite_export(2221))
        profile = Profile.from_json(COMITE_PROFILE)
        overrides = {f: c for c, f in FILES.items()}
        for mapping in (None, overrides):
            started = time.perf_counter()
            result = analyze(export, profile, discipline_overrides=mapping)
            assert time.perf_counter() - started < 10.0
            assert result.raw_clash_count == 2221
        assert set(result.by_responsible()) - {"OTRO"}, "la asignación de disciplinas no se aplicó"

    def test_long_export_returns_a_job_before_the_client_gives_up(self, state) -> None:
        state.bridge.finish_after = 10**6  # never finishes inside this call
        started = time.monotonic()
        answer = M.navis_analyze(wait_seconds=0.3)
        assert time.monotonic() - started < 5
        assert answer["state"] == "running"
        assert answer["job_id"] == "job-1"
        assert answer["progress"]["kind"] == "units"
        assert state.result is None

    def test_a_retry_joins_the_running_export_instead_of_queueing_another(self, state) -> None:
        state.bridge.finish_after = 10**6
        M.navis_analyze(wait_seconds=0.1)
        again = M.navis_analyze(wait_seconds=0.1)
        assert again["job_id"] == "job-1"
        assert len(state.bridge.submitted) == 1, "un reintento encoló otra extracción completa"

    def test_collecting_the_job_produces_the_analysis(self, state) -> None:
        state.bridge.finish_after = 10**6
        first = M.navis_analyze(wait_seconds=0.1)
        state.bridge.finish_after = 0
        summary = M.navis_analyze(job_id=first["job_id"])
        assert summary["raw_clashes"] == 300
        assert summary["job_id"] == "job-1"
        assert state.result is not None and state.analysis_job == {}
        assert summary["timings"]["navisworks_export"]["total_ms"] == 1200.0

    def test_short_export_completes_in_one_call_with_disciplines(self, state) -> None:
        M.navis_set_disciplines({f: c for c, f in FILES.items()})
        summary = M.navis_analyze()
        assert summary["raw_clashes"] == 300
        assert "OTRO" not in summary["by_responsible"]
        assert state.bridge.submitted[0]["route"] == "clash/export"

    def test_concurrent_call_is_refused_not_queued(self, state) -> None:
        assert M._ANALYZE_LOCK.acquire(blocking=False)
        try:
            refused = M.navis_analyze()
        finally:
            M._ANALYZE_LOCK.release()
        assert refused["error"] == "analysis_in_progress"
        assert state.bridge.submitted == []

    def test_a_cancelled_export_is_never_analysed(self, state) -> None:
        state.bridge.terminal = "cancelled"
        answer = M.navis_analyze()
        assert answer["error"] == "export_cancelled"
        assert state.result is None

    def test_checked_export_refuses_a_cancelled_payload(self) -> None:
        with pytest.raises(BridgeError) as caught:
            Bridge.checked_export({"status": "cancelled", "cancelled": True, "analysis_revision": "r"})
        assert caught.value.to_json().get("error") == "export_cancelled"

    def test_older_addin_keeps_the_synchronous_route(self, state) -> None:
        state.bridge.job_routes = []
        summary = M.navis_analyze()
        assert summary["raw_clashes"] == 300
        assert state.bridge.sync_exports == 1 and state.bridge.submitted == []

    def test_wait_is_capped_below_the_client_timeout(self, state) -> None:
        answer = M.navis_analyze(wait_seconds=500)
        assert answer.get("error") == "argumento_invalido" or "wait_seconds" in json.dumps(answer)

    def test_unconfigured_inventory_is_reported_as_not_checked(self, state) -> None:
        summary = M.navis_analyze()
        assert any("no se buscaron pasos" in w for w in summary["warnings"])


# --------------------------------------- defecto 2: sets vacíos no son éxito


class SetsBridge:
    """navis_build_search_sets' surface, answering with the add-in's plan."""

    def __init__(self, planned: list[dict[str, Any]]) -> None:
        self.planned = planned
        self.target_id = ""

    def resolve(self, *, for_mutation: bool = False) -> SessionInfo:
        return SessionInfo(session_id="s", port=8781, document_title="Mirador-comite.nwf",
                           document_fingerprint="fp-live", document_open=True)

    def list_sets(self) -> dict[str, Any]:
        return {"sets": []}

    def run_route(self, route: str, payload: dict[str, Any], *, run_async: bool) -> dict[str, Any]:
        return {"dry_run": payload["dry_run"], "planned": self.planned}


class TestEmptySetsAreReported:
    PLANNED = [{"folder": "ARQ", "sets": [
        {"name": "ARQ - Muros", "matches": 0, "empty": True, "empty_reason": "category_id_absent",
         "hint": "El set filtra por CategoryId y este documento no publica esa propiedad"},
        {"name": "ARQ - Pisos", "matches": 0, "empty": True, "empty_reason": "category_id_absent",
         "hint": "El set filtra por CategoryId y este documento no publica esa propiedad"},
    ]}]

    def test_dry_run_names_every_empty_set_with_its_cause(self, monkeypatch) -> None:
        monkeypatch.setattr(M, "STATE", SessionState(bridge=SetsBridge(self.PLANNED)))
        out = M.navis_build_search_sets(folders=[{"folder": "ARQ", "sets": []}], dry_run=True,
                                        expected_document_fingerprint="fp-live")
        assert out["empty_sets"] == ["ARQ/ARQ - Muros", "ARQ/ARQ - Pisos"]
        assert out["warnings"][0].startswith("NINGÚN set capturó elementos")
        assert any("CategoryId" in w for w in out["warnings"])

    def test_example_profile_selects_by_element_category(self) -> None:
        example = json.loads((Path(__file__).parents[2] / "profiles" / "example-profile.json")
                             .read_text(encoding="utf-8"))
        for folder in example["sets"]["folders"]:
            for entry in folder["sets"]:
                conditions = entry["conditions"]
                assert len(conditions) == 1, f"{entry['name']}: Navisworks une condiciones con Y"
                assert conditions[0]["property"] == "Category", entry["name"]
                assert conditions[0]["tab"] == "Element", entry["name"]


# ---------------------------- defectos 4 y 5: el formato de perfil del complemento


class TestAddinProfileFormat:
    def test_load_profile_reports_the_three_disciplines(self) -> None:
        profile = Profile.from_json(COMITE_PROFILE)
        codes = sorted(c for c in profile.disciplines if c != "OTRO")
        assert codes == ["ARQ", "EST", "MEP"], "el perfil del comité declara tres disciplinas"
        assert profile.label("EST") == "Estructura"
        assert profile.discipline_for_filename("MIR-EST-Mirador.nwc") == "EST"
        assert profile.discipline_for_label("Estructura/EST - Vigas") == "EST"

    def test_shared_categories_do_not_decide_the_trade(self) -> None:
        raw = copy.deepcopy(COMITE_PROFILE)
        raw["sets"]["folders"][1]["sets"].append(
            {"name": "EST - Muros", "conditions": [
                {"tab": "Element", "property": "Category", "test": "equals", "value": "Walls"}]})
        profile = Profile.from_json(raw)
        assert profile.discipline_for_category("Walls") is None, "Walls está en ARQ y en EST"
        assert profile.discipline_for_category("Structural Framing") == "EST"
        assert "walls" in profile.disciplines["EST"].selected_categories

    def test_clash_pairs_are_read_from_either_section(self) -> None:
        profile = Profile.from_json(COMITE_PROFILE)
        pairs = profile.clash_pairs()
        assert [(p["a"], p["b"]) for p in pairs] == [("EST", "MEP"), ("ARQ", "MEP"), ("ARQ", "EST")]
        assert pairs[0]["type"] == "Hard" and pairs[0]["source_section"] == "clash"

        legacy = copy.deepcopy(COMITE_PROFILE)
        legacy["clash_matrix"] = {"pairs": [{"a": "EST", "b": "MEP", "type": "Hard", "tolerance_m": 0.001}]}
        assert Profile.from_json(legacy).clash_pairs()[0]["source_section"] == "clash_matrix"

    def test_explicit_disciplines_still_win(self) -> None:
        default = Profile.load()
        assert "OTRO" in default.disciplines and len(default.disciplines) > 2

    def test_build_clash_matrix_accepts_clash_pairs(self, monkeypatch) -> None:
        class MatrixBridge(SetsBridge):
            def __init__(self) -> None:
                super().__init__([])
                self.matrix: list[dict[str, Any]] = []

            def list_sets(self) -> dict[str, Any]:
                return {"sets": [{"name": "Estructura/EST - Vigas"}, {"name": "Redes/MEP - Ductos"},
                                 {"name": "Arquitectura/ARQ - Muros"}]}

            def build_matrix(self, pairs, prefix, dry_run, replace_existing, fingerprint, key):
                self.matrix = pairs
                return {"dry_run": dry_run, "plan": pairs}

        bridge = MatrixBridge()
        monkeypatch.setattr(M, "STATE", SessionState(profile=Profile.from_json(COMITE_PROFILE), bridge=bridge))
        out = M.navis_build_clash_matrix(dry_run=True, expected_document_fingerprint="fp-live")
        assert "error" not in out, out
        assert {(p["a"], p["b"]) for p in bridge.matrix} == {("EST", "MEP"), ("ARQ", "MEP"), ("ARQ", "EST")}
        assert bridge.matrix[0]["sets_a"] == ["Estructura/EST - Vigas"]

    def test_build_sets_refuses_clearly_instead_of_a_500(self, monkeypatch) -> None:
        empty_profile = {"$schema": "naviscoord.profile/v1", "name": "sin disciplinas"}

        class Untouchable(SetsBridge):
            def build_sets(self, *args: Any) -> dict[str, Any]:
                raise AssertionError("no debió llegar al complemento")

        monkeypatch.setattr(M, "STATE", SessionState(profile=Profile.from_json(empty_profile),
                                                     bridge=Untouchable([])))
        out = M.navis_build_sets(dry_run=True, expected_document_fingerprint="fp-live")
        assert out["error"] == "argumento_invalido"
        assert "disciplinas" in out["detail"]

    def test_build_sets_sends_the_folders_of_the_comite_profile(self, monkeypatch) -> None:
        sent: list[Any] = []

        class Recording(SetsBridge):
            def build_sets(self, disciplines, prefix, dry_run, fingerprint, key):
                sent.extend(disciplines)
                return {"dry_run": dry_run}

        monkeypatch.setattr(M, "STATE", SessionState(profile=Profile.from_json(COMITE_PROFILE),
                                                     bridge=Recording([])))
        M.navis_build_sets(dry_run=True, expected_document_fingerprint="fp-live")
        assert sorted(d["discipline"] for d in sent) == ["ARQ", "EST", "MEP"]
        assert "structural framing" in next(d for d in sent if d["discipline"] == "EST")["categories"]

    def test_load_profile_answer_lists_them(self, monkeypatch, tmp_path) -> None:
        path = tmp_path / "comite.json"
        path.write_text(json.dumps(COMITE_PROFILE), encoding="utf-8")

        class Offline(SetsBridge):
            def profile_load(self, canonical: str, checksum: str) -> dict[str, Any]:
                raise BridgeError("sin Navisworks")

        monkeypatch.setattr(M, "STATE", SessionState(bridge=Offline([])))
        out = M.navis_load_profile(str(path))
        assert out["ok"] is True
        assert out["disciplines"] == ["ARQ", "EST", "MEP", "OTRO"]


# ------------------------------------- defecto 8: el motivo real de un 422


class TestRefusalReasonTravels:
    def test_envelope_errors_become_the_message(self, monkeypatch) -> None:
        import io
        import urllib.error

        body = json.dumps({"status": "failed", "errors": [
            "La carpeta «C:\\x\\ComiteObra» no existe y Navisworks no crea carpetas al guardar."]})
        bridge = Bridge(session=SessionInfo(session_id="s", port=1, token="t" * 64))

        def refuse(_request):
            raise urllib.error.HTTPError("http://x", 422, "Unprocessable", {}, io.BytesIO(body.encode()))

        monkeypatch.setattr(bridge, "_transport", refuse)
        with pytest.raises(BridgeError) as caught:
            bridge.call("document/save_as", {"path": "x"})
        assert "no existe" in str(caught.value)
