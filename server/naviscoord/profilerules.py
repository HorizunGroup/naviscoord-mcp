"""What a profile is allowed to say, expressed once.

A profile that parses is not a profile that works. ``cluster_size_saturation:
1`` is syntactically perfect JSON and makes the severity scorer divide by
``math.log(1)`` — zero — on the first cluster with more than one clash. Nothing
caught it, because the schema described shapes and the code carried defaults
that only apply when a field is *absent*: a field declared with a wrong value
reaches the arithmetic intact.

So the rules live here, as data rather than as scattered ``if`` statements, for
three reasons. Every consumer can be checked against the same table; the add-in
can mirror it without reading Python; and the numbered inventory below is
auditable — each entry names the operation the value participates in, so
"is anything else dividing by a profile value?" is a question this file
answers rather than one a reader has to grep for.

Bounds are *semantic*, not defensive rounding. A weight of −0.3 is not
clamped to zero: a profile that asks for a negative weight is asking for
something the scorer cannot express, and installing it silently would produce
scores nobody can explain.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

# Categories, so both ends can agree on *why* something was refused without
# comparing translated sentences.
TYPE_ERROR = "profile_type"
RANGE_ERROR = "profile_range"
RELATION_ERROR = "profile_relation"
REFERENCE_ERROR = "profile_reference"
UNKNOWN_ERROR = "profile_unknown"


@dataclass(frozen=True)
class Problem:
    """One refusal, with the path that caused it."""

    code: str
    path: str
    detail: str

    def to_json(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path, "detail": self.detail}

    def __str__(self) -> str:  # what the operator reads
        return f"{self.path}: {self.detail}"


@dataclass(frozen=True)
class NumberRule:
    """A numeric field, its bounds, and what it is used for.

    ``why`` is not decoration. It names the operation the value participates
    in, which is what makes this table an audit of the engine's arithmetic
    rather than a list of opinions about reasonable numbers.
    """

    path: str
    why: str
    minimum: float | None = None
    maximum: float | None = None
    exclusive_min: bool = False
    exclusive_max: bool = False
    integer: bool = False

    def check(self, value: Any) -> list[Problem]:
        problems: list[Problem] = []

        # bool BEFORE int, because in Python `isinstance(True, int)` is True
        # and `True` would sail through every numeric bound as 1.
        if isinstance(value, bool):
            return [Problem(TYPE_ERROR, self.path,
                            f"se esperaba un número y llegó un booleano ({value!r})")]
        if isinstance(value, str):
            return [Problem(TYPE_ERROR, self.path,
                            f"se esperaba un número y llegó una cadena ({value!r})")]
        if not isinstance(value, (int, float)):
            return [Problem(TYPE_ERROR, self.path,
                            f"se esperaba un número y llegó {type(value).__name__}")]
        if not math.isfinite(value):
            return [Problem(RANGE_ERROR, self.path, f"no es finito ({value!r})")]
        if self.integer and float(value) != int(value):
            return [Problem(TYPE_ERROR, self.path, f"debe ser entero y es {value!r}")]

        if self.minimum is not None:
            if self.exclusive_min and value <= self.minimum:
                problems.append(Problem(
                    RANGE_ERROR, self.path,
                    f"debe ser mayor que {self.minimum} ({self.why}); llegó {value}"))
            elif not self.exclusive_min and value < self.minimum:
                problems.append(Problem(
                    RANGE_ERROR, self.path,
                    f"no puede ser menor que {self.minimum} ({self.why}); llegó {value}"))
        if self.maximum is not None:
            if self.exclusive_max and value >= self.maximum:
                problems.append(Problem(
                    RANGE_ERROR, self.path,
                    f"debe ser menor que {self.maximum} ({self.why}); llegó {value}"))
            elif not self.exclusive_max and value > self.maximum:
                problems.append(Problem(
                    RANGE_ERROR, self.path,
                    f"no puede superar {self.maximum} ({self.why}); llegó {value}"))
        return problems


# ---------------------------------------------------------------- inventory
#
# Every numeric profile field the engine reads, with the operation that makes
# its bound necessary. Grouped by the section it lives in.

NUMBER_RULES: tuple[NumberRule, ...] = (
    # ---- clustering: the DBSCAN radius, the density threshold and the span
    NumberRule("clustering.eps_m", "radio de vecindad de DBSCAN",
               minimum=0.0, exclusive_min=True, maximum=1000.0),
    NumberRule("clustering.min_samples", "puntos mínimos para formar un núcleo",
               minimum=1, integer=True, maximum=100000),
    NumberRule("clustering.max_cluster_span_m", "cota superior del tamaño de un problema",
               minimum=0.0, exclusive_min=True, maximum=10000.0),

    # ---- severity: the saturations are DIVISORS, which is the defect that
    #      started this. `cluster_size_saturation` divides by log(x), so 1 is
    #      as fatal as 0; `congestion_saturation` divides directly.
    NumberRule("severity.cluster_size_saturation",
               "divisor log(x) del componente de tamaño: con 1, log(1)=0",
               minimum=1.0, exclusive_min=True, maximum=1e6),
    NumberRule("severity.congestion_saturation",
               "divisor del componente de congestión",
               minimum=0.0, exclusive_min=True, maximum=1e6),
    NumberRule("severity.priority_bands.critical", "corte inferior de la banda crítica",
               minimum=0.0, maximum=100.0),
    NumberRule("severity.priority_bands.high", "corte inferior de la banda alta",
               minimum=0.0, maximum=100.0),
    NumberRule("severity.priority_bands.medium", "corte inferior de la banda media",
               minimum=0.0, maximum=100.0),

    # ---- clash matrix: a tolerance is a distance, never negative
    NumberRule("clash_matrix.default_tolerance_m", "tolerancia por defecto de los tests",
               minimum=0.0, maximum=100.0),

    # ---- noise filters: distances and ratios
    NumberRule("noise_filter.max_penetration_m", "penetración máxima considerada ruido",
               minimum=0.0, maximum=100.0),
    NumberRule("noise_filter.min_penetration_m", "penetración mínima considerada real",
               minimum=0.0, maximum=100.0),

    # ---- root cause: sample sizes and confidences
    NumberRule("root_cause.min_clashes", "tamaño mínimo de muestra para declarar una causa",
               minimum=1, integer=True, maximum=100000),
    NumberRule("root_cause.min_confidence", "confianza mínima, es una probabilidad",
               minimum=0.0, maximum=1.0),
)

# Weight maps: every value is a weight, so the rule applies to whatever keys
# the profile declares rather than to a fixed list.
WEIGHT_MAPS: tuple[tuple[str, str], ...] = (
    ("severity.weights", "peso de un componente de severidad"),
    ("movability.by_category", "movilidad por categoría, 0 = inamovible, 1 = libre"),
    ("criticality_matrix", "criticidad del par de disciplinas"),
)


def _dig(raw: Any, path: str) -> tuple[bool, Any]:
    """(present, value) for a dotted path. Never raises."""
    node = raw
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def check_numbers(raw: dict[str, Any]) -> list[Problem]:
    """Every declared numeric field, against its bound.

    Absent fields are not checked: the engine's defaults apply and they are
    known good. A field the author DECLARED is checked whatever the default
    would have been — that is the whole gap, because a default only protects
    a value nobody wrote.
    """
    problems: list[Problem] = []
    for rule in NUMBER_RULES:
        present, value = _dig(raw, rule.path)
        if present:
            problems.extend(rule.check(value))

    for path, why in WEIGHT_MAPS:
        present, table = _dig(raw, path)
        if not present:
            continue
        if not isinstance(table, dict):
            problems.append(Problem(TYPE_ERROR, path, "debe ser un objeto de pesos"))
            continue
        for key, value in table.items():
            if str(key).startswith("_"):
                continue
            problems.extend(
                NumberRule(f"{path}.{key}", why, minimum=0.0, maximum=1e6).check(value)
            )
    return problems


def check_relations(raw: dict[str, Any]) -> list[Problem]:
    """Rules that involve more than one field.

    A value can be individually reasonable and jointly impossible: bands of
    30/50/70 are each inside 0–100 and, read as critical/high/medium, invert
    the scale so nothing is ever critical.
    """
    problems: list[Problem] = []

    # --- severity weights must sum to one, within a stated tolerance
    present, weights = _dig(raw, "severity.weights")
    if present and isinstance(weights, dict):
        numeric = [
            v for k, v in weights.items()
            if not str(k).startswith("_") and isinstance(v, (int, float))
            and not isinstance(v, bool) and math.isfinite(v)
        ]
        total = sum(numeric)
        if numeric and abs(total - 1.0) > 0.01:
            problems.append(Problem(
                RELATION_ERROR, "severity.weights",
                f"los pesos suman {total:.4f} y deben sumar 1.00 ±0.01; "
                "la puntuación quedaría fuera de 0-100"))

    # --- priority bands descend
    bands = []
    for name in ("critical", "high", "medium"):
        present, value = _dig(raw, f"severity.priority_bands.{name}")
        if present and isinstance(value, (int, float)) and not isinstance(value, bool):
            bands.append((name, float(value)))
    if len(bands) >= 2:
        values = [v for _, v in bands]
        if values != sorted(values, reverse=True):
            problems.append(Problem(
                RELATION_ERROR, "severity.priority_bands",
                "deben ir de mayor a menor: critical > high > medium; llegaron "
                + ", ".join(f"{n}={v:g}" for n, v in bands)))

    # --- noise thresholds do not invert
    lo_present, lo = _dig(raw, "noise_filter.min_penetration_m")
    hi_present, hi = _dig(raw, "noise_filter.max_penetration_m")
    if (lo_present and hi_present
            and isinstance(lo, (int, float)) and isinstance(hi, (int, float))
            and not isinstance(lo, bool) and not isinstance(hi, bool)
            and math.isfinite(lo) and math.isfinite(hi) and lo > hi):
        problems.append(Problem(
            RELATION_ERROR, "noise_filter",
            f"min_penetration_m ({lo}) no puede superar max_penetration_m ({hi})"))

    # --- the clustering radius has to fit inside the span it feeds
    eps_present, eps = _dig(raw, "clustering.eps_m")
    span_present, span = _dig(raw, "clustering.max_cluster_span_m")
    if (eps_present and span_present
            and isinstance(eps, (int, float)) and isinstance(span, (int, float))
            and not isinstance(eps, bool) and not isinstance(span, bool)
            and math.isfinite(eps) and math.isfinite(span) and eps > span):
        problems.append(Problem(
            RELATION_ERROR, "clustering",
            f"eps_m ({eps}) supera max_cluster_span_m ({span}): cada grupo nacería "
            "por encima del máximo y habría que partirlo entero"))

    return problems


def check_references(raw: dict[str, Any]) -> list[Problem]:
    """Names that must point at something declared in the same profile."""
    problems: list[Problem] = []

    declared = set()
    disciplines = raw.get("disciplines")
    if isinstance(disciplines, dict):
        declared = {str(k) for k in disciplines if not str(k).startswith("_")}

    # --- the criticality matrix keys are "A|B" discipline pairs
    matrix = raw.get("criticality_matrix")
    if isinstance(matrix, dict) and declared:
        for key in matrix:
            if str(key).startswith("_"):
                continue
            parts = str(key).split("|")
            if len(parts) != 2:
                problems.append(Problem(
                    REFERENCE_ERROR, f"criticality_matrix.{key}",
                    "la clave debe ser 'DISCIPLINA|DISCIPLINA'"))
                continue
            for part in parts:
                if part not in declared:
                    problems.append(Problem(
                        REFERENCE_ERROR, f"criticality_matrix.{key}",
                        f"la disciplina «{part}» no está declarada en 'disciplines'"))

    # --- clash matrix pairs reference declared disciplines
    pairs = _dig(raw, "clash_matrix.pairs")[1]
    if isinstance(pairs, list) and declared:
        for index, pair in enumerate(pairs):
            if not isinstance(pair, dict):
                problems.append(Problem(
                    TYPE_ERROR, f"clash_matrix.pairs[{index}]", "debe ser un objeto"))
                continue
            for side in ("a", "b"):
                value = pair.get(side)
                if isinstance(value, str) and value and value not in declared:
                    problems.append(Problem(
                        REFERENCE_ERROR, f"clash_matrix.pairs[{index}].{side}",
                        f"la disciplina «{value}» no está declarada"))

    # --- empty identifiers
    if isinstance(disciplines, dict):
        for code in disciplines:
            if not str(code).strip():
                problems.append(Problem(
                    REFERENCE_ERROR, "disciplines",
                    "una disciplina tiene el código vacío"))

    return problems


def check_aliases(raw: dict[str, Any]) -> list[Problem]:
    """Alias chains must terminate.

    A cycle is not a strange input to guard against on principle: it is what a
    profile ends up with after two people each redirect a discipline at the
    other's, and any consumer that follows the chain then loops forever.
    """
    problems: list[Problem] = []
    aliases = raw.get("discipline_aliases")
    if not isinstance(aliases, dict):
        return problems

    for start in aliases:
        if str(start).startswith("_"):
            continue
        seen = [str(start)]
        current = aliases.get(start)
        while isinstance(current, str) and current in aliases:
            if current in seen:
                problems.append(Problem(
                    RELATION_ERROR, f"discipline_aliases.{start}",
                    "los alias forman un ciclo: " + " → ".join(seen + [current])))
                break
            seen.append(current)
            current = aliases.get(current)
            if len(seen) > 64:
                problems.append(Problem(
                    RELATION_ERROR, f"discipline_aliases.{start}",
                    "la cadena de alias es demasiado larga; probablemente hay un ciclo"))
                break
    return problems


def check_colours(raw: dict[str, Any]) -> list[Problem]:
    """RGB components are bytes, and a 256 is not a byte."""
    problems: list[Problem] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            keys = {str(k).lower() for k in node}
            if {"r", "g", "b"} <= keys:
                for component in ("r", "g", "b"):
                    value = node.get(component, node.get(component.upper()))
                    problems.extend(NumberRule(
                        f"{path}.{component}", "componente RGB",
                        minimum=0, maximum=255, integer=True).check(value))
                return
            for key, value in node.items():
                if str(key).startswith("_"):
                    continue
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(raw, "")
    return problems


# ----------------------------------------------------------------- contract
#
# The canonical inventory lives in profiles/profile_contract.json so that the
# add-in's mirror (ProfileRules.cs) can be tested against the very same file.
# This module reads it once; the tests on both sides assert that what the code
# consumes and what the contract declares are the same set.

CONTRACT_PATH = Path(__file__).resolve().parent / "profiles" / "profile_contract.json"
CONTRACT_SCHEMA = "naviscoord.profile-contract/1"


def load_contract(path: Path | None = None) -> dict[str, Any]:
    """The contract document. Raises rather than inventing one."""
    raw = json.loads((path or CONTRACT_PATH).read_text(encoding="utf-8"))
    if raw.get("schema") != CONTRACT_SCHEMA:
        raise ValueError(f"contrato con esquema inesperado: {raw.get('schema')!r}")
    return raw


def known_root_keys(contract: dict[str, Any] | None = None) -> frozenset[str]:
    document = contract or load_contract()
    return frozenset(document["metadata_keys"]) | frozenset(document["sections"])


_KNOWN_ROOT: frozenset[str] | None = None


def check_unknown(raw: dict[str, Any]) -> list[Problem]:
    """Root keys the contract does not declare.

    A typo'd section name used to be silently ignored, which is how a profile
    "has no effect" for a week: `severty` parses, validates and changes
    nothing. Underscore keys are comments everywhere, and `extensions` is the
    one declared free area — a project that needs to carry its own data has a
    place for it that no future section name can collide with.
    """
    global _KNOWN_ROOT
    if _KNOWN_ROOT is None:
        _KNOWN_ROOT = known_root_keys()
    problems: list[Problem] = []
    for key in raw:
        name = str(key)
        if name.startswith("_") or name in _KNOWN_ROOT:
            continue
        problems.append(Problem(
            UNKNOWN_ERROR, name,
            "sección desconocida: no está en el contrato del perfil. Los datos "
            "propios van dentro de «extensions»; ¿un typo?"))
    return problems


def validate_semantics(raw: dict[str, Any]) -> list[Problem]:
    """Every semantic rule, in one call. Never mutates the profile."""
    problems: list[Problem] = []
    problems.extend(check_unknown(raw))
    problems.extend(check_numbers(raw))
    problems.extend(check_relations(raw))
    problems.extend(check_references(raw))
    problems.extend(check_aliases(raw))
    problems.extend(check_colours(raw))
    return problems
