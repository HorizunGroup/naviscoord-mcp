"""Portable, versioned analysis snapshots and deterministic delivery comparisons.

The comparison exists for one job: the week after. The trades send corrected
models, the federation is rebuilt and the tests run again, and the question is
what got fixed, what is new and what is still there. By construction the two
runs come from DIFFERENT models — refusing to compare them because the
document fingerprint changed refused the only case anyone needs.

So issues are paired by what survives a re-export, strongest evidence first:

1. the same issue id (identical member clashes);
2. the same authoring elements: Revit ElementId/UniqueId of BOTH sides, scoped
   to their source file, in the same pair of disciplines;
3. a clash GUID both runs share — Navisworks keeps it while the same two
   items still collide in the same test;
4. position: the same pair of disciplines within a tolerance.

Each step pairs one-to-one and only among what earlier steps left, so a
weaker key never overrides a stronger one. Position pairs are reported as
such: they are a reasonable guess, not an identity.
"""
from datetime import datetime, timezone
from typing import Any, Iterable
import json
import math
from collections import Counter
from pathlib import Path

SCHEMA = "naviscoord.analysis-snapshot/1"
COMPARISON_SCHEMA = "naviscoord.analysis-comparison/2"

#: Property names that carry an authoring identity, strongest first. "Id" is
#: what a local Revit NWC publishes for the ElementId; it is only trusted
#: scoped to its source file.
AUTHORING_ID_KEYS = ("UniqueId", "Unique Id", "IFC GUID", "Element Id", "Id")

#: How far an issue may drift and still be "the same" when its identity is
#: carried by its elements or a shared clash GUID. A corrected duct moves a few
#: hundred millimetres; a different crossing of the same two long elements is
#: metres away.
IDENTITY_DRIFT_M = 2.0

#: A geometry change below this is export noise, not a model change.
BBOX_CHANGE_M = 0.005

PRIORITIES = ("critical", "high", "medium", "low")


def load_snapshot(path: str) -> dict:
    # Bound the read itself, including files that grow after opening.
    with Path(path).open("rb") as stream:
        data = stream.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("Snapshot exceeds the 32 MiB input budget.")
    return json.loads(data)


def authoring_key(element) -> str:
    """`source_file:id` for an element, or "" when it has no authoring id."""
    authoring_id = element.prop(*AUTHORING_ID_KEYS).strip()
    source = (element.source_file or "").strip().casefold()
    return f"{source}:{authoring_id}" if authoring_id and source else ""


def _clash_index(result) -> dict[str, Any]:
    filtering = getattr(result, "filtering", None)
    return {clash.guid: clash for clash in (filtering.kept if filtering else [])}


def _issue_keys(issue, clashes: dict[str, Any]) -> dict[str, Any]:
    pairs: set[str] = set()
    tests: set[str] = set()
    for guid in issue.clash_ids:
        clash = clashes.get(guid)
        if clash is None:
            continue
        if clash.test:
            tests.add(clash.test)
        left, right = authoring_key(clash.a), authoring_key(clash.b)
        if left and right:
            pairs.add("~".join(sorted((left, right))))
    return {"clash_guids": sorted(issue.clash_ids), "element_pairs": sorted(pairs), "tests": sorted(tests)}


def _element_inventory(clashes: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Every authoring element in conflict, with the union of its boxes.

    One ElementId can arrive as several Navisworks items (the layers of a
    floor), so the box is the union. Comparing these boxes between runs is
    what tells which model actually moved.
    """
    inventory: dict[str, dict[str, Any]] = {}
    for clash in clashes:
        for side in (clash.a, clash.b):
            key = authoring_key(side)
            if not key:
                continue
            entry = inventory.setdefault(key, {"model": side.source_file, "discipline": side.discipline,
                                               "bbox": [*side.bbox_min, *side.bbox_max]})
            box = entry["bbox"]
            entry["bbox"] = [min(box[i], side.bbox_min[i]) for i in range(3)] + \
                            [max(box[3 + i], side.bbox_max[i]) for i in range(3)]
    for entry in inventory.values():
        entry["bbox"] = [round(v, 3) for v in entry["bbox"]]
    return inventory


def snapshot(result, provenance: dict[str, Any], export=None) -> dict[str, Any]:
    clashes = _clash_index(result)
    issues = []
    for issue in result.issues:
        payload = issue.to_json()
        payload["match_keys"] = _issue_keys(issue, clashes)
        issues.append(payload)
    data = {"schema": SCHEMA, "created_utc": datetime.now(timezone.utc).isoformat(),
            "provenance": provenance, "issues": issues,
            "coverage": {"raw_clashes": result.raw_clash_count,
                         "matrix": result.coverage.to_json(),
                         "noise": result.filtering.to_json() if result.filtering else {}},
            "warnings": list(result.warnings),
            "elements": _element_inventory(clashes.values())}
    if export is not None:
        data["models"] = [{"source_file": m.source_file, "source_path": m.source_path} for m in export.models]
    return data


# ------------------------------------------------------------------ compare

def _validate(value: Any) -> None:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or not isinstance(value.get("issues"), list):
        raise ValueError("Not a NavisCoord analysis snapshot.")
    if any(not isinstance(i, dict) or not isinstance(i.get("issue_id"), str)
           or not i["issue_id"] for i in value["issues"]):
        raise ValueError("Snapshot contains malformed issue identities.")
    if not isinstance(value.get("provenance"), dict) or not value["provenance"].get("document_fingerprint"):
        raise ValueError("Snapshot lacks document provenance.")
    ids = [i["issue_id"] for i in value["issues"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Snapshot contains duplicate issue identities.")


def _pair(issue: dict) -> tuple[str, ...]:
    return tuple(sorted(str(d) for d in issue.get("discipline_pair") or ()))


def _centroid(issue: dict) -> tuple[float, float, float] | None:
    raw = issue.get("centroid")
    try:
        x, y, z = raw
        return float(x), float(y), float(z)
    except (TypeError, ValueError):
        return None


def _distance(a: dict, b: dict) -> float:
    ca, cb = _centroid(a), _centroid(b)
    return math.dist(ca, cb) if ca and cb else math.inf


def _keys(issue: dict, name: str) -> set[str]:
    keys = issue.get("match_keys") or {}
    values = set(keys.get(name) or ())
    if name == "clash_guids" and issue.get("representative_clash"):
        # Snapshots written before match_keys existed only carry this one.
        values.add(issue["representative_clash"])
    return {v for v in values if isinstance(v, str) and v}


def _match(previous: list[dict], current: list[dict], tolerance_m: float) -> list[tuple[dict, dict, str, float]]:
    free_before = {i["issue_id"]: i for i in previous}
    free_after = {i["issue_id"]: i for i in current}
    matches: list[tuple[dict, dict, str, float]] = []

    def take(candidates: list[tuple[tuple, str, str]], method: str) -> None:
        # Globally best first, so input order never decides who gets a match.
        for _, before_id, after_id in sorted(candidates):
            if before_id in free_before and after_id in free_after:
                before, after = free_before.pop(before_id), free_after.pop(after_id)
                matches.append((before, after, method, _distance(before, after)))

    # 1. Same id: the same member clashes, nothing to argue about.
    take([((0,), k, k) for k in free_before.keys() & free_after.keys()], "issue_id")

    # 2 and 3. Shared identities, inside the drift bound and the same trades.
    for name, method in (("element_pairs", "elements"), ("clash_guids", "clash_guid")):
        index: dict[str, list[str]] = {}
        for after in free_after.values():
            for key in _keys(after, name):
                index.setdefault(key, []).append(after["issue_id"])
        candidates = []
        for before in free_before.values():
            mine = _keys(before, name)
            shared: Counter[str] = Counter(a for key in mine for a in index.get(key, ()))
            for after_id, count in shared.items():
                after = free_after[after_id]
                gap = _distance(before, after)
                if _pair(before) == _pair(after) and gap <= max(IDENTITY_DRIFT_M, tolerance_m):
                    candidates.append(((-count, gap, before["issue_id"]), before["issue_id"], after_id))
        take(candidates, method)

    # 4. Position: same trades, within the tolerance. A grid keeps it linear.
    if tolerance_m > 0:
        cell = tolerance_m
        grid: dict[tuple, list[dict]] = {}
        for after in free_after.values():
            c = _centroid(after)
            if c:
                grid.setdefault((_pair(after), *(math.floor(v / cell) for v in c)), []).append(after)
        candidates = []
        for before in free_before.values():
            c = _centroid(before)
            if not c:
                continue
            base = [math.floor(v / cell) for v in c]
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for after in grid.get((_pair(before), base[0] + dx, base[1] + dy, base[2] + dz), ()):
                            gap = _distance(before, after)
                            if gap <= tolerance_m:
                                candidates.append(((gap, before["issue_id"]), before["issue_id"], after["issue_id"]))
        take(candidates, "position")
    return matches


def _differences(before: dict, after: dict) -> dict[str, list]:
    out: dict[str, list] = {}
    for field in ("priority", "responsible", "clash_count"):
        if before.get(field) != after.get(field):
            out[field] = [before.get(field), after.get(field)]
    for field, threshold in (("severity", 1.0), ("max_penetration_mm", 10.0)):
        a, b = before.get(field), after.get(field)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(a - b) >= threshold:
                out[field] = [a, b]
        elif a != b:
            out[field] = [a, b]
    moved = _distance(before, after)
    if math.isfinite(moved) and moved > 0.1:
        out["centroid_moved_m"] = [0, round(moved, 3)]
    return out


def _brief(issue: dict) -> dict[str, Any]:
    return {key: issue.get(key) for key in ("issue_id", "priority", "severity", "discipline_pair",
                                            "responsible", "level", "max_penetration_mm", "centroid")}


def _breakdown(issues: list[dict]) -> dict[str, dict[str, int]]:
    def count(values):
        return dict(Counter(values).most_common())
    return {"priority": count(i.get("priority") for i in issues),
            "discipline_pair": count("-".join(_pair(i)) for i in issues),
            "responsible": count(i.get("responsible") or "" for i in issues),
            "level": count(i.get("level") or "" for i in issues)}


def _ranked(issues: list[dict]) -> list[dict]:
    return sorted(issues, key=lambda i: (-(i.get("severity") or 0), i["issue_id"]))


def _model_changes(previous: dict, current: dict) -> dict[str, Any]:
    """Which models moved between the runs, from the elements in conflict."""
    before, after = previous.get("elements"), current.get("elements")
    out: dict[str, Any] = {}
    names_before = {m.get("source_file") for m in previous.get("models") or [] if isinstance(m, dict)}
    names_after = {m.get("source_file") for m in current.get("models") or [] if isinstance(m, dict)}
    if names_before and names_after:
        out["added"] = sorted(n for n in names_after - names_before if n)
        out["removed"] = sorted(n for n in names_before - names_after if n)
    if not isinstance(before, dict) or not isinstance(after, dict):
        out["per_model"] = []
        out["note"] = ("La foto anterior no trae la identidad de los elementos (es de una versión previa): "
                       "no se puede decir qué modelo cambió, solo que el federado cambió.")
        return out
    models: dict[str, Counter] = {}
    for key in before.keys() | after.keys():
        a, b = before.get(key), after.get(key)
        model = (b or a).get("model") or key.split(":", 1)[0]
        tally = models.setdefault(model, Counter())
        if a and b:
            tally["compared"] += 1
            if any(abs(x - y) > BBOX_CHANGE_M for x, y in zip(a.get("bbox") or (), b.get("bbox") or ())):
                tally["geometry_changed"] += 1
        elif a:
            tally["only_before"] += 1
        else:
            tally["only_after"] += 1
    per_model = []
    for model, tally in sorted(models.items()):
        changed = tally["geometry_changed"] > 0
        per_model.append({
            "model": model, "changed": changed,
            "elements_compared": tally["compared"], "elements_geometry_changed": tally["geometry_changed"],
            "elements_only_before": tally["only_before"], "elements_only_after": tally["only_after"],
            "verdict": (f"cambió: {tally['geometry_changed']} de sus elementos en conflicto tienen otra geometría"
                        if changed else "sin cambios en la geometría de sus elementos en conflicto"),
        })
    out["per_model"] = per_model
    out["changed"] = [m["model"] for m in per_model if m["changed"]]
    out["note"] = ("Se juzga por los elementos que aparecen en algún conflicto, comparando su caja por "
                   "ElementId: un cambio fuera de los conflictos no se ve aquí.")
    return out


def compare(previous: dict, current: dict, *, tolerance_m: float = 0.5,
            detail_limit: int = 25, include_matches: bool = False) -> dict:
    for value in (previous, current):
        _validate(value)
    if not isinstance(tolerance_m, (int, float)) or not 0 <= tolerance_m <= 5:
        raise ValueError("tolerance_m must be between 0 and 5 metres.")
    before_prov, after_prov = previous.get("provenance", {}), current.get("provenance", {})
    federation_changed = before_prov.get("document_fingerprint") != after_prov.get("document_fingerprint")
    profile_changed = before_prov.get("profile_checksum") != after_prov.get("profile_checksum")
    coverage_changed = previous.get("coverage") != current.get("coverage")

    matches = _match(previous["issues"], current["issues"], float(tolerance_m))
    matched_before = {b["issue_id"] for b, _, _, _ in matches}
    matched_after = {a["issue_id"] for _, a, _, _ in matches}
    resolved = [i for i in previous["issues"] if i["issue_id"] not in matched_before]
    new = [i for i in current["issues"] if i["issue_id"] not in matched_after]
    changed = []
    for before, after, method, _ in matches:
        diff = _differences(before, after)
        if diff:
            changed.append({"previous": before["issue_id"], "current": after["issue_id"], "changes": diff})
    changed.sort(key=lambda c: c["current"])
    methods = Counter(method for _, _, method, _ in matches)
    persisting = sorted(a["issue_id"] for _, a, _, _ in matches)

    priority_moves = Counter()
    for before, after, _, _ in matches:
        if before.get("priority") in PRIORITIES and after.get("priority") in PRIORITIES:
            step = PRIORITIES.index(after["priority"]) - PRIORITIES.index(before["priority"])
            priority_moves["up" if step < 0 else "down" if step > 0 else "same"] += 1

    warnings = []
    if federation_changed:
        warnings.append("Los modelos cambiaron entre las dos corridas (otra huella del documento): se "
                        "emparejó por identidad de elementos, GUID del cruce y posición, no por id del problema.")
    if methods.get("position"):
        warnings.append(f"{methods['position']} problemas se emparejaron solo por posición (misma pareja de "
                        f"disciplinas a menos de {tolerance_m:g} m): alguno podría no ser el mismo.")
    if profile_changed:
        warnings.append("El perfil cambió entre corridas: severidades y filtros no son comparables uno a uno.")
    if coverage_changed:
        warnings.append("La cobertura de pruebas o el filtro de ruido cambió entre corridas.")

    payload: dict[str, Any] = {
        "schema": COMPARISON_SCHEMA,
        "summary": {"previous_issues": len(previous["issues"]), "current_issues": len(current["issues"]),
                    "resueltas": len(resolved), "nuevas": len(new), "siguen": len(matches),
                    "cambiaron": len(changed)},
        "new": sorted(i["issue_id"] for i in new),
        "no_longer_reported": sorted(i["issue_id"] for i in resolved),
        "persisting": persisting,
        "changed": [c["current"] for c in changed],
        "match_methods": dict(sorted(methods.items())),
        "tolerance_m": tolerance_m,
        "priority_moves": {k: priority_moves.get(k, 0) for k in ("up", "down", "same")},
        "resolved_breakdown": _breakdown(resolved),
        "new_breakdown": _breakdown(new),
        "resolved_detail": [_brief(i) for i in _ranked(resolved)[:detail_limit]],
        "new_detail": [_brief(i) for i in _ranked(new)[:detail_limit]],
        "changed_detail": changed[:detail_limit],
        "federation_changed": federation_changed,
        "models": _model_changes(previous, current),
        "previous_provenance": before_prov,
        "current_provenance": after_prov,
        "profile_changed": profile_changed,
        "coverage_changed": coverage_changed,
        "warnings": warnings,
        "resolution_verified": False,
        "interpretation": ("Absence from a later analysis is not closure proof; check test coverage, "
                           "status and filter criteria."),
    }
    if include_matches:
        payload["matches"] = [{"previous": b["issue_id"], "current": a["issue_id"], "method": m,
                               "distance_m": round(d, 3) if math.isfinite(d) else None}
                              for b, a, m, d in matches]
    return payload
