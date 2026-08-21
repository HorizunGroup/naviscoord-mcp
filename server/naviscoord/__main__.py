"""CLI for the analysis engine — runs without Navisworks.

    python -m naviscoord analyze export.json [--profile mi-proyecto.json]
    python -m naviscoord demo
    python -m naviscoord check-profile [ruta.json]

Useful for tuning a profile against a saved export: change a weight, rerun,
see the ranking move, without opening Navisworks once.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis import analyze, hotspots
from .model import ClashExport
from .profile import Profile

BANDS = {"critical": "CRÍTICO", "high": "ALTO", "medium": "MEDIO", "low": "BAJO"}


def _render(result, profile: Profile, limit: int) -> str:
    out: list[str] = []
    add = out.append

    add("=" * 78)
    add(f"  ANÁLISIS DE COORDINACIÓN — perfil '{result.profile_name}'")
    add("=" * 78)
    add("")
    add(f"  Cruces crudos leídos . . . {result.raw_clash_count}")
    if result.filtering:
        add(f"  Descartados por ruido  . . {result.filtering.dropped_count}")
        for reason, count in result.filtering.reasons.most_common():
            add(f"      {reason:<24} {count}")
    add(f"  Problemas reales . . . . . {len(result.issues)}")
    folded = len(result.issues) - len(result.decisions)
    if folded:
        add(f"  Decisiones distintas . . . {len(result.decisions)}  ({folded} plegados bajo otro)")
    if result.issues:
        add(f"  Compresión . . . . . . . . {result.compression:.1f} cruces por problema")
    add("")

    bands = result.by_priority()
    add("  Por prioridad:  " + "   ".join(f"{BANDS[k]} {v}" for k, v in bands.items()))
    responsible = result.by_responsible()
    if responsible:
        add(
            "  Por responsable: "
            + "   ".join(f"{profile.label(k)} {v}" for k, v in responsible.items())
        )
    add("")

    if result.warnings:
        add("  ADVERTENCIAS")
        for warning in result.warnings:
            add(f"    ! {warning}")
        add("")

    if result.root_causes:
        add("-" * 78)
        add("  CAUSAS RAÍZ DETECTADAS")
        add("-" * 78)
        for cause in result.root_causes[:6]:
            add("")
            add(f"  [{cause.confidence:.0%} confianza] {cause.title}")
            add(f"    {cause.detail}")
            add(f"    Afecta {cause.affected_count} problemas / {cause.clash_count} cruces")
            add(f"    → {cause.suggested_action}")
        add("")

    add("-" * 78)
    add(f"  TOP {min(limit, len(result.decisions))} DECISIONES")
    add("-" * 78)
    for issue in result.top(limit):
        add("")
        pair = " × ".join(profile.label(d) for d in issue.discipline_pair if d)
        folds = f" +{issue.folds} plegados" if issue.folds else ""
        add(
            f"  {issue.issue_id}  [{BANDS[issue.priority]:<7}] {issue.severity:5.1f}  "
            f"{pair}   ({issue.clash_count} cruces{folds})"
        )
        location = issue.level or f"z={issue.centroid[2]:.1f}"
        add(
            f"      Ubicación: {location}"
            + (f" / eje {issue.grid}" if issue.grid else "")
            + f"   Responsable: {profile.label(issue.responsible)}"
        )
        for reason in issue.why:
            add(f"      · {reason}")
        add(f"      → {issue.suggested_action}")

    zones = hotspots(result, limit=5)
    if zones:
        add("")
        add("-" * 78)
        add("  ZONAS CALIENTES")
        add("-" * 78)
        for zone in zones:
            add(
                f"    {str(zone['centre']):<26} {zone['issues']:>3} problemas  "
                f"severidad {zone['total_severity']:>6.1f}  "
                f"{', '.join(zone['disciplines'])}"
            )
    add("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="naviscoord", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("analyze", help="Analizar un export de clashes")
    run.add_argument("export", type=Path)
    run.add_argument("--profile", type=Path, default=None)
    run.add_argument("--limit", type=int, default=10)
    run.add_argument("--json", action="store_true", help="Salida JSON en vez de texto")

    demo = sub.add_parser("demo", help="Correr el motor sobre el caso sintético completo")
    demo.add_argument("--profile", type=Path, default=None)
    demo.add_argument("--limit", type=int, default=8)

    check = sub.add_parser("check-profile", help="Validar un perfil editado a mano")
    check.add_argument("profile", type=Path, nargs="?", default=None)

    args = parser.parse_args(argv)

    if args.command == "check-profile":
        profile = Profile.load(args.profile)
        problems = profile.validate()
        if problems:
            for problem in problems:
                print(f"  ! {problem}")
            return 1
        print(f"Perfil '{profile.name}' válido: {len(profile.disciplines)} disciplinas.")
        return 0

    profile = Profile.load(args.profile)

    if args.command == "demo":
        from . import samples

        raw = samples.full_project_case()
    else:
        raw = json.loads(args.export.read_text(encoding="utf-8"))

    result = analyze(ClashExport.from_json(raw), profile)

    if getattr(args, "json", False):
        print(json.dumps(result.summary(args.limit), ensure_ascii=False, indent=2))
    else:
        print(_render(result, profile, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
