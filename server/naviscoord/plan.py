"""Turning a ranked list into work somebody can actually schedule.

A thousand ranked decisions is not a plan. Nobody works a list of 1.375
items, and handing one over is how a coordination report ends up unread in a
shared folder — correct, complete and useless.

What a coordinator actually schedules is packages:

* **Systemic** — one decision that closes hundreds. Approving a sleeve
  schedule retires a thousand crossings at once, and it belongs at the top
  because everything else shrinks once it lands.
* **Sessions** — one discipline pair, one zone, sized to fit a single
  meeting. Two teams, one table, a defined scope, an owner.
* **Tail** — everything left, counted rather than listed. Pretending it is
  actionable is what inflates the plan back to a thousand rows.

The sizes are deliberate: a package too big never starts, and a package per
issue is the list again with extra steps.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .analysis.pipeline import AnalysisResult
from .model import Issue
from .profile import Profile

# What fits in one working session. Below the floor a package is not worth
# convening; above the ceiling it will not finish and gets abandoned midway.
SESSION_MIN = 4
SESSION_MAX = 25


@dataclass(slots=True)
class Package:
    package_id: str
    kind: str  # "systemic" | "session" | "tail"
    title: str
    owner: str
    counterpart: str
    scope: str
    issues: int
    critical: int
    clashes: int
    action: str
    #: What somebody has to decide or execute. After folding, a systemic cause
    #: can come down to a single representative — that is the leverage, not a
    #: reason to drop the package.
    decision_issue_ids: list[str] = field(default_factory=list)
    #: Everything that decision closes, folded issues included. Different
    #: semantics from the field above, and the difference is the whole value
    #: of a systemic package: one decision, ten problems gone.
    affected_issue_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    #: Every cause this package closes, never collapsed to one. A package that
    #: merges several elevation findings answers for all of them, and losing
    #: the originals loses the trail back to the evidence.
    cause_ids: list[str] = field(default_factory=list)
    #: For the title and the summary line only. It is NOT a claim that every
    #: issue in the package carries this cause: an issue keeps the cause it
    #: was detected under, and the plan has no business rewriting it.
    primary_cause_id: str = ""
    #: Which issues came from which cause, for a package that closes several.
    issue_ids_by_cause: dict[str, list[str]] = field(default_factory=dict)
    affected_clash_count: int = 0
    #: Decisions this package closes but does NOT execute: another package
    #: owns them. Listed so the coverage claim stays honest without the work
    #: being scheduled twice.
    shared_affected_issue_ids: list[str] = field(default_factory=list)
    #: Issues here that another root cause also explains.
    #:
    #: Causes overlap — a crossing can be both a missing sleeve and one floor
    #: of a repeated detail — and an issue records only the strongest claim.
    #: So an issue can sit in this package and name a different cause, without
    #: either being wrong. Declared rather than hidden, because the honest
    #: alternative to surprising a reader is not rewriting the issue.
    overlapping_issue_ids: list[str] = field(default_factory=list)

    @property
    def issue_ids(self) -> list[str]:
        """The decisions. Kept under the old name for existing readers."""
        return self.decision_issue_ids

    @property
    def affected_issue_count(self) -> int:
        return len(self.affected_issue_ids)

    @property
    def leverage(self) -> float:
        """Problems closed per package — why this ordering exists.

        Counted over everything the package closes, not over the
        representatives that survived folding. Ranking by representatives put
        a decision that clears ten issues below a session that clears four.
        """
        return float(max(self.affected_issue_count, self.issues))

    def to_json(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "kind": self.kind,
            "title": self.title,
            "owner": self.owner,
            "counterpart": self.counterpart,
            "scope": self.scope,
            "issues": self.issues,
            "critical": self.critical,
            "clashes": self.clashes,
            "action": self.action,
            "rationale": self.rationale,
            "decision_issue_ids": self.decision_issue_ids[:40],
            "affected_issue_ids": self.affected_issue_ids[:80],
            "affected_issue_count": self.affected_issue_count,
            "affected_clash_count": self.affected_clash_count,
            "cause_ids": list(self.cause_ids),
            "primary_cause_id": self.primary_cause_id,
            "issue_ids_by_cause": {
                cause: list(ids) for cause, ids in self.issue_ids_by_cause.items()
            },
            "overlapping_issue_ids": list(self.overlapping_issue_ids),
            "shared_affected_issue_ids": list(self.shared_affected_issue_ids),
            "executable": self.executable,
        }

    @property
    def executable(self) -> bool:
        """Whether this package is work somebody can be given."""
        return bool(self.decision_issue_ids)


@dataclass(slots=True)
class Plan:
    packages: list[Package]
    covered_issues: int
    total_issues: int
    tail_issues: int

    @property
    def executable_packages(self) -> list[Package]:
        return [p for p in self.packages if p.executable]

    @property
    def unique_affected_issues(self) -> int:
        """Distinct problems the plan touches — a union, never a sum.

        Packages may legitimately cover the same issue, because causes
        overlap. Adding their counts and calling the total "coverage" would
        report more problems than the model contains, which is how a plan
        starts sounding better than the project it describes.
        """
        seen: set[str] = set()
        for package in self.packages:
            seen.update(package.affected_issue_ids)
        return len(seen)

    @property
    def gross_affected_memberships(self) -> int:
        """Every package-issue pairing, overlaps included. Not a coverage."""
        return sum(len(p.affected_issue_ids) for p in self.packages)

    def to_json(self, limit: int = 20) -> dict[str, Any]:
        return {
            "packages": [p.to_json() for p in self.packages[:limit]],
            "package_count": len(self.packages),
            "executable_package_count": len(self.executable_packages),
            "covered_issues": self.covered_issues,
            "unique_affected_issues": self.unique_affected_issues,
            "gross_affected_memberships": self.gross_affected_memberships,
            "total_issues": self.total_issues,
            "tail_issues": self.tail_issues,
            "summary": (
                f"{self.total_issues} decisiones se agrupan en {len(self.packages)} "
                f"paquetes de trabajo. Los primeros cierran la mayor parte."
            ),
        }

    def text(self, profile: Profile, limit: int = 12) -> str:
        lines = [
            f"PLAN DE COORDINACIÓN — {self.total_issues} decisiones en "
            f"{len(self.executable_packages)} paquetes de trabajo",
            "",
        ]
        for package in self.packages[:limit]:
            marca = "" if package.executable else "  (solo contexto)"
            lines.append(
                f"[{package.package_id}] {package.title}{marca}"
            )
            who = (
                f"mueve {profile.label(package.owner)}"
                if package.owner
                else "varias especialidades"
            )
            if package.counterpart:
                who += f" · con {profile.label(package.counterpart)}"
            cierra = (
                f" · cierra {package.affected_issue_count}"
                if package.affected_issue_count > package.issues
                else ""
            )
            lines.append(
                f"    {package.issues} decisiones ({package.critical} críticas){cierra} · "
                f"{who}" + (f" · {package.scope}" if package.scope else "")
            )
            lines.append(f"    → {package.action}")
            lines.append("")
        if self.tail_issues:
            lines.append(
                f"Cola: {self.tail_issues} decisiones de baja prioridad, sin paquete propio. "
                "Se cierran con las anteriores o se revisan al final."
            )
        return "\n".join(lines)


def _merge_elevation_causes(causes: list[Any]) -> list[Any]:
    """Collapses per-system elevation findings into one decision."""
    if len(causes) < 2:
        return list(causes)

    from .analysis.rootcause import RootCause

    worst = sorted(causes, key=lambda c: -c.clash_count)[:3]
    named = "; ".join(
        f"«{c.evidence.get('system', '?')}» ({c.clash_count})" for c in worst
    )
    # Ids, not positions. The merged cause is consumed after the ranking, so
    # carrying cluster indices here would reintroduce the very bug this file
    # was the victim of.
    affected_ids: list[str] = []
    contributions: dict[str, list[str]] = {}
    for cause in causes:
        if cause.cause_id:
            contributions[cause.cause_id] = list(cause.affected_issue_ids)
        for issue_id in cause.affected_issue_ids:
            if issue_id not in affected_ids:
                affected_ids.append(issue_id)

    return [
        RootCause(
            kind="systemic_elevation",
            title=f"Criterio de alturas libres: {len(causes)} sistemas mal trazados",
            detail=(
                f"{len(causes)} sistemas MEP repiten una penetración casi idéntica cruce "
                "tras cruce. Que le ocurra a tantos a la vez no son descuidos sueltos: "
                "es un criterio de alturas libres mal definido o mal comunicado."
            ),
            confidence=max(c.confidence for c in causes),
            affected_clusters=[],
            affected_issue_ids=affected_ids,
            merged_from=[c.cause_id for c in causes if c.cause_id],
            contributions=contributions,
            clash_count=sum(c.clash_count for c in causes),
            evidence={"systems": len(causes), "worst": named},
            suggested_action=(
                "Definir y comunicar el criterio de alturas libres por especialidad, y "
                f"rebajar los recorridos afectados de una pasada. Los peores: {named}."
            ),
            cause_id="RC-ALT",
        )
    ]


def assign_decision_owners(
    causes: list[Any],
    issues_by_id: dict[str, Issue],
    decision_ids: set[str],
) -> dict[str, str]:
    """One owning cause per decision, chosen the same way every time.

    Three relationships are being kept apart here, and conflating them is
    what makes plans either lie or duplicate work.

    Causal membership is many-to-many: on a real export 51 issues are
    explained by two causes at once, and both explanations are true. Package
    coverage inherits that and may overlap — a package should be able to say
    "this closes 176 problems" even if some of them are also closed by
    another decision. Execution responsibility cannot: schedule the same
    decision in two packages and two people do the same work, or each assumes
    the other did.

    So the owner is resolved before any package is built, by a rule that does
    not depend on the order causes happen to be visited:

    1. the cause the issue itself names, if one of the contenders is it;
    2. then the most confident;
    3. then the one closing the most;
    4. then the id, so the answer is stable rather than merely deterministic.

    A merged cause answers to any of the ids it absorbed, since the issue was
    stamped with one of those before the merge existed.
    """
    claims: dict[str, list[Any]] = defaultdict(list)
    for cause in causes:
        for issue_id in cause.affected_issue_ids:
            if issue_id in decision_ids:
                claims[issue_id].append(cause)

    owners: dict[str, str] = {}
    for issue_id, contenders in claims.items():
        issue = issues_by_id.get(issue_id)
        headline = (issue.root_cause or {}).get("cause_id") if issue else None

        def rank(cause: Any) -> tuple[Any, ...]:
            names = {cause.cause_id, *cause.merged_from}
            return (
                headline not in names,
                -cause.confidence,
                -len(cause.affected_issue_ids),
                cause.cause_id,
            )

        owners[issue_id] = min(contenders, key=rank).cause_id
    return owners


def build_plan(
    result: AnalysisResult,
    profile: Profile,
    *,
    session_max: int = SESSION_MAX,
) -> Plan:
    decisions = result.decisions
    packages: list[Package] = []
    claimed: set[str] = set()
    counter = 0

    # --- systemic first: one decision, many issues closed.
    #
    # Only causes where a SINGLE decision actually closes the set. A congested
    # zone is not one of them — it is several trades needing a table, which is
    # a session. Filing it as systemic produced a plan of sixty packages where
    # fifty were three-issue zones dressed up as strategy.
    single_decision_kinds = {"missing_penetration", "systemic_elevation", "repeated_typology"}
    decision_ids = {issue.issue_id for issue in decisions}
    by_id = {issue.issue_id: issue for issue in decisions}
    # Every issue in the analysis, by id. There is deliberately no map from
    # position: `result.issues` is sorted by severity, and the previous
    # version indexed it with cluster positions from before that sort, which
    # is how a package about free heights filled with a congested zone's
    # issues.
    all_by_id = {issue.issue_id: issue for issue in result.issues}

    # Systemic elevation causes are merged into one package. A real model
    # produces dozens of them — 48 on this project — and emitting one package
    # per system turns "the free-height criterion is wrong" into forty-eight
    # separate errands, which is the list again wearing a hat.
    merged = _merge_elevation_causes(
        [c for c in result.root_causes if c.kind == "systemic_elevation" and c.confidence >= 0.7]
    )
    ordered = merged + [
        c for c in result.root_causes if c.kind != "systemic_elevation"
    ]

    # Who executes what, settled before a single package exists. Computed over
    # the causes that will actually be packaged, so a merged cause competes as
    # the one thing it has become.
    packagable = [
        c for c in ordered
        if c.confidence >= 0.7 and c.kind in single_decision_kinds
    ]
    owner_of = assign_decision_owners(packagable, all_by_id, decision_ids)

    for cause in sorted(ordered, key=lambda c: -c.clash_count):
        if cause.confidence < 0.7 or cause.kind not in single_decision_kinds:
            continue
        # Everything the cause explains, and separately the representatives
        # somebody still has to act on. Only the representatives are claimed
        # and counted as coverage — folded issues are closed by them, and
        # adding both made coverage exceed the total and the tail go negative.
        affected = [i for i in cause.affected_issue_ids if i in all_by_id]
        # Only the decisions this cause OWNS. A decision another cause owns
        # stays in `affected` — the package really does close it — but it is
        # not scheduled here, because it is already scheduled there.
        fresh = [
            i for i in affected
            if i in decision_ids and i not in claimed and owner_of.get(i) == cause.cause_id
        ]
        shared_decisions = [
            i for i in affected
            if i in decision_ids and owner_of.get(i) not in (None, cause.cause_id)
        ]

        # Eligibility on impact, not on survivors.
        #
        # Folding collapses a cause's issues under one representative, so a
        # finding that closes ten problems with one decision arrives here with
        # a single decision — and testing `len(fresh) < SESSION_MIN` threw it
        # away for being too small. That is backwards: one decision clearing
        # ten issues is the highest-leverage thing in the plan.
        if len(affected) < SESSION_MIN:
            continue

        # Every decision it explains belongs to somebody else. The finding is
        # real and worth reading, but it is not work: presenting it with an
        # empty action list would put a package on the plan that nobody can
        # execute and that double-counts what another package already covers.
        advisory = not fresh

        # Which cause contributed which issues. For a merged package this is
        # the trail back to the original findings; collapsing them to one id
        # would lose it, and re-stamping the issues to match would be the plan
        # rewriting the analysis to agree with itself.
        contributions: dict[str, list[str]] = {}
        for origin, ids in (cause.contributions or {}).items():
            kept = [i for i in ids if i in all_by_id]
            if kept:
                contributions[origin] = kept
        if not contributions and cause.cause_id:
            contributions[cause.cause_id] = list(affected)
        sources = [c for c in cause.merged_from if c] or list(contributions)
        mine = set(sources)
        overlapping = [
            issue_id
            for issue_id in affected
            if any(
                other.cause_id not in mine and issue_id in other.affected_issue_ids
                for other in result.root_causes
            )
        ]

        owners: dict[str, int] = defaultdict(int)
        criticals = 0
        for issue_id in affected:
            issue = all_by_id[issue_id]
            owners[issue.responsible] += 1
            if issue.priority == "critical":
                criticals += 1

        ranked_owners = sorted(owners.items(), key=lambda kv: -kv[1])
        owner = ranked_owners[0][0] if ranked_owners else ""
        # A package spanning every trade has no single owner, and naming one
        # sends the whole thing to the wrong person.
        spans = len([o for o, n in ranked_owners if n >= max(2, len(affected) * 0.1)])
        counterpart = ranked_owners[1][0] if len(ranked_owners) > 1 else ""

        # Two causes of the same kind produce the same title — "Cruce
        # repetido en 13 niveles" twice, with nothing to tell them apart on a
        # printed plan.
        #
        # Only qualify with the discipline pair when the package HAS one.
        # Picking an arbitrary pair out of four titled the sleeve-schedule
        # package "Arquitectura × Eléctrica" and contradicted the owner line
        # printed directly underneath it.
        pairs = {
            " × ".join(profile.label(d) for d in all_by_id[i].discipline_pair if d)
            for i in affected
        }
        levels = sorted({all_by_id[i].level for i in affected if all_by_id[i].level})
        qualifier = ""
        if len(pairs) == 1:
            qualifier = " · " + next(iter(pairs))
        elif len(pairs) > 1:
            qualifier = f" · {len(pairs)} parejas de especialidades"
        if levels:
            qualifier += (
                f" · {levels[0]}" if len(levels) == 1 else f" · {len(levels)} niveles desde {levels[0]}"
            )

        counter += 1
        packages.append(
            Package(
                package_id=f"PQ-{counter:02d}",
                kind="advisory" if advisory else "systemic",
                title=cause.title + qualifier,
                owner=owner if spans <= 2 else "",
                counterpart=counterpart if spans <= 2 else "",
                scope="todo el modelo",
                issues=len(fresh),
                critical=criticals,
                clashes=cause.clash_count,
                action=(
                    cause.suggested_action
                    if not advisory
                    else "Sin trabajo propio: sus decisiones se ejecutan en otros paquetes. "
                    "Léelo como contexto de por qué aparecen."
                ),
                decision_issue_ids=fresh,
                affected_issue_ids=affected,
                shared_affected_issue_ids=shared_decisions,
                affected_clash_count=cause.clash_count,
                cause_ids=sources or ([cause.cause_id] if cause.cause_id else []),
                primary_cause_id=cause.cause_id,
                issue_ids_by_cause=contributions,
                overlapping_issue_ids=overlapping,
                rationale=(
                    (
                        f"Una sola decisión cierra {len(affected)} problemas; hacerla "
                        "primero encoge el resto del plan."
                        if not advisory
                        else f"Explica {len(affected)} problemas, pero todos se deciden "
                        "en otro paquete. No es trabajo aparte."
                    )
                    + (
                        f" Toca a {spans} especialidades, así que la decisión es de "
                        "coordinación, no de una sola."
                        if spans > 2 and not advisory
                        else ""
                    )
                ),
            )
        )
        claimed.update(fresh)

    # --- sessions: one discipline pair, one zone, one meeting.
    buckets: dict[tuple[str, str, str], list[Issue]] = defaultdict(list)
    for issue in decisions:
        if issue.issue_id in claimed:
            continue
        if issue.priority not in ("critical", "high"):
            continue
        pair = tuple(sorted(d for d in issue.discipline_pair if d))
        key = (pair[0] if pair else "", pair[-1] if pair else "", issue.level or "sin nivel")
        buckets[key].append(issue)

    for (a, b, level), issues in sorted(
        buckets.items(), key=lambda kv: -sum(1 for i in kv[1] if i.priority == "critical")
    ):
        if len(issues) < SESSION_MIN:
            continue
        issues.sort(key=lambda i: -i.severity)

        # Split oversized buckets rather than emit a package nobody finishes.
        for offset in range(0, len(issues), session_max):
            chunk = issues[offset : offset + session_max]
            if len(chunk) < SESSION_MIN:
                break
            counter += 1
            owners = defaultdict(int)
            for issue in chunk:
                owners[issue.responsible] += 1
            owner = max(owners, key=owners.get) if owners else ""
            counterpart = a if owner == b else b
            part = f" ({offset // session_max + 1})" if len(issues) > session_max else ""

            packages.append(
                Package(
                    package_id=f"PQ-{counter:02d}",
                    kind="session",
                    title=f"{profile.label(a)} × {profile.label(b)} en {level}{part}",
                    owner=owner,
                    counterpart=counterpart,
                    scope=level,
                    issues=len(chunk),
                    critical=sum(1 for i in chunk if i.priority == "critical"),
                    clashes=sum(i.clash_count for i in chunk),
                    action=(
                        f"Sesión de {profile.label(a)} con {profile.label(b)} sobre {level}. "
                        f"Mueve {profile.label(owner)}; llevar las {len(chunk)} decisiones "
                        "resueltas de una pasada."
                    ),
                    # A session has no folding behind it: what you decide and
                    # what you close are the same list.
                    decision_issue_ids=[i.issue_id for i in chunk],
                    affected_issue_ids=[i.issue_id for i in chunk],
                    affected_clash_count=sum(i.clash_count for i in chunk),
                    rationale=(
                        "Una pareja de especialidades y una zona: alcance cerrado, "
                        "responsable único, cabe en una reunión."
                    ),
                )
            )
            claimed.update(i.issue_id for i in chunk)

    # Work first, then advisories. A package nobody can execute must not sit
    # above one somebody has to.
    packages.sort(
        key=lambda p: (not p.executable, p.kind != "systemic", -p.critical, -p.leverage)
    )
    for position, package in enumerate(packages, start=1):
        package.package_id = f"PQ-{position:02d}"

    covered = len(claimed)
    return Plan(
        packages=packages,
        covered_issues=covered,
        total_issues=len(decisions),
        tail_issues=len(decisions) - covered,
    )
