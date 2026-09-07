"""Live release acceptance on Autodesk's gatehouse sample, without saving it.

Open a dedicated sample session and pass its PID with --allow-sample-writes.
This creates QA sets/tests/groups in memory and writes reports to the configured
output directory. The source NWD is never saved by this script.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from naviscoord import mcp_server as m
from naviscoord.bridge import BridgeError


def completed(data):
    if data.get("error") or data.get("status") in {"failed", "partial", "cancelled"}:
        raise AssertionError(data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--allow-sample-writes", action="store_true", required=True)
    parser.add_argument("--resume-qa", action="store_true", help="Resume this script's explicitly selected QA session after a harness interruption")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    b = m.STATE.bridge
    assert b.resolve().pid == args.pid, "Target is not the explicitly supplied QA session"
    health = b.health()
    sample = Path(health["document"]["path"])
    assert sample.name == "gatehouse_pub.nwd" and sample.parent.name == "gatehouse"
    assert "Autodesk" in sample.parts and "Samples" in sample.parts
    assert args.resume_qa or not health["document"]["modified"], "Use a fresh, unmodified sample session"
    fingerprint = health["document_fingerprint"]
    evidence = {"addin": health["addin_version"], "navisworks": health["navisworks"], "checks": {}}
    folder = {"folder": "NC RELEASE ACCEPTANCE", "sets": [
        {"name": "QA structure", "conditions": [{"tab": "Elemento", "property": "Capa", "test": "contains", "value": "S_"}]},
        {"name": "QA brick", "conditions": [{"tab": "Elemento", "property": "Capa", "test": "contains", "value": "BRICK"}]},
    ]}
    sets = completed(b.call("sets/build_search", {"folders": [folder], "dry_run": False,
                     "expected_document_fingerprint": fingerprint}))
    assert sets["verified"] == 1 and set(sets["verified_in_document"][0]["children"]) == {"QA structure", "QA brick"}, sets
    evidence["checks"]["nested_search_sets"] = "passed"
    print("Nested sets verified", flush=True)
    pair = {"a": "structure", "b": "brick", "sets_a": ["QA structure"], "sets_b": ["QA brick"], "type": "Hard", "tolerance_m": .001}
    for invalid in ({**pair, "sets_a": ["missing QA set"]}, {**pair, "type": "invalid"}):
        try:
            response = b.build_matrix([invalid], "QA INVALID", False, False, fingerprint)
        except BridgeError:
            pass
        else:
            assert response.get("error") or response.get("status") == "failed", response
    evidence["checks"]["invalid_matrix_refused"] = "passed"
    matrix = completed(b.build_matrix([pair], "QA RELEASE", False, False, fingerprint))
    assert matrix["verified"] == 1 or (args.resume_qa and matrix["created"] == 0 and matrix["planned"][0]["skipped"]), matrix
    test_name = "QA RELEASE structure vs brick"
    job = b.submit_job("clash/run", {"tests": [test_name], "expected_document_fingerprint": fingerprint})
    deadline = time.monotonic() + 120
    while True:
        job = b.job_status(job["job_id"])
        if job.get("state") in {"completed", "failed", "partial", "cancelled"}:
            assert job["state"] == "completed", job
            completed(job.get("result", {}))
            break
        if time.monotonic() > deadline:
            raise TimeoutError("Sample clash run did not complete")
        time.sleep(.5)
    completed(m.navis_analyze(tests=[test_name]))
    print("Clash run and analysis verified", flush=True)
    result = m.STATE.require_fresh_result()
    assert result.raw_clash_count > 0 and result.issues
    evidence["raw_clashes"] = result.raw_clash_count
    evidence["issues"] = len(result.issues)
    saved = completed(m.navis_snapshot("release-live-snapshot.json", overwrite=True))
    same = completed(m.navis_compare_snapshot(saved["path"]))
    assert not same["new"] and not same["no_longer_reported"], same
    evidence["checks"]["snapshot_roundtrip"] = "passed"
    preview = completed(m.navis_apply_groups(limit=2, dry_run=True, expected_document_fingerprint=fingerprint))
    assert preview["applied"] == 0 and preview["requested"] > 0, preview
    written = completed(m.navis_apply_groups(limit=2, dry_run=False, expected_document_fingerprint=fingerprint))
    assert written["verified"] > 0, written
    stale = m.navis_snapshot("stale-must-not-write.json", overwrite=True)
    assert stale.get("error"), stale
    evidence["checks"]["groups_verified_and_stale_analysis_refused"] = "passed"
    print("Group write-back and stale-analysis refusal verified", flush=True)
    completed(m.navis_analyze(tests=[test_name]))
    pdf = completed(m.navis_pdf_report("release-live-acceptance.pdf", max_issues=2, with_images=True, overwrite=True))
    evidence["report"] = pdf
    evidence["checks"]["pdf_created"] = "passed"
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"checks": evidence["checks"], "raw_clashes": evidence["raw_clashes"], "issues": evidence["issues"]}, indent=2))


if __name__ == "__main__":
    main()
