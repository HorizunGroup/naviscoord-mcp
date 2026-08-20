"""Project profile: every coordination judgement call lives here, not in code.

The engine ships no opinion of its own about what a serious clash is. It
reads weights, tolerances and discipline rules from a JSON profile so a
project can be re-tuned without touching Python, and so two projects can
disagree about severity without forking the engine.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Inside the package, so it ships with a normal (non-editable) install.
# Sitting one level up worked only because every install so far was editable.
PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
DEFAULT_PROFILE = PROFILE_DIR / "default.json"

UNCLASSIFIED = "OTRO"

# Largest profile the add-in will accept over the wire. A profile is a
# hand-written settings file; anything past this is a mistake or an attempt to
# make the add-in allocate on our behalf.
MAX_PROFILE_BYTES = 1024 * 1024


# --------------------------------------------------------------- canonical form


def canonical_text(raw: Any) -> str:
    """The exact characters both ends hash to identify a profile.

    Written out by hand rather than delegated to ``json.dumps`` because the
    add-in has to reproduce this byte for byte in C#, and the standard
    library's spacing, escaping and float formatting are implementation
    detail, not a contract. The two sides previously each hashed their own
    idea of "canonical" — ``json.dumps`` here, a bespoke ``{k:v;}`` walker
    there — so the checksums could never have matched, and the comment
    claiming they mirrored each other was simply wrong.

    The rules, mirrored by ``ProfileSchema.Canonical`` in the add-in:

    * comment keys (leading ``_``) are dropped, so rewording a note does not
      invalidate every stored checksum;
    * object keys are sorted by UTF-16 code unit, which is what C#'s ordinal
      comparer does;
    * strings are escaped to pure ASCII, so the encoding cannot differ;
    * a number that is mathematically an integer is written as one, because
      the add-in's parser has only ``double`` and cannot tell ``1`` from
      ``1.0``.
    """
    out: list[str] = []
    _canonical(raw, out)
    return "".join(out)


def checksum_of(raw: Any) -> str:
    """Stable identity of the criteria a result was produced with."""
    return hashlib.sha256(canonical_text(raw).encode("utf-8")).hexdigest()[:16]


# 2**53: past this a double cannot represent every integer, so the add-in
# could not agree with us about the value even in principle.
_EXACT_INT_LIMIT = 9007199254740992


def _canonical(value: Any, out: list[str]) -> None:
    # bool before int: in Python, bool IS an int, and True would otherwise
    # canonicalise as "1" and collide with the number.
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        _canonical_string(value, out)
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, float):
        out.append(_canonical_float(value))
    elif isinstance(value, dict):
        out.append("{")
        keys = [k for k in value if not str(k).startswith("_")]
        # Ordinal by UTF-16 code unit, so C#'s StringComparer.Ordinal agrees
        # even on the surrogate pairs no real profile key contains.
        for i, key in enumerate(sorted(keys, key=lambda k: str(k).encode("utf-16-be"))):
            if i:
                out.append(",")
            _canonical_string(str(key), out)
            out.append(":")
            _canonical(value[key], out)
        out.append("}")
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _canonical(item, out)
        out.append("]")
    else:
        _canonical_string(str(value), out)


def _canonical_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("JSON canónico no admite NaN ni Infinity")
    if value.is_integer() and abs(value) <= _EXACT_INT_LIMIT:
        return str(int(value))
    # repr gives the shortest string that round-trips; the add-in reaches the
    # same string with a G15/G16/G17 ladder.
    return repr(value)


_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def _canonical_string(value: str, out: list[str]) -> None:
    out.append('"')
    for char in value:
        escape = _ESCAPES.get(char)
        if escape is not None:
            out.append(escape)
            continue
        point = ord(char)
        if 0x20 <= point <= 0x7E:
            out.append(char)
        elif point > 0xFFFF:
            # C# walks UTF-16 code units and would emit the pair; match it.
            point -= 0x10000
            out.append(f"\\u{0xD800 + (point >> 10):04x}")
            out.append(f"\\u{0xDC00 + (point & 0x3FF):04x}")
        else:
            out.append(f"\\u{point:04x}")
    out.append('"')


@dataclass(slots=True)
class DisciplineRule:
    code: str
    label: str
    categories: set[str] = field(default_factory=set)
    file_patterns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Profile:
    name: str
    raw: dict[str, Any]
    disciplines: dict[str, DisciplineRule]
    _category_index: dict[str, str] = field(default_factory=dict)

    # ---------------------------------------------------------------- load

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Profile":
        target = Path(path) if path else DEFAULT_PROFILE
        if not target.exists() and not target.is_absolute():
            candidate = PROFILE_DIR / f"{target.name}"
            if candidate.suffix != ".json":
                candidate = candidate.with_suffix(".json")
            if candidate.exists():
                target = candidate
        if not target.exists():
            raise FileNotFoundError(f"profile not found: {target}")
        return cls.from_json(
            json.loads(
                target.read_text(encoding="utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"JSON no admite la constante {value}")
                ),
            )
        )

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Profile":
        disciplines: dict[str, DisciplineRule] = {}
        category_index: dict[str, str] = {}
        for code, spec in (raw.get("disciplines") or {}).items():
            categories = {c.strip().lower() for c in spec.get("categories", []) if c.strip()}
            disciplines[code] = DisciplineRule(
                code=code,
                label=spec.get("label", code),
                categories=categories,
                file_patterns=[p.lower() for p in spec.get("file_patterns", [])],
            )
            for category in categories:
                # First discipline to claim a category wins, so profile order
                # is the tie-break for genuinely ambiguous categories.
                category_index.setdefault(category, code)
        if UNCLASSIFIED not in disciplines:
            disciplines[UNCLASSIFIED] = DisciplineRule(code=UNCLASSIFIED, label="Sin clasificar")
        return cls(
            name=raw.get("name", "unnamed"),
            raw=raw,
            disciplines=disciplines,
            _category_index=category_index,
        )

    # ------------------------------------------------------------- lookups

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name)
        return value if isinstance(value, dict) else {}

    def discipline_for_category(self, category: str) -> str | None:
        if not category:
            return None
        return self._category_index.get(category.strip().lower())

    def discipline_for_filename(self, filename: str) -> str | None:
        """Match a source filename against the discipline patterns.

        Tokens are matched as whole segments (split on the usual separators)
        so that ``ele`` does not fire on ``MODELO-ARQ-ELEVACIONES.rvt``.
        """
        if not filename:
            return None
        stem = Path(filename).stem.lower()
        tokens = {t for t in _split_tokens(stem) if t}
        best: tuple[int, str] | None = None
        for code, rule in self.disciplines.items():
            for pattern in rule.file_patterns:
                if pattern in tokens:
                    # Longer patterns are more specific: prefer "hvac" over "aa".
                    score = len(pattern)
                    if best is None or score > best[0]:
                        best = (score, code)
        return best[1] if best else None

    def label(self, code: str) -> str:
        rule = self.disciplines.get(code)
        return rule.label if rule else code

    def noun_for(self, category: str) -> str:
        """How to name an element of this category in prose, or ''."""
        nouns = self.raw.get("element_nouns") or {}
        for name, noun in nouns.items():
            if name.startswith("_"):
                continue
            if name.strip().lower() == category.strip().lower():
                return str(noun)
        return ""

    def describe_element(self, element: Any) -> str:
        """A phrase a site coordinator would actually say.

        The geometry node in a Revit export is usually named after its
        material, so the raw name yields "Copper entered Concrete,
        Cast-in-Place gray". The category is what identifies the thing; the
        material and system are what distinguish one from another.
        """
        noun = self.noun_for(getattr(element, "category", ""))
        has_props = hasattr(element, "prop")
        system = element.prop("System Name", "System Type", "Sistema") if has_props else ""
        type_name = element.prop("Type", "Family and Type", "Family") if has_props else ""
        name = (getattr(element, "display_name", "") or "").strip()

        if not noun:
            return type_name or name or getattr(element, "category", "") or "el elemento"

        # System first: "the sanitary pipe" is what a coordinator says and
        # what they can find. Then the type. The display name is the last
        # resort and usually the least useful — on a Revit export it is the
        # material, which turns "the slab" into "the slab (Concrete,
        # Cast-in-Place gray)". That parenthetical helps nobody locate it.
        if system:
            return f"{noun} de {system.strip()}"
        if _is_useful_name(type_name) and type_name.lower() not in noun.lower():
            return f"{noun} {type_name.strip()}"
        return noun

    # ---------------------------------------------------------- parameters

    def criticality(self, a: str, b: str) -> float:
        matrix = self.section("criticality_matrix")
        key = "|".join(sorted((a, b)))
        value = matrix.get(key)
        if value is None:
            return 0.5
        return float(value)

    def movability(self, discipline: str, category: str) -> float:
        """How cheap it is to move this side, 0.0 (immovable) to 1.0.

        Category beats discipline: a piece of mechanical equipment is bolted
        down even though HVAC ductwork around it is flexible.
        """
        spec = self.section("movability")
        by_category = spec.get("by_category") or {}
        for name, value in by_category.items():
            if name.strip().lower() == category.strip().lower():
                return float(value)
        by_discipline = spec.get("by_discipline") or {}
        if discipline in by_discipline:
            return float(by_discipline[discipline])
        return 0.5

    def is_gravity(self, discipline: str) -> bool:
        spec = self.section("movability")
        return discipline in set(spec.get("gravity_disciplines") or [])

    def weight(self, name: str) -> float:
        weights = self.section("severity").get("weights") or {}
        return float(weights.get(name, 0.0))

    def severity_param(self, name: str, default: float) -> float:
        return float(self.section("severity").get(name, default))

    def cluster_param(self, name: str, default: Any) -> Any:
        return self.section("clustering").get(name, default)

    def noise_param(self, name: str, default: Any) -> Any:
        return self.section("noise_filter").get(name, default)

    def root_cause_param(self, rule: str, name: str, default: Any) -> Any:
        spec = self.section("root_cause").get(rule) or {}
        return spec.get(name, default)

    def folding_kinds(self) -> set[str]:
        """Root-cause kinds where one decision closes every affected issue."""
        spec = self.section("root_cause").get("folding") or {}
        return set(spec.get("kinds") or [])

    def priority_for(self, severity: float) -> str:
        bands = self.section("severity").get("priority_bands") or {}
        if severity >= float(bands.get("critical", 75.0)):
            return "critical"
        if severity >= float(bands.get("high", 55.0)):
            return "high"
        if severity >= float(bands.get("medium", 35.0)):
            return "medium"
        return "low"

    def system_keywords(self) -> dict[str, list[str]]:
        return {
            code: [k.lower() for k in keywords]
            for code, keywords in (self.raw.get("system_keywords") or {}).items()
        }

    def validate(self) -> list[str]:
        """Return human-readable complaints about a hand-edited profile."""
        problems: list[str] = []
        _find_non_finite(self.raw, "$", problems)
        weights = self.section("severity").get("weights") or {}
        numeric_weights = [value for value in weights.values() if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if len(numeric_weights) != len(weights) or any(not math.isfinite(float(value)) for value in numeric_weights):
            problems.append("severity.weights debe contener solo números JSON finitos.")
            total = 0.0
        else:
            total = sum(float(value) for value in numeric_weights)
        if total and abs(total - 1.0) > 0.01:
            problems.append(
                f"Los pesos de severidad suman {total:.2f} y deberían sumar 1.00; "
                "la puntuación quedará fuera del rango 0-100."
            )
        eps = _number(self.cluster_param("eps_m", None), "clustering.eps_m", problems)
        if eps is not None and eps <= 0.0:
            problems.append("clustering.eps_m debe ser mayor que cero.")
        clustering = self.section("clustering")
        if "max_cluster_span_m" in clustering:
            max_span = _number(
                clustering["max_cluster_span_m"],
                "clustering.max_cluster_span_m", problems,
            )
            if max_span is not None and max_span <= 0.0:
                problems.append("clustering.max_cluster_span_m debe ser mayor que cero.")
        min_samples = self.cluster_param("min_samples", 1)
        if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples < 1:
            problems.append("clustering.min_samples debe ser un entero mayor o igual a 1.")
        saturation = self.severity_param("cluster_size_saturation", 25.0)
        if isinstance(saturation, bool) or not isinstance(saturation, (int, float)) or not math.isfinite(float(saturation)) or float(saturation) <= 1.0:
            problems.append("severity.cluster_size_saturation debe ser un número finito mayor que 1.")
        severity = self.section("severity")
        for key, allow_zero in (
            ("penetration_ratio_cap", False),
            ("congestion_radius_m", True),
            ("congestion_saturation", False),
        ):
            if key not in severity:
                continue
            value = _number(severity.get(key), f"severity.{key}", problems)
            if value is not None and (value < 0.0 if allow_zero else value <= 0.0):
                problems.append(
                    f"severity.{key} debe ser {'mayor o igual a 0' if allow_zero else 'mayor que 0'}."
                )

        bands = severity.get("priority_bands") or {}
        ordered = [
            _number(bands.get(key, default), f"severity.priority_bands.{key}", problems)
            for key, default in (("critical", 75.0), ("high", 55.0), ("medium", 35.0))
        ]
        if all(value is not None for value in ordered) and not (
            100.0 >= ordered[0] > ordered[1] > ordered[2] >= 0.0
        ):
            problems.append("priority_bands debe ir de mayor a menor: critical > high > medium.")

        for path, value, zero_ok in (
            ("noise_filter.min_penetration_m", self.noise_param("min_penetration_m", 0.0), True),
            ("noise_filter.insulation_penetration_tolerance_m", self.noise_param("insulation_penetration_tolerance_m", 0.0), True),
            ("root_cause.systemic_elevation.max_z_stddev_m", self.root_cause_param("systemic_elevation", "max_z_stddev_m", None), False),
            ("root_cause.missing_penetration.search_radius_m", self.root_cause_param("missing_penetration", "search_radius_m", None), False),
            ("root_cause.repeated_typology.xy_tolerance_m", self.root_cause_param("repeated_typology", "xy_tolerance_m", None), False),
            ("root_cause.congested_zone.radius_m", self.root_cause_param("congested_zone", "radius_m", None), False),
        ):
            if value is None:
                continue
            number = _number(value, path, problems)
            if number is not None and (number < 0.0 if zero_ok else number <= 0.0):
                problems.append(f"{path} debe ser {'no negativo' if zero_ok else 'mayor que cero'}.")

        pairs = self.section("clash_matrix").get("pairs") or []
        if not isinstance(pairs, list):
            problems.append("clash_matrix.pairs debe ser una lista.")
        else:
            for index, pair in enumerate(pairs):
                path = f"clash_matrix.pairs[{index}]"
                if not isinstance(pair, dict):
                    problems.append(f"{path} debe ser un objeto.")
                    continue
                if not str(pair.get("a") or "").strip() or not str(pair.get("b") or "").strip():
                    problems.append(f"{path} debe declarar disciplinas a y b.")
                if str(pair.get("type", "Hard")) not in {"Hard", "Clearance", "Duplicate", "Soft"}:
                    problems.append(f"{path}.type no es un tipo de clash soportado.")
                tolerance = _number(pair.get("tolerance_m", 0.0), f"{path}.tolerance_m", problems)
                if tolerance is not None and tolerance < 0.0:
                    problems.append(f"{path}.tolerance_m no puede ser negativo.")
        return problems


def _number(value: Any, path: str, problems: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{path} debe ser un número JSON finito.")
        return None
    number = float(value)
    if not math.isfinite(number):
        problems.append(f"{path} debe ser un número JSON finito.")
        return None
    return number


def _find_non_finite(value: Any, path: str, problems: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        problems.append(f"{path} contiene un número no finito.")
    elif isinstance(value, dict):
        for key, child in value.items():
            _find_non_finite(child, f"{path}.{key}", problems)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _find_non_finite(child, f"{path}[{index}]", problems)


# Values Navisworks publishes for its own node classes. They appear in the
# Type property just as readily as a real family type, and pasted into prose
# they produce phrases like "la losa Solid".
_NODE_ARTEFACTS = {
    # English
    "solid", "group", "insert", "layer", "geometry", "file", "instance",
    "collection", "composite object", "line", "point", "text", "mesh",
    "cylinder", "box", "item", "element", "node", "unknown", "material",
    # Spanish — a localised Navisworks publishes these just as readily, and
    # an English-only list yields "la tubería Sólido entra en la viga Sólido".
    "sólido", "solido", "grupo", "capa", "geometría", "geometria", "archivo",
    "instancia", "colección", "coleccion", "objeto compuesto", "línea",
    "linea", "punto", "texto", "malla", "cilindro", "caja", "elemento",
    "nodo", "desconocido", "inserción", "insercion",
    # French, German and Portuguese equivalents of the same handful.
    "solide", "groupe", "couche", "géométrie", "fichier", "ligne",
    "körper", "gruppe", "ebene", "geometrie", "datei", "linie",
    "sólido composto", "camada", "arquivo", "linha",
}


def _is_useful_name(value: str) -> bool:
    if not value or not value.strip():
        return False
    return value.strip().lower() not in _NODE_ARTEFACTS


def _split_tokens(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for char in text:
        if char.isalnum():
            current.append(char)
        else:
            if current:
                out.append("".join(current))
                current = []
    if current:
        out.append("".join(current))
    return out
