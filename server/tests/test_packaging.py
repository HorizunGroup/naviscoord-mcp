"""Launcher, manifests and version coherence.

None of this is exercised by running the server: the launcher only matters
when the runtime is broken, and a manifest only matters at install time. Both
are therefore places where a defect ships and nobody notices until a user
cannot start the tool at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import marketplace_pin as pin
import plugin_launcher as launcher


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------- versioning


class TestVersionCoherence:
    """One version, declared in six places, all of which must agree."""

    @pytest.fixture
    def declared(self) -> dict[str, str]:
        import re

        pyproject = (ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8")
        init = (ROOT / "server" / "naviscoord" / "__init__.py").read_text(encoding="utf-8")
        declared = {
            "pyproject": re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1),
            "__init__": re.search(r'^__version__ = "([^"]+)"', init, re.MULTILINE).group(1),
            "claude_plugin": read_json(ROOT / ".claude-plugin" / "plugin.json")["version"],
            "codex_plugin": read_json(ROOT / ".codex-plugin" / "plugin.json")["version"],
        }

        # Discovered, not named: this repository carries a private ribbon
        # project the public one does not, and a test that hardcodes it would
        # fail the moment the engine is promoted.
        for project in sorted((ROOT / "addin").glob("*/*.csproj")):
            found = re.search(r"<Version>([^<]+)</Version>",
                              project.read_text(encoding="utf-8"))
            if found:
                declared[project.stem] = found.group(1)
        return declared

    def test_every_manifest_declares_the_same_version(self, declared: dict[str, str]) -> None:
        distinct = set(declared.values())
        assert len(distinct) == 1, f"versiones divergentes: {declared}"

    def test_the_package_reports_that_version(self, declared: dict[str, str]) -> None:
        from naviscoord import __version__

        assert __version__ == declared["pyproject"]


class TestMarketplacePins:
    """A marketplace entry must not float on a branch.

    `ref: main` means two people installing the same plugin on the same day
    can get different code, and a bad commit is live for everyone until
    somebody notices. A tag is reproducible.
    """

    @pytest.mark.parametrize(
        "manifest",
        [".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"],
    )
    def test_source_is_pinned_to_a_tag(self, manifest: str) -> None:
        data = read_json(ROOT / manifest)
        for plugin in data["plugins"]:
            ref = (plugin.get("source") or {}).get("ref", "")
            assert ref and ref != "main", f"{manifest}: ref suelto «{ref}»"
            assert ref.startswith("v"), f"{manifest}: ref «{ref}» no parece un tag"

    @staticmethod
    def local_tags() -> set[str]:
        import subprocess

        return set(
            subprocess.run(
                ["git", "tag"], cwd=ROOT, capture_output=True, text=True, check=False
            ).stdout.split()
        )

    @staticmethod
    def declared_refs() -> set[str]:
        return {
            plugin["source"]["ref"]
            for manifest in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json")
            for plugin in read_json(ROOT / manifest)["plugins"]
        }

    @staticmethod
    def current_branch() -> str:
        import subprocess

        return subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()

    def test_this_repository_passes_its_own_pin_rule(self) -> None:
        """The real tree, in whatever phase it is actually in.

        This used to `pytest.skip` when no tags were present — which is every
        CI run, because `actions/checkout` fetches none. A guard that skips
        reports success, so the one invariant protecting "the tag publishes
        the ref this commit contains" was never enforced anywhere except a
        developer's machine, and only if they happened to be on the right
        branch. It cannot skip now: a missing prerequisite is a failure with
        the fix in the message.
        """
        result = pin.check(ROOT)
        assert result.ok, f"[fase {result.phase}] {result.detail}"

    # The phases, driven directly so every one is exercised on every run —
    # including the ones this repository is not in today. A rule that is only
    # tested in the state the tree happens to occupy is tested once and then
    # never again.

    @pytest.mark.parametrize(
        ("env", "branch", "expected"),
        [
            # The default branch is its own phase. It used to fall through to
            # `dev`, whose rule is "the ref must already be a tag" — which main
            # cannot satisfy between the release merge and the tag push, so the
            # window every release passes through reported a hard failure.
            ({}, "main", pin.MAIN),
            ({"GITHUB_REF_NAME": "main"}, "", pin.MAIN),
            ({"GITHUB_REF": "refs/heads/main", "GITHUB_REF_NAME": "main"}, "", pin.MAIN),
            ({}, "feature/algo", pin.DEV),
            ({}, "release/naviscoord-0.2.0", pin.PRETAG),
            ({"GITHUB_REF": "refs/tags/v0.2.0"}, "", pin.TAG),
            # A pull request: HEAD is detached, so the branch is unreadable and
            # only GITHUB_HEAD_REF knows where the change came from. This is
            # the case the old branch-name guess could not see.
            ({"GITHUB_HEAD_REF": "release/naviscoord-0.2.0"}, "", pin.PRETAG),
            # A PR *targeting* main still reports the SOURCE branch, so a
            # feature PR is dev and not main: GITHUB_HEAD_REF wins over the
            # checked-out branch precisely so this cannot be confused.
            ({"GITHUB_HEAD_REF": "feature/algo"}, "main", pin.DEV),
            # A tag ref beats the branch name that happens to be checked out.
            ({"GITHUB_REF": "refs/tags/v0.2.0", "GITHUB_REF_NAME": "main"}, "main", pin.TAG),
            ({"NAVISCOORD_RELEASE_VALIDATION": "pretag"}, "main", pin.PRETAG),
            ({"NAVISCOORD_RELEASE_VALIDATION": "main"}, "feature/algo", pin.MAIN),
            # The override beats even a tag ref, because a human running a
            # release knows something the environment does not.
            ({"NAVISCOORD_RELEASE_VALIDATION": "dev",
              "GITHUB_REF": "refs/tags/v0.2.0"}, "", pin.DEV),
        ],
    )
    def test_the_phase_is_read_from_explicit_signals(
        self, env: dict[str, str], branch: str, expected: str
    ) -> None:
        assert pin.detect_phase(env, branch=branch) == expected

    def test_an_unknown_override_is_refused_rather_than_ignored(self) -> None:
        with pytest.raises(ValueError, match="no es válido"):
            pin.detect_phase({"NAVISCOORD_RELEASE_VALIDATION": "casi"})

    OLD = {"v0.1.0", "v0.1.1", "v0.1.2"}

    # Named rather than inlined so the coverage assertion below can read the
    # same table the parametrisation runs, instead of introspecting the mark.
    PHASE_CASES = [
            # dev: the ref has to be a tag that exists.
            (pin.DEV, {"v0.1.2"}, OLD, True, True, "ref en el último tag publicado"),
            (pin.DEV, {"v0.2.0"}, OLD, True, False, "ref a un tag inexistente fuera de la candidata"),
            (pin.DEV, {"v9.9.9"}, OLD, True, False, "ref a un tag que no existe"),
            # pretag: naming the unborn tag is exactly right, and only that one.
            (pin.PRETAG, {"v0.2.0"}, OLD, True, True, "candidata nombrando su propio tag"),
            (pin.PRETAG, {"v0.1.2"}, OLD, True, False, "candidata que dejó el ref viejo"),
            (pin.PRETAG, {"v0.3.0"}, OLD, True, False, "candidata nombrando otro tag"),
            # main: the release window is legitimate, and bounded.
            (pin.MAIN, {"v0.2.0"}, OLD, True, True, "ventana: main nombra su versión sin tag todavía"),
            (pin.MAIN, {"v0.2.0"}, OLD | {"v0.2.0"}, True, True, "estado estable: tag publicado y nombrado"),
            (pin.MAIN, {"v0.1.2"}, OLD, True, True, "main con el ref en el último tag publicado"),
            (pin.MAIN, {"v9.9.9"}, OLD, True, False, "main con un ref que ni existe ni es la versión"),
            (pin.MAIN, {"v0.2.0"}, set(), False, False, "sin tags leíbles en main"),
            # tag: the tag must exist AND be the one named.
            (pin.TAG, {"v0.2.0"}, OLD | {"v0.2.0"}, True, True, "tag publicado y nombrado"),
            (pin.TAG, {"v0.1.2"}, OLD | {"v0.2.0"}, True, False, "tag creado pero ref viejo"),
            (pin.TAG, {"v0.2.0"}, OLD, True, False, "fase tag sin que el tag exista"),
            # Manifests disagreeing with each other is never acceptable.
            (pin.PRETAG, {"v0.2.0", "v0.1.2"}, OLD, True, False, "manifiestos discrepantes"),
            (pin.MAIN, {"v0.2.0", "v0.1.2"}, OLD, True, False, "manifiestos discrepantes en main"),
            # No tags fetched: a failure, never a pass and never a skip.
            (pin.DEV, {"v0.1.2"}, set(), False, False, "sin tags leíbles en dev"),
            (pin.TAG, {"v0.2.0"}, set(), False, False, "sin tags leíbles en tag"),
            # …except in the candidate phase, which does not need them.
            (pin.PRETAG, {"v0.2.0"}, set(), False, True, "la candidata no necesita tags"),
    ]

    @pytest.mark.parametrize(
        ("phase", "refs", "tags", "available", "ok", "because"), PHASE_CASES
    )
    def test_the_rule_holds_in_every_phase(
        self, phase: str, refs: set[str], tags: set[str],
        available: bool, ok: bool, because: str,
    ) -> None:
        # The tag phase additionally requires knowing the tag points at the
        # checkout; supplied here so these cases isolate the ref rule, and
        # exercised on its own below.
        result = pin.verdict(phase, "0.2.0", refs, tags, tags_available=available,
                             tag_targets_head=True)
        assert result.ok is ok, f"{because}: {result.detail}"
        assert result.detail, "un veredicto sin explicación no sirve de nada"

    def test_every_phase_is_covered_by_the_table(self) -> None:
        """A phase added without a rule would otherwise be tested by nobody."""
        covered = {case[0] for case in self.PHASE_CASES}
        assert covered == set(pin.PHASES), f"fases sin caso: {set(pin.PHASES) - covered}"

    # ------------------------------------------------- el tag y el checkout

    @pytest.mark.parametrize(
        ("targets_head", "ok", "because"),
        [
            (True, True, "el tag apunta al commit que se está construyendo"),
            (False, False, "el tag existe pero apunta a otro commit"),
            # Nobody looked. In this phase that is a broken check, not a pass:
            # the same reasoning that makes an unfetched tag list a failure.
            (None, False, "no se pudo comprobar a qué commit apunta el tag"),
        ],
    )
    def test_the_tag_phase_requires_the_tag_to_be_the_one_being_built(
        self, targets_head: bool | None, ok: bool, because: str
    ) -> None:
        """Existing and being the commit under test are two different claims.

        A tag build that checks out something else — a re-run pointed at a
        branch, a workflow_dispatch on the wrong ref — satisfied every
        assertion about the tag while proving nothing about what that tag
        publishes.
        """
        result = pin.verdict(pin.TAG, "0.2.0", {"v0.2.0"}, {"v0.2.0"},
                             tags_available=True, tag_targets_head=targets_head)
        assert result.ok is ok, f"{because}: {result.detail}"
        assert result.detail

    def test_the_other_phases_do_not_need_the_tag_to_point_anywhere(self) -> None:
        """Only the tag phase builds a tag; requiring it elsewhere would be noise."""
        for phase, refs, tags in (
            (pin.PRETAG, {"v0.2.0"}, self.OLD),
            (pin.MAIN, {"v0.2.0"}, self.OLD),
            (pin.DEV, {"v0.1.2"}, self.OLD),
        ):
            result = pin.verdict(phase, "0.2.0", refs, tags,
                                 tags_available=True, tag_targets_head=None)
            assert result.ok, f"{phase}: {result.detail}"

    def test_the_release_window_says_the_tag_is_still_pending(self) -> None:
        """The window has to be legible, not merely tolerated.

        `ok` alone would let a future edit turn the window into a silent pass
        for any missing tag; the message is what tells a human reading the log
        that the tag is the next step rather than a defect.
        """
        result = pin.verdict(pin.MAIN, "0.2.0", {"v0.2.0"}, self.OLD, tags_available=True)
        assert result.ok
        assert "todavía no se ha empujado" in result.detail

    def test_a_release_commit_would_carry_the_new_ref(self) -> None:
        """Guards the ordering itself, so it cannot regress into prose only.

        Simulates the release commit: with the tag present, the refs have to
        equal the declared version. Running it against the CURRENT tree would
        only re-test the development branch above, so what is checked here is
        that the rule is expressible and that the guide states the order.
        """
        version = read_json(ROOT / ".claude-plugin" / "plugin.json")["version"]
        guide = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")

        tag_line = guide.find("git tag -a")
        ref_line = guide.lower().find("refs de marketplace")
        assert tag_line > 0 and ref_line > 0, "la guía debe cubrir ambos pasos"
        assert ref_line < tag_line, (
            "la guía todavía coloca el cambio de refs DESPUÉS del tag: "
            f"el tag v{version} nacería apuntando al ref anterior"
        )

    def test_releasing_documents_the_pending_marketplace_bump(self) -> None:
        """If the refs lag the dev version, the release guide must say so."""
        version = read_json(ROOT / ".claude-plugin" / "plugin.json")["version"]
        refs = {
            plugin["source"]["ref"]
            for manifest in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json")
            for plugin in read_json(ROOT / manifest)["plugins"]
        }
        if refs == {f"v{version}"}:
            return  # already aligned; nothing pending
        guide = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
        assert "marketplace" in guide.lower()
        assert "ref" in guide.lower(), (
            "los marketplaces van por detrás de la versión y la guía de release "
            "debe describir el cambio pendiente"
        )

    def test_marketplaces_name_an_owner(self) -> None:
        """Required by the marketplace schema; its absence fails validation."""
        for manifest in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"):
            data = read_json(ROOT / manifest)
            assert isinstance(data.get("owner"), dict), manifest
            assert data["owner"].get("name"), manifest


class TestHostSpecificPlaceholders:
    def test_codex_manifest_anchors_to_the_plugin_root(self) -> None:
        """Settled against the local Codex install, not against the name.

        Codex substitutes ${CLAUDE_PLUGIN_ROOT} too — the marker is the
        plugin-root convention, not a Claude-only variable. The detailed
        evidence is in test_integration_wiring.TestCodexManifest.
        """
        servers = read_json(ROOT / ".codex-plugin" / "plugin.json")["mcpServers"]
        for name, spec in servers.items():
            script = [a for a in spec.get("args", []) if a.endswith(".py")]
            assert script, name
            assert script[0].startswith("${CLAUDE_PLUGIN_ROOT}/"), name

    def test_claude_manifest_uses_the_variable_it_owns(self) -> None:
        servers = read_json(ROOT / ".claude-plugin" / "plugin.json")["mcpServers"]
        args = servers["horizun-navis-mcp"]["args"]
        assert any("${CLAUDE_PLUGIN_ROOT}" in a for a in args)

    def test_both_manifests_point_at_a_launcher_that_exists(self) -> None:
        for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            servers = read_json(ROOT / manifest)["mcpServers"]
            for spec in servers.values():
                script = [a for a in spec["args"] if a.endswith(".py")]
                assert script, manifest
                relative = script[0].replace("${CLAUDE_PLUGIN_ROOT}/", "").lstrip("./")
                assert (ROOT / relative).is_file(), f"{manifest}: falta {relative}"


# --------------------------------------------------------------- launcher


class TestDegradedLauncher:
    """The fallback exists precisely for when everything else failed.

    It shipped calling a `lib_dir()` that had been deleted, so the one code
    path whose entire job is to explain a failure raised NameError instead —
    and the client showed an absent server with no message at all.
    """

    def test_failure_report_builds_without_exploding(self) -> None:
        report = launcher.failure_report("pip falló")
        assert report["error"] == "runtime_no_disponible"
        assert report["detail"] == "pip falló"

    def test_failure_report_names_the_interpreter_and_the_floor(self) -> None:
        report = launcher.failure_report("x")
        assert report["interprete"] == sys.executable
        assert report["minimo_requerido"] == "3.10"
        assert report["python"].startswith(str(sys.version_info.major))

    def test_failure_report_gives_a_runnable_fix(self) -> None:
        report = launcher.failure_report("x")
        assert "venv" in report["arreglo"]
        assert "pip install" in report["arreglo"]
        for requirement in launcher.REQUIREMENTS:
            assert requirement in report["arreglo"]

    def test_failure_report_does_not_claim_to_ship_a_python(self) -> None:
        """The docs used to promise an included runtime. It creates a venv."""
        note = report_note = launcher.failure_report("x")["nota"]
        assert "no instala un Python propio" in note
        assert "entorno virtual" in report_note

    def test_degraded_server_answers_initialize(self) -> None:
        detail = launcher.failure_report("x")
        response = launcher.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, detail
        )
        assert response["result"]["protocolVersion"] == launcher.PROTOCOL_VERSION
        assert response["result"]["serverInfo"]["name"] == "horizun-navis-mcp"

    def test_degraded_server_reports_the_real_version(self) -> None:
        from naviscoord import __version__

        response = launcher.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, {}
        )
        assert response["result"]["serverInfo"]["version"] == __version__

    def test_degraded_server_lists_exactly_one_diagnostic_tool(self) -> None:
        response = launcher.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, {}
        )
        tools = response["result"]["tools"]
        assert [t["name"] for t in tools] == ["navis_install_status"]

    def test_calling_the_diagnostic_tool_returns_the_reason_as_an_error(self) -> None:
        detail = launcher.failure_report("no hay pip")
        response = launcher.handle_message(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call"}, detail
        )
        assert response["result"]["isError"] is True
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["detail"] == "no hay pip"

    def test_notifications_get_no_response(self) -> None:
        assert launcher.handle_message({"jsonrpc": "2.0", "method": "initialized"}, {}) is None

    def test_unknown_method_is_answered_rather_than_ignored(self) -> None:
        response = launcher.handle_message({"jsonrpc": "2.0", "id": 9, "method": "raro"}, {})
        assert response["id"] == 9


class TestRuntimeDetection:
    """`import mcp` succeeding is not the same as the runtime being viable."""

    def test_every_runtime_dependency_is_checked(self) -> None:
        assert set(launcher.RUNTIME_MODULES) == {"mcp", "reportlab", "PIL"}

    def test_declared_requirements_cover_every_checked_module(self) -> None:
        distributions = set(launcher.RUNTIME_MODULES.values())
        named = {r.split(">")[0].split("<")[0].split("=")[0].strip() for r in launcher.REQUIREMENTS}
        assert distributions == named

    def test_requirements_match_pyproject(self) -> None:
        """The launcher installs what the package declares — not a stale copy."""
        pyproject = (ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8")
        for requirement in launcher.REQUIREMENTS:
            assert f'"{requirement}"' in pyproject, requirement

    def test_old_python_is_refused_with_an_explanation(self) -> None:
        ok, why = launcher.python_is_supported((3, 9))
        assert not ok
        assert "3.10" in why
        assert "no puedo instalar un python" in why.lower()

    def test_supported_python_passes(self) -> None:
        ok, why = launcher.python_is_supported((3, 12))
        assert ok and why == ""

    def test_minimum_matches_pyproject(self) -> None:
        pyproject = (ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8")
        floor = ".".join(str(p) for p in launcher.MIN_PYTHON)
        assert f'requires-python = ">={floor}"' in pyproject

    def test_missing_modules_reports_absent_distributions(self, monkeypatch) -> None:
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, *args, **kwargs):
            if name == "reportlab":
                raise ImportError("simulado")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)
        missing = launcher.missing_modules()
        # Containment, not equality: this file is run in CI with ONLY pytest
        # installed, on purpose, so a packaging break cannot hide a manifest
        # break. `mcp` and `pillow` are genuinely absent there and reporting
        # them is correct — asserting an exact list made the test pass or
        # fail on what happened to be in the environment rather than on what
        # missing_modules does.
        assert "reportlab" in missing, missing
        # It reports DISTRIBUTION names — what you would pip install — not
        # module names, which is the whole point of the mapping.
        assert set(missing) <= set(launcher.RUNTIME_MODULES.values()), missing

    @pytest.mark.skipif(sys.platform == "win32", reason="modo POSIX")
    def test_world_writable_runtime_is_refused(self, tmp_path: Path) -> None:
        loose = tmp_path / "runtime"
        loose.mkdir()
        loose.chmod(0o777)
        private, why = launcher.runtime_is_private(loose)
        assert not private
        assert "escribible por otros" in why

    @pytest.mark.skipif(sys.platform == "win32", reason="modo POSIX")
    def test_private_runtime_is_accepted(self, tmp_path: Path) -> None:
        tight = tmp_path / "runtime"
        tight.mkdir()
        tight.chmod(0o700)
        assert launcher.runtime_is_private(tight)[0]

    @pytest.mark.skipif(sys.platform != "win32", reason="modo Windows")
    def test_runtime_outside_localappdata_is_refused(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
        (tmp_path / "appdata").mkdir()
        outside = tmp_path / "publico"
        outside.mkdir()
        private, why = launcher.runtime_is_private(outside)
        assert not private
        assert "LOCALAPPDATA" in why

    @pytest.mark.skipif(sys.platform != "win32", reason="modo Windows")
    def test_runtime_inside_localappdata_is_accepted(self, tmp_path: Path, monkeypatch) -> None:
        home = tmp_path / "appdata"
        home.mkdir()
        monkeypatch.setenv("LOCALAPPDATA", str(home))
        inside = home / "NavisCoord" / "runtime"
        inside.mkdir(parents=True)
        assert launcher.runtime_is_private(inside)[0]

    def test_runtime_dir_is_keyed_by_interpreter_version(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        tag = f"py{sys.version_info.major}{sys.version_info.minor}"
        assert launcher.runtime_dir().name == tag

    def test_a_missing_runtime_dir_counts_as_private(self, tmp_path: Path) -> None:
        assert launcher.runtime_is_private(tmp_path / "todavia-no")[0]
