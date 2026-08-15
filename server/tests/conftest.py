"""Make the package and the fixture helpers importable without installing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SERVER_DIR = TESTS_DIR.parent

for path in (SERVER_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from naviscoord.paths import ROOTS_ENV, reset_policy
from naviscoord.profile import Profile
from naviscoord.sessions import SESSION_ENV


@pytest.fixture
def profile() -> Profile:
    return Profile.load()


@pytest.fixture(autouse=True)
def sandboxed_outputs(tmp_path, monkeypatch):
    """Point the output policy at the test's own tmp_path.

    Every test that writes a report or a handoff has to authorise a
    destination, exactly as a deployment does — the policy is not relaxed for
    tests, it is configured. Tests that assert a REFUSAL build their own
    policy with their own roots, so this fixture cannot mask one.
    """
    monkeypatch.setenv(ROOTS_ENV, str(tmp_path))
    reset_policy()
    yield
    reset_policy()


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    """Keep the suite away from a real Navisworks running on this machine.

    Without it, a developer with Navisworks open has a live session file on
    disk and the session tests describe their own machine instead of the
    fixture. LOCALAPPDATA is redirected rather than NAVISCOORD_SESSION so the
    registry directory itself is the sandbox.
    """
    home = tmp_path / "localappdata"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("LOCALAPPDATA", str(home))
    monkeypatch.delenv(SESSION_ENV, raising=False)
    yield
