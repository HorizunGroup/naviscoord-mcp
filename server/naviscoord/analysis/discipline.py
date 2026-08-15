"""Discipline tagging.

Every downstream judgement — criticality, who moves, which tests to build —
keys off the discipline of each side, so this runs first and its confidence
is reported rather than assumed. A model that lands mostly in OTRO means the
profile does not match the project, and the caller needs to know that before
reading any severity number.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from ..model import ClashExport, ElementRef
from ..profile import UNCLASSIFIED, Profile


@dataclass(slots=True)
class TaggingReport:
    """What the tagger decided, and how sure it is."""

    by_discipline: Counter[str] = field(default_factory=Counter)
    by_source: dict[str, Counter[str]] = field(default_factory=dict)
    resolved_by: Counter[str] = field(default_factory=Counter)
    unclassified_categories: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.by_discipline.values())

    @property
    def unclassified_ratio(self) -> float:
        if not self.total:
            return 0.0
        return self.by_discipline.get(UNCLASSIFIED, 0) / self.total

    def warnings(self) -> list[str]:
        out: list[str] = []
        ratio = self.unclassified_ratio
        if ratio > 0.25:
            worst = ", ".join(
                f"{name} ({count})" for name, count in self.unclassified_categories.most_common(5)
            )
            out.append(
                f"{ratio:.0%} de los elementos quedaron sin disciplina. "
                f"Categorías más frecuentes sin mapear: {worst or 'ninguna categoría reportada'}. "
                "Ajusta el perfil antes de confiar en la priorización."
            )
        elif ratio > 0.05:
            out.append(
                f"{ratio:.0%} de los elementos quedaron en OTRO; revisa el perfil "
                "si esos elementos importan para la coordinación."
            )
        for source, counter in self.by_source.items():
            if len(counter) > 1 and counter.most_common(1)[0][1] / max(sum(counter.values()), 1) < 0.6:
                mix = ", ".join(f"{d}:{c}" for d, c in counter.most_common(4))
                out.append(
                    f"El archivo '{source}' mezcla disciplinas ({mix}). "
                    "Si debería ser de una sola, el mapeo por categoría lo está partiendo."
                )
        return out

    def to_json(self) -> dict[str, object]:
        return {
            "total_elements": self.total,
            "by_discipline": dict(self.by_discipline.most_common()),
            "resolved_by": dict(self.resolved_by),
            "unclassified_ratio": round(self.unclassified_ratio, 3),
            "top_unmapped_categories": dict(self.unclassified_categories.most_common(10)),
            "by_source": {k: dict(v.most_common()) for k, v in self.by_source.items()},
            "warnings": self.warnings(),
        }


class DisciplineTagger:
    """Resolves an element to a discipline using layered evidence.

    Order matters. Category is the strongest signal because it comes from the
    authoring tool; the filename is a fallback for elements whose category is
    generic; system keywords break the ties the other two cannot, such as
    telling a sprinkler branch apart from a domestic water line when both are
    plain ``Pipes``.
    """

    def __init__(
        self,
        profile: Profile,
        overrides: dict[str, str] | None = None,
        group_key: str = "",
        group_roles: dict[str, str] | None = None,
    ) -> None:
        self.profile = profile
        # source_file -> discipline, set by the user when heuristics are wrong.
        self.overrides = {k.strip().lower(): v for k, v in (overrides or {}).items()}
        # A grouping discovered by reading the model: the property that
        # actually separates trades in THIS model, plus what each of its
        # values turned out to be. Takes precedence over the built-in
        # category rules, because it was measured rather than assumed.
        self.group_key = group_key
        self.group_roles = {k.strip().lower(): v for k, v in (group_roles or {}).items()}
        self.report = TaggingReport()

    def tag_export(self, export: ClashExport) -> TaggingReport:
        self.report = TaggingReport()
        for clash in export.clashes:
            for side in (clash.a, clash.b):
                side.discipline = self.tag(side)
                self._record(side)
        return self.report

    def tag(self, element: ElementRef) -> str:
        override = self.overrides.get(element.source_file.strip().lower())
        if override:
            self._resolved("override")
            return override

        if self.group_key and self.group_roles:
            value = element.prop(self.group_key)
            role = self.group_roles.get(value.strip().lower()) if value else ""
            if role:
                self._resolved("discovered_group")
                return role

        by_category = self.profile.discipline_for_category(element.category)
        refined = self._refine_by_system(element, by_category)
        if refined is not None:
            self._resolved("system_keyword")
            return refined
        if by_category:
            self._resolved("category")
            return by_category

        by_file = self.profile.discipline_for_filename(element.source_file)
        if by_file:
            self._resolved("filename")
            return by_file

        self._resolved("unresolved")
        return UNCLASSIFIED

    def _refine_by_system(self, element: ElementRef, by_category: str | None) -> str | None:
        """Split generic piping into HID / SAN / RCI using system naming.

        Only applies where the category is genuinely ambiguous: refining a
        duct into "fire" because a room name mentions it would be worse than
        leaving it alone.
        """
        if by_category not in {"HID", None}:
            return None
        haystack = " ".join(
            part
            for part in (
                element.prop("System Type", "System Name", "System Classification", "Sistema"),
                element.display_name,
                element.parent_name,
            )
            if part
        ).lower()
        if not haystack:
            return None
        for code, keywords in self.profile.system_keywords().items():
            if any(keyword in haystack for keyword in keywords):
                return code
        return None

    def _resolved(self, how: str) -> None:
        self.report.resolved_by[how] += 1

    def _record(self, element: ElementRef) -> None:
        self.report.by_discipline[element.discipline] += 1
        source = element.source_file or "(sin archivo)"
        self.report.by_source.setdefault(source, Counter())[element.discipline] += 1
        if element.discipline == UNCLASSIFIED and element.category:
            self.report.unclassified_categories[element.category] += 1


def propose_mapping(
    profile: Profile, sources: Iterable[str]
) -> list[dict[str, object]]:
    """Suggest a file -> discipline mapping for review before any test runs.

    Returned as proposals rather than applied silently: a wrong mapping
    quietly poisons every severity score downstream, so a human confirms it.
    """
    proposals: list[dict[str, object]] = []
    for source in sources:
        guess = profile.discipline_for_filename(source)
        proposals.append(
            {
                "source_file": source,
                "proposed_discipline": guess or UNCLASSIFIED,
                "label": profile.label(guess or UNCLASSIFIED),
                "confidence": "alta" if guess else "ninguna",
                "basis": "patrón en el nombre del archivo" if guess else "sin coincidencia; se decidirá por categoría de cada elemento",
            }
        )
    return proposals
