"""The bugs that shipped, each pinned by a test that fails on the old code.

Two of them are here because they were confidently wrong rather than
obviously broken — the colour landed on the wrong half of every other issue,
and the root-cause detector reported "not one pass is defined" at 0.9
confidence on models full of sleeves. Nothing about either looked like a
failure from the outside, which is exactly why they need tests.
"""

from __future__ import annotations

from typing import Any

import pytest
from naviscoord.analysis import analyze
from naviscoord.analysis.noise import NoiseFilter
from naviscoord.analysis.rootcause import RootCauseDetector
from naviscoord.model import Clash, ClashExport, ElementRef, Issue
from naviscoord.profile import Profile


def element(
    path_id: str,
    discipline: str,
    category: str = "Pipes",
    name: str = "",
    *,
    at: tuple[float, float, float] = (0.0, 0.0, 0.0),
    size: tuple[float, float, float] = (4.0, 0.1, 0.1),
) -> ElementRef:
    ref = ElementRef(
        path_id=path_id,
        display_name=name or f"{category} {path_id}",
        category=category,
        bbox_min=at,
        bbox_max=(at[0] + size[0], at[1] + size[1], at[2] + size[2]),
    )
    ref.discipline = discipline
    return ref


def clash(guid: str, a: ElementRef, b: ElementRef, *, depth: float = 0.05,
          point: tuple[float, float, float] = (0.0, 0.0, 0.0), level: str = "Nivel 01") -> Clash:
    return Clash(
        guid=guid, test="T", name=guid, a=a, b=b,
        distance_m=-depth, point=point, level=level,
    )


# ------------------------------------------------------- colour by responsible


class TestResponsibleSideResolution:
    """`discipline_pair` is SORTED, so it cannot say which side is which.

    The old code did `elements_b if responsible == discipline_pair[-1] else
    elements_a`, which is right only when the responsible trade happens to
    sort last. On the other half of the issues it painted the immovable
    structure and left the pipe that had to move untouched.
    """

    def test_responsible_on_side_a_colours_side_a(self) -> None:
        issue = Issue(
            issue_id="ISS-0001", kind="pair",
            discipline_pair=("EST", "HID"),   # sorted
            clash_ids=["g1"], centroid=(0, 0, 0), bbox_min=(0, 0, 0), bbox_max=(1, 1, 1),
            responsible="EST", immovable_side="HID",
            elements_a=["1/2/3"], elements_b=["4/5/6"],
            discipline_a="EST", discipline_b="HID",
            elements_by_discipline={"EST": ["1/2/3"], "HID": ["4/5/6"]},
        )
        assert issue.movable_elements() == ["1/2/3"]
        assert issue.immovable_elements() == ["4/5/6"]

    def test_responsible_on_side_b_colours_side_b(self) -> None:
        issue = Issue(
            issue_id="ISS-0002", kind="pair",
            discipline_pair=("EST", "HID"),
            clash_ids=["g1"], centroid=(0, 0, 0), bbox_min=(0, 0, 0), bbox_max=(1, 1, 1),
            responsible="HID", immovable_side="EST",
            elements_a=["1/2/3"], elements_b=["4/5/6"],
            discipline_a="EST", discipline_b="HID",
            elements_by_discipline={"EST": ["1/2/3"], "HID": ["4/5/6"]},
        )
        assert issue.movable_elements() == ["4/5/6"]
        assert issue.immovable_elements() == ["1/2/3"]

    def test_sides_swapped_relative_to_the_sorted_pair(self) -> None:
        """A is HID and B is EST — the sorted pair says the opposite.

        This is the case the old inference got backwards every time.
        """
        issue = Issue(
            issue_id="ISS-0003", kind="pair",
            discipline_pair=("EST", "HID"),        # sorted: EST first
            clash_ids=["g1"], centroid=(0, 0, 0), bbox_min=(0, 0, 0), bbox_max=(1, 1, 1),
            responsible="HID", immovable_side="EST",
            elements_a=["hid/1"], elements_b=["est/1"],
            discipline_a="HID", discipline_b="EST",   # Navisworks order
            elements_by_discipline={"HID": ["hid/1"], "EST": ["est/1"]},
        )
        assert issue.movable_elements() == ["hid/1"]
        # The old rule: responsible ("HID") == discipline_pair[-1] ("HID") →
        # elements_b → ["est/1"], i.e. the structure.
        legacy = issue.elements_b if issue.responsible == issue.discipline_pair[-1] else issue.elements_a
        assert legacy == ["est/1"], "el bug original pintaba el lado equivocado"
        assert issue.movable_elements() != legacy

    def test_same_discipline_both_sides_colours_everything(self) -> None:
        """Honest fallback: colour it all rather than guess a half."""
        issue = Issue(
            issue_id="ISS-0004", kind="pair",
            discipline_pair=("ARQ", "ARQ"),
            clash_ids=["g1"], centroid=(0, 0, 0), bbox_min=(0, 0, 0), bbox_max=(1, 1, 1),
            responsible="ARQ", immovable_side="ARQ",
            elements_a=["a/1"], elements_b=["a/2"],
            discipline_a="ARQ", discipline_b="ARQ",
            elements_by_discipline={"ARQ": ["a/1", "a/2"]},
        )
        assert set(issue.movable_elements()) == {"a/1", "a/2"}

    def test_pipeline_records_the_real_side_disciplines(self, profile: Profile) -> None:
        """End to end: the analysis must fill the side tags it now depends on."""
        beam = element("0/1", "EST", "Structural Framing", size=(6.0, 0.3, 0.5))
        pipe = element("1/1", "HID", "Pipes")
        export = ClashExport(clashes=[clash("g1", pipe, beam)])
        result = analyze(export, profile)

        assert result.issues, "el caso debe producir al menos un problema"
        issue = result.issues[0]
        assert {issue.discipline_a, issue.discipline_b} == {"EST", "HID"}
        assert issue.elements_by_discipline.get("EST") == ["0/1"]
        assert issue.elements_by_discipline.get("HID") == ["1/1"]
        # Whichever side the scorer blamed, the elements returned belong to it.
        assert issue.movable_elements() == issue.elements_by_discipline[issue.responsible]

    def test_cluster_with_mixed_side_order_groups_by_trade(self, profile: Profile) -> None:
        """The same beam is Item1 in one result and Item2 in the next.

        A per-SIDE split is wrong even when it is honest; only a split by the
        element's own discipline survives this.
        """
        beam = element("0/1", "EST", "Structural Framing", size=(6.0, 0.3, 0.5))
        pipe_a = element("1/1", "HID", "Pipes", at=(0.0, 0.0, 0.0))
        pipe_b = element("1/2", "HID", "Pipes", at=(0.1, 0.0, 0.0))
        export = ClashExport(clashes=[
            clash("g1", pipe_a, beam, point=(0.0, 0.0, 0.0)),
            clash("g2", beam, pipe_b, point=(0.1, 0.0, 0.0)),   # sides swapped
        ])
        result = analyze(export, profile)
        for issue in result.issues:
            for code, paths in issue.elements_by_discipline.items():
                for path in paths:
                    expected = "EST" if path.startswith("0/") else "HID"
                    assert code == expected, f"{path} quedó bajo {code}"


# ---------------------------------------------------------- sleeve detection


class TestPenetrationEvidenceSurvivesFiltering:
    """A sleeve is filtered out for doing its job, then counted as absent.

    `designed_pass_through` correctly drops the clash between a sleeve and
    the pipe running through it. The missing-penetration detector then
    scanned only surviving clashes for sleeves, found none, and escalated to
    "the model has not one defined pass" at 0.9 confidence — on a model whose
    passes were modelled correctly.
    """

    @pytest.fixture
    def profile_with_sleeves(self, profile: Profile) -> Profile:
        noise = profile.raw.setdefault("noise_filter", {})
        noise["pass_through_keywords"] = ["sleeve", "pasamuro", "opening"]
        noise["pass_through_categories"] = ["Generic Models"]
        return profile

    def test_filter_indexes_sleeves_it_drops(self, profile_with_sleeves: Profile) -> None:
        sleeve = element("2/1", "EST", "Generic Models", "Pasamuro DN100")
        pipe = element("1/1", "HID", "Pipes")
        result = NoiseFilter(profile_with_sleeves).run([clash("g1", pipe, sleeve)])

        assert result.kept == [], "el cruce con el pasamuro se filtra, como debe"
        assert result.reasons["designed_pass_through"] == 1
        assert [s.path_id for s in result.sleeves] == ["2/1"], (
            "…pero el pasamuro queda indexado como evidencia"
        )

    def test_sleeve_in_a_kept_clash_is_indexed_too(self, profile_with_sleeves: Profile) -> None:
        """Indexing happens before the verdict, not only for dropped clashes."""
        sleeve = element("2/1", "EST", "Generic Models", "Sleeve 200")
        wall = element("0/1", "ARQ", "Walls", size=(4.0, 0.2, 3.0))
        pipe = element("1/1", "HID", "Pipes")
        result = NoiseFilter(profile_with_sleeves).run([
            clash("g1", pipe, sleeve),
            clash("g2", pipe, wall),
        ])
        assert len(result.kept) == 1
        assert [s.path_id for s in result.sleeves] == ["2/1"]

    def test_root_cause_counts_the_filtered_sleeve(self, profile_with_sleeves: Profile) -> None:
        """The end-to-end proof: sleeves_found_in_model must not read 0."""
        sleeve = element("2/1", "EST", "Generic Models", "Pasamuro DN100", at=(10.0, 0.0, 0.0))
        wall = element("0/1", "ARQ", "Walls", at=(0.0, 0.0, 0.0), size=(4.0, 0.2, 3.0))
        pipe = element("1/1", "HID", "Pipes", at=(0.0, 0.0, 1.0))
        pipe_in_sleeve = element("1/2", "HID", "Pipes", at=(10.0, 0.0, 1.0))

        export = ClashExport(clashes=[
            # A real missing pass: pipe through a wall, nowhere near a sleeve.
            clash("g1", pipe, wall, point=(1.0, 0.0, 1.0)),
            # A designed pass: the sleeve is doing its job and gets filtered.
            clash("g2", pipe_in_sleeve, sleeve, point=(10.0, 0.0, 1.0)),
        ])
        result = analyze(export, profile_with_sleeves)

        causes = [c for c in result.root_causes if c.kind == "missing_penetration"]
        assert causes, "debe detectar el paso sin definir"
        cause = causes[0]
        assert cause.evidence["sleeves_found_in_model"] == 1, (
            "el pasamuro filtrado sigue contando como evidencia"
        )
        # With sleeves present the claim is the narrower one: an omission,
        # not "nothing is defined anywhere".
        assert cause.confidence == pytest.approx(0.75)
        assert "otras partes" in cause.detail

    def test_model_with_no_sleeves_still_reports_zero(self, profile_with_sleeves: Profile) -> None:
        """The stronger claim must remain available when it is true."""
        wall = element("0/1", "ARQ", "Walls", at=(0.0, 0.0, 0.0), size=(4.0, 0.2, 3.0))
        pipe = element("1/1", "HID", "Pipes", at=(0.0, 0.0, 1.0))
        export = ClashExport(clashes=[clash("g1", pipe, wall, point=(1.0, 0.0, 1.0))])
        result = analyze(export, profile_with_sleeves)

        causes = [c for c in result.root_causes if c.kind == "missing_penetration"]
        assert causes
        assert causes[0].evidence["sleeves_found_in_model"] == 0
        assert causes[0].confidence == pytest.approx(0.9)

    def test_detector_accepts_sleeves_it_was_handed(self, profile_with_sleeves: Profile) -> None:
        """The plumbing, in isolation: no sleeves in `all_elements`, one passed in."""
        from naviscoord.analysis.cluster import build_clusters

        wall = element("0/1", "ARQ", "Walls", at=(0.0, 0.0, 0.0), size=(4.0, 0.2, 3.0))
        pipe = element("1/1", "HID", "Pipes", at=(0.0, 0.0, 1.0))
        sleeve = element("2/1", "EST", "Generic Models", "Sleeve", at=(50.0, 0.0, 0.0))
        clusters = build_clusters([clash("g1", pipe, wall, point=(1.0, 0.0, 1.0))], profile_with_sleeves)

        detector = RootCauseDetector(profile_with_sleeves)
        without = detector.run(clusters, [wall, pipe])
        with_index = detector.run(clusters, [wall, pipe], sleeves=[sleeve])

        assert _sleeve_count(without) == 0
        assert _sleeve_count(with_index) == 1

    def test_whole_pipeline_from_a_bridge_payload(self, profile_with_sleeves: Profile) -> None:
        """The same verdict, but entering through the door the addin uses.

        Every other test here builds `ClashExport` from Python objects, which
        skips `from_json` — the one step that has to agree with the C# side
        field by field. That gap is not hypothetical: the addin was shipping
        an empty ``source_file`` for every element of a real federation, and
        no Python test could have seen it because none of them parsed a
        bridge payload. The shape below was copied from a live
        ``clash/export`` response, keys and all.

        The sample models available for automated testing are CAD solids with
        no walls, pipes or sleeves, so this cannot come from a real document;
        it is a contract test over the ingestion path, not proof that the
        detector fires on any particular building.
        """
        def side(path_id: str, category: str, name: str, source: str,
                 at: tuple[float, float, float],
                 size: tuple[float, float, float]) -> dict[str, Any]:
            x, y, z = at
            dx, dy, dz = size
            return {
                "path_id": path_id,
                "display_name": name,
                "category": category,
                "model_index": int(path_id.split("/")[0]),
                "source_file": source,
                "parent_path_id": "",
                "parent_name": "",
                "props": {},
                "bbox_min": [x - dx / 2, y - dy / 2, z - dz / 2],
                "bbox_max": [x + dx / 2, y + dy / 2, z + dz / 2],
            }

        def hit(guid: str, a: dict[str, Any], b: dict[str, Any],
                point: tuple[float, float, float]) -> dict[str, Any]:
            return {
                "guid": guid,
                "test": "EST vs HID",
                "name": f"Conflicto{guid}",
                "status": "New",
                "test_type": "Hard",
                "distance_m": -0.05,
                "point": list(point),
                "level": "Nivel 03",
                "grid": "",
                "a": a,
                "b": b,
            }

        # The pipes need a genuinely linear bounding box: the detector only
        # counts a crossing when the non-host side looks like a run, which is
        # what tells a duct apart from a piece of equipment sitting in a wall.
        wall = side("0/1", "Walls", "Muro 20cm", "ARQ-TORRE.rvt", (0.0, 0.0, 1.0), (4.0, 0.2, 3.0))
        pipe = side("1/1", "Pipes", "Tubería DN100", "HID-TORRE.rvt", (0.0, 0.0, 1.0), (12.0, 0.1, 0.1))
        sleeve = side("2/1", "Generic Models", "Pasamuro DN100", "EST-TORRE.rvt", (10.0, 0.0, 1.0), (0.3, 0.3, 0.3))
        pipe_in_sleeve = side("1/2", "Pipes", "Tubería DN100", "HID-TORRE.rvt", (10.0, 0.0, 1.0), (12.0, 0.1, 0.1))

        payload = {
            "schema": "naviscoord.clashexport/1",
            "document": {"title": "federado.nwf", "models": 3, "units": "Meters"},
            "models": [{"index": 0, "source_file": "ARQ-TORRE.rvt"}],
            "tests": ["EST vs HID"],
            "truncated": False,
            "clashes": [
                hit("g1", pipe, wall, (0.0, 0.0, 1.0)),
                # The sleeve doing its job: filtered out as a designed pass,
                # and it must STILL be counted as evidence downstream.
                hit("g2", pipe_in_sleeve, sleeve, (10.0, 0.0, 1.0)),
            ],
        }

        export = ClashExport.from_json(payload)
        assert [c.a.source_file for c in export.clashes] == ["HID-TORRE.rvt", "HID-TORRE.rvt"], (
            "from_json debe conservar source_file: sin él ninguna regla de disciplina puede casar"
        )

        result = analyze(export, profile_with_sleeves)
        causes = [c for c in result.root_causes if c.kind == "missing_penetration"]
        assert causes, "el cruce sin paso debe detectarse llegando por el payload del bridge"
        cause = causes[0]
        assert cause.evidence["sleeves_found_in_model"] == 1
        assert cause.confidence == pytest.approx(0.75)
        assert "otras partes" in cause.detail


def _sleeve_count(causes: list[Any]) -> int:
    for cause in causes:
        if cause.kind == "missing_penetration":
            return int(cause.evidence["sleeves_found_in_model"])
    return -1
