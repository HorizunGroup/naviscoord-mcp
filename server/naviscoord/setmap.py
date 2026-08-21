"""Which of the document's saved sets stand for which trade.

The clash matrix used to insist on sets it had built itself, named
``NC - EST`` and so on, and building those means walking every node of the
model to sort the geometry into buckets. On a real federation that walk hung
Navisworks — twice — for an answer the document was already holding: a team
that coordinates has search sets, and theirs were called ``STR-WALL``,
``ARCH-GB-WALL``, ``HVAC``, ``PLUM-TYP``, ``FRSP``.

So the matrix reads the sets that exist and works out what each one is for.
The reading happens here rather than in the add-in because the discipline
vocabulary lives here, is configured from the profile, and is tested; a
second copy of it in C# would drift from this one and the drift would show
up as tests quietly comparing the wrong trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .profile import Profile


@dataclass(slots=True)
class SetMapping:
    """Saved sets grouped by the trade their name declares."""

    by_discipline: dict[str, list[str]] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    #: Sets the caller built, keyed by trade. They win: somebody made them
    #: for exactly this, whatever their name happens to resemble.
    owned: dict[str, str] = field(default_factory=dict)

    def sets_for(self, code: str) -> list[str]:
        own = self.owned.get(code)
        if own:
            return [own]
        return list(self.by_discipline.get(code, ()))

    def to_json(self) -> dict[str, Any]:
        return {
            "by_discipline": {k: list(v) for k, v in sorted(self.by_discipline.items())},
            "owned": dict(sorted(self.owned.items())),
            "unmatched": list(self.unmatched),
        }


def map_sets(names: list[str], profile: Profile, prefix: str = "NC") -> SetMapping:
    """Group saved-set names by the trade each one names.

    A set called ``{prefix} - EST`` is taken at its word and nothing else in
    that trade is considered: it was built for this matrix and mixing it with
    the project's own sets would compare a trade against itself.
    """
    mapping = SetMapping()
    marker = f"{prefix} - ".lower()

    for name in names:
        text = (name or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered.startswith(marker):
            code = text[len(marker) :].strip().upper()
            if code:
                mapping.owned[code] = text
                continue
        code = profile.discipline_for_label(text)
        if code:
            mapping.by_discipline.setdefault(code, []).append(text)
        else:
            mapping.unmatched.append(text)
    return mapping


def plan_pairs(
    pairs: list[dict[str, Any]], mapping: SetMapping
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the profile's pairs into the ones we can build and the ones we cannot.

    A pair with no set on one side is not an error and not silently dropped:
    it is returned with the reason, because "we did not compare structure
    against sanitary" and "there is no sanitary in this model" are different
    facts and the coordinator needs to know which one applies.
    """
    buildable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        a, b = str(pair.get("a", "")).upper(), str(pair.get("b", "")).upper()
        if not a or not b:
            continue
        sets_a, sets_b = mapping.sets_for(a), mapping.sets_for(b)
        missing = [code for code, found in ((a, sets_a), (b, sets_b)) if not found]
        if missing:
            skipped.append(
                {
                    "a": a,
                    "b": b,
                    "reason": (
                        "no hay conjunto de selección para "
                        + " ni ".join(missing)
                        + ": esa especialidad no está en este modelo, o sus conjuntos "
                        "se llaman de una forma que el perfil no reconoce"
                    ),
                }
            )
            continue
        entry = dict(pair)
        entry.update({"a": a, "b": b, "sets_a": sets_a, "sets_b": sets_b})
        buildable.append(entry)

    return buildable, skipped
