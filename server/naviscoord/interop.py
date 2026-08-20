"""Handoff to the tools downstream of coordination.

A coordination issue that stops at "there is a clash at these coordinates"
is a dead end. Somebody still has to open the authoring model, find the
element, and change it — and somebody else has to know what that rework
costs. This module produces the two artifacts that close those loops:

* **Revit targets** — issues resolved down to Revit ElementIds, grouped by
  source model, so the Revit MCP can select, annotate or fix them directly.
* **Power BI tables** — a star schema keyed on the same codes the budget
  already uses, so coordination status joins to cost and progress instead of
  living in its own spreadsheet.

The join keys are named in the profile, never hardcoded. A project that
codes its elements differently changes the profile, not this file.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .analysis.pipeline import AnalysisResult
from .model import ClashExport, ElementRef, Issue
from .paths import OutputPolicy, PathPolicyError
from .paths import policy as default_policy
from .profile import Profile
from .safety import sanitize_csv, sanitize_row

HANDOFF_SCHEMA = "naviscoord.coordination/1"
_BUNDLE_LOCK = ".naviscoord-handoff.lock"
_BUNDLE_MANIFEST = "handoff_manifest.json"


@contextmanager
def _handoff_lock(directory: Path) -> Iterator[None]:
    """Hold a process-wide filesystem lock for one handoff directory."""
    lock_path = directory / _BUNDLE_LOCK
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - exercised by Linux CI
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise PathPolicyError(
                f"Ya se está publicando otro handoff en «{directory}»."
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - exercised by Linux CI
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    """Render one table completely before any bundle file is published."""
    if not rows:
        return b"\xef\xbb\xbf"
    text = io.StringIO(newline="")
    fieldnames = list(rows[0].keys())
    writer = csv.writer(text)
    writer.writerow([sanitize_csv(name) for name in fieldnames])
    for row in rows:
        safe = sanitize_row(row)
        writer.writerow([safe.get(name, "") for name in fieldnames])
    return b"\xef\xbb\xbf" + text.getvalue().encode("utf-8")


def _replace_file(source: Path, destination: Path) -> None:
    """Patch seam for failure-injection tests; both paths share a volume."""
    os.replace(source, destination)


def _publish_bundle(
    directory: Path,
    artifacts: dict[str, bytes],
    policy: OutputPolicy,
    *,
    overwrite: bool,
) -> list[str]:
    """Publish all files as one rollback-capable writer transaction."""
    run_id = uuid.uuid4().hex
    manifest = {
        "schema": "naviscoord.handoff-manifest/1",
        "run_id": run_id,
        "artifacts": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in sorted(artifacts.items())
        },
    }
    all_artifacts = dict(artifacts)
    all_artifacts[_BUNDLE_MANIFEST] = json.dumps(
        manifest, ensure_ascii=False, indent=2
    ).encode("utf-8")

    with _handoff_lock(directory):
        targets: dict[str, Path] = {}
        for name in all_artifacts:
            targets[name] = policy.resolve_file(
                directory / name, overwrite=overwrite
            ).path

        staged: dict[str, Path] = {}
        backups: dict[str, Path] = {}
        published: list[Path] = []
        try:
            for name, data in all_artifacts.items():
                temp = directory / f".{name}.{run_id}.stage"
                temp.write_bytes(data)
                staged[name] = temp

            for name, target in targets.items():
                if target.exists():
                    backup = directory / f".{name}.{run_id}.backup"
                    os.replace(target, backup)
                    backups[name] = backup

            order = [n for n in all_artifacts if n != _BUNDLE_MANIFEST]
            order.append(_BUNDLE_MANIFEST)
            for name in order:
                _replace_file(staged[name], targets[name])
                published.append(targets[name])

        except BaseException:
            for target in reversed(published):
                try:
                    target.unlink()
                except OSError:
                    pass
            for name, backup in backups.items():
                if backup.exists():
                    os.replace(backup, targets[name])
            raise
        finally:
            for path in list(staged.values()) + list(backups.values()):
                try:
                    path.unlink()
                except OSError:
                    pass

    return [str(targets[name]) for name in all_artifacts]


def _first_prop(element: ElementRef, keys: Iterable[str]) -> str:
    for key in keys:
        value = element.prop(key)
        if value:
            return value
    return ""


class ElementIndex:
    """path_id -> element, built once and shared by every exporter."""

    def __init__(self, export: ClashExport) -> None:
        self._by_path: dict[str, ElementRef] = {}
        for clash in export.clashes:
            for side in (clash.a, clash.b):
                self._by_path.setdefault(side.path_id, side)

    def get(self, path_id: str) -> ElementRef | None:
        return self._by_path.get(path_id)

    def resolve(self, path_ids: Iterable[str]) -> list[ElementRef]:
        return [e for e in (self.get(p) for p in path_ids) if e is not None]


def build_targets(
    issue: Issue, index: ElementIndex, profile: Profile
) -> list[dict[str, Any]]:
    """Every element involved in an issue, with its cross-tool identities."""
    interop = profile.section("interop")
    id_keys = interop.get("revit_element_id_keys") or ["Element Id"]
    budget_keys = interop.get("budget_code_keys") or []
    class_keys = interop.get("classification_keys") or []

    targets: list[dict[str, Any]] = []
    for side, path_ids in (("a", issue.elements_a), ("b", issue.elements_b)):
        for element in index.resolve(path_ids):
            element_id = _first_prop(element, id_keys)
            targets.append(
                {
                    "side": side,
                    "path_id": element.path_id,
                    "revit_element_id": element_id,
                    "source_file": element.source_file,
                    "discipline": element.discipline,
                    "category": element.category,
                    "name": element.display_name,
                    "budget_code": _first_prop(element, budget_keys),
                    "classification": _first_prop(element, class_keys),
                    # Actionable only if the authoring tool can find it again.
                    "actionable_in_revit": bool(element_id),
                }
            )
    return targets


def build_handoff(
    result: AnalysisResult, export: ClashExport, profile: Profile
) -> dict[str, Any]:
    """The canonical payload downstream tools consume."""
    index = ElementIndex(export)
    issues: list[dict[str, Any]] = []

    for issue in result.issues:
        payload = issue.to_json()
        payload["targets"] = build_targets(issue, index, profile)
        issues.append(payload)

    traceable = sum(
        1
        for issue in issues
        if any(t["actionable_in_revit"] for t in issue["targets"])
    )

    return {
        "schema": profile.section("interop").get("handoff_schema", HANDOFF_SCHEMA),
        "source": {
            "tool": "naviscoord",
            "profile": profile.name,
            "document": export.document_title,
            "document_path": export.document_path,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "totals": {
            "raw_clashes": result.raw_clash_count,
            "issues": len(result.issues),
            "decisions": len(result.decisions),
            "traceable_to_revit": traceable,
            "by_priority": result.by_priority(),
            "by_responsible": result.by_responsible(),
        },
        "root_causes": [cause.to_json() for cause in result.root_causes],
        "issues": issues,
        # Stated rather than assumed: an issue with no Revit id can still be
        # coordinated in Navisworks, it just cannot be routed back upstream.
        "caveats": _caveats(issues, traceable),
    }


def _caveats(issues: list[dict[str, Any]], traceable: int) -> list[str]:
    out: list[str] = []
    if issues and traceable < len(issues):
        missing = len(issues) - traceable
        out.append(
            f"{missing} de {len(issues)} problemas no tienen Element Id de Revit en ningún "
            "elemento, así que no se pueden enrutar de vuelta al modelo de autoría. "
            "Suele significar que el NWC se exportó sin propiedades de elemento."
        )
    return out


def revit_worklist(handoff: dict[str, Any]) -> dict[str, Any]:
    """Issues regrouped the way the Revit MCP wants them: by source model.

    A coordinator fixes one discipline model at a time, not one issue at a
    time. Grouping by file means a single Revit session can close everything
    that model owns.
    """
    by_model: dict[str, dict[str, Any]] = {}

    for issue in handoff["issues"]:
        if issue.get("folded_into"):
            # The representative carries the fix; a folded issue would send
            # someone to do the same edit twice.
            continue
        for target in issue["targets"]:
            if not target["actionable_in_revit"]:
                continue
            if target["discipline"] != issue.get("responsible"):
                # Only the side that is expected to move gets a work item.
                continue

            bucket = by_model.setdefault(
                target["source_file"],
                {"source_file": target["source_file"], "discipline": target["discipline"], "items": []},
            )
            bucket["items"].append(
                {
                    "issue_id": issue["issue_id"],
                    "priority": issue["priority"],
                    "severity": issue["severity"],
                    "revit_element_id": target["revit_element_id"],
                    "element": target["name"],
                    "level": issue.get("level", ""),
                    "action": issue.get("suggested_action", ""),
                    "root_cause": (issue.get("root_cause") or {}).get("title", ""),
                }
            )

    for bucket in by_model.values():
        bucket["items"].sort(key=lambda i: -i["severity"])
        bucket["count"] = len(bucket["items"])

    return {
        "schema": "naviscoord.coordination.worklist/1",
        "source": handoff["source"],
        "models": sorted(by_model.values(), key=lambda m: -m["count"]),
    }


# ------------------------------------------------------------ Power BI


def powerbi_tables(handoff: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Star schema: one fact table, three dimensions, one bridge.

    Shaped for a model that already joins on the budget code, so coordination
    lands next to cost and progress rather than in a parallel universe.
    """
    fact: list[dict[str, Any]] = []
    bridge: list[dict[str, Any]] = []
    causes: list[dict[str, Any]] = []

    for issue in handoff["issues"]:
        root = issue.get("root_cause") or {}
        fact.append(
            {
                "issue_id": issue["issue_id"],
                "priority": issue["priority"],
                "severity": issue["severity"],
                "clash_count": issue["clash_count"],
                "discipline_a": issue["discipline_pair"][0] if issue["discipline_pair"] else "",
                "discipline_b": issue["discipline_pair"][1] if len(issue["discipline_pair"]) > 1 else "",
                "responsible": issue.get("responsible", ""),
                "immovable_side": issue.get("immovable_side", ""),
                "level": issue.get("level", ""),
                "max_penetration_mm": issue.get("max_penetration_mm", 0.0),
                "x": issue["centroid"][0],
                "y": issue["centroid"][1],
                "z": issue["centroid"][2],
                "cause_id": root.get("cause_id", ""),
                "is_decision": 0 if issue.get("folded_into") else 1,
                "also_resolves": issue.get("also_resolves", 0),
                "suggested_action": issue.get("suggested_action", ""),
            }
        )

        for target in issue["targets"]:
            bridge.append(
                {
                    "issue_id": issue["issue_id"],
                    "revit_element_id": target["revit_element_id"],
                    "budget_code": target["budget_code"],
                    "classification": target["classification"],
                    "source_file": target["source_file"],
                    "discipline": target["discipline"],
                    "category": target["category"],
                    "element": target["name"],
                }
            )

    for cause in handoff.get("root_causes", []):
        causes.append(
            {
                "cause_id": cause.get("cause_id", ""),
                "kind": cause["kind"],
                "title": cause["title"],
                "confidence": cause["confidence"],
                "affected_issues": cause["affected_issues"],
                "clash_count": cause["clash_count"],
                "suggested_action": cause["suggested_action"],
            }
        )

    disciplines = sorted({row["discipline"] for row in bridge if row["discipline"]})
    return {
        "fct_issues": fact,
        "brg_issue_elements": bridge,
        "dim_root_causes": causes,
        "dim_disciplines": [{"discipline": d} for d in disciplines],
    }


# ---------------------------------------------------------------- output


def write_handoff(
    directory: Path,
    result: AnalysisResult,
    export: ClashExport,
    profile: Profile,
    *,
    overwrite: bool = True,
    output_policy: OutputPolicy | None = None,
) -> dict[str, Any]:
    """Writes every ecosystem artifact and reports exactly what it produced.

    The destination is authorised once, as a directory, and each artifact is
    then authorised individually — so a policy change between the two, or a
    filename that would climb out of the directory, still fails closed.

    `overwrite` defaults to True here and False for the PDF, deliberately:
    a handoff is a regenerated snapshot of one analysis, and refusing to
    refresh it would make the tool unusable on the second run. The PDF is a
    deliverable someone may have already sent.
    """
    policy_in_use = output_policy or default_policy()
    target_dir = policy_in_use.resolve_dir(directory)
    directory = target_dir.path

    handoff = build_handoff(result, export, profile)
    worklist = revit_worklist(handoff)
    tables = powerbi_tables(handoff)

    artifacts: dict[str, bytes] = {}
    for name, payload in (
        ("coordination_handoff.json", handoff),
        ("revit_worklist.json", worklist),
    ):
        artifacts[name] = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    for name, rows in tables.items():
        artifacts[f"{name}.csv"] = _csv_bytes(rows)

    written = _publish_bundle(
        directory, artifacts, policy_in_use, overwrite=overwrite
    )

    return {
        "written": written,
        "manifest": str(directory / _BUNDLE_MANIFEST),
        "directory": str(directory),
        "allowed_root": str(target_dir.root),
        "issues": len(handoff["issues"]),
        "traceable_to_revit": handoff["totals"]["traceable_to_revit"],
        "revit_models_with_work": len(worklist["models"]),
        "caveats": handoff["caveats"],
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    # utf-8-sig: Power BI and Excel both guess the wrong codepage without it,
    # and Spanish column values come back mangled.
    #
    # Every cell goes through sanitize_row first. These tables are opened in
    # Excel and Power BI, and a value like `=cmd|'/c calc'!A1` — perfectly
    # legal as a Revit type name — executes on open. Numbers, including
    # negative coordinates and depths, pass through untouched.
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([sanitize_csv(name) for name in fieldnames])
        for row in rows:
            safe = sanitize_row(row)
            writer.writerow([safe.get(name, "") for name in fieldnames])
