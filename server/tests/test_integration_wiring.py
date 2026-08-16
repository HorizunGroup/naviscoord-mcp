"""Does the wiring actually connect? Read both sides and compare.

A new class with its own unit test is not an implementation. These assertions
read the real C# sources and the real Python sources and check they agree
about what exists — the failure mode being a capability that is beautifully
tested in isolation and reachable by nobody.

Deliberately source-level rather than behavioural: behaviour needs Navisworks,
but "the tool calls a route the Router does not serve" is a defect that can be
caught here, on every push, in milliseconds.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[2]
ADDIN = ROOT / "addin" / "NavisCoord.Addin"
SERVER = ROOT / "server" / "naviscoord"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_json_file(path: Path) -> dict:
    import json

    return json.loads(read(path))


# ------------------------------------------------------------- extraction


def router_routes() -> set[str]:
    """Routes the C# Router dispatches."""
    return set(
        re.findall(
            r'\["([a-z0-9_]+/[a-z0-9_]+|health|census|capabilities)"\]\s*=',
            read(ADDIN / "Router.cs"),
        )
    )


def router_job_routes() -> set[str]:
    """Routes declared runnable as a background job."""
    source = read(ADDIN / "Router.cs")
    block = source.split("_jobRoutes = new Dictionary", 1)
    if len(block) < 2:
        return set()
    return set(re.findall(r'\["([a-z0-9_]+/[a-z0-9_]+)"\]\s*=', block[1]))


def bridge_served_routes() -> set[str]:
    """Routes HttpBridge answers before the Router ever sees them."""
    source = read(ADDIN / "HttpBridge.cs")
    served = set(re.findall(r'case "([a-z0-9_]+/[a-z0-9_]+|sessions|output_policy)":', source))
    served |= set(re.findall(r'route,\s*"([a-z/_]+)",\s*StringComparison', source))
    return served


def python_called_routes() -> set[str]:
    """Routes the Python client asks for, by name."""
    bridge = read(SERVER / "bridge.py")
    called = set(re.findall(r'self\.call\(\s*["\']([^"\']+)["\']', bridge))
    called |= set(re.findall(r'self\.require\(\s*["\']([^"\']+)["\']', bridge))
    mcp = read(SERVER / "mcp_server.py")
    called |= {f"workflow/{s}" for s in re.findall(r'_workflow\(\s*["\']([^"\']+)', mcp)}
    return called


def mcp_tools() -> dict[str, str]:
    """Registered tool name -> its source, from the AST."""
    source = read(SERVER / "mcp_server.py")
    tree = ast.parse(source)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and any(
            isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool"
            for d in node.decorator_list
        ):
            found[node.name] = ast.get_source_segment(source, node) or ""
    return found


# ------------------------------------------------------------------ tests


class TestRouteWiring:
    def test_every_route_python_calls_is_served(self) -> None:
        """The defect this catches: a tool wired to a route nobody serves.

        It surfaces to the operator as `unknown_route`, which explains
        nothing, and only ever at the moment they needed the feature.
        """
        served = router_routes() | bridge_served_routes()
        orphans = sorted(r for r in python_called_routes() if r not in served)
        assert not orphans, f"Python llama rutas que nadie atiende: {orphans}"

    def test_job_routes_are_real_routes(self) -> None:
        """A route can only run as a job if it exists at all."""
        served = router_routes()
        phantom = sorted(r for r in router_job_routes() if r not in served)
        assert not phantom, f"declaradas como job pero sin handler: {phantom}"

    def test_the_four_workflow_steps_exist_on_both_sides(self) -> None:
        served = router_routes()
        for step in ("audit_models", "configure", "run", "group_levels"):
            assert f"workflow/{step}" in served, step

    def test_save_routes_exist(self) -> None:
        served = router_routes()
        assert "document/save" in served
        assert "document/save_as" in served

    def test_long_operations_can_run_as_jobs(self) -> None:
        """Anything that owns the UI thread for minutes must be submittable."""
        jobs = router_job_routes()
        for route in (
            "workflow/configure", "workflow/run", "workflow/group_levels",
            "document/save", "document/save_as", "clash/run",
        ):
            assert route in jobs, f"{route} debería poder ejecutarse como trabajo"


class TestCapabilityHonesty:
    """capabilities is the negotiation surface; it must not overpromise."""

    def test_declared_operations_have_a_route_behind_them(self) -> None:
        source = read(ADDIN / "Capabilities.cs")
        block = source.split("Operations =", 1)[1].split("};", 1)[0]
        declared = set(re.findall(r'"([a-z0-9_]+)"', block))
        served = router_routes() | bridge_served_routes()

        # Operation name -> the route that must exist for it to be true.
        backed = {
            "document_save": "document/save",
            "document_save_as": "document/save_as",
            "document_close": "document/close",
            "application_exit": "application/exit",
            "workflow_audit_models": "workflow/audit_models",
            "workflow_configure": "workflow/configure",
            "workflow_run": "workflow/run",
            "workflow_group_levels": "workflow/group_levels",
            "clash_export": "clash/export",
            "clash_run": "clash/run",
            "clash_group": "clash/group",
            "clash_status": "clash/status",
            "clash_rules": "clash/apply_rules",
            "sets_explicit": "sets/build",
            "sets_search": "sets/build_search",
            "clash_matrix": "clash/matrix",
            "viewpoints_save": "viewpoints/save",
            "appearance_color": "appearance/color",
            "appearance_reset": "appearance/reset",
            "selection_set": "selection/set",
            "model_schema": "model/schema",
            "clash_image": "clash/image",
            "census": "census",
            "jobs": "job/submit",
            "sessions": "sessions",
        }
        for operation, route in backed.items():
            if operation in declared:
                assert route in served, (
                    f"capabilities declara «{operation}» pero «{route}» no existe"
                )

    def test_capabilities_advertises_the_routes_the_bridge_serves_itself(self) -> None:
        """The defect the first live smoke test found.

        `capabilities.routes` was built from the Router alone, so every route
        HttpBridge answers before the Router sees it — `job/submit` above all
        — was missing from the advertised list. `Bridge.require("job/submit")`
        then refused EVERY asynchronous submission with "update the add-in",
        against an add-in that supported it perfectly.

        Unit tests missed it because they stubbed the capability list and so
        only ever agreed with themselves.
        """
        source = read(ADDIN / "HttpBridge.cs")
        assert "AddBridgeRoutes" in source, (
            "capabilities debe incluir las rutas que HttpBridge atiende por su cuenta"
        )
        # And the merge must cover every route it actually handles.
        block = source.split("var mine = new List<string>(NonDocumentRoutes)", 1)
        assert len(block) == 2, "AddBridgeRoutes debe partir de NonDocumentRoutes"
        assert '"job/submit"' in block[1].split(";", 1)[0], (
            "job/submit debe anunciarse: es la ruta de la que dependen TODOS los trabajos"
        )

    def test_every_route_the_python_client_gates_on_is_advertisable(self) -> None:
        """`require(route)` can only pass for routes capabilities can list."""
        bridge = read(SERVER / "bridge.py")
        gated = set(re.findall(r'self\.require\(\s*["\']([^"\']+)["\']', bridge))
        http = read(ADDIN / "HttpBridge.cs")
        advertisable = router_routes() | bridge_served_routes()
        advertisable |= set(re.findall(r'\{\s*"(job/submit|capabilities)"', http))
        advertisable |= {"job/submit", "capabilities"}
        missing = sorted(r for r in gated if r not in advertisable)
        assert not missing, f"require() bloquea rutas que capabilities nunca anuncia: {missing}"

    def test_contract_versions_agree_across_the_bridge(self) -> None:
        """Python and C# must name the same profile schema, or negotiation lies."""
        cs = read(ADDIN / "ProfileSchema.cs")
        current = re.search(r'Current\s*=\s*"([^"]+)"', cs).group(1)
        py = read(SERVER / "mcp_server.py")
        declared = re.search(r'PROFILE_SCHEMA\s*=\s*"([^"]+)"', py).group(1)
        assert current == declared, f"C#={current} vs Python={declared}"

    def test_session_contract_agrees(self) -> None:
        cs = read(ADDIN / "SessionStore.cs")
        current = re.search(r'ContractVersion\s*=\s*"([^"]+)"', cs).group(1)
        py = read(SERVER / "sessions.py")
        declared = re.search(r'CONTRACT\s*=\s*"([^"]+)"', py).group(1)
        assert current == declared, f"C#={current} vs Python={declared}"


class TestToolWiring:
    """Every advertised capability must reach the bridge or local state."""

    NEW_CAPABILITIES: ClassVar[dict[str, str]] = {
        "navis_audit_models": "workflow/audit_models",
        "navis_configure": "workflow/configure",
        "navis_run": "workflow/run",
        "navis_group_levels": "workflow/group_levels",
    }

    def test_workflow_tools_name_their_route(self) -> None:
        tools = mcp_tools()
        for tool, route in self.NEW_CAPABILITIES.items():
            assert tool in tools, f"{tool} no está registrada"
            step = route.split("/", 1)[1]
            # The call is formatted across lines, so match the argument rather
            # than an exact adjacency.
            assert re.search(rf'_workflow\(\s*"{step}"', tools[tool]), (
                f"{tool} no llama {route}"
            )

    def test_save_tools_reach_the_bridge(self) -> None:
        tools = mcp_tools()
        assert "STATE.bridge.save(" in tools["navis_save"]
        assert "STATE.bridge.save_as(" in tools["navis_save_as"]

    def test_close_tools_reach_the_bridge(self) -> None:
        tools = mcp_tools()
        assert "STATE.bridge.close_document(" in tools["navis_close_document"]
        assert "STATE.bridge.exit_application(" in tools["navis_exit"]
        assert "STATE.require_mutable(" in tools["navis_close_document"]
        assert "resolve(for_mutation=True)" in tools["navis_exit"]

    def test_targeting_tools_reach_the_session_layer(self) -> None:
        tools = mcp_tools()
        assert "session_summary()" in tools["navis_sessions"]
        assert "STATE.bridge.pin(" in tools["navis_target"]
        assert "STATE.bridge.resolve(" in tools["navis_current_target"]

    def test_capabilities_tool_asks_the_addin(self) -> None:
        assert "STATE.bridge.capabilities(" in mcp_tools()["navis_capabilities"]

    def test_every_mutating_tool_guards_the_document(self) -> None:
        """A mutation that skips require_mutable can land on another model."""
        tools = mcp_tools()
        mutating = [
            "navis_apply_groups", "navis_set_status", "navis_save_viewpoints",
            "navis_color_by_priority", "navis_configure", "navis_run",
            "navis_group_levels", "navis_save", "navis_save_as",
        ]
        for name in mutating:
            assert "STATE.require_mutable(" in tools[name], (
                f"{name} muta sin comprobar la huella del documento"
            )

    def test_read_tools_use_the_freshness_guard(self) -> None:
        """Answering from an analysis of a different document is worse than
        answering nothing."""
        tools = mcp_tools()
        for name in ("navis_root_causes", "navis_decisions", "navis_work_plan",
                     "navis_coordination_matrix", "navis_pdf_report", "navis_handoff"):
            assert "require_fresh_result()" in tools[name], name


class TestCodexManifest:
    """The plugin root marker, settled against a real Codex installation.

    An earlier pass reasoned from the NAME — "CLAUDE_PLUGIN_ROOT must be
    Claude-only" — and replaced it with a relative path. The local evidence
    says the opposite, and a relative path is strictly worse: it resolves
    against the spawned process's working directory, which no host promises.

    Evidence, from this machine:

    * Codex installs plugins to
      ``~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/``.
    * The one cached Codex plugin that declares an MCP server, staged by
      Codex itself, writes ``${CLAUDE_PLUGIN_ROOT}/scripts/launch.cmd`` in
      ``.codex-plugin/plugin.json``, and that script exists at the cached
      root.
    * No cached Codex plugin uses a relative path.
    * ``~/.codex/config.toml`` uses absolute paths, never a relative one.
    """

    def test_codex_manifest_anchors_the_launcher_to_the_plugin_root(self) -> None:
        servers = read_json_file(ROOT / ".codex-plugin" / "plugin.json")["mcpServers"]
        args = servers["horizun-navis-mcp"]["args"]
        script = [a for a in args if a.endswith(".py")]
        assert script, "el manifiesto de Codex debe nombrar el launcher"
        assert script[0].startswith("${CLAUDE_PLUGIN_ROOT}/"), (
            "sin el marcador de raíz la ruta se resuelve contra el cwd del proceso, "
            "que el host no garantiza"
        )

    def test_both_hosts_point_at_the_same_launcher(self) -> None:
        claude = read_json_file(ROOT / ".claude-plugin" / "plugin.json")["mcpServers"]
        codex = read_json_file(ROOT / ".codex-plugin" / "plugin.json")["mcpServers"]
        assert claude["horizun-navis-mcp"]["args"] == codex["horizun-navis-mcp"]["args"]

    def test_the_launcher_exists_at_that_relative_position(self) -> None:
        for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            servers = read_json_file(ROOT / manifest)["mcpServers"]
            for spec in servers.values():
                for arg in spec["args"]:
                    if arg.endswith(".py"):
                        relative = arg.replace("${CLAUDE_PLUGIN_ROOT}/", "")
                        assert (ROOT / relative).is_file(), f"{manifest}: falta {relative}"


class TestParity:
    """Every coordination step must reach the service, not a copy of it.

    This used to assert the same thing about the ribbon buttons AND the
    routes. The buttons are gone: six of them stood in a row on the Tool
    add-ins tab doing exactly what six MCP tools already did, so the ribbon
    now carries one button — the bridge toggle — and the workflow is reached
    the one way that has tests behind it.
    """

    def test_the_ribbon_offers_only_the_bridge(self) -> None:
        """One button, and it says what it does.

        A second `AddInPlugin` in this add-in is a duplicate of a tool by
        construction: everything else it could offer is already a route.
        """
        # Only classes deriving from `AddInPlugin` become buttons. The bridge's
        # autostart is an `EventWatcherPlugin` and shows nothing, so matching
        # on "a file that mentions AddInPlugin" counted it and made this fail
        # for a button that does not exist.
        buttons = []
        for path in ADDIN.glob("*.cs"):
            for attribute, base in re.findall(
                r"\[Plugin\((.*?)\)\]\s*public sealed class \w+\s*:\s*(\w+)",
                read(path),
                re.DOTALL,
            ):
                if base != "AddInPlugin":
                    continue
                shown = re.search(r'DisplayName\s*=\s*"([^"]+)"', attribute)
                buttons.append(shown.group(1) if shown else "(sin nombre)")

        assert buttons == ["Puente - NavisCoord"], (
            f"la cinta ofrece {buttons}; debe ofrecer solo el puente"
        )

    def test_routes_delegate_to_the_same_service(self) -> None:
        handlers = read(ADDIN / "WorkflowHandlers.cs")
        for step in ("AuditModels", "Configure", "RunAll", "GroupByLevel", "ApplyRules"):
            assert f"CoordinationWorkflow.{step}" in handlers, step

    @staticmethod
    def routes_served() -> set[str]:
        return set(re.findall(r'\["([a-z_]+/[a-z_]+)"\]\s*=', read(ADDIN / "Router.cs")))

    @staticmethod
    def routes_reached() -> set[str]:
        python = read(SERVER / "mcp_server.py") + read(SERVER / "bridge.py")
        reached = set(re.findall(r'"([a-z_]+/[a-z_]+)"', python))
        # Workflow steps are addressed by name and assembled at call time.
        reached |= {f"workflow/{step}" for step in re.findall(r'_workflow\(\s*"([a-z_]+)"', python)}
        return reached

    def test_every_route_the_addin_serves_is_reachable_from_mcp(self) -> None:
        """Parity, stated as the invariant instead of as an inventory.

        The predecessor of this test listed the four routes that had no tool
        and asserted the list had not changed, which quietly turned a defect
        into the expected result: the addin served `clash/apply_rules`,
        advertised `clash_rules` among its capabilities, and no caller driving
        the server could run it. A test that pins a gap keeps it.

        The chain this defends is: capability advertised → route served →
        bridge method → MCP tool → contract test. Adding a route without a
        tool now fails here, which is the only moment anyone will notice.
        """
        orphans = self.routes_served() - self.routes_reached()
        assert self.routes_served(), "no se pudo leer ninguna ruta del Router"
        assert not orphans, (
            f"el complemento sirve rutas que ninguna tool MCP alcanza: {sorted(orphans)}. "
            "Una capacidad anunciada sin tool es una promesa que el servidor no puede cumplir."
        )

    def test_advertised_operations_map_to_served_routes(self) -> None:
        """The capability names are a promise; the Router is what keeps it."""
        operations = set(
            re.findall(r'"([a-z_]+)"', read(ADDIN / "Capabilities.cs").split("Operations")[1].split("};")[0])
        )
        served = {route.replace("/", "_") for route in self.routes_served()}
        # `clash_rules` is the advertised name of `clash/apply_rules`; the
        # rest of the operation names are the route with the slash swapped.
        aliases = {"clash_rules": "clash_apply_rules", "sets_search": "sets_build_search",
                   "sets_explicit": "sets_build", "selection_set": "selection_set",
                   "appearance_color": "appearance_color", "appearance_reset": "appearance_reset",
                   "clash_export": "clash_export", "clash_image": "clash_image"}
        infra = {"jobs", "sessions", "targeting", "idempotency", "mutation_envelope",
                 "census", "model_schema"}
        unmatched = {
            op for op in operations - infra
            if aliases.get(op, op) not in served
        }
        assert not unmatched, f"capacidades anunciadas sin ruta que las sirva: {sorted(unmatched)}"

    def test_no_business_logic_left_in_the_ribbon_file(self) -> None:
        """The ribbon should start the bridge and delegate, nothing else.

        The old button files computed AND formatted, which is why the MCP
        server could not reuse a single step. They are gone; this keeps the
        rule pointed at whatever is left on the ribbon.
        """
        source = read(ADDIN / "BridgePlugin.cs")
        for banned in ("TestsRunAllTests", "TestsAddCopy", "TestsMove",
                       "TestsEditDisplayName", "new ClashTest", "SelectionSets.AddCopy"):
            assert banned not in source, (
                f"BridgePlugin.cs manipula el documento directamente: «{banned}»"
            )


class TestSourceHygiene:
    """The defect: a raw 0x1F separator pasted into a C# source file.

    It made git classify the file as binary, which silently exempted it from
    the LF normalisation in .gitattributes and from every textual diff — and
    it was invisible in every editor.
    """

    @staticmethod
    def sources() -> list[Path]:
        paths: list[Path] = []
        for pattern in ("addin/**/*.cs", "server/**/*.py", "scripts/*.py"):
            paths.extend(
                p for p in ROOT.glob(pattern)
                if "obj" not in p.parts and "bin" not in p.parts
                and "__pycache__" not in p.parts
            )
        return paths

    def test_no_control_bytes_in_any_source(self) -> None:
        offenders: list[str] = []
        for path in self.sources():
            data = path.read_bytes()
            for index, byte in enumerate(data):
                if byte < 9 or 13 < byte < 32 or byte == 127:
                    line = data[:index].count(b"\n") + 1
                    offenders.append(f"{path.relative_to(ROOT)}:{line} 0x{byte:02X}")
                    break
        assert not offenders, f"bytes de control en fuentes: {offenders}"

    def test_every_source_decodes_as_utf8(self) -> None:
        broken = []
        for path in self.sources():
            try:
                path.read_bytes().decode("utf-8")
            except UnicodeDecodeError as exc:
                broken.append(f"{path.relative_to(ROOT)}: {exc}")
        assert not broken, broken


class TestNewFilesAreBuilt:
    """A new .cs that no project compiles is documentation, not code."""

    def test_addin_compiles_every_source_in_its_folder(self) -> None:
        """The add-in csproj uses default globbing, so this is about strays."""
        csproj = read(ADDIN / "NavisCoord.Addin.csproj")
        # EnableDefaultCompileItems is not disabled for the add-in, so every
        # .cs in the folder is compiled. Assert that assumption holds.
        assert "<EnableDefaultCompileItems>false" not in csproj, (
            "si se desactiva el globbing, cada archivo nuevo debe listarse a mano"
        )

    def test_test_project_links_the_production_sources(self) -> None:
        """The C# tests must exercise production files, not copies of them."""
        csproj = read(ROOT / "addin" / "NavisCoord.Tests" / "NavisCoord.Tests.csproj")
        linked = set(re.findall(r'Include="\.\.\\NavisCoord\.Addin\\([A-Za-z]+)\.cs"', csproj))
        for required in (
            "Json", "RulePrecedence", "CoordinationLogic", "MutationContract",
            "JobManager", "PathPolicy", "ProfileSchema", "SessionStore",
            "SaveFormats", "Capabilities", "UiDispatcher",
        ):
            assert required in linked, f"{required}.cs no se enlaza en las pruebas"

    def test_only_bridgehost_is_stubbed(self) -> None:
        """Stubs.cs must not grow into a parallel implementation.

        A stub that reimplements production logic lets the suite pass while
        the real integration is broken — the exact failure this project has
        been trying to design out.
        """
        stubs = read(ROOT / "addin" / "NavisCoord.Tests" / "Stubs.cs")
        classes = set(re.findall(r"(?:static |sealed )?class (\w+)", stubs))
        assert classes == {"BridgeHost"}, (
            f"Stubs.cs define más que BridgeHost: {sorted(classes)}"
        )
        assert len(stubs.splitlines()) < 60, "Stubs.cs creció más allá de un stub"


class TestUnauthenticatedHealthSurface:
    """The one route answered without a token, pinned field by field.

    Anything added here later is a disclosure decision, and it should have to
    be made deliberately by editing this list rather than by adding a
    convenient key to a dictionary.
    """

    ALLOWED: ClassVar[set[str]] = {"ok", "version", "pid", "busy"}

    FORBIDDEN: ClassVar[set[str]] = {
        "session_id",      # identifies a handshake whose secrecy IS the model
        "port",            # same
        "token",           # never, anywhere
        "document",        # the model
        "document_open",   # presence information about a person
        "document_path",
        "path", "title", "models", "profile",
        "api_version",     # precise build fingerprint
        "session_contract",
        "capabilities", "routes", "save", "note", "running",
    }

    @staticmethod
    def payload_keys() -> set[str]:
        source = read(ADDIN / "HttpBridge.cs")
        block = source.split("private Dictionary<string, object> LivenessPayload()", 1)[1]
        block = block.split("};", 1)[0]
        return set(re.findall(r'\["([a-z_]+)"\]\s*=', block))

    def test_exactly_the_allowed_fields(self) -> None:
        assert self.payload_keys() == self.ALLOWED

    def test_no_forbidden_field_is_present(self) -> None:
        leaked = self.payload_keys() & self.FORBIDDEN
        assert not leaked, f"GET /health sin token expone: {sorted(leaked)}"

    def test_only_get_health_is_anonymous(self) -> None:
        """Every other route, and POST /health, must require the token."""
        source = read(ADDIN / "HttpBridge.cs")
        guard = source.split("private static bool IsAnonymousLiveness", 1)[1].split(";", 1)[0]
        assert '"GET"' in guard, "la excepción anónima debe limitarse al verbo GET"
        assert '"health"' in guard, "…y a la ruta health"
        # The token check must come after that gate and before any dispatch.
        handle = source.split("private void Handle(", 1)[1]
        anon = handle.index("IsAnonymousLiveness")
        token = handle.index("FixedTimeEquals")
        dispatch = handle.index("_dispatcher.Invoke")
        assert anon < token < dispatch, (
            "el token debe comprobarse tras la excepción anónima y antes de despachar"
        )
