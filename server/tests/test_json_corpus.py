"""The JSON corpus both ends must agree on.

The add-in and the server each parse profiles, and for years they used
different readers: Python's ``json`` with its default ``parse_constant``, and a
hand-written C# scanner that accepted ``1..2``, ``+1``, ``01`` and ``1e``. A
document one side installs and the other refuses is a split-brain profile, and
the checksum that is supposed to prove they agree is computed *after* parsing —
so it proves nothing about the disagreement.

This file is the Python half. ``JsonCorpusTests`` in the add-in reads the same
fixture and asserts the same decisions, so a drift in either reader fails on
both sides rather than silently changing what one of them accepts.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from naviscoord.profile import (
    MAX_PROFILE_DEPTH,
    ProfileRejected,
    canonical_text,
    checksum_of,
    loads_strict,
)

CORPUS = Path(__file__).parent / "fixtures" / "json-corpus.json"
_CASES = json.loads(CORPUS.read_text(encoding="utf-8"))


def _ids(cases):
    return [c["name"] for c in cases]


@pytest.mark.parametrize("case", _CASES["valid"], ids=_ids(_CASES["valid"]))
def test_valid_corpus_is_accepted(case):
    parsed = loads_strict(case["text"])
    assert isinstance(parsed, dict)
    # A document that parses must also canonicalise and hash: anything that
    # cannot be described cannot be identified.
    assert len(checksum_of(parsed)) == 16


@pytest.mark.parametrize("case", _CASES["invalid"], ids=_ids(_CASES["invalid"]))
def test_invalid_corpus_is_refused(case):
    with pytest.raises(ProfileRejected) as raised:
        loads_strict(case["text"])
    assert raised.value.code, "toda negativa lleva un código"


def test_every_invalid_case_declares_a_category():
    for case in _CASES["invalid"]:
        assert case.get("code"), f"{case['name']} no declara categoría esperada"


def test_categories_match_the_declared_one():
    """Where the corpus names a category, Python must produce it.

    Kept separate from the acceptance test because agreeing on *reject* is the
    contract, and agreeing on *why* is the finer claim: a case whose category
    is genuinely ambiguous between the two readers may still be listed as
    invalid, and this test is where that shows up rather than in a silent
    mismatch.
    """
    mismatches = []
    for case in _CASES["invalid"]:
        try:
            loads_strict(case["text"])
        except ProfileRejected as exc:
            if exc.code != case["code"]:
                mismatches.append((case["name"], case["code"], exc.code))
    assert not mismatches, f"categorías divergentes: {mismatches}"


# ------------------------------------------------------------- non-finite


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_json_constants_are_refused(token):
    with pytest.raises(ProfileRejected):
        loads_strict('{"weight": %s}' % token)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_built_in_memory_gets_no_checksum(value):
    """A non-finite that never passed through the parser still cannot hash.

    This is the case the old canonicaliser turned into ``null``: a profile
    holding NaN and one holding null produced the same checksum while the two
    servers held different numbers. Two things that hash alike and behave
    differently is exactly what a checksum exists to rule out.
    """
    assert not math.isfinite(value)
    with pytest.raises(ProfileRejected):
        canonical_text({"weight": value})
    with pytest.raises(ProfileRejected):
        checksum_of({"weight": value})


def test_nested_non_finite_is_found():
    with pytest.raises(ProfileRejected):
        checksum_of({"a": {"b": [1.0, float("inf")]}})


# ------------------------------------------------------------------ depth


def _nested(depth: int) -> str:
    return '{"a":' * depth + "1" + "}" * depth


def test_maximum_depth_is_accepted():
    assert isinstance(loads_strict(_nested(MAX_PROFILE_DEPTH)), dict)


def test_beyond_maximum_depth_is_refused_without_crashing():
    with pytest.raises(ProfileRejected) as raised:
        loads_strict(_nested(MAX_PROFILE_DEPTH + 1))
    assert raised.value.code == "json_too_deep"


def test_very_deep_document_does_not_recurse_to_death():
    # Far past the limit: the refusal must arrive as a value error, not as an
    # interpreter-level recursion failure the caller cannot handle.
    with pytest.raises(ProfileRejected):
        loads_strict(_nested(5000))


# ------------------------------------------------------------ canonical form


def test_canonical_is_stable_across_key_order():
    a = loads_strict('{"x": 1, "y": {"p": 2, "q": 3}}')
    b = loads_strict('{"y": {"q": 3, "p": 2}, "x": 1}')
    assert canonical_text(a) == canonical_text(b)
    assert checksum_of(a) == checksum_of(b)


def test_comment_keys_do_not_change_identity():
    a = loads_strict('{"x": 1}')
    b = loads_strict('{"_nota": "lo que sea", "x": 1}')
    assert checksum_of(a) == checksum_of(b)


def test_integral_floats_and_ints_agree():
    assert canonical_text({"a": 1}) == canonical_text({"a": 1.0})
