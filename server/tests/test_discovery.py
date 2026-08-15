"""Structure discovery tests.

The federated path cannot be exercised against the models available here —
both real projects publish a single flattened file — so it is covered with a
synthetic schema instead. That is worth stating plainly: these tests prove
the logic, not that a real federation behaves as expected.

The level trap has its own test because it is the failure that actually
happened: on a live model the scorer recommended `Layer` as the discipline
key, and `Layer` held the storey names. It would have proposed coordinating
"ground floor versus 7th floor".
"""

from __future__ import annotations

from naviscoord.discovery import discover, infer_group_roles


def _property(tab, name, coverage, values, by_category=None):
    return {
        "tab": tab,
        "name": name,
        "coverage": coverage,
        "distinct_values": len(values),
        "top_values": values,
        "by_category": by_category or {},
    }


def _federated_schema() -> dict:
    """Four files, each holding one trade — the classic federation."""
    return {
        "sampled_items": 4000,
        "models": [
            {"index": 0, "source_file": "PROY-EST.rvt", "sampled": 1200,
             "categories": {"Structural Framing": 800, "Structural Columns": 400}},
            {"index": 1, "source_file": "PROY-HVAC.rvt", "sampled": 1000,
             "categories": {"Ducts": 700, "Duct Fittings": 300}},
            {"index": 2, "source_file": "PROY-HID.rvt", "sampled": 900,
             "categories": {"Pipes": 600, "Pipe Fittings": 300}},
            {"index": 3, "source_file": "PROY-ARQ.rvt", "sampled": 900,
             "categories": {"Walls": 600, "Doors": 300}},
        ],
        "categories": {
            "Structural Framing": 800, "Structural Columns": 400,
            "Ducts": 700, "Duct Fittings": 300,
            "Pipes": 600, "Pipe Fittings": 300,
            "Walls": 600, "Doors": 300,
        },
        "properties": [],
    }


def _storey_trap_schema() -> dict:
    """One flattened file whose tidiest property holds the storey names.

    Every floor contains the same mix of trades, so `Layer` scores perfectly
    on coverage, group count and balance — and means nothing.
    """
    per_floor = {"Walls": 200, "Pipes": 150, "Ducts": 150, "Structural Framing": 100}
    return {
        "sampled_items": 2400,
        "models": [{"index": 0, "source_file": "TODO.rvt", "sampled": 2400,
                    "categories": {k: v * 4 for k, v in per_floor.items()}}],
        "categories": {k: v * 4 for k, v in per_floor.items()},
        "properties": [
            _property(
                "Item", "Layer", 1.0,
                {"NIVEL 1": 600, "NIVEL 2": 600, "NIVEL 3": 600, "CUBIERTA": 600},
                {floor: dict(per_floor) for floor in
                 ("NIVEL 1", "NIVEL 2", "NIVEL 3", "CUBIERTA")},
            ),
        ],
    }


def test_federation_is_recognised():
    report = discover(_federated_schema())
    assert report.federated
    assert report.recommended is not None
    assert report.recommended.kind == "source_file"


def test_federated_files_get_roles_from_their_contents(profile):
    """The filename is a label; what the file holds is the evidence."""
    report = discover(_federated_schema())
    roles = {r.value: r.role for r in infer_group_roles(report.recommended, profile)}
    assert roles["PROY-EST.rvt"] == "EST"
    assert roles["PROY-HVAC.rvt"] == "HVAC"
    assert roles["PROY-HID.rvt"] == "HID"
    assert roles["PROY-ARQ.rvt"] == "ARQ"


def test_role_inference_ignores_the_filename(profile):
    """Rename every file to something meaningless: the roles must hold."""
    schema = _federated_schema()
    for index, model in enumerate(schema["models"]):
        model["source_file"] = f"Lote {index + 1}.rvt"
    report = discover(schema)
    roles = {r.value: r.role for r in infer_group_roles(report.recommended, profile)}
    assert roles["Lote 1.rvt"] == "EST"
    assert roles["Lote 2.rvt"] == "HVAC"
    assert roles["Lote 3.rvt"] == "HID"


def test_storey_property_is_not_recommended_as_a_discipline_key():
    """The regression that matters: floors must never pass as trades."""
    report = discover(_storey_trap_schema())
    assert report.recommended is not None
    assert report.recommended.name != "Layer"

    layer = next(c for c in report.candidates if c.name == "Layer")
    # Every floor holds the same mix, so the grouping says nothing about
    # what an element is.
    assert layer.informativeness < 0.05
    assert any("NO dicen nada" in reason for reason in layer.reasons)


def test_category_candidate_produces_roles(profile):
    """The most common recommendation must not come back empty-handed."""
    report = discover(_storey_trap_schema())
    assert report.recommended.kind == "category"
    roles = infer_group_roles(report.recommended, profile)
    assert roles
    assert {r.role for r in roles} >= {"ARQ", "HID", "HVAC", "EST"}


def test_classification_systems_are_detected_by_value_shape():
    """Matched on the values, because the property name varies by office."""
    schema = _storey_trap_schema()
    schema["properties"].extend([
        _property("Element", "Assembly Code", 0.8,
                  {"23-13 11 11": 40, "23-17 11 00": 30, "22-01 00 00": 20}),
        _property("Datos", "Clasificación", 0.7,
                  {"Ss_25_10_30": 30, "Pr_20_93_52": 25, "Ss_30_12_25": 20}),
    ])
    systems = {c.system for c in discover(schema).classifications}
    assert "OmniClass" in systems
    assert "Uniclass" in systems


def test_unknown_values_do_not_invent_a_classification():
    schema = _storey_trap_schema()
    schema["properties"].append(
        _property("Datos", "Comentario", 0.9,
                  {"revisar": 40, "pendiente obra": 30, "ok": 20})
    )
    assert not discover(schema).classifications


def test_flattened_model_is_flagged():
    report = discover(_storey_trap_schema())
    assert not report.federated
    assert any("aplanado" in note for note in report.notes)


def test_empty_schema_does_not_explode():
    report = discover({"sampled_items": 0, "models": [], "categories": {}, "properties": []})
    assert report.recommended is None
    assert report.notes
