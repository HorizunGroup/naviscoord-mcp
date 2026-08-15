"""Noise filtering: separating clashes from problems.

A raw Navisworks run is dominated by hits that no one will ever act on — a
pipe touching its own elbow, insulation grazing a wall, a sleeve doing
exactly the job it was modelled for. Left in, they bury the real issues and
inflate every count in the report.

Nothing is discarded silently. Each rejection is tallied by reason and the
clashes themselves are kept, so a coordinator can always ask what was
filtered and why, and put a rule back if it is throwing away real work.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..model import Clash, ElementRef
from ..profile import Profile


@dataclass(slots=True)
class FilterResult:
    kept: list[Clash] = field(default_factory=list)
    dropped: list[tuple[Clash, str]] = field(default_factory=list)
    reasons: Counter[str] = field(default_factory=Counter)
    # Sleeves, shafts and openings the filter removed, kept as an index.
    #
    # This exists because dropping them broke the root-cause detector that
    # depends on them. A sleeve doing its job is correctly filtered as
    # `designed_pass_through` — it is not an interference — but
    # `_missing_penetration` then scanned only the SURVIVING clashes for
    # sleeves, found none, and reported "the model has not a single defined
    # pass" at 0.9 confidence on a model full of them. The confident claim was
    # the exact opposite of the truth, and it was produced BY the filter
    # working correctly.
    #
    # So the evidence is preserved separately: filtered out of the ranking,
    # still available to anything reasoning about why a clash exists.
    penetration_elements: dict[str, ElementRef] = field(default_factory=dict)

    @property
    def kept_count(self) -> int:
        return len(self.kept)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)

    @property
    def sleeves(self) -> list[ElementRef]:
        """Every penetration element seen anywhere in the export."""
        return list(self.penetration_elements.values())

    def warnings(self) -> list[str]:
        """Flags a filter that is eating the project instead of the noise.

        Each rule here is meant to remove a slice of a run. When one of them
        removes most of it, the likely cause is bad input or a bad rule, not
        a clean model — and reporting "0 problems" in that state is the worst
        possible failure, because it looks like good news.
        """
        total = self.kept_count + self.dropped_count
        if total == 0:
            return []

        out: list[str] = []
        explanations = {
            "same_element": (
                "Suele significar que ambos lados del cruce se están resolviendo al mismo "
                "elemento padre. Si el modelo viene de Revit, revisa que el export publique "
                "los compuestos y no solo nodos de categoría o nivel."
            ),
            "below_tolerance": (
                "El umbral min_penetration_m del perfil puede estar demasiado alto para las "
                "unidades de este modelo."
            ),
            "status_excluded": (
                "El equipo ya marcó como aprobado o resuelto la mayor parte del modelo; "
                "puede ser correcto, pero conviene confirmarlo antes de reportar cero."
            ),
        }

        for reason, count in self.reasons.items():
            share = count / total
            if share >= 0.5:
                detail = explanations.get(reason, "")
                out.append(
                    f"El filtro descartó {share:.0%} de los cruces por '{reason}'. {detail}".strip()
                )
        return out

    def to_json(self) -> dict[str, object]:
        return {
            "input": self.kept_count + self.dropped_count,
            "kept": self.kept_count,
            "dropped": self.dropped_count,
            "dropped_by_reason": dict(self.reasons.most_common()),
            "explanation": {
                "same_element": "las dos caras del cruce son el mismo elemento o el mismo padre",
                "below_tolerance": "penetración por debajo del umbral: contacto, no interferencia",
                "status_excluded": "el equipo ya lo aprobó o resolvió en una corrida anterior",
                "same_system_fitting": "tubo o ducto contra su propio accesorio conectado",
                "insulation_graze": "solo el aislamiento roza, el elemento no",
                "designed_pass_through": "el cruce ocurre por un pasamuros o vano modelado a propósito",
                "designed_embedment": "dispositivo empotrado en su anfitrión por diseño (tomacorriente en muro, luminaria en cielo)",
                "architectural_hosting": "puerta o ventana dentro de su propio muro: el traslape ES el vano",
                "architectural_self_overlap": "arquitectura contra sí misma (muro que sube a losa contra cielo colgado): calidad de modelo, no coordinación",
                "previously_approved": "huella registrada como aprobada en una corrida anterior",
            },
        }


class NoiseFilter:
    def __init__(self, profile: Profile, approved_fingerprints: set[str] | None = None) -> None:
        self.profile = profile
        self.approved = approved_fingerprints or set()
        spec_categories = profile.noise_param("insulation_categories", [])
        self._insulation = {c.strip().lower() for c in spec_categories}
        self._pass_categories = {
            c.strip().lower() for c in profile.noise_param("pass_through_categories", [])
        }
        self._pass_keywords = [
            k.strip().lower() for k in profile.noise_param("pass_through_keywords", []) if k.strip()
        ]
        self._drop_statuses = {
            s.strip().lower() for s in profile.noise_param("drop_statuses", [])
        }

    def run(self, clashes: list[Clash]) -> FilterResult:
        result = FilterResult()
        for clash in clashes:
            # Indexed BEFORE the verdict, and for kept clashes too: a sleeve
            # is evidence wherever it appears, and whether this particular
            # clash survives the filter has nothing to do with it.
            for side in (clash.a, clash.b):
                if self.is_penetration_element(side):
                    result.penetration_elements.setdefault(side.path_id, side)

            reason = self._reject_reason(clash)
            if reason:
                result.dropped.append((clash, reason))
                result.reasons[reason] += 1
            else:
                result.kept.append(clash)
        return result

    def is_penetration_element(self, element: ElementRef) -> bool:
        """A sleeve, shaft or modelled opening — by category or by name."""
        if element.category.strip().lower() in self._pass_categories:
            return True
        haystack = f"{element.display_name} {element.parent_name}".lower()
        return any(keyword in haystack for keyword in self._pass_keywords)

    # ------------------------------------------------------------- rules

    def _reject_reason(self, clash: Clash) -> str | None:
        if clash.status.strip().lower() in self._drop_statuses:
            return "status_excluded"

        if fingerprint(clash) in self.approved:
            return "previously_approved"

        if clash.a.group_key == clash.b.group_key:
            return "same_element"

        if self._is_designed_pass_through(clash):
            return "designed_pass_through"

        if self._is_flush_mounted(clash):
            return "designed_embedment"

        architectural = self._architectural_by_design(clash)
        if architectural:
            return architectural

        if self._is_insulation_graze(clash):
            return "insulation_graze"

        if self._is_same_system_fitting(clash):
            return "same_system_fitting"

        # Tolerance goes last so that everything above stays visible in the
        # tally even when the overlap is also tiny.
        min_penetration = float(self.profile.noise_param("min_penetration_m", 0.002))
        if clash.is_hard and clash.penetration_m < min_penetration:
            return "below_tolerance"

        return None

    def _is_same_system_fitting(self, clash: Clash) -> bool:
        """A run against its own connected fitting is modelling, not a clash."""
        if not self.profile.noise_param("drop_same_system_fittings", True):
            return False
        if clash.a.discipline != clash.b.discipline:
            return False

        system_a = clash.a.prop("System Name", "System Type", "Sistema")
        system_b = clash.b.prop("System Name", "System Type", "Sistema")
        if not system_a or system_a.strip().lower() != system_b.strip().lower():
            return False

        # Connected parts touch; they do not drive through each other. A deep
        # overlap inside one system is a real modelling error worth seeing.
        if clash.penetration_m > 0.05:
            return False

        return _is_fitting(clash.a) or _is_fitting(clash.b) or _bboxes_touch(clash.a, clash.b)

    def _is_insulation_graze(self, clash: Clash) -> bool:
        tolerance = float(self.profile.noise_param("insulation_penetration_tolerance_m", 0.03))
        involved = [
            side for side in (clash.a, clash.b) if side.category.strip().lower() in self._insulation
        ]
        if not involved:
            return False
        return clash.penetration_m <= tolerance

    def _is_flush_mounted(self, clash: Clash) -> bool:
        """A device sitting inside the host it is mounted on.

        These produce textbook-perfect systematic patterns — hundreds of
        receptacles all exactly 100 mm into their walls — which a root-cause
        detector will confidently report as a whole system set at the wrong
        elevation. It is the opposite: the model is right and the test is
        asking the wrong question.

        Bounded by embedment depth on purpose. A switch 114 mm into a wall is
        how switches work; a panel half a metre in is a real problem.
        """
        spec = self.profile.noise_param("flush_mounted", {}) or {}
        devices = {c.strip().lower() for c in spec.get("device_categories", [])}
        hosts = {c.strip().lower() for c in spec.get("host_categories", [])}
        if not devices or not hosts:
            return False

        limit = float(spec.get("max_embedment_m", 0.15))
        if clash.penetration_m > limit:
            return False

        for device, host in ((clash.a, clash.b), (clash.b, clash.a)):
            if (
                device.category.strip().lower() in devices
                and host.category.strip().lower() in hosts
            ):
                return True
        return False

    def _architectural_by_design(self, clash: Clash) -> str | None:
        """Architecture overlapping itself, which coordination does not fix.

        Two cases, both textbook geometric interferences and neither a
        coordination problem:

        * A door or window inside its host wall. It is hosted there — the
          overlap is the opening.
        * A partition that runs to the slab meeting the ceiling hung beneath
          it. On site the ceiling dies against the wall; in the model they
          share a sliver.

        These are real information about the architectural model, so they are
        counted and reported — just not ranked alongside a pipe driving
        through a beam, which is what a coordinator is actually paid to catch.
        """
        spec = self.profile.noise_param("architectural_by_design", {}) or {}
        if not spec:
            return None

        hosted = {c.strip().lower() for c in spec.get("hosted_categories", [])}
        hosted_in = {c.strip().lower() for c in spec.get("hosted_in", [])}
        host_to_host = {c.strip().lower() for c in spec.get("host_to_host_categories", [])}

        cat_a = clash.a.category.strip().lower()
        cat_b = clash.b.category.strip().lower()

        for child, host in ((cat_a, cat_b), (cat_b, cat_a)):
            if child in hosted and host in hosted_in:
                return "architectural_hosting"

        if (
            cat_a in host_to_host
            and cat_b in host_to_host
            and clash.a.discipline == clash.b.discipline
        ):
            return "architectural_self_overlap"

        return None

    def _is_designed_pass_through(self, clash: Clash) -> bool:
        """A sleeve or shaft doing its job is the opposite of a problem."""
        return any(self.is_penetration_element(side) for side in (clash.a, clash.b))


def _is_fitting(element: ElementRef) -> bool:
    category = element.category.strip().lower()
    return "fitting" in category or "accessor" in category or "accesorio" in category


def _bboxes_touch(a: ElementRef, b: ElementRef, slack: float = 0.02) -> bool:
    """True when the boxes barely overlap on every axis — i.e. they abut."""
    for axis in range(3):
        overlap = min(a.bbox_max[axis], b.bbox_max[axis]) - max(a.bbox_min[axis], b.bbox_min[axis])
        if overlap < -slack:
            return False
        if overlap > 0.15:
            return False
    return True


def fingerprint(clash: Clash) -> str:
    """Stable identity of a clash across reruns.

    Navisworks reassigns clash GUIDs when a test is rebuilt, so approvals
    would evaporate every run if we keyed on those. Keying on the colliding
    pair plus a coarse location survives a rerun while still separating two
    genuinely different crossings of the same two elements.
    """
    left, right = clash.pair_key
    cell = tuple(round(coord, 1) for coord in clash.point)
    return f"{left}~{right}@{cell[0]},{cell[1]},{cell[2]}"
