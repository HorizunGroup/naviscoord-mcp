"""Bounds on what an MCP caller may ask for, checked before anything moves.

An MCP tool argument arrives from a language model, and a model that has drifted
one token asks for ``limit=-1``. In Python that is not an error — it is a slice
from the end — so ``issues[:limit]`` silently returns everything except the last
one, and the caller is told it got the top −1. ``cell_size_m=0`` is worse: it
reaches a grid discretisation and divides by zero inside the engine, several
frames away from the argument that caused it.

The rule is the same one the profile rules follow: refuse at the boundary, with
the parameter named, rather than clamping to something plausible. A clamped
argument produces a confident answer to a question nobody asked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

INVALID_ARGUMENTS = "invalid_arguments"


class ArgumentError(ValueError):
    """One or more arguments the tool refuses to act on."""

    def __init__(self, problems: list[dict[str, Any]]) -> None:
        detail = "; ".join(f"{p['parameter']}: {p['detail']}" for p in problems)
        super().__init__(detail)
        self.problems = problems

    def to_json(self) -> dict[str, Any]:
        return {
            "error": INVALID_ARGUMENTS,
            "detail": str(self),
            "problems": self.problems,
            "hint": (
                "Corrige los parámetros señalados y repite. No se llamó al "
                "complemento ni se tocó el estado."
            ),
        }


@dataclass
class _Collector:
    problems: list[dict[str, Any]]

    def fail(self, parameter: str, detail: str, value: Any) -> None:
        self.problems.append({
            "parameter": parameter,
            "detail": detail,
            # Truncated: a rejected argument can be a 50 000-element list, and
            # echoing it back into an error message helps nobody.
            "received": repr(value)[:120],
        })


def _is_number(value: Any) -> bool:
    # bool first: `isinstance(True, int)` is True, and `limit=True` would
    # otherwise pass every bound as 1.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class Arguments:
    """Accumulates every complaint, then raises once.

    Reporting all of them together matters: a caller that fixes ``limit`` only
    to be told about ``top`` on the next attempt learns the tool one refusal at
    a time.
    """

    def __init__(self) -> None:
        self._c = _Collector(problems=[])

    # ------------------------------------------------------------ numbers

    def count(self, name: str, value: Any, *, minimum: int = 0,
              maximum: int = 100000) -> int:
        """A whole number of things. Negative is never "from the end" here."""
        if not _is_number(value):
            self._c.fail(name, "debe ser un número entero", value)
            return minimum
        if isinstance(value, float) and (not math.isfinite(value) or value != int(value)):
            self._c.fail(name, "debe ser un entero finito", value)
            return minimum
        number = int(value)
        if number < minimum:
            self._c.fail(
                name,
                f"no puede ser menor que {minimum}"
                + (" (un valor negativo cortaría la lista por el otro extremo)"
                   if minimum >= 0 and number < 0 else ""),
                value)
            return minimum
        if number > maximum:
            self._c.fail(name, f"no puede superar {maximum}", value)
            return maximum
        return number

    def positive(self, name: str, value: Any, *, maximum: float = 1e6) -> float:
        """A magnitude that participates in a division or a radius."""
        if not _is_number(value):
            self._c.fail(name, "debe ser un número", value)
            return 1.0
        number = float(value)
        if not math.isfinite(number):
            self._c.fail(name, "debe ser finito", value)
            return 1.0
        if number <= 0.0:
            self._c.fail(name, "debe ser mayor que cero: se usa como divisor o radio", value)
            return 1.0
        if number > maximum:
            self._c.fail(name, f"no puede superar {maximum}", value)
            return maximum
        return number

    def ratio(self, name: str, value: Any) -> float:
        """A proportion between 0 and 1."""
        if not _is_number(value):
            self._c.fail(name, "debe ser un número entre 0 y 1", value)
            return 0.0
        number = float(value)
        if not math.isfinite(number) or number < 0.0 or number > 1.0:
            self._c.fail(name, "debe estar entre 0 y 1", value)
            return 0.0
        return number

    def pixels(self, name: str, value: Any, *, minimum: int = 64,
               maximum: int = 8192) -> int:
        return self.count(name, value, minimum=minimum, maximum=maximum)

    # ------------------------------------------------------------ strings

    def text(self, name: str, value: Any, *, required: bool = False,
             max_length: int = 512) -> str:
        if value is None:
            value = ""
        if not isinstance(value, str):
            self._c.fail(name, "debe ser texto", value)
            return ""
        if required and not value.strip():
            self._c.fail(name, "es obligatorio y llegó vacío", value)
            return ""
        if len(value) > max_length:
            self._c.fail(name, f"no puede superar {max_length} caracteres", value[:40])
            return value[:max_length]
        return value

    def choice(self, name: str, value: Any, allowed: Iterable[str], *,
               required: bool = False) -> str:
        options = list(allowed)
        text = self.text(name, value, required=required)
        if not text:
            return text
        if text not in options:
            self._c.fail(
                name,
                "valor desconocido; se admiten " + ", ".join(sorted(options)),
                value)
            return ""
        return text

    def id_list(self, name: str, value: Any, *, max_items: int = 20000,
                required: bool = False) -> list[str]:
        """A list of identifiers: bounded, non-empty entries, no blanks."""
        if value is None:
            value = []
        if not isinstance(value, (list, tuple)):
            self._c.fail(name, "debe ser una lista", value)
            return []
        items = list(value)
        if len(items) > max_items:
            self._c.fail(name, f"no puede llevar más de {max_items} elementos",
                         f"{len(items)} elementos")
            return items[:max_items]
        if required and not items:
            self._c.fail(name, "es obligatorio y llegó vacío", value)
            return []
        cleaned: list[str] = []
        for index, item in enumerate(items):
            if not isinstance(item, str) or not item.strip():
                self._c.fail(f"{name}[{index}]", "cada elemento debe ser un texto no vacío", item)
                continue
            cleaned.append(item)
        return cleaned

    def flag(self, name: str, value: Any) -> bool:
        if not isinstance(value, bool):
            self._c.fail(name, "debe ser true o false", value)
            return False
        return value

    # ------------------------------------------------------------- finish

    def raise_if_invalid(self) -> None:
        if self._c.problems:
            raise ArgumentError(self._c.problems)

    @property
    def problems(self) -> list[dict[str, Any]]:
        return list(self._c.problems)


# Values Navisworks accepts for a clash result's status. Anything else is a
# typo, and sending it produces a refusal several layers away from the caller.
CLASH_STATUSES = ("New", "Active", "Reviewed", "Approved", "Resolved")

# The priority bands a work plan can be filtered by.
PRIORITIES = ("critical", "high", "medium", "low")
