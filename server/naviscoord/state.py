"""Server state, bound to the document it was derived from.

The MCP server is one long-lived process holding an expensive analysis in
memory. That cache is only meaningful for the document it came from, and
nothing in the old design said so: the analysis, the discovered grouping key
and the group roles all survived the operator closing Torre A and opening
Torre B. Every path id in the cached issues then pointed at different
geometry, and the next `navis_apply_groups` wrote a plan computed for one
building into another — with no error anywhere, because every call succeeded.

So state carries its provenance: which instance, which document, which
profile. When any of the three changes, the parts derived from it are
dropped rather than reused, and a mutation whose expected fingerprint does
not match the live document is refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .analysis.pipeline import AnalysisResult
from .bridge import Bridge
from .model import ClashExport, Issue
from .profile import Profile, checksum_of


def profile_checksum(profile: Profile) -> str:
    """Identity of the criteria a result was produced with.

    The canonical form lives in `profile.canonical_text` because the add-in
    has to reproduce it exactly; see the note there about why this used to
    disagree with C# despite claiming to mirror it.
    """
    return checksum_of(profile.raw)


class StateError(RuntimeError):
    """Raised when cached state cannot honestly answer the question asked."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": "stale_state", "detail": str(self)}
        if self.hint:
            payload["hint"] = self.hint
        return payload


@dataclass(slots=True)
class SessionState:
    """Analysis is expensive; the result is reused across tool calls."""

    profile: Profile = field(default_factory=Profile.load)
    bridge: Bridge = field(default_factory=Bridge)
    export: ClashExport | None = None
    result: AnalysisResult | None = None
    overrides: dict[str, str] = field(default_factory=dict)
    # Filled by navis_discover: how THIS model separates its trades.
    discovery: Any = None
    group_key: str = ""
    group_roles: dict[str, str] = field(default_factory=dict)

    # Provenance of everything above.
    target_id: str = ""
    document_fingerprint: str = ""
    document_title: str = ""
    profile_id: str = ""

    def __post_init__(self) -> None:
        if not self.profile_id:
            self.profile_id = profile_checksum(self.profile)

    # ------------------------------------------------------- provenance

    def stamp(self) -> dict[str, Any]:
        """Attached to every result so a report can be traced back."""
        return {
            "target_id": self.target_id,
            "document_fingerprint": self.document_fingerprint,
            "document_title": self.document_title,
            "profile": self.profile.name,
            "profile_checksum": self.profile_id,
        }

    def live_fingerprint(self, *, for_mutation: bool = False) -> tuple[str, str]:
        """(fingerprint, title) of the document the bridge is pointing at."""
        session = self.bridge.resolve(for_mutation=for_mutation)
        if session.document_fingerprint:
            # From the session file: free, and current as of the last time the
            # add-in refreshed it.
            return session.document_fingerprint, session.document_title
        health = self.bridge.health()
        document = health.get("document") or {}
        return (
            str(health.get("document_fingerprint") or document.get("fingerprint") or ""),
            str(document.get("title") or ""),
        )

    def bind(self, fingerprint: str, title: str = "", target_id: str = "") -> bool:
        """Records which document the state now describes.

        Returns True when this replaced a DIFFERENT document, which is the
        caller's cue that derived state was dropped.
        """
        changed = bool(self.document_fingerprint) and fingerprint != self.document_fingerprint
        if changed:
            self.forget_derived()
        self.document_fingerprint = fingerprint
        self.document_title = title or self.document_title
        self.target_id = target_id or self.target_id
        return changed

    def forget_derived(self) -> None:
        """Drops everything computed from a document or profile."""
        self.export = None
        self.result = None
        self.discovery = None
        self.group_key = ""
        self.group_roles = {}

    def set_profile(self, profile: Profile) -> bool:
        """Swaps the profile and drops what it invalidates.

        A profile change moves severity, tolerances, discipline rules and the
        noise filter all at once, so a cached analysis produced under the old
        one is not "slightly out of date" — it answers a different question.
        """
        new_id = profile_checksum(profile)
        changed = bool(self.profile_id) and new_id != self.profile_id
        self.profile = profile
        self.profile_id = new_id
        if changed:
            self.forget_derived()
            self.overrides = {}
        return changed

    def clear_grouping(self) -> None:
        """Used when discover produces no confident recommendation.

        Leaving a previous model's grouping key in place is worse than having
        none: the tagger silently keys on a property this model does not
        publish, every group comes back empty, and the run reports a clean
        project.
        """
        self.group_key = ""
        self.group_roles = {}
        self.discovery = None

    # -------------------------------------------------------- guards

    def require_result(self) -> AnalysisResult:
        if self.result is None:
            raise StateError(
                "Todavía no hay análisis en memoria. Corre navis_analyze primero."
            )
        return self.result

    def require_fresh_result(self) -> AnalysisResult:
        """The analysis, but only if it still describes the open document."""
        result = self.require_result()
        live, title = self.live_fingerprint()
        if live and self.document_fingerprint and live != self.document_fingerprint:
            self.forget_derived()
            raise StateError(
                f"El análisis en memoria es de otro documento ({self.document_title or 'anterior'}) "
                f"y el abierto ahora es «{title or live}». Lo descarté en vez de responder con datos "
                "que ya no corresponden.",
                hint="Corre navis_analyze de nuevo sobre el documento actual.",
            )
        return result

    def require_mutable(self, expected_fingerprint: str = "") -> str:
        """The fingerprint a mutation may act on, or a refusal.

        `for_mutation=True` on the resolve is what makes several open
        instances an error rather than a silent choice.
        """
        live, title = self.live_fingerprint(for_mutation=True)
        if not live:
            raise StateError(
                "No pude leer la huella del documento activo; no voy a mutar a ciegas.",
                hint="Comprueba navis_health: puede que Navisworks aún esté cargando el federado.",
            )

        expected = (expected_fingerprint or "").strip()
        if not expected:
            raise StateError(
                "Falta expected_document_fingerprint; no voy a mutar usando una huella cacheada.",
                hint="Lee la huella vigente con navis_health y repite la operación explícitamente.",
            )
        if expected != live:
            raise StateError(
                f"El documento activo («{title or live}») no es el que esperabas ({expected}). "
                "No se tocó nada.",
                hint=(
                    "Si de verdad quieres actuar sobre el documento abierto ahora, corre "
                    "navis_analyze de nuevo y repite; si querías el otro, selecciónalo con "
                    "navis_target."
                ),
            )

        self.bind(live, title)
        return live

    def issue(self, issue_id: str) -> Issue:
        result = self.require_fresh_result()
        for issue in result.issues:
            if issue.issue_id.lower() == issue_id.lower():
                return issue
        raise StateError(f"No existe el problema '{issue_id}'.")
