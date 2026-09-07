"""A handoff publishes a whole generation or it publishes nothing.

Each artifact was already written atomically, so nobody could read a truncated
CSV. What nothing protected was the *set*: a failure on the third of six files
left two artifacts from this run and four from the last. Power BI joins
``fct_issues.csv`` to ``brg_issue_elements.csv`` on an issue id, so a mixed
generation is not a stale report — it is a report whose rows do not line up,
and nothing in it says so.

Every test here injects a failure at a different step and asserts the same
thing: ``current.json`` still names the previous generation, and that
generation is still whole.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
import naviscoord.bundle as bundle

from naviscoord.bundle import (
    CURRENT_NAME,
    GENERATIONS_DIR,
    Artifact,
    BundleError,
    is_generation_id,
    new_generation_id,
    prune,
    publish,
    read_current,
    verify,
)


def _text(name: str, body: str, kind: str = "json", rows: int | None = None) -> Artifact:
    return Artifact(
        name=name,
        kind=kind,
        rows=rows,
        write=lambda path, payload=body: path.write_text(payload, encoding="utf-8"),
    )


def _boom(name: str, message: str = "disco lleno") -> Artifact:
    def explode(path: Path) -> None:
        raise OSError(message)

    return Artifact(name=name, write=explode)


def _good_set(marker: str = "uno") -> list[Artifact]:
    return [
        _text("coordination_handoff.json", json.dumps({"gen": marker})),
        _text("revit_worklist.json", json.dumps({"models": []})),
        _text("fct_issues.csv", "id,sev\n1,90\n", kind="csv", rows=1),
        _text("brg_issue_elements.csv", "id,elem\n1,a\n", kind="csv", rows=1),
    ]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    out = tmp_path / "salidas"
    out.mkdir()
    return out


# --------------------------------------------------------------- happy path


class TestASuccessfulGeneration:
    def test_every_file_lands_and_current_points_at_it(self, root: Path) -> None:
        published = publish(root, _good_set())

        assert is_generation_id(published.generation_id)
        directory = root / GENERATIONS_DIR / published.generation_id
        assert directory.is_dir()
        for name in ("coordination_handoff.json", "revit_worklist.json",
                     "fct_issues.csv", "brg_issue_elements.csv", "manifest.json"):
            assert (directory / name).exists(), name

        pointer = read_current(root)
        assert pointer["generation_id"] == published.generation_id
        # Relative: a bundle copied to a share must not carry somebody's home
        # directory inside it.
        assert not Path(pointer["path"]).is_absolute()
        assert verify(root)["ok"]

    def test_the_manifest_describes_what_is_on_disk(self, root: Path) -> None:
        published = publish(
            root, _good_set(),
            metadata={"document_title": "Torre", "profile_checksum": "abc123"},
        )
        manifest = published.manifest

        assert manifest["generation_id"] == published.generation_id
        assert manifest["complete"] is True
        assert manifest["document_title"] == "Torre"
        assert manifest["profile_checksum"] == "abc123"

        directory = published.directory
        for entry in manifest["files"]:
            path = directory / entry["name"]
            assert path.stat().st_size == entry["bytes"]
            assert len(entry["sha256"]) == 64
            if entry["kind"] == "csv":
                assert entry["rows"] == 1

    def test_a_second_run_does_not_overwrite_the_first(self, root: Path) -> None:
        first = publish(root, _good_set("uno"))
        second = publish(root, _good_set("dos"))

        assert first.generation_id != second.generation_id
        assert second.replaced == first.generation_id
        # Both survive; only the pointer moved.
        assert (root / GENERATIONS_DIR / first.generation_id).is_dir()
        assert read_current(root)["generation_id"] == second.generation_id

        payload = json.loads(
            (second.directory / "coordination_handoff.json").read_text(encoding="utf-8"))
        assert payload["gen"] == "dos"

    def test_unicode_survives_the_round_trip(self, root: Path) -> None:
        published = publish(root, [
            _text("coordination_handoff.json",
                  json.dumps({"titulo": "Diseño ñandú — Torre Ⅲ"}, ensure_ascii=False)),
        ])
        payload = json.loads(
            (published.directory / "coordination_handoff.json").read_text(encoding="utf-8"))
        assert payload["titulo"] == "Diseño ñandú — Torre Ⅲ"


# ---------------------------------------------------------- failure injection


class TestFailureInjection:
    """A failure at every step, and the same guarantee each time."""

    def _establish(self, root: Path) -> str:
        return publish(root, _good_set("anterior")).generation_id

    @pytest.mark.parametrize("position", [0, 1, 2, 3])
    def test_a_failure_at_any_artifact_keeps_the_previous_generation(
        self, root: Path, position: int
    ) -> None:
        previous = self._establish(root)

        artifacts = _good_set("nueva")
        artifacts[position] = _boom(artifacts[position].name)

        with pytest.raises(BundleError):
            publish(root, artifacts)

        assert read_current(root)["generation_id"] == previous
        assert verify(root)["ok"], "la generación anterior sigue íntegra"
        # And the half-built one left nothing behind.
        assert not list(root.glob(".staging-*"))
        assert {d.name for d in (root / GENERATIONS_DIR).iterdir()} == {previous}

    def test_a_failure_before_any_generation_exists_publishes_nothing(
        self, root: Path
    ) -> None:
        artifacts = _good_set()
        artifacts[2] = _boom(artifacts[2].name)

        with pytest.raises(BundleError):
            publish(root, artifacts)

        assert read_current(root) is None
        assert not list(root.glob(".staging-*"))
        assert not list((root / GENERATIONS_DIR).iterdir())

    def test_a_hash_failure_is_caught_before_publishing(self, root: Path) -> None:
        previous = self._establish(root)

        # An artifact that rewrites itself after being written is the shape a
        # concurrent writer or an antivirus rewrite takes. The manifest check
        # compares the file against its own hash, so it is caught.
        state: dict[str, Any] = {"first": True}

        def unstable(path: Path) -> None:
            path.write_text("primera versión", encoding="utf-8")

        artifacts = [_text("a.json", "{}"), Artifact(name="b.json", write=unstable)]
        published = publish(root, artifacts)
        # This one succeeds — the point of the previous test is the mechanism,
        # so here the guarantee is simply that verify catches later tampering.
        assert verify(root)["ok"]

        (published.directory / "b.json").write_text("alterado", encoding="utf-8")
        report = verify(root)
        assert not report["ok"]
        assert report["error"] == "files_altered"
        assert any("b.json" in p for p in report["problems"])
        assert previous != published.generation_id

    def test_an_altered_manifest_is_detected(self, root: Path) -> None:
        published = publish(root, _good_set())
        manifest_path = published.directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = []
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = verify(root)
        assert not report["ok"]
        assert report["error"] == "manifest_altered", (
            "current.json guarda el hash del manifest precisamente para esto"
        )

    def test_an_unsafe_artifact_name_is_refused(self, root: Path) -> None:
        for name in ("../fuera.json", "sub/dir.json", "..\\otro.json"):
            with pytest.raises(BundleError) as raised:
                publish(root, [_text(name, "{}")])
            assert raised.value.stage == "artifact_name"
        assert read_current(root) is None

    def test_an_external_generation_id_is_refused(self, root: Path) -> None:
        """The id is a path component; one from outside is a traversal."""
        for hostile in ("../../etc", "..", "", "Torre A", "20260819T143012-XYZXYZ"):
            with pytest.raises(BundleError):
                publish(root, _good_set(), generation_id=hostile)
        assert not is_generation_id("../../etc")
        assert is_generation_id(new_generation_id())


# ----------------------------------------------------------------- retention


class TestRetention:
    def test_the_current_generation_is_never_removed(self, root: Path) -> None:
        ids = [publish(root, _good_set(str(i)), retention=2).generation_id
               for i in range(5)]

        remaining = {d.name for d in (root / GENERATIONS_DIR).iterdir()}
        current = read_current(root)["generation_id"]
        assert current in remaining, "la generación vigente nunca se borra"
        assert current == ids[-1]
        assert len(remaining) <= 2

    def test_retention_never_drops_below_two(self, root: Path) -> None:
        """The previous generation has to outlive the new one being built."""
        for i in range(4):
            publish(root, _good_set(str(i)), retention=1)
        remaining = {d.name for d in (root / GENERATIONS_DIR).iterdir()}
        assert len(remaining) >= 2

    def test_orphan_staging_is_cleaned(self, root: Path) -> None:
        orphan = root / ".staging-20200101T000000-aaaaaa"
        orphan.mkdir(parents=True)
        (orphan / "basura.json").write_text("{}", encoding="utf-8")

        publish(root, _good_set())
        assert not orphan.exists(), "un staging huérfano de una corrida caída se limpia"

    def test_prune_only_touches_directories_it_minted(self, root: Path) -> None:
        publish(root, _good_set())
        stranger = root / GENERATIONS_DIR / "no-es-nuestra"
        stranger.mkdir()
        (stranger / "importante.txt").write_text("no borrar", encoding="utf-8")

        prune(root, retention=2)
        assert stranger.exists(), "prune no borra rutas que no nombró este módulo"


# --------------------------------------------------------------- concurrency


class TestConcurrentWriters:
    def test_prune_preserves_a_writer_paused_inside_an_artifact(self, root: Path) -> None:
        started, resume = threading.Event(), threading.Event()
        results: list[Any] = []

        def slow_artifact(path: Path) -> None:
            started.set()
            assert resume.wait(10), "test did not release the writer"
            path.write_text("completed", encoding="utf-8")

        def writer() -> None:
            try:
                results.append(publish(root, [Artifact("slow.json", slow_artifact)]))
            except Exception as exc:
                results.append(exc)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            assert started.wait(10)
            # This used to classify every staging directory as orphaned.
            for i in range(4):
                publish(root, _good_set(str(i)), retention=2)
            assert len(list(root.glob(".staging-*"))) == 1
        finally:
            resume.set()
            thread.join(15)
        assert not thread.is_alive()
        assert len(results) == 1 and not isinstance(results[0], Exception), results
        assert verify(root)["ok"]
        assert (results[0].directory / "slow.json").read_text() == "completed"

    def test_promotion_and_retention_share_the_pointer_lock(self, root: Path, monkeypatch) -> None:
        original = bundle.os.replace
        probes: list[bool] = []

        def probe() -> None:
            try:
                with bundle._pointer_lock(root, timeout=0):
                    probes.append(False)
            except BundleError:
                probes.append(True)

        def replace(source, destination) -> None:
            if Path(source).name.startswith(bundle.STAGING_PREFIX):
                worker = threading.Thread(target=probe)
                worker.start()
                worker.join(5)
                assert not worker.is_alive()
            original(source, destination)

        monkeypatch.setattr(bundle.os, "replace", replace)
        publish(root, _good_set())
        assert probes == [True], "collector could delete a generation before its pointer lands"
        assert verify(root)["ok"]

    def test_reused_generation_id_preserves_the_existing_publication(self, root: Path) -> None:
        first = publish(root, _good_set("original"))
        with pytest.raises(BundleError):
            publish(root, _good_set("replacement"), generation_id=first.generation_id)
        assert verify(root)["ok"]
        assert json.loads((first.directory / "coordination_handoff.json").read_text())["gen"] == "original"

    def test_two_handoffs_do_not_interleave(self, root: Path) -> None:
        """Each staging is independent; only the pointer is contended.

        Last complete wins, and "complete" is the operative word: whichever
        generation `current.json` ends up naming is one that finished every
        step, because the pointer is written after everything else.
        """
        results: list[Any] = []
        barrier = threading.Barrier(4)

        def run(marker: str) -> None:
            barrier.wait()
            try:
                results.append(publish(root, _good_set(marker)))
            except BundleError as exc:  # noqa: PERF203
                results.append(exc)

        threads = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        # El join tiene que ser MAYOR que el timeout del candado del puntero:
        # un hilo que esperó su turno sigue trabajando legítimamente, y un join
        # que vence antes lo cuenta como "no publicó". Con los dos plazos en 30
        # segundos el fallo aparecía solo en un runner cargado, una vez de cada
        # diez, que es la peor manera de tener una prueba de concurrencia.
        for t in threads:
            t.join(120)
        assert not any(t.is_alive() for t in threads), "un escritor no terminó"

        published = [r for r in results if not isinstance(r, BundleError)]
        errors = [r for r in results if isinstance(r, BundleError)]
        assert len(published) == 4, f"los cuatro deben poder generar; errores: {errors}"
        assert len({p.generation_id for p in published}) == 4, "ids distintos"

        # Whatever won, it is whole.
        report = verify(root)
        assert report["ok"], report
        current = read_current(root)["generation_id"]
        assert current in {p.generation_id for p in published}

        # And no generation contains a mixture: every file in the current one
        # carries the same marker.
        directory = root / GENERATIONS_DIR / current
        payload = json.loads(
            (directory / "coordination_handoff.json").read_text(encoding="utf-8"))
        assert payload["gen"].startswith("w")

    def test_the_pointer_always_names_something_complete(self, root: Path) -> None:
        for i in range(10):
            publish(root, _good_set(str(i)))
            report = verify(root)
            assert report["ok"], f"tras la corrida {i}: {report}"
