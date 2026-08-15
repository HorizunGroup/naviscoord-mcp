"""Reading how a model is organised, instead of assuming it.

A coordination tool that ships a fixed list of disciplines and English Revit
categories works on the models that happen to match it and quietly mangles
every other one. Real federations arrive in Spanish or French, from Tekla or
Plant 3D, sometimes with a corporate classification in a shared parameter and
sometimes with nothing but a layer name carrying the discipline.

So nothing here is hardcoded to a standard. The module looks at what the
model actually contains and answers three questions with evidence:

1. **Is this a federation or one flattened file?** That decides whether the
   source file can identify a discipline at all.
2. **Is there a classification system?** OmniClass, Uniclass, MasterFormat,
   UniFormat, IFC or a corporate scheme — each has a recognisable value
   shape, and a real one beats every heuristic.
3. **What property best separates the model into trades?** Ranked by
   coverage, by how many distinct values it takes, and by whether those
   values line up with the file structure.

The output is a proposal with its reasoning attached, not a decision. A wrong
grouping key silently poisons every severity score downstream, so a human
confirms it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# Value shapes that identify a classification standard. Deliberately matched
# on the VALUES rather than the property name: the same standard shows up as
# "Assembly Code", "Clasificación", "Keynote" or a shared parameter depending
# on who set the model up.
CLASSIFICATION_PATTERNS: list[tuple[str, str, str]] = [
    ("OmniClass", r"^\d{2}-\d{2}\s?\d{2}\s?\d{2}(\s?\d{2})?$", "Tabla OmniClass (p. ej. 23-13 11 11)"),
    ("Uniclass", r"^[A-Z][a-z]_\d{2}_\d{2}(_\d{2})*$", "Uniclass 2015 (p. ej. Ss_25_10_30)"),
    ("MasterFormat", r"^\d{2}\s\d{2}\s\d{2}(\.\d+)?$", "MasterFormat (p. ej. 22 11 16)"),
    ("UniFormat", r"^[A-G]\d{4}(\.\d+)?$", "UniFormat (p. ej. B2010)"),
    ("IFC", r"^Ifc[A-Z][A-Za-z]+$", "Clase IFC (p. ej. IfcPipeSegment)"),
    # Hierarchical in-house schemes. Plenty of firms run their own coding
    # instead of a public standard, and the shape is recognisable even when
    # the scheme is not: a letter-and-digit root followed by dash-separated
    # levels. Add your own pattern here — the detector is data, not logic.
    ("Corporativo", r"^[A-Z]\d{3}-[A-Z]\d(-[A-Z]\d{1,3})*$",
     "Código corporativo jerárquico (p. ej. D021-A1-A12-A123)"),
]

# A property whose value is unique per element identifies elements, not
# trades. One that takes a single value separates nothing. The useful range
# for a discipline key sits between.
MIN_GROUPS = 2
MAX_GROUPS = 60
MIN_COVERAGE = 0.35

# Extensions that mean "this value names the model an element came from".
# Matched on the values rather than the property name, which is localised and
# differs between authoring tools.
MODEL_EXTENSIONS = (
    ".rvt", ".ifc", ".dwg", ".nwc", ".nwd", ".dgn", ".skp", ".3ds",
    ".sat", ".stp", ".step", ".iam", ".ipt", ".pln", ".tekla", ".db1",
)


def _looks_like_model_files(values: list[str]) -> float:
    """Share of values that name a model file."""
    if not values:
        return 0.0
    hits = sum(
        1 for v in values if v.strip().lower().endswith(MODEL_EXTENSIONS)
    )
    return hits / len(values)


@dataclass(slots=True)
class Candidate:
    """A property that might identify the trade an element belongs to."""

    kind: str  # "property" | "category" | "source_file"
    tab: str
    name: str
    coverage: float
    distinct: int
    top_values: dict[str, int]
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)
    informativeness: float = 0.0
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    # False for the two candidates built from document structure rather than
    # from a property. Only a real property can be read back per element, so
    # this is what decides whether a recommendation can actually be applied.
    synthetic: bool = False

    @property
    def label(self) -> str:
        if self.kind == "property":
            return f"{self.tab} → {self.name}" if self.tab else self.name
        return self.name

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "coverage": round(self.coverage, 3),
            "distinct_values": self.distinct,
            "informativeness": round(self.informativeness, 3),
            "readable_property": not self.synthetic,
            "score": round(self.score, 3),
            "why": self.reasons,
            "examples": list(self.top_values)[:8],
        }


@dataclass(slots=True)
class Classification:
    system: str
    description: str
    property_label: str
    coverage: float
    match_ratio: float
    examples: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "description": self.description,
            "property": self.property_label,
            "coverage": round(self.coverage, 3),
            "match_ratio": round(self.match_ratio, 3),
            "examples": self.examples[:6],
        }


@dataclass(slots=True)
class DiscoveryReport:
    federated: bool
    model_count: int
    sampled_items: int
    models: list[dict[str, Any]]
    classifications: list[Classification]
    candidates: list[Candidate]
    recommended: Candidate | None
    notes: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "structure": {
                "federated": self.federated,
                "models": self.model_count,
                "sampled_items": self.sampled_items,
                "files": [
                    {"index": m.get("index"), "source_file": m.get("source_file"),
                     "sampled": m.get("sampled")}
                    for m in self.models
                ],
            },
            "classification_systems": [c.to_json() for c in self.classifications],
            "grouping_candidates": [c.to_json() for c in self.candidates[:10]],
            "recommended": self.recommended.to_json() if self.recommended else None,
            "notes": self.notes,
        }

    def summary(self) -> str:
        lines: list[str] = []
        lines.append(
            f"Modelo {'federado' if self.federated else 'aplanado'} · "
            f"{self.model_count} modelo(s) cargado(s) · "
            f"{self.sampled_items} elementos muestreados"
        )
        if self.classifications:
            lines.append("")
            lines.append("Sistemas de clasificación encontrados:")
            for c in self.classifications:
                lines.append(
                    f"  · {c.system} en «{c.property_label}» — cubre {c.coverage:.0%} "
                    f"del modelo, {c.match_ratio:.0%} de los valores cuadran. "
                    f"Ej: {', '.join(c.examples[:3])}"
                )
        else:
            lines.append("")
            lines.append(
                "No hay un sistema de clasificación reconocible. Hay que agrupar por "
                "estructura del modelo (archivo, categoría o algún parámetro propio)."
            )

        lines.append("")
        lines.append("Cómo se puede separar por especialidad:")
        for c in self.candidates[:5]:
            lines.append(f"  · {c.label} — {c.score:.2f} · {'; '.join(c.reasons)}")

        if self.recommended:
            lines.append("")
            lines.append(f"Recomendado: {self.recommended.label}")
        for note in self.notes:
            lines.append(f"  ! {note}")
        return "\n".join(lines)


def discover(schema: dict[str, Any]) -> DiscoveryReport:
    """Interprets the raw schema dump from the addin."""
    models = schema.get("models") or []
    sampled = int(schema.get("sampled_items") or 0)
    properties = schema.get("properties") or []
    categories = schema.get("categories") or {}

    appended = len([m for m in models if int(m.get("sampled") or 0) > 0]) > 1

    # Federated by ORIGIN, not by how the file was delivered. A published
    # coordination NWD merges everything into one model while each element
    # still records the file it came from, so counting appended models
    # reports a real federation as a single blob.
    origin_property = next(
        (
            p
            for p in properties
            if float(p.get("coverage") or 0.0) >= 0.6
            and 2 <= int(p.get("distinct_values") or 0) <= MAX_GROUPS
            and _looks_like_model_files(list((p.get("top_values") or {}).keys())) >= 0.75
        ),
        None,
    )
    federated = appended or origin_property is not None

    classifications = _detect_classifications(properties)
    candidates = _rank_candidates(properties, categories, models, appended)
    notes: list[str] = []

    if origin_property is not None and not appended:
        notes.append(
            f"El documento trae un solo modelo, pero cada elemento conserva su archivo de "
            f"origen en «{origin_property.get('name')}» "
            f"({int(origin_property.get('distinct_values') or 0)} archivos): el proyecto SÍ "
            "está federado, solo que se entregó publicado en un archivo fusionado."
        )

    if not sampled:
        notes.append("No se muestreó ningún elemento; el modelo puede estar vacío o sin geometría hoja.")

    recommended = candidates[0] if candidates else None

    # In a federation the file split wins outright, not by a hair. Splitting
    # a project into models is a decision somebody made about who owns what,
    # and no statistic should overrule it — the element category may well
    # correlate a little better while cutting straight across the ownership
    # the team actually works to. The exception is a split that turns out not
    # to follow trades at all, which the content check catches.
    if federated:
        by_file = next((c for c in candidates if c.kind == "source_file"), None)
        if by_file is not None:
            # When the categories themselves came out unusable — a model
            # exported from a localised authoring tool, or one publishing
            # classification codes where the category belongs — the content
            # check has nothing to measure against and scores everything low.
            # The file split is then the ONLY trustworthy signal left, and
            # falling back to a category nobody could read would be worse.
            categories_usable = any(
                c.kind == "category" and c.distinct >= 5 for c in candidates
            )
            # The threshold only rejects a split that is no better than
            # random. It cannot be set high: informativeness is normalised
            # against the category entropy, so three files can no more
            # "determine" twenty-four categories than a boolean flag can, and
            # a 0.5 bar quietly disqualifies every real federation. Whether
            # the split actually follows trades is settled by reading what
            # each file contains, not by this number.
            if by_file.informativeness >= 0.12 or not categories_usable:
                recommended = by_file
                notes.append(
                    "Se agrupa por archivo de origen: es como el equipo dividió el proyecto, "
                    "y respetarlo mantiene la responsabilidad donde ya está."
                    + (
                        ""
                        if categories_usable
                        else " Además, las categorías de este modelo no se leyeron bien, "
                        "así que el archivo es la única señal confiable."
                    )
                )
    if recommended and recommended.score < 0.35:
        notes.append(
            "Ninguna propiedad separa bien las especialidades. Conviene confirmar el "
            "agrupamiento a mano antes de generar la matriz."
        )
    if not federated:
        notes.append(
            "El modelo es un solo archivo aplanado y sus elementos no conservan de qué "
            "archivo vienen, así que el origen no distingue especialidades: hay que "
            "separarlas por propiedades del elemento."
        )
    return DiscoveryReport(
        federated=federated,
        model_count=len(models),
        sampled_items=sampled,
        models=models,
        classifications=classifications,
        candidates=candidates,
        recommended=recommended,
        notes=notes,
    )


# ------------------------------------------------------- classifications


def _detect_classifications(properties: list[dict[str, Any]]) -> list[Classification]:
    found: list[Classification] = []

    for prop in properties:
        values = list((prop.get("top_values") or {}).keys())
        if len(values) < 2:
            continue

        for system, pattern, description in CLASSIFICATION_PATTERNS:
            regex = re.compile(pattern)
            matches = [v for v in values if regex.match(v.strip())]
            ratio = len(matches) / len(values)
            # Two thirds of sampled values fitting a standard's shape is not
            # a coincidence; a handful is.
            if ratio >= 0.66 and len(matches) >= 2:
                tab = prop.get("tab") or ""
                name = prop.get("name") or ""
                found.append(
                    Classification(
                        system=system,
                        description=description,
                        property_label=f"{tab} → {name}" if tab else name,
                        coverage=float(prop.get("coverage") or 0.0),
                        match_ratio=ratio,
                        examples=matches[:6],
                    )
                )
                break

    found.sort(key=lambda c: (-c.coverage, -c.match_ratio))
    return found


# ------------------------------------------------------------ candidates


def _rank_candidates(
    properties: list[dict[str, Any]],
    categories: dict[str, Any],
    models: list[dict[str, Any]],
    federated: bool,
) -> list[Candidate]:
    candidates: list[Candidate] = []

    if categories:
        candidates.append(
            _score(
                Candidate(
                    kind="category",
                    tab="",
                    name="Categoría del elemento",
                    synthetic=True,
                    coverage=1.0,
                    distinct=len(categories),
                    top_values={k: int(v) for k, v in categories.items()},
                    # Each category is its own group, so the cross-tab is the
                    # identity. Without it role inference silently returns an
                    # empty list for the most common recommendation there is.
                    by_category={k: {k: int(v)} for k, v in categories.items()},
                )
            )
        )

    if federated:
        files = {
            str(m.get("source_file") or f"modelo {m.get('index')}"): int(m.get("sampled") or 0)
            for m in models
        }
        # Each file's own category histogram is what tells us what trade it
        # holds — the filename is a label, the contents are the evidence.
        file_categories = {
            str(m.get("source_file") or f"modelo {m.get('index')}"): {
                str(c): int(n) for c, n in (m.get("categories") or {}).items()
            }
            for m in models
        }
        candidates.append(
            _score(
                Candidate(
                    kind="source_file",
                    tab="",
                    name="Archivo de origen",
                    synthetic=True,
                    coverage=1.0,
                    distinct=len(files),
                    top_values=files,
                    by_category=file_categories,
                )
            )
        )

    for prop in properties:
        coverage = float(prop.get("coverage") or 0.0)
        distinct = int(prop.get("distinct_values") or 0)
        if coverage < MIN_COVERAGE or not (MIN_GROUPS <= distinct <= MAX_GROUPS):
            continue
        values = {k: int(v) for k, v in (prop.get("top_values") or {}).items()}

        # A property whose values are model filenames records where each
        # element came from — the federation, preserved inside a merged file.
        # Publishing a coordination NWD flattens Models.Count to one, so a
        # check on that alone reports a genuinely federated project as a
        # single blob and throws away the team's own split. Which is the
        # normal case in practice, not the exception.
        kind = "source_file" if _looks_like_model_files(list(values)) >= 0.75 else "property"

        candidates.append(
            _score(
                Candidate(
                    kind=kind,
                    tab=str(prop.get("tab") or ""),
                    name=str(prop.get("name") or ""),
                    coverage=coverage,
                    distinct=distinct,
                    top_values=values,
                    by_category={
                        value: {c: int(n) for c, n in (cats or {}).items()}
                        for value, cats in (prop.get("by_category") or {}).items()
                    },
                )
            )
        )

    candidates.sort(key=lambda c: (-round(c.score, 3), _preference(c)))
    return candidates


def _informativeness(by_category: dict[str, dict[str, int]]) -> float:
    """How much knowing the group tells you about what an element IS.

    This is the test that separates a trade from a storey without knowing a
    word of the model's language, and it is the difference between proposing
    the disciplines and proposing the building's floors.

    Every storey holds walls, pipes and ducts in roughly the same mix, so the
    per-group category distribution barely moves from the model-wide one. A
    trade is the opposite: its group is dominated by its own categories.
    Measured as normalised mutual information between group and category — 0
    means the grouping says nothing about what things are, 1 means it says
    everything.
    """
    if not by_category:
        return 0.0

    joint: dict[tuple[str, str], int] = {}
    group_totals: dict[str, int] = {}
    category_totals: dict[str, int] = {}
    total = 0

    for group, categories in by_category.items():
        for category, count in categories.items():
            joint[(group, category)] = joint.get((group, category), 0) + count
            group_totals[group] = group_totals.get(group, 0) + count
            category_totals[category] = category_totals.get(category, 0) + count
            total += count

    if total == 0 or len(group_totals) < 2 or len(category_totals) < 2:
        return 0.0

    mutual = 0.0
    for (group, category), count in joint.items():
        p_xy = count / total
        p_x = group_totals[group] / total
        p_y = category_totals[category] / total
        if p_xy > 0:
            mutual += p_xy * math.log(p_xy / (p_x * p_y))

    entropy_category = -sum(
        (n / total) * math.log(n / total) for n in category_totals.values() if n > 0
    )
    # Normalised against the CATEGORY entropy — how much of what things are
    # does this grouping actually explain. Dividing by the smaller of the two
    # entropies flatters low-cardinality properties: a boolean flag like
    # "has triangles" scores a perfect 1.0 because two groups explain two
    # groups' worth of information, and it outranks the real discipline key
    # while explaining nothing about twenty-four categories.
    if entropy_category <= 0:
        return 0.0
    return max(0.0, min(1.0, mutual / entropy_category))


def _score(candidate: Candidate) -> Candidate:
    """How well a property would work as a discipline key.

    Four things matter. It has to cover most of the model, split it into a
    workable number of groups, split it evenly enough that the groups mean
    something — and, decisively, its groups have to correlate with what the
    elements ARE. The first three are satisfied perfectly by a storey name,
    which is exactly the trap.
    """
    reasons: list[str] = []
    candidate.informativeness = _informativeness(candidate.by_category)

    coverage_score = min(candidate.coverage / 0.9, 1.0)
    reasons.append(f"presente en {candidate.coverage:.0%} de los elementos")

    # Around eight groups is ideal, because that is roughly how many trades a
    # building project has. Twelve was too high: it penalised a perfectly
    # normal four-discipline federation for being "coarse".
    ideal = 8.0
    ratio = candidate.distinct / ideal
    granularity = math.exp(-((math.log(max(ratio, 1e-6))) ** 2) / 2.0)
    reasons.append(f"{candidate.distinct} valores distintos")

    balance = 1.0
    if candidate.top_values:
        counts = sorted(candidate.top_values.values(), reverse=True)
        total = sum(counts) or 1
        dominant = counts[0] / total
        # One value covering nearly everything is a label, not a partition.
        balance = 1.0 - max(0.0, dominant - 0.6) / 0.4
        if dominant > 0.85:
            reasons.append(f"pero un solo valor se lleva el {dominant:.0%}")

    if candidate.kind == "source_file":
        reasons.append("cada archivo suele ser una especialidad")
    elif candidate.kind == "category":
        # The category IS what an element is, so measuring how much it tells
        # you about itself is circular. It gets the full mark by definition.
        candidate.informativeness = 1.0
        reasons.append("siempre disponible")

    if candidate.kind != "category":
        if candidate.informativeness >= 0.5:
            reasons.append(
                f"sus grupos predicen bien qué es cada elemento ({candidate.informativeness:.0%})"
            )
        elif candidate.informativeness >= 0.15:
            reasons.append(
                f"solo predice débilmente qué es cada elemento ({candidate.informativeness:.0%})"
            )
        else:
            reasons.append(
                "sus grupos NO dicen nada sobre qué es cada elemento: "
                "esto separa por ubicación (niveles o zonas), no por especialidad"
            )

    # Weights sum to exactly 1.0 and nothing is added on top. Bonuses used to
    # push good candidates past the ceiling, where two very different keys
    # both clamped to 1.00 and the winner came down to insertion order — a
    # federation's own file structure lost a coin toss to the element
    # category. Preference between equals belongs in the tie-break, not in
    # the score.
    candidate.score = max(
        0.0,
        min(
            1.0,
            0.25 * coverage_score
            + 0.20 * granularity
            + 0.10 * balance
            + 0.45 * candidate.informativeness,
        ),
    )
    candidate.reasons = reasons
    return candidate


def _preference(candidate: Candidate) -> int:
    """Tie-break between equally strong keys, best first.

    A federation's file split is a decision somebody made about who owns
    what, so it beats a property that happens to correlate just as well.
    """
    return {"source_file": 0, "category": 1, "property": 2}.get(candidate.kind, 3)


@dataclass(slots=True)
class GroupRole:
    """What a discovered group actually is, judged by what it holds."""

    value: str
    role: str
    confidence: float
    items: int
    evidence: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "group": self.value,
            "role": self.role,
            "confidence": round(self.confidence, 2),
            "items": self.items,
            "evidence": self.evidence,
        }


def infer_group_roles(candidate: Candidate, profile: Any) -> list[GroupRole]:
    """Assigns each discovered group a trade role from its CONTENT.

    Names lie and names are local. A group may be called "MEC-01", "Lote 3",
    "Gewerk 4" or nothing at all, and matching on those strings is exactly the
    assumption that breaks the moment a model arrives from another office or
    another country.

    What does not lie is what the group contains. A group full of ducts and
    air terminals is the ventilation trade wherever it was authored and
    whatever it is called. So the role comes from the category profile, and
    the confidence comes from how dominant that profile is — a group that is
    60% ducts is ventilation; one split evenly across six trades is a mixed
    bag and gets said so.
    """
    roles: list[GroupRole] = []

    ignored = {
        name.strip().lower()
        for name in (profile.section("non_discipline_categories").get("names") or [])
    }

    for value, raw_categories in candidate.by_category.items():
        # Grids, levels, rooms and annotation belong to no trade and are not
        # coordinated. Counting them as "unrecognised" buries the real
        # signal: on a live model 7.043 grid elements dragged the electrical
        # file's confidence down to 19%.
        categories = {
            c: n for c, n in raw_categories.items() if c.strip().lower() not in ignored
        }
        total = sum(categories.values())
        if total == 0:
            continue

        votes: dict[str, int] = {}
        unmapped = 0
        for category, count in categories.items():
            code = profile.discipline_for_category(category)
            if code:
                votes[code] = votes.get(code, 0) + count
            else:
                unmapped += count

        if not votes:
            roles.append(
                GroupRole(
                    value=value,
                    role="OTRO",
                    confidence=0.0,
                    items=total,
                    evidence=[
                        "Ninguna de sus categorías es reconocible; hay que asignarle "
                        "el rol a mano."
                    ],
                )
            )
            continue

        winner, winner_count = max(votes.items(), key=lambda kv: kv[1])
        share = winner_count / total

        # A winner holding a sliver of the group is not a winner. Reporting
        # "electrical model → ventilation (0%)" because two of its 14.000
        # elements mapped to ductwork is worse than admitting ignorance: it
        # reads as a decision and routes the work to the wrong trade.
        if share < 0.15:
            mapped = sum(votes.values()) / total
            roles.append(
                GroupRole(
                    value=value,
                    role="OTRO",
                    confidence=0.0,
                    items=total,
                    evidence=[
                        f"Solo se reconoció el {mapped:.0%} de sus categorías, y ninguna "
                        "domina lo suficiente para decidir la especialidad",
                        "Categorías presentes: "
                        + ", ".join(c for c, _ in sorted(categories.items(), key=lambda kv: -kv[1])[:4]),
                        "Hay que asignarle el rol a mano o ampliar el perfil con estas categorías",
                    ],
                )
            )
            continue
        top_categories = sorted(categories.items(), key=lambda kv: -kv[1])[:3]

        evidence = [
            f"{share:.0%} de sus elementos son de {profile.label(winner)}",
            "principalmente " + ", ".join(f"{c} ({n})" for c, n in top_categories),
        ]
        if unmapped / total > 0.3:
            evidence.append(
                f"{unmapped / total:.0%} de sus elementos tienen categorías sin mapear"
            )
        if len(votes) > 1:
            runner_up = sorted(votes.items(), key=lambda kv: -kv[1])[1]
            if runner_up[1] / total > 0.25:
                evidence.append(
                    f"pero también contiene {runner_up[1] / total:.0%} de "
                    f"{profile.label(runner_up[0])}: es un grupo mixto"
                )

        roles.append(
            GroupRole(
                value=value,
                role=winner,
                confidence=round(share, 2),
                items=total,
                evidence=evidence,
            )
        )

    roles.sort(key=lambda r: -r.items)
    return roles


def build_groups(
    schema: dict[str, Any], candidate: Candidate, min_items: int = 25
) -> list[dict[str, Any]]:
    """Turns the chosen key into the groups the clash matrix will be built from.

    Groups below `min_items` are reported but flagged: testing a five-element
    group against everything produces noise, not coordination.
    """
    groups: list[dict[str, Any]] = []
    for value, count in sorted(candidate.top_values.items(), key=lambda kv: -kv[1]):
        groups.append(
            {
                "value": value,
                "items": int(count),
                "usable": int(count) >= min_items,
            }
        )
    return groups
