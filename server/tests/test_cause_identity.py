"""A root cause has to keep pointing at the issues it explains.

Causes are detected against clusters, and a cluster's identity was its
position in a list. Issues are then ranked by severity, which reorders them,
and the ids the outside world uses are handed out by that ranking. Between
those two steps the meaning of "7" changed from "what cluster 7 became" to
"the eighth worst problem", and nothing said so — the plan read cluster
positions out of the ranked list and built a package about free heights out
of a congested zone's issues.

Two things follow, and both are tested here rather than asserted in a
comment. Identity travels on the issue (`cluster_id`), so no two orderings
have to agree for the association to hold. And the plan only reads: it does
not get to rewrite an issue's root cause so that its own packages look tidy.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from naviscoord import samples
from naviscoord.analysis import analyze
from naviscoord.analysis.pipeline import _resolve_cause_identities
from naviscoord.analysis.rootcause import RootCause
from naviscoord.model import ClashExport, Issue
from naviscoord.plan import build_plan
from naviscoord.profile import Profile


def _issue(cluster_id: int, severity: float, marker: str) -> Issue:
    return Issue(
        issue_id="",
        kind="pair",
        discipline_pair=("EST", "HID"),
        clash_ids=[marker],
        centroid=(0.0, 0.0, 0.0),
        bbox_min=(0.0, 0.0, 0.0),
        bbox_max=(1.0, 1.0, 1.0),
        cluster_id=cluster_id,
        severity=severity,
        responsible="HID",
        immovable_side="EST",
        elements_a=["a"],
        elements_b=["b"],
        discipline_a="EST",
        discipline_b="HID",
    )


def _rank(issues: list[Issue]) -> list[Issue]:
    """Assign ids by severity, as `analyze` does. Returns the ranked order."""
    order = sorted(range(len(issues)), key=lambda i: -issues[i].severity)
    for rank, original in enumerate(order, start=1):
        issues[original].issue_id = f"ISS-{rank:04d}"
    return [issues[i] for i in order]


def _cause(name: str, clusters: list[int], kind: str = "systemic_elevation") -> RootCause:
    return RootCause(
        kind=kind,
        title=f"causa {name}",
        detail="",
        confidence=0.9,
        affected_clusters=list(clusters),
        clash_count=len(clusters) * 3,
        cause_id=name,
    )


class TestIdentityTravelsOnTheIssue:
    def test_membership_holds_when_the_order_reverses(self) -> None:
        issues = [_issue(0, 10.0, "c0"), _issue(1, 90.0, "c1"), _issue(2, 50.0, "c2")]
        _rank(issues)
        assert [i.issue_id for i in issues] == ["ISS-0003", "ISS-0001", "ISS-0002"], (
            "el orden tiene que invertirse o el test no prueba nada"
        )

        cause = _cause("RC-A", [0])
        _resolve_cause_identities(issues, [cause])

        assert cause.affected_issue_ids == ["ISS-0003"]
        resolved = next(i for i in issues if i.issue_id == "ISS-0003")
        assert resolved.clash_ids == ["c0"]

    def test_an_extra_sort_before_resolving_changes_nothing(self) -> None:
        """The brittle-window objection, answered by construction.

        Identity is a field, not a position, so the list can be shuffled any
        number of times before the causes are resolved and the association
        survives. Previously this call had to sit in the one gap between
        building the issues and sorting them, and nothing enforced that.
        """
        issues = [_issue(i, sev, f"c{i}") for i, sev in enumerate([10, 90, 50, 70])]
        _rank(issues)

        cause_a = _cause("RC-A", [0, 2])
        shuffled = sorted(issues, key=lambda i: i.clash_ids[0], reverse=True)
        reshuffled = sorted(shuffled, key=lambda i: i.issue_id)
        _resolve_cause_identities(reshuffled, [cause_a])
        after_shuffles = list(cause_a.affected_issue_ids)

        cause_b = _cause("RC-A", [0, 2])
        _resolve_cause_identities(issues, [cause_b])

        assert after_shuffles == cause_b.affected_issue_ids
        by_id = {i.issue_id: i for i in issues}
        assert {by_id[x].clash_ids[0] for x in after_shuffles} == {"c0", "c2"}

    def test_interleaved_causes_do_not_bleed(self) -> None:
        issues = [_issue(i, sev, f"c{i}") for i, sev in enumerate([50, 10, 60, 20, 55, 15])]
        _rank(issues)

        even = _cause("RC-PAR", [0, 2, 4])
        odd = _cause("RC-IMPAR", [1, 3, 5])
        _resolve_cause_identities(issues, [even, odd])

        by_id = {i.issue_id: i for i in issues}
        assert {by_id[x].clash_ids[0] for x in even.affected_issue_ids} == {"c0", "c2", "c4"}
        assert {by_id[x].clash_ids[0] for x in odd.affected_issue_ids} == {"c1", "c3", "c5"}
        assert not set(even.affected_issue_ids) & set(odd.affected_issue_ids)

    def test_a_cluster_with_no_issue_is_declared_not_guessed(self) -> None:
        issues = [_issue(0, 10.0, "c0"), _issue(1, 90.0, "c1")]
        _rank(issues)

        cause = _cause("RC-FANTASMA", [0, 7, -1])
        _resolve_cause_identities(issues, [cause])

        assert cause.affected_issue_ids == ["ISS-0002"]
        assert cause.unresolved_clusters == [7, -1]
        assert cause.affected_count == 1
        assert "ISS-0001" not in cause.affected_issue_ids


@pytest.fixture(scope="module")
def analysed() -> Any:
    profile = Profile.load()
    return analyze(ClashExport.from_json(samples.full_project_case()), profile), profile


def _fingerprint(result: Any) -> Any:
    """Everything build_plan could plausibly damage, in comparable form."""
    return {
        "issues": [
            (i.issue_id, i.cluster_id, i.severity, i.priority,
             tuple(sorted((i.root_cause or {}).items())), i.folded_into, i.folds)
            for i in result.issues
        ],
        "causes": [
            (c.cause_id, c.kind, c.confidence, tuple(c.affected_clusters),
             tuple(c.affected_issue_ids), tuple(c.merged_from))
            for c in result.root_causes
        ],
    }


class TestThePlanOnlyReads:
    def test_build_plan_does_not_mutate_the_analysis(self, analysed) -> None:
        """The plan is a consumer. It does not get to edit its input.

        The first attempt at this fix had `build_plan` re-stamp each issue's
        `root_cause` so that a package's members would all name the package's
        cause. That makes the assertion pass by rewriting the finding, which
        is the opposite of fixing it.
        """
        result, profile = analysed
        working = copy.deepcopy(result)
        before = _fingerprint(working)

        build_plan(working, profile)

        assert _fingerprint(working) == before, "build_plan modificó el análisis"

    def test_running_it_twice_gives_the_same_plan(self, analysed) -> None:
        result, profile = analysed
        working = copy.deepcopy(result)
        first = [p.to_json() for p in build_plan(working, profile).packages]
        second = [p.to_json() for p in build_plan(working, profile).packages]
        assert first == second

    def test_reordering_the_issues_does_not_change_membership(self, analysed) -> None:
        """Membership is by id, so the list order is nobody's business."""
        result, profile = analysed
        straight = copy.deepcopy(result)
        shuffled = copy.deepcopy(result)
        shuffled.issues = list(reversed(shuffled.issues))

        def membership(plan: Any) -> Any:
            return {
                p.primary_cause_id: (
                    frozenset(p.decision_issue_ids), frozenset(p.affected_issue_ids)
                )
                for p in plan.packages
                if p.kind == "systemic"
            }

        assert membership(build_plan(straight, profile)) == membership(
            build_plan(shuffled, profile)
        )


class TestFoldingDoesNotErasePackages:
    def test_a_cause_folded_to_one_decision_still_gets_a_package(self, analysed) -> None:
        """Ten problems closed by one decision is leverage, not smallness."""
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)

        elevation = [c for c in result.root_causes if c.kind == "systemic_elevation"]
        assert elevation, "el sample trae una causa de cota"
        target = elevation[0]

        package = next(
            (p for p in plan.packages
             if p.kind == "systemic" and target.cause_id in p.cause_ids),
            None,
        )
        assert package is not None, (
            f"{target.cause_id} afecta {len(target.affected_issue_ids)} incidencias "
            "y tiene que producir paquete aunque plieguen en una sola decisión"
        )
        assert len(package.decision_issue_ids) < len(package.affected_issue_ids)

    def test_the_two_id_lists_mean_different_things(self, analysed) -> None:
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)
        systemic = [p for p in plan.packages if p.kind == "systemic"]
        assert systemic

        for package in systemic:
            assert set(package.decision_issue_ids) <= set(package.affected_issue_ids), (
                "cada representante está entre lo que cierra"
            )
            assert package.affected_issue_count == len(package.affected_issue_ids)
            assert package.decision_issue_ids, "algo hay que decidir"

    def test_the_package_carries_exactly_its_causes_issues(self, analysed) -> None:
        """No issue from another cause may appear, whatever the ranking did."""
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)
        by_cause = {c.cause_id: set(c.affected_issue_ids) for c in result.root_causes}

        for package in plan.packages:
            if package.kind != "systemic":
                continue
            allowed: set[str] = set()
            for cause_id in package.cause_ids:
                allowed |= by_cause.get(cause_id, set())
            assert set(package.affected_issue_ids) <= allowed, (
                f"{package.package_id} incluye incidencias de otra causa: "
                f"{sorted(set(package.affected_issue_ids) - allowed)}"
            )

    def test_an_issue_naming_another_cause_is_still_genuinely_claimed(
        self, analysed
    ) -> None:
        """Causes overlap, and membership is not the issue's headline cause.

        A crossing can be both a missing sleeve and one floor of a repeated
        detail; `_attach_root_causes` records only the strongest claim. On a
        real export 51 of 319 issues are claimed by two causes, so requiring
        every member of a package to name that package's cause is not an
        invariant — it is a thing you can only make true by rewriting the
        finding, which is what the first version of this fix did.

        What must hold is that membership was earned: the package's own cause
        really does claim the issue. Anything else is the positional bug.
        """
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)
        by_cause = {c.cause_id: set(c.affected_issue_ids) for c in result.root_causes}
        by_id = {i.issue_id: i for i in result.issues}

        for package in plan.packages:
            if package.kind != "systemic":
                continue
            claimed_here: set[str] = set()
            for cause_id in package.cause_ids:
                claimed_here |= by_cause.get(cause_id, set())

            for issue_id in package.affected_issue_ids:
                assert issue_id in claimed_here, (
                    f"{issue_id} no lo reclama ninguna causa del paquete"
                )
                origin = (by_id[issue_id].root_cause or {}).get("cause_id")
                if origin not in package.cause_ids:
                    assert issue_id in package.overlapping_issue_ids, (
                        f"{issue_id} nombra {origin}: el solape tiene que estar declarado"
                    )

    def test_overlap_is_declared_not_silent(self, analysed) -> None:
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)
        for package in plan.packages:
            if package.kind != "systemic":
                continue
            assert set(package.overlapping_issue_ids) <= set(package.affected_issue_ids)

    def test_a_merged_package_keeps_every_original_cause_id(self, analysed) -> None:
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)

        for package in plan.packages:
            if package.kind != "systemic":
                continue
            assert package.cause_ids, "un paquete sistémico nombra sus causas"
            assert set(package.issue_ids_by_cause) <= set(package.cause_ids)
            covered = {i for ids in package.issue_ids_by_cause.values() for i in ids}
            assert covered == set(package.affected_issue_ids), (
                "el desglose por causa cubre todo lo que el paquete cierra"
            )


class TestOneOwnerPerDecision:
    """Coverage may overlap. Execution may not.

    Three relationships, deliberately kept apart. An issue can be explained
    by several causes and all of them are right. A package may therefore
    claim to close an issue another package also closes. But only one package
    may SCHEDULE it, or two people do the same work — or each assumes the
    other did.
    """

    def test_decisions_are_globally_disjoint(self, analysed) -> None:
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)

        seen: dict[str, str] = {}
        for package in plan.executable_packages:
            for issue_id in package.decision_issue_ids:
                assert issue_id not in seen, (
                    f"{issue_id} lo ejecutan {seen.get(issue_id)} y {package.package_id}"
                )
                seen[issue_id] = package.package_id

    def test_sessions_respect_the_same_uniqueness(self, analysed) -> None:
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)
        systemic = {
            i for p in plan.packages if p.kind != "session" for i in p.decision_issue_ids
        }
        for package in plan.packages:
            if package.kind != "session":
                continue
            assert not (set(package.decision_issue_ids) & systemic)

    def test_coverage_may_overlap_and_says_so(self, analysed) -> None:
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)

        counts: dict[str, int] = {}
        for package in plan.packages:
            for issue_id in package.affected_issue_ids:
                counts[issue_id] = counts.get(issue_id, 0) + 1
        shared = {i for i, n in counts.items() if n > 1}

        assert plan.gross_affected_memberships >= plan.unique_affected_issues
        if shared:
            assert plan.gross_affected_memberships > plan.unique_affected_issues, (
                "si hay solape, el bruto tiene que superar a la unión"
            )

    def test_global_coverage_is_a_union_not_a_sum(self, analysed) -> None:
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)
        union = set()
        for package in plan.packages:
            union.update(package.affected_issue_ids)
        assert plan.unique_affected_issues == len(union)
        assert plan.unique_affected_issues <= len(result.issues), (
            "no se puede cubrir más problemas de los que hay"
        )

    def test_a_package_with_no_decisions_is_not_executable(self, analysed) -> None:
        result, profile = analysed
        plan = build_plan(copy.deepcopy(result), profile)
        for package in plan.packages:
            if package.decision_issue_ids:
                assert package.executable
                assert package.kind != "advisory"
            else:
                assert not package.executable
                assert package.kind == "advisory"
                assert "se ejecutan en otros paquetes" in package.action

    def test_shuffling_causes_does_not_move_ownership(self, analysed) -> None:
        """Ownership is a rule, not the order the loop happened to run in."""
        result, profile = analysed

        straight = copy.deepcopy(result)
        reversed_causes = copy.deepcopy(result)
        reversed_causes.root_causes = list(reversed(reversed_causes.root_causes))
        reversed_causes.issues = list(reversed(reversed_causes.issues))

        def ownership(plan: Any) -> dict[str, frozenset]:
            return {
                p.primary_cause_id: frozenset(p.decision_issue_ids)
                for p in plan.executable_packages
            }

        assert ownership(build_plan(straight, profile)) == ownership(
            build_plan(reversed_causes, profile)
        )

    def test_a_cause_that_loses_every_decision_becomes_context(self) -> None:
        """Forced, because no real export has produced one yet.

        Two causes explain the same five issues; only one of them is a
        decision after folding, and the issue names the first cause. The
        second still explains all five and must say so — but it schedules
        nothing, so it cannot be handed to anybody as work.
        """
        from naviscoord.analysis.pipeline import AnalysisResult

        issues = []
        for n in range(5):
            issue = _issue(n, 90.0 - n, f"c{n}")
            issue.issue_id = f"ISS-{n + 1:04d}"
            issue.priority = "critical" if n == 0 else "medium"
            issue.root_cause = {"cause_id": "RC-DUENA", "kind": "missing_penetration"}
            # All but the first fold under it, so one decision survives.
            if n:
                issue.folded_into = "ISS-0001"
            issues.append(issue)

        ids = [i.issue_id for i in issues]
        owner_cause = _cause("RC-DUENA", [], kind="missing_penetration")
        owner_cause.affected_issue_ids = list(ids)
        rival = _cause("RC-OTRA", [], kind="repeated_typology")
        rival.affected_issue_ids = list(ids)
        rival.confidence = 0.99

        result = AnalysisResult()
        result.issues = issues
        result.root_causes = [owner_cause, rival]

        plan = build_plan(result, Profile.load())
        by_cause = {p.primary_cause_id: p for p in plan.packages}

        assert "RC-OTRA" in by_cause, "la causa perdedora sigue apareciendo"
        context = by_cause["RC-OTRA"]
        assert context.kind == "advisory"
        assert not context.executable
        assert context.decision_issue_ids == []
        assert len(context.affected_issue_ids) == 5, "sigue declarando lo que explica"
        assert context.shared_affected_issue_ids == ["ISS-0001"], (
            "y dice qué decisión ejecuta otro"
        )

        work = by_cause["RC-DUENA"]
        assert work.executable and work.decision_issue_ids == ["ISS-0001"]
        assert plan.executable_packages == [work]

    def test_the_owner_is_the_cause_the_issue_names(self) -> None:
        """The first tiebreak, isolated from everything else."""
        from naviscoord.plan import assign_decision_owners

        issue = _issue(0, 50.0, "c0")
        issue.issue_id = "ISS-0001"
        issue.root_cause = {"cause_id": "RC-B"}

        weak = _cause("RC-B", [0])
        weak.confidence = 0.71
        strong = _cause("RC-A", [0])
        strong.confidence = 0.99
        for cause in (weak, strong):
            cause.affected_issue_ids = ["ISS-0001"]

        owners = assign_decision_owners(
            [strong, weak], {"ISS-0001": issue}, {"ISS-0001"}
        )
        assert owners["ISS-0001"] == "RC-B", (
            "la causa que la incidencia nombra gana aunque otra tenga más confianza"
        )

    def test_ties_fall_through_to_confidence_then_impact_then_id(self) -> None:
        from naviscoord.plan import assign_decision_owners

        issue = _issue(0, 50.0, "c0")
        issue.issue_id = "ISS-0001"
        issue.root_cause = {"cause_id": "RC-AUSENTE"}

        low = _cause("RC-A", [0])
        low.confidence = 0.80
        high = _cause("RC-B", [0])
        high.confidence = 0.95
        for cause in (low, high):
            cause.affected_issue_ids = ["ISS-0001"]

        owners = assign_decision_owners([low, high], {"ISS-0001": issue}, {"ISS-0001"})
        assert owners["ISS-0001"] == "RC-B", "sin coincidencia, manda la confianza"

        wide = _cause("RC-C", [0])
        wide.confidence = 0.95
        wide.affected_issue_ids = ["ISS-0001", "ISS-0002", "ISS-0003"]
        owners = assign_decision_owners(
            [high, wide], {"ISS-0001": issue}, {"ISS-0001"}
        )
        assert owners["ISS-0001"] == "RC-C", "a igual confianza, mayor impacto"


class TestEveryPackageKindIsBuilt:
    """The sample only produces systemic packages; the others need exercising.

    Renaming `issue_ids` to `decision_issue_ids` broke the session branch and
    the whole suite stayed green, because no fixture here reaches it. A real
    export found it in one run. This closes that hole.
    """

    def _many_issues(self) -> Any:
        from naviscoord.analysis.pipeline import AnalysisResult

        issues = []
        for n in range(12):
            issue = _issue(n, 80.0 - n, f"c{n}")
            issue.issue_id = f"ISS-{n + 1:04d}"
            issue.level = "PISO 3"
            # Sessions only gather critical and high work: a bucket of
            # medium-priority issues is the tail, not a meeting.
            issue.priority = "critical" if n < 3 else "high"
            issue.responsible = "HID"
            issue.discipline_pair = ("EST", "HID")
            issues.append(issue)
        result = AnalysisResult()
        result.issues = issues
        result.root_causes = []
        return result

    def test_a_plan_of_sessions_builds_and_serialises(self) -> None:
        profile = Profile.load()
        plan = build_plan(self._many_issues(), profile)
        sessions = [p for p in plan.packages if p.kind == "session"]
        assert sessions, "doce decisiones de una pareja y una zona son una sesión"

        for package in sessions:
            assert package.decision_issue_ids == package.affected_issue_ids, (
                "sin plegado, decidir y cerrar son la misma lista"
            )
            assert package.cause_ids == []
            assert package.primary_cause_id == ""
            payload = package.to_json()
            assert payload["affected_issue_count"] == len(package.affected_issue_ids)

    def test_the_plan_text_renders(self) -> None:
        profile = Profile.load()
        plan = build_plan(self._many_issues(), profile)
        assert "PLAN DE COORDINACIÓN" in plan.text(profile)


class TestNoPositionalLookupsRemain:
    def test_causes_resolve_every_cluster_in_the_sample(self, analysed) -> None:
        result, _ = analysed
        for cause in result.root_causes:
            assert not cause.unresolved_clusters
            assert len(cause.affected_issue_ids) == len(cause.affected_clusters)

    def test_the_plan_never_reads_cluster_positions(self) -> None:
        """Secondary guard. The behaviour above is the real one."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "naviscoord" / "plan.py").read_text(
            encoding="utf-8"
        )
        body = source.split("def build_plan", 1)[1]
        assert "enumerate(result.issues)" not in body
        assert "affected_clusters" not in body
