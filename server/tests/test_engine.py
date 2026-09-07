"""Engine tests, written against planted defects.

Each fixture contains one known pathology. A test asserts the engine finds
that and nothing invented — the failure mode that matters here is not a
crash, it is a plausible-looking ranking that is quietly wrong.
"""

from __future__ import annotations

import pytest

from naviscoord import samples as synth
from naviscoord.analysis import analyze, build_clusters, hotspots
from naviscoord.analysis.discipline import DisciplineTagger
from naviscoord.analysis.noise import NoiseFilter
from naviscoord.model import ClashExport


def _tagged(raw: dict, profile) -> ClashExport:
    export = ClashExport.from_json(raw)
    DisciplineTagger(profile).tag_export(export)
    return export


# --------------------------------------------------------------- profile


def test_default_profile_is_internally_consistent(profile):
    assert profile.validate() == []
    assert abs(sum(profile.section("severity")["weights"].values()) - 1.0) < 1e-9


def test_filename_tokens_do_not_match_substrings(profile):
    # "ele" must not fire on ELEVACIONES, or every architectural sheet model
    # would be tagged electrical.
    assert profile.discipline_for_filename("MODELO-ARQ-ELEVACIONES.rvt") == "ARQ"
    assert profile.discipline_for_filename("PROY-ELE.rvt") == "ELE"
    assert profile.discipline_for_filename("PROY-HVAC-TORRE1.rvt") == "HVAC"
    assert profile.discipline_for_filename("cualquier-cosa.rvt") is None


# ------------------------------------------------------------ discipline


def test_categories_drive_discipline(profile):
    export = _tagged(synth.full_project_case(), profile)
    seen = {(c.a.category, c.a.discipline) for c in export.clashes}
    seen |= {(c.b.category, c.b.discipline) for c in export.clashes}
    mapping = dict(seen)
    assert mapping["Structural Framing"] == "EST"
    assert mapping["Ducts"] == "HVAC"
    assert mapping["Walls"] == "ARQ"
    assert mapping["Cable Trays"] == "ELE"
    assert mapping["Sprinklers"] == "RCI"


def test_system_name_splits_generic_piping(profile):
    """Both are plain `Pipes`; only the system name tells them apart."""
    export = _tagged(synth.repeated_typology_case(), profile)
    riser = export.clashes[0].b
    assert riser.category == "Pipes"
    assert riser.discipline == "SAN"


def test_tagging_reports_its_own_blind_spots(profile):
    raw = synth.noise_case()
    orphan = raw["clashes"][0]["a"]
    orphan["category"] = "Categoría Inventada"
    orphan["source_file"] = "desconocido.rvt"
    orphan["props"] = {}  # no system name either: genuinely no evidence left
    orphan["parent_name"] = ""
    export = ClashExport.from_json(raw)
    report = DisciplineTagger(profile).tag_export(export)
    assert report.by_discipline["OTRO"] >= 1
    assert "Categoría Inventada" in report.unclassified_categories


def test_total_tagging_failure_is_loud(profile):
    """A blind run must announce itself, not return confident-looking numbers.

    This guard is what caught a real defect: the addin was emitting an empty
    ``source_file`` for every element of a five-model federation, so no
    filename rule could fire and 100% of the elements fell to OTRO. The
    severity numbers still came out looking perfectly ordinary. The only
    reason the failure was visible at all was this warning, so it is worth a
    test of its own — a silent tagger turns the next such bug into wrong
    priorities that nobody questions.
    """
    raw = synth.noise_case()
    for clash in raw["clashes"]:
        for side in ("a", "b"):
            clash[side]["source_file"] = ""
            clash[side]["category"] = "3D Face Set"
            clash[side]["props"] = {}
            clash[side]["parent_name"] = ""
            # The display name is evidence too: leaving "Rociador" in it lets
            # the system-keyword rule rescue one element and the run stops
            # being the total blackout this test is about.
            clash[side]["display_name"] = "Solid"

    report = DisciplineTagger(profile).tag_export(ClashExport.from_json(raw))

    assert report.unclassified_ratio == 1.0
    assert report.resolved_by["unresolved"] == report.total
    warning = " ".join(report.warnings())
    assert "100%" in warning
    assert "3D Face Set" in warning
    # The empty source file must still be reported as its own bucket rather
    # than quietly dropped: that grouping is what points at the culprit.
    assert report.by_source


# ----------------------------------------------------------------- noise


def test_noise_filter_drops_exactly_the_planted_noise(profile):
    export = _tagged(synth.noise_case(), profile)
    result = NoiseFilter(profile).run(export.clashes)

    assert [c.guid for c in result.kept] == ["real-hit"]
    assert result.reasons == {
        "same_system_fitting": 1,
        "insulation_graze": 1,
        "status_excluded": 1,
        "below_tolerance": 1,
        "designed_pass_through": 1,
    }


def test_every_dropped_clash_keeps_its_reason(profile):
    export = _tagged(synth.noise_case(), profile)
    result = NoiseFilter(profile).run(export.clashes)
    assert len(result.dropped) == 5
    assert all(reason for _, reason in result.dropped)


def test_flush_mounted_devices_are_not_clashes(profile):
    """A receptacle embedded in its wall is how receptacles work.

    Found on a real project: 600+ such pairs produced textbook systematic
    patterns that the root-cause detector reported at 100% confidence as
    whole systems set at the wrong elevation.
    """
    wall = synth.element(
        "arq/w1", "Walls", "Muro Drywall", ((0, 0, 0), (10, 0.15, 3)),
        source_file="PROY-ARQ.rvt",
    )
    outlet = synth.element(
        "ele/o1", "Electrical Fixtures", "DUPLEX_REC", ((4, 0.02, 0.3), (4.1, 0.12, 0.45)),
        source_file="PROY-ELE.rvt",
    )
    outlet["props"]["NC:HostPath"] = wall["path_id"]
    panel = synth.element(
        "ele/p1", "Electrical Equipment", "TABLERO TD-1", ((6, -0.4, 1.0), (6.6, 0.15, 2.0)),
        source_file="PROY-ELE.rvt",
    )

    raw = synth.export([
        synth.clash("flush", wall, outlet, point=(4.05, 0.07, 0.38), penetration_m=0.10),
        synth.clash("buried", wall, panel, point=(6.3, 0.05, 1.5), penetration_m=0.40),
    ])
    export = _tagged(raw, profile)
    result = NoiseFilter(profile).run(export.clashes)

    assert result.reasons["designed_embedment"] == 1
    # The panel goes far deeper than any device mounts: that stays a problem.
    assert [c.guid for c in result.kept] == ["buried"]


def test_deep_overlap_inside_one_system_is_not_noise(profile):
    """A fitting 300 mm inside its own pipe is a modelling error, not noise."""
    raw = synth.noise_case()
    raw["clashes"][0]["distance_m"] = -0.30
    export = _tagged(raw, profile)
    result = NoiseFilter(profile).run(export.clashes)
    assert "n-fitting" in {c.guid for c in result.kept}


# ------------------------------------------------------------ clustering


def test_multilayer_wall_collapses_to_one_issue(profile):
    export = _tagged(synth.multilayer_wall_case(), profile)
    clusters = build_clusters(export.clashes, profile)
    assert len(clusters) == 1
    assert len(clusters[0].clashes) == 4
    assert clusters[0].kind == "pair"


def test_pipe_rack_collapses_to_one_issue(profile):
    export = _tagged(synth.pipe_rack_case(), profile)
    clusters = build_clusters(export.clashes, profile)
    assert len(clusters) == 1
    assert len(clusters[0].clashes) == 8
    assert clusters[0].kind == "cluster"


def test_distant_crossings_stay_separate(profile):
    """Ten beams six metres apart are ten places to stand, not one."""
    export = _tagged(synth.systemic_elevation_case(), profile)
    clusters = build_clusters(export.clashes, profile)
    assert len(clusters) == 10


def test_clustering_is_order_independent(profile):
    export = _tagged(synth.full_project_case(), profile)
    forward = build_clusters(list(export.clashes), profile)
    backward = build_clusters(list(reversed(export.clashes)), profile)
    assert sorted(len(c.clashes) for c in forward) == sorted(len(c.clashes) for c in backward)


# ------------------------------------------------------------- severity


def test_structure_outranks_cosmetic(profile):
    result = analyze(ClashExport.from_json(synth.full_project_case()), profile)
    top = result.issues[0]
    assert "EST" in top.discipline_pair
    assert top.priority in {"critical", "high"}


def test_immovable_side_decides_who_moves(profile):
    result = analyze(ClashExport.from_json(synth.systemic_elevation_case()), profile)
    issue = result.issues[0]
    assert issue.immovable_side == "EST"
    assert issue.responsible == "HVAC"


def test_gravity_systems_are_treated_as_hard_to_move(profile):
    """A sanitary riser can be moved on paper but not without redesign."""
    result = analyze(ClashExport.from_json(synth.repeated_typology_case()), profile)
    issue = result.issues[0]
    assert issue.immovable_side == "EST"
    assert issue.severity > 50.0


def test_every_issue_explains_itself(profile):
    result = analyze(ClashExport.from_json(synth.full_project_case()), profile)
    for issue in result.issues:
        assert issue.why, f"{issue.issue_id} sin justificación"
        assert issue.suggested_action
        assert 0.0 <= issue.severity <= 100.0


def test_issue_ids_follow_priority_order(profile):
    result = analyze(ClashExport.from_json(synth.full_project_case()), profile)
    severities = [issue.severity for issue in result.issues]
    assert severities == sorted(severities, reverse=True)
    assert len({i.issue_id for i in result.issues}) == len(result.issues)
    assert all(i.issue_id.startswith("ISS-") for i in result.issues)


# ----------------------------------------------------------- root causes


def test_systemic_elevation_is_detected(profile):
    result = analyze(ClashExport.from_json(synth.systemic_elevation_case()), profile)
    causes = [c for c in result.root_causes if c.kind == "systemic_elevation"]
    assert len(causes) == 1
    cause = causes[0]
    assert cause.confidence >= 0.6
    assert cause.clash_count == 10
    assert cause.evidence["system"] == "AA-SUMINISTRO-01"
    assert 240.0 <= cause.evidence["mean_overlap_mm"] <= 260.0
    assert cause.evidence["vertical_separation"]["up_m"] > .25
    assert cause.evidence["vertical_separation"]["down_m"] > .9


def test_scattered_clashes_do_not_fake_a_systemic_cause(profile):
    """Vary the depths and the theory must collapse — that is the control."""
    raw = synth.systemic_elevation_case()
    for index, clash in enumerate(raw["clashes"]):
        clash["a"]["bbox_max"][2] = 3.60 + 0.25 * index  # wildly inconsistent
    result = analyze(ClashExport.from_json(raw), profile)
    assert not [c for c in result.root_causes if c.kind == "systemic_elevation"]


def test_repeated_typology_is_detected(profile):
    result = analyze(ClashExport.from_json(synth.repeated_typology_case()), profile)
    causes = [c for c in result.root_causes if c.kind == "repeated_typology"]
    assert causes
    assert len(causes[0].evidence["levels"]) == 5


def test_congested_zone_is_detected(profile):
    result = analyze(ClashExport.from_json(synth.congested_zone_case()), profile)
    causes = [c for c in result.root_causes if c.kind == "congested_zone"]
    assert causes
    assert causes[0].evidence["disciplines"]


def test_missing_penetration_is_detected(profile):
    result = analyze(ClashExport.from_json(synth.multilayer_wall_case()), profile)
    causes = [c for c in result.root_causes if c.kind == "missing_penetration"]
    assert causes
    assert causes[0].evidence["sleeves_found_in_model"] == 0


# --------------------------------------------------------------- folding


def test_repeated_detail_takes_one_slot_not_five(profile):
    """Five floors of the same crossing is one decision, not five."""
    result = analyze(ClashExport.from_json(synth.repeated_typology_case()), profile)
    assert len(result.issues) == 5
    assert len(result.decisions) == 1
    representative = result.decisions[0]
    assert representative.folds == 4
    assert all(i.folded_into == representative.issue_id for i in result.issues[1:])


def test_folded_issues_keep_their_data(profile):
    """Folding is a ranking decision, not deletion: write-back still needs them."""
    result = analyze(ClashExport.from_json(synth.repeated_typology_case()), profile)
    for issue in result.issues:
        assert issue.issue_id
        assert issue.clash_ids
        assert issue.severity > 0.0


def test_congested_zones_are_not_folded(profile):
    """Sharing a location is not sharing a fix — those stay separate."""
    result = analyze(ClashExport.from_json(synth.congested_zone_case()), profile)
    assert [c.kind for c in result.root_causes] == ["congested_zone"]
    assert len(result.decisions) == len(result.issues)


def test_top_of_the_list_holds_distinct_problems(profile):
    """No foldable cause may occupy two slots in the top.

    Non-foldable causes still can, and should: a dozen problems sharing a
    congested shaft are a dozen decisions that happen to share an address.
    """
    result = analyze(ClashExport.from_json(synth.full_project_case()), profile)
    foldable = profile.folding_kinds()
    causes = [
        issue.root_cause.get("cause_id")
        for issue in result.top(10)
        if issue.root_cause.get("kind") in foldable
    ]
    assert len(causes) == len(set(causes)), "una misma causa plegable ocupa dos puestos"


# -------------------------------------------------------------- pipeline


def test_pipeline_compresses_the_raw_run(profile):
    export = ClashExport.from_json(synth.full_project_case())
    result = analyze(export, profile)
    assert result.raw_clash_count > len(result.issues)
    assert result.compression > 1.0
    assert result.filtering.dropped_count == 5


def test_summary_stays_small_enough_to_reason_over(profile):
    result = analyze(ClashExport.from_json(synth.full_project_case()), profile)
    summary = result.summary(limit=20)
    assert len(summary["top_issues"]) <= 20
    assert set(summary["by_priority"]) == {"critical", "high", "medium", "low"}
    assert summary["raw_clashes"] == result.raw_clash_count


def test_hotspots_rank_by_accumulated_severity(profile):
    result = analyze(ClashExport.from_json(synth.full_project_case()), profile)
    zones = hotspots(result, cell_size=5.0, limit=5)
    assert zones
    totals = [zone["total_severity"] for zone in zones]
    assert totals == sorted(totals, reverse=True)


def test_empty_export_does_not_explode(profile):
    result = analyze(ClashExport.from_json(synth.export([])), profile)
    assert result.issues == []
    assert result.root_causes == []


def test_unknown_schema_is_rejected():
    with pytest.raises(ValueError, match="schema"):
        ClashExport.from_json({"schema": "otra.cosa/9", "clashes": []})
