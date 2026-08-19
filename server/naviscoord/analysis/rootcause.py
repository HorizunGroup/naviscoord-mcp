"""Root-cause detection: the difference between a list and an insight.

Ranking issues tells a coordinator what to open first. It still leaves them
opening fifty things. What actually compresses a coordination cycle is
noticing that forty of those fifty share one cause — a rack set at the wrong
elevation, a missing sleeve family, a detail repeating on every floor — and
that one decision closes them all.

Each detector is a heuristic with a stated confidence, and each says in
plain language what it saw and what it would take to test the theory. None
of them modify anything; they annotate.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..model import Clash, ElementRef, Point, centroid_of
from ..profile import Profile
from .cluster import ClashCluster


@dataclass(slots=True)
class RootCause:
    kind: str
    title: str
    detail: str
    confidence: float
    affected_clusters: list[int] = field(default_factory=list)
    clash_count: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_action: str = ""
    cause_id: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "cause_id": self.cause_id,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "confidence": round(self.confidence, 2),
            "affected_issues": len(self.affected_clusters),
            "clash_count": self.clash_count,
            "evidence": self.evidence,
            "suggested_action": self.suggested_action,
        }


class RootCauseDetector:
    def __init__(self, profile: Profile) -> None:
        self.profile = profile

    def run(
        self,
        clusters: list[ClashCluster],
        all_elements: Iterable[ElementRef] = (),
        sleeves: Iterable[ElementRef] = (),
    ) -> list[RootCause]:
        """`sleeves` is separate because the noise filter removes them.

        A sleeve is dropped as `designed_pass_through` — correctly, it is not
        an interference — so it never appears among `all_elements`. Passing
        the index alongside is what keeps "are there sleeves in this model?"
        answerable at all.
        """
        causes: list[RootCause] = []
        causes.extend(self._systemic_elevation(clusters))
        causes.extend(self._missing_penetration(clusters, list(all_elements), list(sleeves)))
        causes.extend(self._repeated_typology(clusters))
        causes.extend(self._congested_zones(clusters))
        causes.sort(key=lambda c: (-c.clash_count, -c.confidence))
        for index, cause in enumerate(causes, start=1):
            cause.cause_id = f"RC-{index:03d}"
        return causes

    # ------------------------------------------------- systemic elevation

    def _systemic_elevation(self, clusters: list[ClashCluster]) -> list[RootCause]:
        """One system meeting many hard elements at a consistent depth.

        If a whole run is buried the same amount into everything it passes,
        the run is at the wrong height. The tell is a low spread in the
        vertical overlap — a genuinely random set of clashes has a wide one.
        """
        min_clashes = int(self.profile.root_cause_param("systemic_elevation", "min_clashes", 6))
        max_std = float(
            self.profile.root_cause_param("systemic_elevation", "max_z_stddev_m", 0.12)
        )
        min_confidence = float(
            self.profile.root_cause_param("systemic_elevation", "min_confidence", 0.6)
        )

        buckets: dict[tuple[str, str], list[tuple[int, Clash]]] = defaultdict(list)
        for index, cluster in enumerate(clusters):
            for clash in cluster.clashes:
                key = _system_key(clash)
                if key:
                    buckets[key].append((index, clash))

        causes: list[RootCause] = []
        for (system, hard_discipline), entries in buckets.items():
            if len(entries) < min_clashes:
                continue
            # Only crossings a vertical move could actually change. Without
            # this the statistic is computed over pass-throughs, where the
            # overlap is the run's own diameter and its tightness says
            # nothing about elevation.
            entries = [pair for pair in entries if _is_elevation_candidate(pair[1])]
            if len(entries) < min_clashes:
                continue
            overlaps = [_vertical_overlap(clash) for _, clash in entries]
            overlaps = [o for o in overlaps if o > 0.0]
            if len(overlaps) < min_clashes:
                continue

            mean = sum(overlaps) / len(overlaps)
            std = math.sqrt(sum((o - mean) ** 2 for o in overlaps) / len(overlaps))
            if std > max_std or mean <= 0.0:
                continue

            # Tight spread over many hits is strong evidence; loosen either
            # and the confidence falls away.
            spread_score = 1.0 - min(std / max_std, 1.0)
            volume_score = min(len(overlaps) / (min_clashes * 3.0), 1.0)
            confidence = 0.5 * spread_score + 0.5 * volume_score
            if confidence < min_confidence:
                continue

            shift_mm = math.ceil(mean * 1000.0 / 50.0) * 50.0
            affected = sorted({index for index, _ in entries})
            causes.append(
                RootCause(
                    kind="systemic_elevation",
                    title=f"Cota sistemática: {system} contra {self.profile.label(hard_discipline)}",
                    detail=(
                        f"{len(overlaps)} cruces del sistema '{system}' penetran "
                        f"{mean * 1000.0:.0f} mm en promedio contra elementos de "
                        f"{self.profile.label(hard_discipline)}, con una dispersión de solo "
                        f"{std * 1000.0:.0f} mm. Una penetración tan pareja no viene de "
                        f"{len(overlaps)} errores independientes: el recorrido completo está "
                        "a la altura equivocada."
                    ),
                    confidence=confidence,
                    affected_clusters=affected,
                    clash_count=len(entries),
                    evidence={
                        "system": system,
                        "against_discipline": hard_discipline,
                        "mean_overlap_mm": round(mean * 1000.0, 1),
                        "stddev_mm": round(std * 1000.0, 1),
                        "sample_size": len(overlaps),
                    },
                    suggested_action=(
                        f"Bajar el recorrido de '{system}' unos {shift_mm:.0f} mm y volver a "
                        f"correr el test: si la teoría es correcta, cierra {len(entries)} cruces "
                        "de una sola vez."
                    ),
                )
            )
        return causes

    # ------------------------------------------------ missing penetration

    def _missing_penetration(
        self,
        clusters: list[ClashCluster],
        all_elements: list[ElementRef],
        known_sleeves: list[ElementRef] | None = None,
    ) -> list[RootCause]:
        """MEP through a wall or slab with no sleeve modelled at the crossing.

        Caveat worth stating: this only sees elements that appear somewhere
        in the clash export. A sleeve that clashes with nothing is invisible
        here, so a hit is a strong prompt to check rather than proof.

        `known_sleeves` carries the ones the noise filter removed. Without it
        the count came only from surviving clashes, which by construction
        exclude every sleeve that was doing its job — so the detector reported
        `sleeves_found_in_model: 0` and escalated to the "not one pass is
        defined" wording at 0.9 confidence, on models where the passes existed
        and were modelled correctly.
        """
        host_categories = {
            c.strip().lower()
            for c in self.profile.root_cause_param(
                "missing_penetration", "host_categories", ["Walls", "Floors"]
            )
        }
        radius = float(
            self.profile.root_cause_param("missing_penetration", "search_radius_m", 1.0)
        )
        keywords = [
            k.lower()
            for k in self.profile.noise_param(
                "pass_through_keywords", ["sleeve", "pasamuro", "opening"]
            )
        ]

        pass_categories = {
            c.strip().lower()
            for c in self.profile.noise_param("pass_through_categories", [])
        }

        def looks_like_a_sleeve(element: ElementRef) -> bool:
            if element.category.strip().lower() in pass_categories:
                return True
            haystack = f"{element.display_name} {element.parent_name} {element.category}".lower()
            return any(keyword in haystack for keyword in keywords)

        by_path: dict[str, ElementRef] = {
            element.path_id: element for element in (known_sleeves or [])
        }
        for element in all_elements:
            if looks_like_a_sleeve(element):
                by_path.setdefault(element.path_id, element)
        sleeves = list(by_path.values())

        candidates: list[int] = []
        clash_total = 0
        samples: list[dict[str, Any]] = []
        for index, cluster in enumerate(clusters):
            for clash in cluster.clashes:
                host = _side_with_category(clash, host_categories)
                if host is None or not clash.is_hard:
                    continue
                other = clash.b if host is clash.a else clash.a
                if other.discipline in {"EST", "ARQ", ""}:
                    continue
                if not other.is_linear:
                    continue
                if _has_sleeve_near(clash.point, sleeves, radius):
                    continue
                candidates.append(index)
                clash_total += 1
                if len(samples) < 5:
                    samples.append(
                        {
                            "point": [round(c, 2) for c in clash.point],
                            "host": host.display_name or host.category,
                            "crossing": other.display_name or other.category,
                            "level": clash.level,
                        }
                    )

        if not candidates:
            return []

        affected = sorted(set(candidates))

        # No sleeves anywhere is the stronger signal, not the weaker one: it
        # means the passes are not being defined at all, so every one of these
        # crossings becomes a field decision. When sleeves do exist, a missing
        # one is an omission — real, but a narrower claim.
        if sleeves:
            confidence = 0.75
            context = (
                "El modelo sí tiene pasos definidos en otras partes, así que estos quedaron por fuera."
            )
        else:
            # Finding nothing used to RAISE the confidence to 0.9 and licence
            # the sentence "the model has not one pass defined". It cannot.
            #
            # A sleeve only reaches this export by clashing with something
            # inside one of the configured tests. On a matrix whose selection
            # sets hold structure, ducts and pipes, no sleeve was ever
            # eligible to appear — so zero is what this export would report
            # whether the project models passes beautifully or not at all.
            # That is a statement about the tests, not about the model.
            #
            # The crossings themselves are unaffected and still reported. What
            # goes is the certainty about WHY, which is now lower than the
            # case where sleeves were actually seen, not higher.
            confidence = 0.7
            context = (
                "No apareció ningún paso definido en este export — pero los tests corridos "
                "tampoco podían mostrarlo, porque ningún conjunto de selección incluye "
                "elementos de paso. Antes de darlo por hecho, confírmalo contra el modelo."
            )

        return [
            RootCause(
                kind="missing_penetration",
                title="Pasos de instalaciones sin definir",
                detail=(
                    f"{clash_total} cruces son de instalaciones atravesando muros o losas sin un "
                    f"paso definido. {context} "
                    "Lo que ocurre después es conocido: el primero que llega perfora donde le "
                    "sirve. Si es mampostería, se pierde el acabado y se repara; si es una losa o "
                    "una viga, ya no es una perforación cualquiera — un paso en viga solo se "
                    "admite dentro del tercio central y con un diámetro acotado por el peralte, y "
                    "esa aprobación no existe después de vaciado. El costo no es el hueco: es la "
                    "orden de cambio y el tiempo parado esperando el visto bueno del calculista."
                ),
                confidence=confidence,
                affected_clusters=affected,
                clash_count=clash_total,
                evidence={
                    "samples": samples,
                    "sleeves_found_in_model": len(sleeves),
                    # Named so the number can be argued with: it counts every
                    # penetration element seen anywhere in the export,
                    # including the ones the noise filter removed for doing
                    # their job.
                    "sleeves_counted_from": "todo el export, incluidos los cruces filtrados como paso por diseño",
                },
                suggested_action=(
                    "Levantar el listado de pasos y pasarlo a estructura para aprobación ANTES de "
                    "vaciar. Es un solo trámite para los "
                    f"{clash_total} cruces, contra {clash_total} decisiones improvisadas en obra."
                ),
            )
        ]

    # ------------------------------------------------- repeated typology

    def _repeated_typology(self, clusters: list[ClashCluster]) -> list[RootCause]:
        """The same crossing stacked on several floors.

        Corrected once in the type or the standard detail, it closes on every
        level; corrected level by level, it is the same decision made N times.
        """
        min_levels = int(self.profile.root_cause_param("repeated_typology", "min_levels", 3))
        tolerance = float(
            self.profile.root_cause_param("repeated_typology", "xy_tolerance_m", 0.5)
        )

        buckets: dict[tuple[str, str, int, int], list[int]] = defaultdict(list)
        levels: dict[tuple[str, str, int, int], set[str]] = defaultdict(set)
        for index, cluster in enumerate(clusters):
            centroid = cluster.centroid
            key = (
                cluster.discipline_pair[0],
                cluster.discipline_pair[1],
                int(centroid[0] / tolerance),
                int(centroid[1] / tolerance),
            )
            buckets[key].append(index)
            level = cluster.dominant("level") or f"z={centroid[2]:.1f}"
            levels[key].add(level)

        causes: list[RootCause] = []
        for key, indices in buckets.items():
            distinct_levels = levels[key]
            if len(distinct_levels) < min_levels:
                continue
            clash_total = sum(len(clusters[i].clashes) for i in indices)
            causes.append(
                RootCause(
                    kind="repeated_typology",
                    title=f"Cruce repetido en {len(distinct_levels)} niveles",
                    detail=(
                        f"El mismo cruce {self.profile.label(key[0])} × {self.profile.label(key[1])} "
                        f"aparece en la misma posición en planta en {len(distinct_levels)} niveles "
                        f"({', '.join(sorted(distinct_levels)[:6])}). Es un detalle tipo, no "
                        f"{len(indices)} problemas distintos."
                    ),
                    confidence=min(0.5 + 0.1 * len(distinct_levels), 0.95),
                    affected_clusters=sorted(indices),
                    clash_count=clash_total,
                    evidence={
                        "levels": sorted(distinct_levels),
                        "plan_position": [round(key[2] * tolerance, 1), round(key[3] * tolerance, 1)],
                    },
                    suggested_action=(
                        "Resolver el detalle una vez y propagarlo a todos los niveles; "
                        "verificar si el error viene del tipo o del grupo repetido."
                    ),
                )
            )
        return causes

    # ---------------------------------------------------- congested zones

    def _congested_zones(self, clusters: list[ClashCluster]) -> list[RootCause]:
        min_issues = int(self.profile.root_cause_param("congested_zone", "min_issues", 8))
        radius = float(self.profile.root_cause_param("congested_zone", "radius_m", 3.0))

        cells: dict[tuple[int, int, int], list[int]] = defaultdict(list)
        for index, cluster in enumerate(clusters):
            centroid = cluster.centroid
            cells[
                (
                    int(centroid[0] // radius),
                    int(centroid[1] // radius),
                    int(centroid[2] // radius),
                )
            ].append(index)

        causes: list[RootCause] = []
        for indices in _dense_peaks(cells, min_issues):
            if len(indices) < min_issues:
                continue
            disciplines: set[str] = set()
            clash_total = 0
            for i in indices:
                disciplines.update(clusters[i].discipline_pair)
                clash_total += len(clusters[i].clashes)
            centre = centroid_of([clusters[i].centroid for i in indices])
            level = clusters[indices[0]].dominant("level")
            causes.append(
                RootCause(
                    kind="congested_zone",
                    # The coordinates are part of the title on purpose: a
                    # dense floor yields several peaks, and three findings
                    # named only "congested zone on 2nd FLOOR" look like a
                    # bug rather than three different places to walk to.
                    title=(
                        f"Zona congestionada{f' en {level}' if level else ''} "
                        f"(X={centre[0]:.0f} Y={centre[1]:.0f})"
                    ),
                    detail=(
                        f"{len(indices)} problemas de {len(disciplines)} disciplinas concentrados "
                        f"en un volumen de {radius:.0f} m. Resolverlos uno por uno se deshace "
                        "solo: cada movimiento empuja al vecino."
                    ),
                    confidence=0.8,
                    affected_clusters=sorted(indices),
                    clash_count=clash_total,
                    evidence={
                        "centre": [round(c, 2) for c in centre],
                        "disciplines": sorted(d for d in disciplines if d),
                        "level": level,
                    },
                    suggested_action=(
                        "Convocar una sesión de coordinación sobre esta zona y definir el orden "
                        "de prioridad de paso entre disciplinas antes de mover nada."
                    ),
                )
            )
        return causes


# ------------------------------------------------------------- helpers


def _dense_peaks(
    cells: dict[tuple[int, int, int], list[int]], min_density: int
) -> list[list[int]]:
    """Distinct congestion peaks: the densest cells, none touching another.

    Two approaches were tried and rejected against a live 3,512-problem
    model. Reporting every cell produced several findings with identical
    titles for what is one crowded shaft. Flood-filling adjacent cells was
    far worse: in a real building nearly every dense cell touches another, so
    the fill percolated and merged 3,510 of 3,512 problems into a single
    "zone" — technically true, useless to act on.

    Greedy peak selection avoids both. The densest cell wins, its neighbours
    are suppressed so it cannot be reported twice, and the next independent
    peak is taken from what remains. The result is a short list of places a
    coordinator can actually walk to, ordered by how bad they are.
    """
    dense = sorted(
        ((cell, members) for cell, members in cells.items() if len(members) >= min_density),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )

    taken: set[tuple[int, int, int]] = set()
    peaks: list[list[int]] = []

    for cell, members in dense:
        if cell in taken:
            continue
        cx, cy, cz = cell
        # Claim the peak and its whole neighbourhood, so the same hotspot
        # cannot be reported again from an adjacent cell.
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    taken.add((cx + dx, cy + dy, cz + dz))
        peaks.append(members)

    return peaks


def _hard_and_soft(clash: Clash) -> tuple[ElementRef, ElementRef] | None:
    """The side that does not move, and the side that does."""
    for side, other in ((clash.a, clash.b), (clash.b, clash.a)):
        if side.discipline in {"EST", "ARQ"} and other.discipline not in {"EST", "ARQ"}:
            return side, other
    return None


def _system_key(clash: Clash) -> tuple[str, str] | None:
    """(system identity of the movable side, discipline of the hard side)."""
    sides = _hard_and_soft(clash)
    if sides is None:
        return None
    hard_side, soft_side = sides
    system = soft_side.prop("System Name", "System Type", "Sistema") or soft_side.parent_name
    if not system:
        return None
    return (system, hard_side.discipline)


def _is_elevation_candidate(clash: Clash) -> bool:
    """Could this crossing be explained by the run sitting at the wrong height?

    Only if the run STICKS OUT of what it crosses. A pipe wholly inside the
    host's vertical range is passing through it, and then the Z overlap this
    detector measures is the pipe's own diameter — the same number at every
    crossing, because the pipe is the same pipe. That constancy is precisely
    what the detector reads as proof of a systematic error, so a clean
    pass-through produced the most confident finding in the report.

    Measured on a live model: 67 of 67 crossings of two sprinkler branches
    had a Z overlap equal to the branch's own bounding height, and the claim
    that the run sat 44 mm too low was published at 0.88 and 0.95 confidence.
    Its suggested fix — lower the run 50 mm — could not have worked: 40 of
    the 54 things it crossed were vertical walls and columns, where moving
    down keeps you inside the wall and merely moves the hole.

    Those crossings are real and they are not free; they are penetrations
    needing a sleeve, which is what `missing_penetration` already says about
    them. What they are not is a levelling mistake.
    """
    sides = _hard_and_soft(clash)
    if sides is None:
        return False
    hard, soft = sides

    overlap = _vertical_overlap(clash)
    if overlap <= 0.0:
        return False

    soft_height = soft.bbox_max[2] - soft.bbox_min[2]
    hard_height = hard.bbox_max[2] - hard.bbox_min[2]
    if soft_height <= 0.0 or hard_height <= 0.0:
        return False

    # A tenth of slack on each side: an overlap that consumes ALL of either
    # box means that box passes clean through the other, and moving it up or
    # down carries the crossing with it.
    #
    # The test has to run both ways round because the same arithmetic hides
    # two different geometries. A horizontal branch through a wall reports
    # its own diameter; a vertical riser through a slab reports the slab's
    # thickness — 177.8 mm, seven inches, with a standard deviation of
    # exactly zero across every crossing, which the detector read as the
    # tightest evidence it had ever seen.
    if overlap >= soft_height * 0.9:
        return False
    return overlap < hard_height * 0.9


def _vertical_overlap(clash: Clash) -> float:
    """How much the two bounding boxes overlap on Z, in metres."""
    low = max(clash.a.bbox_min[2], clash.b.bbox_min[2])
    high = min(clash.a.bbox_max[2], clash.b.bbox_max[2])
    return max(high - low, 0.0)


def _side_with_category(clash: Clash, categories: set[str]) -> ElementRef | None:
    for side in (clash.a, clash.b):
        if side.category.strip().lower() in categories:
            return side
    return None


def _has_sleeve_near(point: Point, sleeves: list[ElementRef], radius: float) -> bool:
    for sleeve in sleeves:
        centre = (
            (sleeve.bbox_min[0] + sleeve.bbox_max[0]) / 2.0,
            (sleeve.bbox_min[1] + sleeve.bbox_max[1]) / 2.0,
            (sleeve.bbox_min[2] + sleeve.bbox_max[2]) / 2.0,
        )
        if math.dist(point, centre) <= radius:
            return True
    return False
