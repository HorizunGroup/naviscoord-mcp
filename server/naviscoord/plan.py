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
    issue_ids: list[str] = field(default_factory=list)
    rationale: str = ""

    @property
    def leverage(self) -> float:
        """Issues closed per decision taken — why this ordering exists."""
        return float(self.issues)

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
            "issue_ids": self.issue_ids[:40],
        }


@dataclass(slots=True)
class Plan:
    packages: list[Package]
    covered_issues: int
    total_issues: int
    tail_issues: int

    def to_json(self, limit: int = 20) -> dict[str, Any]:
        return {
            "packages": [p.to_json() for p in self.packages[:limit]],
            "package_count": len(self.packages),
            "covered_issues": self.covered_issues,
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
            f"{len(self.packages)} paquetes",
            "",
        ]
        for package in self.packages[:limit]:
            lines.append(
                f"[{package.package_id}] {package.title}"
            )
            who = (
                f"mueve {profile.label(package.owner)}"
                if package.owner
                else "varias especialidades"
            )
            if package.counterpart:
                who += f" · con {profile.label(package.counterpart)}"
            lines.append(
                f"    {package.issues} decisiones ({package.critical} críticas) · "
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
    affected: list[int] = []
    for cause in causes:
        affected.extend(cause.affected_clusters)

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
            affected_clusters=sorted(set(affected)),
            clash_count=sum(c.clash_count for c in causes),
            evidence={"systems": len(causes), "worst": named},
            suggested_action=(
                "Definir y comunicar el criterio de alturas libres por especialidad, y "
                f"rebajar los recorridos afectados de una pasada. Los peores: {named}."
            ),
            cause_id="RC-ALT",
        )
    ]


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
    index_by_position = {i: issue for i, issue in enumerate(result.issues)}

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

    for cause in sorted(ordered, key=lambda c: -c.clash_count):
        if cause.confidence < 0.7 or cause.kind not in single_decision_kinds:
            continue
        # Folded issues are closed by their representative and are not in
        # `decisions`; counting them made coverage exceed the total and the
        # tail come out negative.
        fresh = [
            index_by_position[i].issue_id
            for i in cause.affected_clusters
            if i in index_by_position
            and index_by_position[i].issue_id in decision_ids
            and index_by_position[i].issue_id not in claimed
        ]
        if len(fresh) < SESSION_MIN:
            continue

        owners: dict[str, int] = defaultdict(int)
        criticals = 0
        for issue_id in fresh:
            issue = by_id[issue_id]
            owners[issue.responsible] += 1
            if issue.priority == "critical":
                criticals += 1

        ranked_owners = sorted(owners.items(), key=lambda kv: -kv[1])
        owner = ranked_owners[0][0] if ranked_owners else ""
        # A package spanning every trade has no single owner, and naming one
        # sends the whole thing to the wrong person.
        spans = len([o for o, n in ranked_owners if n >= max(2, len(fresh) * 0.1)])
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
            " × ".join(profile.label(d) for d in by_id[i].discipline_pair if d)
            for i in fresh
        }
        levels = sorted({by_id[i].level for i in fresh if by_id[i].level})
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
                kind="systemic",
                title=cause.title + qualifier,
                owner=owner if spans <= 2 else "",
                counterpart=counterpart if spans <= 2 else "",
                scope="todo el modelo",
                issues=len(fresh),
                critical=criticals,
                clashes=cause.clash_count,
                action=cause.suggested_action,
                issue_ids=fresh,
                rationale=(
                    "Una sola decisión cierra todo el paquete; hacerla primero encoge "
                    "el resto del plan."
                    + (
                        f" Toca a {spans} especialidades, así que la decisión es de "
                        "coordinación, no de una sola."
                        if spans > 2
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
                    issue_ids=[i.issue_id for i in chunk],
                    rationale=(
                        "Una pareja de especialidades y una zona: alcance cerrado, "
                        "responsable único, cabe en una reunión."
                    ),
                )
            )
            claimed.update(i.issue_id for i in chunk)

    packages.sort(key=lambda p: (p.kind != "systemic", -p.critical, -p.issues))
    for position, package in enumerate(packages, start=1):
        package.package_id = f"PQ-{position:02d}"

    covered = len(claimed)
    return Plan(
        packages=packages,
        covered_issues=covered,
        total_issues=len(decisions),
        tail_issues=len(decisions) - covered,
    )
