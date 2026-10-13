"""Names a coordinator can read in Clash Detective, numbered by priority.

Navisworks calls every result «Clash1» or «Conflicto1». A list of two
thousand of those says nothing about which to open first, and the two names
NavisCoord wrote before did not fix it: `navis_apply_groups` produced
`[CRITICAL] ISS-db75bd49ae215134 EST×MEP → MEP` — English priority, a hash
for an id, no running number — and the rules workflow numbered results in
whatever order Navisworks happened to list them.

Here the number IS the triage order. `001` is the most urgent problem in the
analysis, and the label says what the coordinator needs before clicking:

    001 · CRÍTICO · EST × MEP · Losa vs Ducto · N03 · mueve MEP

Only the label is decided here. Which names in the document may be replaced
— never one the coordinator typed — is the add-in's call, made against the
names it reads from the live document (see ClashNaming.cs).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .model import Clash, ClashExport, ElementRef, Issue

#: Clash Detective shows a narrow column; past this the tail is noise anyway.
MAX_NAME = 120

PRIORITY_ES = {"critical": "CRÍTICO", "high": "ALTO", "medium": "MEDIO", "low": "BAJO"}
_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

SEPARATOR = " · "

# Revit category (English and Spanish builds) → the word used on site.
# Floors are the exception that needs the trade: a structural floor is a
# slab, an architectural one is a finish floor.
_CATEGORY_ES: dict[str, str] = {
    "structural framing": "Viga",
    "armazón estructural": "Viga",
    "structural columns": "Columna",
    "pilares estructurales": "Columna",
    "columns": "Columna",
    "pilares": "Columna",
    "structural foundations": "Cimentación",
    "cimentación estructural": "Cimentación",
    "walls": "Muro",
    "muros": "Muro",
    "ceilings": "Cielo",
    "techos": "Cielo",
    "roofs": "Cubierta",
    "cubiertas": "Cubierta",
    "doors": "Puerta",
    "puertas": "Puerta",
    "windows": "Ventana",
    "ventanas": "Ventana",
    "stairs": "Escalera",
    "escaleras": "Escalera",
    "railings": "Baranda",
    "barandillas": "Baranda",
    "ducts": "Ducto",
    "conductos": "Ducto",
    "duct fittings": "Accesorio de ducto",
    "uniones de conducto": "Accesorio de ducto",
    "duct accessories": "Accesorio de ducto",
    "accesorios de conductos": "Accesorio de ducto",
    "flex ducts": "Ducto flexible",
    "conductos flexibles": "Ducto flexible",
    "air terminals": "Rejilla",
    "terminales de aire": "Rejilla",
    "pipes": "Tubería",
    "tuberías": "Tubería",
    "pipe fittings": "Accesorio de tubería",
    "uniones de tubería": "Accesorio de tubería",
    "pipe accessories": "Accesorio de tubería",
    "accesorios de tuberías": "Accesorio de tubería",
    "flex pipes": "Tubería flexible",
    "tuberías flexibles": "Tubería flexible",
    "cable trays": "Bandeja",
    "bandejas de cables": "Bandeja",
    "cable tray fittings": "Accesorio de bandeja",
    "uniones de bandeja de cables": "Accesorio de bandeja",
    "conduits": "Conduit",
    "tubos": "Conduit",
    "mechanical equipment": "Equipo",
    "equipos mecánicos": "Equipo",
    "electrical equipment": "Equipo eléctrico",
    "equipos eléctricos": "Equipo eléctrico",
    "plumbing fixtures": "Aparato sanitario",
    "aparatos sanitarios": "Aparato sanitario",
    "sprinklers": "Rociador",
    "rociadores": "Rociador",
    "lighting fixtures": "Luminaria",
    "luminarias": "Luminaria",
    "generic models": "Modelo genérico",
    "modelos genéricos": "Modelo genérico",
}
_FLOOR_CATEGORIES = {"floors", "suelos"}
_STRUCTURE = {"EST", "STR", "ESTRUCTURA", "STRUCTURE"}

# «03 - Floor», «Nivel 5», «Level 12», «PISO 2», «N3»: a storey NUMBER, which
# shortens to N03. Anything else («Cubierta», «Sótano 1») already says what
# it is and is kept — «Sótano 1» as N01 would put a basement on the first
# floor.
_LEVEL_NUMBER = re.compile(r"^\s*(?:(?:nivel|level|piso|n|l)\s*[-_.]?\s*)?(\d{1,3})(?:\b|\s|-|$)", re.IGNORECASE)
_NO_LEVEL = {"", "sin nivel", "no level"}


def order_by_priority(issues: Iterable[Issue]) -> list[Issue]:
    """Priority band first, score inside the band, id to break ties.

    A low with a big score never jumps a critical: the band is what the team
    triages by. The id makes two runs over the same analysis number alike.
    """
    return sorted(
        issues,
        key=lambda i: (_PRIORITY_RANK.get(i.priority, len(_PRIORITY_RANK)), -float(i.severity), i.issue_id),
    )


def priority_es(priority: str) -> str:
    return PRIORITY_ES.get((priority or "").lower(), (priority or "").upper())


def element_noun(ref: ElementRef | None) -> str:
    """What the element is, in the word used on site, or its own category."""
    if ref is None:
        return ""
    category = (ref.category or "").strip()
    key = category.lower()
    if key in _FLOOR_CATEGORIES:
        return "Losa" if (ref.discipline or "").upper() in _STRUCTURE else "Piso"
    if key in _CATEGORY_ES:
        return _CATEGORY_ES[key]
    return _fit(category, 28)


def level_short(level: str) -> str:
    text = (level or "").strip()
    if text.lower() in _NO_LEVEL:
        return ""
    match = _LEVEL_NUMBER.match(text)
    if match:
        return "N" + match.group(1).zfill(2)
    return _fit(text, 16)


def _fit(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _sides(issue: Issue, clash: Clash | None) -> tuple[str, str, ElementRef | None, ElementRef | None]:
    """The two trades in reading order — what stays, then what moves.

    Navisworks' A/B order is whatever the test declared, so it says nothing
    about the decision. Ordering by the analysis' immovable side makes every
    label read the same way: «EST × MEP · Losa vs Ducto · mueve MEP».
    """
    first = issue.immovable_side or ""
    second = issue.responsible or ""
    pair = [d for d in issue.discipline_pair if d]
    if not first or not second or first == second or {first, second} != set(pair):
        first, second = (pair + ["", ""])[:2]

    ref_first = ref_second = None
    if clash is not None:
        ref_first = clash.side_for(first) if first else None
        ref_second = clash.side_for(second) if second else None
        if ref_first is None and ref_second is None:
            ref_first, ref_second = clash.a, clash.b
        elif ref_first is None:
            ref_first = clash.b if ref_second is clash.a else clash.a
        elif ref_second is None:
            ref_second = clash.b if ref_first is clash.a else clash.a
    return first, second, ref_first, ref_second


def issue_label(sequence: int, issue: Issue, clashes: Mapping[str, Clash]) -> str:
    """`001 · CRÍTICO · EST × MEP · Losa vs Ducto · N03 · mueve MEP`."""
    representative = issue.representative_clash_id or (issue.clash_ids[0] if issue.clash_ids else "")
    clash = clashes.get(representative)
    first, second, ref_first, ref_second = _sides(issue, clash)

    parts = [f"{sequence:03d}", priority_es(issue.priority)]
    trades = " × ".join(d for d in (first, second) if d)
    if trades:
        parts.append(trades)
    nouns = [n for n in (element_noun(ref_first), element_noun(ref_second)) if n]
    if len(nouns) == 2:
        parts.append(f"{_fit(nouns[0], 24)} vs {_fit(nouns[1], 24)}")
    elif nouns:
        parts.append(_fit(nouns[0], 24))
    level = level_short(issue.level)
    if level:
        parts.append(level)
    if issue.responsible:
        parts.append(f"mueve {issue.responsible}")
    return _fit(SEPARATOR.join(parts), MAX_NAME)


def build_plan(
    issues: Iterable[Issue], export: ClashExport | None, limit: int = 0
) -> list[dict[str, Any]]:
    """What the add-in receives: one entry per numbered issue.

    A problem of several clashes becomes a group carrying the label, with
    its clashes numbered `001.1`, `001.2`…; the representative clash — the
    one the analysis describes and photographs — is `.1`. A problem of one
    clash is not worth a group: the clash itself carries the label.
    """
    clashes = {c.guid: c for c in (export.clashes if export else [])}
    numbered = [i for i in order_by_priority(issues) if i.clash_ids]
    if limit and limit > 0:
        numbered = numbered[:limit]

    plan: list[dict[str, Any]] = []
    for sequence, issue in enumerate(numbered, start=1):
        label = issue_label(sequence, issue, clashes)
        guids = list(dict.fromkeys(issue.clash_ids))
        lead = issue.representative_clash_id
        if lead in guids:
            guids.remove(lead)
            guids.insert(0, lead)
        if len(guids) == 1:
            names = [label]
        else:
            names = [f"{sequence:03d}.{k}" for k in range(1, len(guids) + 1)]
        plan.append(
            {
                "issue_id": issue.issue_id,
                "priority": issue.priority,
                "name": label,
                "clash_guids": guids,
                "clash_names": names,
            }
        )
    return plan
