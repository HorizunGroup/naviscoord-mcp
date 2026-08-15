"""Spatial clustering: turning thousands of clashes into countable issues.

One duct crossing a pipe rack produces a clash per pipe. One wall built of
four layers produces a clash per layer. Neither is four or thirty problems —
each is one thing a person walks up to and fixes. Collapsing them is what
makes the output small enough to reason about and honest enough to plan
with.

Two passes, in order:

1. **Pair collapse** — clashes sharing the same host-level element pair are
   the same problem regardless of distance, which absorbs multi-layer hosts
   and reinforcement bars.
2. **Density clustering** — a DBSCAN over the clash points, restricted to a
   single discipline pair, which absorbs a run crossing many parallel
   elements.

DBSCAN is implemented here over a uniform grid rather than pulled from
scipy: the neighbourhood radius is fixed, so a grid of cell size `eps` makes
each region query a scan of 27 cells, and the engine stays dependency-free.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..model import Clash, Point, bounds_of, centroid_of, distance
from ..profile import Profile

NOISE = -1


@dataclass(slots=True)
class ClashCluster:
    """A group of raw clashes that represent one coordination problem."""

    clashes: list[Clash] = field(default_factory=list)
    kind: str = "cluster"

    @property
    def points(self) -> list[Point]:
        return [c.point for c in self.clashes]

    @property
    def centroid(self) -> Point:
        return centroid_of(self.points)

    @property
    def bounds(self) -> tuple[Point, Point]:
        return bounds_of(self.points)

    @property
    def span(self) -> float:
        lo, hi = self.bounds
        return max(hi[i] - lo[i] for i in range(3)) if self.clashes else 0.0

    @property
    def max_penetration(self) -> float:
        return max((c.penetration_m for c in self.clashes), default=0.0)

    @property
    def discipline_pair(self) -> tuple[str, str]:
        """Canonically SORTED, for keying and display. Not side information."""
        return self.clashes[0].discipline_pair if self.clashes else ("", "")

    @property
    def side_disciplines(self) -> tuple[str, str]:
        """(discipline of side A, discipline of side B), in Navisworks order.

        Kept apart from `discipline_pair` on purpose: that one is sorted, so
        reading a side out of it is a coin flip. The representative clash is
        the same one the scorer used, so `elements_a` and this agree.
        """
        if not self.clashes:
            return ("", "")
        worst = max(self.clashes, key=lambda c: (c.penetration_m, c.is_hard))
        return (worst.a.discipline, worst.b.discipline)

    def elements_by_discipline(self) -> dict[str, list[str]]:
        """Every element in the cluster, grouped by its own discipline tag.

        Built from both sides of every clash rather than from side A or B,
        because a cluster can hold clashes whose Navisworks order differs —
        the same beam is Item1 in one result and Item2 in the next — so even
        an honest per-side split is not a split by trade.
        """
        grouped: dict[str, set[str]] = defaultdict(set)
        for clash in self.clashes:
            for side in (clash.a, clash.b):
                if side.discipline:
                    grouped[side.discipline].add(side.path_id)
        return {code: sorted(paths) for code, paths in grouped.items()}

    def dominant(self, attribute: str) -> str:
        """Most common non-empty value of a clash attribute, e.g. level."""
        counts: dict[str, int] = defaultdict(int)
        for clash in self.clashes:
            value = getattr(clash, attribute, "") or ""
            if value:
                counts[value] += 1
        if not counts:
            return ""
        return max(counts.items(), key=lambda kv: kv[1])[0]


def build_clusters(clashes: list[Clash], profile: Profile) -> list[ClashCluster]:
    """Full collapse pipeline. Input order does not affect the result."""
    if not clashes:
        return []

    eps = float(profile.cluster_param("eps_m", 0.4))
    min_samples = int(profile.cluster_param("min_samples", 1))
    split_by_pair = bool(profile.cluster_param("same_discipline_pair_only", True))
    max_span = float(profile.cluster_param("max_cluster_span_m", 6.0))

    groups = _collapse_by_pair(clashes)

    clusters: list[ClashCluster] = []
    buckets: dict[tuple[str, str] | None, list[Clash]] = defaultdict(list)
    for group in groups:
        if len(group) > 1:
            # Already one problem by identity: an element pair cannot be two
            # separate issues just because the contact is spread out.
            clusters.append(ClashCluster(clashes=group, kind="pair"))
            continue
        clash = group[0]
        key = clash.discipline_pair if split_by_pair else None
        buckets[key].append(clash)

    for bucket in buckets.values():
        for labelled in _dbscan(bucket, eps, min_samples):
            cluster = ClashCluster(clashes=labelled, kind="cluster" if len(labelled) > 1 else "pair")
            if cluster.span > max_span and len(labelled) > 1:
                # A chain of near-touching points can drift across a whole
                # floor. Splitting keeps an "issue" something you can stand
                # in front of.
                clusters.extend(_split_oversized(cluster, max_span))
            else:
                clusters.append(cluster)

    clusters.sort(key=lambda c: (-len(c.clashes), c.centroid))
    return clusters


def _collapse_by_pair(clashes: list[Clash]) -> list[list[Clash]]:
    grouped: dict[tuple[str, str], list[Clash]] = defaultdict(list)
    for clash in clashes:
        grouped[clash.pair_key].append(clash)
    return list(grouped.values())


def _split_oversized(cluster: ClashCluster, max_span: float) -> list[ClashCluster]:
    """Re-cluster a sprawling group with a tighter radius until it fits."""
    eps = max_span / 3.0
    for _ in range(4):
        parts = _dbscan(cluster.clashes, eps, 1)
        if all(ClashCluster(clashes=p).span <= max_span for p in parts) or len(parts) == 1:
            return [ClashCluster(clashes=p, kind="cluster" if len(p) > 1 else "pair") for p in parts]
        eps /= 2.0
    return [ClashCluster(clashes=[c], kind="pair") for c in cluster.clashes]


# --------------------------------------------------------------- DBSCAN


class _Grid:
    """Uniform spatial hash with cell size `eps`.

    Any point within `eps` of a query point must fall in the query cell or
    one of its 26 neighbours, so a full pairwise scan is never needed.
    """

    def __init__(self, points: list[Point], eps: float) -> None:
        self.eps = eps
        self.cells: dict[tuple[int, int, int], list[int]] = defaultdict(list)
        for index, point in enumerate(points):
            self.cells[self._cell(point)].append(index)

    def _cell(self, point: Point) -> tuple[int, int, int]:
        return (
            int(point[0] // self.eps),
            int(point[1] // self.eps),
            int(point[2] // self.eps),
        )

    def neighbours(self, points: list[Point], index: int) -> list[int]:
        origin = points[index]
        cx, cy, cz = self._cell(origin)
        found: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for candidate in self.cells.get((cx + dx, cy + dy, cz + dz), ()):
                        if distance(origin, points[candidate]) <= self.eps:
                            found.append(candidate)
        return found


def _dbscan(clashes: list[Clash], eps: float, min_samples: int) -> list[list[Clash]]:
    """Density clustering. Points that reach min_samples form a cluster;
    the rest are returned as singletons rather than thrown away."""
    if not clashes:
        return []
    if len(clashes) == 1:
        return [list(clashes)]

    points = [c.point for c in clashes]
    grid = _Grid(points, eps)
    labels = [None] * len(points)  # type: list[int | None]
    cluster_id = 0

    for index in range(len(points)):
        if labels[index] is not None:
            continue
        neighbours = grid.neighbours(points, index)
        if len(neighbours) < min_samples:
            labels[index] = NOISE
            continue

        labels[index] = cluster_id
        queue = [n for n in neighbours if n != index]
        seen = set(neighbours)
        while queue:
            current = queue.pop()
            if labels[current] == NOISE:
                labels[current] = cluster_id
                continue
            if labels[current] is not None:
                continue
            labels[current] = cluster_id
            expansion = grid.neighbours(points, current)
            if len(expansion) >= min_samples:
                for candidate in expansion:
                    if candidate not in seen:
                        seen.add(candidate)
                        queue.append(candidate)
        cluster_id += 1

    grouped: dict[int, list[Clash]] = defaultdict(list)
    singletons: list[list[Clash]] = []
    for index, label in enumerate(labels):
        if label is None or label == NOISE:
            singletons.append([clashes[index]])
        else:
            grouped[label].append(clashes[index])
    return list(grouped.values()) + singletons
