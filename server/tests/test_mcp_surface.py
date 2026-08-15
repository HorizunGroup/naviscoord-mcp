"""The MCP surface, checked against whichever mcp release is installed.

Two failures live here, both of which shipped once and neither of which any
other test could see:

* All 27 tools registered, listed and described themselves perfectly while
  not one could be called, because the schema came from a wrapper that had
  not preserved its signature.
* mcp 2.0 removed the entry point the server imports, so a fresh install
  stopped working while a pinned development machine saw nothing.

These run under the installed release, so CI across versions is what gives
them their reach.
"""

from __future__ import annotations

import asyncio

import pytest
from naviscoord.mcp_server import mcp

# Asserted by NAME rather than by count. A count catches "somebody added a
# tool" — which is not a defect — and misses "somebody removed one", which is
# a breaking change for every saved prompt that calls it.
EXPECTED_TOOLS = {
    # session and targeting
    "navis_health",
    "navis_load_profile",
    "navis_profile_info",
    "navis_reset_profile",
    "navis_sessions",
    "navis_target",
    "navis_current_target",
    "navis_capabilities",
    "navis_output_policy",
    # preparation
    "navis_model_census",
    "navis_propose_disciplines",
    "navis_set_disciplines",
    "navis_build_sets",
    "navis_build_search_sets",
    "navis_list_search_sets",
    "navis_build_clash_matrix",
    "navis_list_tests",
    "navis_run_tests",
    # the four operational steps, same routes the ribbon buttons use
    "navis_audit_models",
    "navis_configure",
    "navis_run",
    "navis_group_levels",
    "navis_run_rules_workflow",
    # rule triage with caller-supplied pairs, as opposed to profile-supplied
    "navis_apply_clash_rules",
    # analysis
    "navis_analyze",
    "navis_issue_detail",
    "navis_root_causes",
    "navis_hotspots",
    "navis_decisions",
    "navis_discover",
    # deliverables
    "navis_narrative_report",
    "navis_work_plan",
    "navis_coordination_matrix",
    "navis_pdf_report",
    # write-back
    "navis_apply_groups",
    "navis_set_status",
    "navis_save_viewpoints",
    "navis_color_by_priority",
    "navis_reset_appearance",
    "navis_select_issue",
    # jobs
    "navis_job_status",
    "navis_jobs",
    "navis_cancel_job",
    # saving
    "navis_save",
    "navis_save_as",
    # ecosystem
    "navis_handoff",
    "navis_save_export",
}


def _schema(tool) -> dict:
    """1.x exposes inputSchema, 2.x renamed it input_schema.

    Reading only one name yields {} on the other release, which looks exactly
    like "this tool takes no parameters" — a false failure a checker must not
    manufacture.
    """
    for attr in ("input_schema", "inputSchema"):
        value = getattr(tool, attr, None)
        if value:
            return value
    return {}


@pytest.fixture(scope="module")
def tools():
    return asyncio.run(mcp.list_tools())


def test_every_tool_registers(tools):
    registered = {t.name for t in tools}
    assert registered == EXPECTED_TOOLS, (
        f"faltan: {sorted(EXPECTED_TOOLS - registered)}; "
        f"sobran: {sorted(registered - EXPECTED_TOOLS)}"
    )
    assert all(t.name.startswith("navis_") for t in tools)


def test_mutating_tools_accept_the_fingerprint_guard(tools):
    """Every write must let the caller say which document it meant.

    Without it a plan computed against one model lands on another, silently,
    because every call succeeds.
    """
    mutating = {
        "navis_apply_groups",
        "navis_set_status",
        "navis_save_viewpoints",
        "navis_color_by_priority",
        "navis_configure",
        "navis_run",
        "navis_group_levels",
        "navis_save",
        "navis_save_as",
    }
    by_name = {t.name: t for t in tools}
    for name in mutating:
        properties = set(_schema(by_name[name]).get("properties") or {})
        assert "expected_document_fingerprint" in properties, name


def test_save_as_requires_a_path_and_defaults_to_not_overwriting(tools):
    save_as = next(t for t in tools if t.name == "navis_save_as")
    schema = _schema(save_as)
    properties = schema.get("properties") or {}
    assert "path" in properties
    assert properties["overwrite"].get("default") is False
    assert "path" in (schema.get("required") or ["path"])


def test_no_tool_advertises_the_wrapper_signature(tools):
    """The regression that made all 27 uncallable.

    A decorator without functools.wraps presents itself as (*args, **kwargs),
    and the schema generator faithfully advertises two literal fields named
    "args" and "kwargs" — so every call fails validation before reaching any
    of this project's code.
    """
    offenders = [
        t.name for t in tools
        if {"args", "kwargs"} & set(_schema(t).get("properties") or {})
    ]
    assert not offenders, f"esquema tomado del envoltorio: {offenders}"


def test_parameters_survive_the_guard_decorator(tools):
    """A tool with arguments must still declare them after wrapping."""
    analyze = next(t for t in tools if t.name == "navis_analyze")
    assert set(_schema(analyze).get("properties") or {}) == {"tests", "limit", "top"}


def test_every_tool_carries_a_description(tools):
    """The description is what the model reads to decide when to call it."""
    silent = [t.name for t in tools if not (t.description or "").strip()]
    assert not silent


def test_server_runs_on_either_mcp_major():
    """The import alias must resolve to a class with the surface used here."""
    assert type(mcp).__name__ in {"FastMCP", "MCPServer"}
    for attribute in ("tool", "run", "list_tools", "call_tool"):
        assert hasattr(mcp, attribute), attribute
