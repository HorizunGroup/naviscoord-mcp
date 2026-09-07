"""Severity scoring: deciding what matters most, and saying why.

The score is a weighted sum of six bounded components, each in 0..1, scaled
to 0..100. It is deliberately arithmetic rather than learned: a coordinator
has to be able to disagree with a ranking and change it, which means every
number needs a sentence attached and every weight needs to live in the
profile.

The components, and the reasoning behind each:

* **penetration** — depth relative to the size of the element being bitten.
  30 mm into a 1200 mm duct is cosmetic; 30 mm into a 50 mm sprinkler branch
  severs it.
* **criticality** — how bad this pair of disciplines meeting is at all,
  from the profile matrix.
* **immovability** — the real cost of the fix. Structure does not move, so a
  beam-versus-pipe is expensive even when shallow.
* **cluster_size** — how many raw clashes collapsed into this issue. Fifty
  crossings in one place is a systemic error and outranks a lone hard hit.
* **congestion** — how many disciplines are packed into the surrounding
  volume. Dense zones are where coordination actually fails.
* **clearance** — maintenance and access violations, which are invisible to
  a hard-clash run but block commissioning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..model import Clash, ElementRef
from ..profile import Profile
from .cluster import ClashCluster


@dataclass(slots=True)
class SeverityVerdict:
    score: float
    breakdown: dict[str, float]
    why: list[str]
    responsible: str
    immovable_side: str


class SeverityScorer:
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self._cluster_saturation = max(
            1.000001, profile.severity_param("cluster_size_saturation", 25.0)
        )
        self._congestion_saturation = max(
            1.0, profile.severity_param("congestion_saturation", 4.0)
        )
        self._penetration_cap = max(
            0.01, profile.severity_param("penetration_ratio_cap", 1.0)
        )

    def score(self, cluster: ClashCluster, congestion: int = 0) -> SeverityVerdict:
        representative = _worst_clash(cluster)
        a, b = representative.a, representative.b

        move_a = self._effective_movability(a)
        move_b = self._effective_movability(b)
        if move_a <= move_b:
            immovable, movable = a, b
        else:
            immovable, movable = b, a

        components = {
            "penetration": self._penetration(cluster, representative),
            "criticality": self.profile.criticality(a.discipline, b.discipline),
            "immovability": 1.0 - min(move_a, move_b),
            "cluster_size": self._cluster_size(cluster),
            "congestion": min(congestion / self._congestion_saturation, 1.0),
            "clearance": self._clearance(cluster),
        }

        total = 0.0
        breakdown: dict[str, float] = {}
        for name, value in components.items():
            weight = self.profile.weight(name)
            contribution = weight * value * 100.0
            breakdown[name] = contribution
            total += contribution

        total = max(0.0, min(100.0, total))
        why = self._explain(cluster, components, immovable, movable, congestion)

        # Depth can carry an issue on its own.
        #
        # Six of the components reward repetition and company — cluster size,
        # congestion — so a lone crossing has to win on the other four, and it
        # cannot: measured on a real model, twenty-six issues sat in the bottom
        # band with over 150 mm of penetration into structure, one of them at
        # 194 mm. A partition nineteen centimetres inside a beam is not a minor
        # item because it only happens once; it is one decision that has to be
        # taken before that beam is poured.
        #
        # So the floor is absolute and it only applies against a side that
        # cannot move. Against something reroutable, depth is a nuisance; into
        # structure, it is a request to the engineer.
        deep = float(self.profile.severity_param("deep_interference_m", 0.15))
        if deep > 0.0 and cluster.max_penetration >= deep:
            if self._effective_movability(immovable) <= 0.2:
                bands = self.profile.section("severity").get("priority_bands") or {}
                minimum = float(bands.get("high", 71.0))
                if total < minimum:
                    total = minimum
                    why.append(
                        f"Entra {cluster.max_penetration * 1000:.0f} mm en "
                        f"{self.profile.label(immovable.discipline).lower()}, que no se "
                        "mueve. Aunque ocurra una sola vez, esa profundidad ya no se "
                        "resuelve en obra: necesita decisión antes de vaciar."
                    )

        return SeverityVerdict(
            score=total,
            breakdown=breakdown,
            why=why,
            responsible=movable.discipline,
            immovable_side=immovable.discipline,
        )

    # ---------------------------------------------------------- components

    def _penetration(self, cluster: ClashCluster, worst: Clash) -> float:
        """Depth normalised by the smaller of the two elements."""
        depth = cluster.max_penetration
        if depth <= 0.0:
            return 0.0
        reference = min(
            (size for size in (worst.a.nominal_size, worst.b.nominal_size) if size > 0.0),
            default=0.0,
        )
        if reference <= 0.0:
            # No geometry to normalise against; fall back to absolute depth
            # where 100 mm counts as a full hit.
            return min(depth / 0.1, 1.0)
        return min(depth / reference / self._penetration_cap, 1.0)

    def _cluster_size(self, cluster: ClashCluster) -> float:
        """Log-scaled so 5 clashes registers strongly and 500 does not swamp."""
        count = len(cluster.clashes)
        if count <= 1:
            return 0.0
        return min(math.log(count) / math.log(self._cluster_saturation), 1.0)

    def _clearance(self, cluster: ClashCluster) -> float:
        """Access and maintenance violations, weighted by how tight they are."""
        soft = [c for c in cluster.clashes if not c.is_hard]
        if not soft:
            return 0.0
        share = len(soft) / len(cluster.clashes)
        equipment = any(
            _needs_access(side) for c in soft for side in (c.a, c.b)
        )
        return min(share * (1.0 if equipment else 0.5), 1.0)

    def _effective_movability(self, element: ElementRef) -> float:
        base = self.profile.movability(element.discipline, element.category)
        if self.profile.is_gravity(element.discipline):
            # Gravity systems carry a fixed slope: the pipe can be moved on
            # paper, but not without redesigning the run it belongs to.
            base *= 0.4
        if element.is_vertical and element.discipline not in {"EST", "ARQ"}:
            # Risers are shared by every floor above and below.
            base *= 0.6
        return max(0.0, min(1.0, base))

    # ------------------------------------------------------------ wording

    def _explain(
        self,
        cluster: ClashCluster,
        components: dict[str, float],
        immovable: ElementRef,
        movable: ElementRef,
        congestion: int,
    ) -> list[str]:
        why: list[str] = []
        profile = self.profile
        depth_mm = cluster.max_penetration * 1000.0
        count = len(cluster.clashes)

        if count > 1:
            why.append(
                f"{count} cruces agrupados en un radio de {cluster.span:.1f} m: "
                "es un solo problema, no {count} tareas.".replace("{count}", str(count))
            )

        immovable_name = profile.describe_element(immovable)
        movable_name = profile.describe_element(movable)

        if depth_mm >= 1.0:
            why.append(
                f"{movable_name} entra {depth_mm:.0f} mm dentro de {immovable_name}: "
                "no es un roce, los dos ocupan el mismo espacio."
            )
        elif components["clearance"] > 0.0:
            why.append(
                "No se tocan, pero no queda espacio para operar ni mantener. Un test de choque "
                "duro no lo ve, y en obra aparece el día que hay que abrir la válvula."
            )

        if any(code in {"", "OTRO"} for code in cluster.discipline_pair):
            why.append("La clasificación está incompleta: la puntuación es provisional y no determina "
                       "qué elemento puede moverse ni el coste relativo del ajuste.")
            return [_es(reason) for reason in why]

        # The consequence, said the way a site coordinator would say it.
        if immovable.discipline == "EST":
            why.append(
                "Del lado duro hay estructura. No se toca en obra: o se rerutea "
                f"{movable_name}, o hay que pedir paso formal al calculista antes de vaciar."
            )
        elif immovable.discipline == "ARQ":
            why.append(
                f"El muro o cielo define el espacio; {movable_name} tiene que caber dentro. "
                "Correr la arquitectura para acomodar una instalación arrastra acabados, "
                "puertas y áreas útiles."
            )
        elif components["immovability"] >= 0.6:
            why.append(
                f"{immovable_name} tiene poco margen: el ajuste sale más barato por el lado de "
                f"{profile.label(movable.discipline)}."
            )

        if profile.is_gravity(movable.discipline):
            why.append(
                "Ojo: el lado que debe moverse va por gravedad. Bajarlo o correrlo cambia la "
                "pendiente y arrastra todo el tramo aguas abajo — no es un ajuste local."
            )

        if congestion >= self._congestion_saturation:
            why.append(
                f"Hay {congestion} disciplinas conviviendo en "
                f"{profile.severity_param('congestion_radius_m', 2.5):.1f} m. Mover una empuja a "
                "la siguiente: esto se resuelve en mesa, no por correo."
            )

        if len(cluster.clashes) >= 10:
            why.append(
                f"Son {len(cluster.clashes)} choques en el mismo punto. Eso ya no es un ajuste "
                "de detalle: es un tramo mal resuelto."
            )

        if not why:
            why.append(
                "Cruce aislado y superficial, entre elementos que se acomodan sin arrastrar a nadie."
            )
        return [_es(reason) for reason in why]


def _es(text: str) -> str:
    """Spanish contractions over assembled phrases.

    Element names are nouns with their article ("el muro", "la viga"), and
    templating them after a preposition produces "dentro de el muro" — the
    one mistake no native speaker makes, so it reads as machine output in a
    client-facing report. Contracting after assembly keeps every template
    oblivious to which article the noun carries.
    """
    return text.replace(" de el ", " del ").replace(" a el ", " al ")


def _worst_clash(cluster: ClashCluster) -> Clash:
    """The deepest hit represents the cluster; ties break on hard over soft."""
    return max(cluster.clashes, key=lambda c: (c.penetration_m, c.is_hard))


def _needs_access(element: ElementRef) -> bool:
    haystack = f"{element.category} {element.display_name}".lower()
    return any(
        token in haystack
        for token in (
            "equipment", "equipo", "valve", "válvula", "valvula", "damper",
            "panel", "tablero", "filter", "filtro", "pump", "bomba", "vav",
            "access", "registro", "compuerta",
        )
    )


def suggest_action(
    cluster: ClashCluster, verdict: SeverityVerdict, profile: Profile
) -> str:
    """One concrete next step, phrased for the person who has to do it."""
    if any(code in {"", "OTRO"} for code in cluster.discipline_pair):
        return _es("Identifica las disciplinas y el responsable de ambos elementos en el perfil, "
                   "y repite el análisis antes de decidir qué elemento mover. "
                   "La geometría por sí sola no establece esa responsabilidad.")
    worst = _worst_clash(cluster)
    movable_side = worst.side_for(verdict.responsible)
    immovable_side = worst.side_for(verdict.immovable_side)
    movable_name = (
        profile.describe_element(movable_side) if movable_side else profile.label(verdict.responsible)
    )
    immovable_name = (
        profile.describe_element(immovable_side)
        if immovable_side
        else profile.label(verdict.immovable_side)
    )
    depth_mm = cluster.max_penetration * 1000.0

    if len(cluster.clashes) >= 5:
        return _es(
            f"No corrijas los {len(cluster.clashes)} choques uno por uno: replantea el tramo de "
            f"{movable_name} completo. Que choque {len(cluster.clashes)} veces contra "
            f"{immovable_name} en el mismo sitio dice que la ruta o la cota del tramo están mal, "
            "y ajustar choque por choque los vuelve a abrir río abajo."
        )
    if depth_mm <= 0.0:
        return _es(
            f"Verifica en sitio que quede espacio para operar y mantener {immovable_name}: "
            f"corre {movable_name} lo necesario para dejar acceso de mano y herramienta. "
            "Este se cobra en puesta en marcha, no en obra gris."
        )
    if verdict.immovable_side == "EST":
        return _es(
            f"Rerutea {movable_name}. Si no hay por dónde, pide paso formal al calculista ANTES "
            f"de vaciar — {depth_mm:.0f} mm dentro de {immovable_name} no se resuelven con "
            "martillo después, y un paso en viga tiene reglas de posición y diámetro."
        )
    if verdict.immovable_side == "ARQ":
        return _es(
            f"Acomoda {movable_name} dentro del espacio que da {immovable_name} "
            f"({depth_mm:.0f} mm de invasión). Si de verdad no cabe, súbelo a mesa con "
            "arquitectura antes de mover el muro: eso arrastra acabados y área útil."
        )
    return _es(
        f"Ajusta {movable_name}: invade {depth_mm:.0f} mm a {immovable_name}. "
        f"Confirma con {profile.label(verdict.immovable_side)} antes de mover, para no "
        "trasladarle el problema."
    )
