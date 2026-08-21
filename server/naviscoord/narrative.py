"""The coordination report, written rather than listed.

A ranked table says what to open first. It does not say what is wrong with
the project, and that is the paragraph a coordinator is actually paid for:
whether the model is fit to coordinate at all, whether the clashes come from
design, from modelling, or from two trades that simply have not spoken, and
what the client should do about it this week.

Everything here is derived from the analysis — no fixed sentences about a
"typical project". Where the evidence is thin the text says so, because a
confident paragraph on weak evidence is worse than no paragraph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .analysis.pipeline import AnalysisResult
from .matrix import CoordinationMatrix
from .profile import Profile

# What a class of finding means for whoever has to act on it. This is the
# judgement the whole report turns on: the same clash count means very
# different things depending on which bucket it falls in.
DESIGN = "diseño"
MODELLING = "modelado"
COORDINATION = "coordinación"


def _n(value: float) -> str:
    """Thousands separator, applied to the number and nothing else.

    Formatting by running .replace(',', '.') over the finished sentence eats
    every grammatical comma in it too, which turns prose into "De esas. 5.737
    son elementos que se traslapan por diseño —un tomacorriente. una puerta—".
    """
    return f"{int(value):,}".replace(",", ".")


@dataclass(slots=True)
class Finding:
    kind: str
    headline: str
    body: str
    weight: int = 0
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Narrative:
    verdict: str
    readiness: str
    findings: list[Finding]
    paragraphs: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "readiness": self.readiness,
            "findings": [
                {"kind": f.kind, "headline": f.headline, "body": f.body,
                 "weight": f.weight, "evidence": f.evidence}
                for f in self.findings
            ],
            "text": "\n\n".join(self.paragraphs),
        }

    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


def compose(
    result: AnalysisResult,
    matrix: CoordinationMatrix,
    profile: Profile,
    *,
    document_title: str = "",
    discovery: Any = None,
    grouping_basis: str = "",
) -> Narrative:
    findings = _findings(result, matrix, profile, discovery)
    readiness, verdict = _verdict(result, findings)

    paragraphs = [
        _opening(result, document_title, discovery, verdict, grouping_basis),
        _landscape(result, profile),
        _matrix_paragraph(matrix, profile),
    ]

    by_kind: dict[str, list[Finding]] = {}
    for finding in findings:
        by_kind.setdefault(finding.kind, []).append(finding)

    for kind, label in (
        (MODELLING, "Lo que es un problema de modelado"),
        (DESIGN, "Lo que es un problema de diseño"),
        (COORDINATION, "Lo que es un problema de coordinación"),
    ):
        group = by_kind.get(kind)
        if not group:
            continue
        chunk = [f"**{label}.**"]
        for finding in sorted(group, key=lambda f: -f.weight):
            chunk.append(f"{finding.headline} {finding.body}")
        paragraphs.append(" ".join(chunk))

    paragraphs.append(_closing(result, findings, readiness))
    return Narrative(verdict=verdict, readiness=readiness, findings=findings, paragraphs=paragraphs)


# ------------------------------------------------------------- paragraphs


def _opening(
    result: AnalysisResult, title: str, discovery: Any, verdict: str, grouping_basis: str = ""
) -> str:
    name = title or "el modelo entregado"
    structure = ""
    if discovery is not None:
        if not discovery.federated:
            structure = (
                " Antes de nada, una observación sobre cómo está armado: no es una "
                "federación de modelos por especialidad sino un solo archivo con todas "
                "las disciplinas dentro. Eso limita la coordinación desde el origen, "
                "porque no hay forma de aislar responsabilidades por archivo ni de que "
                "cada especialista trabaje sin pisar el modelo del otro."
            )
        elif discovery.classifications:
            first = discovery.classifications[0]
            structure = (
                f" El modelo trae clasificación {first.system} en «{first.property_label}», "
                f"presente en el {first.coverage:.0%} de los elementos, lo que permite "
                "agrupar y trazar por sistema con criterio propio del proyecto."
            )
        else:
            # Say what the analysis ACTUALLY used to separate trades, which
            # the caller knows and the discovery report does not: discovery
            # only recommends, and the applied gate can decline or trim the
            # recommendation. Reading the recommendation here produced a
            # report that named a grouping the analysis never applied.
            basis = (
                f"el parámetro «{grouping_basis}» de cada elemento"
                if grouping_basis
                else "la categoría de cada elemento"
            )
            structure = (
                " El modelo no trae ningún sistema de clasificación reconocible, así que "
                f"la separación por especialidad se apoya en {basis}."
            )

    return (
        f"**Diagnóstico de {name}.** {verdict}{structure}"
    )


def _landscape(result: AnalysisResult, profile: Profile) -> str:
    dropped = result.filtering.dropped_count if result.filtering else 0
    reasons = result.filtering.reasons if result.filtering else {}
    by_design = sum(
        count
        for reason, count in reasons.items()
        # Every reason that means "this is how the building is built" belongs
        # here. A reason missing from the set does not vanish — it falls into
        # the closing "everything else" clause and gets described as a contact
        # below tolerance, so 510 partitions dying against walls were reported
        # to the reader as 510 sub-millimetre grazes.
        if reason in {"designed_embedment", "architectural_hosting",
                      "architectural_self_overlap", "designed_pass_through",
                      "designed_adjacency"}
    )
    approved = reasons.get("status_excluded", 0)

    text = (
        f"El modelo reporta {_n(result.raw_clash_count)} interferencias en bruto. "
        f"De esas, {_n(dropped)} no son problemas de coordinación: "
    )

    pieces: list[str] = []
    if by_design:
        pieces.append(
            f"{_n(by_design)} son elementos que se traslapan por diseño —un tomacorriente "
            "embebido en su muro, una puerta dentro de su vano, un muro que sube a losa "
            "contra el cielo que cuelga debajo—"
        )
    if approved:
        pieces.append(f"{_n(approved)} ya fueron revisadas y aprobadas por el equipo")
    other = dropped - by_design - approved
    if other > 0:
        pieces.append(f"{_n(other)} son contactos por debajo de la tolerancia útil")

    text += ("; ".join(pieces) if pieces else "son ruido geométrico") + ". "
    text += (
        f"Quedan {_n(len(result.issues))} problemas reales, que se resuelven con "
        f"{_n(len(result.decisions))} decisiones distintas: el resto se cierra solo cuando "
        "se toma la decisión de la que dependen. "
    )

    bands = result.by_priority()
    text += (
        f"De esas decisiones, {bands.get('critical', 0)} son críticas —frenan obra o "
        f"cuestan caro rehacerlas— y {bands.get('high', 0)} son altas. "
        "Ese es el trabajo de las próximas dos semanas; el resto es cola."
    )
    return text


def _matrix_paragraph(matrix: CoordinationMatrix, profile: Profile) -> str:
    ranked = [c for c in matrix.ranked() if c.issues][:3]
    if not ranked:
        return "No hay cruces entre especialidades que reportar."

    worst = ranked[0]
    text = (
        f"**Dónde está concentrado el conflicto.** La peor intersección es "
        f"{profile.label(worst.a)} contra {profile.label(worst.b)}, con {worst.issues} "
        f"decisiones abiertas y {worst.critical} críticas; mueve "
        f"{profile.label(worst.worst_owner)}. "
    )
    if len(ranked) > 1:
        rest = ", ".join(
            f"{profile.label(c.a)}×{profile.label(c.b)} ({c.issues})" for c in ranked[1:]
        )
        text += f"Le siguen {rest}. "
    text += (
        "Esas son las parejas que deben sentarse; convocar a todas las especialidades a "
        "la vez para revisar cruces que no les competen es la forma más común de perder "
        "una sesión de coordinación."
    )
    return text


def _closing(result: AnalysisResult, findings: list[Finding], readiness: str) -> str:
    top = result.top(3)
    text = "**Qué hacer esta semana.** "

    causes = [c for c in result.root_causes if c.confidence >= 0.7]
    if causes:
        first = causes[0]
        text += (
            f"Primero lo que cierra en bloque: {first.suggested_action} "
            f"Eso solo resuelve {first.clash_count} cruces en "
            f"{first.affected_count} problemas. "
        )

    if top:
        text += "Después, punto por punto: "
        text += " ".join(
            f"{issue.issue_id} en {issue.level or 'sin nivel'} — {issue.suggested_action}"
            for issue in top[:2]
        )

    if readiness == "no apto":
        text += (
            " Y una advertencia de fondo: el modelo todavía no está en condiciones de "
            "coordinarse punto por punto. Corregir los problemas de modelado primero "
            "evita revisar cientos de cruces que van a desaparecer solos."
        )
    return text


# --------------------------------------------------------------- findings


def _findings(
    result: AnalysisResult, matrix: CoordinationMatrix, profile: Profile, discovery: Any
) -> list[Finding]:
    findings: list[Finding] = []
    reasons = result.filtering.reasons if result.filtering else {}

    # --- modelling
    self_overlap = reasons.get("architectural_self_overlap", 0)
    if self_overlap > 200:
        findings.append(
            Finding(
                kind=MODELLING,
                headline=f"Arquitectura se traslapa consigo misma en {_n(self_overlap)} puntos.",
                body=(
                    "Muros que suben a losa contra cielos colgados debajo. En obra el cielo "
                    "muere contra el muro, así que no es una interferencia, pero sí dice que "
                    "el modelo arquitectónico no distingue el alcance real de cada elemento. "
                    "Mientras siga así, cualquier test que incluya arquitectura arrastra este "
                    "ruido."
                ),
                weight=self_overlap,
            )
        )

    embedment = reasons.get("designed_embedment", 0)
    if embedment > 200:
        findings.append(
            Finding(
                kind=MODELLING,
                headline=f"{_n(embedment)} dispositivos embebidos en su anfitrión.",
                body=(
                    "Tomacorrientes, interruptores y luminarias dentro de muros y cielos. Es "
                    "correcto que estén ahí; el problema es que los tests no los excluyen y "
                    "cada uno aparece como interferencia. Ajustar las reglas de los tests "
                    "libera esa carga de la lista."
                ),
                weight=embedment,
            )
        )

    # Root causes are aggregated by kind, never listed one by one. Fifty
    # systems each get their own "is at the wrong elevation" sentence from
    # the detector, and printing all fifty is not a report — it is the raw
    # output with paragraph marks. What a coordinator writes is the pattern,
    # the size of it, and the two or three worst offenders by name.
    by_kind: dict[str, list[Any]] = {}
    for cause in result.root_causes:
        by_kind.setdefault(cause.kind, []).append(cause)

    for cause in by_kind.get("missing_penetration", [])[:1]:
        findings.append(
            Finding(
                kind=DESIGN,
                headline="Los pasos de instalaciones no están definidos.",
                body=(
                    f"{_n(cause.clash_count)} cruces de instalaciones atravesando muros y losas "
                    "sin un paso modelado. No es un error de dibujo: es una decisión de "
                    "diseño que no se tomó, y que se va a tomar en obra con un martillo si "
                    "nadie la levanta antes. Es el hallazgo más importante de este modelo."
                ),
                weight=cause.clash_count * 2,
                evidence=[cause.suggested_action],
            )
        )

    elevation = [c for c in by_kind.get("systemic_elevation", []) if c.confidence >= 0.8]
    if elevation:
        total = sum(c.clash_count for c in elevation)
        worst = sorted(elevation, key=lambda c: -c.clash_count)[:3]
        named = "; ".join(
            f"«{c.evidence.get('system', '?')}» ({c.clash_count} cruces a "
            f"{c.evidence.get('mean_overlap_mm', 0):.0f} mm)"
            for c in worst
        )
        findings.append(
            Finding(
                kind=DESIGN,
                headline=(
                    f"{len(elevation)} sistemas MEP están trazados a la cota equivocada, "
                    f"con {_n(total)} cruces entre todos."
                ),
                body=(
                    "En cada uno la penetración se repite casi idéntica cruce tras cruce, y esa "
                    "regularidad no sale de errores independientes: sale de un trazado completo "
                    f"mal ubicado. Los peores son {named}. "
                    "Que le pase a tantos sistemas a la vez apunta a un criterio de alturas "
                    "libres mal definido o mal comunicado, no a descuidos aislados."
                ),
                weight=total,
                evidence=[c.suggested_action for c in worst],
            )
        )

    zones = by_kind.get("congested_zone", [])
    if zones:
        affected = sum(z.affected_count for z in zones)
        levels: dict[str, int] = {}
        for zone in zones:
            level = str(zone.evidence.get("level") or "sin nivel")
            levels[level] = levels.get(level, 0) + 1
        worst_level, worst_count = max(levels.items(), key=lambda kv: kv[1])
        findings.append(
            Finding(
                kind=COORDINATION,
                headline=f"{len(zones)} zonas saturadas concentran {affected} problemas.",
                body=(
                    f"{worst_count} de ellas están en {worst_level}, lo que señala una planta "
                    "con el pleno técnico apretado más que un problema repartido. En estas "
                    "zonas no sirve resolver de a uno: cada movimiento empuja al vecino y el "
                    "trabajo se deshace. Se resuelven en mesa, definiendo primero el orden de "
                    "paso entre especialidades."
                ),
                weight=affected * 3,
            )
        )

    structural = [
        c for c in matrix.ranked()
        if c.issues and ("EST" in (c.a, c.b)) and c.critical
    ]
    if structural:
        total = sum(c.critical for c in structural)
        pairs = ", ".join(
            f"{profile.label(c.a)}×{profile.label(c.b)}" for c in structural[:3]
        )
        findings.append(
            Finding(
                kind=DESIGN,
                headline=f"{total} interferencias críticas contra estructura.",
                body=(
                    f"Concentradas en {pairs}. La estructura no se modifica en obra, así que "
                    "cada una de estas es o un reruteo de instalaciones o una solicitud formal "
                    "de paso al calculista. Ninguna se resuelve sola."
                ),
                weight=total * 4,
            )
        )

    findings.sort(key=lambda f: -f.weight)
    return findings


def _blind_spots(result: AnalysisResult) -> str:
    """Why a zero here might mean 'not looked at' rather than 'nothing there'.

    Returns a sentence naming the gap, or an empty string when the run really
    did cover its matrix.
    """
    parts: list[str] = []

    coverage = getattr(result, "coverage", None)
    if coverage is not None and coverage.never_run:
        count = len(coverage.never_run)
        parts.append(
            f"{count} de {coverage.total} tests nunca se corrieron, así que esos "
            "pares de especialidades no se han mirado"
        )
    if coverage is not None and coverage.missing_pairs:
        named = ", ".join(f"{a}×{b}" for a, b in coverage.missing_pairs[:4])
        more = (
            f" y {len(coverage.missing_pairs) - 4} más"
            if len(coverage.missing_pairs) > 4
            else ""
        )
        parts.append(
            f"la matriz solo compara {coverage.required_covered} de las "
            f"{len(coverage.required_pairs)} parejas que exige el perfil, y sobre "
            f"{named}{more} no hay ni un test"
        )

    filtering = result.filtering
    if filtering is not None:
        emptied = [
            test
            for test, total in filtering.input_by_test.items()
            if total > 0 and filtering.kept_by_test.get(test, 0) == 0
        ]
        if emptied:
            named = ", ".join(f"«{t}»" for t in emptied[:3])
            more = f" y {len(emptied) - 3} más" if len(emptied) > 3 else ""
            parts.append(
                f"el filtro de ruido vació {len(emptied)} test(s) por completo "
                f"({named}{more}), que quedan reportados como limpios sin evidencia"
            )

    if not parts:
        return ""
    return "; ".join(parts) + "."


def _verdict(result: AnalysisResult, findings: list[Finding]) -> tuple[str, str]:
    """Is this model fit to coordinate, and what is the one-line judgement?"""
    modelling_weight = sum(f.weight for f in findings if f.kind == MODELLING)
    total = max(result.raw_clash_count, 1)
    noise_share = (result.filtering.dropped_count / total) if result.filtering else 0.0
    critical = result.by_priority().get("critical", 0)

    if noise_share > 0.45 and modelling_weight > 0.25 * total:
        return (
            "no apto",
            "El modelo todavía no está listo para coordinarse punto por punto: más de la "
            "mitad de lo que reporta no son interferencias reales, y eso indica que las "
            "reglas de los tests y el alcance de los elementos hay que corregirlos antes "
            "de sentar a las especialidades.",
        )
    if critical > 80:
        return (
            "apto con reservas",
            "El modelo es coordinable, pero arrastra un volumen alto de interferencias "
            "críticas que no se cierran en una sola sesión; hay que planificarlas por "
            "bloques y por pareja de especialidades.",
        )
    # "Apto" is a claim about the whole model, so it needs the whole model to
    # have been looked at — and this used to be checked only when nothing was
    # found, as if finding something proved the search had been thorough. It
    # does not. A matrix that compares three of sixteen trade pairs can turn
    # up twenty-seven critical interferences and still be silent about most
    # of the building; passing that as "coordinable" is the same false
    # reassurance as passing an unrun matrix as clean, wearing a number.
    blind = _blind_spots(result)
    if blind:
        found = (
            f"Se encontraron {critical} interferencias críticas y son reales, pero "
            if critical
            else "No aparecen interferencias críticas, pero "
        )
        return (
            "sin evidencia suficiente",
            f"{found}el modelo todavía no puede declararse apto: {blind} "
            "Cerrar el vacío y volver a correr es lo que convierte este resultado "
            "en una respuesta sobre el modelo y no solo sobre lo que se miró.",
        )
    if critical == 0:
        return (
            "apto",
            "El modelo está en buen estado de coordinación: no hay interferencias que "
            "frenen obra pendientes.",
        )
    return (
        "apto",
        f"El modelo es coordinable. Hay {critical} interferencias críticas que resolver, "
        "un volumen manejable en las próximas sesiones.",
    )
