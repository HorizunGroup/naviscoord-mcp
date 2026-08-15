"""One profile, on both sides of the bridge.

Until 0.2.0 there were two. `navis_load_profile` installed a profile in this
process; every step that runs inside Navisworks — `workflow/rules`,
Configurar, the search sets, the clash matrix — went and read
`naviscoord-profile.json` off the add-in's disk. Both were called "the
profile", the response carried a `profile_path` field, and a docstring asked
the reader to notice. Loading a profile and then running the rules applied
criteria the caller had just replaced, silently, and the result recorded
nothing about which document had actually been used.

The tests here are about the contract that replaced it: the server sends the
validated content and its checksum over the authenticated port, the add-in
installs it for that session, and both ends can be asked what is in force.

The canonical-form tests matter more than they look. The two checksum
functions each claimed in a comment to mirror the other and never produced
the same value for anything — Python hashed `json.dumps` output, C# hashed a
bespoke `{k:v;}` walk. Nothing caught it because each side only ever compared
its own output with its own output. The C# half of this contract is pinned in
`LogicTests.CanonicalFormTests` against literals generated here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from naviscoord import mcp_server
from naviscoord.bridge import BridgeError
from naviscoord.profile import (
    MAX_PROFILE_BYTES,
    Profile,
    canonical_text,
    checksum_of,
)
from naviscoord.state import SessionState, profile_checksum


class RecordingBridge:
    """A bridge that records what the server pushed, and answers as the add-in.

    Deliberately not a mock that returns whatever the test wants: it
    recomputes the checksum from the canonical text it received, exactly as
    `ProfileStore.Load` does, so a test asserting "both ends agree" is
    checking the agreement rather than a value the stub was handed.
    """

    def __init__(self, *, fail: str = "", checksum_override: str | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.active: dict[str, Any] | None = None
        self.fail = fail
        self.checksum_override = checksum_override

    def profile_load(self, canonical: str, checksum: str) -> dict[str, Any]:
        self.calls.append(("profile/load", {"canonical": canonical, "checksum": checksum}))
        if self.fail == "bridge":
            raise BridgeError("no hay ninguna instancia de Navisworks conectada")
        if self.fail == "invalid":
            return {"ok": False, "error": "profile_invalid", "problems": ["esquema desconocido"]}

        recomputed = self.checksum_override or checksum_of(json.loads(canonical))
        self.active = {
            "ok": True,
            "checksum": recomputed,
            "source": "explicit_mcp",
            "session_id": "sess-1",
            "profile_name": json.loads(canonical).get("name", ""),
        }
        return dict(self.active)

    def profile_reset(self) -> dict[str, Any]:
        self.calls.append(("profile/reset", {}))
        self.active = None
        return {"ok": True, "had_explicit_profile": True, "active": {"source": "plugin_default"}}

    def profile_info(self) -> dict[str, Any]:
        self.calls.append(("profile/info", {}))
        if self.active is None:
            return {"ok": True, "source": "plugin_default", "checksum": "deadbeefdeadbeef"}
        return dict(self.active)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """A fresh server state with a recording bridge in place of Navisworks."""
    state = SessionState()
    bridge = RecordingBridge()
    state.bridge = bridge  # type: ignore[assignment]
    monkeypatch.setattr(mcp_server, "STATE", state)
    return state, bridge


def write_profile(tmp_path, name: str, **extra: Any) -> str:
    payload: dict[str, Any] = {
        "$schema": "naviscoord.profile/v1",
        "name": name,
        "disciplines": {"ARQ": {"label": "Arquitectura", "categories": ["Walls"]}},
        "clustering": {"eps_m": 1.5},
        "severity": {"weights": {"penetration": 1.0}},
    }
    payload.update(extra)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class TestCanonicalForm:
    """The bytes both ends hash. Changing these breaks the C# contract test."""

    def test_the_pinned_vector_is_stable(self) -> None:
        """The exact fixture asserted in LogicTests.CanonicalFormTests.

        If this changes, the C# literals must be regenerated from here — not
        the other way round, and never adjusted until they match.
        """
        fixture = json.loads(
            """
            {"name": "cross-check", "_comment": "dropped",
             "severity": {"weights": {"b": 0.25, "a": 0.75},
                          "priority_bands": {"critical": 75.0}},
             "zed": [1, 2.5, true, false, null, "tab\\tquote\\""],
             "acento": "tuberia n con e\\u00f1e",
             "tiny": 1e-05, "big": 1e+16, "third": 0.1}
            """
        )
        assert canonical_text(fixture) == (
            '{"acento":"tuberia n con e\\u00f1e","big":1e+16,"name":"cross-check",'
            '"severity":{"priority_bands":{"critical":75},"weights":{"a":0.75,"b":0.25}},'
            '"third":0.1,"tiny":1e-05,"zed":[1,2.5,true,false,null,"tab\\tquote\\""]}'
        )
        assert checksum_of(fixture) == "6f46652e0f82fe68"

    def test_an_integral_float_is_written_as_an_integer(self) -> None:
        """The add-in's parser has only `double` and cannot tell 1 from 1.0.

        Without this, the same profile read on each side hashed differently
        depending on whether a tolerance was written `1` or `1.0`.
        """
        assert canonical_text({"a": 1}) == canonical_text({"a": 1.0})
        assert canonical_text({"a": 75.0}) == '{"a":75}'

    def test_comment_keys_are_dropped_at_every_depth(self) -> None:
        with_notes = {"a": {"_note": "x", "b": 1}, "_top": "y"}
        without = {"a": {"b": 1}}
        assert checksum_of(with_notes) == checksum_of(without)

    def test_key_order_in_the_file_is_not_part_of_the_identity(self) -> None:
        assert checksum_of({"b": 2, "a": 1}) == checksum_of({"a": 1, "b": 2})

    def test_a_real_change_moves_the_checksum(self) -> None:
        assert checksum_of({"tolerance_m": 0.01}) != checksum_of({"tolerance_m": 0.05})

    def test_strings_are_escaped_to_pure_ascii(self) -> None:
        """So the encoding on the wire cannot change the hash."""
        assert canonical_text({"k": "ñ"}).isascii()
        assert canonical_text({"k": "ñ"}) == '{"k":"\\u00f1"}'

    def test_astral_characters_become_surrogate_pairs(self) -> None:
        """C# walks UTF-16 code units; Python must emit what C# would."""
        assert canonical_text({"k": "\U0001F600"}) == '{"k":"\\ud83d\\ude00"}'

    def test_bools_are_not_confused_with_numbers(self) -> None:
        """In Python `True` IS an int, and would otherwise canonicalise as 1."""
        assert canonical_text({"k": True}) == '{"k":true}'
        assert canonical_text({"k": True}) != canonical_text({"k": 1})


class TestLoadPushesToTheAddin:
    def test_loading_a_profile_sends_it_to_the_addin(self, wired, tmp_path) -> None:
        state, bridge = wired
        result = mcp_server.navis_load_profile(write_profile(tmp_path, "obra-a"))

        assert result["ok"] is True
        assert [route for route, _ in bridge.calls] == ["profile/load"]
        _, payload = bridge.calls[0]
        assert json.loads(payload["canonical"])["name"] == "obra-a", (
            "el add-in debe recibir el CONTENIDO, no una ruta que abrir"
        )

    def test_both_ends_report_the_same_checksum(self, wired, tmp_path) -> None:
        """The headline invariant of the phase."""
        state, bridge = wired
        result = mcp_server.navis_load_profile(write_profile(tmp_path, "obra-a"))

        assert result["addin"]["synchronised"] is True
        assert result["checksum"] == result["addin"]["checksum"]
        assert result["checksum"] == state.profile_id

    def test_no_path_is_ever_sent_to_the_addin(self, wired, tmp_path) -> None:
        """A path would make profile/load an arbitrary-file-read primitive."""
        state, bridge = wired
        path = write_profile(tmp_path, "obra-a")
        mcp_server.navis_load_profile(path)

        _, payload = bridge.calls[0]
        assert set(payload) == {"canonical", "checksum"}
        assert path not in json.dumps(payload)

    def test_a_disconnected_bridge_is_reported_not_swallowed(self, monkeypatch, tmp_path) -> None:
        """A caller who thinks the add-in took their profile must be told.

        Silently succeeding here recreates the exact confusion this contract
        exists to remove, just with an extra step.
        """
        state = SessionState()
        state.bridge = RecordingBridge(fail="bridge")  # type: ignore[assignment]
        monkeypatch.setattr(mcp_server, "STATE", state)

        result = mcp_server.navis_load_profile(write_profile(tmp_path, "obra-a"))
        assert result["ok"] is True, "el perfil sigue cargado en el servidor"
        assert result["addin"]["synchronised"] is False
        assert result["addin"]["error"] == "bridge_unavailable"
        assert "NO se pudo instalar en el complemento" in result["note"]

    def test_an_addin_refusal_is_reported(self, monkeypatch, tmp_path) -> None:
        state = SessionState()
        state.bridge = RecordingBridge(fail="invalid")  # type: ignore[assignment]
        monkeypatch.setattr(mcp_server, "STATE", state)

        result = mcp_server.navis_load_profile(write_profile(tmp_path, "obra-a"))
        assert result["addin"]["synchronised"] is False
        assert result["addin"]["error"] == "profile_invalid"

    def test_a_checksum_disagreement_is_not_rounded_off(self, monkeypatch, tmp_path) -> None:
        """Two canonicalisers drifting is exactly what this must catch.

        Both ends validated the same bytes and disagreed about their identity.
        That is the failure the old code could not even express.
        """
        state = SessionState()
        state.bridge = RecordingBridge(checksum_override="ffffffffffffffff")  # type: ignore[assignment]
        monkeypatch.setattr(mcp_server, "STATE", state)

        result = mcp_server.navis_load_profile(write_profile(tmp_path, "obra-a"))
        assert result["addin"]["synchronised"] is False
        assert result["addin"]["error"] == "checksum_mismatch"

    def test_an_invalid_profile_is_not_pushed(self, wired, tmp_path) -> None:
        """A profile that does not validate must not reach the add-in at all."""
        state, bridge = wired
        bad = write_profile(tmp_path, "rota", clustering={"eps_m": 0.0})

        result = mcp_server.navis_load_profile(bad)
        assert result["ok"] is False
        assert bridge.calls == [], "no se envía un perfil que no valida"
        assert "sigue vigente el anterior" in result["note"]

    def test_an_unknown_schema_fails(self, wired, tmp_path) -> None:
        state, bridge = wired
        path = tmp_path / "futuro.json"
        path.write_text(
            json.dumps({"$schema": "naviscoord.profile/v9", "clustering": {"eps_m": 1.0}}),
            encoding="utf-8",
        )
        result = mcp_server.navis_load_profile(str(path))
        assert result["ok"] is False
        assert any("v9" in p for p in result["problems"])
        assert bridge.calls == []

    def test_a_profile_without_schema_migrates_with_a_warning(self, wired, tmp_path) -> None:
        """Every profile written before the format was versioned lacks it."""
        state, bridge = wired
        path = tmp_path / "heredado.json"
        path.write_text(
            json.dumps(
                {
                    "name": "heredado",
                    "clustering": {"eps_m": 1.5},
                    "severity": {"weights": {"penetration": 1.0}},
                }
            ),
            encoding="utf-8",
        )
        result = mcp_server.navis_load_profile(str(path))
        assert result["ok"] is True
        assert any("$schema" in w for w in result["warnings"])
        assert bridge.calls, "un perfil migrado sí se instala"

    def test_an_oversized_profile_is_refused_before_the_wire(self, wired, tmp_path) -> None:
        """Measured in bytes, and never sent for the add-in to allocate."""
        state, bridge = wired
        path = tmp_path / "enorme.json"
        path.write_text(
            json.dumps(
                {
                    "$schema": "naviscoord.profile/v1",
                    "name": "enorme",
                    "clustering": {"eps_m": 1.5},
                    "_relleno": "x",
                    "element_nouns": {f"cat{i}": "ñ" * 200 for i in range(4000)},
                }
            ),
            encoding="utf-8",
        )
        result = mcp_server.navis_load_profile(str(path))
        assert result["ok"] is False
        assert any(str(MAX_PROFILE_BYTES) in p for p in result["problems"])
        assert bridge.calls == []


class TestDerivedStateInvalidation:
    def test_changing_the_profile_drops_what_it_invalidated(self, wired, tmp_path) -> None:
        state, bridge = wired
        state.result = object()
        state.overrides = {"a.rvt": "EST"}
        state.group_key = "Nivel"
        state.group_roles = {"g": "r"}

        result = mcp_server.navis_load_profile(
            write_profile(tmp_path, "obra-b", clustering={"eps_m": 9.5})
        )
        assert result["invalidated_analysis"] is True
        assert state.result is None
        assert state.overrides == {}
        assert state.group_key == ""
        assert state.group_roles == {}

    def test_reloading_the_same_criteria_keeps_the_analysis(self, wired, tmp_path) -> None:
        state, bridge = wired
        path = write_profile(tmp_path, "obra-a")
        mcp_server.navis_load_profile(path)
        state.result = object()

        result = mcp_server.navis_load_profile(path)
        assert result["invalidated_analysis"] is False
        assert state.result is not None


class TestProfileInfo:
    def test_it_reports_both_sides_and_whether_they_agree(self, wired, tmp_path) -> None:
        """Two checksums in one answer, instead of two calls and a guess."""
        state, bridge = wired
        mcp_server.navis_load_profile(write_profile(tmp_path, "obra-a"))

        info = mcp_server.navis_profile_info()
        assert info["in_sync"] is True
        assert info["server"]["checksum"] == state.profile_id
        assert info["checksum"] == state.profile_id

    def test_it_notices_when_the_addin_is_on_a_different_profile(self, wired) -> None:
        state, bridge = wired
        info = mcp_server.navis_profile_info()
        assert info["source"] == "plugin_default"
        assert info["in_sync"] is False, (
            "el add-in usando su perfil de disco NO es lo mismo que el del servidor"
        )

    def test_it_takes_no_path_argument(self) -> None:
        """It used to open whatever file the caller named."""
        import inspect

        signature = inspect.signature(mcp_server.navis_profile_info)
        assert "path" not in signature.parameters


class TestReset:
    def test_reset_returns_the_addin_to_its_own_default(self, wired, tmp_path) -> None:
        state, bridge = wired
        mcp_server.navis_load_profile(write_profile(tmp_path, "obra-a"))

        result = mcp_server.navis_reset_profile()
        assert result["ok"] is True
        assert ("profile/reset", {}) in bridge.calls
        assert result["server_checksum"] == state.profile_id, (
            "el reset es del complemento; el servidor conserva su perfil"
        )


class TestStateChecksumUsesTheSharedForm:
    def test_state_and_profile_module_agree(self) -> None:
        loaded = Profile.load()
        assert profile_checksum(loaded) == checksum_of(loaded.raw)
