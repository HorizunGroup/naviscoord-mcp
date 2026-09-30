"""Registration must leave exactly one NavisCoord instance per client program.

The same server registered twice (as a plugin and as a direct entry, or under an
older name) makes each session talk to a different instance. The installers in
``scripts/`` remove the duplicate; these tests pin that contract.

The behaviour itself is exercised by ``scripts/Test-RegistrationDedupe.ps1``, in
a temporary home directory, never against this machine's client configuration.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def test_the_resolver_is_shipped_with_the_desktop_package() -> None:
    packaging = (SCRIPTS / "package_desktop.py").read_text(encoding="utf-8")
    assert "scripts/Resolve-DuplicateRegistrations.ps1" in packaging
    assert "Resolve-DuplicateRegistrations.ps1" in (SCRIPTS / "Configure-Clients.ps1").read_text(encoding="utf-8")
    assert "Resolve-DuplicateRegistrations.ps1" in (SCRIPTS / "Install-DesktopPlugin.ps1").read_text(encoding="utf-8")


def test_ci_runs_the_registration_test() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "Test-RegistrationDedupe.ps1" in workflow


def test_the_resolver_never_hand_edits_the_claude_user_configuration() -> None:
    """Claude Code rewrites ~/.claude.json while it runs: only the CLI may change it."""
    source = (SCRIPTS / "Resolve-DuplicateRegistrations.ps1").read_text(encoding="utf-8")
    assert "mcp remove" in source
    for line in source.splitlines():
        if ".claude.json" in line:
            assert "WriteAll" not in line and "Write-TextFileSafely" not in line


@pytest.mark.skipif(sys.platform != "win32", reason="the installers are Windows PowerShell scripts")
def test_registration_leaves_one_instance_per_client() -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is not available")
    result = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPTS / "Test-RegistrationDedupe.ps1")],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
