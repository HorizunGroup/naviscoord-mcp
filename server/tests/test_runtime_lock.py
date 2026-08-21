"""Exclusion between PROCESSES, demonstrated with processes.

The defect: two MCP clients starting together both saw a missing venv and both
ran ``pip install`` into it. A ``threading.Lock`` cannot help — the builders are
separate processes, often separate clients — and neither can "if the directory
exists, someone is building", because between the check and the create there is
a window, and that window is the bug.

So the concurrency tests here spawn real ``subprocess`` children. Two threads
inside one interpreter would prove nothing about the thing that broke.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import textwrap
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from runtime_lock import (  # noqa: E402
    BUSY_ALIVE,
    BUSY_UNKNOWN,
    CORRUPT,
    DENIED,
    LOCK_FORMAT,
    LockUnavailable,
    Owner,
    RuntimeLock,
    classify_owner,
    mint_owner,
    read_owner,
)
from runtime_lock_spec import (  # noqa: E402
    LOCK_PATH,
    install_arguments,
    load,
    requirements_text,
    satisfies_range,
    verify,
)

PUBLIC_RANGES = {"mcp": ">=1.9,<3", "reportlab": ">=4.0,<6", "pillow": ">=10.0"}


# ------------------------------------------------------- real processes


CHILD = textwrap.dedent(
    """
    import json, os, sys, time
    sys.path.insert(0, sys.argv[1])
    from runtime_lock import RuntimeLock, LockUnavailable

    lock_path, marker_dir, hold = sys.argv[2], sys.argv[3], float(sys.argv[4])
    timeout, start_at = float(sys.argv[5]), float(sys.argv[6])

    # Barrera de arranque. Sin ella la prueba medía el arranque del intérprete
    # y no el lock: con la máquina cargada, un hijo podía tardar más en
    # arrancar de lo que el ganador tardaba en soltar, encontrarse un lock
    # libre y construir también. Dos builders, cero solapamiento, y el lock
    # habiendo hecho exactamente su trabajo. Con todos esperando al mismo
    # instante de reloj, la contención ocurre de verdad.
    while time.time() < start_at:
        time.sleep(0.002)

    began = time.time()
    guard = RuntimeLock(lock_path, "k", "0.4.0")
    try:
        guard.acquire(timeout=timeout, poll=0.01)
    except LockUnavailable as exc:
        # `waited` no es adorno: un rechazo que llega antes del plazo pedido
        # es un defecto distinto de uno que llega al vencerlo, y sin el numero
        # los dos se leen igual en el fallo.
        print(json.dumps({"outcome": "refused", "code": exc.code,
                          "waited": round(time.time() - began, 3),
                          "timeout": timeout, "detail": exc.detail[:120]}))
        raise SystemExit(0)

    acquired = time.time()
    # Proof that this process, and only this process, ran the builder.
    with open(os.path.join(marker_dir, "built-%d" % os.getpid()), "w") as handle:
        handle.write(guard.owner.nonce)
    time.sleep(hold)
    released = guard.release()
    print(json.dumps({"outcome": "built", "pid": os.getpid(), "released": released,
                      "held_from": acquired, "held_to": time.time()}))
    """
)


def _spawn(tmp_path: Path, lock_path: Path, marker_dir: Path,
           hold: float = 0.4, timeout: float = 0.05,
           start_at: float | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(SCRIPTS), str(lock_path),
         str(marker_dir), str(hold), str(timeout),
         str(start_at if start_at is not None else 0.0)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


class TestBetweenProcesses:
    @pytest.mark.parametrize("attempt", range(5))
    def test_only_one_of_four_processes_builds(self, tmp_path: Path, attempt: int) -> None:
        """Repeated, because one green run does not disprove a race."""
        lock_path = tmp_path / "runtime.lock"
        markers = tmp_path / "markers"
        markers.mkdir()

        # Margen para que los cuatro intérpretes estén arrancados antes del
        # instante común: la barrera es lo que hace que esto mida el lock y no
        # al planificador.
        start_at = time.time() + 2.0
        children = [_spawn(tmp_path, lock_path, markers, start_at=start_at)
                    for _ in range(4)]
        results = []
        for child in children:
            out, err = child.communicate(timeout=60)
            assert child.returncode == 0, err
            results.append(json.loads(out.strip().splitlines()[-1]))

        built = [r for r in results if r["outcome"] == "built"]
        refused = [r for r in results if r["outcome"] == "refused"]

        assert len(built) == 1, f"exactamente un builder; salieron {len(built)}"
        assert len(refused) == 3, "los otros tres deben rechazarse con motivo"
        # DENIED entra en la lista, y no por conveniencia: en Windows un
        # archivo marcado para borrado responde ACCESS_DENIED en vez de
        # "ya existe", asi que un competidor que caiga en esa ventana con un
        # plazo de 50 ms agota el plazo y reporta DENIED. Es contencion, uno
        # de los cinco desenlaces documentados, y sigue sin construir: eso es
        # lo que esta prueba mide. Lo que NO se admite -abajo- es que
        # cualquiera de los tres rehuse antes de agotar su plazo.
        assert all(r["code"] in (BUSY_ALIVE, BUSY_UNKNOWN, DENIED) for r in refused), results
        for entry in refused:
            assert entry["waited"] >= entry["timeout"] * 0.95, (
                f"rechazo prematuro: espero {entry['waited']}s de {entry['timeout']}s "
                f"({entry['code']}: {entry['detail']})")

        # And the filesystem agrees: one marker, one builder.
        assert len(list(markers.iterdir())) == 1
        assert built[0]["released"], "el ganador libera su lock"
        assert not lock_path.exists(), "y no queda lock huerfano"

    @pytest.mark.parametrize("attempt", range(5))
    def test_no_two_processes_ever_hold_it_at_once(
        self, tmp_path: Path, attempt: int
    ) -> None:
        """La propiedad que el lock promete, sin depender del planificador.

        "Solo uno construye" exige que la contencion se solape de verdad, y de
        eso se encarga la barrera. Esto es lo otro: reparta el reloj los
        procesos como quiera, los intervalos en que cada uno TUVO el lock
        tienen que ser disjuntos. Aqui se da timeout de sobra para que los
        cuatro acaben construyendo por turnos, que es justo el caso donde un
        lock roto se delata solapandose.
        """
        lock_path = tmp_path / "runtime.lock"
        markers = tmp_path / "markers"
        markers.mkdir()

        children = [
            _spawn(tmp_path, lock_path, markers, hold=0.15, timeout=30.0,
                   start_at=time.time() + 2.0)
            for _ in range(4)
        ]
        results = []
        for child in children:
            out, err = child.communicate(timeout=90)
            assert child.returncode == 0, err
            results.append(json.loads(out.strip().splitlines()[-1]))

        built = [r for r in results if r["outcome"] == "built"]
        refused = [r for r in results if r["outcome"] == "refused"]

        # LA propiedad: los intervalos de tenencia son disjuntos. No depende
        # del planificador, de la carga ni de cuantos lleguen a construir, y
        # es exactamente lo que un lock roto viola.
        spans = sorted((r["held_from"], r["held_to"], r["pid"]) for r in built)
        for (_, end_a, pid_a), (start_b, _, pid_b) in zip(spans, spans[1:]):
            assert start_b >= end_a, (
                f"los procesos {pid_a} y {pid_b} tuvieron el lock a la vez: "
                f"{pid_a} lo solto en {end_a} y {pid_b} lo tomo en {start_b}")

        # Cada constructor dejo su marca, y solo la suya.
        assert len(list(markers.iterdir())) == len(built)
        assert len(built) >= 1, f"nadie construyo: {results}"

        # Con 30 s de plazo y turnos de 0,15 s, lo normal es que construyan los
        # cuatro. Un rechazo solo se admite si DEMUESTRA haber agotado su
        # plazo: en esta maquina hay atascos que duplican el tiempo de la
        # suite entera, y un plazo vencido de verdad es un desenlace legitimo
        # del entorno. Uno que llegue antes es el defecto que ya aparecio una
        # vez -rehusar en el acto sin respetar el timeout- y no vuelve a pasar
        # inadvertido.
        for entry in refused:
            assert entry["waited"] >= entry["timeout"] * 0.95, (
                f"rechazo prematuro: espero {entry['waited']}s de un plazo de "
                f"{entry['timeout']}s ({entry['code']}: {entry['detail']})")
            assert entry["code"] in (BUSY_ALIVE, BUSY_UNKNOWN), entry

        assert not lock_path.exists(), "y no queda lock huerfano"

    def test_the_second_process_proceeds_once_the_first_finishes(
        self, tmp_path: Path
    ) -> None:
        lock_path = tmp_path / "runtime.lock"
        markers = tmp_path / "markers"
        markers.mkdir()

        first = _spawn(tmp_path, lock_path, markers, hold=0.2, timeout=0.05)
        time.sleep(0.15)
        # Generous timeout: this one is meant to WAIT and then succeed.
        second = _spawn(tmp_path, lock_path, markers, hold=0.0, timeout=20.0)

        a = json.loads(first.communicate(timeout=60)[0].strip().splitlines()[-1])
        b = json.loads(second.communicate(timeout=60)[0].strip().splitlines()[-1])

        assert a["outcome"] == "built"
        assert b["outcome"] == "built", "el segundo entra cuando el primero suelta"
        assert a["pid"] != b["pid"]
        assert len(list(markers.iterdir())) == 2, "cada uno construyó en su turno"


# --------------------------------------------------------------- ownership


class TestOwnership:
    def _owner(self, **overrides) -> Owner:
        base = dict(pid=4242, started="1000", host="EQUIPO", nonce="abcd",
                    key="k", plugin="0.4.0", acquired=time.time())
        base.update(overrides)
        return Owner(**base)

    def test_a_live_owner_is_alive(self) -> None:
        state = classify_owner(
            self._owner(), host="EQUIPO",
            exists=lambda _pid: True, start_of=lambda _pid: "1000")
        assert state == "alive"

    def test_a_recycled_pid_is_dead(self) -> None:
        """Same PID, different creation time: a different process."""
        state = classify_owner(
            self._owner(), host="EQUIPO",
            exists=lambda _pid: True, start_of=lambda _pid: "9999")
        assert state == "dead"

    def test_an_absent_process_is_dead(self) -> None:
        state = classify_owner(
            self._owner(), host="EQUIPO",
            exists=lambda _pid: False, start_of=lambda _pid: None)
        assert state == "dead"

    def test_an_unreadable_start_time_is_unknown(self) -> None:
        """It exists and we cannot ask. Guessing either way is worse."""
        state = classify_owner(
            self._owner(), host="EQUIPO",
            exists=lambda _pid: True, start_of=lambda _pid: None)
        assert state == "unknown"

    def test_an_unqueryable_process_is_unknown(self) -> None:
        state = classify_owner(
            self._owner(), host="EQUIPO",
            exists=lambda _pid: None, start_of=lambda _pid: None)
        assert state == "unknown"

    def test_a_lock_from_another_host_is_unknown(self) -> None:
        """A shared drive: this host cannot see that process at all."""
        state = classify_owner(
            self._owner(host="OTRO-EQUIPO"), host="EQUIPO",
            exists=lambda _pid: False, start_of=lambda _pid: None)
        assert state == "unknown", "declararlo muerto rompería el lock de otra máquina"

    def test_the_real_probe_recognises_this_process(self) -> None:
        owner = mint_owner("k", "0.4.0")
        assert owner.pid == os.getpid()
        assert classify_owner(owner) == "alive"


class TestBreakingLocks:
    def test_a_live_lock_is_never_broken(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "runtime.lock"
        held = RuntimeLock(lock_path, "k", "0.4.0").acquire(timeout=1)

        other = RuntimeLock(lock_path, "k", "0.4.0")
        with pytest.raises(LockUnavailable) as raised:
            other.acquire(timeout=0.05, poll=0.01)
        assert raised.value.code == BUSY_ALIVE
        assert lock_path.exists(), "el lock del proceso vivo sigue ahí"
        held.release()

    def test_a_dead_owner_releases_the_lock(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "runtime.lock"
        stale = Owner(pid=999_999, started="1", host=os.environ.get("COMPUTERNAME", ""),
                      nonce="x", key="k", plugin="0.4.0", acquired=0.0)
        import platform
        stale = Owner(pid=999_999, started="1", host=platform.node(),
                      nonce="x", key="k", plugin="0.4.0", acquired=0.0)
        lock_path.write_text(json.dumps(stale.to_json()), encoding="utf-8")

        taken = RuntimeLock(lock_path, "k", "0.4.0").acquire(timeout=5, poll=0.01)
        assert taken.owner is not None, "un dueño demostrablemente muerto sí libera"
        taken.release()

    def test_a_corrupt_lock_is_reported_not_deleted(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "runtime.lock"
        lock_path.write_text("{no soy json", encoding="utf-8")

        with pytest.raises(LockUnavailable) as raised:
            RuntimeLock(lock_path, "k", "0.4.0").acquire(timeout=0.05, poll=0.01)
        assert raised.value.code == CORRUPT
        assert lock_path.exists(), (
            "no se borra: puede pertenecer a un proceso vivo cuya escritura se cortó"
        )

    def test_an_old_lock_is_not_stale_merely_for_being_old(self, tmp_path: Path) -> None:
        """A first provisioning on a slow machine legitimately takes minutes."""
        lock_path = tmp_path / "runtime.lock"
        mine = mint_owner("k", "0.4.0")
        aged = Owner(pid=mine.pid, started=mine.started, host=mine.host,
                     nonce=mine.nonce, key=mine.key, plugin=mine.plugin,
                     acquired=time.time() - 86_400)
        lock_path.write_text(json.dumps(aged.to_json()), encoding="utf-8")

        with pytest.raises(LockUnavailable) as raised:
            RuntimeLock(lock_path, "k", "0.4.0").acquire(timeout=0.05, poll=0.01)
        assert raised.value.code == BUSY_ALIVE
        assert lock_path.exists()

    def test_release_refuses_when_the_nonce_changed(self, tmp_path: Path) -> None:
        """A broken-and-retaken lock must not be deleted by the old holder."""
        lock_path = tmp_path / "runtime.lock"
        held = RuntimeLock(lock_path, "k", "0.4.0").acquire(timeout=1)

        # Somebody else now holds it.
        usurper = mint_owner("k", "0.4.0")
        lock_path.write_text(json.dumps(usurper.to_json()), encoding="utf-8")

        assert not held.release(), "no es suyo, no lo borra"
        assert lock_path.exists(), "el lock del nuevo dueño sobrevive"
        current, _ = read_owner(lock_path)
        assert current.nonce == usurper.nonce

    def test_a_timeout_names_the_holder(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "runtime.lock"
        held = RuntimeLock(lock_path, "k", "0.4.0").acquire(timeout=1)
        with pytest.raises(LockUnavailable) as raised:
            RuntimeLock(lock_path, "k", "0.4.0").acquire(timeout=0.05, poll=0.01)
        assert str(held.owner.pid) in raised.value.detail
        held.release()

    def test_the_lock_records_everything_needed_to_judge_it(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "runtime.lock"
        held = RuntimeLock(lock_path, "clave-runtime", "0.4.0").acquire(timeout=1)
        payload = json.loads(lock_path.read_text(encoding="utf-8"))

        for field in ("format", "pid", "process_started", "host", "nonce",
                      "runtime_key", "plugin_version", "acquired_at", "acquired_utc"):
            assert field in payload, field
        assert payload["format"] == LOCK_FORMAT
        assert payload["runtime_key"] == "clave-runtime"
        held.release()


# ------------------------------------------------------------ the lock file


class TestDependencyLock:
    def test_the_shipped_lock_loads_and_verifies(self) -> None:
        lock = load()
        assert lock.packages, "el lock no puede estar vacío"
        problems = verify(lock, PUBLIC_RANGES)
        assert not problems, problems

    def test_every_direct_dependency_is_pinned(self) -> None:
        names = {p.name for p in load().packages}
        for direct in ("mcp", "reportlab", "pillow"):
            assert direct in names

    def test_the_transitive_closure_is_pinned_too(self) -> None:
        """Floating transitives are the same defect one level down."""
        names = {p.name for p in load().packages}
        assert len(names) > 3, "un lock que solo fija las directas no fija nada"
        assert "anyio" in names or "httpx" in names, names

    def test_every_pin_is_exact(self) -> None:
        for package in load().packages:
            requirement = package.requirement()
            assert "==" in requirement
            assert ">" not in requirement and "<" not in requirement, requirement

    def test_pins_stay_inside_the_public_ranges(self) -> None:
        """The launcher must not provision what the package does not claim."""
        lock = load()
        for name, specifier in PUBLIC_RANGES.items():
            pin = next(p for p in lock.packages if p.name == name)
            assert satisfies_range(pin.version, specifier), (
                f"{name} {pin.version} fuera de {specifier}")

    def test_a_pin_outside_the_public_range_is_caught(self) -> None:
        from runtime_lock_spec import Pinned, RuntimeLockFile

        broken = RuntimeLockFile(
            schema=load().schema, generated_utc="", python_requires=">=3.10",
            packages=(
                Pinned("mcp", "3.5.0"), Pinned("reportlab", "4.2.0"),
                Pinned("pillow", "10.4.0"),
            ),
        )
        problems = verify(broken, PUBLIC_RANGES)
        assert any("mcp" in p and "fuera del rango" in p for p in problems)

    def test_install_never_resolves(self) -> None:
        args = install_arguments(load())
        assert "--no-deps" in args, (
            "sin --no-deps pip resolvería otra vez y reintroduciría la deriva"
        )

    def test_hashes_enable_require_hashes_only_when_complete(self) -> None:
        from runtime_lock_spec import Pinned, RuntimeLockFile

        without = RuntimeLockFile("s", "", ">=3.10", (Pinned("mcp", "1.9.0"),))
        assert "--require-hashes" not in install_arguments(without)

        with_hashes = RuntimeLockFile(
            "s", "", ">=3.10", (Pinned("mcp", "1.9.0", hashes=("a" * 64,)),))
        assert "--require-hashes" in install_arguments(with_hashes)

    def test_the_requirements_text_is_installable(self) -> None:
        text = requirements_text(load())
        assert "--no-editar" not in text
        for package in load().packages:
            assert f"{package.name}=={package.version}" in text

    @pytest.mark.parametrize(
        "version,specifier,expected",
        [
            ("1.28.1", ">=1.9,<3", True),
            ("3.0.0", ">=1.9,<3", False),
            ("1.8.0", ">=1.9,<3", False),
            ("1.9", ">=1.9,<3", True),
            ("1.9.0", ">=1.9", True),
            ("5.0.0", ">=4.0,<6", True),
            ("6.0.0", ">=4.0,<6", False),
        ],
    )
    def test_range_comparison(self, version, specifier, expected) -> None:
        assert satisfies_range(version, specifier) is expected


class TestBootstrapPurity:
    def test_the_bootstrap_modules_import_with_no_dependencies(self) -> None:
        """They run BEFORE anything is installed, so they cannot import one."""
        probe = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SCRIPTS)!r})
            # Nothing from site-packages is reachable for this check.
            import runtime_identity, runtime_lock, runtime_lock_spec
            forbidden = {{"mcp", "reportlab", "PIL", "packaging", "pydantic"}}
            leaked = forbidden & set(sys.modules)
            print("|".join(sorted(leaked)))
            """
        )
        out = subprocess.run(
            [sys.executable, "-S", "-c", probe],
            capture_output=True, text=True, timeout=60,
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "", (
            f"el bootstrap importó dependencias: {out.stdout.strip()}"
        )

    def test_the_lock_file_is_reachable_as_a_fixture(self) -> None:
        assert LOCK_PATH.is_file(), "las pruebas leen el lock del repo, nunca la red"


class TestATransientlyUnreadableLockIsNotCorruption:
    """El defecto que encontro la prueba de intervalos disjuntos.

    ``O_EXCL`` crea el archivo VACIO y el contenido se escribe justo despues.
    No son un acto atomico: un competidor que mire entre los dos pasos lee
    cero bytes. La version anterior lo declaraba corrupto y **rehusaba en el
    acto**, sin respetar el timeout que le habian pedido -asi que dos clientes
    MCP arrancando a la vez producian "el lock esta corrupto, revisalo a mano"
    sin que nada estuviera roto.

    Ahora un lock ilegible significa SEGUIR ESPERANDO. Solo el que sigue
    ilegible cuando vence el plazo se reporta como corrupto, y ni siquiera
    entonces se borra solo.
    """

    def test_an_empty_lock_file_does_not_refuse_before_the_deadline(
        self, tmp_path: Path
    ) -> None:
        """Exactamente la ventana entre O_EXCL y la escritura."""
        lock_path = tmp_path / "runtime.lock"
        lock_path.write_bytes(b"")          # creado, aun sin contenido

        guard = RuntimeLock(lock_path, "k", "0.4.0")
        began = time.monotonic()
        with pytest.raises(LockUnavailable) as caught:
            guard.acquire(timeout=0.3, poll=0.01)
        waited = time.monotonic() - began

        assert caught.value.code == CORRUPT, "sigue ilegible al vencer el plazo"
        assert waited >= 0.25, (
            f"rehuso en {waited:.3f}s con un plazo de 0.3s: no espero nada")

    def test_a_lock_completed_mid_wait_is_then_read_properly(
        self, tmp_path: Path
    ) -> None:
        """El caso real: el dueno termina de escribir y el que espera lo ve.

        Deja de ser ilegible y pasa a ser un dueno vivo identificable, que es
        un rechazo con NOMBRE en vez de un aviso de corrupcion.
        """
        lock_path = tmp_path / "runtime.lock"
        lock_path.write_bytes(b"")

        def finish_writing() -> None:
            time.sleep(0.15)
            owner = mint_owner("k", "0.4.0")
            lock_path.write_text(
                json.dumps(owner.to_json(), sort_keys=True), encoding="utf-8")

        writer = threading.Thread(target=finish_writing)
        writer.start()
        try:
            guard = RuntimeLock(lock_path, "k", "0.4.0")
            with pytest.raises(LockUnavailable) as caught:
                guard.acquire(timeout=0.6, poll=0.01)
        finally:
            writer.join()

        assert caught.value.code in (BUSY_ALIVE, BUSY_UNKNOWN), (
            f"tras completarse debe identificar al dueno, no gritar corrupcion: "
            f"{caught.value.code}")

    def test_a_lock_that_disappears_mid_wait_is_taken(self, tmp_path: Path) -> None:
        """Y si el dueno termina y lo borra, el que esperaba entra."""
        lock_path = tmp_path / "runtime.lock"
        lock_path.write_bytes(b"")

        def release_it() -> None:
            time.sleep(0.15)
            lock_path.unlink()

        releaser = threading.Thread(target=release_it)
        releaser.start()
        try:
            guard = RuntimeLock(lock_path, "k", "0.4.0")
            guard.acquire(timeout=2.0, poll=0.01)
        finally:
            releaser.join()

        assert guard.owner is not None
        assert guard.release()

    def test_garbage_that_never_becomes_valid_is_still_corrupt(
        self, tmp_path: Path
    ) -> None:
        """Tolerar lo transitorio no puede volverse tolerar cualquier cosa."""
        lock_path = tmp_path / "runtime.lock"
        lock_path.write_text("esto no es json", encoding="utf-8")

        guard = RuntimeLock(lock_path, "k", "0.4.0")
        with pytest.raises(LockUnavailable) as caught:
            guard.acquire(timeout=0.2, poll=0.01)
        assert caught.value.code == CORRUPT

    def test_a_corrupt_lock_is_never_deleted_automatically(
        self, tmp_path: Path
    ) -> None:
        """La garantia que no cambia: puede seguir sostenido por alguien vivo."""
        lock_path = tmp_path / "runtime.lock"
        lock_path.write_text("basura", encoding="utf-8")

        guard = RuntimeLock(lock_path, "k", "0.4.0")
        with pytest.raises(LockUnavailable):
            guard.acquire(timeout=0.15, poll=0.01)
        assert lock_path.exists(), "un lock ilegible no se borra solo"
        assert lock_path.read_text(encoding="utf-8") == "basura"


class TestReleaseTellsTheTruth:
    """El tercer defecto que salio de la prueba de intervalos disjuntos.

    `_force_remove` era un `unlink()` dentro de `except OSError: pass`. En
    Windows, un competidor que esta LEYENDO el lock para clasificar al dueno
    lo tiene abierto, y borrar contra un handle abierto o falla o queda
    pendiente: el nombre sigue visible. La excepcion se tragaba, `release()`
    contestaba True, y el archivo sobrevivia a todos los procesos que sabian
    de el.
    """

    def test_release_reports_whether_the_file_is_actually_gone(
        self, tmp_path: Path
    ) -> None:
        guard = RuntimeLock(tmp_path / "runtime.lock", "k", "0.4.0")
        guard.acquire(timeout=1.0)
        assert guard.release() is True
        assert not (tmp_path / "runtime.lock").exists()

    def test_a_lock_held_open_by_a_reader_is_still_removed(
        self, tmp_path: Path
    ) -> None:
        """La ventana real: alguien con el archivo abierto mientras se suelta."""
        lock_path = tmp_path / "runtime.lock"
        guard = RuntimeLock(lock_path, "k", "0.4.0")
        guard.acquire(timeout=1.0)

        opened = threading.Event()
        done = threading.Event()

        def hold_it_open() -> None:
            with open(lock_path, "rb"):
                opened.set()
                done.wait(1.0)

        reader = threading.Thread(target=hold_it_open)
        reader.start()
        opened.wait(1.0)
        try:
            # Se suelta el evento enseguida: el borrado insiste durante unos
            # milisegundos, que es cuanto vive el handle de un lector.
            threading.Timer(0.05, done.set).start()
            assert guard.release() is True, "no logro borrar el lock"
        finally:
            done.set()
            reader.join(2.0)
        assert not lock_path.exists()

    def test_it_gives_up_bounded_rather_than_blocking(self, tmp_path: Path) -> None:
        """Insistir no puede convertirse en esperar para siempre."""
        guard = RuntimeLock(tmp_path / "runtime.lock", "k", "0.4.0")
        guard.acquire(timeout=1.0)
        began = time.monotonic()
        guard._force_remove(attempts=3, pause=0.01)
        assert time.monotonic() - began < 1.0, "el borrado no puede bloquear"
