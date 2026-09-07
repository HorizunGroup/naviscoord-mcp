"""The engine against a slice of a real model.

Every bug this suite pins was found on real data, and every other test in it
is synthetic — built from the shape of the bug after somebody had already
seen it. That ordering is the problem: a fixture written from a known failure
cannot surprise you, and each of these failures was a surprise.

`fixtures/torre-demo-export.json` is 90 crossings cut out of an actual
coordination model, anonymised, holding one worked example of every trap the
engine walked into: structural and architectural walls that share a Revit
category, window blinds filed under `Generic Models`, a sprinkler branch
whose overlap is its own diameter, a riser whose overlap is the slab's
thickness, a label that spans six storeys, and real deep interferences that
must survive all of it.

The assertions are deliberately about BEHAVIOUR rather than exact counts. A
test that pins "466 issues" breaks on every tuning change and teaches nobody
anything; these say what must remain true of any correct run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from naviscoord.analysis import analyze
from naviscoord.matrix import build_matrix
from naviscoord.model import ClashExport
from naviscoord.narrative import compose
from naviscoord.profile import Profile

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "torre-demo-export.json"


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def result(raw: dict[str, Any]) -> Any:
    return analyze(ClashExport.from_json(raw), Profile.load())


class TestTheFixtureIsFitToShip:
    """It came out of a client's model, so it must not still say so."""

    def test_nothing_identifying_survived(self) -> None:
        blob = FIXTURE.read_text(encoding="utf-8")
        for leak in ("LP01", "Le Parc", "KREA", "OneDrive", "C:\\", "Users\\"):
            assert leak not in blob, f"el fixture filtra «{leak}»"

    def test_it_stays_small_enough_to_read(self) -> None:
        assert FIXTURE.stat().st_size < 400_000

    def test_it_still_contains_every_trap(self, raw: dict[str, Any]) -> None:
        names = " ".join(json.dumps(c) for c in raw["clashes"])
        assert "WIN_BLIND" in names, "las persianas bajo Generic Models"
        assert "ARC_2ND" in names, "la etiqueta que cruza plantas"
        assert "Fire Protection" in names, "el ramal cuyo solape es su diámetro"
        assert any(
            c["a"]["category"] == "Walls" and c["b"]["category"] == "Walls"
            for c in raw["clashes"]
        ), "muro estructural contra muro arquitectónico"


class TestWhatMustBeTrueOfAnyCorrectRun:
    def test_walls_of_different_trades_are_not_one_trade(self, result: Any) -> None:
        """The 477-clash bug: both sides tagged ARQ, whole test eaten."""
        assert result.filtering.reasons.get("architectural_self_overlap", 0) == 0
        pairs = {c.discipline_pair for c in result.filtering.kept}
        assert ("ARQ", "EST") in pairs or ("EST", "HVAC") in pairs

    def test_blinds_without_host_evidence_remain_reviewable(self, result: Any) -> None:
        """Real fixture has no host/contact IDs; small depth does not prove intent."""
        blinds = [c for c in result.filtering.kept
                  if any("WIN_BLIND" in (side.parent_name or "") for side in (c.a,c.b))]
        assert len(blinds) == 12
        assert any(c.penetration_m <= .025 for c in blinds)
        assert not any(reason == "designed_adjacency" and
                       any("WIN_BLIND" in (side.parent_name or "") for side in (c.a,c.b))
                       for c,reason in result.filtering.dropped)

    def test_no_elevation_cause_is_invented(self, result: Any) -> None:
        """Overlap equal to the pipe's diameter, or to the slab's thickness."""
        causes = [c for c in result.root_causes if c.kind == "systemic_elevation"]
        assert not causes, [c.title for c in causes]

    def test_a_label_spanning_the_building_is_not_a_storey(self, result: Any) -> None:
        assert "ARC_2ND" in result.levels.cross_cutting
        assert all(
            "ARC_2ND" not in storey.aliases for storey in result.levels.storeys
        )

    def test_deep_interferences_all_survive(self, result: Any) -> None:
        """Whatever the filters do, nothing over 100 mm may be dropped."""
        deep_in = [
            c for c in ClashExport.from_json(
                json.loads(FIXTURE.read_text(encoding="utf-8"))
            ).clashes
            if c.penetration_m > 0.10
        ]
        kept_deep = [c for c in result.filtering.kept if c.penetration_m > 0.10]
        assert len(kept_deep) == len(deep_in), (
            f"se perdieron {len(deep_in) - len(kept_deep)} interferencias profundas"
        )

    def test_elements_are_counted_once(self, result: Any, raw: dict[str, Any]) -> None:
        sides = len(raw["clashes"]) * 2
        assert result.tagging.total < sides

    def test_the_verdict_admits_a_partial_matrix(self, result: Any) -> None:
        profile = Profile.load()
        narrative = compose(result, build_matrix(result, profile), profile)
        assert result.coverage.complete is False
        assert narrative.readiness == "sin evidencia suficiente"

    def test_no_claim_outruns_its_evidence(self, result: Any) -> None:
        """Nothing may be published above 0.75 on an absence."""
        for cause in result.root_causes:
            if cause.kind == "missing_penetration":
                assert cause.confidence <= 0.75, cause.title
