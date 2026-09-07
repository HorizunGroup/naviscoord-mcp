"""Portable, versioned analysis snapshots and deterministic delivery comparisons."""
from datetime import datetime, timezone
from typing import Any
import json
from pathlib import Path

SCHEMA = "naviscoord.analysis-snapshot/1"


def load_snapshot(path: str) -> dict:
    # Bound the read itself, including files that grow after opening.
    with Path(path).open("rb") as stream:
        data = stream.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("Snapshot exceeds the 32 MiB input budget.")
    return json.loads(data)


def snapshot(result, provenance: dict[str, Any]) -> dict[str, Any]:
    return {"schema": SCHEMA, "created_utc": datetime.now(timezone.utc).isoformat(),
            "provenance": provenance, "issues": [i.to_json() for i in result.issues],
            "coverage": {"raw_clashes": result.raw_clash_count,
                         "matrix": result.coverage.to_json(),
                         "noise": result.filtering.to_json() if result.filtering else {}},
            "warnings": list(result.warnings)}


def compare(previous: dict, current: dict) -> dict:
    for value in (previous, current):
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
    a, b = ({i["issue_id"]: i for i in v["issues"]} for v in (previous, current))
    before, after = previous.get("provenance", {}), current.get("provenance", {})
    if before.get("document_fingerprint") != after.get("document_fingerprint"):
        raise ValueError("Snapshots belong to different federations; compare matching source models.")
    profile_changed = before.get("profile_checksum") != after.get("profile_checksum")
    coverage_changed = previous.get("coverage") != current.get("coverage")
    persisted = sorted(a.keys() & b.keys())
    return {"schema": "naviscoord.analysis-comparison/1", "new": sorted(b.keys()-a.keys()),
            "no_longer_reported": sorted(a.keys()-b.keys()), "persisting": persisted,
            "changed": [key for key in persisted if any(a[key].get(k) != b[key].get(k)
                        for k in ("severity", "priority", "responsible", "centroid", "max_penetration_m"))],
            "profile_changed": profile_changed,
            "coverage_changed": coverage_changed,
            "resolution_verified": False,
            "interpretation": "Absence from a later analysis is not closure proof; check test coverage, status and filter criteria."}
