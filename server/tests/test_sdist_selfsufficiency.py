"""El sdist tiene que bastarse a sí mismo, y el build tiene que repetirse.

Estas son las dos preguntas del bloque E que solo se responden construyendo:

  * ¿Puede alguien que solo tiene el sdist reconstruir el wheel? Si le falta
    el ``pyproject.toml``, el perfil por defecto o el README que
    ``project.readme`` nombra, se descarga bien y falla al construir, meses
    después y en la máquina de otro.
  * ¿Dos construcciones del mismo árbol dan los mismos bytes? El wheel lo
    consigue solo porque setuptools honra ``SOURCE_DATE_EPOCH``; el sdist NO
    —ahí las marcas de tiempo son las del disco— y por eso existe
    ``normalize_sdist``. Sin una prueba, esa normalización podía romperse y
    nadie se enteraba hasta comparar dos releases.

Ninguna de las dos toca la red: ``--no-isolation`` construye con lo que ya
está instalado. Cuando el backend no está, se **omiten** en vez de fingir que
pasaron — un `skip` visible dice la verdad y un verde inventado no.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "server" / "dist"
sys.path.insert(0, str(ROOT / "scripts"))

import verify_sdist  # noqa: E402


def backend_missing() -> bool:
    return not verify_sdist.backend_available()


def artifacts_missing() -> bool:
    return not (list(DIST.glob("*.whl")) and list(DIST.glob("*.tar.gz")))


needs_build = pytest.mark.skipif(
    backend_missing(),
    reason="sin 'build'/'setuptools' no se puede reconstruir sin red")
needs_artifacts = pytest.mark.skipif(
    artifacts_missing(),
    reason="no hay artefactos en server/dist: corre scripts/build_artifacts.py")


class TestTheSdistCanRebuildTheWheel:
    @needs_build
    @needs_artifacts
    def test_the_wheel_rebuilds_from_the_extracted_sdist(self) -> None:
        """La prueba entera de E5, hecha construyendo y no leyendo la lista."""
        assert verify_sdist.verify(strict=False) == 0, (
            "el sdist no se basta a sí mismo o produce otro wheel")

    @needs_artifacts
    def test_the_sdist_carries_what_a_build_needs(self) -> None:
        """Lectura barata que además nombra QUÉ falta cuando falta."""
        import tarfile

        sdist = sorted(DIST.glob("*.tar.gz"))[-1]
        names = tarfile.open(sdist).getnames()
        for required in ("pyproject.toml", "README.md", "LICENSE", "NOTICE"):
            assert any(n.endswith(f"/{required}") for n in names), (
                f"el sdist no lleva {required}: no se puede construir desde él")
        for packaged in ("naviscoord/profiles/default.json",
                         "naviscoord/profiles/profile_contract.json",
                         "naviscoord/mcp_server.py"):
            assert any(n.endswith(packaged) for n in names), (
                f"el sdist no lleva {packaged}")

    @needs_artifacts
    def test_the_sdist_has_exactly_one_root_directory(self) -> None:
        """Un sdist con dos raíces se extrae encima de lo que haya al lado."""
        import tarfile

        sdist = sorted(DIST.glob("*.tar.gz"))[-1]
        roots = {n.split("/")[0] for n in tarfile.open(sdist).getnames()}
        assert len(roots) == 1, f"el sdist trae varias raíces: {sorted(roots)}"

    @needs_artifacts
    def test_no_member_escapes_its_root(self) -> None:
        """Ni rutas absolutas ni '..': un sdist es un archivo descargable."""
        import tarfile

        sdist = sorted(DIST.glob("*.tar.gz"))[-1]
        for name in tarfile.open(sdist).getnames():
            assert not name.startswith("/"), name
            assert ".." not in Path(name).parts, name


class TestTheBuildRepeatsItself:
    @needs_build
    def test_two_builds_of_this_tree_produce_the_same_bytes(self) -> None:
        """E6, sin adornos: construir dos veces y comparar.

        Lento a propósito —construye dos veces de verdad— porque la
        alternativa es afirmar reproducibilidad sin haberla medido, que es
        exactamente lo que este bloque existe para dejar de hacer.
        """
        result = subprocess.run(
            # `--no-isolation` no es un atajo: sin el, `build` resuelve el
            # backend contra el indice y esta prueba descargaria de Internet.
            # Una prueba que descarga falla cuando el indice va lento y aprueba
            # por razones ajenas al codigo.
            [sys.executable, str(ROOT / "scripts" / "build_artifacts.py"),
             "--reproducible", "--no-isolation"],
            capture_output=True, text=True, cwd=str(ROOT))
        assert result.returncode == 0, (
            "dos construcciones del mismo árbol dieron artefactos distintos:\n"
            + (result.stdout or "")[-3000:] + (result.stderr or "")[-2000:])
        assert "reproducible:" in result.stdout


class TestTheVerifierIsHonest:
    """Un verificador que no sabe fallar no verifica nada."""

    def test_it_refuses_when_the_backend_is_absent(self, monkeypatch) -> None:
        monkeypatch.setattr(verify_sdist, "backend_available", lambda: False)
        monkeypatch.setattr(verify_sdist, "newest", lambda pattern: Path("x"))
        assert verify_sdist.verify() == 2, (
            "sin backend debe salir 2, no fingir éxito")

    def test_it_refuses_when_there_is_nothing_to_verify(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(verify_sdist, "DIST", tmp_path)
        with pytest.raises(SystemExit):
            verify_sdist.newest("*.whl")

    def test_its_workspace_removal_demands_its_own_marker(self, tmp_path) -> None:
        victim = tmp_path / "no-es-suyo"
        victim.mkdir()
        (victim / "importante.txt").write_text("x", encoding="utf-8")
        verify_sdist.discard(victim, "cualquiera")
        assert (victim / "importante.txt").exists()
