"""Ecosystem handoff tests.

The value of this layer is that a coordination issue survives the trip to
the tool that can actually fix it. These tests assert the join keys arrive
intact, that folded issues do not generate duplicate work orders, and that
untraceable issues are declared rather than quietly dropped.
"""

from __future__ import annotations

import codecs
import json
from pathlib import Path

from naviscoord import samples as synth
from naviscoord.analysis import analyze
from naviscoord.interop import build_handoff, powerbi_tables, revit_worklist, write_handoff
from naviscoord.model import ClashExport


def _analysed(raw: dict, profile):
    export = ClashExport.from_json(raw)
    result = analyze(export, profile)
    # analyze() tags in place, so the export carries disciplines afterwards.
    return result, export


def test_handoff_carries_revit_ids(profile):
    result, export = _analysed(synth.systemic_elevation_case(), profile)
    handoff = build_handoff(result, export, profile)

    assert handoff["schema"] == "naviscoord.coordination/1"
    assert handoff["totals"]["issues"] == len(result.issues)

    targets = [t for issue in handoff["issues"] for t in issue["targets"]]
    traceable = [t for t in targets if t["actionable_in_revit"]]
    assert traceable, "ningún elemento conserva su Element Id de Revit"
    assert all(t["revit_element_id"] for t in traceable)


def test_untraceable_issues_are_declared_not_dropped(profile):
    """A model exported without element properties must say so."""
    raw = synth.pipe_rack_case()
    for clash in raw["clashes"]:
        clash["a"]["props"] = {}
        clash["b"]["props"] = {}
    result, export = _analysed(raw, profile)
    handoff = build_handoff(result, export, profile)

    assert handoff["totals"]["traceable_to_revit"] == 0
    assert handoff["caveats"], "se perdió la trazabilidad sin advertirlo"
    assert "Element Id" in handoff["caveats"][0]


def test_worklist_groups_by_authoring_model(profile):
    result, export = _analysed(synth.systemic_elevation_case(), profile)
    worklist = revit_worklist(build_handoff(result, export, profile))

    assert worklist["schema"] == "naviscoord.coordination.worklist/1"
    for model in worklist["models"]:
        assert model["source_file"]
        assert model["count"] == len(model["items"])
        severities = [item["severity"] for item in model["items"]]
        assert severities == sorted(severities, reverse=True)


def test_worklist_skips_folded_issues(profile):
    """A folded issue would send someone to make the same edit twice."""
    result, export = _analysed(synth.repeated_typology_case(), profile)
    handoff = build_handoff(result, export, profile)
    worklist = revit_worklist(handoff)

    issued = {item["issue_id"] for model in worklist["models"] for item in model["items"]}
    folded = {i.issue_id for i in result.issues if i.folded_into}
    assert not (issued & folded)


def test_worklist_only_tasks_the_movable_side(profile):
    """Nobody should get a work order to move a structural beam."""
    result, export = _analysed(synth.systemic_elevation_case(), profile)
    worklist = revit_worklist(build_handoff(result, export, profile))
    assert all(model["discipline"] != "EST" for model in worklist["models"])


def test_powerbi_tables_form_a_star_schema(profile):
    result, export = _analysed(synth.full_project_case(), profile)
    tables = powerbi_tables(build_handoff(result, export, profile))

    assert set(tables) == {
        "fct_issues",
        "brg_issue_elements",
        "dim_root_causes",
        "dim_disciplines",
    }

    fact_ids = {row["issue_id"] for row in tables["fct_issues"]}
    bridge_ids = {row["issue_id"] for row in tables["brg_issue_elements"]}
    assert bridge_ids <= fact_ids, "el puente referencia problemas que no están en el hecho"

    cause_ids = {row["cause_id"] for row in tables["dim_root_causes"]}
    used = {row["cause_id"] for row in tables["fct_issues"] if row["cause_id"]}
    assert used <= cause_ids, "el hecho referencia causas raíz inexistentes"

    assert sum(row["is_decision"] for row in tables["fct_issues"]) == len(result.decisions)


def test_write_handoff_produces_every_artifact(profile, tmp_path):
    result, export = _analysed(synth.full_project_case(), profile)
    report = write_handoff(tmp_path, result, export, profile)

    # The artifacts live inside one immutable generation, and `current.json`
    # is the only file that ever moves. A consumer reading loose top-level
    # files can read a mixture of two runs; one that follows the pointer
    # cannot, which is the whole point of the change.
    generation = Path(report["directory"])
    names = {p.name for p in generation.iterdir()}
    assert "coordination_handoff.json" in names
    assert "revit_worklist.json" in names
    assert "fct_issues.csv" in names
    assert "manifest.json" in names
    assert report["issues"] == len(result.issues)

    current = json.loads((tmp_path / "current.json").read_text(encoding="utf-8"))
    assert current["generation_id"] == report["generation_id"]

    payload = json.loads((generation / "coordination_handoff.json").read_text(encoding="utf-8"))
    assert payload["source"]["tool"] == "naviscoord"
    assert payload["source"]["profile"] == profile.name


def test_csv_is_written_with_bom_for_excel(profile, tmp_path):
    """Without the BOM, Power BI and Excel mangle every accented value."""
    result, export = _analysed(synth.noise_case(), profile)
    report = write_handoff(tmp_path, result, export, profile)
    csv_path = Path(report["directory"]) / "fct_issues.csv"
    assert csv_path.read_bytes().startswith(codecs.BOM_UTF8)
