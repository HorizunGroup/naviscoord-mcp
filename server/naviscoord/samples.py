"""Synthetic clash exports with known, planted defects.

Shipped inside the package rather than left in the test tree: `naviscoord
demo` is the first thing anyone runs after installing, and reaching into a
sibling `tests/` directory only works for an editable install. From a wheel
that path does not exist and the demo dies on import.

Each builder plants one recognisable coordination pathology so a test can
assert that the engine finds exactly that and does not invent others. The
geometry is deliberately simple but dimensionally realistic: beams at floor
soffit height, ducts in the ceiling void, sprinklers below them.

Everything is deterministic — no randomness — so a failing test points at a
change in the engine rather than at luck.
"""

from __future__ import annotations

from typing import Any

from .model import SCHEMA

Box = tuple[tuple[float, float, float], tuple[float, float, float]]


def element(
    path_id: str,
    category: str,
    name: str,
    box: Box,
    *,
    source_file: str = "MODELO.rvt",
    parent_path_id: str = "",
    parent_name: str = "",
    model_index: int = 0,
    **props: str,
) -> dict[str, Any]:
    return {
        "path_id": path_id,
        "model_index": model_index,
        "display_name": name,
        "category": category,
        "parent_path_id": parent_path_id,
        "parent_name": parent_name,
        "source_file": source_file,
        "bbox_min": list(box[0]),
        "bbox_max": list(box[1]),
        "props": dict(props),
    }


def clash(
    guid: str,
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    point: tuple[float, float, float],
    penetration_m: float = 0.0,
    clearance_m: float | None = None,
    test: str = "EST vs MEP",
    status: str = "New",
    level: str = "N+03.50",
    grid: str = "",
    test_type: str = "Hard",
) -> dict[str, Any]:
    if clearance_m is not None:
        distance = clearance_m
        test_type = "Clearance"
    else:
        distance = -abs(penetration_m)
    return {
        "guid": guid,
        "test": test,
        "name": guid,
        "status": status,
        "distance_m": distance,
        "point": list(point),
        "grid": grid,
        "level": level,
        "test_type": test_type,
        "a": a,
        "b": b,
    }


def export(clashes: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "document": {"title": kwargs.get("title", "SINTETICO.nwf"), "path": "C:/synthetic.nwf"},
        "models": kwargs.get(
            "models",
            [
                {"index": 0, "source_file": "PROY-EST.rvt", "item_count": 1000},
                {"index": 1, "source_file": "PROY-HVAC.rvt", "item_count": 2000},
                {"index": 2, "source_file": "PROY-HID.rvt", "item_count": 1500},
                {"index": 3, "source_file": "PROY-ARQ.rvt", "item_count": 3000},
                {"index": 4, "source_file": "PROY-RCI.rvt", "item_count": 800},
            ],
        ),
        "tests": kwargs.get("tests", []),
        "clashes": clashes,
    }


# ------------------------------------------------------------- builders


def beam(index: int, x: float, *, z0: float = 3.00, z1: float = 3.60) -> dict[str, Any]:
    """A 600 mm deep beam spanning in Y at a given X."""
    return element(
        f"est/{index}",
        "Structural Framing",
        f"VIGA 40x60 - Eje {index}",
        ((x - 0.2, -1.0, z0), (x + 0.2, 25.0, z1)),
        source_file="PROY-EST.rvt",
        model_index=0,
        **{"Element Id": f"9{index:05d}", "Level": "N+03.50"},
    )


def supply_duct(
    path_id: str = "hvac/main",
    *,
    z0: float = 3.35,
    z1: float = 3.95,
    system: str = "AA-SUMINISTRO-01",
) -> dict[str, Any]:
    """The main supply run. Sits too high by default: it eats the beams."""
    return element(
        path_id,
        "Ducts",
        "Rectangular Duct 600x600",
        ((-1.0, 11.7, z0), (60.0, 12.3, z1)),
        source_file="PROY-HVAC.rvt",
        model_index=1,
        parent_path_id="hvac/system-01",
        parent_name=system,
        **{"System Name": system, "System Type": "Supply Air", "Size": "600x600", "Level": "N+03.50"},
    )


def systemic_elevation_case(beam_count: int = 10) -> dict[str, Any]:
    """One duct run buried a consistent 250 mm into every beam it crosses.

    The engine should report ~`beam_count` issues (they are metres apart, so
    they are genuinely separate crossings) but flag a single systemic
    elevation root cause covering all of them.
    """
    duct = supply_duct()
    clashes = []
    for i in range(beam_count):
        x = 6.0 * i
        # Overlap is 3.60 - 3.35 = 0.25 m, with a millimetric wobble so the
        # detector has to tolerate real-world noise rather than exact equality.
        wobble = 0.004 * ((i % 3) - 1)
        clashes.append(
            clash(
                f"sys-{i:03d}",
                beam(i, x, z1=3.60 + wobble),
                duct,
                point=(x, 12.0, 3.47),
                penetration_m=0.25 + wobble,
                test="EST vs HVAC",
                grid=f"{chr(65 + i)}-4",
            )
        )
    return export(clashes)


def multilayer_wall_case() -> dict[str, Any]:
    """A pipe through a four-layer wall: four clashes, one problem."""
    pipe = element(
        "hid/riser",
        "Pipes",
        "Tubería CPVC 2in",
        ((10.0, 4.9, 0.0), (10.2, 5.1, 12.0)),
        source_file="PROY-HID.rvt",
        model_index=2,
        parent_path_id="hid/system-agua",
        parent_name="AGUA POTABLE",
        **{"System Name": "AGUA POTABLE", "Size": "2\"", "Level": "N+03.50"},
    )
    clashes = []
    for layer in range(4):
        y = 4.85 + 0.05 * layer
        clashes.append(
            clash(
                f"wall-{layer}",
                element(
                    f"arq/wall-layer-{layer}",
                    "Walls",
                    f"Muro Bloque - Capa {layer + 1}",
                    ((0.0, y, 0.0), (30.0, y + 0.05, 3.0)),
                    source_file="PROY-ARQ.rvt",
                    model_index=3,
                    parent_path_id="arq/wall-A12",
                    parent_name="Muro Bloque 15cm",
                ),
                pipe,
                point=(10.1, y + 0.02, 1.5),
                penetration_m=0.05,
                test="ARQ vs HID",
            )
        )
    return export(clashes)


def pipe_rack_case(pipe_count: int = 8) -> dict[str, Any]:
    """A duct crossing a bank of parallel pipes at one point in space.

    Eight raw clashes, one place to stand, one decision to make.
    """
    duct = supply_duct("hvac/branch-07", z0=3.0, z1=3.5, system="AA-RETORNO-02")
    clashes = []
    for i in range(pipe_count):
        y = 11.85 + 0.08 * i
        clashes.append(
            clash(
                f"rack-{i:02d}",
                duct,
                element(
                    f"hid/pipe-{i}",
                    "Pipes",
                    f"Tubería HG 1.5in - {i}",
                    ((-1.0, y, 3.2), (60.0, y + 0.05, 3.25)),
                    source_file="PROY-HID.rvt",
                    model_index=2,
                    parent_path_id=f"hid/run-{i}",
                    parent_name="AGUA FRIA",
                    **{"System Name": "AGUA FRIA", "Size": "1.5\""},
                ),
                point=(30.0, y, 3.22),
                penetration_m=0.05,
                test="HVAC vs HID",
            )
        )
    return export(clashes)


def noise_case() -> dict[str, Any]:
    """Five hits that should all be filtered, one that should survive.

    If the filter ever starts eating the survivor, the whole engine is
    reporting a clean model that is not clean.
    """
    system = "AGUA POTABLE"
    pipe = element(
        "hid/p1",
        "Pipes",
        "Tubería CPVC 2in",
        ((0.0, 0.0, 2.0), (6.0, 0.1, 2.1)),
        source_file="PROY-HID.rvt",
        model_index=2,
        parent_path_id="hid/run-1",
        parent_name=system,
        **{"System Name": system},
    )
    fitting = element(
        "hid/f1",
        "Pipe Fittings",
        "Codo 90° CPVC 2in",
        ((5.95, 0.0, 2.0), (6.10, 0.1, 2.1)),
        source_file="PROY-HID.rvt",
        model_index=2,
        parent_path_id="hid/run-1b",
        parent_name=system,
        **{"System Name": system},
    )
    insulation = element(
        "hvac/ins1",
        "Duct Insulations",
        "Aislamiento 25mm",
        ((0.0, 5.0, 3.0), (20.0, 5.7, 3.7)),
        source_file="PROY-HVAC.rvt",
        model_index=1,
    )
    wall = element(
        "arq/w9",
        "Walls",
        "Muro Bloque 15cm",
        ((0.0, 5.6, 0.0), (20.0, 5.75, 3.5)),
        source_file="PROY-ARQ.rvt",
        model_index=3,
    )
    sleeve = element(
        "arq/sl1",
        "Generic Models",
        "PASAMURO PVC 4in",
        ((9.9, 5.5, 2.0), (10.3, 5.9, 2.4)),
        source_file="PROY-ARQ.rvt",
        model_index=3,
    )
    sprinkler = element(
        "rci/s1",
        "Sprinklers",
        "Rociador colgante 1/2in",
        ((9.9, 5.6, 2.0), (10.3, 5.8, 2.4)),
        source_file="PROY-RCI.rvt",
        model_index=4,
        **{"System Name": "RCI HUMEDA"},
    )
    column = element(
        "est/c1",
        "Structural Columns",
        "COLUMNA 40x40",
        ((14.8, 5.8, 0.0), (15.2, 6.2, 3.5)),
        source_file="PROY-EST.rvt",
        model_index=0,
    )
    duct = element(
        "hvac/d9",
        "Ducts",
        "Rectangular Duct 400x400",
        ((0.0, 5.8, 2.8), (30.0, 6.2, 3.2)),
        source_file="PROY-HVAC.rvt",
        model_index=1,
        parent_path_id="hvac/sys-9",
        parent_name="AA-SUMINISTRO-02",
        **{"System Name": "AA-SUMINISTRO-02"},
    )

    return export(
        [
            # 1. connected fitting inside one system
            clash("n-fitting", pipe, fitting, point=(6.0, 0.05, 2.05), penetration_m=0.01),
            # 2. insulation grazing a wall
            clash("n-insul", insulation, wall, point=(10.0, 5.65, 3.2), penetration_m=0.01),
            # 3. already signed off
            clash("n-approved", column, duct, point=(15.0, 6.0, 3.0), penetration_m=0.20,
                  status="Approved"),
            # 4. numerically touching, physically nothing
            clash("n-tolerance", column, duct, point=(15.0, 5.9, 3.1), penetration_m=0.0005),
            # 5. a sleeve doing its job
            clash("n-sleeve", sleeve, sprinkler, point=(10.1, 5.7, 2.2), penetration_m=0.10),
            # 6. the real one: duct driving through a structural column
            clash("real-hit", column, duct, point=(15.0, 6.0, 3.0), penetration_m=0.20),
        ]
    )


def repeated_typology_case(levels: int = 5) -> dict[str, Any]:
    """The same riser-versus-beam crossing stacked on every floor."""
    clashes = []
    for level in range(levels):
        z = 3.0 + 3.0 * level
        riser = element(
            f"san/riser-{level}",
            "Pipes",
            "Bajante sanitaria 4in",
            ((7.9, 3.9, z - 3.0), (8.2, 4.2, z + 0.6)),
            source_file="PROY-SAN.rvt",
            model_index=2,
            parent_path_id="san/riser",
            parent_name="AGUAS NEGRAS",
            **{"System Name": "AGUAS NEGRAS - desague", "Level": f"N{level}"},
        )
        clashes.append(
            clash(
                f"typ-{level}",
                beam(100 + level, 8.0, z0=z, z1=z + 0.6),
                riser,
                point=(8.0, 4.0, z + 0.3),
                penetration_m=0.30,
                test="EST vs SAN",
                level=f"N+{z:05.2f}",
            )
        )
    return export(clashes)


def congested_zone_case(count: int = 12) -> dict[str, Any]:
    """A dozen distinct problems packed into one shaft-sized volume."""
    disciplines = [
        ("Ducts", "PROY-HVAC.rvt", 1),
        ("Pipes", "PROY-HID.rvt", 2),
        ("Cable Trays", "PROY-ELE.rvt", 5),
        ("Sprinklers", "PROY-RCI.rvt", 4),
    ]
    clashes = []
    for i in range(count):
        category, source, model_index = disciplines[i % len(disciplines)]
        # Tight enough that every problem lands in one congestion cell, but
        # far enough apart that they stay distinct issues rather than merging.
        offset = 0.15 * i
        clashes.append(
            clash(
                f"cong-{i:02d}",
                beam(200 + i, 40.0 + offset * 0.1),
                element(
                    f"mep/{i}",
                    category,
                    f"{category} {i}",
                    ((39.0 + offset, 2.0, 3.2), (42.0 + offset, 2.3, 3.5)),
                    source_file=source,
                    model_index=model_index,
                    parent_path_id=f"mep/sys-{i}",
                    parent_name=f"SISTEMA-{i}",
                    **{"System Name": f"SISTEMA-{i}"},
                ),
                point=(40.0 + offset, 2.0 + offset * 0.2, 3.3),
                penetration_m=0.12,
                test="EST vs MEP",
            )
        )
    return export(clashes)


def full_project_case() -> dict[str, Any]:
    """Everything at once — the closest thing to a real federated run."""
    merged: list[dict[str, Any]] = []
    for builder in (
        systemic_elevation_case,
        multilayer_wall_case,
        pipe_rack_case,
        noise_case,
        repeated_typology_case,
        congested_zone_case,
    ):
        merged.extend(builder()["clashes"])
    return export(merged, title="PROYECTO-COMPLETO.nwf")
