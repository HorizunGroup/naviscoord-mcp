"""HTTP client for the Navisworks addin.

The addin publishes one session file per running instance, each with its port
and a random token; this reads that registry (see `sessions.py`). The
handshake is what keeps the bridge from being an open door: reaching
Navisworks requires the ability to read that user's own AppData, not merely
the ability to open a socket on localhost.

Standard library only — no requests dependency — so the MCP server installs
anywhere Python does.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .sessions import SessionInfo, TargetError, legacy_session_file
from .sessions import discover as discover_sessions
from .sessions import is_alive
from .sessions import select as select_session

DEFAULT_TIMEOUT = 180.0

# Kept as aliases: `Session` and `session_file` were the public names before
# the registry, and third-party scripts import them.
Session = SessionInfo


def session_file() -> Path:
    """The pre-registry single session file. Compatibility only."""
    return legacy_session_file()


# Statuses that mean the addin understood the request and refused it. None of
# them can be changed by asking again, and for a mutation a blind replay is how
# the same work lands twice.
_NEVER_RETRY = frozenset({400, 403, 404, 409, 413, 422})


class BridgeError(RuntimeError):
    """Raised with a message meant to be shown to a human, not parsed."""

    def __init__(
        self,
        message: str,
        *,
        hint: str = "",
        detail: Any = None,
        code: str = "",
        status: int = 0,
        candidates: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.hint = hint
        self.detail = detail
        # The HTTP status, kept rather than collapsed. Every failure used to
        # arrive as the same generic BridgeError, so a caller could not tell a
        # malformed payload (400) from a busy host (503) from a semantic
        # conflict (409) — and therefore could not tell what was worth
        # retrying.
        self.status = status
        # Carried so wrapping a TargetError does not flatten it. Without
        # these, the same condition reached the caller in two different
        # shapes depending on where it was raised — `{"error": code,
        # "detail": message, "candidates": [...]}` when it escaped directly,
        # and `{"error": message, "detail": [candidates]}` once wrapped — so
        # nothing could handle "which instance did you mean?" uniformly.
        self.code = code
        self.candidates = candidates or []

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": self.code or str(self)}
        if self.status:
            payload["http_status"] = self.status
        if self.code:
            payload["detail"] = str(self)
        elif self.detail is not None:
            payload["detail"] = self.detail
        if self.hint:
            payload["hint"] = self.hint
        if self.candidates:
            payload["candidates"] = self.candidates
        return payload


class CapabilityError(BridgeError):
    """The running addin does not offer what was asked for.

    Its own class because the fix is different from every other failure: not
    "retry" or "check the model", but "close Navisworks and install a newer
    addin". A caller that cannot tell those apart makes the user chase the
    wrong problem.
    """


def read_session(path: Path | None = None) -> SessionInfo:
    """One session, for callers that predate targeting."""
    if path is not None:
        from .sessions import _read_file

        session = _read_file(path)
        if session is None:
            raise BridgeError(
                f"El archivo de sesión en {path} no existe o está corrupto.",
                hint="Cierra y reabre Navisworks para regenerarlo.",
            )
        return session
    try:
        return select_session()
    except TargetError as exc:
        raise BridgeError(
                str(exc), hint=exc.hint, code="target_ambiguous",
                candidates=exc.candidates,
            ) from exc


class Bridge:
    """Thin request/response wrapper over the addin's routes."""

    def __init__(
        self,
        session: SessionInfo | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        target_id: str = "",
    ) -> None:
        self._session = session
        self.timeout = timeout
        # Empty means "resolve each time". A pinned target is what makes a
        # mutation unambiguous when several instances are open.
        self.target_id = target_id
        self._capabilities: dict[str, Any] | None = None
        # The session the cached capabilities describe. A manifest belongs to
        # ONE addin build; keeping it across a restart is how a caller is told
        # a route exists on an addin that no longer has it.
        self._capabilities_session: str = ""
        self._retry_key: str = ""
        self._retry_fingerprint: str = ""

    @property
    def session(self) -> SessionInfo:
        # Re-read lazily: Navisworks may have been restarted since the MCP
        # server booted, which mints a new token.
        if self._session is None:
            self._session = self.resolve()
        return self._session

    def resolve(self, *, for_mutation: bool = False) -> SessionInfo:
        try:
            return select_session(self.target_id, for_mutation=for_mutation)
        except TargetError as exc:
            raise BridgeError(
                str(exc), hint=exc.hint, code="target_ambiguous",
                candidates=exc.candidates,
            ) from exc

    def pin(self, target_id: str) -> SessionInfo:
        """Fixes which instance every later call goes to."""
        session = self.resolve_target(target_id)
        self.target_id = session.target_id
        self._session = session
        self._capabilities = None
        return session

    def resolve_target(self, target_id: str) -> SessionInfo:
        try:
            return select_session(target_id)
        except TargetError as exc:
            raise BridgeError(
                str(exc), hint=exc.hint, code="target_ambiguous",
                candidates=exc.candidates,
            ) from exc

    def unpin(self) -> None:
        self.target_id = ""
        self.invalidate()

    def invalidate(self) -> None:
        self._session = None
        self._capabilities = None
        self._capabilities_session = ""

    def snapshot(self, *, for_mutation: bool = False) -> SessionInfo:
        """The one session a call will use, resolved now.

        Every field a request needs comes from this single object: endpoint,
        token, target_id, session_id and document fingerprint. The bug this
        replaces was subtle and expensive — ``resolve()`` returned the NEW
        session while ``self._session`` still cached the old one, and ``call()``
        used the cached one. So a fingerprint could be read from one instance
        and the request sent to another, and the provenance on the report named
        an instance that never saw the work.
        """
        if self._session is None:
            self._session = self.resolve(for_mutation=for_mutation)
        return self._session

    # -------------------------------------------------------- capabilities

    def capabilities(self, refresh: bool = False) -> dict[str, Any]:
        """What the connected addin actually offers.

        Cached per session: the answer cannot change without the addin being
        reinstalled, which mints a new session anyway.
        """
        # Which session the cached manifest belongs to. Resolution can fail
        # here — there may be no Navisworks at all — and that is not a reason
        # to refuse: the call below will raise its own, better error. What
        # matters is that a manifest cached under session A is never served for
        # session B.
        try:
            current = self.snapshot().session_id
        except BridgeError:
            current = self._capabilities_session

        # An empty marker means the manifest was injected rather than fetched
        # (the test doubles do this), so there is no session it can be stale
        # against. Once a real fetch records one, a change invalidates it.
        stale = bool(self._capabilities_session) and current != self._capabilities_session
        if self._capabilities is None or refresh or stale:
            self._capabilities = self.call("capabilities")
            self._capabilities_session = current
        return self._capabilities

    def require(self, route: str, *, since: str = "0.1.2") -> None:
        """Fails with an actionable message when a route is not offered.

        Called before anything that a pre-registry addin lacks. Without it the
        failure surfaces as `unknown_route`, which tells the operator nothing
        about what to do; with it they are told to update the addin and to
        which version.
        """
        try:
            offered = self.capabilities().get("routes") or []
        except BridgeError:
            # Capabilities itself is new. An addin that does not have it
            # certainly does not have whatever we were about to call.
            raise CapabilityError(
                f"El complemento de Navisworks instalado no expone «{route}» "
                "(tampoco expone 'capabilities', así que es anterior a esta versión).",
                hint=(
                    f"Actualiza el complemento a {since} o posterior: cierra Navisworks y "
                    "ejecuta install.ps1 o el instalador."
                ),
            ) from None

        if route not in offered:
            raise CapabilityError(
                f"El complemento de Navisworks instalado no expone «{route}».",
                hint=(
                    f"Actualiza el complemento a {since} o posterior (cierra Navisworks y "
                    "ejecuta install.ps1 o el instalador). Rutas disponibles: "
                    + ", ".join(sorted(str(r) for r in offered))
                ),
                detail={"available": offered},
            )

    def call(
        self,
        route: str,
        payload: dict[str, Any] | None = None,
        *,
        session: SessionInfo | None = None,
        mutating: bool | None = None,
    ) -> dict[str, Any]:
        """One request, with a single silent retry across a Navisworks swap.

        Closing 2024 and opening 2026 mints a new token, often on the same
        port. The cached session then fails — measured live: the first call
        after the switch died with "token rechazado" and only the second
        recovered. One failed call is one error a user sees for something the
        client can resolve alone, so on a dead endpoint or a rejected token
        the session file is re-read, and if it CHANGED the request retries
        once with the fresh session. Unchanged, the error stands: retrying
        against the same dead endpoint would only mask a real outage.
        """
        payload = dict(payload or {})
        derived_routes = {"clash/group", "clash/status", "viewpoints/save", "appearance/color", "selection/set"}
        revision = getattr(self, "analysis_revision", "")
        if route in derived_routes and revision:
            payload["expected_analysis_revision"] = revision
        if route == "job/submit" and revision and payload.get("route") in derived_routes:
            payload["payload"] = {**payload.get("payload", {}), "expected_analysis_revision": revision}
        body_payload = payload
        if mutating is None:
            # A payload that carries a fingerprint or an idempotency key is a
            # mutation by construction; the caller can still say so explicitly.
            mutating = bool(
                body_payload.get("expected_document_fingerprint")
                or body_payload.get("idempotency_key")
            )
        inner = body_payload.get("payload", body_payload) if route == "job/submit" else body_payload
        readonly = os.environ.get("NAVISCOORD_READ_ONLY", "").lower() in {"1", "true", "yes"}
        write_routes = derived_routes | {"sets/build", "sets/build_search", "clash/matrix", "clash/run",
            "clash/apply_rules", "appearance/reset", "workflow/configure", "workflow/run",
            "workflow/group_levels", "workflow/rules", "document/save", "document/save_as",
            "document/close", "application/exit"}
        operation = body_payload.get("route", "") if route == "job/submit" else route
        writes = mutating or operation in write_routes or (route == "job/submit" and operation != "workflow/audit_models")
        if readonly and writes and inner.get("dry_run") is not True:
            raise BridgeError("Read-only mode refuses this write.", code="read_only")
        self._retry_key = str(body_payload.get("idempotency_key") or "")
        self._retry_fingerprint = str(body_payload.get("expected_document_fingerprint") or "")

        refreshed = False
        while True:
            # ONE snapshot per attempt, used for the endpoint, the token and
            # the attribution. A retry takes a whole new snapshot rather than
            # mixing a fresh endpoint with a stale fingerprint.
            current = session if session is not None else self.snapshot()
            body = json.dumps(payload or {}).encode("utf-8")
            request = urllib.request.Request(
                f"{current.base_url}/{route.lstrip('/')}",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "X-NavisCoord-Token": current.token,
                },
            )

            try:
                return self._transport(request)
            except urllib.error.HTTPError as exc:
                detail = _safe_json(exc)
                code = ""
                message = ""
                if isinstance(detail, dict):
                    code = str(detail.get("error") or "")
                    message = str(detail.get("detail") or code or "")

                if exc.code in _NEVER_RETRY:
                    # A refusal the addin meant. Retrying cannot change a
                    # malformed payload, a semantic conflict or an invalid
                    # profile, and retrying a 409 on a mutation is how the same
                    # work is applied twice.
                    raise BridgeError(
                        message or f"El complemento respondió {exc.code}.",
                        hint=str(detail.get("hint") or "") if isinstance(detail, dict) else "",
                        code=code,
                        status=exc.code,
                        candidates=detail.get("candidates") if isinstance(detail, dict) else None,
                        detail=detail,
                    ) from exc

                if exc.code == 401:
                    self.invalidate()
                    # Only when the registry really moved. An unchanged token
                    # that was rejected is a genuine authentication failure, and
                    # replaying against the same endpoint just doubles it.
                    if not refreshed and not mutating and self._session_changed(current):
                        refreshed = True
                        session = None
                        continue
                    if not refreshed and mutating and self._may_retry_mutation(current):
                        refreshed = True
                        session = None
                        continue
                    raise BridgeError(
                        "El token de sesión fue rechazado.",
                        hint="Navisworks se reinició y generó uno nuevo. Reintenta.",
                        code=code or "unauthorized",
                        status=exc.code,
                        detail=detail,
                    ) from exc
                if exc.code == 503:
                    raise BridgeError(
                        message or "Navisworks está ocupado y la cola del puente está llena.",
                        hint="Ejecuta una sola operación a la vez; reintenta en unos segundos.",
                        code=code or "unavailable",
                        status=exc.code,
                        detail=detail,
                    ) from exc
                # Surface the addin's own message. "responded 500" tells
                # nobody anything; the sentence the addin wrote is the point.
                raise BridgeError(
                    message or f"El complemento respondió {exc.code}.",
                    hint="Revisa el estado con navis_health.",
                    code=code,
                    status=exc.code,
                    detail=detail,
                ) from exc
            except urllib.error.URLError as exc:
                self.invalidate()
                # The connection failed, so the request may or may not have
                # reached the addin. For a read that is harmless. For a mutation
                # it is not, and only an idempotency key makes the replay safe.
                if not refreshed and not mutating and self._session_changed(current):
                    refreshed = True
                    session = None
                    continue
                if not refreshed and mutating and self._may_retry_mutation(current):
                    refreshed = True
                    session = None
                    continue
                raise BridgeError(
                    "No hay respuesta del complemento en Navisworks.",
                    hint=(
                        "Verifica que Navisworks siga abierto y que el puente esté activo "
                        "(pestaña Add-Ins → NavisCoord)."
                    ),
                    detail=str(exc.reason),
                ) from exc

    def _transport(self, request: "urllib.request.Request") -> dict[str, Any]:
        """The one place a request actually leaves the process.

        Separated so the retry POLICY above can be exercised without a socket.
        The policy is the part that decides whether the same mutation is sent
        twice, and a rule that can only be tested against a live Navisworks is
        a rule that is never tested.
        """
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _session_changed(self, stale: SessionInfo) -> bool:
        """True when the registry now describes a different bridge."""
        try:
            fresh = self.resolve()
        except BridgeError:
            return False
        return fresh.token != stale.token or fresh.port != stale.port

    def _may_retry_mutation(self, stale: SessionInfo) -> bool:
        """Whether replaying a mutation is safe, not merely convenient.

        Three conditions, all of them:

        * the request carried an ``idempotency_key``, so a request that DID
          reach the addin is recognised and replayed instead of applied twice;
        * the registry really moved, rather than the endpoint being down;
        * and the new session is the SAME target and document. Sending a
          mutation to whichever instance happens to be newest is how somebody's
          other model gets edited.
        """
        if not self._retry_key:
            return False
        try:
            fresh = self.resolve()
        except BridgeError:
            return False
        if fresh.token == stale.token and fresh.port == stale.port:
            return False
        if fresh.target_id != stale.target_id:
            return False
        return not self._retry_fingerprint or fresh.document_fingerprint == self._retry_fingerprint

    # ------------------------------------------------------------- routes

    def health(self) -> dict[str, Any]:
        return self.call("health")

    def sessions(self) -> list[SessionInfo]:
        return discover_sessions()

    # ------------------------------------------------------------- jobs

    def submit_job(
        self,
        route: str,
        payload: dict[str, Any] | None = None,
        target_id: str = "",
    ) -> dict[str, Any]:
        self.require("job/submit", since="0.1.2")
        return self.call(
            "job/submit",
            {"route": route, "payload": payload or {}, "target_id": target_id or self.target_id},
        )

    def job_status(self, job_id: str) -> dict[str, Any]:
        return self.call("job/status", {"job_id": job_id})

    def job_list(self) -> dict[str, Any]:
        return self.call("job/list")

    def job_cancel(self, job_id: str) -> dict[str, Any]:
        return self.call("job/cancel", {"job_id": job_id})

    # ------------------------------------------------------ document/save

    def save(self, expected_fingerprint: str, **kwargs: Any) -> dict[str, Any]:
        self.require("document/save")
        return self.call(
            "document/save",
            {"expected_document_fingerprint": expected_fingerprint, **kwargs},
        )

    def save_as(self, path: str, expected_fingerprint: str, **kwargs: Any) -> dict[str, Any]:
        self.require("document/save_as")
        return self.call(
            "document/save_as",
            {
                "path": path,
                "expected_document_fingerprint": expected_fingerprint,
                **kwargs,
            },
        )

    def close_document(
        self, disposition: str, expected_fingerprint: str, **kwargs: Any
    ) -> dict[str, Any]:
        self.require("document/close", since="0.2.3")
        return self.call(
            "document/close",
            {
                "disposition": disposition,
                "expected_document_fingerprint": expected_fingerprint,
                **kwargs,
            },
        )

    def exit_application(
        self,
        disposition: str,
        expected_fingerprint: str,
        *,
        dry_run: bool = True,
        verify_timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Ask Navisworks to exit, then verify the process actually ended."""
        self.require("application/exit", since="0.2.3")
        session = self.resolve(for_mutation=True)
        self._session = session
        result = self.call(
            "application/exit",
            {
                "disposition": disposition,
                "expected_document_fingerprint": expected_fingerprint,
                "dry_run": dry_run,
            },
        )
        if dry_run or not result.get("exit_requested"):
            return result

        deadline = time.monotonic() + max(0.1, min(float(verify_timeout), 60.0))
        while time.monotonic() < deadline and is_alive(session):
            time.sleep(0.1)

        exited = not is_alive(session)
        result["application_exit_verified"] = exited
        result["verification_source"] = "process_liveness"
        result["status"] = "completed" if exited else "partial"
        if not exited:
            warnings = result.setdefault("warnings", [])
            warnings.append(
                "Navisworks sigue abierto. Puede haber un diálogo de otro complemento o "
                "la ventana principal rechazó el cierre; no se reporta como completado."
            )
        self.invalidate()
        return result

    # --------------------------------------------------------- workflow

    def workflow(self, step: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        route = f"workflow/{step}"
        self.require(route)
        return self.call(route, payload or {})

    def profile_info(self) -> dict[str, Any]:
        """What profile the addin will actually use for the next step.

        No `path` argument any more: it used to hand the addin a path to open,
        which made an authenticated route into a general-purpose file reader
        running inside Navisworks — and it answered about a file rather than
        about what is in force, which is the question being asked.
        """
        self.require("profile/info", since="0.2.1")
        return self.call("profile/info")

    def profile_load(self, canonical: str, checksum: str) -> dict[str, Any]:
        """Installs a profile in the addin as this session's own.

        The canonical text is sent, not a path: the addin must never be told
        to open a file chosen by whatever composed this call, and a profile
        loaded here has to work even when the file lives only on the machine
        running the MCP server.
        """
        self.require("profile/load", since="0.2.1")
        return self.call("profile/load", {"canonical": canonical, "checksum": checksum})

    def profile_reset(self) -> dict[str, Any]:
        """Drops the pushed profile; the addin's own default applies again."""
        self.require("profile/reset", since="0.2.1")
        return self.call("profile/reset")

    def census(self, category_sample: int = 20000) -> dict[str, Any]:
        return self.call("census", {"category_sample": category_sample})

    def model_schema(self, sample: int = 20000, stride: int = 0) -> dict[str, Any]:
        return self.call("model/schema", {"sample": sample, "stride": stride})

    def list_tests(self) -> dict[str, Any]:
        return self.call("clash/tests")

    def analysis_state(self) -> dict[str, Any]:
        return self.call("analysis/revision")

    def export_clashes(
        self,
        properties: list[str] | None = None,
        tests: list[str] | None = None,
        statuses: list[str] | None = None,
        limit: int = 0,
        penetration_categories: list[str] | None = None,
        penetration_keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        result = self.call(
            "clash/export",
            {
                "properties": properties or [],
                "tests": tests or [],
                "statuses": statuses or [],
                "limit": limit,
                "penetration_categories": penetration_categories or [],
                "penetration_keywords": penetration_keywords or [],
            },
        )
        if not result.get("analysis_revision"):
            raise BridgeError("The add-in cannot track analysis freshness. Update the NavisCoord add-in.", code="analysis_revision_required")
        return result

    def clash_image(
        self,
        clash_guid: str,
        options: dict[str, Any] | None = None,
        width: int = 900,
        height: int = 600,
        style: str = "ScenePlusOverlay",
    ) -> dict[str, Any]:
        """Renders one clash. `options` carries the framing and visual schema.

        The three positional arguments stay because they are what the first
        version took, and a saved call that passes them must keep working.
        `options` is merged over them, so a caller that supplies both gets the
        richer one — which is the only ordering that lets the defaults move
        without breaking an old call.
        """
        payload: dict[str, Any] = {
            "clash_guid": clash_guid,
            "width": width,
            "height": height,
            "style": style,
        }
        payload.update(options or {})
        return self.call("clash/image", payload)

    def build_sets(self, disciplines: list[dict[str, Any]], prefix: str, dry_run: bool,
                   fingerprint: str = "", idempotency_key: str = "") -> dict[str, Any]:
        return self.call(
            "sets/build",
            _guarded({"disciplines": disciplines, "prefix": prefix, "dry_run": dry_run}, fingerprint, idempotency_key),
        )

    def build_matrix(
        self, pairs: list[dict[str, Any]], prefix: str, dry_run: bool, replace_existing: bool = False,
        fingerprint: str = "", idempotency_key: str = ""
    ) -> dict[str, Any]:
        return self.call(
            "clash/matrix",
            _guarded({
                "pairs": pairs,
                "prefix": prefix,
                "dry_run": dry_run,
                "replace_existing": replace_existing,
            }, fingerprint, idempotency_key),
        )

    def list_sets(self) -> dict[str, Any]:
        """Every selection set and folder in the document. Reads, never writes."""
        self.require("sets/list")
        return self.call("sets/list")

    def run_route(
        self, route: str, payload: dict[str, Any], *, run_async: bool
    ) -> dict[str, Any]:
        """Capability check, then the route — as a job or straight through.

        The same shape `_workflow` uses for the ribbon steps, because the
        choice is identical: a federation takes minutes, and holding the HTTP
        connection open for them is how a caller ends up with a socket timeout
        on top of work that is still running. The capability check comes
        first either way, so an addin that lacks the route says so instead of
        failing as `unknown_route` inside a job nobody can interpret.
        """
        self.require(route)
        if run_async:
            return self.submit_job(route, payload)
        return self.call(route, payload)

    def run_tests(self, tests: list[str] | None = None, fingerprint: str = "", idempotency_key: str = "") -> dict[str, Any]:
        return self.call("clash/run", _guarded({"tests": tests or []}, fingerprint, idempotency_key))

    def apply_groups(
        self,
        groups: list[dict[str, Any]],
        dry_run: bool,
        fingerprint: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        return self.call(
            "clash/group",
            _guarded({"groups": groups, "dry_run": dry_run}, fingerprint, idempotency_key),
        )

    def set_status(
        self,
        clash_guids: list[str],
        status: str,
        dry_run: bool,
        fingerprint: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        return self.call(
            "clash/status",
            _guarded(
                {"clash_guids": clash_guids, "status": status, "dry_run": dry_run},
                fingerprint,
                idempotency_key,
            ),
        )

    def save_viewpoints(
        self,
        viewpoints: list[dict[str, Any]],
        dry_run: bool,
        fingerprint: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        return self.call(
            "viewpoints/save",
            _guarded({"viewpoints": viewpoints, "dry_run": dry_run}, fingerprint, idempotency_key),
        )

    def color(self, path_ids: list[str], rgb: tuple[int, int, int], transparency: float = -1.0) -> dict[str, Any]:
        r, g, b = rgb
        return self.call(
            "appearance/color",
            {"path_ids": path_ids, "r": r, "g": g, "b": b, "transparency": transparency},
        )

    def reset_appearance(self, path_ids: list[str] | None = None) -> dict[str, Any]:
        return self.call("appearance/reset", {"path_ids": path_ids or []})

    def select(
        self,
        path_ids: list[str],
        *,
        expected_document_fingerprint: str = "",
    ) -> dict[str, Any]:
        """Selects elements — a mutation, so it carries the fingerprint.

        The selection dies with the session, which is why it looked harmless
        and travelled without a guard. It is still a change applied to whatever
        document happens to be open, and the path ids come from an analysis of
        a document that may not be that one.
        """
        payload = _guarded({"path_ids": path_ids}, expected_document_fingerprint, "")
        return self.call("selection/set", payload, mutating=bool(expected_document_fingerprint))


def _guarded(payload: dict[str, Any], fingerprint: str, idempotency_key: str) -> dict[str, Any]:
    """Adds the mutation guards, omitting the ones the caller left empty.

    Sending an empty `expected_document_fingerprint` would read to the addin
    as "did not ask", which is the correct meaning — but sending the key at
    all when it is empty makes an old addin log a field it does not
    understand. Omitting is quieter and means exactly the same thing.
    """
    if fingerprint:
        payload["expected_document_fingerprint"] = fingerprint
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    return payload


def _safe_json(exc: urllib.error.HTTPError) -> Any:
    try:
        return json.loads(exc.read().decode("utf-8"))
    except Exception:
        return None
