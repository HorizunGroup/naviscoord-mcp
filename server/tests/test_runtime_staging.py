"""A runtime is published whole or not at all.

``ensure_runtime`` used to create the venv directly on the final path. A build
that failed halfway — no network, a wheel that would not install, a full disk —
left a directory that existed, contained a Python, and imported *some* of what
was needed. The next start found it and used it.

So the build happens in a staging directory nobody else can see, ``READY`` is
written after every check, and publication is one rename. These tests drive
``_build_runtime`` with the venv and pip steps replaced, because what is being
asserted is the ORDER and the failure handling, not pip.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import plugin_launcher as launcher  # noqa: E402
from runtime_identity import MANIFEST_NAME, READY_NAME  # noqa: E402
from runtime_lock_spec import Pinned, RuntimeLockFile  # noqa: E402

LOCK = RuntimeLockFile(
    schema="naviscoord.runtime-lock/1",
    generated_utc="2026-08-19T00:00:00Z",
    python_requires=">=3.10",
    packages=(
        Pinned("mcp", "1.28.1"),
        Pinned("pillow", "12.3.0"),
        Pinned("reportlab", "5.0.0"),
    ),
)

HEALTHY = {"mcp": "1.28.1", "reportlab": "5.0.0", "pillow": "12.3.0"}


@pytest.fixture
def stub(monkeypatch):
    """Replaces the two subprocess steps and the two probes.

    Everything else — the ordering, READY, the rename, the cleanup — is the
    real code.
    """
    calls: dict[str, list] = {"run": []}

    def fake_run(argv, timeout=None):
        calls["run"].append(list(argv))
        if "venv" in argv:
            venv = Path(argv[-1])
            (venv / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
            launcher.venv_python(venv).write_text("#!/usr/bin/env python\n", encoding="utf-8")
            return 0, ""
        return 0, ""

    monkeypatch.setattr(launcher, "_run", fake_run)
    monkeypatch.setattr(launcher, "missing_modules", lambda python=None: [])
    monkeypatch.setattr(launcher, "installed_versions", lambda python=None: dict(HEALTHY))
    monkeypatch.setattr(launcher, "_server_version", lambda: "0.4.0")
    return calls


def _build(tmp_path: Path):
    final = tmp_path / "runtime" / "py312-64-abcdef"
    staging = final.parent / (final.name + launcher.STAGING_SUFFIX + "nonce")
    return final, staging


# --------------------------------------------------------------- happy path


class TestASuccessfulBuild:
    def test_ready_is_written_after_everything_else(self, tmp_path, stub) -> None:
        final, staging = _build(tmp_path)
        python, problem = launcher._build_runtime(staging, final, "0.4.0", LOCK)

        assert not problem, problem
        assert python is not None
        assert (final / READY_NAME).is_file(), "READY vive en el runtime publicado"
        assert (final / MANIFEST_NAME).is_file()
        assert not staging.exists(), "el staging deja de existir tras el rename"

    def test_the_manifest_carries_the_full_identity(self, tmp_path, stub) -> None:
        final, staging = _build(tmp_path)
        launcher._build_runtime(staging, final, "0.4.0", LOCK)

        manifest = json.loads((final / MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest["plugin_version"] == "0.4.0"
        assert manifest["runtime_key"] == final.name
        assert manifest["installed"] == HEALTHY
        for field in ("version", "implementation", "bits", "machine"):
            assert manifest["interpreter"][field], field
        assert manifest["lock_digest"], "el digest del lock identifica las dependencias"

    def test_pip_is_given_the_lock_and_never_a_range(self, tmp_path, stub) -> None:
        final, staging = _build(tmp_path)
        launcher._build_runtime(staging, final, "0.4.0", LOCK)

        pip = [c for c in stub["run"] if "pip" in c]
        assert pip, "pip debe ejecutarse"
        argv = pip[0]
        assert "--no-deps" in argv, "sin --no-deps pip volvería a resolver"
        assert "-r" in argv, "instala desde el lock, no desde argumentos sueltos"
        joined = " ".join(argv)
        assert ">=" not in joined and "<" not in joined, (
            f"no puede quedar un rango flotante: {joined}")

    def test_the_published_runtime_validates(self, tmp_path, stub) -> None:
        final, staging = _build(tmp_path)
        launcher._build_runtime(staging, final, "0.4.0", LOCK)
        usable, why = launcher.runtime_is_usable(final, "0.4.0", LOCK)
        assert usable, why


# ---------------------------------------------------------- failure paths


class TestAFailedBuildPublishesNothing:
    def _assert_nothing_published(self, final: Path) -> None:
        assert not (final / READY_NAME).exists(), "no puede quedar READY"
        assert not final.exists() or not any(final.iterdir()), (
            "no puede quedar un runtime a medias")

    def test_a_venv_that_will_not_create(self, tmp_path, stub, monkeypatch) -> None:
        monkeypatch.setattr(launcher, "_run",
                            lambda argv, timeout=None: (1, "no se pudo crear"))
        final, staging = _build(tmp_path)
        python, problem = launcher._build_runtime(staging, final, "0.4.0", LOCK)

        assert python is None
        assert "entorno virtual" in problem
        self._assert_nothing_published(final)

    def test_a_pip_failure(self, tmp_path, stub, monkeypatch) -> None:
        real = launcher._run

        def failing(argv, timeout=None):
            if "pip" in argv:
                return 1, "ERROR: no matching distribution"
            return real(argv, timeout=timeout)

        monkeypatch.setattr(launcher, "_run", failing)
        final, staging = _build(tmp_path)
        python, problem = launcher._build_runtime(staging, final, "0.4.0", LOCK)

        assert python is None
        assert "pip falló" in problem
        assert "refresh_runtime_lock" in problem, "dice cómo arreglarlo"
        self._assert_nothing_published(final)

    def test_a_module_that_will_not_import(self, tmp_path, stub, monkeypatch) -> None:
        monkeypatch.setattr(launcher, "missing_modules",
                            lambda python=None: ["reportlab"])
        final, staging = _build(tmp_path)
        python, problem = launcher._build_runtime(staging, final, "0.4.0", LOCK)

        assert python is None
        assert "reportlab" in problem
        self._assert_nothing_published(final)

    def test_a_version_outside_the_contract(self, tmp_path, stub, monkeypatch) -> None:
        """mcp 3 imports perfectly. That was the whole point."""
        monkeypatch.setattr(launcher, "installed_versions",
                            lambda python=None: {**HEALTHY, "mcp": "3.0.0"})
        final, staging = _build(tmp_path)
        python, problem = launcher._build_runtime(staging, final, "0.4.0", LOCK)

        assert python is None
        assert "mcp 3.0.0" in problem
        self._assert_nothing_published(final)

    def test_a_staging_that_already_exists_is_refused(self, tmp_path, stub) -> None:
        final, staging = _build(tmp_path)
        staging.mkdir(parents=True)
        (staging / "de-otra-corrida.txt").write_text("x", encoding="utf-8")

        python, problem = launcher._build_runtime(staging, final, "0.4.0", LOCK)
        assert python is None
        assert "ya existía" in problem
        assert (staging / "de-otra-corrida.txt").exists(), "no se reutiliza ni se borra"


class TestValidationOfAnExistingRuntime:
    def _publish(self, tmp_path, stub) -> Path:
        final, staging = _build(tmp_path)
        launcher._build_runtime(staging, final, "0.4.0", LOCK)
        return final

    def test_a_missing_ready_makes_it_unusable(self, tmp_path, stub) -> None:
        final = self._publish(tmp_path, stub)
        (final / READY_NAME).unlink()
        usable, why = launcher.runtime_is_usable(final, "0.4.0", LOCK)
        assert not usable
        assert "READY" in why

    def test_a_truncated_manifest_makes_it_unusable(self, tmp_path, stub) -> None:
        final = self._publish(tmp_path, stub)
        (final / MANIFEST_NAME).write_text('{"format": 3, "runt', encoding="utf-8")
        usable, why = launcher.runtime_is_usable(final, "0.4.0", LOCK)
        assert not usable
        assert "manifest" in why

    def test_a_different_plugin_version_makes_it_unusable(self, tmp_path, stub) -> None:
        final = self._publish(tmp_path, stub)
        usable, why = launcher.runtime_is_usable(final, "0.5.0", LOCK)
        assert not usable
        assert "plugin" in why

    def test_a_different_lock_digest_makes_it_unusable(self, tmp_path, stub) -> None:
        final = self._publish(tmp_path, stub)
        moved = RuntimeLockFile(
            schema=LOCK.schema, generated_utc=LOCK.generated_utc,
            python_requires=LOCK.python_requires,
            packages=(Pinned("mcp", "2.0.0"), Pinned("pillow", "12.3.0"),
                      Pinned("reportlab", "5.0.0")),
        )
        usable, why = launcher.runtime_is_usable(final, "0.4.0", moved)
        assert not usable

    def test_versions_are_rechecked_on_reuse(self, tmp_path, stub, monkeypatch) -> None:
        """A runtime can rot: something else can install into it later."""
        final = self._publish(tmp_path, stub)
        monkeypatch.setattr(launcher, "installed_versions",
                            lambda python=None: {**HEALTHY, "mcp": "3.1.0"})
        usable, why = launcher.runtime_is_usable(final, "0.4.0", LOCK)
        assert not usable
        assert "mcp" in why

    def test_a_missing_interpreter_makes_it_unusable(self, tmp_path, stub) -> None:
        final = self._publish(tmp_path, stub)
        launcher.venv_python(final / "venv").unlink()
        usable, why = launcher.runtime_is_usable(final, "0.4.0", LOCK)
        assert not usable
        assert "intérprete" in why


class TestStagingCleanup:
    def test_only_our_own_staging_is_removed(self, tmp_path) -> None:
        root = tmp_path / "runtime"
        root.mkdir()
        mine = root / ("py312-64-abc" + launcher.STAGING_SUFFIX + "mio")
        mine.mkdir()
        (mine / "x.txt").write_text("x", encoding="utf-8")

        launcher._discard_staging(mine, root)
        assert not mine.exists()

    def test_another_runs_staging_survives(self, tmp_path) -> None:
        """Two provisionings can overlap; each cleans only its own."""
        root = tmp_path / "runtime"
        root.mkdir()
        theirs = root / ("py312-64-abc" + launcher.STAGING_SUFFIX + "suyo")
        theirs.mkdir()
        (theirs / "y.txt").write_text("y", encoding="utf-8")

        mine = root / ("py312-64-abc" + launcher.STAGING_SUFFIX + "mio")
        mine.mkdir()
        launcher._discard_staging(mine, root)

        assert not mine.exists()
        assert theirs.exists(), "el staging de otra ejecución no se toca"

    def test_a_path_outside_the_root_is_refused(self, tmp_path) -> None:
        root = tmp_path / "runtime"
        root.mkdir()
        outside = tmp_path / ("fuera" + launcher.STAGING_SUFFIX + "x")
        outside.mkdir()
        (outside / "importante.txt").write_text("no borrar", encoding="utf-8")

        launcher._discard_staging(outside, root)
        assert outside.exists(), "un borrado recursivo no sale del root"

    def test_a_directory_without_the_staging_suffix_is_refused(self, tmp_path) -> None:
        root = tmp_path / "runtime"
        root.mkdir()
        published = root / "py312-64-abcdef"
        published.mkdir()
        (published / READY_NAME).write_text("", encoding="utf-8")

        launcher._discard_staging(published, root)
        assert published.exists(), "un runtime publicado no es un staging"

    def test_the_root_itself_is_refused(self, tmp_path) -> None:
        root = tmp_path / ("runtime" + launcher.STAGING_SUFFIX + "x")
        root.mkdir()
        launcher._discard_staging(root, root)
        assert root.exists(), "nunca se borra el root de runtimes"

    @pytest.mark.skipif(os.name != "nt", reason="junctions son de Windows")
    def test_a_reparse_point_is_not_followed(self, tmp_path) -> None:
        import subprocess

        root = tmp_path / "runtime"
        root.mkdir()
        real = tmp_path / "datos-reales"
        real.mkdir()
        (real / "importante.txt").write_text("no borrar", encoding="utf-8")

        link = root / ("py312-64-abc" + launcher.STAGING_SUFFIX + "junc")
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(real)],
            capture_output=True, text=True)
        if made.returncode != 0:
            pytest.skip("no se pudo crear la junction: " + made.stderr.strip())

        launcher._discard_staging(link, root)
        assert (real / "importante.txt").exists(), (
            "borrar a través de una junction habría vaciado el destino real")
