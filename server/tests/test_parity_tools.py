"""Contract for the four tools that closed the add-in/MCP parity gap.

These routes exist and work in the add-in, and for a while
no MCP tool could reach them, so the capability list promised something the
server could not deliver. What is tested here is the half that lives in
Python: argument validation, capability negotiation, the exact route each
tool calls, and the transformation of the raw answer into the uniform
envelope. The add-in's own behaviour is covered by the C# suite and by the
in-Navisworks run recorded in docs/TESTING.md.

The bridge is doubled rather than mocked away: `call` records what it was
asked for and replays a captured answer, so a tool that invents a field or
addresses the wrong route fails here instead of on a real federation.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from naviscoord import mcp_server as M
from naviscoord.bridge import CapabilityError
from naviscoord.sessions import SessionInfo, TargetError

ALL_ROUTES = [
    "capabilities", "sets/list", "sets/build_search", "clash/apply_rules",
    "workflow/rules", "job/submit", "job/status",
]


class BridgeDouble:
    """Records every route it is asked for and replays canned answers."""

    def __init__(self, *, routes: list[str] | None = None, answers: dict[str, Any] | None = None):
        self.offered = ALL_ROUTES if routes is None else routes
        self.answers = answers or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.target_id = "sess-1"

    # -- the surface the tools actually use -----------------------------
    def capabilities(self, refresh: bool = False) -> dict[str, Any]:
        return {"routes": list(self.offered)}

    def require(self, route: str, *, since: str = "0.1.2") -> None:
        if route not in self.offered:
            raise CapabilityError(
                f"El complemento de Navisworks instalado no expone «{route}».",
                hint=f"Actualiza el complemento a {since} o posterior.",
                detail={"available": list(self.offered)},
            )

    def call(self, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((route, dict(payload or {})))
        return self.answers.get(route, {})

    def run_route(self, route: str, payload: dict[str, Any], *, run_async: bool) -> dict[str, Any]:
        self.require(route)
        if run_async:
            self.require("job/submit")
            self.calls.append(("job/submit", {"route": route, "payload": payload}))
            return {"job_id": "job-abc", "state": "queued"}
        return self.call(route, payload)

    def list_sets(self) -> dict[str, Any]:
        self.require("sets/list")
        return self.call("sets/list")

    def resolve(self, *, for_mutation: bool = False) -> SessionInfo:
        return SessionInfo(
            session_id="sess-1", port=8781, document_title="federado.nwf",
            document_fingerprint="fp-live", document_open=True,
        )

    @property
    def routes_seen(self) -> list[str]:
        return [route for route, _ in self.calls]


@pytest.fixture
def bridge(monkeypatch) -> BridgeDouble:
    double = BridgeDouble()
    monkeypatch.setattr(M.STATE, "bridge", double)
    # A document already bound, so require_mutable has a live fingerprint.
    return double


# --------------------------------------------------------------- registro


class TestRegistration:
    NEW: ClassVar[dict[str, str]] = {
        "navis_build_search_sets": "sets/build_search",
        "navis_list_search_sets": "sets/list",
        "navis_apply_clash_rules": "clash/apply_rules",
        "navis_run_rules_workflow": "workflow/rules",
    }

    def test_the_four_tools_exist_and_are_callable(self) -> None:
        for name in self.NEW:
            assert callable(getattr(M, name, None)), f"falta la tool {name}"

    def test_mutating_tools_take_the_full_guard_set(self) -> None:
        """Fingerprint, idempotency and async are not optional extras here."""
        import inspect

        for name in ("navis_build_search_sets", "navis_apply_clash_rules",
                     "navis_run_rules_workflow"):
            params = inspect.signature(getattr(M, name)).parameters
            for guard in ("expected_document_fingerprint", "idempotency_key", "run_async"):
                assert guard in params, f"{name} no acepta {guard}"

    def test_the_read_only_tool_takes_no_write_guards(self) -> None:
        """A reader that asks for a fingerprint invites the wrong mental model."""
        import inspect

        params = inspect.signature(M.navis_list_search_sets).parameters
        assert not params, f"navis_list_search_sets debería no tomar argumentos: {list(params)}"


# ----------------------------------------------------------- mapeo de rutas


class TestRouteMapping:
    def test_each_tool_addresses_exactly_its_route(self, bridge: BridgeDouble) -> None:
        M.navis_list_search_sets()
        assert bridge.routes_seen == ["sets/list"]

        bridge.calls.clear()
        M.navis_build_search_sets(folders=[{"folder": "MEP", "sets": [{"name": "Tubo"}]}])
        assert bridge.routes_seen == ["sets/build_search"]

        bridge.calls.clear()
        M.navis_apply_clash_rules(
            pairs=[{"test": "T", "a": "A", "b": "B"}],
            sets_index={"A": {}, "B": {}},
        )
        assert bridge.routes_seen == ["clash/apply_rules"]

    def test_async_goes_through_the_job_system(self, bridge: BridgeDouble) -> None:
        out = M.navis_build_search_sets(
            folders=[{"folder": "MEP", "sets": [{"name": "Tubo"}]}],
            dry_run=False, run_async=True, idempotency_key="k-1",
        )
        assert out["job_id"] == "job-abc"
        assert "job/submit" in bridge.routes_seen
        submitted = dict(bridge.calls)["job/submit"]
        assert submitted["route"] == "sets/build_search"
        assert submitted["payload"]["idempotency_key"] == "k-1"


# --------------------------------------------------------- capacidad ausente


class TestCapabilityNegotiation:
    @pytest.mark.parametrize(
        ("tool", "kwargs", "route"),
        [
            ("navis_list_search_sets", {}, "sets/list"),
            ("navis_build_search_sets",
             {"folders": [{"folder": "MEP", "sets": [{"name": "T"}]}]}, "sets/build_search"),
            ("navis_apply_clash_rules",
             {"pairs": [{"test": "T", "a": "A", "b": "B"}], "sets_index": {"A": {}, "B": {}}},
             "clash/apply_rules"),
        ],
    )
    def test_a_missing_route_refuses_before_calling(
        self, monkeypatch, tool: str, kwargs: dict[str, Any], route: str
    ) -> None:
        """An old addin must be told to update, not fed a different route."""
        double = BridgeDouble(routes=[r for r in ALL_ROUTES if r != route])
        monkeypatch.setattr(M.STATE, "bridge", double)

        out = getattr(M, tool)(**kwargs)

        assert out["error"] == "capability_unavailable"
        assert route in out["detail"]
        assert "Actualiza el complemento" in out.get("hint", "")
        assert route not in double.routes_seen, "no debe llamar la ruta que declaró ausente"
        assert not any(r.startswith(("sets/", "clash/", "workflow/")) for r in double.routes_seen), (
            "no debe caer silenciosamente a otra ruta"
        )


# ------------------------------------------------------ target y fingerprint


class TestGuards:
    def test_an_ambiguous_target_stops_a_write(self, bridge: BridgeDouble) -> None:
        """Two instances open is a question, not a coin toss.

        The refusal comes from `resolve(for_mutation=True)`, which is the only
        place that knows a write is about to happen — a read against the same
        two instances is still allowed to pick the active one.
        """
        def refuse(*, for_mutation: bool = False):
            if not for_mutation:
                return BridgeDouble.resolve(bridge)
            raise TargetError(
                "Hay 2 instancias de Navisworks abiertas; elige una con navis_target.",
                hint="navis_sessions las lista.",
                candidates=[{"target_id": "sess-1"}, {"target_id": "sess-2"}],
            )

        bridge.resolve = refuse  # type: ignore[method-assign]
        out = M.navis_apply_clash_rules(
            pairs=[{"test": "T", "a": "A", "b": "B"}], sets_index={"A": {}, "B": {}},
            dry_run=False,
        )
        assert "navis_target" in str(out.get("error", "")) + str(out.get("detail", ""))
        assert not bridge.routes_seen, "no se tocó el documento con el destino ambiguo"

    def test_a_stale_fingerprint_stops_a_write(self, bridge: BridgeDouble) -> None:
        out = M.navis_build_search_sets(
            folders=[{"folder": "MEP", "sets": [{"name": "T"}]}],
            dry_run=False,
            expected_document_fingerprint="fp-de-otro-documento",
        )
        assert out.get("error"), "una huella ajena debe rechazarse"
        assert not bridge.routes_seen, "no se tocó el documento equivocado"

    def test_arguments_are_validated_before_the_bridge(self, bridge: BridgeDouble) -> None:
        assert M.navis_build_search_sets(folders=[])["error"] == "argumento_invalido"
        assert M.navis_apply_clash_rules(pairs=[], sets_index={})["error"] == "argumento_invalido"
        # A pair naming a set that does not exist would silently match nothing.
        out = M.navis_apply_clash_rules(
            pairs=[{"test": "T", "a": "A", "b": "FANTASMA"}], sets_index={"A": {}},
        )
        assert out["error"] == "argumento_invalido"
        assert "FANTASMA" in out["detail"]
        assert not bridge.routes_seen


# ------------------------------------------------------------- transformación


BUILD_OK = {
    "ok": True,
    "action": "build_search_sets",
    "dry_run": False,
    "planned": [
        {"folder": "MEP", "scope_models": ["m1"],
         "sets": [{"name": "Tubo", "matches": 12, "match_mode": "int"},
                  {"name": "Ducto", "matches": 7, "match_mode": "text"}]},
        {"folder": "SIN", "scope_model_contains": "-NADA-",
         "error": "ningún modelo anexado coincide con ese token"},
    ],
    "verified_in_document": [
        {"folder": "MEP", "exists": True, "is_group": True, "children": ["Tubo", "Ducto"]},
    ],
}

RULES_OK = {
    "ok": True, "action": "apply_ignore_rules", "dry_run": False,
    "status_applied": "Approved", "matched": 5, "edited": 5,
    "tests": [{"test": "EST vs HID", "results_total": 5, "matched": 5, "by_rule": {"A VS B": 5}}],
    "verified_in_document": {"EST vs HID": {"edited": 5, "verified": 5, "mismatch": 0,
                                            "new": 0, "approved": 5}},
    "verified_edited": 5, "verification_source": "document_reread",
}


class TestEnvelope:
    def test_build_reports_created_updated_and_failed(self, monkeypatch) -> None:
        double = BridgeDouble(answers={
            "sets/list": {"sets": [{"name": "ARQ", "is_group": True}]},
            "sets/build_search": BUILD_OK,
        })
        monkeypatch.setattr(M.STATE, "bridge", double)

        out = M.navis_build_search_sets(
            folders=[{"folder": "MEP", "sets": [{"name": "Tubo"}, {"name": "Ducto"}]},
                     {"folder": "SIN", "scope_model_contains": "-NADA-", "sets": []}],
            dry_run=False,
        )

        assert out["requested"] == 2
        assert out["verified"] == 2
        assert out["created"] == ["MEP"], "MEP no existía antes, así que es creación"
        assert out["updated"] == []
        assert out["failed"] == 1, "la carpeta cuyo alcance no casó cuenta como fallo"
        assert out["status"] == "partial", "verificar 2 de 2 pero fallar una carpeta no es completed"
        assert out["verification_source"] == "document_reread"
        assert out["errors"], "el motivo del fallo tiene que viajar"
        assert out["precedence"]

    def test_an_existing_folder_is_an_update_not_a_creation(self, monkeypatch) -> None:
        double = BridgeDouble(answers={
            "sets/list": {"sets": [{"name": "MEP", "is_group": True}]},
            "sets/build_search": {**BUILD_OK, "planned": BUILD_OK["planned"][:1]},
        })
        monkeypatch.setattr(M.STATE, "bridge", double)

        out = M.navis_build_search_sets(
            folders=[{"folder": "MEP", "sets": [{"name": "Tubo"}, {"name": "Ducto"}]}],
            dry_run=False, replace_existing=True,
        )
        assert out["created"] == []
        assert out["updated"] == ["MEP"]
        assert out["status"] == "completed"

    def test_dry_run_never_claims_verification(self, monkeypatch) -> None:
        double = BridgeDouble(answers={"sets/build_search": {**BUILD_OK, "dry_run": True,
                                                             "verified_in_document": []}})
        monkeypatch.setattr(M.STATE, "bridge", double)

        out = M.navis_build_search_sets(
            folders=[{"folder": "MEP", "sets": [{"name": "Tubo"}]}], dry_run=True,
        )
        assert out["status"] == "planned"
        assert out["verification_source"] == "not_applicable"
        assert out["applied"] == 0
        assert "sets/list" not in double.routes_seen, "un ensayo no necesita foto previa"

    def test_rules_envelope_carries_every_declared_field(self, monkeypatch) -> None:
        double = BridgeDouble(answers={"clash/apply_rules": RULES_OK})
        monkeypatch.setattr(M.STATE, "bridge", double)

        out = M.navis_apply_clash_rules(
            pairs=[{"test": "EST vs HID", "a": "A", "b": "B"}],
            sets_index={"A": {}, "B": {}}, dry_run=False, only_new=True,
        )
        assert out["evaluated"] == 5
        assert out["matched"] == 5
        assert out["status_changed"] == 5
        assert out["verified"] == 5
        assert out["mismatch"] == 0
        assert out["rules_applied"] == {"A VS B": 5}
        assert out["only_new"] is True
        assert out["status"] == "completed"
        assert out["errors"] == []

    def test_a_partial_edit_is_not_reported_as_completed(self, monkeypatch) -> None:
        """Three matched, two written: the envelope must say so."""
        partial = {
            **RULES_OK, "matched": 3, "edited": 2, "verified_edited": 2,
            "tests": [{"test": "T", "results_total": 5, "matched": 3, "by_rule": {"A VS B": 3}}],
            "verified_in_document": {"T": {"edited": 2, "verified": 2, "mismatch": 1}},
        }
        double = BridgeDouble(answers={"clash/apply_rules": partial})
        monkeypatch.setattr(M.STATE, "bridge", double)

        out = M.navis_apply_clash_rules(
            pairs=[{"test": "T", "a": "A", "b": "B"}], sets_index={"A": {}, "B": {}},
            dry_run=False,
        )
        assert out["status"] == "partial"
        assert out["mismatch"] == 1
        assert out["applied"] == 2 and out["requested"] == 3

    def test_matched_but_nothing_written_is_a_failure_with_a_warning(self, monkeypatch) -> None:
        nothing = {
            **RULES_OK, "matched": 4, "edited": 0, "verified_edited": 0,
            "tests": [{"test": "T", "results_total": 4, "matched": 4, "by_rule": {}}],
            "verified_in_document": {},
        }
        double = BridgeDouble(answers={"clash/apply_rules": nothing})
        monkeypatch.setattr(M.STATE, "bridge", double)

        out = M.navis_apply_clash_rules(
            pairs=[{"test": "T", "a": "A", "b": "B"}], sets_index={"A": {}, "B": {}},
            dry_run=False,
        )
        # `partial`, not `failed`: the addin's own table reserves `failed` for
        # work that was attempted and did not land, and nothing was attempted
        # here. The warning is what carries the news, and it must be there.
        assert out["status"] == "partial"
        assert out["applied"] == 0 and out["requested"] == 4
        assert out["warnings"], "4 coincidencias y 0 escrituras no puede pasar en silencio"

    def test_the_status_table_matches_the_addin(self) -> None:
        """The rule is shared; drift between the two would be invisible."""
        cases = [
            ({"dry_run": True, "requested": 9, "applied": 9, "verified": 9,
              "failed": 0, "errors": []}, "planned"),
            ({"dry_run": False, "requested": 0, "applied": 0, "verified": 0,
              "failed": 0, "errors": []}, "completed"),
            ({"dry_run": False, "requested": 2, "applied": 2, "verified": 0,
              "failed": 0, "errors": []}, "failed"),
            ({"dry_run": False, "requested": 2, "applied": 2, "verified": 1,
              "failed": 0, "errors": []}, "partial"),
            ({"dry_run": False, "requested": 3, "applied": 2, "verified": 2,
              "failed": 0, "errors": []}, "partial"),
            ({"dry_run": False, "requested": 2, "applied": 2, "verified": 2,
              "failed": 0, "errors": []}, "completed"),
            ({"dry_run": False, "requested": 1, "applied": 0, "verified": 0,
              "failed": 0, "errors": ["x"]}, "failed"),
        ]
        for kwargs, expected in cases:
            assert M._status_for(**kwargs) == expected, kwargs


# --------------------------------------------------------------- solo lectura


class TestListIsReadOnly:
    def test_it_only_reads(self, bridge: BridgeDouble) -> None:
        bridge.answers["sets/list"] = {"sets": [
            {"name": "MEP", "guid": "g1", "is_group": True, "item_count": 0},
            {"name": "Tubo", "guid": "g2", "is_group": False, "item_count": 12},
            {"name": "Regla", "guid": "g3", "is_group": False, "item_count": 0},
        ]}
        out = M.navis_list_search_sets()

        assert bridge.routes_seen == ["sets/list"], "solo lectura: una llamada y ninguna escritura"
        assert out["count"] == 3
        kinds = {s["name"]: s["kind"] for s in out["sets"]}
        assert kinds == {"MEP": "folder", "Tubo": "explicit", "Regla": "search"}

    def test_a_search_set_reports_no_size_instead_of_zero(self, bridge: BridgeDouble) -> None:
        """Zero would read as «empty»; the truth is «not evaluated»."""
        bridge.answers["sets/list"] = {"sets": [
            {"name": "Regla", "guid": "g3", "is_group": False, "item_count": 0},
        ]}
        out = M.navis_list_search_sets()

        assert out["sets"][0]["item_count"] is None
        assert out["warnings"], "hay que decir por qué no hay tamaño"

    def test_it_names_the_document_it_read(self, bridge: BridgeDouble) -> None:
        out = M.navis_list_search_sets()
        assert out["target_id"] == "sess-1"
        assert out["document_fingerprint"] == "fp-live"
        assert out["contract"]
