"""navis_compare_snapshot must compare the week after, when the models changed.

Recorded in the Navisworks course (class C6, 2026-10-04): the trades sent
corrected NWC files, the tests ran again and the tool refused to compare week 1
with week 2 because the document fingerprint differed — exactly the case the
comparison exists for. The agent had to write its own script.
"""
import json
from dataclasses import replace

import pytest

from naviscoord.analysis.noise import FilterResult
from naviscoord.analysis.pipeline import AnalysisResult
from naviscoord.history import compare, snapshot
from naviscoord.model import Clash, ElementRef, Issue


def element(path, discipline, revit_id, source, lo=(0., 0., 0.), hi=(1., 1., 1.)):
    return ElementRef(path_id=path, discipline=discipline, source_file=source,
                      bbox_min=lo, bbox_max=hi, props={"Id": str(revit_id)})


def census(**models):
    """models: source_file -> (item_count, {category: count})."""
    return {"models": [{"source_file": name, "item_count": count, "bbox_min": [0, 0, 0], "bbox_max": [60, 10, 12],
                        "top_categories": categories} for name, (count, categories) in models.items()]}


def run(fingerprint, cases, inventory=None):
    """cases: (issue_id, guid, duct, beam, centroid, penetration_m)."""
    clashes, issues = [], []
    for issue_id, guid, duct, beam, centroid, penetration in cases:
        clash = Clash(guid, "EST VS MEP", guid, beam, duct, distance_m=-penetration, point=centroid)
        clashes.append(clash)
        issues.append(Issue(issue_id, "pair", ("EST", "MEP"), [guid], centroid, centroid, centroid,
                            severity=70.0, priority="high", responsible="MEP",
                            max_penetration_m=penetration, representative_clash_id=guid,
                            elements_a=[beam.path_id], elements_b=[duct.path_id]))
    result = AnalysisResult(issues=issues, filtering=FilterResult(kept=clashes))
    return snapshot(result, {"document_fingerprint": fingerprint, "profile_checksum": "p"}, census=inventory)


BEAM = element("1/2/3", "EST", 200, "MIR-EST.rvt", (0, -5, 3), (0.3, 5, 3.6))
OTHER_BEAM = element("1/2/9", "EST", 201, "MIR-EST.rvt", (20, -5, 3), (20.3, 5, 3.6))
SLAB = element("1/7/1", "EST", 300, "MIR-EST.rvt", (40, 0, 3), (50, 10, 3.3))


def duct(path, revit_id, x, z=3.2):
    return element(path, "MEP", revit_id, "MIR-MEP.rvt", (x - 2, -0.2, z - 0.2), (x + 2, 0.2, z + 0.2))


EST_INVENTORY = (500, {"Structural Framing": 300, "Floors": 200})


def week_one(inventory=True):
    return run("semana-1", [
        ("ISS-moved", "g-1", duct("0/1/1", 100, 0.0), BEAM, (0.15, 0.0, 3.2), 0.20),
        ("ISS-fixed", "g-2", duct("0/1/2", 101, 20.0), OTHER_BEAM, (20.15, 0.0, 3.2), 0.20),
        ("ISS-same", "g-3", duct("0/1/3", 102, 45.0), SLAB, (45.0, 0.0, 3.2), 0.10),
    ], census(**{"MIR-EST.rvt": EST_INVENTORY, "MIR-MEP.rvt": (900, {"Ducts": 600, "Walls": 300})}) if inventory else None)


def week_two(inventory=True):
    # MEP republished: new Navisworks paths, new clash GUIDs, new issue ids.
    # Duct 100 dropped 0.8 m (still hits the beam: same two Revit elements),
    # duct 101 was rerouted clear, duct 102 is untouched, and a new crossing
    # appeared where nothing was before.
    return run("semana-2", [
        ("ISS-moved-2", "h-1", duct("0/9/1", 100, 0.0, z=2.4), BEAM, (0.15, 0.0, 2.4), 0.20),
        ("ISS-same", "g-3", duct("0/9/3", 102, 45.0), SLAB, (45.0, 0.0, 3.2), 0.10),
        ("ISS-new", "h-9", duct("0/9/9", 103, 60.0), replace(SLAB, path_id="1/7/2", props={"Id": "301"}),
         (60.0, 0.0, 3.2), 0.05),
    ], census(**{"MIR-EST.rvt": EST_INVENTORY, "MIR-MEP.rvt": (600, {"Ducts": 600})}) if inventory else None)


def test_compares_runs_whose_models_changed():
    changes = compare(week_one(), week_two())
    assert changes["federation_changed"] is True
    assert changes["summary"]["resueltas"] == 1
    assert changes["summary"]["nuevas"] == 1
    assert changes["summary"]["siguen"] == 2
    assert changes["resolution_verified"] is False


def test_pairs_by_revit_element_ids_when_ids_guids_and_position_change():
    changes = compare(week_one(), week_two(), include_matches=True)
    by_previous = {m["previous"]: m for m in changes["matches"]}
    # 0.8 m is beyond the position tolerance: only the ElementIds can pair it.
    assert by_previous["ISS-moved"]["current"] == "ISS-moved-2"
    assert by_previous["ISS-moved"]["method"] == "elements"
    assert changes["no_longer_reported"] == ["ISS-fixed"]
    assert changes["new"] == ["ISS-new"]
    moved = next(c for c in changes["changed_detail"] if c["previous"] == "ISS-moved")
    assert moved["changes"]["centroid_moved_m"][1] == pytest.approx(0.8)


def test_reports_which_models_changed_instead_of_refusing():
    models = compare(week_one(), week_two())["models"]
    assert models["changed"] == ["MIR-MEP.rvt"]
    assert models["unchanged"] == ["MIR-EST.rvt"]
    mep = next(m for m in models["per_model"] if m["model"] == "MIR-MEP.rvt")
    assert mep["inventory"]["item_count"] == [900, 600]
    assert mep["inventory"]["categories"] == {"Walls": [300, 0]}


def test_a_model_change_outside_the_conflicts_is_seen_through_the_inventory():
    # C6: MEP was republished without the architecture it used to carry. None
    # of the elements that still collide moved, so only the inventory shows it.
    before, after = week_one(), week_two()
    for snap in (before, after):
        for entry in snap["elements"].values():
            entry["bbox"] = [0, 0, 0, 1, 1, 1]
    models = compare(before, after)["models"]
    assert models["changed"] == ["MIR-MEP.rvt"]


def test_without_an_inventory_an_unseen_change_is_not_called_unchanged():
    models = compare(week_one(inventory=False), week_two(inventory=False))["models"]
    assert models["changed"] == ["MIR-MEP.rvt"]  # duct 100 moved: seen in the conflicts
    assert models["undetermined"] == ["MIR-EST.rvt"] and models["unchanged"] == []


def legacy(issue_id, guid, centroid, pair=("ARQ", "MEP")):
    """An issue as snapshots written before match_keys existed carry it."""
    return {"issue_id": issue_id, "discipline_pair": list(pair), "representative_clash": guid,
            "centroid": list(centroid), "priority": "low", "severity": 50.0, "responsible": "MEP",
            "clash_count": 1, "max_penetration_mm": 20.0, "level": "02 - Floor"}


def legacy_snapshot(fingerprint, issues):
    return {"schema": "naviscoord.analysis-snapshot/1", "provenance": {"document_fingerprint": fingerprint},
            "issues": issues, "coverage": {}}


def test_shared_clash_guid_wins_over_a_nearer_position_regardless_of_order():
    # The C6 script paired in list order and let position steal a partner that
    # a later issue matched by Navisworks' own clash GUID.
    before = legacy_snapshot("a", [legacy("ISS-a", "gA", (0.0, 0, 0)), legacy("ISS-b", "gB", (0.3, 0, 0))])
    after = legacy_snapshot("b", [legacy("ISS-x", "gB", (0.1, 0, 0))])
    changes = compare(before, after, include_matches=True)
    assert changes["matches"] == [{"previous": "ISS-b", "current": "ISS-x", "method": "clash_guid",
                                   "distance_m": 0.2}]
    assert changes["no_longer_reported"] == ["ISS-a"]
    assert "per_model" in changes["models"] and changes["models"]["per_model"] == []


def test_position_pairs_only_the_same_trades_and_are_flagged():
    before = legacy_snapshot("a", [legacy("ISS-a", "g1", (0, 0, 0)), legacy("ISS-b", "g2", (5, 0, 0))])
    after = legacy_snapshot("b", [legacy("ISS-x", "z1", (0.3, 0, 0)),
                                  legacy("ISS-y", "z2", (5.1, 0, 0), pair=("ARQ", "EST"))])
    changes = compare(before, after)
    assert changes["persisting"] == ["ISS-x"]
    assert changes["no_longer_reported"] == ["ISS-b"] and changes["new"] == ["ISS-y"]
    assert changes["match_methods"] == {"position": 1}
    assert any("solo por posición" in w for w in changes["warnings"])


def test_a_deeper_penetration_is_a_change():
    before = legacy_snapshot("a", [legacy("ISS-a", "g1", (0, 0, 0))])
    deeper = dict(legacy("ISS-a", "g1", (0, 0, 0)), max_penetration_mm=180.0)
    changes = compare(before, legacy_snapshot("a", [deeper]))
    assert changes["changed"] == ["ISS-a"]
    assert changes["changed_detail"][0]["changes"]["max_penetration_mm"] == [20.0, 180.0]


def test_tool_compares_two_saved_snapshots_without_navisworks(tmp_path):
    from naviscoord import mcp_server as m
    first, second = tmp_path / "semana-1.json", tmp_path / "semana-2.json"
    first.write_text(json.dumps(week_one()), encoding="utf-8")
    second.write_text(json.dumps(week_two()), encoding="utf-8")
    result = m.navis_compare_snapshot(str(first), current_path=str(second))
    assert "error" not in result, result
    assert result["summary"]["resueltas"] == 1 and result["summary"]["siguen"] == 2
