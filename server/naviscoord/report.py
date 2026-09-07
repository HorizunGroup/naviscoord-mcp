"""PDF coordination report.

One page per critical interference, each with the 3D image Navisworks
renders for it. That picture is the point: a row saying "pipe into beam at
X=34" gets argued with in a meeting; a picture of the pipe going through the
beam ends the argument and the conversation moves to who moves it.

Built with reportlab. Images are fetched one at a time from the addin, which
is slow — Navisworks has to position a camera and render for each — so the
caller sets how many issues get pages.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .analysis.pipeline import AnalysisResult
from .imaging import Capture, to_hex
from .matrix import CoordinationMatrix, build_matrix
from .model import Issue
from .paths import OutputPolicy
from .paths import policy as default_policy
from .plan import Plan, build_plan
from .profile import Profile
from .safety import bold, coloured, escape_markup, italic

PRIORITY_COLORS = {
    "critical": colors.HexColor("#B3261E"),
    "high": colors.HexColor("#E8710A"),
    "medium": colors.HexColor("#C8A200"),
    "low": colors.HexColor("#5F6368"),
}
PRIORITY_LABELS = {
    "critical": "CRÍTICO",
    "high": "ALTO",
    "medium": "MEDIO",
    "low": "BAJO",
}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "t", parent=base["Title"], fontSize=22, leading=26, spaceAfter=4
        ),
        "subtitle": ParagraphStyle(
            "st", parent=base["Normal"], fontSize=10, leading=13,
            textColor=colors.HexColor("#5F6368"),
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontSize=14, leading=17, spaceBefore=10, spaceAfter=6
        ),
        "body": ParagraphStyle(
            "b", parent=base["Normal"], fontSize=9.5, leading=13, alignment=TA_LEFT
        ),
        "small": ParagraphStyle(
            "s", parent=base["Normal"], fontSize=8, leading=11,
            textColor=colors.HexColor("#5F6368"),
        ),
        "action": ParagraphStyle(
            "a", parent=base["Normal"], fontSize=10, leading=14,
            textColor=colors.HexColor("#1A1A1A"), leftIndent=6,
        ),
    }


def build_report(
    path: str | Path,
    result: AnalysisResult,
    profile: Profile,
    *,
    document_title: str = "",
    max_issues: int = 25,
    image_fetcher: Callable[[str], bytes | None] | None = None,
    capture_fetcher: Callable[[Issue], Capture] | None = None,
    overwrite: bool = False,
    output_policy: OutputPolicy | None = None,
) -> dict[str, Any]:
    """Writes the PDF and reports what actually went into it.

    The destination goes through the output policy before a byte is written:
    the caller of this tool is a model holding a path string, and a report
    writer that honours any path is an arbitrary-file-write primitive.

    Two ways to supply pictures, and the difference is the whole point of this
    change. `image_fetcher` returns bytes for a clash id and is what the first
    version took; it still works and its images are embedded unexamined.
    `capture_fetcher` returns a `Capture`, which carries the frame that was
    used, the pixels that came back and the verdict on both — so a picture that
    does not actually show the clash can be counted and explained instead of
    printed.
    """
    authorised = (output_policy or default_policy()).resolve_file(path, overwrite=overwrite)
    target = authorised.path
    st = _styles()

    matrix = build_matrix(result, profile)
    issues = result.top(max_issues)

    # reportlab writes by path, not into a handle, so the destination is
    # reserved atomically up front and the PDF is BUILT INTO A TEMP FILE in
    # the same directory, then renamed into place. Building straight onto the
    # destination would leave a half-written PDF there if a render failed
    # halfway, and would lose a race against a concurrent writer.
    with authorised.staged_write() as staging:
        plan, tally = _compose(
            staging, result, profile, matrix, issues, document_title,
            image_fetcher, capture_fetcher, st)

    # Coverage means what the report resolves, not how many pages it has.
    # Counting the detail pages reported "6 of 1.375 (0%)" for a document
    # whose plan accounts for two thirds of the project — technically true
    # and completely misleading about what the reader is holding.
    planned = plan.covered_issues
    payload = {
        "path": str(target),
        "allowed_root": str(authorised.root),
        "overwrote_existing": authorised.existed,
        "packages": len(plan.packages),
        "decisions_planned": planned,
        "decisions_total": plan.total_issues,
        "tail_not_planned": plan.tail_issues,
        "issue_pages": len(issues),
        "images_embedded": tally.embedded,
        "images_failed": tally.failed,
        "coverage": (
            f"El plan cubre {planned} de {plan.total_issues} decisiones "
            f"({planned / max(plan.total_issues, 1):.0%}) en {len(plan.packages)} paquetes; "
            f"{len(issues)} llevan página de detalle con imagen."
        ),
    }
    payload["images"] = tally.to_json()
    return payload


@dataclass
class ImageTally:
    """The honest account of what happened to every requested picture.

    Four numbers, not one, because they call for different actions. A render
    that failed is a bridge or a licence problem; a render that was rejected is
    a modelling or a framing problem, and reporting both as "images_failed"
    sent people to restart Navisworks over a wall standing in front of a pipe.
    """

    requested: int = 0
    generated: int = 0
    embedded: int = 0
    failed: int = 0
    rejected: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    per_issue: list[dict[str, Any]] = field(default_factory=list)

    def record(self, issue: Issue, capture: Capture | None, embedded: bool) -> None:
        self.requested += 1
        if capture is None:
            self.failed += 1
            return

        if capture.png is not None:
            self.generated += 1
        else:
            self.failed += 1
        if embedded:
            self.embedded += 1
        elif capture.png is not None:
            self.rejected += 1

        for reason in capture.verdict.reasons:
            self.reasons[reason] = self.reasons.get(reason, 0) + 1

        entry = capture.to_json()
        entry["issue_id"] = issue.issue_id
        entry["embedded"] = embedded
        self.per_issue.append(entry)

    def to_json(self) -> dict[str, Any]:
        payload = {
            "requested": self.requested,
            "generated": self.generated,
            "embedded": self.embedded,
            "failed": self.failed,
            "rejected_for_quality": self.rejected,
            "rejection_reasons": dict(sorted(self.reasons.items())),
            "per_issue": self.per_issue,
        }
        warning = self.document_warning()
        if warning:
            payload["document_note"] = warning
        return payload

    def document_warning(self) -> str:
        """Said out loud when a clean document ends up marked as modified.

        Not a footnote in a per-image field: whoever ran the report is the
        person Navisworks will ask about saving when they close it, and they
        should hear it from here rather than be surprised an hour later.
        Measured, and unavoidable — see docs/IMAGENES.md.
        """
        dirtied = any(
            (entry.get("document") or {}).get("modified_before") is False
            and (entry.get("document") or {}).get("modified_after") is True
            for entry in self.per_issue
        )
        if not dirtied:
            return ""
        return (
            "El documento quedó marcado como modificado: renderizar exige mover "
            "la cámara y Navisworks cuenta eso como un cambio. No se guardó nada, "
            "la huella del documento no cambió y la cámara volvió a su sitio; "
            "puedes cerrar sin guardar."
        )


def _compose(
    target: Path,
    result: AnalysisResult,
    profile: Profile,
    matrix: CoordinationMatrix,
    issues: list[Issue],
    document_title: str,
    image_fetcher: Callable[[str], bytes | None] | None,
    capture_fetcher: Callable[[Issue], Capture] | None,
    st,
) -> tuple[Plan, ImageTally]:
    """Builds the PDF at `target`, a temp file, and reports what went in."""
    doc = SimpleDocTemplate(
        str(target),
        pagesize=landscape(A4),
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        # Document metadata is not markup, but it is still model-supplied
        # text landing in a PDF dictionary; keep it single-line and bounded.
        title=f"Coordinación — {_flatten(document_title)}",
        author="NavisCoord",
    )

    story: list[Any] = []
    story.extend(_cover(result, profile, document_title, st))
    story.append(PageBreak())

    # The plan goes before the matrix and long before the issue pages. A
    # client opening this wants to know what to do, not to browse a thousand
    # ranked rows; the detail pages are evidence for the plan, not the point.
    plan = build_plan(result, profile)
    story.extend(_plan_section(plan, profile, st))
    story.append(PageBreak())

    story.extend(_matrix_section(matrix, profile, st))
    story.append(PageBreak())

    tally = ImageTally()
    for index, issue in enumerate(issues):
        image: bytes | None = None
        capture: Capture | None = None

        if capture_fetcher and issue.clash_ids:
            try:
                capture = capture_fetcher(issue)
            except Exception as exc:
                capture = Capture(
                    clash_guid=issue.image_clash_id(), error=f"{type(exc).__name__}: {exc}"
                )
            image = capture.png if capture.accepted else None
            tally.record(issue, capture, embedded=image is not None)
        elif image_fetcher and issue.clash_ids:
            try:
                image = image_fetcher(issue.image_clash_id())
            except Exception:
                image = None
            tally.requested += 1
            if image:
                tally.generated += 1
                tally.embedded += 1
            else:
                tally.failed += 1

        story.extend(_issue_page(issue, profile, image, capture, st))
        if index < len(issues) - 1:
            story.append(PageBreak())

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return plan, tally


# ------------------------------------------------------------------ cover


def _cover(result, profile: Profile, document_title: str, st) -> list[Any]:
    bands = result.by_priority()
    owners = result.by_responsible()

    flow: list[Any] = [
        Paragraph("Informe de coordinación", st["title"]),
        Paragraph(
            escape_markup(document_title or "Modelo federado")
            + " · "
            + datetime.now().strftime("%d/%m/%Y %H:%M")
            + " · perfil «"
            + escape_markup(result.profile_name)
            + "»",
            st["subtitle"],
        ),
        Spacer(1, 8 * mm),
    ]

    summary = [
        ["Cruces leídos del modelo", f"{result.raw_clash_count:,}".replace(",", ".")],
        [
            "Descartados (ruido y por diseño)",
            f"{result.filtering.dropped_count:,}".replace(",", ".") if result.filtering else "0",
        ],
        ["Incidencias agrupadas", f"{len(result.issues):,}".replace(",", ".")],
        ["Decisiones distintas", f"{len(result.decisions):,}".replace(",", ".")],
        ["Críticos", str(bands.get("critical", 0))],
        ["Altos", str(bands.get("high", 0))],
    ]
    table = Table(summary, colWidths=[85 * mm, 35 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#E0E0E0")),
                ("TEXTCOLOR", (0, 4), (-1, 4), PRIORITY_COLORS["critical"]),
                ("TEXTCOLOR", (0, 5), (-1, 5), PRIORITY_COLORS["high"]),
                ("FONTNAME", (0, 4), (-1, 5), "Helvetica-Bold"),
            ]
        )
    )
    flow.append(table)

    if result.warnings:
        flow.append(Spacer(1, 4 * mm))
        flow.append(Paragraph("Condiciones del análisis", st["h2"]))
        for warning in result.warnings:
            flow.append(Paragraph(escape_markup(warning), st["small"]))
            flow.append(Spacer(1, 1 * mm))

    if owners:
        flow.append(Spacer(1, 6 * mm))
        flow.append(Paragraph("Carga por responsable", st["h2"]))
        rows = [["Disciplina", "Incidencias"]] + [
            [_cell(profile.label(code)), str(count)] for code, count in owners.items()
        ]
        owner_table = Table(rows, colWidths=[85 * mm, 35 * mm])
        owner_table.setStyle(_grid_style())
        flow.append(owner_table)

    if result.root_causes:
        flow.append(Spacer(1, 6 * mm))
        flow.append(Paragraph("Causas raíz", st["h2"]))
        for cause in result.root_causes[:3]:
            # `title` and `suggested_action` are composed from element names,
            # system names and profile labels — all of them model-supplied.
            # The bold tag is ours; the text inside it is escaped.
            flow.append(
                Paragraph(
                    bold(cause.title)
                    + f" — {cause.confidence:.0%} de confianza, "
                    + f"{cause.clash_count} cruces en {cause.affected_count} problemas.",
                    st["body"],
                )
            )
            flow.append(Paragraph(escape_markup(cause.suggested_action), st["small"]))
            flow.append(Spacer(1, 3 * mm))

    return flow


# ------------------------------------------------------------------- plan


def _plan_section(plan: Plan, profile: Profile, st) -> list[Any]:
    """The page a client actually acts on."""
    flow: list[Any] = [
        Paragraph("Plan de trabajo", st["title"]),
        Paragraph(
            f"{plan.total_issues} decisiones agrupadas en {len(plan.packages)} paquetes. "
            "Los primeros cierran la mayor parte: hacerlos antes encoge todo lo demás.",
            st["subtitle"],
        ),
        Spacer(1, 6 * mm),
    ]

    rows = [["#", "Paquete", "Decisiones", "Críticas", "Quién", "Alcance"]]
    styles = [
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E0E0E0")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F3F4")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (2, 1), (3, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]

    for position, package in enumerate(plan.packages[:14], start=1):
        rows.append([
            _cell(package.package_id),
            Paragraph(escape_markup(package.title), st["body"]),
            str(package.issues),
            str(package.critical),
            _cell(profile.label(package.owner)) if package.owner else "varias",
            _cell(package.scope) or "—",
        ])
        if package.kind == "systemic":
            # The systemic ones are where the leverage is; they should be
            # visible without reading the table twice.
            styles.append(
                ("BACKGROUND", (0, position), (-1, position), colors.HexColor("#E8F0FE"))
            )
        if package.critical:
            styles.append(
                ("TEXTCOLOR", (3, position), (3, position), PRIORITY_COLORS["critical"])
            )
            styles.append(("FONTNAME", (3, position), (3, position), "Helvetica-Bold"))

    table = Table(rows, colWidths=[16 * mm, 96 * mm, 22 * mm, 20 * mm, 40 * mm, 42 * mm])
    table.setStyle(TableStyle(styles))
    flow.append(table)

    if plan.tail_issues:
        flow.append(Spacer(1, 3 * mm))
        flow.append(
            Paragraph(
                f"Cola: {plan.tail_issues} decisiones de baja prioridad sin paquete propio. "
                "Se cierran con las anteriores o se revisan al final. Se cuentan aquí en "
                "vez de listarse porque fingir que son accionables devuelve el plan a mil filas.",
                st["small"],
            )
        )

    flow.append(Spacer(1, 6 * mm))
    flow.append(Paragraph("Los tres primeros, en detalle", st["h2"]))
    for package in plan.packages[:3]:
        who = profile.label(package.owner) if package.owner else "varias especialidades"
        flow.append(
            Paragraph(
                bold(f"{package.package_id} · {package.title}")
                + f" — {package.issues} decisiones, {package.critical} críticas. "
                + "Mueve "
                + escape_markup(who)
                + ".",
                st["body"],
            )
        )
        flow.append(Paragraph(escape_markup(package.action), st["action"]))
        if package.rationale:
            flow.append(Paragraph(escape_markup(package.rationale), st["small"]))
        flow.append(Spacer(1, 3 * mm))

    return flow


# ----------------------------------------------------------------- matrix


def _matrix_section(matrix: CoordinationMatrix, profile: Profile, st) -> list[Any]:
    flow: list[Any] = [
        Paragraph("Matriz de coordinación", st["title"]),
        Paragraph(
            "Decisiones abiertas por cruce de disciplinas. El color marca dónde hay "
            "problemas críticos: esas son las parejas que deben sentarse.",
            st["subtitle"],
        ),
        Spacer(1, 6 * mm),
    ]

    order = matrix.disciplines
    header = [""] + [_cell(d) for d in order]
    rows = [header]
    styles = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D0D0D0")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F3F4")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F3F4")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ]

    for r, a in enumerate(order, start=1):
        row = [_cell(a)]
        for c, b in enumerate(order, start=1):
            cell = matrix.get(a, b)
            if cell is None or cell.issues == 0:
                row.append("·")
                continue
            row.append(f"{cell.issues}" + (f"\n({cell.critical}✕)" if cell.critical else ""))
            if cell.critical:
                styles.append(("BACKGROUND", (c, r), (c, r), colors.HexColor("#FADAD7")))
                styles.append(("TEXTCOLOR", (c, r), (c, r), PRIORITY_COLORS["critical"]))
            elif cell.high:
                styles.append(("BACKGROUND", (c, r), (c, r), colors.HexColor("#FDE7CE")))
        rows.append(row)

    table = Table(rows, colWidths=[22 * mm] + [20 * mm] * len(order))
    table.setStyle(TableStyle(styles))
    flow.append(table)
    flow.append(Spacer(1, 3 * mm))
    flow.append(
        Paragraph("Número = decisiones abiertas. (n✕) = cuántas son críticas.", st["small"])
    )

    ranked = [c for c in matrix.ranked() if c.issues][:8]
    if ranked:
        flow.append(Spacer(1, 6 * mm))
        flow.append(Paragraph("Agenda sugerida", st["h2"]))
        rows = [["Cruce", "Decisiones", "Críticas", "Quién mueve"]]
        for cell in ranked:
            rows.append(
                [
                    _cell(f"{profile.label(cell.a)} × {profile.label(cell.b)}"),
                    str(cell.issues),
                    str(cell.critical),
                    _cell(profile.label(cell.worst_owner)),
                ]
            )
        agenda = Table(rows, colWidths=[95 * mm, 28 * mm, 24 * mm, 60 * mm])
        agenda.setStyle(_grid_style())
        flow.append(agenda)

    return flow


# ------------------------------------------------------------ issue page


def _issue_page(
    issue: Issue,
    profile: Profile,
    image: bytes | None,
    capture: Capture | None,
    st,
) -> list[Any]:
    colour = PRIORITY_COLORS.get(issue.priority, colors.black)
    pair = " × ".join(profile.label(d) for d in issue.discipline_pair if d)

    flow: list[Any] = [
        Paragraph(
            # `colour` is a palette constant and `hexval()` yields 0x……; the
            # helper still validates it rather than trusting the shape.
            coloured(
                PRIORITY_LABELS.get(issue.priority, issue.priority.upper()),
                "#" + colour.hexval()[2:],
            )
            + "  ·  "
            + escape_markup(issue.issue_id)
            + "  ·  "
            + escape_markup(_cell(issue.level) or "sin nivel")
            + "  ·  severidad "
            + f"{issue.severity:.0f}",
            st["subtitle"],
        ),
        Paragraph(escape_markup(pair), st["title"]),
    ]

    # The grid reference the addin resolved from the model's own grid system.
    # A clash result carries no level and no grid of its own, so on a
    # federation whose elements publish neither this is the only answer to
    # "where is it" that is not a triple of coordinates.
    located = _cell((capture.location or {}).get("grid_reference", "")) if capture else ""

    facts = [
        ["Ubicación", _cell(issue.level) or f"z = {issue.centroid[2]:.1f} m"],
    ]
    if located:
        facts.append(["Eje / nivel del modelo", located])
    facts.extend([
        ["Coordenadas", f"X {issue.centroid[0]:.1f}   Y {issue.centroid[1]:.1f}   Z {issue.centroid[2]:.1f}"],
        ["Disciplinas", _cell(_side_labels(issue, profile))],
        ["Choques agrupados", str(issue.clash_count)],
        ["Penetración máxima", f"{issue.max_penetration_m * 1000:.0f} mm"],
        ["Quién mueve", _cell(profile.label(issue.responsible))],
        ["No se toca", _cell(profile.label(issue.immovable_side))],
        ["Severidad", f"{issue.severity:.0f} / 100"],
    ])
    if issue.folds:
        facts.append(["Cierra además", f"{issue.folds} problemas con la misma causa"])
    if issue.root_cause:
        facts.append(["Causa raíz", _cell(issue.root_cause.get("title", ""))])

    fact_table = Table(facts, colWidths=[38 * mm, 82 * mm])
    fact_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5F6368")),
                ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.HexColor("#EEEEEE")),
            ]
        )
    )

    picture: Any = Paragraph(
        italic(_no_image_reason(capture)), st["small"]
    )
    if image:
        try:
            picture = Image(io.BytesIO(image), width=140 * mm, height=93 * mm, kind="proportional")
        except Exception:
            picture = Paragraph(
                italic("Sin imagen: el PNG recibido no se pudo insertar."), st["small"]
            )

    right: list[Any] = [picture]
    caption = _image_caption(issue, profile, capture)
    if caption:
        right.append(Spacer(1, 1.5 * mm))
        right.append(Paragraph(caption, st["small"]))

    layout = Table([[fact_table, right]], colWidths=[122 * mm, 145 * mm])
    layout.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    flow.append(Spacer(1, 3 * mm))
    flow.append(layout)

    flow.append(Spacer(1, 4 * mm))
    flow.append(Paragraph("Por qué importa", st["h2"]))
    for reason in issue.why:
        flow.append(Paragraph("•  " + escape_markup(reason), st["body"]))

    flow.append(Spacer(1, 3 * mm))
    flow.append(Paragraph("Qué hacer", st["h2"]))
    flow.append(Paragraph(escape_markup(issue.suggested_action), st["action"]))

    return [KeepTogether(flow)]


# -------------------------------------------------------- image chrome


#: Why a picture is missing, in words a coordinator can act on. The codes come
#: from the quality gate; printing them raw would put "side_b_not_visible" in
#: front of a client.
_REJECTION_TEXT = {
    "side_a_out_of_frame": "uno de los elementos quedaba fuera del encuadre",
    "side_b_out_of_frame": "uno de los elementos quedaba fuera del encuadre",
    "clash_out_of_frame": "el volumen de interferencia no entraba en el encuadre",
    "clash_not_contained": "el volumen de interferencia quedaba cortado por el borde",
    "side_a_too_small": "uno de los elementos salía demasiado pequeño para leerse",
    "side_b_too_small": "uno de los elementos salía demasiado pequeño para leerse",
    "side_a_not_visible": "otra geometría tapaba uno de los elementos",
    "side_b_not_visible": "otra geometría tapaba uno de los elementos",
    "image_blank": "la vista salía vacía",
    "image_unreadable": "Navisworks devolvió una imagen ilegible",
    "render_failed": "Navisworks no pudo renderizar este cruce",
    "bridge_error": "no hubo respuesta del complemento de Navisworks",
    "not_attempted": "no se pidió imagen para este problema",
}


def _no_image_reason(capture: Capture | None) -> str:
    """The sentence that goes where the picture would have been.

    A blank space reads as "the tool forgot". Naming the reason turns a hole
    into a finding: "otra geometría tapaba uno de los elementos" is something a
    modeller can go and look at.
    """
    if capture is None:
        return "Sin imagen: Navisworks no pudo renderizar este cruce."

    reasons = capture.verdict.reasons
    if not reasons:
        return "Sin imagen: no se solicitó para este problema."

    seen: list[str] = []
    for reason in reasons:
        text = _REJECTION_TEXT.get(reason, reason)
        if text not in seen:
            seen.append(text)
    detail = "; ".join(seen)
    attempts = len(capture.attempts)
    tail = f" Se intentó {attempts} vez." if attempts == 1 else f" Se intentaron {attempts} encuadres."
    return f"Sin imagen: {detail}.{tail}"


def _image_caption(issue: Issue, profile: Profile, capture: Capture | None) -> str:
    """The legend under the picture: which colour is which trade.

    Without it a coloured render is decoration. The colours are read back from
    the capture — the ones the addin reported APPLYING, not the ones that were
    requested — so the legend cannot describe a palette the picture does not use.
    """
    if capture is None or capture.png is None:
        return ""

    parts = [
        coloured("■", to_hex(capture.colour_a))
        + " "
        + escape_markup(profile.label(issue.discipline_a) or "lado A"),
        coloured("■", to_hex(capture.colour_b))
        + " "
        + escape_markup(profile.label(issue.discipline_b) or "lado B"),
    ]
    mode = (capture.options.camera_mode if capture.options else "") or ""
    if mode:
        parts.append(escape_markup({"closeup": "primer plano", "context": "contexto",
                                    "plan": "planta"}.get(mode, mode)))
    return "  ·  ".join(parts)


def _side_labels(issue: Issue, profile: Profile) -> str:
    """Side A and side B by name, in Navisworks order rather than sorted."""
    a = profile.label(issue.discipline_a) or "—"
    b = profile.label(issue.discipline_b) or "—"
    return f"A: {a}   ·   B: {b}"


# ------------------------------------------------------------ escaping


def _cell(value: Any) -> str:
    """Text for a plain table cell.

    Table cells are drawn, not parsed, so markup is inert here — but a
    newline or a control character still breaks the layout, and a cell that
    is later promoted to a Paragraph would silently become a markup sink. One
    helper for every cell keeps that promotion safe.
    """
    return _flatten(value)


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return "".join(ch for ch in text if ch >= " ").strip()


# ---------------------------------------------------------------- chrome


def _grid_style() -> TableStyle:
    return TableStyle(
        [
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E0E0E0")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F3F4")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
    )


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#9AA0A6"))
    canvas.drawString(16 * mm, 8 * mm, "NavisCoord")
    canvas.drawRightString(
        doc.pagesize[0] - 16 * mm, 8 * mm, f"Página {canvas.getPageNumber()}"
    )
    canvas.restoreState()


# Re-exported: `decode_image` used to live here, and callers import it from
# this module. It belongs beside the rest of the image handling now, but the
# name stays reachable where it has always been.
from .imaging import decode_image  # noqa: E402  (re-export, kept at the tail)

__all__ = ["ImageTally", "build_report", "decode_image"]
