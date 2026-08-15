"""Coordination matrix: who owes work to whom, and how bad it is.

A ranked list answers "what do I open first". It does not answer "which two
teams need to sit down together", which is the question that decides who gets
called to the meeting. The matrix does — one cell per discipline pair, so the
worst intersection in the project is visible at a glance instead of inferred
from scrolling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .analysis.pipeline import AnalysisResult
from .profile import Profile


@dataclass(slots=True)
class Cell:
    a: str
    b: str
    issues: int = 0
    clashes: int = 0
    critical: int = 0
    high: int = 0
    max_severity: float = 0.0
    total_severity: float = 0.0
    responsible: dict[str, int] = field(default_factory=dict)

    @property
    def worst_owner(self) -> str:
        if not self.responsible:
            return ""
        return max(self.responsible.items(), key=lambda kv: kv[1])[0]

    def to_json(self) -> dict[str, Any]:
        return {
            "a": self.a,
            "b": self.b,
            "issues": self.issues,
            "clashes": self.clashes,
            "critical": self.critical,
            "high": self.high,
            "max_severity": round(self.max_severity, 1),
            "total_severity": round(self.total_severity, 1),
            "responsible": self.worst_owner,
        }


@dataclass(slots=True)
class CoordinationMatrix:
    disciplines: list[str]
    cells: dict[tuple[str, str], Cell]

    def get(self, a: str, b: str) -> Cell | None:
        return self.cells.get(tuple(sorted((a, b))))  # type: ignore[arg-type]

    def ranked(self) -> list[Cell]:
        """Worst intersections first — the agenda for the next session."""
        return sorted(
            self.cells.values(),
            key=lambda c: (-c.critical, -c.high, -c.total_severity),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "disciplines": self.disciplines,
            "cells": [cell.to_json() for cell in self.ranked()],
        }


def build_matrix(result: AnalysisResult, profile: Profile) -> CoordinationMatrix:
    cells: dict[tuple[str, str], Cell] = {}
    seen: set[str] = set()

    # Built from decisions, not from every issue: a folded issue is closed by
    # its representative, and counting it again would inflate the cell that
    # its owner has to answer for.
    for issue in result.decisions:
        pair = tuple(sorted(d for d in issue.discipline_pair if d))
        if len(pair) != 2:
            # Same-discipline work still belongs on the matrix diagonal.
            single = pair[0] if pair else "OTRO"
            pair = (single, single)

        seen.update(pair)
        cell = cells.get(pair)
        if cell is None:
            cell = Cell(a=pair[0], b=pair[1])
            cells[pair] = cell

        cell.issues += 1
        cell.clashes += issue.clash_count
        cell.total_severity += issue.severity
        cell.max_severity = max(cell.max_severity, issue.severity)
        if issue.priority == "critical":
            cell.critical += 1
        elif issue.priority == "high":
            cell.high += 1
        if issue.responsible:
            cell.responsible[issue.responsible] = cell.responsible.get(issue.responsible, 0) + 1

    order = [d for d in profile.disciplines if d in seen]
    order.extend(sorted(d for d in seen if d not in order))
    return CoordinationMatrix(disciplines=order, cells=cells)


def render_text(matrix: CoordinationMatrix, profile: Profile) -> str:
    """Console rendering, for when the PDF is overkill."""
    out: list[str] = []
    order = matrix.disciplines
    width = 8

    header = "".ljust(6) + "".join(d.rjust(width) for d in order)
    out.append(header)
    out.append("-" * len(header))

    for a in order:
        row = [a.ljust(6)]
        for b in order:
            cell = matrix.get(a, b)
            row.append((str(cell.issues) if cell else "·").rjust(width))
        out.append("".join(row))

    out.append("")
    out.append("Peores intersecciones:")
    for cell in matrix.ranked()[:6]:
        if cell.issues == 0:
            continue
        out.append(
            f"  {profile.label(cell.a)} × {profile.label(cell.b)}: "
            f"{cell.issues} problemas ({cell.critical} críticos), "
            f"mueve {profile.label(cell.worst_owner)}"
        )
    return "\n".join(out)
