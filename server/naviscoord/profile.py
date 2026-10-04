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

from .profilerules import Problem, validate_semantics

UNCLASSIFIED = "OTRO"

# Largest profile the add-in will accept over the wire. A profile is a
# hand-written settings file; anything past this is a mistake or an attempt to
# make the add-in allocate on our behalf.
MAX_PROFILE_BYTES = 1024 * 1024

# Deepest nesting accepted, matching JsonStrict.MaxDepth in the add-in. Both
# ends have to agree or a profile one accepts is a profile the other refuses.
MAX_PROFILE_DEPTH = 128


class ProfileRejected(ValueError):
    """A profile that must not be installed, with a machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _reject_constant(token: str) -> Any:
    """Refuse the three tokens ``json`` accepts and JSON does not.

    ``json.loads`` maps ``NaN``, ``Infinity`` and ``-Infinity`` to floats by
    default. None of them is JSON, and none survives the round trip: the
    canonicaliser used to write them out as ``null``, so a profile carrying
    ``NaN`` produced the same checksum as one carrying ``null`` while the two
    servers held genuinely different numbers in memory. Two profiles that hash
    alike and behave differently is the exact failure a checksum exists to
    prevent.
    """
    # `invalid_json` and not `profile_invalid`: this is a syntax refusal, and
    # the add-in refuses the same three tokens at the same point in the grammar
    # with the same code. The categories are part of the parity contract.
    raise ProfileRejected(
        "invalid_json", f"JSON no admite {token}; el perfil no se cargó"
    )


def _assert_finite(value: Any, path: str = "$") -> None:
    """Every float in the tree is a real number, or nothing is loaded."""
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ProfileRejected(
            "profile_invalid", f"{path} no es finito ({value!r})"
        )
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _assert_finite(item, f"{path}[{i}]")


def _assert_depth(value: Any, limit: int = MAX_PROFILE_DEPTH, depth: int = 0) -> None:
    """Count containers, not values.

    A scalar sitting inside 128 nested objects is 128 deep, not 129 — and the
    add-in's reader counts the same way. An off-by-one here means a profile one
    end accepts and the other refuses, which is precisely the split this corpus
    exists to rule out.
    """
    if isinstance(value, (dict, list, tuple)):
        depth += 1
        if depth > limit:
            raise ProfileRejected(
                "json_too_deep", f"el anidamiento supera {limit} niveles"
            )
        items = value.values() if isinstance(value, dict) else value
        for item in items:
            _assert_depth(item, limit, depth)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Two parsers keep different values for a repeated key; refuse it."""
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ProfileRejected(
                "json_duplicate_key", f"la clave '{key}' aparece dos veces"
            )
        seen[key] = value
    return seen


def _text_depth_exceeds(text: str, limit: int) -> bool:
    """La anidación del TEXTO, sin construir el documento.

    `json.loads` con `object_pairs_hook` no usa el escáner en C: usa el de
    Python, que recursa. Un documento de miles de niveles agota la pila del
    intérprete mientras se parsea —antes de que `_assert_depth` llegue a
    mirarlo— y lo que recibe quien llamó es un RecursionError, que no es un
    rechazo tratable sino un fallo del intérprete. Y depende de la plataforma:
    el mismo documento pasa en un Windows con la pila grande y revienta en el
    Linux del CI, que es la peor forma de tener un límite.

    Se cuenta igual que `_assert_depth` —contenedores abiertos, no valores—
    para que las dos medidas no puedan discrepar en uno.
    """
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            depth += 1
            if depth > limit:
                return True
        elif character in "}]":
            depth -= 1
    return False


def loads_strict(text: str) -> Any:
    """Parse JSON the way the add-in does: no NaN, no duplicates, bounded."""
    if len(text.encode("utf-8")) > MAX_PROFILE_BYTES:
        raise ProfileRejected(
            "profile_too_large", f"el perfil supera {MAX_PROFILE_BYTES} bytes"
        )
    if _text_depth_exceeds(text, MAX_PROFILE_DEPTH):
        raise ProfileRejected(
            "json_too_deep", f"el anidamiento supera {MAX_PROFILE_DEPTH} niveles"
        )
    try:
        value = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except ProfileRejected:
        raise
    except json.JSONDecodeError as exc:
        # "Extra data" is the one decoder message that maps to a distinct
        # category on both sides: the document itself parsed, and then there
        # was more of it. Everything else is a malformed document.
        code = "json_trailing_content" if exc.msg.startswith("Extra data") else "invalid_json"
        raise ProfileRejected(code, f"{exc.msg} (línea {exc.lineno}, col {exc.colno})")
    if not isinstance(value, dict):
        raise ProfileRejected("json_not_an_object", "se esperaba un objeto JSON en la raíz")
    _assert_depth(value)
    _assert_finite(value)
    return value


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
    """Stable identity of the criteria a result was produced with.

    Raises :class:`ProfileRejected` for anything that cannot be canonicalised,
    including a non-finite float built in memory rather than parsed. A profile
    that cannot be described cannot be identified, and returning a checksum
    anyway would name two different things with one number.
    """
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
        # Refused, not written as ``null``.
        #
        # Emitting ``null`` made two genuinely different profiles hash alike:
        # one holding NaN and one holding null produced identical checksums
        # while the servers kept different numbers, so the identity a result
        # cites stopped identifying anything. A value that cannot be expressed
        # cannot be canonicalised, and saying so is the only honest option.
        raise ProfileRejected(
            "profile_invalid",
            f"un valor no finito ({value!r}) no tiene forma canónica en JSON",
        )
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
    #: Every category the profile's sets select for this trade, shared ones
    #: included. Used to build explicit sets, where the source file — not
    #: the category — decides between two trades that both model walls.
    selected_categories: set[str] = field(default_factory=set)


@dataclass(slots=True)
class Profile:
    name: str
    raw: dict[str, Any]
    disciplines: dict[str, DisciplineRule]
    _category_index: dict[str, str] = field(default_factory=dict)
    #: Engine sections this profile does not declare and reads from the
    #: default profile instead (see ``INHERITED_SECTIONS``). ``raw`` is never
    #: touched, so the checksum the add-in agrees on stays the file's own.
    _inherited: dict[str, dict[str, Any]] = field(default_factory=dict)

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
        return cls.from_json(loads_strict(target.read_text(encoding="utf-8")))

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
        if not disciplines:
            # The add-in's format: the trades are the folders of `sets`, and
            # nothing else declares them. The «Comité de obra» profile is
            # written that way, and this side read it as a profile with no
            # disciplines at all — navis_load_profile answered ["OTRO"] for
            # a three-trade project and every later step agreed.
            for rule in _disciplines_from_sets(raw):
                disciplines[rule.code] = rule
                for category in rule.categories:
                    category_index.setdefault(category, rule.code)
        if UNCLASSIFIED not in disciplines:
            disciplines[UNCLASSIFIED] = DisciplineRule(code=UNCLASSIFIED, label="Sin clasificar")
        return cls(
            name=raw.get("name", "unnamed"),
            raw=raw,
            disciplines=disciplines,
            _category_index=category_index,
            _inherited=_inherited_sections(raw, disciplines),
        )

    # ------------------------------------------------------------- lookups

    def section(self, name: str) -> dict[str, Any]:
        inherited = self._inherited.get(name)
        if inherited is not None:
            return inherited
        value = self.raw.get(name)
        return value if isinstance(value, dict) else {}

    @property
    def inherited_sections(self) -> list[str]:
        """Engine sections read from the default profile, for the caller to see."""
        return sorted(self._inherited)

    def discipline_for_category(self, category: str) -> str | None:
        if not category:
            return None
        return self._category_index.get(category.strip().lower())

    def discipline_for_filename(self, filename: str) -> str | None:
        """Match a source filename against the discipline patterns."""
        if not filename:
            return None
        return self.discipline_for_label(Path(filename).stem)

    def discipline_for_label(self, text: str) -> str | None:
        """Match any human-authored label against the discipline patterns.

        Filenames are one such label; the name of a clash test and the names
        of the saved sets on each of its sides are others, and they carry the
        same trade abbreviations. Matching them with one implementation keeps
        "STR" meaning structure everywhere it appears.

        Tokens are matched as whole segments (split on the usual separators)
        so that ``ele`` does not fire on ``MODELO-ARQ-ELEVACIONES.rvt``.
        """
        if not text:
            return None
        tokens = {t for t in _split_tokens(text.lower()) if t}
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

    def discipline_code(self, name: str) -> str:
        """The code a profile name refers to: a code, a folder label or an alias.

        The add-in's clash pairs name folders (``"a": "Estructura"``) while
        the engine tags elements with codes (``EST``); without this the two
        never meet and every pair reads as uncovered.
        """
        text = str(name or "").strip()
        if not text:
            return ""
        aliases = {
            str(k).strip().lower(): str(v).strip()
            for k, v in (self.raw.get("discipline_aliases") or {}).items()
            if not str(k).startswith("_") and str(v).strip()
        }
        text = aliases.get(text.lower(), text)
        for code, rule in self.disciplines.items():
            if text.lower() in (code.lower(), rule.label.lower()):
                return code
        return text.upper()

    def known_code(self, name: str) -> str:
        """The code for a code, folder label or alias the profile knows; else ``name`` as given.

        Unlike :meth:`discipline_code` an unknown name is not upper-cased: it is
        the caller's own discipline and stays as they wrote it.
        """
        text = str(name or "").strip()
        code = self.discipline_code(text)
        return code if code in self.disciplines else text

    def clash_pairs(self) -> list[dict[str, Any]]:
        """The pairs to compare, from ``clash_matrix.pairs`` or ``clash.pairs``.

        Two spellings exist because the server and the add-in grew separate
        formats: the add-in's configure reads ``clash``, the server's matrix
        read only ``clash_matrix`` and refused a profile that configure had
        just applied. Both are accepted; sides are resolved to discipline
        codes and the add-in's ``test`` key is read as the test ``type``.
        """
        for section in ("clash_matrix", "clash"):
            pairs = self.section(section).get("pairs") or []
            if not isinstance(pairs, list) or not pairs:
                continue
            out: list[dict[str, Any]] = []
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                entry = dict(pair)
                entry["a"] = self.discipline_code(str(pair.get("a", "")))
                entry["b"] = self.discipline_code(str(pair.get("b", "")))
                if "type" not in entry and pair.get("test"):
                    entry["type"] = pair["test"]
                entry["source_section"] = section
                out.append(entry)
            return out
        return []

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
        # Either order: default.json writes "EST|ARQ", "SAN|ELE", "RCI|ELE"…
        # and a sorted-only lookup read those pairs as the neutral 0.5.
        value = matrix.get(f"{a}|{b}")
        if value is None:
            value = matrix.get(f"{b}|{a}")
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
        """Human-readable complaints about a hand-edited profile.

        Three checks used to live inline here — the weight sum, a positive
        epsilon and the band order — and everything else was trusted. The rest
        of the engine's arithmetic was protected only by defaults, which apply
        when a field is ABSENT and not when it is declared wrong: a profile
        saying `cluster_size_saturation: 1` reached `math.log(1)` and divided
        by zero on the first cluster with two clashes in it.

        The rules now live in :mod:`profilerules` as a table, so the same
        inventory can be mirrored by the add-in and audited as a list rather
        than grepped for.
        """
        return [str(p) for p in self.semantic_problems()]

    def semantic_problems(self) -> list["Problem"]:
        """The structured version: code, path and detail per refusal."""
        return validate_semantics(self.raw)


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


# The engine's scoring and traceability criteria. A profile written in the
# add-in's format (sets, clash pairs, rules) declares none of them, and the
# engine used to read their absence as zeros: every severity weight 0.0, so
# all 1.434 issues of the «Comité de obra» scored 0 and came out «low» —
# navis_color_by_priority then had nothing to paint — and the Element Id was
# looked up only under "Element Id", while a local Revit NWC publishes it as
# "Id", so the handoff traced 0 of 1.434 issues back to Revit. A section the
# profile does not declare is now read from the default profile; one it does
# declare is used as written.
#
# `noise_filter` decides which clashes count as active at all, and the same
# gap applied: the Mirador profile of the Navisworks course (C6, 2026-10-04)
# declares no noise filter, so `drop_statuses` read as empty and the week-2
# run counted as open the 211 clashes Navisworks itself marked «Resuelto»
# (1.420 issues instead of 1.335). Its absence now reads the default filter.
INHERITED_SECTIONS = ("severity", "criticality_matrix", "movability", "interop", "noise_filter")

_DEFAULT_RAW: dict[str, Any] | None = None


def _default_raw() -> dict[str, Any]:
    global _DEFAULT_RAW
    if _DEFAULT_RAW is None:
        _DEFAULT_RAW = loads_strict(DEFAULT_PROFILE.read_text(encoding="utf-8"))
    return _DEFAULT_RAW


def _inherited_sections(
    raw: dict[str, Any], disciplines: dict[str, DisciplineRule] | None = None
) -> dict[str, dict[str, Any]]:
    try:
        defaults = _default_raw()
    except (OSError, ValueError):
        return {}
    if raw is defaults:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name in INHERITED_SECTIONS:
        fallback = defaults.get(name)
        if not isinstance(fallback, dict):
            continue
        own = raw.get(name)
        if not isinstance(own, dict):
            out[name] = fallback
        elif name == "severity" and not isinstance(own.get("weights"), dict):
            # Bands or thresholds without weights still score everything 0.
            out[name] = {**own, "weights": fallback.get("weights", {})}
        elif name == "noise_filter" and "drop_statuses" not in own:
            # A filter written without it would count Approved and Resolved
            # clashes as open. A profile that wants them kept says [] itself.
            out[name] = {**own, "drop_statuses": list(fallback.get("drop_statuses") or [])}
    if disciplines and ("criticality_matrix" in out or "movability" in out):
        _extend_for_own_codes(out, defaults, disciplines)
    return out


def _extend_for_own_codes(
    out: dict[str, dict[str, Any]],
    defaults: dict[str, Any],
    disciplines: dict[str, DisciplineRule],
) -> None:
    """Give the profile's own codes a row in the inherited tables.

    The default tables are keyed by the default trades (EST, ARQ, HVAC, HID…).
    A profile can name its own — a BEP that delivers every installation in
    one model calls it MEP — and a code missing from the tables scores as the
    neutral 0.5 on both, so «a duct through a beam» weighed like «two
    unknowns meet». Each own code is read through the default trades its
    categories belong to: the worst criticality of any of them, and the
    least movable of them. Only the inherited copy is extended; a table the
    profile writes itself is used as written.
    """
    index: dict[str, str] = {}
    for code, spec in (defaults.get("disciplines") or {}).items():
        if not isinstance(spec, dict):
            continue
        for category in spec.get("categories", []):
            index.setdefault(str(category).strip().lower(), code)
    default_codes = set(defaults.get("disciplines") or {})
    equivalents: dict[str, set[str]] = {}
    for code, rule in disciplines.items():
        if code in default_codes or code == UNCLASSIFIED:
            continue
        mapped = {index[c] for c in (rule.selected_categories or rule.categories) if c in index}
        if mapped:
            equivalents[code] = mapped
    if not equivalents:
        return

    def expand(code: str) -> set[str]:
        return equivalents.get(code, {code})

    if "criticality_matrix" in out:
        matrix = dict(out["criticality_matrix"])
        codes = sorted(set(equivalents) | default_codes)
        for a in codes:
            for b in codes:
                if a not in equivalents and b not in equivalents:
                    continue
                key = "|".join(sorted((a, b)))
                if key in matrix or f"{b}|{a}" in matrix:
                    continue
                values = [
                    float(matrix.get(f"{x}|{y}", matrix.get(f"{y}|{x}")))
                    for x in expand(a) for y in expand(b)
                    if f"{x}|{y}" in matrix or f"{y}|{x}" in matrix
                ]
                if values:
                    matrix[key] = max(values)
        out["criticality_matrix"] = matrix
    if "movability" in out:
        movability = dict(out["movability"])
        by_discipline = dict(movability.get("by_discipline") or {})
        gravity = list(movability.get("gravity_disciplines") or [])
        for code, mapped in equivalents.items():
            values = [float(by_discipline[m]) for m in mapped if m in by_discipline]
            if values and code not in by_discipline:
                by_discipline[code] = min(values)
        movability["by_discipline"] = by_discipline
        movability["gravity_disciplines"] = gravity
        out["movability"] = movability


def _scope_tokens(scope: str) -> list[str]:
    """``"-VTM-|-AAC-"`` → ``["VTM", "AAC"]``: the codes a scope names."""
    out: list[str] = []
    for variant in str(scope or "").split("|"):
        token = variant.strip().strip("-_ .").strip()
        if token and token.replace("-", "").replace("_", "").isalnum():
            out.append(token)
    return out


def _disciplines_from_sets(raw: dict[str, Any]) -> list[DisciplineRule]:
    """Discipline rules read from ``sets.folders`` (the add-in's format).

    Code: the model token of the folder's scope (``-EST-`` → ``EST``), or the
    folder name when there is none. Label: the folder name. File patterns:
    the scope tokens and the folder name. Categories: the ``Category`` values
    the folder's sets select — but only the ones no other folder also
    selects. ``Walls`` in both architecture and structure says nothing about
    which trade a wall is, and claiming it for the first folder would tag
    every structural wall as architecture; the model file decides instead.
    """
    sets = raw.get("sets") if isinstance(raw.get("sets"), dict) else {}
    folders = [f for f in (sets.get("folders") or []) if isinstance(f, dict)]
    rules: list[DisciplineRule] = []
    claimed: dict[str, set[str]] = {}
    for folder in folders:
        name = str(folder.get("folder") or "").strip()
        if not name:
            continue
        tokens = _scope_tokens(str(folder.get("scope_model_contains") or ""))
        code = tokens[0].upper() if tokens else name
        categories = {
            str(condition.get("value") or "").strip().lower()
            for entry in (folder.get("sets") or []) if isinstance(entry, dict)
            for condition in (entry.get("conditions") or []) if isinstance(condition, dict)
            if str(condition.get("property") or "").strip().lower() in ("category", "categoría", "categoria")
            and str(condition.get("test") or "equals").strip().lower() in ("equals", "=", "==")
            and str(condition.get("value") or "").strip()
        }
        for category in categories:
            claimed.setdefault(category, set()).add(code)
        patterns = [t.lower() for t in tokens] + [name.lower()]
        rules.append(DisciplineRule(code=code, label=name, categories=set(categories),
                                    file_patterns=list(dict.fromkeys(patterns)),
                                    selected_categories=set(categories)))
    for rule in rules:
        rule.categories = {c for c in rule.categories if len(claimed.get(c, ())) == 1}
    return rules


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
