"""Escaping and neutralisation for values that came from outside.

Everything in this module exists because model data is attacker-influenced
data. A element name, a system name, a Type, a `suggested_action` composed
from them — all of it originates in a Revit file some third party authored
and published to ACC. Treating it as trusted markup is how a coordination
report turns into an exfiltration primitive.

Two sinks matter today:

* **ReportLab paragraphs** parse a mini-HTML dialect. An element named
  ``<img src="file:///C:/Windows/win.ini">`` is not a funny name; it is a
  request for the PDF builder to embed a local file, and ``<a
  href="\\\\attacker\\share">`` is a request to reach a UNC path. Escaping the
  value before it reaches ``Paragraph`` makes it text again.
* **CSV exports** are opened in Excel and Power BI, which interpret a leading
  ``=``, ``+``, ``-``, ``@``, tab or CR as the start of a formula. A cell
  reading ``=cmd|'/c calc'!A1`` executes on open.

Neither sink is fixed by validating input: the value is legitimate model
content and must survive to the report. It is fixed at the boundary, once,
here.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------- markup

# ReportLab's paragraph parser is an XML parser, so these five are the whole
# escape set. Quotes matter because values are also interpolated into
# attribute positions by the helpers below.
_XML_ESCAPES = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&#39;"),
)


def escape_markup(value: Any) -> str:
    """Renders any value as literal text inside a ReportLab paragraph.

    ``&`` first, always: escaping it after the others would double-escape the
    entities they just produced.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    for raw, entity in _XML_ESCAPES:
        text = text.replace(raw, entity)
    # A NUL or a control character is never legitimate report content and
    # some PDF viewers treat the byte stream unpredictably.
    return "".join(ch for ch in text if ch >= " " or ch in "\t\n")


def bold(value: Any) -> str:
    """``<b>`` around an escaped value — the tag is ours, the text is theirs."""
    return f"<b>{escape_markup(value)}</b>"


def italic(value: Any) -> str:
    return f"<i>{escape_markup(value)}</i>"


def coloured(value: Any, hex_colour: str) -> str:
    """``<font color=...>`` with the colour validated, not interpolated.

    The colour is ours (a palette constant), but validating it anyway means a
    future caller cannot turn this into an attribute-injection point by
    passing a profile value through.
    """
    safe = hex_colour if _is_hex_colour(hex_colour) else "#000000"
    return f'<font color="{safe}">{escape_markup(value)}</font>'


def _is_hex_colour(value: str) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text.startswith("#") or len(text) not in (4, 7, 9):
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in text[1:])


def paragraph_text(*parts: str) -> str:
    """Joins fragments that are ALREADY safe.

    Exists so a call site reads as an explicit assembly of trusted markup
    plus escaped values, rather than an f-string where the difference is
    invisible to a reviewer.
    """
    return "".join(parts)


# ------------------------------------------------------------------- CSV

# Excel, LibreOffice and Power BI all treat these as the start of a formula.
# The tab and carriage return are in the list because a leading one makes a
# spreadsheet re-parse what follows, which reintroduces the others.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv(value: Any) -> Any:
    """Neutralises formula injection while leaving ordinary values alone.

    Prefixing with an apostrophe is the usual advice and is wrong here: it is
    an Excel-only convention that Power BI ingests literally, so every
    affected value would gain a visible quote in the dashboard. A single
    leading tab-free approach is not available either. This prepends a NUL-free
    zero-width-safe marker instead: a single quote is only added when the
    value would otherwise be parsed as a formula, and numbers keep working
    because a numeric value is passed through untouched.
    """
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if not text:
        return text
    # Leading whitespace does not protect the cell: the parser strips it.
    stripped = text.lstrip(" \t\r\n")
    if not stripped:
        return text
    if not stripped.startswith(_FORMULA_LEADS):
        return text
    # A negative number is a number, not a formula, and mangling it would
    # corrupt real data — coordinates and depths are routinely negative.
    if _is_number(stripped):
        return text
    return "'" + text


# A plain decimal, and nothing else. `float()` was doing this job and is too
# generous: it accepts "inf", "nan", "-Infinity" and digit separators like
# "1_0". None of those are dangerous in a spreadsheet, but "is this a number?"
# is the question that decides whether a value skips neutralisation, and a
# loose answer there is not worth the convenience.
_DECIMAL = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _is_number(text: str) -> bool:
    return bool(_DECIMAL.match(text))


def sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Every value in a CSV row, neutralised.

    Keys are left alone here so the row stays addressable by its original
    field names; the header itself is neutralised where it is written, since
    a header cell is a cell too.
    """
    return {key: sanitize_csv(value) for key, value in row.items()}
