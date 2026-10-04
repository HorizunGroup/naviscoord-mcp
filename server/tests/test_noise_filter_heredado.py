"""A profile without a noise filter must not count Resolved clashes as open.

Course class C6 (2026-10-04): the Mirador profile — the add-in's format, with
sets and clash pairs but no `noise_filter` — read `drop_statuses` as empty,
so the week-2 run counted as active the 211 clashes Navisworks itself marked
«Resuelto» (59 in EST VS MEP, 152 in ARQ VS MEP): 1.420 issues instead of
1.335. Each test below fails on the code before the fix.
"""
from __future__ import annotations

from naviscoord.analysis import analyze
from naviscoord.analysis.noise import NoiseFilter
from naviscoord.model import Clash, ClashExport, ElementRef
from naviscoord.profile import Profile

from test_comite_obra import COMITE_PROFILE, comite_export


def _clash(guid: str, status: str, x: float) -> Clash:
    beam = ElementRef(path_id=f"b{guid}", category="Structural Framing", discipline="EST",
                      source_file="EST.rvt", bbox_min=(x, 0, 3), bbox_max=(x + .3, 5, 3.6))
    duct = ElementRef(path_id=f"d{guid}", category="Ducts", discipline="MEP",
                      source_file="MEP.rvt", bbox_min=(x - 1, 2, 3.1), bbox_max=(x + 1, 2.4, 3.5))
    return Clash(guid, "EST VS MEP 1mm", guid, beam, duct, status=status, distance_m=-0.2, point=(x, 2.2, 3.3))


def test_a_profile_without_noise_filter_inherits_it():
    profile = Profile.from_json(COMITE_PROFILE)
    assert "noise_filter" not in COMITE_PROFILE
    assert "noise_filter" in profile.inherited_sections
    assert {s.lower() for s in profile.noise_param("drop_statuses", [])} == {"approved", "resolved"}


def test_resolved_and_approved_clashes_do_not_count_as_active():
    profile = Profile.from_json(COMITE_PROFILE)
    clashes = [_clash("1", "Active", 0), _clash("2", "Resolved", 10), _clash("3", "New", 20),
               _clash("4", "Approved", 30)]
    result = NoiseFilter(profile).run(clashes)
    assert [c.guid for c in result.kept] == ["1", "3"]
    assert result.reasons["status_excluded"] == 2


def test_a_resolved_clash_does_not_become_an_issue():
    raw = comite_export(40)
    for clash in raw["clashes"][:10]:
        clash["status"] = "Resolved"
    profile = Profile.from_json(COMITE_PROFILE)
    result = analyze(ClashExport.from_json(raw), profile)
    resolved = {c["guid"] for c in raw["clashes"][:10]}
    assert not resolved & {g for issue in result.issues for g in issue.clash_ids}
    assert result.filtering.reasons["status_excluded"] == 10


def test_a_declared_filter_without_drop_statuses_still_drops_resolved():
    profile = Profile.from_json({**COMITE_PROFILE, "noise_filter": {"min_penetration_m": 0.005}})
    assert profile.noise_param("min_penetration_m", 0) == 0.005  # its own value stays
    assert {s.lower() for s in profile.noise_param("drop_statuses", [])} == {"approved", "resolved"}


def test_a_profile_that_keeps_resolved_on_purpose_is_obeyed():
    profile = Profile.from_json({**COMITE_PROFILE, "noise_filter": {"drop_statuses": []}})
    assert profile.noise_param("drop_statuses", None) == []
    assert "noise_filter" not in profile.inherited_sections
