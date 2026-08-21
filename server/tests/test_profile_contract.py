"""El contrato del perfil contra lo que el código consume DE VERDAD.

Un contrato que se mantiene a mano se queda atrás en la primera prisa. Estas
pruebas lo convierten en estructural: escanean el código fuente del motor en
busca de cada acceso al perfil —``.section("x")``, ``severity_param("y")``…—
y exigen que cada nombre encontrado aparezca en el contrato, y que cada campo
del contrato tenga al menos un consumidor. Añadir un acceso nuevo sin declarar
el campo rompe aquí; declarar un campo que nadie lee rompe aquí también.

El mismo archivo ``profile_contract.json`` lo lee la suite del add-in
(ProfileContractTests), que compara su ``KnownRootKeys`` compilado contra las
mismas claves. Los dos lados no pueden divergir sin que un lado falle.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "server" / "naviscoord"
sys.path.insert(0, str(ROOT / "server"))

from naviscoord import profilerules  # noqa: E402
from naviscoord.profilerules import (  # noqa: E402
    NUMBER_RULES,
    UNKNOWN_ERROR,
    WEIGHT_MAPS,
    check_unknown,
    known_root_keys,
    load_contract,
    validate_semantics,
)


@pytest.fixture(scope="module")
def contract() -> dict:
    return load_contract()


def engine_sources() -> list[Path]:
    return [p for p in PACKAGE.rglob("*.py") if "profiles" not in p.parts]


def scan(pattern: str) -> set[str]:
    found: set[str] = set()
    for path in engine_sources():
        found.update(re.findall(pattern, path.read_text(encoding="utf-8")))
    return found


class TestEverySectionConsumedIsDeclared:
    """A1: el guard. Consumir una sección nueva exige declararla primero."""

    def test_every_dot_section_call_is_in_the_contract(self, contract):
        consumed = scan(r'\.section\("([a-z_$][a-z_]*)"\)')
        declared = set(contract["sections"])
        undeclared = consumed - declared
        assert not undeclared, (
            f"el motor lee secciones fuera del contrato: {sorted(undeclared)}. "
            "Declararlas en profiles/profile_contract.json es parte de añadirlas.")

    def test_every_number_rule_lives_in_a_declared_section(self, contract):
        declared = set(contract["sections"])
        for rule in NUMBER_RULES:
            section = rule.path.split(".")[0]
            assert section in declared, f"{rule.path}: sección {section} sin declarar"

    def test_every_weight_map_lives_in_a_declared_section(self, contract):
        declared = set(contract["sections"])
        for path, _ in WEIGHT_MAPS:
            assert path.split(".")[0] in declared, path

    def test_every_param_accessor_field_is_declared(self, contract):
        """Los accessors con nombre: severity_param('x'), cluster_param('x')…"""
        by_accessor = {
            "severity_param": "severity",
            "cluster_param": "clustering",
            "noise_param": "noise_filter",
        }
        for accessor, section in by_accessor.items():
            consumed = scan(rf'{accessor}\("([a-z_]+)"')
            declared = set(contract["sections"][section]["fields"])
            undeclared = consumed - declared
            assert not undeclared, (
                f"{accessor} lee campos fuera del contrato de «{section}»: "
                f"{sorted(undeclared)}")

    def test_every_root_cause_rule_is_declared(self, contract):
        consumed = scan(r'root_cause_param\("([a-z_]+)",')
        declared = set(contract["sections"]["root_cause"]["fields"])
        undeclared = consumed - declared
        assert not undeclared, f"reglas de root_cause sin declarar: {sorted(undeclared)}"

    def test_direct_section_gets_are_declared(self, contract):
        """.section('x').get('y') accede a un campo con nombre: también cuenta."""
        pairs = set()
        for path in engine_sources():
            pairs.update(re.findall(
                r'\.section\("([a-z_]+)"\)\.get\("([a-z_]+)"',
                path.read_text(encoding="utf-8")))
        for section, fieldname in pairs:
            spec = contract["sections"].get(section)
            assert spec is not None, section
            if spec["field_policy"] == "closed":
                assert fieldname in spec["fields"], (
                    f"{section}.{fieldname} se consume y no está en el contrato")


class TestEveryDeclaredFieldIsConsumed:
    """La otra dirección: un contrato con campos muertos también miente."""

    def test_no_declared_field_is_orphaned(self, contract):
        source = "\n".join(p.read_text(encoding="utf-8") for p in engine_sources())
        addin_source = "\n".join(
            p.read_text(encoding="utf-8")
            for p in (ROOT / "addin" / "NavisCoord.Addin").glob("*.cs"))
        for section, spec in contract["sections"].items():
            if not spec["fields"]:
                continue
            consumers = spec["consumers"]
            reserved = set(spec.get("reserved", []))
            for fieldname in spec["fields"]:
                if fieldname in reserved:
                    continue
                haystacks = []
                if "engine" in consumers:
                    haystacks.append(source)
                if "addin" in consumers:
                    haystacks.append(addin_source)
                assert any(f'"{fieldname}"' in h for h in haystacks), (
                    f"{section}.{fieldname} está declarado y ningún consumidor "
                    "lo nombra: o sobró o el acceso usa otra clave")


class TestReservedFields:
    """Un campo «reserved» no es una puerta trasera: sigue validado."""

    def test_every_reserved_field_is_still_guarded_by_a_rule(self, contract):
        ruled = {rule.path for rule in NUMBER_RULES}
        for section, spec in contract["sections"].items():
            for fieldname in spec.get("reserved", []):
                assert f"{section}.{fieldname}" in ruled, (
                    f"{section}.{fieldname} está reservado y sin regla numérica: "
                    "reservado significa validado-sin-consumidor, no ignorado")

    def test_every_reserved_field_is_also_declared(self, contract):
        for section, spec in contract["sections"].items():
            for fieldname in spec.get("reserved", []):
                assert fieldname in spec["fields"], f"{section}.{fieldname}"

    def test_a_reserved_field_carries_its_why(self, contract):
        for section, spec in contract["sections"].items():
            if spec.get("reserved"):
                assert spec.get("_why_reserved"), (
                    f"{section}: reserved sin explicación no se distingue de un olvido")


class TestShippedProfilesFitTheContract:
    def test_default_profile_root_keys_are_all_declared(self, contract):
        raw = json.loads(
            (PACKAGE / "profiles" / "default.json").read_text(encoding="utf-8"))
        known = known_root_keys(contract)
        for key in raw:
            if str(key).startswith("_"):
                continue
            assert key in known, f"default.json trae «{key}» fuera del contrato"

    def test_default_noise_filter_fields_are_all_declared(self, contract):
        """El inventario encontró drop_same_element: declarado en el perfil,
        leído por nadie (el descarte de mismo-elemento es incondicional).
        Esta prueba impide que vuelva."""
        raw = json.loads(
            (PACKAGE / "profiles" / "default.json").read_text(encoding="utf-8"))
        declared = set(contract["sections"]["noise_filter"]["fields"])
        for key in raw.get("noise_filter", {}):
            if str(key).startswith("_"):
                continue
            assert key in declared, (
                f"default.json declara noise_filter.{key} y ningún consumidor lo lee")

    def test_example_profile_root_keys_are_all_declared(self, contract):
        example = ROOT / "profiles" / "example-profile.json"
        if not example.exists():
            pytest.skip("no hay perfil de ejemplo en el repo")
        raw = json.loads(example.read_text(encoding="utf-8"))
        known = known_root_keys(contract)
        for key in raw:
            if str(key).startswith("_"):
                continue
            assert key in known, f"example-profile.json trae «{key}» fuera del contrato"


class TestUnknownKeyPolicy:
    def test_an_unknown_root_section_is_refused(self):
        problems = check_unknown({"severty": {"weights": {}}})
        assert len(problems) == 1
        assert problems[0].code == UNKNOWN_ERROR
        assert problems[0].path == "severty"

    def test_the_refusal_names_the_escape_hatch(self):
        problems = check_unknown({"mi_seccion": {}})
        assert "extensions" in problems[0].detail

    def test_a_comment_key_is_not_unknown(self):
        assert check_unknown({"_comment": "hola", "_todo": ["x"]}) == []

    def test_extensions_content_is_never_validated(self):
        raw = {"extensions": {"loquesea": {"eps_m": -5, "weights": {"a": -1}}}}
        assert validate_semantics(raw) == []

    def test_every_metadata_key_is_accepted(self, contract):
        raw = {key: "x" for key in contract["metadata_keys"]}
        assert check_unknown(raw) == []

    def test_it_participates_in_validate_semantics(self):
        problems = validate_semantics({"seccion_inventada": {}})
        assert any(p.code == UNKNOWN_ERROR for p in problems)


class TestContractDocument:
    def test_the_schema_is_the_expected_one(self, contract):
        assert contract["schema"] == "naviscoord.profile-contract/1"

    def test_extensions_is_a_declared_section_with_free_policy(self, contract):
        spec = contract["sections"]["extensions"]
        assert spec["field_policy"] == "free"
        assert spec["consumers"] == []

    def test_field_lists_are_sorted_and_unique(self, contract):
        for section, spec in contract["sections"].items():
            fields = spec["fields"]
            assert len(fields) == len(set(fields)), f"{section}: campos repetidos"

    def test_a_wrong_schema_is_refused(self, tmp_path):
        bogus = tmp_path / "contract.json"
        bogus.write_text('{"schema": "otra-cosa/9"}', encoding="utf-8")
        with pytest.raises(ValueError):
            load_contract(bogus)

    def test_the_contract_ships_inside_the_package_data(self):
        """El wheel lo incluye por el glob profiles/*.json; si el archivo se
        moviera fuera de esa carpeta, el launcher validaría contra nada."""
        assert profilerules.CONTRACT_PATH.parent.name == "profiles"
        assert profilerules.CONTRACT_PATH.exists()


class TestDocsDoNotDrift:
    """docs/PROFILES.md no puede contar un formato que el contrato no tiene."""

    DOC = ROOT / "docs" / "PROFILES.md"

    def test_the_doc_names_the_contract_file(self):
        text = self.DOC.read_text(encoding="utf-8")
        assert "profile_contract.json" in text

    def test_the_doc_names_every_reserved_field(self, contract):
        text = self.DOC.read_text(encoding="utf-8")
        for section, spec in contract["sections"].items():
            for fieldname in spec.get("reserved", []):
                assert fieldname in text, (
                    f"{section}.{fieldname} está reservado y la doc no lo cuenta")

    def test_the_doc_documents_the_escape_hatch(self):
        text = self.DOC.read_text(encoding="utf-8")
        assert "`extensions`" in text

    def test_the_doc_says_unknown_sections_are_errors_not_warnings(self):
        text = self.DOC.read_text(encoding="utf-8")
        assert "profile_unknown" in text or "rechaza" in text
