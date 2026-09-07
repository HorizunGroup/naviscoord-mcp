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
from .relations import hosted_by, designed_contact


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

    # Per-test tallies, because a share of the whole run hides exactly the
    # failure worth catching. A rule that eats one entire test is a serious
    # fault — that pair of trades now reports clean on no evidence — but if
    # the run holds thirteen tests it is a seventh of the total and stays
    # under any global threshold. Measured per test, it is 100% and screams.
    input_by_test: Counter[str] = field(default_factory=Counter)
    kept_by_test: Counter[str] = field(default_factory=Counter)
    reasons_by_test: dict[str, Counter[str]] = field(default_factory=dict)

    # Surviving clashes whose side is a catch-all category. Naming the trade
    # from the clash test tells us WHO owns the element; it says nothing about
    # WHAT the element is, and every noise rule keys off what it is. These
    # cannot be judged either way — not filtered, because a duct filed under
    # Generic Models grazing a wall is a real hit; not reasoned about, because
    # nothing knows it is a facade panel. So they are declared.
    unnameable_kept: Counter[str] = field(default_factory=Counter)

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

        unnameable = sum(self.unnameable_kept.values())
        if unnameable and self.kept_count:
            share = unnameable / self.kept_count
            if share >= 0.10:
                worst = ", ".join(
                    f"«{name}»" for name, _ in self.unnameable_kept.most_common(3)
                )
                out.append(
                    f"{share:.0%} de los cruces que quedan ({unnameable}) tienen un lado "
                    f"en una categoría genérica ({worst}). Saber de qué especialidad es "
                    "no basta: las reglas de ruido deciden por lo que el elemento ES, y "
                    "de estos no se sabe. Ni se filtran ni se pueden explicar bien; "
                    "nombrarlos en el modelo de origen es lo que los vuelve legibles."
                )

        out.extend(self._per_test_warnings())
        return out

    def _per_test_warnings(self) -> list[str]:
        """Tests the filter emptied, reported one by one.

        A test reduced to nothing is never good news. Either the pair really
        has no interference — in which case the coordinator wants to know
        that explicitly, not infer it from an absence — or a rule is eating
        work, and the report is about to claim that pair of trades is clear
        on the strength of zero surviving evidence.
        """
        out: list[str] = []
        for test, total in sorted(
            self.input_by_test.items(), key=lambda kv: kv[1], reverse=True
        ):
            if total <= 0:
                continue
            kept = self.kept_by_test.get(test, 0)
            share_dropped = (total - kept) / total
            # Emptied entirely, or so nearly that what survives cannot speak
            # for the pair. Only the exact-zero case used to warn, so a test
            # that went from 477 clashes to 30 — 94% of a structure-versus-
            # architecture comparison gone — passed without a word.
            if share_dropped < 0.90:
                continue
            reasons = self.reasons_by_test.get(test, Counter())
            if len(reasons) == 1:
                reason, _ = reasons.most_common(1)[0]
                cause = f"todos por '{reason}'"
            else:
                spread = ", ".join(f"{r} ({c})" for r, c in reasons.most_common(3))
                cause = f"por {spread}"
            if kept:
                out.append(
                    f"El test «{test}» aportó {total} cruces y el filtro descartó "
                    f"{share_dropped:.0%} ({cause}); quedan {kept}. Con tan poco "
                    "sobreviviente, ese par de especialidades se está juzgando por "
                    "un resto: confirma la regla antes de leer el conteo."
                )
            else:
                out.append(
                    f"El test «{test}» aportó {total} cruces y el filtro los descartó "
                    f"todos ({cause}). Ese par de especialidades queda reportado como "
                    "limpio sin una sola evidencia que lo sostenga: confirma la regla "
                    "antes de darlo por resuelto."
                )
        return out

    def to_json(self) -> dict[str, object]:
        return {
            "input": self.kept_count + self.dropped_count,
            "kept": self.kept_count,
            "dropped": self.dropped_count,
            "dropped_by_reason": dict(self.reasons.most_common()),
            # Named explicitly rather than left to be inferred from a zero.
            "emptied_tests": [
                {
                    "test": test,
                    "input": count,
                    "dropped_by_reason": dict(
                        self.reasons_by_test.get(test, Counter()).most_common()
                    ),
                }
                for test, count in self.input_by_test.most_common()
                if count > 0 and self.kept_by_test.get(test, 0) == 0
            ],
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
                "designed_adjacency": "dos elementos anfitriones de especialidades distintas que se tocan por el espesor de un acabado: el tabique muere contra el muro, no lo atraviesa",
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
        # Categories that describe nothing. "Generic Models" is where Revit
        # puts whatever did not fit anywhere else: a sleeve, yes, but equally
        # a facade panel or an in-place stair. Treated as proof of a sleeve it
        # produced a textbook false positive — on the model this was found
        # on, all 255 "sleeves" were cladding, 246 of them named after a paint
        # colour — which both emptied a 700-clash test as "passes through a
        # sleeve by design" and supplied the headline root cause its evidence
        # that the project models sleeves properly elsewhere.
        #
        # They stay eligible, but they have to earn it on the name.
        self._ambiguous_pass_categories = {
            c.strip().lower()
            for c in profile.noise_param("ambiguous_pass_through_categories", [])
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

            test = clash.test or "(sin test)"
            result.input_by_test[test] += 1

            reason = self._reject_reason(clash)
            if reason:
                result.dropped.append((clash, reason))
                result.reasons[reason] += 1
                result.reasons_by_test.setdefault(test, Counter())[reason] += 1
            else:
                result.kept.append(clash)
                result.kept_by_test[test] += 1
                for side in (clash.a, clash.b):
                    category = side.category.strip()
                    if category.lower() in self._ambiguous_pass_categories:
                        result.unnameable_kept[category] += 1
                        break
        return result

    def is_penetration_element(self, element: ElementRef) -> bool:
        """A sleeve, shaft or modelled opening.

        A purpose-built category (Shafts, Sleeves, Openings) is enough on its
        own — nobody files a facade panel under Sleeves. A catch-all category
        is not, and must be corroborated by the element's name.
        """
        category = element.category.strip().lower()
        ambiguous = category in self._ambiguous_pass_categories
        if category in self._pass_categories and not ambiguous:
            return True
        return any(keyword in _identity(element) for keyword in self._pass_keywords)

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

        if self._is_designed_adjacency(clash):
            return "designed_adjacency"

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
                and hosted_by(device, host)
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

        same_trade = clash.a.discipline == clash.b.discipline

        for child, host in ((cat_a, cat_b), (cat_b, cat_a)):
            if child in hosted and host in hosted_in:
                # Only when the wall really IS the host. A window inside a
                # STRUCTURAL wall is not hosted there — it is an opening
                # through structure, which needs a lintel and, in a shear
                # wall, may not be permissible at all. Skipping the trade
                # check discarded exactly those: on the reference model, 28
                # windows between 100 and 170 mm into structure, silently.
                if same_trade and (hosted_by(clash.a, clash.b) or hosted_by(clash.b, clash.a)):
                    return "architectural_hosting"
                break

        if (cat_a in host_to_host and cat_b in host_to_host and same_trade
                and clash.a.discipline == "ARQ"
                and designed_contact(clash.a, clash.b)):
            return "architectural_self_overlap"

        return None

    def _is_designed_adjacency(self, clash: Clash) -> bool:
        if not designed_contact(clash.a, clash.b):
            return False
        """Two host elements of different trades meeting, as buildings do.

        A gypsum partition dies against a concrete wall; a window frame sits
        in its opening. In the model they overlap by the thickness of the
        finish, and no one on site acts on it.

        Bounded by DEPTH, not by the pair of categories, because depth is the
        only thing that separates the convention from the defect. On the
        reference model the partition-to-wall test had a median of 12.4 mm —
        one sheet of plasterboard — and a maximum of 51 mm, while the same
        partitions against beams reached 247 mm. The first is how the wall
        was drawn; the second means the partition runs through structure.
        The default cut is 25 mm: two layers of board, one finish build-up.

        Bounding-box volume overlap looks like the natural measure here and
        is useless: two plates meeting at an angle share a sliver of volume
        whether they touch or interpenetrate deeply.
        """
        spec = self.profile.noise_param("designed_adjacency", {}) or {}
        if not spec:
            return False
        if clash.a.discipline == clash.b.discipline:
            return False  # same trade is already covered above

        if not self._is_building_surface(clash.a, spec):
            return False
        if not self._is_building_surface(clash.b, spec):
            return False

        limit = float(spec.get("max_contact_m", 0.025))
        return clash.penetration_m <= limit

    def _is_building_surface(self, element: ElementRef, spec: dict) -> bool:
        """A wall, floor, ceiling, window — anything a room is made of.

        Category answers this for almost everything. It cannot answer it for
        the catch-all, and the catch-all is where a surprising amount of a
        building lives: on the reference model, 605 window blinds filed under
        `Generic Models` grazed the concrete wall they hang on by a median of
        6 mm, survived every rule, and became 45% of the surviving clashes and
        69% of the work assigned to architecture.

        So a catch-all element may identify itself by name. That is the same
        concession the pass-through rule makes, and it is safe for the same
        reason plus one: this rule already requires both sides to be surfaces,
        the two to belong to different trades, and the contact to be within a
        finish thickness. A duct that talks its way in still has to be grazing
        a wall by under 25 mm to be dropped.
        """
        category = element.category.strip().lower()
        if category in {c.strip().lower() for c in spec.get("categories", [])}:
            return True
        ambiguous = {c.strip().lower() for c in spec.get("ambiguous_categories", [])}
        if category not in ambiguous:
            return False
        keywords = [k.strip().lower() for k in spec.get("name_keywords", []) if k.strip()]
        if not keywords:
            return False
        return any(keyword in _identity(element) for keyword in keywords)

    def _is_designed_pass_through(self, clash: Clash) -> bool:
        """A sleeve or shaft doing its job is the opposite of a problem."""
        return any(self.is_penetration_element(side) and (hosted_by(side, other) or designed_contact(side, other))
                   for side, other in ((clash.a, clash.b), (clash.b, clash.a)))


def _identity(element: ElementRef) -> str:
    """Every name the model gives an element, lowercased, in one string.

    Which of them carries the answer depends on how the export was harvested,
    and that is not something a rule should have to know. The same window
    blind came back as `Type: WIN_BLIND_7'8"_BLACKOUT` through one property
    set and `Type: Solid` through another — Navisworks has more than one
    property tab offering a field called Type, and which one wins depends on
    the harvest order. `Family` and the parent's name held the blind's real
    identity in both.

    So all of them are searched. A rule that reads only `Type` works on one
    export and silently stops working on the next, which is the failure this
    whole class of bug keeps taking.
    """
    return " ".join(
        part
        for part in (
            element.prop("Type", "Tipo"),
            element.prop("Family", "Familia"),
            element.prop("Family and Type"),
            element.display_name,
            element.parent_name,
        )
        if part
    ).lower()


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
    def stable(element):
        authoring_id = element.prop("UniqueId", "Unique Id", "IFC GUID", "Element Id")
        return f"{element.source_file.casefold()}:{authoring_id}" if authoring_id and element.source_file else element.group_key
    left, right = sorted((stable(clash.a), stable(clash.b)))
    cell = tuple(round(coord, 1) for coord in clash.point)
    return f"{left}~{right}@{cell[0]},{cell[1]},{cell[2]}"
