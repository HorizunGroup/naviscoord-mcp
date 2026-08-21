"""El build no puede modificar el árbol que está empaquetando.

La versión anterior copiaba LICENSE, NOTICE y README.md dentro de `server/` y
los borraba en un `finally`. Un `finally` no corre si matan el proceso, así
que una compilación interrumpida dejaba tres archivos sin rastrear que parecen
una segunda fuente de verdad; dos builds simultáneos se peleaban por ellos; y
compilar desde un tag marcaba como sucio el worktree cuya limpieza la release
tenía que demostrar.

Estas pruebas no compilan de verdad —eso requiere red y minutos— sino que
ejercitan las piezas que tocan el disco: la creación del workspace y su
borrado. Es donde estaban los defectos.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_artifacts as build  # noqa: E402


@pytest.fixture()
def workspace():
    made, nonce = build.make_workspace()
    yield made, nonce
    build.discard_workspace(made, nonce)


class TestWorkspaceIsolation:
    def test_the_source_tree_is_not_touched(self, workspace):
        """Ni antes ni después existe LICENSE dentro de server/."""
        for name in build.STAGED:
            assert not (build.SERVER / name).exists(), (
                f"server/{name} apareció en el árbol fuente: el build lo está mutando")

    def test_the_licences_land_in_the_copy(self, workspace):
        made, _ = workspace
        package = made / "server"
        for name in build.STAGED:
            assert (package / name).is_file(), f"falta {name} en el workspace"
            assert (package / name).read_bytes() == (ROOT / name).read_bytes()

    def test_the_package_source_is_actually_there(self, workspace):
        made, _ = workspace
        assert (made / "server" / "naviscoord" / "mcp_server.py").is_file()
        assert (made / "server" / "pyproject.toml").is_file()
        assert (made / "server" / "naviscoord" / "profiles" / "default.json").is_file()

    def test_previous_output_is_not_copied_in(self, workspace):
        """`dist` dentro del workspace metería la release anterior en la nueva."""
        made, _ = workspace
        assert not (made / "server" / "dist").exists()
        for cached in (made / "server").rglob("__pycache__"):
            pytest.fail(f"se copió una caché: {cached}")

    def test_two_runs_never_share_a_directory(self):
        first, nonce_a = build.make_workspace()
        second, nonce_b = build.make_workspace()
        try:
            assert first != second, "dos ejecuciones usaron el mismo temporal"
            assert nonce_a != nonce_b
        finally:
            build.discard_workspace(first, nonce_a)
            build.discard_workspace(second, nonce_b)

    def test_the_workspace_lives_in_the_system_temp(self, workspace):
        made, _ = workspace
        assert Path(tempfile.gettempdir()).resolve() in made.resolve().parents


class TestWorkspaceRemoval:
    def test_its_own_workspace_is_removed(self):
        made, nonce = build.make_workspace()
        build.discard_workspace(made, nonce)
        assert not made.exists()

    def test_a_wrong_nonce_removes_nothing(self):
        """El borrado no se apoya en la ruta: exige el marcador de ESTA corrida."""
        made, nonce = build.make_workspace()
        try:
            build.discard_workspace(made, "otro-nonce-cualquiera")
            assert made.exists(), "borró un workspace que no era suyo"
        finally:
            build.discard_workspace(made, nonce)

    def test_a_directory_without_the_marker_survives(self, tmp_path):
        victim = tmp_path / "no-es-un-workspace"
        victim.mkdir()
        (victim / "importante.txt").write_text("x", encoding="utf-8")
        build.discard_workspace(victim, "cualquiera")
        assert (victim / "importante.txt").exists()

    def test_a_path_outside_the_temp_root_is_refused(self, tmp_path, monkeypatch):
        """Marcador correcto, ruta fuera del temporal: se rechaza igual."""
        outside = tmp_path / "fuera"
        outside.mkdir()
        (outside / build.OWNERSHIP_MARKER).write_text("n1", encoding="utf-8")
        (outside / "importante.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "otro-temp"))
        (tmp_path / "otro-temp").mkdir()
        build.discard_workspace(outside, "n1")
        assert (outside / "importante.txt").exists()

    def test_the_temp_root_itself_is_refused(self, tmp_path, monkeypatch):
        (tmp_path / build.OWNERSHIP_MARKER).write_text("n1", encoding="utf-8")
        (tmp_path / "importante.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        build.discard_workspace(tmp_path, "n1")
        assert (tmp_path / "importante.txt").exists()

    @pytest.mark.skipif(os.name != "nt", reason="junctions son de Windows")
    def test_a_junction_is_not_followed(self, tmp_path, monkeypatch):
        """Un junction con el marcador correcto no autoriza borrar su destino."""
        real = tmp_path / "datos-reales"
        real.mkdir()
        (real / build.OWNERSHIP_MARKER).write_text("n1", encoding="utf-8")
        (real / "importante.txt").write_text("x", encoding="utf-8")
        link = tmp_path / "enlace"
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(real)],
            capture_output=True, text=True)
        if made.returncode != 0:
            pytest.skip("el entorno no permite crear junctions")
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        build.discard_workspace(link, "n1")
        assert (real / "importante.txt").exists(), "siguió el junction y borró el destino"


class TestSourceDateEpoch:
    def test_it_comes_from_the_commit_not_the_clock(self, monkeypatch):
        monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
        first = build.source_date_epoch()
        second = build.source_date_epoch()
        assert first == second, "dos lecturas dieron fechas distintas"
        assert first.isdigit()

    def test_an_existing_value_wins(self, monkeypatch):
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1234567890")
        assert build.source_date_epoch() == "1234567890"
