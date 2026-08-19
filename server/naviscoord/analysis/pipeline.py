"""The analysis pipeline: raw export in, ranked issues out.

This is the contract the MCP layer depends on. It runs entirely offline on
an exported payload, which is what makes the whole engine testable without
Navisworks open and keeps the expensive thinking out of the UI thread.

Stage order is not arbitrary:

    tag → filter → collapse → congestion → score → root cause → rank

Tagging first because everything downstream keys off discipline. Filtering
before collapsing so noise cannot inflate a cluster and fake a systemic
problem. Congestion measured on collapsed issues rather than raw clashes so
a single messy crossing does not read as a crowded zone.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..model import Clash, ClashExport, ElementRef, Issue
from ..profile import UNCLASSIFIED, Profile
from .cluster import ClashCluster, build_clusters
from .declared import read_declarations
from .discipline import DisciplineTagger, TaggingReport
from .levels import LevelMap, normalise_levels
from .noise import FilterResult, NoiseFilter, fingerprint
from .rootcause import RootCause, RootCauseDetector
from .severity import SeverityScorer, suggest_action


def _pair(a: str, b: str) -> tuple[str, str]:
    """A trade pair with no near or far side, so A×B and B×A are one thing."""
    return tuple(sorted((a.strip().upper(), b.strip().upper())))  # type: ignore[return-value]


@dataclass(slots=True)
class MatrixCoverage:
    """How much of the clash matrix actually ran.

    Every count downstream is conditional on this and used not to be. A test
    that was never run contributes no results, contributes no criticals, and
    was therefore indistinguishable from a test that ran and found nothing —
    so a matrix where most tests had never been executed produced the same
    "no interference stops work" verdict as a genuinely clean model. Absence
    of evidence was being reported as evidence of absence.
    """

    total: int = 0
    ran: int = 0
    never_run: list[str] = field(default_factory=list)
    #: Trade pairs the profile says this project has to check, and the ones
    #: the tests actually compare. Counting only the tests that EXIST answers
    #: "did everything run", which is a different question from "was
    #: everything looked at" — and the second is the one a verdict rests on.
    #: On the reference model all 13 tests ran, so coverage read complete,
    #: while every one of them had structure on side A: not one architecture
    #: against services, nothing service against service, no electrical at
    #: all. Two of the sixteen pairs the profile requires.
    required_pairs: list[tuple[str, str]] = field(default_factory=list)
    covered_pairs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def missing_pairs(self) -> list[tuple[str, str]]:
        covered = set(self.covered_pairs)
        return [pair for pair in self.required_pairs if pair not in covered]

    @property
    def required_covered(self) -> int:
        """Required pairs that have a test.

        Not the same as how many pairs are covered: a matrix can compare a
        pair the profile never asked for — structure against architecture is
        the usual one — and counting those made the report say it compared
        "9 of the 8 pairs the profile requires".
        """
        covered = set(self.covered_pairs)
        return sum(1 for pair in self.required_pairs if pair in covered)

    @property
    def complete(self) -> bool:
        return self.total > 0 and not self.never_run and not self.missing_pairs

    @property
    def ratio(self) -> float:
        return self.ran / self.total if self.total else 0.0

    @classmethod
    def from_tests(
        cls,
        tests: list[dict[str, Any]] | None,
        declarations: dict[str, Any] | None = None,
        profile: Profile | None = None,
        present: set[str] | None = None,
    ) -> "MatrixCoverage":
        out = cls()
        for entry in tests or []:
            if not isinstance(entry, dict):
                continue
            out.total += 1
            status = str(entry.get("status", "") or "").strip().lower()
            exported = int(entry.get("exported", 0) or 0)
            # "New" is Navisworks for never run OR rules edited since the last
            # run. Paired with zero results it can only be the former; with
            # results it is a stale run, which still counts as evidence.
            if status == "new" and exported == 0:
                out.never_run.append(str(entry.get("name", "") or ""))
            else:
                out.ran += 1

        if profile is not None:
            for entry in profile.section("clash_matrix").get("pairs", []) or []:
                if not isinstance(entry, dict):
                    continue
                a, b = str(entry.get("a", "")), str(entry.get("b", ""))
                if not a or not b:
                    continue
                # Only pairs this model could actually produce. The profile
                # lists every combination the practice coordinates, sixteen of
                # them; a tower with no electrical and no sanitary in the
                # federation can never satisfy ten of those, and reporting
                # "3 of 16" turns a real gap — three trades that ARE modelled
                # and never compared — into a number nobody acts on.
                if present is not None and (a.upper() not in present or b.upper() not in present):
                    continue
                out.required_pairs.append(_pair(a, b))

        seen: set[tuple[str, str]] = set()
        for declaration in (declarations or {}).values():
            side_a = getattr(declaration, "side_a", "")
            side_b = getattr(declaration, "side_b", "")
            if side_a and side_b and side_a != side_b:
                seen.add(_pair(side_a, side_b))
        out.covered_pairs = sorted(seen)
        return out

    def to_json(self) -> dict[str, object]:
        return {
            "tests_total": self.total,
            "tests_with_evidence": self.ran,
            "tests_never_run": list(self.never_run),
            "pairs_required": [list(p) for p in self.required_pairs],
            "pairs_covered": [list(p) for p in self.covered_pairs],
            "pairs_missing": [list(p) for p in self.missing_pairs],
            "complete": self.complete,
        }


@dataclass(slots=True)
class AnalysisResult:
    issues: list[Issue] = field(default_factory=list)
    root_causes: list[RootCause] = field(default_factory=list)
    tagging: TaggingReport | None = None
    filtering: FilterResult | None = None
    levels: LevelMap | None = None
    raw_clash_count: int = 0
    profile_name: str = ""
    warnings: list[str] = field(default_factory=list)
    coverage: "MatrixCoverage" = field(default_factory=lambda: MatrixCoverage())

    @property
    def compression(self) -> float:
        """Raw clashes per surviving issue — the headline number."""
        if not self.issues:
            return 0.0
        return self.raw_clash_count / len(self.issues)

    def by_priority(self) -> dict[str, int]:
        counts: Counter[str] = Counter(issue.priority for issue in self.issues)
        return {band: counts.get(band, 0) for band in ("critical", "high", "medium", "low")}

    def by_responsible(self) -> dict[str, int]:
        counts: Counter[str] = Counter(issue.responsible for issue in self.issues if issue.responsible)
        return dict(counts.most_common())

    @property
    def decisions(self) -> list[Issue]:
        """Issues minus the ones another issue's fix already closes.

        This is the honest answer to "what do I open first". The raw issue
        list ranks a repeated detail once per floor, which is technically
        correct and practically useless.
        """
        return [issue for issue in self.issues if not issue.folded_into]

    def top(self, limit: int = 20) -> list[Issue]:
        return self.decisions[:limit]

    def summary(self, limit: int = 20) -> dict[str, Any]:
        """The compact view handed to an LLM. Never the full issue list."""
        return {
            "profile": self.profile_name,
            "raw_clashes": self.raw_clash_count,
            "issues": len(self.issues),
            "decisions": len(self.decisions),
            "compression": f"{self.compression:.1f} cruces por problema",
            "by_priority": self.by_priority(),
            "by_responsible": self.by_responsible(),
            "root_causes": [cause.to_json() for cause in self.root_causes[:8]],
            "top_issues": [issue.to_json() for issue in self.top(limit)],
            "noise": self.filtering.to_json() if self.filtering else {},
            "levels": self.levels.to_json() if self.levels else {},
            "tagging": self.tagging.to_json() if self.tagging else {},
            "warnings": self.warnings,
        }


def analyze(
    export: ClashExport,
    profile: Profile,
    discipline_overrides: dict[str, str] | None = None,
    approved_fingerprints: set[str] | None = None,
    group_key: str = "",
    group_roles: dict[str, str] | None = None,
) -> AnalysisResult:
    result = AnalysisResult(
        raw_clash_count=len(export.clashes), profile_name=profile.name
    )
    result.warnings.extend(profile.validate())

    declarations = read_declarations(export.tests, profile)
    tagger = DisciplineTagger(
        profile, discipline_overrides, group_key, group_roles, declarations
    )
    result.tagging = tagger.tag_export(export)
    result.warnings.extend(result.tagging.warnings())

    # Coverage is judged against the trades this federation actually holds,
    # so tagging has to have run first.
    present = {
        code
        for code, count in result.tagging.by_discipline.items()
        if count > 0 and code != UNCLASSIFIED
    }
    result.coverage = MatrixCoverage.from_tests(
        export.tests, declarations, profile, present
    )
    if result.coverage.never_run:
        missing = ", ".join(f"«{n}»" for n in result.coverage.never_run[:6])
        if len(result.coverage.never_run) > 6:
            missing += f" y {len(result.coverage.never_run) - 6} más"
        result.warnings.append(
            f"{len(result.coverage.never_run)} de {result.coverage.total} tests nunca "
            f"se han corrido: {missing}. Los conteos de abajo solo cubren los tests "
            "que sí tienen resultados; un cero aquí no significa que esos pares estén limpios."
        )
    if result.coverage.missing_pairs:
        pending = ", ".join(f"{a}×{b}" for a, b in result.coverage.missing_pairs[:8])
        if len(result.coverage.missing_pairs) > 8:
            pending += f" y {len(result.coverage.missing_pairs) - 8} más"
        result.warnings.append(
            f"La matriz compara {result.coverage.required_covered} de las "
            f"{len(result.coverage.required_pairs)} parejas que exige el perfil. "
            f"Sin tests: {pending}. Que todos los tests hayan corrido no significa "
            "que se haya mirado todo: de esas parejas este informe no dice nada."
        )
    # Before anything groups by level. Clustering, hotspots, the work plan
    # and every per-level count all key off this string, so reconciling it
    # afterwards would mean redoing all of them.
    result.levels = normalise_levels(export.clashes)
    result.warnings.extend(result.levels.notes())

    noise = NoiseFilter(profile, approved_fingerprints)
    result.filtering = noise.run(export.clashes)
    result.warnings.extend(result.filtering.warnings())
    if not result.filtering.kept:
        result.warnings.append(
            "El filtro descartó todos los cruces. Revisa noise_filter en el perfil "
            "antes de concluir que el modelo está limpio."
        )
        return result

    clusters = build_clusters(result.filtering.kept, profile)
    congestion = _congestion_map(clusters, profile)

    scorer = SeverityScorer(profile)
    issues: list[Issue] = []
    for index, cluster in enumerate(clusters):
        verdict = scorer.score(cluster, congestion.get(index, 0))
        low, high = cluster.bounds
        issues.append(
            Issue(
                issue_id="",  # assigned after ranking so IDs follow priority
                kind=cluster.kind,
                discipline_pair=cluster.discipline_pair,
                clash_ids=[c.guid for c in cluster.clashes],
                centroid=cluster.centroid,
                bbox_min=low,
                bbox_max=high,
                severity=verdict.score,
                priority=profile.priority_for(verdict.score),
                level=cluster.dominant("level"),
                grid=cluster.dominant("grid"),
                max_penetration_m=cluster.max_penetration,
                responsible=verdict.responsible,
                immovable_side=verdict.immovable_side,
                why=verdict.why,
                suggested_action=suggest_action(cluster, verdict, profile),
                score_breakdown=verdict.breakdown,
                elements_a=sorted({c.a.path_id for c in cluster.clashes}),
                elements_b=sorted({c.b.path_id for c in cluster.clashes}),
                discipline_a=cluster.side_disciplines[0],
                discipline_b=cluster.side_disciplines[1],
                representative_clash_id=(
                    cluster.representative.guid if cluster.representative else ""
                ),
                elements_by_discipline=cluster.elements_by_discipline(),
            )
        )

    elements = _all_elements(result.filtering.kept)
    detector = RootCauseDetector(profile)
    # The sleeve index travels separately from the surviving elements. A
    # sleeve is filtered out of the ranking precisely BECAUSE it is doing its
    # job, so passing only `kept` here made the missing-penetration detector
    # confidently report zero sleeves on a model full of them.
    result.root_causes = detector.run(clusters, elements, sleeves=result.filtering.sleeves)
    _attach_root_causes(issues, result.root_causes)

    order = sorted(range(len(issues)), key=lambda i: (-issues[i].severity, -issues[i].clash_count))
    ranked: list[Issue] = []
    for rank, original_index in enumerate(order, start=1):
        issue = issues[original_index]
        issue.issue_id = f"ISS-{rank:04d}"
        ranked.append(issue)

    _fold_shared_causes(ranked, profile)
    result.issues = ranked
    return result


def _fold_shared_causes(issues: list[Issue], profile: Profile) -> None:
    """Collapse issues that one decision closes into a single entry.

    A riser clashing with the same beam on ten floors is ten issues and one
    decision. Ranking it ten times pushes nine genuinely different problems
    off the first page, so the highest-severity one represents the set and
    the rest fold under it. They are not deleted — they keep their IDs, their
    scores and their clash lists, and still get their own group and viewpoint
    on write-back. They just stop competing for attention.
    """
    foldable = profile.folding_kinds()
    if not foldable:
        return

    groups: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        cause = issue.root_cause
        if cause and cause.get("kind") in foldable and cause.get("cause_id"):
            groups[cause["cause_id"]].append(issue)

    for members in groups.values():
        if len(members) < 2:
            continue
        # `issues` is already ranked, so the first member is the worst.
        representative, *rest = members
        representative.folds = len(rest)
        representative.why.append(
            f"Representa {len(rest)} problemas más con la misma causa: "
            "una sola decisión los cierra todos."
        )
        for other in rest:
            other.folded_into = representative.issue_id


def _congestion_map(clusters: list[ClashCluster], profile: Profile) -> dict[int, int]:
    """Distinct disciplines within the congestion radius of each issue.

    Counted over issues, not raw clashes, so a single busy crossing does not
    masquerade as a crowded zone.
    """
    radius = profile.severity_param("congestion_radius_m", 2.5)
    if radius <= 0.0:
        return {}

    cells: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for index, cluster in enumerate(clusters):
        centroid = cluster.centroid
        cells[
            (int(centroid[0] // radius), int(centroid[1] // radius), int(centroid[2] // radius))
        ].append(index)

    congestion: dict[int, int] = {}
    for index, cluster in enumerate(clusters):
        centroid = cluster.centroid
        cx, cy, cz = (
            int(centroid[0] // radius),
            int(centroid[1] // radius),
            int(centroid[2] // radius),
        )
        disciplines: set[str] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for neighbour in cells.get((cx + dx, cy + dy, cz + dz), ()):
                        other = clusters[neighbour]
                        if _within(other.centroid, centroid, radius):
                            disciplines.update(d for d in other.discipline_pair if d)
        congestion[index] = len(disciplines)
    return congestion


def _within(a, b, radius: float) -> bool:
    return (
        abs(a[0] - b[0]) <= radius
        and abs(a[1] - b[1]) <= radius
        and abs(a[2] - b[2]) <= radius
    )


def _all_elements(clashes: list[Clash]) -> list[ElementRef]:
    seen: dict[str, ElementRef] = {}
    for clash in clashes:
        for side in (clash.a, clash.b):
            seen.setdefault(side.path_id, side)
    return list(seen.values())


def _attach_root_causes(issues: list[Issue], causes: list[RootCause]) -> None:
    """Annotate each issue with the strongest cause that claims it."""
    best: dict[int, RootCause] = {}
    for cause in causes:
        for index in cause.affected_clusters:
            current = best.get(index)
            if current is None or cause.confidence > current.confidence:
                best[index] = cause
    for index, cause in best.items():
        if index >= len(issues):
            continue
        issue = issues[index]
        issue.root_cause = {
            "cause_id": cause.cause_id,
            "kind": cause.kind,
            "title": cause.title,
            "confidence": round(cause.confidence, 2),
        }
        issue.why.append(f"Causa raíz probable: {cause.title.lower()}.")
        if cause.kind == "systemic_elevation":
            issue.kind = "systemic"


def hotspots(result: AnalysisResult, cell_size: float = 5.0, limit: int = 10) -> list[dict[str, Any]]:
    """Worst zones by accumulated severity, for walking a model in order."""
    cells: dict[tuple[int, int, int], list[Issue]] = defaultdict(list)
    for issue in result.issues:
        centroid = issue.centroid
        cells[
            (
                int(centroid[0] // cell_size),
                int(centroid[1] // cell_size),
                int(centroid[2] // cell_size),
            )
        ].append(issue)

    ranked = sorted(
        cells.items(), key=lambda kv: -sum(issue.severity for issue in kv[1])
    )
    out: list[dict[str, Any]] = []
    for (cx, cy, cz), members in ranked[:limit]:
        disciplines = sorted({d for issue in members for d in issue.discipline_pair if d})
        out.append(
            {
                "centre": [
                    round((cx + 0.5) * cell_size, 1),
                    round((cy + 0.5) * cell_size, 1),
                    round((cz + 0.5) * cell_size, 1),
                ],
                "level": Counter(i.level for i in members if i.level).most_common(1)[0][0]
                if any(i.level for i in members)
                else "",
                "issues": len(members),
                "clashes": sum(i.clash_count for i in members),
                "total_severity": round(sum(i.severity for i in members), 1),
                "max_severity": round(max(i.severity for i in members), 1),
                "disciplines": disciplines,
                "top_issue": members[0].issue_id if members else "",
            }
        )
    return out


def approved_set(result: AnalysisResult, issue_ids: set[str]) -> set[str]:
    """Fingerprints to carry into the next run so approvals survive a rerun."""
    index = {issue.issue_id: issue for issue in result.issues}
    lookup: dict[str, Clash] = {}
    if result.filtering:
        lookup = {clash.guid: clash for clash in result.filtering.kept}
    out: set[str] = set()
    for issue_id in issue_ids:
        issue = index.get(issue_id)
        if not issue:
            continue
        for guid in issue.clash_ids:
            clash = lookup.get(guid)
            if clash:
                out.add(fingerprint(clash))
    return out
