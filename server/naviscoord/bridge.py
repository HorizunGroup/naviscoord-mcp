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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .sessions import SessionInfo, TargetError, legacy_session_file
from .sessions import discover as discover_sessions
from .sessions import select as select_session

DEFAULT_TIMEOUT = 180.0

# Kept as aliases: `Session` and `session_file` were the public names before
# the registry, and third-party scripts import them.
Session = SessionInfo


def session_file() -> Path:
    """The pre-registry single session file. Compatibility only."""
    return legacy_session_file()


class BridgeError(RuntimeError):
    """Raised with a message meant to be shown to a human, not parsed."""

    def __init__(
        self,
        message: str,
        *,
        hint: str = "",
        detail: Any = None,
        code: str = "",
        candidates: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.hint = hint
        self.detail = detail
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

    # -------------------------------------------------------- capabilities

    def capabilities(self, refresh: bool = False) -> dict[str, Any]:
        """What the connected addin actually offers.

        Cached per session: the answer cannot change without the addin being
        reinstalled, which mints a new session anyway.
        """
        if self._capabilities is None or refresh:
            self._capabilities = self.call("capabilities")
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

    def call(self, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
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
        refreshed = False
        while True:
            session = self.session
            body = json.dumps(payload or {}).encode("utf-8")
            request = urllib.request.Request(
                f"{session.base_url}/{route.lstrip('/')}",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "X-NavisCoord-Token": session.token,
                },
            )

            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = _safe_json(exc)
                if exc.code == 401:
                    self.invalidate()
                    if not refreshed and self._session_changed(session):
                        refreshed = True
                        continue
                    raise BridgeError(
                        "El token de sesión fue rechazado.",
                        hint="Navisworks se reinició y generó uno nuevo. Reintenta.",
                        detail=detail,
                    ) from exc
                if exc.code == 503:
                    raise BridgeError(
                        "Navisworks está ocupado y la cola del puente está llena.",
                        hint="Ejecuta una sola operación a la vez; reintenta en unos segundos.",
                        detail=detail,
                    ) from exc
                # Surface the addin's own message. "responded 500" tells
                # nobody anything; the sentence the addin wrote is the point.
                message = ""
                if isinstance(detail, dict):
                    message = str(detail.get("detail") or detail.get("error") or "")
                raise BridgeError(
                    message or f"El complemento respondió {exc.code}.",
                    hint="Revisa el estado con navis_health.",
                    detail=detail,
                ) from exc
            except urllib.error.URLError as exc:
                self.invalidate()
                if not refreshed and self._session_changed(session):
                    refreshed = True
                    continue
                raise BridgeError(
                    "No hay respuesta del complemento en Navisworks.",
                    hint=(
                        "Verifica que Navisworks siga abierto y que el puente esté activo "
                        "(pestaña Add-Ins → NavisCoord)."
                    ),
                    detail=str(exc.reason),
                ) from exc

    def _session_changed(self, stale: SessionInfo) -> bool:
        """True when the registry now describes a different bridge."""
        try:
            fresh = self.resolve()
        except BridgeError:
            return False
        return fresh.token != stale.token or fresh.port != stale.port

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

    def export_clashes(
        self,
        properties: list[str] | None = None,
        tests: list[str] | None = None,
        statuses: list[str] | None = None,
        limit: int = 0,
    ) -> dict[str, Any]:
        return self.call(
            "clash/export",
            {
                "properties": properties or [],
                "tests": tests or [],
                "statuses": statuses or [],
                "limit": limit,
            },
        )

    def clash_image(
        self, clash_guid: str, width: int = 900, height: int = 600, style: str = "ScenePlusOverlay"
    ) -> dict[str, Any]:
        return self.call(
            "clash/image",
            {"clash_guid": clash_guid, "width": width, "height": height, "style": style},
        )

    def build_sets(self, disciplines: list[dict[str, Any]], prefix: str, dry_run: bool) -> dict[str, Any]:
        return self.call(
            "sets/build",
            {"disciplines": disciplines, "prefix": prefix, "dry_run": dry_run},
        )

    def build_matrix(
        self, pairs: list[dict[str, Any]], prefix: str, dry_run: bool, replace_existing: bool = False
    ) -> dict[str, Any]:
        return self.call(
            "clash/matrix",
            {
                "pairs": pairs,
                "prefix": prefix,
                "dry_run": dry_run,
                "replace_existing": replace_existing,
            },
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

    def run_tests(self, tests: list[str] | None = None) -> dict[str, Any]:
        return self.call("clash/run", {"tests": tests or []})

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

    def select(self, path_ids: list[str]) -> dict[str, Any]:
        return self.call("selection/set", {"path_ids": path_ids})


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
