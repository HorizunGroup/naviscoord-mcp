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


def _storey_rhythm(by_label: dict[str, list[float]], tolerance_m: float) -> float:
    """Floor-to-floor height, read off the gaps between the labels themselves.

    Every project has its own. Asking the model rather than assuming 3 m is
    what lets the same rule work on a car park with 2.4 m storeys and on a
    plant room with 6 m ones.
    """
    medians = sorted(statistics.median(zs) for zs in by_label.values())
    gaps = [b - a for a, b in zip(medians, medians[1:]) if b - a > tolerance_m]
    return statistics.median(gaps) if gaps else 3.0


def _spread(values: list[float]) -> float:
    """Height covered by the middle nine tenths of a label's elements."""
    ordered = sorted(values)
    if len(ordered) < 3:
        return ordered[-1] - ordered[0]
    low = ordered[int(len(ordered) * 0.10)]
    high = ordered[min(int(len(ordered) * 0.90), len(ordered) - 1)]
    return high - low


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
    #: Crossings the map looked at. Held explicitly because the per-storey
    #: counts are not a denominator: a crossing that matched no storey at all
    #: appears in neither, so deriving the total from them put the inferred
    #: share at 135%.
    considered: int = 0
    #: label -> how many metres of building it spans. A name that covers more
    #: than a storey height is a grouping, not a floor, and merging it into
    #: one by its median moved 133 crossings onto a floor most of them were
    #: nowhere near.
    cross_cutting: dict[str, float] = field(default_factory=dict)

    @property
    def merges(self) -> list[Storey]:
        return [s for s in self.storeys if s.aliases]

    def to_json(self) -> dict[str, Any]:
        return {
            "storeys": [s.to_json() for s in self.storeys],
            "merged_names": {s.canonical: s.aliases for s in self.merges},
            "assigned_by_elevation": self.assigned_by_elevation,
            "assigned_by_elevation_share": round(self.inferred_share, 3),
            "levels_are_inferred": self.inferred_share >= 0.5,
            "cross_cutting_labels": dict(self.cross_cutting),
        }

    @property
    def labelled(self) -> int:
        """Crossings that arrived carrying a storey of their own."""
        return max(self.considered - self.assigned_by_elevation, 0)

    @property
    def inferred_share(self) -> float:
        return self.assigned_by_elevation / self.considered if self.considered else 0.0

    def notes(self) -> list[str]:
        out: list[str] = []
        for label, span in self.cross_cutting.items():
            out.append(
                f"«{label}» abarca {span:.1f} m de edificio: no es un piso, es una "
                "etiqueta que cruza varias plantas. No se fusiona con ninguna; sus "
                "cruces se ubican uno a uno por su propia cota."
            )
        for storey in self.merges:
            out.append(
                f"«{storey.canonical}» y {', '.join(f'«{a}»' for a in storey.aliases)} "
                f"están a la misma cota ({storey.elevation:.1f} m): son el mismo piso "
                "nombrado distinto en cada modelo."
            )
        if self.assigned_by_elevation:
            share = self.inferred_share
            # A count on its own reads like an aside. It is not: the storey of
            # a crossing is what every per-level total, every hotspot label
            # and the "repeated on N floors" root cause are built from, and
            # the elevations those crossings are matched against are
            # themselves the median height of the crossings that DID carry a
            # storey — a guess measured against a guess.
            #
            # It is also fixable at the source rather than endured, which is
            # why the note says where to look: the storey rides in the
            # `Layer` property, and an export taken without it loses the lot.
            if share >= 0.5:
                out.append(
                    f"{self.assigned_by_elevation} cruces ({share:.0%}) no traían nivel y se "
                    "les asignó el piso más cercano por cota. Con esa proporción el mapa de "
                    "pisos es una inferencia, no un dato: los conteos por nivel, las zonas "
                    "calientes y las causas de «repetido en N niveles» heredan el error. "
                    "Revisa que el export incluya la propiedad «Layer» antes de leerlos."
                )
            else:
                out.append(
                    f"{self.assigned_by_elevation} cruces ({share:.0%}) no traían nivel y se "
                    "asignaron por su cota al piso más cercano."
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

    # A label is only a storey if its elements are actually at one height.
    #
    # Everything below reasons about labels through their median, and a median
    # is a fine summary of a floor and a meaningless one for a label that is
    # not a floor. On a live model `ARC_2ND` carried 133 crossings spread from
    # 4.7 m to 24.6 m — twenty metres, the whole building — because it names an
    # architectural grouping, not a storey. Its median landed at 15.5 m, within
    # centimetres of `5 FLOOR`, so the two merged and 133 crossings scattered
    # through the tower were reported as happening on the fifth floor.
    #
    # Such a label is set aside rather than merged: its crossings then get
    # placed one by one against their own height, which is what the fallback
    # for an unlabelled crossing already does and is far closer to the truth
    # than inheriting somebody else's floor.
    # Measured between the tenth and ninetieth percentile, not end to end. A
    # real storey collects the odd stray — one crossing on `8 FLOOR` sat 13 m
    # below the rest — and min-to-max lets that single element disqualify the
    # floor. The middle nine tenths ignores it and still spots a label whose
    # elements genuinely run the height of the building.
    # The limit comes from the building's own rhythm, not from a constant. A
    # storey legitimately spans a whole floor height — a crossing on it can be
    # at the slab or up against the ceiling — so anything under about one
    # storey is a floor behaving normally. Measured here: the real floors came
    # out at 3.0 m and the two cross-cutting labels at 17.5 and 20.8, six
    # storeys apiece. A fixed threshold either keeps those or throws away
    # every real floor with them, which is what a first attempt at 3.0 m did.
    floor_height = _storey_rhythm(by_label, tolerance_m)
    spread_limit = max(floor_height * 2.5, tolerance_m * 2.0)
    cross_cutting = {
        label: _spread(zs)
        for label, zs in by_label.items()
        if len(zs) > 1 and _spread(zs) > spread_limit
    }
    storey_labels = {k: v for k, v in by_label.items() if k not in cross_cutting}
    if not storey_labels:
        return LevelMap(considered=len(clashes))

    # Median rather than mean: a single stray element at roof height would
    # drag a whole storey's average with it.
    ranked = sorted(
        ((label, statistics.median(zs), len(zs)) for label, zs in storey_labels.items()),
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

    result = LevelMap(
        storeys=storeys,
        mapping=mapping,
        considered=len(clashes),
        cross_cutting={k: round(v, 1) for k, v in sorted(cross_cutting.items())},
    )

    for clash in clashes:
        if clash.level and clash.level in cross_cutting:
            # Not a storey. Drop the label so the elevation pass below places
            # this crossing on its own merits.
            clash.level = ""
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
