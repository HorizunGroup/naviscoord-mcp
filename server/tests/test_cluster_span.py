"""``max_cluster_span_m`` as a postcondition, not an aspiration.

The reproduction: 21 clashes spaced 0.35 m apart form a chain 7 m long. The
profile says ``max_cluster_span_m: 6.0``. The old ``_split_oversized`` asked
DBSCAN for a tighter re-clustering and then returned early when it produced a
single part — ``or len(parts) == 1`` — without ever checking whether that single
part was still too long. A chain of near-touching points is *always* one
connected component at any epsilon larger than the spacing, so the early return
fired every time and the 7 m cluster survived.

What that costs downstream: an "issue" is supposed to be something a
coordinator can stand in front of. A 7 m one is a corridor, and its centroid —
which drives the camera, the level attribution and the responsible-trade
heuristic — sits in the middle of a run where there may be no clash at all.

These tests are the reproduction, kept as regressions rather than as a script
that proved the point once.
"""

from __future__ import annotations

import random

import pytest

from naviscoord.analysis.cluster import ClashCluster, build_clusters
from naviscoord.model import Clash, ElementRef
from naviscoord.profile import Profile

MAX_SPAN = 6.0
# Floating-point slack. A cluster whose span is 6.0000000001 is not a defect;
# one whose span is 7.0 is.
EPSILON = 1e-6


# ------------------------------------------------------------------ fixtures


def _element(path_id: str, discipline: str, category: str = "Pipes") -> ElementRef:
    ref = ElementRef(
        path_id=path_id,
        display_name=f"{category} {path_id}",
        category=category,
        bbox_min=(0.0, 0.0, 0.0),
        bbox_max=(0.1, 0.1, 0.1),
    )
    ref.discipline = discipline
    return ref


def _clash(
    guid: str,
    point: tuple[float, float, float],
    *,
    pair: tuple[str, str] = ("EST", "HID"),
    size: float = 0.1,
) -> Clash:
    """One clash at a point, with a bounding box of its own around it."""
    a = _element(f"{guid}/a", pair[0], "Structural Framing")
    b = _element(f"{guid}/b", pair[1], "Pipes")
    for ref in (a, b):
        ref.bbox_min = (point[0], point[1], point[2])
        ref.bbox_max = (point[0] + size, point[1] + size, point[2] + size)
    return Clash(
        guid=guid, test="T", name=guid, a=a, b=b,
        distance_m=-0.05, point=point, level="Nivel 01",
    )


def _profile(**overrides) -> Profile:
    profile = Profile.load()
    clustering = dict(profile.raw.get("clustering") or {})
    clustering.setdefault("eps_m", 0.4)
    clustering.setdefault("min_samples", 1)
    clustering["max_cluster_span_m"] = MAX_SPAN
    clustering.update(overrides)
    raw = dict(profile.raw)
    raw["clustering"] = clustering
    return Profile.from_json(raw)


def _chain(
    count: int, spacing: float, *, start: float = 0.0, prefix: str = "g"
) -> list[Clash]:
    """A line of clashes, each between a DIFFERENT element pair.

    The prefix matters: `_collapse_by_pair` groups by element identity first,
    so reusing path ids would make the whole line one problem by identity and
    the spatial rules would never be reached.
    """
    return [
        _clash(f"{prefix}{i:03d}", (start + i * spacing, 0.0, 0.0))
        for i in range(count)
    ]


def _spans(clusters: list[ClashCluster]) -> list[float]:
    return [c.span for c in clusters]


def _assert_postcondition(clusters: list[ClashCluster], original: list[Clash]) -> None:
    """The whole contract, asserted in one place.

    Every test in this file ends here, because "the span is bounded" is worth
    nothing if the bound was reached by dropping clashes.
    """
    for cluster in clusters:
        assert cluster.span <= MAX_SPAN + EPSILON, (
            f"un clúster mide {cluster.span:.3f} m y el máximo es {MAX_SPAN}"
        )

    seen = [c.guid for cluster in clusters for c in cluster.clashes]
    assert len(seen) == len(set(seen)), "ningún choque puede aparecer dos veces"
    assert set(seen) == {c.guid for c in original}, "ningún choque puede perderse"

    for cluster in clusters:
        pairs = {c.discipline_pair for c in cluster.clashes}
        assert len(pairs) == 1, "un clúster mezcla pares de disciplina"


# ------------------------------------------------------- the reproduction


class TestTheReproduction:
    def test_twenty_one_clashes_at_035_do_not_span_seven_metres(self) -> None:
        """The exact case from the brief. Fails on the previous implementation."""
        clashes = _chain(21, 0.35)
        raw_span = ClashCluster(clashes=clashes).span
        assert raw_span > MAX_SPAN, "la reproducción debe empezar por encima del máximo"
        assert raw_span == pytest.approx(7.0, abs=0.2), f"span de partida {raw_span:.2f} m"

        clusters = build_clusters(clashes, _profile())
        assert clusters, "algo tiene que salir"
        _assert_postcondition(clusters, clashes)

    def test_a_hundred_link_chain_is_split(self) -> None:
        clashes = _chain(100, 0.35)
        clusters = build_clusters(clashes, _profile())
        _assert_postcondition(clusters, clashes)
        assert len(clusters) > 1, "una cadena de 35 m no puede ser un solo problema"


# --------------------------------------------------------------- geometry


class TestSpatialCases:
    def test_two_separated_conglomerates_stay_separate(self) -> None:
        near = _chain(5, 0.3, start=0.0, prefix="cerca")
        far = _chain(5, 0.3, start=200.0, prefix="lejos")
        clashes = near + far
        clusters = build_clusters(clashes, _profile())
        _assert_postcondition(clusters, clashes)

        centres = sorted(c.centroid[0] for c in clusters)
        assert centres[0] < 100 < centres[-1], "dos zonas lejanas no se fusionan"

    def test_a_singleton_survives(self) -> None:
        clashes = [_clash("solo", (1.0, 2.0, 3.0))]
        clusters = build_clusters(clashes, _profile())
        _assert_postcondition(clusters, clashes)
        assert len(clusters) == 1

    def test_one_clash_with_enormous_geometry_is_a_singleton(self) -> None:
        """A 12 m duct is one clash, and one clash cannot be subdivided.

        Worth stating precisely, because the two sources of span are not the
        same thing and only one of them is a defect: `span` is measured across
        clash POINTS, so a single clash spans zero however large the elements
        are. The size shows up in the cluster's bounding box instead. There is
        nothing here to split — splitting a set of one returns the same set —
        so the requirement is termination, not subdivision.
        """
        clashes = [_clash("enorme", (0.0, 0.0, 0.0), size=12.0)]
        clusters = build_clusters(clashes, _profile())

        assert len(clusters) == 1, "un solo choque es un solo problema"
        assert clusters[0].clashes[0].guid == "enorme"
        assert clusters[0].span == 0.0, "el span mide separación entre puntos, no geometría"
        _assert_postcondition(clusters, clashes)

    def test_the_same_element_pair_far_apart_is_still_bounded(self) -> None:
        """Identity collapses first, and that path skipped the span check.

        `_collapse_by_pair` treats two clashes between the same element pair as
        one problem, which is right — a duct crossing a wall twice is one
        coordination item. But the branch that does it appended the cluster
        without ever measuring it, so a long duct clipping the same wall 40 m
        apart produced a 40 m "issue" whose centroid was in open air.
        """
        a = _element("duct/1", "HVAC", "Ducts")
        b = _element("wall/1", "ARQ", "Walls")
        clashes = []
        for i, x in enumerate((0.0, 40.0)):
            clash = Clash(
                guid=f"same{i}", test="T", name=f"same{i}", a=a, b=b,
                distance_m=-0.05, point=(x, 0.0, 0.0), level="Nivel 01",
            )
            clashes.append(clash)

        clusters = build_clusters(clashes, _profile())
        _assert_postcondition(clusters, clashes)

    def test_identical_points_collapse_without_dividing_by_zero(self) -> None:
        clashes = [_clash(f"g{i}", (5.0, 5.0, 5.0)) for i in range(30)]
        clusters = build_clusters(clashes, _profile())
        _assert_postcondition(clusters, clashes)
        assert len(clusters) == 1, "treinta choques en el mismo punto son un problema"

    def test_negative_coordinates(self) -> None:
        clashes = [_clash(f"g{i:03d}", (-20.0 + i * 0.35, -8.0, -3.0)) for i in range(21)]
        clusters = build_clusters(clashes, _profile())
        _assert_postcondition(clusters, clashes)

    @pytest.mark.parametrize(
        "spacing,count",
        [
            (MAX_SPAN / 20, 21),      # exactly at the boundary
            (MAX_SPAN / 20 + 1e-9, 21),  # a hair past it
            (1.0, 21),                # far past it
        ],
    )
    def test_the_boundary_and_just_past_it(self, spacing: float, count: int) -> None:
        clashes = _chain(count, spacing)
        clusters = build_clusters(clashes, _profile())
        _assert_postcondition(clusters, clashes)


# ------------------------------------------------------------- determinism


class TestDeterminism:
    def _signature(self, clusters: list[ClashCluster]) -> list[tuple[str, ...]]:
        return sorted(tuple(sorted(c.guid for c in cl.clashes)) for cl in clusters)

    def test_reversed_input_produces_the_same_grouping(self) -> None:
        clashes = _chain(21, 0.35)
        forward = build_clusters(list(clashes), _profile())
        backward = build_clusters(list(reversed(clashes)), _profile())
        assert self._signature(forward) == self._signature(backward)

    def test_a_hundred_shuffles_agree(self) -> None:
        clashes = _chain(21, 0.35)
        expected = self._signature(build_clusters(list(clashes), _profile()))

        rng = random.Random(20260819)
        for _ in range(100):
            shuffled = list(clashes)
            rng.shuffle(shuffled)
            assert self._signature(build_clusters(shuffled, _profile())) == expected

    def test_discipline_pair_order_does_not_matter(self) -> None:
        forward = _chain(10, 0.35)
        reverse = [
            _clash(f"g{i:03d}", (i * 0.35, 0.0, 0.0), pair=("HID", "EST"))
            for i in range(10)
        ]
        assert (
            self._signature(build_clusters(forward, _profile()))
            == self._signature(build_clusters(reverse, _profile()))
        ), "A×B y B×A agrupan igual: discipline_pair está ordenado"


# ----------------------------------------------------------------- params


class TestClusteringParameters:
    def test_min_samples_one(self) -> None:
        clashes = _chain(21, 0.35)
        clusters = build_clusters(clashes, _profile(min_samples=1))
        _assert_postcondition(clusters, clashes)

    def test_min_samples_larger_than_the_group(self) -> None:
        clashes = _chain(5, 0.35)
        clusters = build_clusters(clashes, _profile(min_samples=50))
        _assert_postcondition(clusters, clashes)

    def test_a_tiny_epsilon_still_terminates(self) -> None:
        clashes = _chain(21, 0.35)
        clusters = build_clusters(clashes, _profile(eps_m=0.001))
        _assert_postcondition(clusters, clashes)
        assert len(clusters) == 21, "con eps minúsculo nada se agrupa"

    def test_an_epsilon_near_the_span(self) -> None:
        clashes = _chain(21, 0.35)
        clusters = build_clusters(clashes, _profile(eps_m=MAX_SPAN))
        _assert_postcondition(clusters, clashes)


# -------------------------------------------------------------- benchmark


class TestScale:
    """A regression guard against turning the engine quadratic.

    Not a wall-clock threshold — that only measures the machine. What it
    measures is the SHAPE: if the cost per clash grows with n, the ratio
    between successive sizes climbs. A linearithmic implementation keeps it
    roughly flat; a quadratic one multiplies it by ten each time.
    """

    @pytest.mark.parametrize("count", [1000, 10000])
    def test_large_inputs_stay_correct(self, count: int) -> None:
        rng = random.Random(count)
        clashes = [
            _clash(
                f"g{i:06d}",
                (rng.uniform(0, 300), rng.uniform(0, 120), rng.uniform(0, 40)),
            )
            for i in range(count)
        ]
        clusters = build_clusters(clashes, _profile())
        _assert_postcondition(clusters, clashes)

    def test_the_cost_does_not_grow_quadratically(self) -> None:
        import time

        def elapsed(count: int) -> float:
            rng = random.Random(7)
            clashes = [
                _clash(
                    f"g{i:06d}",
                    (rng.uniform(0, 300), rng.uniform(0, 120), rng.uniform(0, 40)),
                )
                for i in range(count)
            ]
            profile = _profile()
            start = time.perf_counter()
            build_clusters(clashes, profile)
            return time.perf_counter() - start

        small = elapsed(2000)
        large = elapsed(20000)
        # Ten times the input. Linearithmic gives roughly 10-13x; quadratic
        # gives about 100x. The bar is deliberately loose — it is there to
        # catch a change of complexity class, not to police a few percent.
        ratio = large / max(small, 1e-6)
        assert ratio < 40, f"el coste creció {ratio:.1f}× al multiplicar la entrada por 10"
