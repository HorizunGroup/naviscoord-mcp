"""What the coordinator declared each side of a clash test to be.

This exists because the strongest evidence in the export was being ignored.

Discipline was resolved from the Revit category of each element, on the
reasoning that a category comes from the authoring tool and is therefore
measured rather than guessed. That holds right up until two trades share a
category — and the most expensive pair in any building does. A structural
wall and an architectural wall are both ``Walls``. Tagged from category alone
they land in the same discipline, and every downstream rule that asks "are
these two the same trade?" answers yes.

The consequence was not a slightly worse ranking. ``architectural_self_overlap``
uses same-discipline as its safety catch — the rule is meant to drop a
partition grazing the ceiling hung beneath it, and it checks both sides are
architecture so it cannot eat anything else. With structural walls tagged as
architecture, a whole structure-versus-architecture test was discarded as
architecture overlapping itself. On the model this was found on, that was 477
clashes: every one of them gone, silently, and the run still reported a clean
result for that pair.

A clash test named ``STR-WAL VS ARCH-GB-WALL`` is a person stating that side A
is structure and side B is architecture. The sets behind those two sides say
it more precisely still. That statement outranks a category lookup, because
somebody configured it on purpose for this exact comparison.

Two sources, in order of trust:

* the names of the saved sets on each side, which the add-in resolves from the
  test itself — exact, and immune to how the test was titled;
* the test name split on its ``VS`` separator, which is all that is available
  from an add-in older than this feature, or from a test built on an ad-hoc
  selection with no saved set behind it.

Neither is guaranteed to resolve. A side that names no recognisable trade
yields nothing and the caller falls back to the category chain, which is the
previous behaviour — this narrows the gap, it does not replace the ladder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..profile import Profile

#: Separators a coordinator writes between the two sides of a test name.
#: Matched as a whole token so that the ``x`` in ``BOX`` is not a separator
#: and the ``vs`` in ``HVAC-VSD`` is not one either.
_SEPARATOR = re.compile(r"(?:^|[\s_\-])(?:vs?|versus|x|×|contra)(?:[\s_\-]|$)", re.IGNORECASE)

SELECTION_SETS = "selection_sets"
TEST_NAME = "test_name"


@dataclass(slots=True)
class TestDeclaration:
    """The trades a single clash test says its two sides are."""

    test: str
    side_a: str = ""
    side_b: str = ""
    basis: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.side_a or self.side_b)

    @property
    def separates(self) -> bool:
        """True when the test declares two DIFFERENT trades.

        This is the case worth acting on. A test whose sides name the same
        trade adds nothing a category lookup would not already say, and if it
        is wrong it is wrong on both sides at once.
        """
        return bool(self.side_a and self.side_b and self.side_a != self.side_b)

    def for_side(self, side: str) -> str:
        return self.side_a if side == "a" else self.side_b

    def to_json(self) -> dict[str, object]:
        return {
            "test": self.test,
            "side_a": self.side_a,
            "side_b": self.side_b,
            "basis": self.basis,
            "separates": self.separates,
        }


def _from_sets(names: Any, profile: Profile) -> str:
    """Resolve one side from the saved sets behind it.

    A side can carry several sets. They are matched together rather than
    voted on: ``["STR-WAL", "STR-COL"]`` is one trade stated twice, and
    joining them lets the longest pattern win the same way it does for a
    filename.
    """
    if not isinstance(names, (list, tuple)):
        return ""
    text = " ".join(str(n) for n in names if str(n).strip())
    return profile.discipline_for_label(text) or ""


def _split_test_name(name: str) -> tuple[str, str] | None:
    """Split ``A VS B`` into its two halves, or None when there is no split."""
    if not name:
        return None
    matches = list(_SEPARATOR.finditer(name))
    if len(matches) != 1:
        # No separator, or several: "A VS B VS C" is not two sides and
        # guessing which boundary is the real one would be inventing data.
        return None
    match = matches[0]
    left = name[: match.start()].strip()
    right = name[match.end() :].strip()
    if not left or not right:
        return None
    return left, right


def declaration_for(entry: dict[str, Any], profile: Profile) -> TestDeclaration:
    """Read one test entry from the export."""
    name = str(entry.get("name", "") or "")
    decl = TestDeclaration(test=name)

    side_a = _from_sets(entry.get("selection_a"), profile)
    side_b = _from_sets(entry.get("selection_b"), profile)
    if side_a or side_b:
        decl.side_a, decl.side_b, decl.basis = side_a, side_b, SELECTION_SETS
        return decl

    halves = _split_test_name(name)
    if halves:
        left = profile.discipline_for_label(halves[0]) or ""
        right = profile.discipline_for_label(halves[1]) or ""
        if left or right:
            decl.side_a, decl.side_b, decl.basis = left, right, TEST_NAME
    return decl


def read_declarations(
    tests: list[dict[str, Any]], profile: Profile
) -> dict[str, TestDeclaration]:
    """Index every test's declaration by name, lowercased for lookup.

    Only declarations that separate two different trades are kept. A test
    that names one trade on both sides cannot tell the tagger anything the
    category index does not already say, and letting it through would put a
    guess ahead of a measurement for no gain.
    """
    out: dict[str, TestDeclaration] = {}
    for entry in tests or []:
        if not isinstance(entry, dict):
            continue
        decl = declaration_for(entry, profile)
        if decl.separates and decl.test:
            out[decl.test.strip().lower()] = decl
    return out
