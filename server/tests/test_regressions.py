"""The bugs that shipped, each pinned by a test that fails on the old code.

Two of them are here because they were confidently wrong rather than
obviously broken — the colour landed on the wrong half of every other issue,
and the root-cause detector reported "not one pass is defined" at 0.9
confidence on models full of sleeves. Nothing about either looked like a
failure from the outside, which is exactly why they need tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from naviscoord.analysis import MatrixCoverage, analyze, read_declarations
from naviscoord.analysis.noise import NoiseFilter
from naviscoord.analysis.rootcause import RootCauseDetector
from naviscoord.model import Clash, ClashExport, ElementRef, Issue
from naviscoord.narrative import _verdict
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
        sleeve.props["NC:DesignedContactWith"] = pipe.path_id
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
        sleeve.props["NC:DesignedContactWith"] = pipe.path_id
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

        sleeve.props["NC:DesignedContactWith"] = pipe_in_sleeve.path_id
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
        assert cause.evidence["claim_scope"] == "analyzed_inventory"

    def test_finding_no_sleeves_does_not_raise_the_confidence(
        self, profile_with_sleeves: Profile
    ) -> None:
        """Seeing none is weaker evidence than seeing some, not stronger.

        Zero used to escalate to 0.9 and licence "the model has not one pass
        defined". A sleeve only reaches the export by clashing inside one of
        the configured tests, and a matrix of structure-versus-services has
        no set that could ever hold one — so zero is what this export returns
        whether the project models passes perfectly or never models them at
        all. The crossings stay; the certainty about why does not.
        """
        wall = element("0/1", "ARQ", "Walls", at=(0.0, 0.0, 0.0), size=(4.0, 0.2, 3.0))
        pipe = element("1/1", "HID", "Pipes", at=(0.0, 0.0, 1.0))
        export = ClashExport(clashes=[clash("g1", pipe, wall, point=(1.0, 0.0, 1.0))])
        result = analyze(export, profile_with_sleeves)

        causes = [c for c in result.root_causes if c.kind == "missing_penetration"]
        assert causes
        assert causes[0].evidence["sleeves_found_in_model"] == 0
        assert causes[0].confidence == pytest.approx(0.5)
        assert causes[0].confidence < 0.75, (
            "no ver ninguno es menos concluyente que ver algunos, no más"
        )
        assert "ningún conjunto" not in causes[0].detail
        assert "no tiene un solo" not in causes[0].detail

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

        sleeve["props"]["NC:DesignedContactWith"] = pipe_in_sleeve["path_id"]
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
        assert cause.evidence["claim_scope"] == "analyzed_inventory"


def _sleeve_count(causes: list[Any]) -> int:
    for cause in causes:
        if cause.kind == "missing_penetration":
            return int(cause.evidence["sleeves_found_in_model"])
    return -1


# ------------------------------------------- the trade a category cannot tell


def _wall(path_id: str, name: str, at: tuple[float, float, float]) -> ElementRef:
    """A wall. Structural or architectural — Revit files both under `Walls`."""
    return element(path_id, "", category="Walls", name=name, at=at, size=(4.0, 0.2, 3.0))


def _wall_vs_wall_export(test_name: str, **test_fields: Any) -> ClashExport:
    clashes = [
        clash(
            f"g{i}",
            _wall(f"str/{i}", "Muro estructural", at=(float(i), 0.0, 0.0)),
            _wall(f"arq/{i}", "Muro divisorio", at=(float(i), 0.1, 0.0)),
            depth=0.08,
            point=(float(i), 0.0, 0.0),
        )
        for i in range(6)
    ]
    for item in clashes:
        item.test = test_name
        if test_name == "ARQ-MUROS VS ARQ-CIELOS":
            item.a.props["NC:DesignedContactWith"] = item.b.path_id
    entry: dict[str, Any] = {"name": test_name, "status": "Old", "exported": len(clashes)}
    entry.update(test_fields)
    return ClashExport(clashes=clashes, tests=[entry])


class TestStructureVersusArchitectureSurvivesTheNoiseFilter:
    """A whole test was discarded as "architecture overlapping itself".

    Discipline came from the Revit category, and a structural wall and an
    architectural wall are both `Walls`. Both sides landed in ARQ, which is
    precisely the condition `architectural_self_overlap` uses as its safety
    catch, so the rule fired on a structure-versus-architecture test and ate
    every clash in it. On the model this was found on that was 477 clashes —
    a quarter of the run — and the pair still reported clean.

    It stayed invisible because the filter warned on a reason crossing half
    the WHOLE run, and one emptied test out of thirteen never gets there.
    """

    def test_test_name_separates_the_two_walls(self, profile: Profile) -> None:
        result = analyze(_wall_vs_wall_export("STR-WAL VS ARCH-GB-WALL"), profile)

        assert result.filtering.reasons.get("architectural_self_overlap", 0) == 0, (
            "el test declara estructura contra arquitectura: no puede descartarse "
            "como arquitectura consigo misma"
        )
        assert result.filtering.kept_count == 6
        pairs = {c.discipline_pair for c in result.filtering.kept}
        assert pairs == {("ARQ", "EST")}

    def test_selection_sets_outrank_the_test_name(self, profile: Profile) -> None:
        """The add-in ships the sets behind each side; they are the exact signal."""
        export = _wall_vs_wall_export(
            "Prueba 14",  # a name that declares nothing
            selection_a=["STR-WAL"],
            selection_b=["ARCH-GB-WALL"],
        )
        result = analyze(export, profile)

        assert result.filtering.kept_count == 6
        assert {c.discipline_pair for c in result.filtering.kept} == {("ARQ", "EST")}

    def test_architecture_against_itself_is_still_dropped(self, profile: Profile) -> None:
        """The rule must keep working where it was right: no over-correction."""
        result = analyze(_wall_vs_wall_export("ARQ-MUROS VS ARQ-CIELOS"), profile)

        assert result.filtering.kept_count == 0
        assert result.filtering.reasons["architectural_self_overlap"] == 6

    def test_an_unreadable_test_name_falls_back_to_categories(
        self, profile: Profile
    ) -> None:
        """No trade named on either side means no declaration, not a guess."""
        assert read_declarations([{"name": "Prueba 14"}], profile) == {}


class TestAdjacencyIsJudgedOnDepthNotOnCategories:
    """Fixing the tagging removed an accidental suppression; this replaces it.

    A gypsum partition dying against a concrete wall overlaps it by the
    thickness of the board. Nobody acts on that. It used to vanish because
    both walls were mis-tagged as architecture and the self-overlap rule ate
    them — wrong reason, and it took the real defects with it.

    Depth is what separates the two. On the reference model the
    partition-to-wall test ran at a 12.4 mm median and never passed 51 mm,
    while the same partitions against beams reached 247 mm and windows sat
    170 mm inside structural walls. The first is how a wall is drawn; the
    other two are a partition through structure and an opening that needs a
    lintel — the most expensive findings in that model, and all of them were
    being discarded.
    """

    def _pair(self, depth: float, cat_b: str = "Walls") -> ClashExport:
        a = element("str/1", "", category="Walls", name="Muro estructural")
        b = element("arq/1", "", category=cat_b, name="Tabique drywall")
        a.props["NC:DesignedContactWith"] = b.path_id
        item = clash("g1", a, b, depth=depth)
        item.test = "STR-WAL VS ARCH-GB-WALL"
        return ClashExport(
            clashes=[item],
            tests=[{"name": "STR-WAL VS ARCH-GB-WALL", "status": "Old", "exported": 1}],
        )

    def test_a_board_thickness_contact_is_filtered(self, profile: Profile) -> None:
        result = analyze(self._pair(0.0124), profile)
        assert result.filtering.kept_count == 0
        assert result.filtering.reasons["designed_adjacency"] == 1

    def test_a_partition_driven_through_structure_survives(
        self, profile: Profile
    ) -> None:
        result = analyze(self._pair(0.247), profile)
        assert result.filtering.kept_count == 1, (
            "247 mm no es un acabado tocando: el tabique atraviesa la estructura"
        )

    def test_the_threshold_is_the_boundary(self, profile: Profile) -> None:
        assert analyze(self._pair(0.025), profile).filtering.kept_count == 0
        assert analyze(self._pair(0.026), profile).filtering.kept_count == 1

    def test_a_window_deep_inside_a_structural_wall_survives(
        self, profile: Profile
    ) -> None:
        """Its host is the architectural wall, not this one — that needs a lintel."""
        result = analyze(self._pair(0.170, cat_b="Windows"), profile)
        assert result.filtering.kept_count == 1
        assert result.filtering.reasons.get("architectural_hosting", 0) == 0

    def test_hosting_still_applies_within_one_trade(self, profile: Profile) -> None:
        """A window in ITS OWN wall is the opening, at any depth."""
        wall = element("arq/1", "", category="Walls", name="Muro divisorio")
        window = element("arq/2", "", category="Windows", name="V-01")
        window.props["NC:HostPath"] = wall.path_id
        item = clash("g1", wall, window, depth=0.170)
        item.test = "ARQ-MUROS VS ARQ-VENTANAS"
        export = ClashExport(
            clashes=[item],
            tests=[{"name": "ARQ-MUROS VS ARQ-VENTANAS", "status": "Old", "exported": 1}],
        )

        result = analyze(export, profile)

        assert result.filtering.reasons["architectural_hosting"] == 1

    def test_mep_against_a_wall_is_never_adjacency(self, profile: Profile) -> None:
        """The rule needs BOTH sides to be building surfaces."""
        wall = element("str/1", "", category="Walls", name="Muro")
        duct = element("hvac/1", "", category="Ducts", name="Suministro 1")
        item = clash("g1", wall, duct, depth=0.010)
        item.test = "STR-WALL VS HVAC"
        export = ClashExport(
            clashes=[item],
            tests=[{"name": "STR-WALL VS HVAC", "status": "Old", "exported": 1}],
        )

        result = analyze(export, profile)

        assert result.filtering.reasons.get("designed_adjacency", 0) == 0


class TestUnnameableElementsAreDeclared:
    """Knowing the trade is not knowing the object.

    A clash test names WHO owns an element. Every noise rule decides on WHAT
    it is. An element filed under `Generic Models` and declared architecture
    by its test can be neither filtered nor explained — and on the reference
    model 599 of one test's 700 clashes were exactly that. Adding the
    catch-all to the adjacency rule would clear them and would repeat, with
    better manners, the very mistake that made facade panels count as sleeves.
    """

    def test_surviving_catch_all_elements_are_reported(self, profile: Profile) -> None:
        wall = element("str/1", "", category="Walls", name="Muro estructural")
        thing = element("arq/1", "", category="Generic Models", name="Grey RAL 9006")
        item = clash("g1", wall, thing, depth=0.008)
        item.test = "STR-WAL VS ARCH-WINDOR"
        export = ClashExport(
            clashes=[item],
            tests=[{"name": "STR-WAL VS ARCH-WINDOR", "status": "Old", "exported": 1}],
        )

        result = analyze(export, profile)

        assert result.filtering.kept_count == 1, "no se filtra: no se sabe qué es"
        assert any("categoría genérica" in w for w in result.warnings)


class TestDepthAloneCanRaiseAnIssue:
    """A lone deep interference sank to the bottom band.

    Two of the six severity components reward repetition and company, so a
    crossing that happens once has to win on the rest — and it cannot.
    Measured on a real model: twenty-six issues in the lowest band with more
    than 150 mm of penetration into structure, one at 194 mm. Those are the
    same crossings that had to be rescued from the noise filter earlier the
    same day; rescuing them only for the scoring to bury them is no rescue.

    Against something reroutable, depth is a nuisance. Into structure, it is
    a decision that has to be taken before the pour.
    """

    def _lone(self, depth: float) -> Any:
        beam = element("est/1", "", category="Structural Framing", name="Viga",
                       at=(0.0, 0.0, 3.0), size=(6.0, 0.4, 0.6))
        wall = element("arq/1", "", category="Walls", name="Tabique",
                       at=(2.0, 0.0, 3.0), size=(0.15, 4.0, 2.6))
        item = clash("g1", beam, wall, depth=depth, point=(2.0, 0.0, 3.2))
        item.test = "STR-STF VS ARCH-GB-WALL"
        export = ClashExport(
            clashes=[item],
            tests=[{"name": "STR-STF VS ARCH-GB-WALL", "status": "Old", "exported": 1}],
        )
        result = analyze(export, Profile.load())
        return result.issues[0] if result.issues else None

    def test_a_lone_deep_hit_does_not_land_in_the_bottom_band(self) -> None:
        issue = self._lone(0.194)
        assert issue is not None
        assert issue.priority in ("critical", "high", "medium"), issue.priority
        assert issue.priority != "low"
        assert any("no se mueve" in reason for reason in issue.why), issue.why

    def test_a_shallow_lone_hit_is_still_minor(self) -> None:
        """The floor must not promote everything that happens once."""
        issue = self._lone(0.030)
        assert issue is not None
        assert issue.priority == "low", issue.priority


class TestInferredLevelsSaySo:
    """A level map built by guessing must not read like a level map.

    The storey rides in the Navisworks `Layer` property. It was absent from
    the add-in's base property list, so an export taken by any caller that
    did not name it explicitly lost the storey on every element — silently,
    because the level module falls back to matching each crossing to the
    nearest floor by height, and the floor heights it matches against are
    themselves the median height of the crossings that DID carry a storey.

    Measured on a real export: 86% of crossings placed that way, a third of
    them on the wrong floor, under a map that looked entirely plausible. The
    per-level totals, the hotspots and "repeated on N floors" all inherit it.
    """

    def _levelless(self, count: int) -> Any:
        from naviscoord.analysis.levels import normalise_levels

        clashes = []
        for index in range(count):
            height = 3.0 if index % 2 else 6.0
            a = element(f"est/{index}", "EST", category="Floors", at=(0.0, 0.0, height))
            b = element(f"hvac/{index}", "HVAC", category="Ducts", at=(0.0, 0.0, height))
            item = clash(f"g{index}", a, b, point=(0.0, 0.0, height), level="")
            if index < 2:  # a couple that do carry one, to anchor the elevations
                item.level = "PISO 1" if height == 3.0 else "PISO 2"
            clashes.append(item)
        return normalise_levels(clashes)

    def test_mostly_inferred_levels_are_declared_unreliable(self) -> None:
        levels = self._levelless(20)
        assert levels.inferred_share >= 0.5
        assert levels.to_json()["levels_are_inferred"] is True
        note = " ".join(levels.notes())
        assert "es una inferencia, no un dato" in note
        assert "Layer" in note, "y dice dónde mirar para arreglarlo"

    def test_the_property_that_carries_the_storey_is_always_exported(self) -> None:
        """`Layer` has to be a base property, not something a caller must know."""
        source = (
            Path(__file__).resolve().parents[2]
            / "addin" / "NavisCoord.Addin" / "NavisContext.cs"
        )
        base = source.read_text(encoding="utf-8").split("BaseProperties", 1)[1]
        base = base.split("};", 1)[0]
        assert '"Layer"' in base


class TestALabelSpanningTheBuildingIsNotAStorey:
    """A grouping name was merged into a floor because their medians matched.

    `ARC_2ND` carried 133 crossings spread from 4.7 m to 24.6 m — twenty
    metres, six storeys, because it names an architectural grouping and not a
    floor. Summarised by its median it landed at 15.5 m, within centimetres
    of `5 FLOOR`, so the two merged and 133 crossings scattered up the tower
    were all reported as happening on the fifth floor.

    The spread is measured between the tenth and ninetieth percentile — one
    stray crossing 13 m below the rest must not disqualify a real floor — and
    against the building's own floor-to-floor height rather than a constant,
    because a storey legitimately spans one: a crossing on it can be at the
    slab or up against the ceiling. A first attempt with a fixed 3 m limit
    threw away every real floor in the model.
    """

    def _levels(self, extra: dict[str, list[float]] | None = None) -> Any:
        from naviscoord.analysis.levels import normalise_levels

        heights: dict[str, list[float]] = {
            f"PISO {n}": [3.0 * n + offset for offset in (0.0, 1.4, 2.8)] * 8
            for n in range(1, 8)
        }
        heights.update(extra or {})
        clashes = []
        index = 0
        for label, zs in heights.items():
            for z in zs:
                a = element(f"est/{index}", "EST", category="Floors", at=(0.0, 0.0, z))
                b = element(f"hvac/{index}", "HVAC", category="Ducts", at=(0.0, 0.0, z))
                clashes.append(clash(f"g{index}", a, b, point=(0.0, 0.0, z), level=label))
                index += 1
        return normalise_levels(clashes)

    def test_a_cross_cutting_label_is_set_aside(self) -> None:
        levels = self._levels({"ARC_2ND": [4.7 + n * 1.0 for n in range(20)]})

        assert "ARC_2ND" in levels.cross_cutting
        assert all("ARC_2ND" not in s.aliases for s in levels.storeys), (
            "no puede fusionarse dentro de un piso"
        )
        note = " ".join(levels.notes())
        assert "no es un piso" in note and "ARC_2ND" in note

    def test_real_floors_survive_the_rule(self) -> None:
        """The failure mode of the fix is throwing away every storey."""
        levels = self._levels()
        assert levels.cross_cutting == {}
        assert len(levels.storeys) == 7

    def test_one_stray_crossing_does_not_disqualify_a_floor(self) -> None:
        levels = self._levels({"PISO 8": [24.5] * 30 + [10.9]})
        assert "PISO 8" not in levels.cross_cutting

    def test_two_names_for_one_floor_still_merge(self) -> None:
        """The rule must not cost the merge it was never about."""
        levels = self._levels({"AZOTEA": [21.0, 21.1, 21.2] * 6})
        merged = {s.canonical: s.aliases for s in levels.storeys if s.aliases}
        assert any("AZOTEA" in aliases for aliases in merged.values()), merged


class TestElevationCauseNeedsAnElevationError:
    """The most confident finding in the report could not have been acted on.

    `systemic_elevation` measures how much the two boxes overlap on Z and
    reads a tight spread as proof that a run sits at the wrong height. The
    same arithmetic produces a tight spread for two geometries that are not
    elevation errors at all, and on the reference model those were ALL of
    them — eleven causes, none real:

    * a horizontal branch crossing a wall reports its own diameter, the same
      at every crossing because it is the same pipe. Two sprinkler branches
      matched their own bounding height in 67 of 67 crossings, published at
      0.88 and 0.95 confidence, with a suggested fix — lower the run 50 mm —
      that could not work, because 40 of the 54 things it crossed were
      vertical walls where moving down only moves the hole.
    * a vertical riser through a slab reports the slab's thickness: 177.8 mm,
      seven inches, standard deviation exactly zero. A riser crosses the slab
      at every height, so no vertical move separates them.

    A real elevation error leaves the run PARTLY out of what it crosses, on
    both counts, and that is what is asserted here.
    """

    def _run(self, soft_height: float, hard_height: float, overlap: float) -> Any:
        """Ten crossings of one system, with the geometry under test."""
        clashes = []
        for index in range(10):
            slab_bottom = 3.0
            slab = element(
                f"est/{index}", "EST", category="Floors", name="Losa",
                at=(float(index) * 6.0, 0.0, slab_bottom),
                size=(5.0, 5.0, hard_height),
            )
            # The run's top sits `overlap` above the host's underside.
            duct = element(
                f"hvac/{index}", "HVAC", category="Ducts", name="Extracción 4",
                at=(float(index) * 6.0, 1.0, slab_bottom + overlap - soft_height),
                size=(4.0, 0.4, soft_height),
            )
            duct.props["System Name"] = "Mechanical Exhaust Air 9"
            item = clash(f"g{index}", slab, duct, depth=0.05,
                         point=(float(index) * 6.0, 1.0, slab_bottom))
            item.test = "STR-FLR VS HVAC"
            clashes.append(item)
        export = ClashExport(
            clashes=clashes,
            tests=[{"name": "STR-FLR VS HVAC", "status": "Old", "exported": len(clashes)}],
        )
        return analyze(export, Profile.load())

    def _elevation_causes(self, result: Any) -> list[Any]:
        return [c for c in result.root_causes if c.kind == "systemic_elevation"]

    def test_a_run_clipping_a_slab_is_still_detected(self) -> None:
        """The detector must survive the fix, not be disabled by it."""
        result = self._run(soft_height=0.40, hard_height=0.20, overlap=0.044)
        causes = self._elevation_causes(result)
        assert causes, "un conducto que roza la losa 44 mm SÍ es una cota mal puesta"
        assert causes[0].evidence["mean_overlap_mm"] == pytest.approx(44.0, abs=1.0)

    def test_a_branch_passing_through_reports_its_own_diameter(self) -> None:
        """Overlap equals the run's full height: it is inside, not hanging low."""
        result = self._run(soft_height=0.038, hard_height=0.60, overlap=0.038)
        assert self._elevation_causes(result) == []

    def test_a_riser_through_a_slab_reports_the_slab(self) -> None:
        """Overlap equals the host's full thickness: no vertical move separates them."""
        result = self._run(soft_height=2.40, hard_height=0.1778, overlap=0.1778)
        assert self._elevation_causes(result) == []


class TestContradictoryDeclarationsAreReported:
    """Letting the matrix outrank the category means trusting the matrix.

    Two tests can put the same element on opposite sides of the trade line.
    Whichever is read last would win silently, and the resulting split of
    work by responsible trade would be wrong with no sign of it. The
    disagreement is reported instead — it is a real fault in the search sets.
    """

    def test_an_element_declared_twice_over_is_flagged(self, profile: Profile) -> None:
        shared = _wall("muro/1", "Muro compartido", at=(0.0, 0.0, 0.0))
        other = _wall("muro/2", "Otro muro", at=(0.0, 0.2, 0.0))
        first = clash("g1", shared, other, depth=0.08)
        first.test = "STR-WAL VS ARCH-GB-WALL"          # shared is EST here
        second = clash("g2", other, shared, depth=0.08)
        second.test = "STR-STF VS ARCH-GB-WALL"          # and ARQ here
        export = ClashExport(
            clashes=[first, second],
            tests=[
                {"name": "STR-WAL VS ARCH-GB-WALL", "status": "Old", "exported": 1},
                {"name": "STR-STF VS ARCH-GB-WALL", "status": "Old", "exported": 1},
            ],
        )

        result = analyze(export, profile)

        assert result.tagging.declaration_conflicts.get("muro/1") == {"ARQ", "EST"}
        assert any("dos especialidades distintas" in w for w in result.warnings)

    def test_a_consistent_matrix_reports_nothing(self, profile: Profile) -> None:
        result = analyze(_wall_vs_wall_export("STR-WAL VS ARCH-GB-WALL"), profile)
        assert result.tagging.declaration_conflicts == {}


class TestAnEmptiedTestIsReportedByName:
    """A rule that eats one whole test is diluted into silence by the others.

    The filter warned when a single reason crossed 50% of the entire run.
    One test of thirteen emptied completely is ~8% of the run, so the loudest
    possible failure — a pair of trades now reporting clean on zero surviving
    evidence — produced no warning at all.
    """

    def test_emptied_test_is_named_in_the_warnings(self, profile: Profile) -> None:
        result = analyze(_wall_vs_wall_export("ARQ-MUROS VS ARQ-CIELOS"), profile)
        warnings = " ".join(result.warnings)

        assert "ARQ-MUROS VS ARQ-CIELOS" in warnings
        assert "descartó" in warnings

    def test_emptied_tests_are_listed_in_the_payload(self, profile: Profile) -> None:
        result = analyze(_wall_vs_wall_export("ARQ-MUROS VS ARQ-CIELOS"), profile)
        emptied = result.filtering.to_json()["emptied_tests"]

        assert [e["test"] for e in emptied] == ["ARQ-MUROS VS ARQ-CIELOS"]
        assert emptied[0]["input"] == 6
        assert emptied[0]["dropped_by_reason"] == {"architectural_self_overlap": 6}


class TestGenericModelsDoNotProveASleeve:
    """`Generic Models` was read as proof of a modelled penetration.

    It is Revit's catch-all: a sleeve fits there, and so does a facade panel.
    Counted as sleeves, 255 cladding elements — 246 of them named after a
    paint colour, `Grey RAL 9006` — did two kinds of damage at once. They
    emptied a 700-clash test as `designed_pass_through`, and they supplied
    the largest root cause its evidence that the project models penetrations
    correctly elsewhere, which inverted what that finding told the reader.

    A purpose-built category still stands alone; a catch-all must be
    corroborated by the name.
    """

    def test_a_facade_panel_is_not_a_pass_through(self, profile: Profile) -> None:
        panel = element(
            "fachada/1", "ARQ", category="Generic Models", name="Grey RAL 9006"
        )
        pipe = element("hid/1", "HID", category="Pipes", name="Refrigerating 120")

        assert NoiseFilter(profile).is_penetration_element(panel) is False
        result = NoiseFilter(profile).run([clash("g1", panel, pipe, depth=0.08)])
        assert result.kept_count == 1, "un panel de fachada no justifica descartar el cruce"

    def test_a_named_sleeve_in_the_catch_all_category_still_counts(
        self, profile: Profile
    ) -> None:
        """The category is ambiguous, not disqualifying."""
        sleeve = element(
            "paso/1", "EST", category="Generic Models", name="Sleeve 200mm"
        )
        assert NoiseFilter(profile).is_penetration_element(sleeve) is True

    def test_a_purpose_built_category_needs_no_name(self, profile: Profile) -> None:
        shaft = element("shaft/1", "EST", category="Shafts", name="SH-04")
        assert NoiseFilter(profile).is_penetration_element(shaft) is True


class TestVerdictKnowsWhetherItLooked:
    """"No critical interferences" was reported as a clean bill of health.

    Nothing asked how much of the matrix had run. A model whose tests had
    never been executed produced zero criticals for the same reason a clean
    model does, and the narrative called both "apto: no hay interferencias
    que frenen obra". Absence of evidence, reported as evidence of absence.
    """

    def test_never_run_tests_are_counted_as_uncovered(self) -> None:
        coverage = MatrixCoverage.from_tests(
            [
                {"name": "EST vs HVAC", "status": "Old", "exported": 12},
                {"name": "EST vs HID", "status": "New", "exported": 0},
                {"name": "EST vs ELE", "status": "New", "exported": 0},
            ]
        )
        assert coverage.total == 3
        assert coverage.ran == 1
        assert coverage.never_run == ["EST vs HID", "EST vs ELE"]
        assert coverage.complete is False

    def test_a_stale_run_still_counts_as_evidence(self) -> None:
        """`New` plus results means the rules were edited, not that nobody ran it."""
        coverage = MatrixCoverage.from_tests(
            [{"name": "EST vs HVAC", "status": "New", "exported": 40}]
        )
        assert coverage.never_run == []
        assert coverage.complete is True

    def test_uncovered_matrix_is_flagged_in_the_analysis(self, profile: Profile) -> None:
        export = _wall_vs_wall_export("STR-WAL VS ARCH-GB-WALL")
        export.tests.append({"name": "EST VS HID", "status": "New", "exported": 0})

        result = analyze(export, profile)

        assert result.coverage.never_run == ["EST VS HID"]
        assert any("nunca" in w and "EST VS HID" in w for w in result.warnings)

    def test_zero_criticals_without_full_coverage_is_not_apto(
        self, profile: Profile
    ) -> None:
        export = _wall_vs_wall_export("STR-WAL VS ARCH-GB-WALL")
        export.tests.append({"name": "EST VS HID", "status": "New", "exported": 0})
        result = analyze(export, profile)
        assert result.by_priority()["critical"] == 0

        readiness, verdict = _verdict(result, [])

        assert readiness == "sin evidencia suficiente"
        assert "nunca se corrieron" in verdict

    def test_full_coverage_and_no_criticals_is_still_apto(
        self, profile: Profile
    ) -> None:
        """The guard must not swallow a genuinely clean result."""
        export = _wall_vs_wall_export("STR-WAL VS ARCH-GB-WALL")
        result = analyze(export, profile)
        # Every pair the profile asks for has a test behind it. Asserted by
        # construction rather than by building sixteen fixtures: the point
        # here is the verdict, not the coverage arithmetic.
        result.coverage = MatrixCoverage(
            total=1, ran=1,
            required_pairs=[("ARQ", "EST")], covered_pairs=[("ARQ", "EST")],
        )

        readiness, _ = _verdict(result, [])

        assert readiness == "apto"

    def test_running_every_test_is_not_the_same_as_covering_every_pair(
        self, profile: Profile
    ) -> None:
        """All thirteen tests ran, and eleven of thirteen pairs went unlooked at.

        This is the shape the bug took on the reference model: nothing had
        failed to run, so coverage reported complete — while every single
        test had structure on side A. No architecture against services, no
        service against service, no electrical anywhere.
        """
        # Three trades in the federation, so the profile's EST×HVAC and
        # ARQ×HVAC both become answerable. Only one of them has a test.
        export = _wall_vs_wall_export("STR-WAL VS ARCH-GB-WALL")
        duct = element("hvac/1", "", category="Ducts", name="Suministro 1")
        beam = element("est/9", "", category="Structural Framing", name="Viga")
        extra = clash("gx", beam, duct, depth=0.09)
        extra.test = "STR-STF VS HVAC"
        export.clashes.append(extra)
        export.tests.append(
            {"name": "STR-STF VS HVAC", "status": "Old", "exported": 1}
        )
        result = analyze(export, profile)

        assert result.coverage.never_run == [], "los tests que existen sí corrieron"
        assert ("EST", "HVAC") in result.coverage.covered_pairs
        assert ("ARQ", "HVAC") in result.coverage.required_pairs, (
            "arquitectura y ventilación están las dos en el modelo: son comparables"
        )
        assert ("ARQ", "HVAC") in result.coverage.missing_pairs
        assert result.coverage.complete is False
        assert all(
            "HID" not in pair and "ELE" not in pair
            for pair in result.coverage.required_pairs
        ), "no se exige comparar contra especialidades que no están modeladas"

        readiness, verdict = _verdict(result, [])
        assert readiness == "sin evidencia suficiente"
        assert "parejas que exige el perfil" in verdict
