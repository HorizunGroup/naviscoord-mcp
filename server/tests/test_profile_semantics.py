"""A profile that parses is not a profile that works.

``cluster_size_saturation: 1`` is perfect JSON. It also makes the severity
scorer evaluate ``math.log(count) / math.log(1)`` — a division by zero — on the
first cluster holding more than one clash. The schema described shapes, and the
engine's defaults only apply to fields that are ABSENT, so a field declared with
an impossible value travelled all the way to the arithmetic.

The corpus here is the semantic twin of ``json-corpus.json``: same file read by
Python and by the add-in, same decision, same category and path. Messages are
deliberately excluded from the parity contract — the two sides write prose
differently, and pinning sentences would make a translation a test failure.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from naviscoord.profile import Profile
from naviscoord.profilerules import (
    RANGE_ERROR,
    RELATION_ERROR,
    TYPE_ERROR,
    NUMBER_RULES,
    validate_semantics,
)

CORPUS = Path(__file__).parent / "fixtures" / "profile-semantics.json"
_CASES = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]


def _default_raw() -> dict[str, Any]:
    return json.loads(json.dumps(Profile.load().raw))


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge a corpus patch onto the default profile."""
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _apply(case: dict[str, Any]) -> dict[str, Any]:
    return _merge(_default_raw(), case["patch"])


# ------------------------------------------------------------------ corpus


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_the_corpus_decision(case: dict[str, Any]) -> None:
    problems = validate_semantics(_apply(case))
    if case["accepted"]:
        assert not problems, f"debía aceptarse y dio: {[str(p) for p in problems]}"
    else:
        assert problems, "debía rechazarse y no dio ningún problema"


@pytest.mark.parametrize(
    "case", [c for c in _CASES if not c["accepted"]],
    ids=[c["name"] for c in _CASES if not c["accepted"]],
)
def test_the_corpus_category_and_path(case: dict[str, Any]) -> None:
    """Category and path are the parity contract; the message is not."""
    problems = validate_semantics(_apply(case))
    matched = [
        p for p in problems
        if p.code == case["code"] and p.path == case["path"]
    ]
    assert matched, (
        f"esperaba {case['code']} en {case['path']}; salió "
        + str([(p.code, p.path) for p in problems])
    )


def test_every_rejected_case_declares_a_category_and_path() -> None:
    for case in _CASES:
        if case["accepted"]:
            continue
        assert case.get("code"), f"{case['name']} no declara categoría"
        assert case.get("path"), f"{case['name']} no declara ruta"


# ------------------------------------------------------------- divisors


class TestEveryDivisor:
    """The audit: no accepted profile can make a known divisor zero."""

    def test_the_shipped_profiles_all_validate(self) -> None:
        folder = Path(__file__).resolve().parents[1] / "naviscoord" / "profiles"
        profiles = sorted(p for p in folder.glob("*.json") if p.name != "profile_contract.json")
        assert profiles, "No shipped profiles found"
        for path in profiles:
            raw = json.loads(path.read_text(encoding="utf-8"))
            assert not validate_semantics(raw), f"{path.name} no valida"

    def test_cluster_size_saturation_can_never_make_log_zero(self) -> None:
        """The reproduction, as an assertion about the bound rather than a case."""
        rule = next(r for r in NUMBER_RULES
                    if r.path == "severity.cluster_size_saturation")
        assert rule.minimum == 1.0 and rule.exclusive_min, (
            "la cota debe excluir 1: log(1) = 0 y ese es el divisor"
        )
        # Anything the rule accepts gives a usable divisor.
        for value in (1.0000001, 2, 25, 1000):
            assert not rule.check(value), f"{value} debía aceptarse"
            assert math.log(value) > 0.0

    @pytest.mark.parametrize(
        "path",
        [
            "severity.cluster_size_saturation",
            "severity.congestion_saturation",
            "clustering.eps_m",
            "clustering.max_cluster_span_m",
        ],
    )
    def test_every_divisor_excludes_zero(self, path: str) -> None:
        rule = next(r for r in NUMBER_RULES if r.path == path)
        assert rule.minimum is not None and rule.exclusive_min, (
            f"{path} participa en una división y su cota debe ser estricta"
        )
        assert rule.check(0), "el cero debe rechazarse"
        assert rule.check(-1), "y un negativo también"

    def test_every_rule_names_the_operation_it_guards(self) -> None:
        """The inventory is only an audit if each entry says why."""
        for rule in NUMBER_RULES:
            assert rule.why.strip(), f"{rule.path} no declara qué operación protege"


# --------------------------------------------------------------- typing


class TestTypeDiscipline:
    def test_a_boolean_is_not_a_number(self) -> None:
        """`isinstance(True, int)` is True in Python, so this needs saying."""
        assert isinstance(True, int)
        raw = _merge(_default_raw(), {"severity": {"weights": {"cluster_size": True}}})
        problems = validate_semantics(raw)
        assert any(p.code == TYPE_ERROR and "cluster_size" in p.path for p in problems)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_built_in_memory_is_refused(self, value: float) -> None:
        """It never passed the parser, so only the semantic pass can catch it."""
        raw = _merge(_default_raw(), {"clustering": {"eps_m": value}})
        problems = validate_semantics(raw)
        assert any(p.code == RANGE_ERROR and p.path == "clustering.eps_m"
                   for p in problems)

    def test_a_negative_weight_is_refused(self) -> None:
        raw = _merge(_default_raw(), {"severity": {"weights": {"cluster_size": -0.3}}})
        assert any(p.code == RANGE_ERROR for p in validate_semantics(raw))

    def test_weights_that_do_not_sum_to_one(self) -> None:
        raw = _default_raw()
        weights = dict(raw["severity"]["weights"])
        first = next(iter(weights))
        weights[first] = weights[first] + 0.5
        raw = _merge(raw, {"severity": {"weights": weights}})
        problems = validate_semantics(raw)
        assert any(p.code == RELATION_ERROR and p.path == "severity.weights"
                   for p in problems)


# ------------------------------------------------------------ properties


class TestValidationIsPure:
    def test_validation_never_mutates_the_profile(self) -> None:
        raw = _default_raw()
        before = json.dumps(raw, sort_keys=True)
        validate_semantics(raw)
        assert json.dumps(raw, sort_keys=True) == before

    def test_a_rejected_profile_leaves_the_previous_one_in_place(self) -> None:
        from naviscoord.profile import checksum_of

        good = checksum_of(Profile.load().raw)
        raw = _merge(_default_raw(), {"clustering": {"eps_m": 0}})
        assert validate_semantics(raw), "el parche debe ser inválido"
        # Nothing about validating a bad profile disturbs a loaded one.
        assert checksum_of(Profile.load().raw) == good


class TestDeterministicFuzz:
    """Extreme values and wrong types, generated the same way every run.

    Not random: a fuzz test that cannot be reproduced is a test that reports a
    failure nobody can act on. The seed is fixed and the corpus of hostile
    values is explicit.
    """

    HOSTILE: tuple[Any, ...] = (
        0, -1, -0.0, 1e308, -1e308, float("nan"), float("inf"), float("-inf"),
        True, False, "1", "", None, [], {}, 2 ** 63, -(2 ** 63),
    )

    def test_no_hostile_value_escapes_its_rule(self) -> None:
        escaped: list[tuple[str, Any]] = []
        for rule in NUMBER_RULES:
            for value in self.HOSTILE:
                problems = rule.check(value)
                acceptable = (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and (rule.minimum is None
                         or (value > rule.minimum if rule.exclusive_min
                             else value >= rule.minimum))
                    and (rule.maximum is None
                         or (value < rule.maximum if rule.exclusive_max
                             else value <= rule.maximum))
                    and (not rule.integer or float(value) == int(value))
                )
                if acceptable != (not problems):
                    escaped.append((rule.path, value))
        assert not escaped, f"valores mal clasificados: {escaped}"

    def test_hostile_values_never_raise(self) -> None:
        """A validator that throws is a validator that fails open."""
        for rule in NUMBER_RULES:
            for value in self.HOSTILE:
                rule.check(value)   # must not raise
