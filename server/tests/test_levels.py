"""Storey reconciliation tests.

The failure this prevents is quiet: the same floor appears twice in the work
plan under two names, the team gets convened twice for one place, and every
per-level count is wrong without anything looking broken.
"""

from __future__ import annotations

from naviscoord import samples as synth
from naviscoord.analysis.levels import normalise_levels
from naviscoord.model import ClashExport


def _clashes(entries):
    """entries: list of (level, z).

    Elements deliberately carry NO Level or Layer property. The extractor
    falls back to those when a clash reports no level — correct behaviour,
    but it would quietly supply the value these tests are trying to leave
    empty, and every "no level" case would silently test nothing.
    """
    raw = []
    for index, (level, z) in enumerate(entries):
        beam = synth.element(
            f"est/{index}", "Structural Framing", "VIGA",
            ((-1, -1, z), (1, 20, z + 0.6)), source_file="EST.rvt",
        )
        pipe = synth.element(
            f"hid/{index}", "Pipes", "Tubería", ((0, 0, z), (10, 0.1, z + 0.1)),
            source_file="HID.rvt", **{"System Name": "AGUA"},
        )
        raw.append(
            synth.clash(f"c{index}", beam, pipe, point=(0.0, 0.0, z),
                        penetration_m=0.05, level=level)
        )
    return ClashExport.from_json(synth.export(raw)).clashes


def test_same_elevation_different_names_are_one_storey():
    """The real case: three models, three names, one floor."""
    clashes = _clashes([
        ("N1", 3.0), ("N1", 3.1), ("N1", 3.2),
        ("ORG_NIVEL_01", 3.05), ("ORG_NIVEL_01", 3.15),
        ("Level 1", 3.1),
    ])
    result = normalise_levels(clashes)

    assert len(result.storeys) == 1
    # The name carrying the most clashes wins: it is the one people know.
    assert result.storeys[0].canonical == "N1"
    assert result.storeys[0].aliases == ["Level 1", "ORG_NIVEL_01"]
    assert {c.level for c in clashes} == {"N1"}


def test_genuinely_different_storeys_stay_apart():
    """The control: merging two real floors would be the worse error."""
    clashes = _clashes([("N1", 3.0), ("N1", 3.1), ("N2", 6.0), ("N2", 6.1)])
    result = normalise_levels(clashes)
    assert len(result.storeys) == 2
    assert {s.canonical for s in result.storeys} == {"N1", "N2"}


def test_chained_labels_do_not_swallow_a_second_storey():
    """Single-linkage creep: each label near the last, the group spanning two floors.

    Found on a live model, where five labels chained into one bucket and
    merged N2 with N3 and N4 — two real storeys reported as one.
    """
    clashes = _clashes([
        ("N2", 5.0), ("N2", 5.1),
        ("Nivel 3", 6.2), ("Nivel 3", 6.3),
        ("N3", 7.4), ("N3", 7.5),
        ("Nivel 4", 8.6), ("Nivel 4", 8.7),
    ])
    result = normalise_levels(clashes, tolerance_m=1.5)
    # 3.7 m of spread cannot be one floor, whatever the neighbour gaps are.
    assert len(result.storeys) > 1
    for storey in result.storeys:
        members = [c for c in clashes if c.level == storey.canonical]
        span = max(c.point[2] for c in members) - min(c.point[2] for c in members)
        assert span <= 1.6, f"{storey.canonical} abarca {span:.1f} m"


def test_merges_are_reported_not_silent():
    clashes = _clashes([("CUB", 20.0), ("ROOF", 20.1), ("ROOF", 20.2)])
    result = normalise_levels(clashes)
    notes = result.notes()
    assert notes
    assert "mismo piso" in notes[0]
    assert "CUB" in notes[0] or "ROOF" in notes[0]


def test_unlabelled_clashes_are_placed_by_elevation():
    """A crossing with no level still happened somewhere."""
    clashes = _clashes([("N1", 3.0), ("N1", 3.1), ("", 3.05), ("N2", 6.0)])
    result = normalise_levels(clashes)
    assert result.assigned_by_elevation == 1
    assert all(c.level for c in clashes)


def test_far_from_any_storey_is_left_alone():
    """Better no level than a wrong one."""
    clashes = _clashes([("N1", 3.0), ("N1", 3.1), ("", 90.0)])
    result = normalise_levels(clashes)
    assert result.assigned_by_elevation == 0
    assert any(not c.level for c in clashes)


def test_no_levels_at_all_is_not_an_error():
    clashes = _clashes([("", 3.0), ("", 6.0)])
    result = normalise_levels(clashes)
    assert result.storeys == []
    assert result.notes() == []
