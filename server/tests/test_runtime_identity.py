"""The runtime a client uses must be the one it asked for, built once.

Three reproductions:

* ``mcp 3.0`` imports perfectly and is a major the server cannot use — the
  requirement says ``<3`` — so ``import mcp`` passed and the first real call
  failed.
* Two interpreters sharing a major/minor resolved to one directory: x86 and
  x64, CPython and another implementation, two installs whose wheels are not
  interchangeable.
* Two clients starting together both saw a missing venv and both ran ``pip
  install`` into it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from runtime_identity import (  # noqa: E402
    MANIFEST_NAME,
    READY_NAME,
    RUNTIME_FORMAT,
    Requirement,
    build_manifest,
    check_versions,
    interpreter_fingerprint,
    lock_digest,
    lock_is_stale,
    manifest_matches,
    parse_version,
    runtime_is_ready,
    runtime_key,
)

REQUIREMENTS = ["mcp>=1.9,<3", "reportlab>=4.0,<6", "pillow>=10.0"]

CONTRACT = [
    Requirement("mcp", "mcp", minimum=(1, 9), below=(3,)),
    Requirement("reportlab", "reportlab", minimum=(4, 0), below=(6,)),
    Requirement("pillow", "PIL", minimum=(10, 0)),
]


# --------------------------------------------------------------- versions


class TestVersionsNotJustImports:
    def test_mcp_three_is_refused_even_though_it_imports(self) -> None:
        """The reproduction. `import mcp` cannot see this."""
        problems = check_versions(CONTRACT, {
            "mcp": "3.0.1", "reportlab": "4.2.0", "pillow": "10.4.0"})
        assert problems, "mcp 3 debe rechazarse"
        assert "mcp 3.0.1" in problems[0]
        assert "<3" in problems[0]

    def test_the_accepted_range_is_accepted(self) -> None:
        assert not check_versions(CONTRACT, {
            "mcp": "1.9.0", "reportlab": "4.0.0", "pillow": "10.0.0"})
        assert not check_versions(CONTRACT, {
            "mcp": "2.14.3", "reportlab": "5.9.9", "pillow": "11.2.0"})

    @pytest.mark.parametrize(
        "installed,expected",
        [
            ({"mcp": "1.8.9", "reportlab": "4.2.0", "pillow": "10.4.0"}, "mcp"),
            ({"mcp": "2.0.0", "reportlab": "3.9.0", "pillow": "10.4.0"}, "reportlab"),
            ({"mcp": "2.0.0", "reportlab": "6.0.0", "pillow": "10.4.0"}, "reportlab"),
            ({"mcp": "2.0.0", "reportlab": "4.2.0", "pillow": "9.5.0"}, "pillow"),
        ],
    )
    def test_each_bound_is_enforced(self, installed, expected) -> None:
        problems = check_versions(CONTRACT, installed)
        assert problems and problems[0].startswith(expected)

    def test_a_missing_distribution_is_named(self) -> None:
        problems = check_versions(CONTRACT, {"mcp": "2.0.0", "reportlab": None, "pillow": "10.4.0"})
        assert any("reportlab" in p and "no está instalado" in p for p in problems)

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1.9.0", (1, 9, 0)),
            ("2.0", (2, 0)),
            ("2.0.0rc1", (2, 0, 0)),
            # `post1` starts with a letter, so the release segment ends there.
            ("4.2.0.post1", (4, 2, 0)),
            ("1.0.0+local", (1, 0, 0)),
            ("", None),
            ("sin-numeros", None),
        ],
    )
    def test_version_parsing(self, text, expected) -> None:
        assert parse_version(text) == expected

    def test_an_unparseable_version_is_refused_not_assumed_good(self) -> None:
        problems = check_versions(CONTRACT, {
            "mcp": "desconocida", "reportlab": "4.2.0", "pillow": "10.4.0"})
        assert problems, "una versión ilegible no puede pasar por buena"


# --------------------------------------------------------------- identity


class TestRuntimeKey:
    BASE = dict(plugin_version="0.4.0", requirements=REQUIREMENTS)

    def _interp(self, **overrides) -> dict[str, str]:
        info = interpreter_fingerprint()
        info.update({k: str(v) for k, v in overrides.items()})
        return info

    def test_the_key_is_stable(self) -> None:
        interp = self._interp()
        assert runtime_key(interpreter=interp, **self.BASE) == runtime_key(
            interpreter=interp, **self.BASE)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("executable", r"C:\Otro\Python\python.exe"),
            ("version", "3.13.1"),
            ("implementation", "PyPy"),
            ("bits", "32"),
            ("machine", "ARM64"),
        ],
    )
    def test_every_incompatibility_changes_the_key(self, field, value) -> None:
        """Two interpreters sharing a major/minor must not share a runtime."""
        a = runtime_key(interpreter=self._interp(), **self.BASE)
        b = runtime_key(interpreter=self._interp(**{field: value}), **self.BASE)
        assert a != b, f"{field} debe formar parte de la identidad del runtime"

    def test_x86_and_x64_never_collide(self) -> None:
        thirty_two = runtime_key(interpreter=self._interp(bits="32"), **self.BASE)
        sixty_four = runtime_key(interpreter=self._interp(bits="64"), **self.BASE)
        assert thirty_two != sixty_four

    def test_the_plugin_version_is_part_of_the_key(self) -> None:
        interp = self._interp()
        old = runtime_key(plugin_version="0.2.0", requirements=REQUIREMENTS, interpreter=interp)
        new = runtime_key(plugin_version="0.4.0", requirements=REQUIREMENTS, interpreter=interp)
        assert old != new, "actualizar el plugin no puede reutilizar el venv anterior"

    def test_the_dependency_lock_is_part_of_the_key(self) -> None:
        interp = self._interp()
        a = runtime_key(plugin_version="0.4.0", requirements=REQUIREMENTS, interpreter=interp)
        b = runtime_key(
            plugin_version="0.4.0",
            requirements=["mcp>=1.9,<3", "reportlab>=4.0,<6", "pillow>=11.0"],
            interpreter=interp,
        )
        assert a != b

    def test_requirement_order_does_not_change_the_lock(self) -> None:
        assert lock_digest(REQUIREMENTS) == lock_digest(list(reversed(REQUIREMENTS)))

    def test_the_key_carries_no_personal_path(self) -> None:
        """The executable is part of the identity and must not be readable."""
        key = runtime_key(
            interpreter=self._interp(executable=r"C:\Users\pablo\Python\python.exe"),
            **self.BASE,
        )
        assert "pablo" not in key
        assert "Users" not in key
        assert "\\" not in key and "/" not in key


# ---------------------------------------------------------------- manifest


class TestManifest:
    def _manifest(self, **overrides):
        manifest = build_manifest(
            key="py312-64-abc",
            plugin_version="0.4.0",
            requirements=REQUIREMENTS,
            installed={"mcp": "2.0.0", "reportlab": "4.2.0", "pillow": "10.4.0"},
        )
        manifest.update(overrides)
        return manifest

    def test_a_matching_manifest_is_reused(self) -> None:
        ok, why = manifest_matches(
            self._manifest(), key="py312-64-abc",
            plugin_version="0.4.0", requirements=REQUIREMENTS)
        assert ok, why

    @pytest.mark.parametrize(
        "overrides,fragment",
        [
            ({"format": RUNTIME_FORMAT - 1}, "formato"),
            ({"runtime_key": "otro"}, "otro runtime"),
            ({"plugin_version": "0.2.0"}, "plugin"),
            ({"lock_digest": "0000000000000000"}, "dependencias"),
        ],
    )
    def test_a_stale_manifest_forces_a_rebuild(self, overrides, fragment) -> None:
        ok, why = manifest_matches(
            self._manifest(**overrides), key="py312-64-abc",
            plugin_version="0.4.0", requirements=REQUIREMENTS)
        assert not ok
        assert fragment in why

    def test_a_corrupt_manifest_is_not_a_match(self) -> None:
        ok, _ = manifest_matches(
            "no soy un objeto", key="k", plugin_version="0.4.0", requirements=REQUIREMENTS)
        assert not ok


class TestReadyMarker:
    def test_a_runtime_without_ready_is_not_usable(self, tmp_path: Path) -> None:
        """A venv directory proves somebody started, not that they finished."""
        root = tmp_path / "runtime"
        root.mkdir()
        (root / MANIFEST_NAME).write_text("{}", encoding="utf-8")

        ok, why = runtime_is_ready(root)
        assert not ok
        assert "READY" in why

    def test_a_runtime_without_a_manifest_is_not_usable(self, tmp_path: Path) -> None:
        root = tmp_path / "runtime"
        root.mkdir()
        (root / READY_NAME).write_text("", encoding="utf-8")
        ok, why = runtime_is_ready(root)
        assert not ok
        assert "manifest" in why

    def test_a_complete_runtime_is_usable(self, tmp_path: Path) -> None:
        root = tmp_path / "runtime"
        root.mkdir()
        (root / MANIFEST_NAME).write_text("{}", encoding="utf-8")
        (root / READY_NAME).write_text("", encoding="utf-8")
        assert runtime_is_ready(root)[0]

    def test_a_missing_runtime_is_not_usable(self, tmp_path: Path) -> None:
        assert not runtime_is_ready(tmp_path / "no-existe")[0]


# -------------------------------------------------------------------- lock


class TestProvisioningLock:
    RECORD = {"pid": 4242, "process_started": "2026-08-19T09:00:00",
              "runtime_key": "py312-64-abc", "acquired_at": 1000.0}

    def test_a_live_owner_keeps_the_lock_however_old(self) -> None:
        """A first provisioning on a slow machine legitimately takes minutes."""
        stale, why = lock_is_stale(
            self.RECORD, now=1000.0 + 3600, timeout=60, liveness="alive")
        assert not stale
        assert "sigue vivo" in why

    def test_an_unqueryable_owner_keeps_the_lock(self) -> None:
        """ACCESS_DENIED means the process exists and we may not ask."""
        stale, why = lock_is_stale(
            self.RECORD, now=1000.0 + 3600, timeout=60, liveness="unknown")
        assert not stale, "romperlo dejaría dos constructores en el mismo directorio"
        assert "no se puede confirmar" in why

    def test_a_dead_owner_releases_the_lock(self) -> None:
        stale, why = lock_is_stale(self.RECORD, now=1001.0, timeout=60, liveness="dead")
        assert stale
        assert "4242" in why

    def test_an_ownerless_lock_expires_on_time(self) -> None:
        fresh, _ = lock_is_stale(
            {"acquired_at": 1000.0}, now=1030.0, timeout=60, liveness="")
        assert not fresh, "dentro del plazo se espera"

        expired, why = lock_is_stale(
            {"acquired_at": 1000.0}, now=1100.0, timeout=60, liveness="")
        assert expired
        assert "sin dueño" in why
