"""Core data contract shared by the addin exporter and the analysis engine.

Every length in this module is in METRES. The addin normalises Navisworks
internal units at extraction time so nothing downstream has to care.

Sign convention for `Clash.distance_m` follows the Navisworks API:
negative means the two solids interpenetrate, and the magnitude is the
penetration depth. Positive means a clearance test found the pair closer
than the tolerance but not touching.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

SCHEMA = "naviscoord.clashexport/1"

Point = tuple[float, float, float]


def _as_point(raw: Any) -> Point:
    x, y, z = raw
    return (float(x), float(y), float(z))


def _level_from_sides(a: "ElementRef", b: "ElementRef") -> str:
    """Last resort for the storey when no Level property was published.

    Plenty of models keep the storey in the Navisworks Layer instead — a
    Spanish project carried it as ORG_NIVEL_01..07 — and without it every
    issue lands in "sin nivel", which then propagates into work package
    titles like "Estructura × Hidráulica en sin nivel". Useless for zoning a
    plan, and it reads like a bug.
    """
    for side in (a, b):
        # `Reference Level` is where Revit puts the storey for anything hosted
        # on one, which is most of MEP. The add-in already reads it when it
        # fills the clash's own `level`, so this line changes nothing on a
        # normal export — it is here for the case where that lookup came back
        # empty and the property survived in `props` anyway. Measured on the
        # export that prompted it: recovered zero, which is the honest note to
        # leave rather than a comment claiming a fix it did not make.
        value = side.prop("Layer", "Capa", "Nivel", "Level", "Reference Level")
        cleaned = clean_level(value)
        # A layer that is not a storey is worse than no storey at all: it
        # would split the plan into meaningless zones.
        if cleaned and cleaned.lower() not in {"<sin nivel>", "sin nivel", "<no level>", "no level"}:
            return cleaned
    return ""


def clean_level(raw: str) -> str:
    """Reduces `Level "2th FLOOR", #9946` to `2th FLOOR`.

    Navisworks publishes the level as a formatted reference including the
    element id. Left raw it becomes the label on every report row and chart
    axis, and the id makes two mentions of the same floor look different.
    """
    if not raw:
        return ""
    text = raw.strip()
    if "," in text:
        head, _, tail = text.rpartition(",")
        if tail.strip().startswith("#"):
            text = head.strip()
    # Strip the "Level " prefix only when a usable name survives it. On
    # `Level "2th FLOOR", #9946` it leaves `2th FLOOR`; on plain `Level 1` it
    # would leave a bare `1`, which is a worse label than the original and
    # collides with every other model's floor numbering.
    if text.lower().startswith("level "):
        remainder = text[6:].strip().strip('"').strip()
        if remainder and not remainder.isdigit():
            text = remainder
    return text.strip().strip('"').strip()


def distance(a: Point, b: Point) -> float:
    return math.dist(a, b)


@dataclass(frozen=True, slots=True)
class ModelSource:
    """One file in the federated model."""

    index: int
    source_file: str
    source_path: str = ""
    item_count: int = 0

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "ModelSource":
        return cls(
            index=int(raw["index"]),
            source_file=raw.get("source_file", ""),
            source_path=raw.get("source_path", ""),
            item_count=int(raw.get("item_count", 0)),
        )


@dataclass(slots=True)
class ElementRef:
    """One side of a clash.

    `path_id` is the Navisworks ModelItem path and is what write-back uses to
    re-select the element, so it must survive the round trip untouched.
    """

    path_id: str
    model_index: int = -1
    display_name: str = ""
    category: str = ""
    parent_path_id: str = ""
    parent_name: str = ""
    source_file: str = ""
    bbox_min: Point = (0.0, 0.0, 0.0)
    bbox_max: Point = (0.0, 0.0, 0.0)
    props: dict[str, str] = field(default_factory=dict)

    # Filled in by the discipline tagger.
    discipline: str = ""

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "ElementRef":
        return cls(
            path_id=str(raw["path_id"]),
            model_index=int(raw.get("model_index", -1)),
            display_name=raw.get("display_name", ""),
            category=raw.get("category", ""),
            parent_path_id=raw.get("parent_path_id", ""),
            parent_name=raw.get("parent_name", ""),
            source_file=raw.get("source_file", ""),
            bbox_min=_as_point(raw.get("bbox_min", (0, 0, 0))),
            bbox_max=_as_point(raw.get("bbox_max", (0, 0, 0))),
            props={str(k): str(v) for k, v in (raw.get("props") or {}).items()},
        )

    @property
    def extent(self) -> Point:
        return (
            self.bbox_max[0] - self.bbox_min[0],
            self.bbox_max[1] - self.bbox_min[1],
            self.bbox_max[2] - self.bbox_min[2],
        )

    @property
    def volume(self) -> float:
        dx, dy, dz = self.extent
        return max(dx, 0.0) * max(dy, 0.0) * max(dz, 0.0)

    @property
    def nominal_size(self) -> float:
        """Smallest cross-section dimension, used to normalise penetration.

        A 30 mm bite into a 1200 mm duct is cosmetic; the same bite into a
        50 mm sprinkler branch severs it. Scoring needs that ratio, not the
        raw millimetres.
        """
        dims = sorted(d for d in self.extent if d > 0.0)
        return dims[0] if dims else 0.0

    @property
    def is_linear(self) -> bool:
        """True for runs (pipe, duct, beam) as opposed to blocky elements."""
        dims = sorted((d for d in self.extent if d > 0.0), reverse=True)
        if len(dims) < 2 or dims[1] <= 0.0:
            return False
        return dims[0] / dims[1] >= 4.0

    @property
    def is_vertical(self) -> bool:
        dx, dy, dz = self.extent
        return dz > 0.0 and dz >= 2.0 * max(dx, dy)

    def prop(self, *names: str, default: str = "") -> str:
        """First non-empty property among `names`, matched case-insensitively."""
        lowered = {k.lower(): v for k, v in self.props.items()}
        for name in names:
            value = lowered.get(name.lower(), "")
            if value:
                return value
        return default

    @property
    def group_key(self) -> str:
        """Identity used to collapse sub-elements onto their host.

        A four-layer wall reports one clash per layer and rebar reports one
        per bar; both are a single coordination problem. Preferring the
        parent path is what turns that noise back into one issue.
        """
        return self.parent_path_id or self.path_id


@dataclass(slots=True)
class Clash:
    """A single raw Navisworks clash result."""

    guid: str
    test: str
    name: str
    a: ElementRef
    b: ElementRef
    status: str = "New"
    distance_m: float = 0.0
    point: Point = (0.0, 0.0, 0.0)
    grid: str = ""
    level: str = ""
    test_type: str = "Hard"

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Clash":
        a = ElementRef.from_json(raw["a"])
        b = ElementRef.from_json(raw["b"])
        return cls(
            guid=str(raw["guid"]),
            test=raw.get("test", ""),
            name=raw.get("name", ""),
            a=a,
            b=b,
            status=raw.get("status", "New"),
            distance_m=float(raw.get("distance_m", 0.0)),
            point=_as_point(raw.get("point", (0, 0, 0))),
            grid=raw.get("grid", ""),
            level=clean_level(raw.get("level", "")) or _level_from_sides(a, b),
            test_type=raw.get("test_type", "Hard"),
        )

    @property
    def penetration_m(self) -> float:
        """Penetration depth as a positive number; 0.0 for clearance hits."""
        return -self.distance_m if self.distance_m < 0.0 else 0.0

    @property
    def is_hard(self) -> bool:
        return self.distance_m < 0.0

    @property
    def discipline_pair(self) -> tuple[str, str]:
        """Disciplines as an unordered, canonically sorted pair."""
        return tuple(sorted((self.a.discipline, self.b.discipline)))  # type: ignore[return-value]

    @property
    def pair_key(self) -> tuple[str, str]:
        """Host-level identity of the colliding pair, order-independent."""
        return tuple(sorted((self.a.group_key, self.b.group_key)))  # type: ignore[return-value]

    def side_for(self, discipline: str) -> ElementRef | None:
        if self.a.discipline == discipline:
            return self.a
        if self.b.discipline == discipline:
            return self.b
        return None


@dataclass(slots=True)
class ClashExport:
    """The full payload the addin hands to the analysis engine."""

    document_title: str = ""
    document_path: str = ""
    models: list[ModelSource] = field(default_factory=list)
    clashes: list[Clash] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "ClashExport":
        schema = raw.get("schema", "")
        if schema and schema != SCHEMA:
            raise ValueError(f"unsupported export schema {schema!r}, expected {SCHEMA!r}")
        document = raw.get("document") or {}
        return cls(
            document_title=document.get("title", ""),
            document_path=document.get("path", ""),
            models=[ModelSource.from_json(m) for m in raw.get("models", [])],
            clashes=[Clash.from_json(c) for c in raw.get("clashes", [])],
            tests=list(raw.get("tests", [])),
        )

    def model_for(self, index: int) -> ModelSource | None:
        for model in self.models:
            if model.index == index:
                return model
        return None


@dataclass(slots=True)
class Issue:
    """A deduplicated, scored coordination problem.

    This is the unit a human actually works on, and the only thing the LLM
    ever sees in bulk. `why` carries the plain-language justification for the
    score so the ranking can be argued with rather than trusted blindly.
    """

    issue_id: str
    kind: str  # "pair" | "cluster" | "systemic"
    discipline_pair: tuple[str, str]
    clash_ids: list[str]
    centroid: Point
    bbox_min: Point
    bbox_max: Point
    #: The cluster this issue was built from, carried on the issue instead of
    #: implied by its place in a list.
    #:
    #: Root causes are detected against clusters and can only name them by
    #: position, while issues are ranked by severity and take their public ids
    #: from that ranking. Holding the cluster number here means the two are
    #: matched by looking something up, rather than by both lists happening to
    #: be in the same order at the same moment — which they only were for the
    #: few lines between building the issues and sorting them, and which
    #: nothing enforced.
    cluster_id: int = -1
    severity: float = 0.0
    priority: str = "low"
    level: str = ""
    grid: str = ""
    max_penetration_m: float = 0.0
    responsible: str = ""
    immovable_side: str = ""
    root_cause: dict[str, Any] = field(default_factory=dict)
    why: list[str] = field(default_factory=list)
    suggested_action: str = ""
    score_breakdown: dict[str, float] = field(default_factory=dict)
    elements_a: list[str] = field(default_factory=list)
    elements_b: list[str] = field(default_factory=list)
    # The discipline actually on each side, kept because `discipline_pair` is
    # SORTED and therefore says nothing about which side is which. Colouring
    # "the side that must move" used to infer the side by comparing
    # `responsible` against the sorted pair's last entry, so on any issue
    # where the responsible discipline sorted first the wrong elements were
    # painted — the immovable structure lit up red and the pipe that had to
    # move stayed grey.
    discipline_a: str = ""
    discipline_b: str = ""
    # The clash to photograph. `clash_ids[0]` is whichever member the cluster
    # happened to collect first; `discipline_a`/`discipline_b` describe the
    # DEEPEST one. Rendering the first and labelling it with the deepest one's
    # trades put the wrong colour under the wrong pipe on any cluster whose
    # members ordered differently — a caption that is confidently wrong.
    representative_clash_id: str = ""
    # path ids grouped by the discipline of the element itself. This is the
    # only reliable answer to "which elements belong to the side that must
    # move": a cluster can hold clashes whose Navisworks A/B order differs,
    # so even the honest side lists are not a clean split by trade.
    elements_by_discipline: dict[str, list[str]] = field(default_factory=dict)

    # Set when several issues resolve with one decision. The representative
    # carries `folds`; the rest point back at it and drop out of the ranking
    # so the top of the list stays a list of distinct decisions.
    folded_into: str = ""
    folds: int = 0

    @property
    def clash_count(self) -> int:
        return len(self.clash_ids)

    def elements_of(self, discipline: str) -> list[str]:
        """Path ids belonging to one trade, by the element's own tag.

        Falls back to the recorded side disciplines for an Issue built before
        the per-discipline index existed, and returns nothing rather than
        guessing when neither can answer.
        """
        if not discipline:
            return []
        found = self.elements_by_discipline.get(discipline)
        if found:
            return list(found)
        if discipline == self.discipline_a != self.discipline_b:
            return list(self.elements_a)
        if discipline == self.discipline_b != self.discipline_a:
            return list(self.elements_b)
        return []

    def movable_elements(self) -> list[str]:
        """The path ids of the side that is expected to move.

        Resolved from the element's OWN discipline, never by comparing
        `responsible` against a sorted pair. That inference was wrong roughly
        half the time — on every issue whose responsible trade sorted first,
        the immovable structure lit up red and the pipe that had to move
        stayed grey — and it looked plausible in every screenshot.

        When the responsible trade cannot be located (both sides are the same
        discipline, or an override retagged them after the issue was built),
        everything involved is returned. Colouring the whole interference is
        honest; colouring the wrong half is not.
        """
        found = self.elements_of(self.responsible)
        if found:
            return found
        return list(self.elements_a) + list(self.elements_b)

    def immovable_elements(self) -> list[str]:
        """The side that stays put. Empty when it cannot be identified."""
        return self.elements_of(self.immovable_side)

    def image_clash_id(self) -> str:
        """The clash to photograph for this issue.

        Falls back to the first member for an Issue built before the
        representative was recorded, so an older cached analysis still gets a
        picture instead of no picture.
        """
        if self.representative_clash_id:
            return self.representative_clash_id
        return self.clash_ids[0] if self.clash_ids else ""

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "issue_id": self.issue_id,
            "kind": self.kind,
            "discipline_pair": list(self.discipline_pair),
            # Reported separately from the pair because the pair is sorted:
            # a consumer that needs to know which side is which cannot get it
            # from `discipline_pair` and must not try.
            "side_a_discipline": self.discipline_a,
            "side_b_discipline": self.discipline_b,
            "representative_clash": self.image_clash_id(),
            "clash_count": self.clash_count,
            "severity": round(self.severity, 1),
            "priority": self.priority,
            "level": self.level,
            "grid": self.grid,
            "centroid": [round(c, 3) for c in self.centroid],
            "max_penetration_mm": round(self.max_penetration_m * 1000.0, 1),
            "responsible": self.responsible,
            "immovable_side": self.immovable_side,
            "root_cause": self.root_cause,
            "why": self.why,
            "suggested_action": self.suggested_action,
            "score_breakdown": {k: round(v, 2) for k, v in self.score_breakdown.items()},
        }
        if self.folds:
            payload["also_resolves"] = self.folds
        if self.folded_into:
            payload["folded_into"] = self.folded_into
        return payload


def bounds_of(points: Iterable[Point]) -> tuple[Point, Point]:
    pts = list(points)
    if not pts:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return (
        (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts)),
        (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts)),
    )


def centroid_of(points: Iterable[Point]) -> Point:
    pts = list(points)
    if not pts:
        return (0.0, 0.0, 0.0)
    n = float(len(pts))
    return (
        sum(p[0] for p in pts) / n,
        sum(p[1] for p in pts) / n,
        sum(p[2] for p in pts) / n,
    )
