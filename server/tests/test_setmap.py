"""Reading the sets a team already made, instead of building 200.000 nodes.

`sets/build` sorts every node of every model into explicit sets so the matrix
has something named `NC - EST` to point at. On a real federation that walk
took Navisworks down twice — for an answer the document was already holding.
A coordinating team has search sets, and on the model this came from they
were called `STR-WALL`, `ARCH-GB-WALL`, `HVAC`, `PLUM-TYP` and `FRSP`.

The names below are those. They are the whole point of the test: a mapping
that works on invented names like "EST" and "ARQ" proves nothing.
"""

from __future__ import annotations

import pytest
from naviscoord.profile import Profile
from naviscoord.setmap import map_sets, plan_pairs

#: Verbatim from a real coordination model.
REAL_SETS = [
    "STR-STF",
    "STR-COL",
    "STR-WALL",
    "STR-FLR",
    "ARCH-GB-WALL",
    "ARCH-WINDOR",
    "HVAC",
    "PLUM-TYP",
    "PLUM-GF",
    "FRSP",
]


@pytest.fixture
def mapping(profile: Profile):
    return map_sets(REAL_SETS, profile)


class TestRealSetNamesResolve:
    def test_every_set_finds_its_trade(self, mapping) -> None:
        assert mapping.unmatched == [], (
            f"sin reconocer: {mapping.unmatched}. Si un conjunto real no casa, "
            "el par que dependa de él se salta en silencio."
        )

    def test_structure_collects_all_four_of_its_sets(self, mapping) -> None:
        assert sorted(mapping.sets_for("EST")) == [
            "STR-COL", "STR-FLR", "STR-STF", "STR-WALL"
        ]

    def test_plumbing_is_found_under_its_own_name(self, mapping) -> None:
        """`PLUM-TYP` and `PLUM-GF` are the trade nothing was comparing."""
        assert sorted(mapping.sets_for("HID")) == ["PLUM-GF", "PLUM-TYP"]

    def test_fire_protection_resolves(self, mapping) -> None:
        assert mapping.sets_for("RCI") == ["FRSP"]

    def test_architecture_and_ventilation(self, mapping) -> None:
        assert sorted(mapping.sets_for("ARQ")) == ["ARCH-GB-WALL", "ARCH-WINDOR"]
        assert mapping.sets_for("HVAC") == ["HVAC"]


class TestOwnedSetsWin:
    def test_a_set_we_built_beats_the_project_ones(self, profile: Profile) -> None:
        """Mixing them would compare a trade against itself."""
        mapping = map_sets(REAL_SETS + ["NC - EST"], profile)
        assert mapping.sets_for("EST") == ["NC - EST"]
        assert mapping.owned["EST"] == "NC - EST"

    def test_other_trades_are_unaffected(self, profile: Profile) -> None:
        mapping = map_sets(REAL_SETS + ["NC - EST"], profile)
        assert mapping.sets_for("RCI") == ["FRSP"]


class TestPlanningThePairs:
    def test_the_pair_nobody_was_testing_becomes_buildable(
        self, profile: Profile, mapping
    ) -> None:
        """Structure against plumbing: modelled, and compared by no test."""
        buildable, _ = plan_pairs(
            profile.section("clash_matrix").get("pairs", []), mapping
        )
        pairs = {(entry["a"], entry["b"]) for entry in buildable}
        assert ("EST", "HID") in pairs
        entry = next(e for e in buildable if (e["a"], e["b"]) == ("EST", "HID"))
        assert sorted(entry["sets_a"]) == ["STR-COL", "STR-FLR", "STR-STF", "STR-WALL"]
        assert sorted(entry["sets_b"]) == ["PLUM-GF", "PLUM-TYP"]

    def test_absent_trades_are_reported_not_dropped(
        self, profile: Profile, mapping
    ) -> None:
        """"Not compared" and "not in the model" are different facts."""
        _, skipped = plan_pairs(
            profile.section("clash_matrix").get("pairs", []), mapping
        )
        electrical = [s for s in skipped if "ELE" in (s["a"], s["b"])]
        assert electrical, "el modelo no trae eléctrica y eso tiene que decirse"
        assert all("no hay conjunto de selección" in s["reason"] for s in electrical)

    def test_tolerance_and_type_survive_the_planning(
        self, profile: Profile, mapping
    ) -> None:
        buildable, _ = plan_pairs(
            profile.section("clash_matrix").get("pairs", []), mapping
        )
        entry = next(e for e in buildable if (e["a"], e["b"]) == ("EST", "HVAC"))
        assert entry["type"] == "Hard"
        assert entry["tolerance_m"] == pytest.approx(0.001)

    def test_nothing_needs_a_walk_of_the_model(self, mapping) -> None:
        """The whole point: this is names and a profile, no geometry."""
        assert mapping.to_json()["by_discipline"]
