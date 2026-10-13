"""Renaming Clash Detective by priority: «Conflicto1» → «001 · CRÍTICO · …».

Pablo's request of 2026-10-13, from a Clash Detective full of «Conflicto1,
Conflicto2…»: rename them with a running number and something informative,
ordered by priority. Two things already wrote names and neither answered it:
`navis_apply_groups` wrote `[CRITICAL] ISS-db75bd49ae215134 EST×MEP → MEP`
(English priority, a hash for an id, no number) and the rules workflow wrote
`NNN · a vs b` in whatever order Navisworks listed the results.

These tests pin the server half: the order, the label and the payload the
add-in receives. Which names the add-in may overwrite is decided there and
tested in NavisCoord.Tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from naviscoord import mcp_server
from naviscoord import naming
from naviscoord.model import Clash, ClashExport, ElementRef, Issue


def _ref(path: str, category: str, discipline: str, name: str = "") -> ElementRef:
    return ElementRef(path_id=path, category=category, display_name=name, discipline=discipline)


def _clash(guid: str, a: ElementRef, b: ElementRef, level: str = "03 - Floor") -> Clash:
    return Clash(guid=guid, test="NC EST vs MEP", name="Conflicto1", a=a, b=b, level=level)


def _issue(
    issue_id: str,
    priority: str,
    severity: float,
    clash_ids: list[str],
    *,
    pair: tuple[str, str] = ("EST", "MEP"),
    responsible: str = "MEP",
    immovable: str = "EST",
    level: str = "03 - Floor",
    representative: str = "",
) -> Issue:
    return Issue(
        issue_id=issue_id,
        kind="pair",
        discipline_pair=pair,
        clash_ids=list(clash_ids),
        centroid=(0.0, 0.0, 0.0),
        bbox_min=(0.0, 0.0, 0.0),
        bbox_max=(0.0, 0.0, 0.0),
        severity=severity,
        priority=priority,
        level=level,
        responsible=responsible,
        immovable_side=immovable,
        discipline_a=pair[0],
        discipline_b=pair[1],
        representative_clash_id=representative or (clash_ids[0] if clash_ids else ""),
    )


SLAB = _ref("1/1", "Floors", "EST", "Concrete Deck")
DUCT = _ref("2/1", "Ducts", "MEP", "Rectangular Duct")
BEAM = _ref("1/2", "Structural Framing", "EST", "W310")
PIPE = _ref("2/2", "Pipes", "MEP", "Copper")
WALL = _ref("3/1", "Walls", "ARQ", "Plasterboard")


class TestOrder:
    def test_priority_first_then_score(self) -> None:
        issues = [
            _issue("ISS-low", "low", 90, ["g1"]),
            _issue("ISS-crit-b", "critical", 70, ["g2"]),
            _issue("ISS-high", "high", 99, ["g3"]),
            _issue("ISS-crit-a", "critical", 80, ["g4"]),
            _issue("ISS-med", "medium", 50, ["g5"]),
        ]
        ordered = [i.issue_id for i in naming.order_by_priority(issues)]
        # A low with a high score does not jump a critical: priority is the
        # band the team triages by, the score only orders inside it.
        assert ordered == ["ISS-crit-a", "ISS-crit-b", "ISS-high", "ISS-med", "ISS-low"]

    def test_ties_are_stable_by_id(self) -> None:
        issues = [_issue("ISS-b", "high", 60, ["g1"]), _issue("ISS-a", "high", 60, ["g2"])]
        assert [i.issue_id for i in naming.order_by_priority(issues)] == ["ISS-a", "ISS-b"]


class TestLabel:
    def test_the_label_pablo_asked_for(self) -> None:
        issue = _issue("ISS-1", "critical", 80, ["g1", "g2"])
        clashes = {"g1": _clash("g1", SLAB, DUCT)}
        label = naming.issue_label(1, issue, clashes)
        assert label == "001 · CRÍTICO · EST × MEP · Losa vs Ducto · N03 · mueve MEP"

    @pytest.mark.parametrize(
        ("priority", "spanish"),
        [("critical", "CRÍTICO"), ("high", "ALTO"), ("medium", "MEDIO"), ("low", "BAJO")],
    )
    def test_priority_is_in_spanish(self, priority: str, spanish: str) -> None:
        issue = _issue("ISS-1", priority, 50, ["g1"])
        label = naming.issue_label(7, issue, {"g1": _clash("g1", SLAB, DUCT)})
        assert label.startswith(f"007 · {spanish} · ")

    def test_immovable_side_comes_first_whatever_navisworks_called_a(self) -> None:
        # Navisworks put the duct on side A this time. The label still reads
        # «structure × what moves», so a list of them reads the same way.
        issue = _issue("ISS-1", "high", 50, ["g1"])
        label = naming.issue_label(2, issue, {"g1": _clash("g1", DUCT, BEAM)})
        assert "EST × MEP · Viga vs Ducto" in label
        assert label.endswith("mueve MEP")

    def test_floor_is_a_slab_in_structure_and_a_floor_in_architecture(self) -> None:
        arq_floor = _ref("3/2", "Floors", "ARQ")
        issue = _issue("ISS-1", "medium", 40, ["g1"], pair=("ARQ", "MEP"), immovable="ARQ")
        label = naming.issue_label(3, issue, {"g1": _clash("g1", arq_floor, PIPE)})
        assert "Piso vs Tubería" in label

    def test_spanish_revit_categories_are_understood(self) -> None:
        losa = _ref("1/9", "Suelos", "EST")
        ducto = _ref("2/9", "Conductos", "MEP")
        issue = _issue("ISS-1", "high", 50, ["g1"])
        assert "Losa vs Ducto" in naming.issue_label(1, issue, {"g1": _clash("g1", losa, ducto)})

    def test_unknown_category_is_kept_not_invented(self) -> None:
        odd = _ref("2/7", "Specialty Equipment", "MEP")
        issue = _issue("ISS-1", "low", 10, ["g1"])
        assert "Losa vs Specialty Equipment" in naming.issue_label(
            1, issue, {"g1": _clash("g1", SLAB, odd)}
        )

    @pytest.mark.parametrize(
        ("level", "short"),
        [
            ("03 - Floor", "N03"),
            ("01 - Entry Level", "N01"),
            ("Nivel 5", "N05"),
            ("Level 12", "N12"),
            ("PISO 2", "N02"),
            ("Cubierta", "Cubierta"),
            ("Sótano 1", "Sótano 1"),
        ],
    )
    def test_level_is_shortened_only_when_it_is_a_number(self, level: str, short: str) -> None:
        assert naming.level_short(level) == short

    def test_no_level_and_no_responsible_leave_their_segments_out(self) -> None:
        issue = _issue("ISS-1", "low", 10, ["g1"], level="SIN NIVEL", responsible="", immovable="")
        label = naming.issue_label(4, issue, {"g1": _clash("g1", SLAB, DUCT, level="")})
        assert label == "004 · BAJO · EST × MEP · Losa vs Ducto"

    def test_a_missing_representative_clash_drops_only_the_types(self) -> None:
        issue = _issue("ISS-1", "high", 50, ["gone"])
        assert naming.issue_label(1, issue, {}) == "001 · ALTO · EST × MEP · N03 · mueve MEP"

    def test_long_names_are_truncated(self) -> None:
        long_a = _ref("1/3", "Structural Framing" * 1, "EST")
        long_b = _ref("2/3", "Some Very Long Custom Category Name From A Plugin " * 3, "MEP")
        issue = _issue("ISS-1", "high", 50, ["g1"], level="A level with a very long name " * 3)
        label = naming.issue_label(1, issue, {"g1": _clash("g1", long_a, long_b)})
        assert len(label) <= naming.MAX_NAME
        assert label.startswith("001 · ALTO · EST × MEP · Viga vs ")

    def test_numbers_past_999_keep_growing(self) -> None:
        issue = _issue("ISS-1", "low", 1, ["g1"])
        assert naming.issue_label(1335, issue, {}).startswith("1335 · BAJO")


class TestPlan:
    def _export(self, clashes: list[Clash]) -> ClashExport:
        return ClashExport(clashes=clashes)

    def test_numbers_follow_priority_and_children_get_dotted_numbers(self) -> None:
        issues = [
            _issue("ISS-high", "high", 60, ["h1"]),
            _issue("ISS-crit", "critical", 80, ["c1", "c2", "c3"], representative="c2"),
        ]
        export = self._export([
            _clash("h1", BEAM, PIPE),
            _clash("c1", SLAB, DUCT),
            _clash("c2", SLAB, DUCT),
            _clash("c3", SLAB, DUCT),
        ])
        plan = naming.build_plan(issues, export)

        assert [p["issue_id"] for p in plan] == ["ISS-crit", "ISS-high"]
        crit, high = plan
        assert crit["name"].startswith("001 · CRÍTICO")
        # The representative — the clash the analysis photographs and
        # describes — is .1, so the first child matches the group's label.
        assert crit["clash_guids"] == ["c2", "c1", "c3"]
        assert crit["clash_names"] == ["001.1", "001.2", "001.3"]
        # One clash is not a group: the clash itself carries the full label.
        assert high["clash_guids"] == ["h1"]
        assert high["clash_names"] == [high["name"]]
        assert high["name"].startswith("002 · ALTO")

    def test_limit_keeps_the_most_urgent(self) -> None:
        issues = [_issue(f"ISS-{n}", "low", n, [f"g{n}"]) for n in range(5)]
        issues.append(_issue("ISS-c", "critical", 1, ["gc"]))
        plan = naming.build_plan(issues, self._export([]), limit=2)
        assert [p["issue_id"] for p in plan] == ["ISS-c", "ISS-4"]

    def test_issues_without_clashes_take_no_number(self) -> None:
        issues = [_issue("ISS-empty", "critical", 99, []), _issue("ISS-1", "high", 50, ["g1"])]
        plan = naming.build_plan(issues, self._export([]))
        assert [p["issue_id"] for p in plan] == ["ISS-1"]
        assert plan[0]["name"].startswith("001 · ")


class FakeBridge:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def rename_by_priority(self, issues, **kwargs) -> dict[str, Any]:
        self.sent.append(("clash/rename", {"issues": issues, **kwargs}))
        return {"status": "dry_run", "dry_run": kwargs.get("dry_run"), "requested": 3}


class FakeResult:
    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        self.profile_name = "test"

    @property
    def decisions(self) -> list[Issue]:
        return [i for i in self.issues if not i.folded_into]


class FakeState:
    def __init__(self, issues: list[Issue], export: ClashExport) -> None:
        self.bridge = FakeBridge()
        self.result = FakeResult(issues)
        self.export = export
        self.mutable_calls: list[str] = []

    def require_mutable(self, expected: str = "") -> str:
        self.mutable_calls.append(expected)
        return expected or "fp-live"

    def require_fresh_result(self):
        return self.result


class TestTool:
    @pytest.fixture
    def state(self, monkeypatch: pytest.MonkeyPatch) -> FakeState:
        issues = [
            _issue("ISS-2", "high", 60, ["h1"]),
            _issue("ISS-1", "critical", 80, ["c1", "c2"]),
        ]
        export = ClashExport(clashes=[_clash("h1", BEAM, PIPE), _clash("c1", SLAB, DUCT)])
        fake = FakeState(issues, export)
        monkeypatch.setattr(mcp_server, "STATE", fake)
        return fake

    def test_is_a_dry_run_by_default(self, state: FakeState) -> None:
        result = mcp_server.navis_rename_by_priority()
        route, payload = state.bridge.sent[0]
        assert payload["dry_run"] is True
        assert payload["overwrite_naviscoord_names"] is False
        assert payload["sort_by_name"] is True
        assert result["issues_named"] == 2
        assert result["preview"][0]["name"].startswith("001 · CRÍTICO")
        assert result["preview"][0]["clash_names"] == ["001.1", "001.2"]

    def test_carries_the_guards(self, state: FakeState) -> None:
        mcp_server.navis_rename_by_priority(
            dry_run=False,
            expected_document_fingerprint="fp-1",
            idempotency_key="k-1",
            overwrite_naviscoord_names=True,
        )
        assert state.mutable_calls == ["fp-1"]
        _, payload = state.bridge.sent[0]
        assert payload["fingerprint"] == "fp-1"
        assert payload["idempotency_key"] == "k-1"
        assert payload["dry_run"] is False
        assert payload["overwrite_naviscoord_names"] is True

    def test_is_registered_as_destructive_and_takes_the_fingerprint(self) -> None:
        import asyncio

        tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
        tool = tools["navis_rename_by_priority"]
        assert tool.annotations.destructiveHint is True
        schema = getattr(tool, "input_schema", None) or tool.inputSchema
        props = schema["properties"]
        assert props["dry_run"]["default"] is True
        assert props["overwrite_naviscoord_names"]["default"] is False
        assert "expected_document_fingerprint" in props
        assert "idempotency_key" in props


class TestBridge:
    def test_rename_route_is_guarded_like_the_other_derived_writes(self) -> None:
        from naviscoord.bridge import Bridge

        sent: list[tuple[str, dict[str, Any]]] = []

        class Recording(Bridge):
            def require(self, route: str, *, since: str = "0.1.2") -> None:
                sent.append(("require", {"route": route}))

            def _transport(self, request):  # noqa: ANN001
                import json

                sent.append((request.full_url.rsplit("/", 2)[-2] + "/" + request.full_url.rsplit("/", 1)[-1],
                             json.loads(request.data)))
                return {"ok": True}

            def snapshot(self):  # noqa: ANN201
                class S:
                    base_url = "http://127.0.0.1:1"
                    token = "t"

                return S()

        bridge = Recording()
        bridge.analysis_revision = "rev-1"
        bridge.rename_by_priority(
            [{"name": "001", "clash_guids": ["g"], "clash_names": ["001"]}],
            dry_run=True,
            overwrite_naviscoord_names=False,
            fingerprint="fp",
            idempotency_key="",
            run_async=False,
        )
        assert sent[0] == ("require", {"route": "clash/rename"})
        route, payload = sent[1]
        assert route == "clash/rename"
        # Derived from the analysis: refused by the add-in if the clash
        # results moved since, instead of renaming against a stale ranking.
        assert payload["expected_analysis_revision"] == "rev-1"
        assert payload["expected_document_fingerprint"] == "fp"
        assert payload["overwrite_naviscoord_names"] is False
