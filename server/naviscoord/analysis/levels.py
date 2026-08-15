"""Reconciling storey names across models that do not agree on them.

A federation almost never agrees on level naming. One real project carried
`N1`, `ORG_NIVEL_01` and `CUB` for storeys that overlap in space, because
each discipline model was set up by a different person. Passed through
untouched, the same floor becomes two or three zones in the work plan, the
team gets convened twice for one place, and the counts per level are wrong.

Matching the strings is the obvious approach and the wrong one: it needs a
list of prefixes per office, ordinal words per language, and it still fails
on `CUB` versus `CUBIERTA` versus `ROOF`. What does not need any of that is
the geometry. Two labels whose elements occupy the same band of elevation
are the same floor, whatever they are called and in whatever language.

Nothing is merged silently — the mapping is reported so a coordinator can
see that `ORG_NIVEL_01` was folded into `N1` and say otherwise.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..model import Clash

# Storeys are ~3 m apart, so labels whose elevations sit within this are the
# same floor described twice. Kept well under a floor height: merging two
# real storeys would be a worse error than leaving two names unmerged.
DEFAULT_TOLERANCE_M = 1.5


@dataclass(slots=True)
class Storey:
    canonical: str
    aliases: list[str] = field(default_factory=list)
    elevation: float = 0.0
    clashes: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "level": self.canonical,
            "aliases": self.aliases,
            "elevation_m": round(self.elevation, 2),
            "clashes": self.clashes,
        }


@dataclass(slots=True)
class LevelMap:
    storeys: list[Storey] = field(default_factory=list)
    mapping: dict[str, str] = field(default_factory=dict)
    assigned_by_elevation: int = 0

    @property
    def merges(self) -> list[Storey]:
        return [s for s in self.storeys if s.aliases]

    def to_json(self) -> dict[str, Any]:
        return {
            "storeys": [s.to_json() for s in self.storeys],
            "merged_names": {s.canonical: s.aliases for s in self.merges},
            "assigned_by_elevation": self.assigned_by_elevation,
        }

    def notes(self) -> list[str]:
        out: list[str] = []
        for storey in self.merges:
            out.append(
                f"«{storey.canonical}» y {', '.join(f'«{a}»' for a in storey.aliases)} "
                f"están a la misma cota ({storey.elevation:.1f} m): son el mismo piso "
                "nombrado distinto en cada modelo."
            )
        if self.assigned_by_elevation:
            out.append(
                f"{self.assigned_by_elevation} cruces no traían nivel y se asignaron por "
                "su cota al piso más cercano."
            )
        return out


def normalise_levels(
    clashes: list[Clash], tolerance_m: float = DEFAULT_TOLERANCE_M
) -> LevelMap:
    """Merges level labels by elevation and applies the result in place."""
    by_label: dict[str, list[float]] = defaultdict(list)
    for clash in clashes:
        if clash.level:
            by_label[clash.level].append(clash.point[2])

    if not by_label:
        return LevelMap()

    # Median rather than mean: a single stray element at roof height would
    # drag a whole storey's average with it.
    ranked = sorted(
        ((label, statistics.median(zs), len(zs)) for label, zs in by_label.items()),
        key=lambda item: item[1],
    )

    storeys: list[Storey] = []
    bucket: list[tuple[str, float, int]] = []

    def flush() -> None:
        if not bucket:
            return
        # The label carrying the most clashes wins the name: it is the one
        # the team will recognise.
        winner = max(bucket, key=lambda item: item[2])
        storeys.append(
            Storey(
                canonical=winner[0],
                aliases=sorted(label for label, _, _ in bucket if label != winner[0]),
                elevation=statistics.median([z for _, z, _ in bucket]),
                clashes=sum(count for _, _, count in bucket),
            )
        )

    for entry in ranked:
        # Compared against the FIRST label in the bucket, not the last.
        # Chaining off the last one lets a group creep: five labels each
        # within tolerance of its neighbour spanned two real storeys on a
        # live model and merged N2 with N3 and N4. Single-linkage percolation,
        # the same failure as merging adjacent congested cells.
        if bucket and entry[1] - bucket[0][1] > tolerance_m:
            flush()
            bucket = []
        bucket.append(entry)
    flush()

    mapping: dict[str, str] = {}
    for storey in storeys:
        mapping[storey.canonical] = storey.canonical
        for alias in storey.aliases:
            mapping[alias] = storey.canonical

    result = LevelMap(storeys=storeys, mapping=mapping)

    for clash in clashes:
        if clash.level:
            clash.level = mapping.get(clash.level, clash.level)
        elif storeys:
            # A clash with no level still happened somewhere. Placing it by
            # elevation keeps it in the plan instead of pooling every
            # unlabelled crossing into one meaningless "sin nivel" package.
            nearest = min(storeys, key=lambda s: abs(s.elevation - clash.point[2]))
            if abs(nearest.elevation - clash.point[2]) <= tolerance_m * 2:
                clash.level = nearest.canonical
                result.assigned_by_elevation += 1

    return result
