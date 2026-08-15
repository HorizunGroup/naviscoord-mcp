"""Untrusted model text must never become markup, a formula, or a path.

Every payload here is something a Revit family, type or system name can
legally be called, so none of it can be rejected at the door — it has to
survive to the report as *text*.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest
from naviscoord.paths import (
    ROOTS_ENV,
    OutputPolicy,
    PathPolicyError,
    _reject_hostile_shape,
    policy,
    reset_policy,
)
from naviscoord.safety import bold, coloured, escape_markup, sanitize_csv, sanitize_row

# The payloads named in the brief, plus the ones that broke the first fix.
HOSTILE_MARKUP = [
    '<img src="file:///C:/Windows/win.ini">',
    "<b>texto</b>",
    '<a href="file://C:/Windows/win.ini">click</a>',
    r"<a href='\\attacker\share\loot'>x</a>",
    r"\\attacker\share\payload.exe",
    "<para><font color='red'>x</font></para>",
    "onload=alert(1)",
    "Tubería & Ducto <Tipo>",
    "5 < 7 && 8 > 2",
    '"comillas" y \'apóstrofes\'',
]


class TestMarkupEscaping:
    @pytest.mark.parametrize("payload", HOSTILE_MARKUP)
    def test_no_tag_survives(self, payload: str) -> None:
        escaped = escape_markup(payload)
        assert "<" not in escaped
        assert ">" not in escaped

    @pytest.mark.parametrize("payload", HOSTILE_MARKUP)
    def test_reportlab_renders_it_as_text(self, payload: str) -> None:
        """The real proof: reportlab parses the result and finds no elements."""
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph

        style = getSampleStyleSheet()["Normal"]
        para = Paragraph(escape_markup(payload), style)
        # `text` is what reportlab kept after parsing. If a tag had been
        # honoured it would have become a frag/attribute instead of text.
        rendered = "".join(frag.text for frag in para.frags)
        assert rendered == payload

    def test_unescaped_payload_would_be_interpreted(self) -> None:
        """Guards the guard: without escaping, reportlab really does parse it."""
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph

        style = getSampleStyleSheet()["Normal"]
        para = Paragraph("<b>texto</b>", style)
        rendered = "".join(frag.text for frag in para.frags)
        # The tag was consumed: the literal angle brackets are gone.
        assert rendered == "texto"
        assert para.frags[0].bold

    def test_unc_path_is_text_not_a_link(self) -> None:
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph

        style = getSampleStyleSheet()["Normal"]
        payload = r'<a href="\\evil\share">Muro</a>'
        para = Paragraph(escape_markup(payload), style)
        rendered = "".join(frag.text for frag in para.frags)
        assert rendered == payload
        assert not any(getattr(frag, "link", None) for frag in para.frags)

    def test_ampersand_is_escaped_once(self) -> None:
        assert escape_markup("A & B") == "A &amp; B"
        assert escape_markup("&amp;") == "&amp;amp;"

    def test_control_characters_are_dropped(self) -> None:
        assert escape_markup("a\x00b\x07c") == "abc"
        assert escape_markup("línea\nsigue") == "línea\nsigue"

    def test_helpers_keep_their_own_tag_and_escape_the_value(self) -> None:
        assert bold("<b>x</b>") == "<b>&lt;b&gt;x&lt;/b&gt;</b>"
        assert coloured("x", "#B3261E") == '<font color="#B3261E">x</font>'

    def test_colour_attribute_cannot_be_injected(self) -> None:
        out = coloured("x", '"><img src=y onerror=z>')
        assert out == '<font color="#000000">x</font>'

    def test_none_and_numbers_are_accepted(self) -> None:
        assert escape_markup(None) == ""
        assert escape_markup(42) == "42"


class TestCsvInjection:
    @pytest.mark.parametrize(
        "payload",
        [
            "=cmd|'/c calc'!A1",
            "+1+1",
            "@SUM(A1:A9)",
            "-2+3+cmd|'/c calc'!A0",
            "\t=1+1",
            "\r=1+1",
            "   =HYPERLINK(\"http://evil\",\"click\")",
        ],
    )
    def test_formula_leads_are_neutralised(self, payload: str) -> None:
        out = sanitize_csv(payload)
        assert out.startswith("'")
        assert out[1:] == payload

    @pytest.mark.parametrize(
        "payload",
        ["Muro básico 200mm", "", "ARQ", "Tubo PVC 4\"", "A-B-C", "x=y"],
    )
    def test_ordinary_values_are_untouched(self, payload: str) -> None:
        assert sanitize_csv(payload) == payload

    @pytest.mark.parametrize(
        "payload", ["-3.5", "-12", "-0.001", "+7", "1e-3", "-2.5E+4", ".5", "-.75"]
    )
    def test_negative_numbers_survive_intact(self, payload: str) -> None:
        """Coordinates and depths are routinely negative; mangling them
        would corrupt the Power BI model far more reliably than any macro."""
        assert sanitize_csv(payload) == payload

    @pytest.mark.parametrize(
        "payload", ["-inf", "+nan", "-Infinity", "-1_0", "+1_000", "- 5"]
    )
    def test_things_float_would_accept_are_still_neutralised(self, payload: str) -> None:
        """`float()` used to decide "is this a number?" and is too generous.

        None of these execute in a spreadsheet, but the numeric check is what
        lets a value SKIP neutralisation, so it is held to a plain decimal.
        """
        assert sanitize_csv(payload).startswith("'")

    @pytest.mark.parametrize(
        "payload", ["\n=1+1", "  \t\r\n =cmd|'/c calc'!A1", "\r\n@SUM(A1)"]
    )
    def test_leading_whitespace_of_any_kind_does_not_hide_a_formula(
        self, payload: str
    ) -> None:
        """Spreadsheets strip leading whitespace before parsing the cell."""
        assert sanitize_csv(payload).startswith("'")

    def test_native_numbers_pass_through(self) -> None:
        assert sanitize_csv(-3.5) == -3.5
        assert sanitize_csv(7) == 7
        assert sanitize_csv(None) is None

    def test_row_helper_touches_values_only(self) -> None:
        out = sanitize_row({"=name": "=danger", "ok": "fine"})
        assert out == {"=name": "'=danger", "ok": "fine"}

    def test_written_csv_is_inert(self, tmp_path: Path) -> None:
        from naviscoord.interop import _write_csv

        target = tmp_path / "t.csv"
        _write_csv(target, [{"element": "=cmd|'/c calc'!A1", "z": -3.5}])
        rows = list(csv.reader(target.read_text(encoding="utf-8-sig").splitlines()))
        assert rows[0] == ["element", "z"]
        assert rows[1][0].startswith("'")
        assert rows[1][1] == "-3.5"


class TestOutputPolicy:
    @pytest.fixture
    def sandbox(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OutputPolicy:
        root = tmp_path / "salidas"
        root.mkdir()
        monkeypatch.setenv(ROOTS_ENV, str(root))
        reset_policy()
        yield OutputPolicy([root.resolve()])
        reset_policy()

    def test_file_inside_root_is_allowed(self, sandbox: OutputPolicy) -> None:
        out = sandbox.resolve_file(sandbox.roots[0] / "sub" / "informe.pdf")
        assert out.path.parent.exists()
        assert out.existed is False

    def test_relative_path_lands_in_the_first_root(self, sandbox: OutputPolicy) -> None:
        out = sandbox.resolve_file("informe.pdf")
        assert out.path == sandbox.roots[0] / "informe.pdf"

    def test_traversal_is_refused(self, sandbox: OutputPolicy) -> None:
        with pytest.raises(PathPolicyError, match="fuera de las rutas"):
            sandbox.resolve_file(sandbox.roots[0] / ".." / ".." / "escapado.pdf")

    def test_traversal_by_relative_string_is_refused(self, sandbox: OutputPolicy) -> None:
        with pytest.raises(PathPolicyError):
            sandbox.resolve_file("../../escapado.pdf")

    def test_unauthorised_absolute_path_is_refused(self, sandbox: OutputPolicy) -> None:
        with pytest.raises(PathPolicyError, match="fuera de las rutas"):
            sandbox.resolve_file(r"C:\Windows\Temp\x.pdf" if os.name == "nt" else "/etc/x.pdf")

    @pytest.mark.parametrize(
        "hostile",
        [
            r"\\attacker\share\loot.pdf",
            r"\\.\PhysicalDrive0",
            r"\\?\C:\Windows\x.pdf",
            "//attacker/share/loot.pdf",
        ],
    )
    def test_unc_and_device_paths_are_refused(self, sandbox: OutputPolicy, hostile: str) -> None:
        with pytest.raises(PathPolicyError, match="red o de dispositivo"):
            sandbox.resolve_file(hostile)

    def test_reserved_device_name_is_refused(self, sandbox: OutputPolicy) -> None:
        with pytest.raises(PathPolicyError, match="dispositivo reservado"):
            sandbox.resolve_file(sandbox.roots[0] / "NUL.pdf")

    @pytest.mark.parametrize(
        "hostile",
        [
            r"C:\salida\NUL.pdf",
            "/salida/NUL.pdf",
            r"C:\salida\con.pdf",
            "/salida/PRN.pdf",
            r"C:\salida\LPT1.nwf",
            "/salida/aux.csv",
        ],
    )
    def test_device_names_are_refused_on_every_platform(self, hostile: str) -> None:
        """The check has to work off Windows, because CI is off Windows.

        It did not: the separator is normalised to backslash and then split
        with `PurePath`, which on POSIX is `PurePosixPath` and does not treat
        backslash as a separator — so the whole path came back as a single
        component and no device name ever matched. The guard silently did
        nothing on Linux, and the suite only ran on Windows until this branch
        reached CI, so nothing said so.

        Driven through the shape check directly rather than through a policy,
        so the assertion is about the rule and not about which roots happen
        to be authorised on the machine running it.
        """
        with pytest.raises(PathPolicyError, match="dispositivo reservado"):
            _reject_hostile_shape(hostile)

    @pytest.mark.parametrize(
        "benign",
        [r"C:\salida\nulo.pdf", "/salida/console.pdf", r"C:\salida\auxiliar\informe.pdf"],
    )
    def test_names_that_merely_start_like_a_device_are_allowed(self, benign: str) -> None:
        """«nulo» is not «nul»; refusing it would be its own bug."""
        _reject_hostile_shape(benign)

    def test_alternate_data_stream_is_refused(self, sandbox: OutputPolicy) -> None:
        with pytest.raises(PathPolicyError, match="flujo alterno"):
            sandbox.resolve_file(str(sandbox.roots[0] / "informe.pdf") + ":oculto")

    def test_null_byte_is_refused(self, sandbox: OutputPolicy) -> None:
        with pytest.raises(PathPolicyError, match="nulo"):
            sandbox.resolve_file("informe\x00.pdf")

    def test_existing_file_needs_explicit_overwrite(self, sandbox: OutputPolicy) -> None:
        target = sandbox.roots[0] / "ya.pdf"
        target.write_text("x", encoding="utf-8")
        with pytest.raises(PathPolicyError, match="ya existe"):
            sandbox.resolve_file(target)
        allowed = sandbox.resolve_file(target, overwrite=True)
        assert allowed.existed is True

    def test_directory_target_is_refused_as_a_file(self, sandbox: OutputPolicy) -> None:
        (sandbox.roots[0] / "carpeta").mkdir()
        with pytest.raises(PathPolicyError, match="es una carpeta"):
            sandbox.resolve_file(sandbox.roots[0] / "carpeta")

    def test_dir_resolution_creates_and_authorises(self, sandbox: OutputPolicy) -> None:
        out = sandbox.resolve_dir(sandbox.roots[0] / "handoff")
        assert out.path.is_dir()
        with pytest.raises(PathPolicyError):
            sandbox.resolve_dir(sandbox.roots[0] / ".." / "fuera")

    def test_late_confirmation_catches_a_file_that_appeared_meanwhile(
        self, sandbox: OutputPolicy
    ) -> None:
        """Authorising and writing are not the same instant.

        The PDF authorises its destination, then spends minutes asking
        Navisworks to render one image per issue, and only then writes. A file
        created during that window would be replaced by a caller who had
        explicitly asked not to overwrite anything.
        """
        target = sandbox.roots[0] / "informe.pdf"
        authorised = sandbox.resolve_file(target)
        assert authorised.existed is False

        target.write_text("lo creó otro proceso", encoding="utf-8")

        with pytest.raises(PathPolicyError, match="apareció mientras"):
            authorised.confirm()

    def test_late_confirmation_is_silent_when_nothing_changed(
        self, sandbox: OutputPolicy
    ) -> None:
        sandbox.resolve_file(sandbox.roots[0] / "informe.pdf").confirm()

    def test_late_confirmation_respects_an_explicit_overwrite(
        self, sandbox: OutputPolicy
    ) -> None:
        target = sandbox.roots[0] / "informe.pdf"
        target.write_text("previo", encoding="utf-8")
        sandbox.resolve_file(target, overwrite=True).confirm()

    def test_root_comparison_ignores_case_on_windows(self, sandbox: OutputPolicy) -> None:
        """A Windows path differing only in case is the same path.

        Getting this wrong fails closed — a legitimate destination refused —
        which is safe but makes the policy look broken to whoever typed the
        drive letter in caps.
        """
        if os.name != "nt":
            pytest.skip("semántica de Windows")
        shouty = Path(str(sandbox.roots[0]).upper()) / "informe.pdf"
        assert sandbox.resolve_file(shouty).path.exists() is False  # authorised, not written

    def test_two_writers_racing_for_one_name_produce_one_winner(
        self, sandbox: OutputPolicy
    ) -> None:
        """The guarantee `exists()`-then-write could never make.

        Both threads authorise the same destination before either writes, so
        both see "does not exist". Only the exclusive reservation decides the
        winner, and the loser must be refused rather than silently overwrite.
        """
        import threading

        target = sandbox.roots[0] / "disputado.json"
        start = threading.Barrier(2)
        outcomes: list[tuple[str, str]] = []
        lock = threading.Lock()

        def writer(tag: str) -> None:
            slot = sandbox.resolve_file(target)   # both authorise first
            start.wait(timeout=5)
            try:
                slot.write_text(f"escrito por {tag}")
                result = ("ganó", tag)
            except PathPolicyError:
                result = ("rechazado", tag)
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=writer, args=(t,)) for t in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        verdicts = sorted(v for v, _ in outcomes)
        assert verdicts == ["ganó", "rechazado"], f"resultados: {outcomes}"

        winner = next(tag for verdict, tag in outcomes if verdict == "ganó")
        assert target.read_text(encoding="utf-8") == f"escrito por {winner}", (
            "el contenido debe ser el del ganador, no una mezcla ni el del perdedor"
        )

    def test_a_failed_generation_leaves_no_debris(self, sandbox: OutputPolicy) -> None:
        """A crash mid-write must not squat on the name with a 0-byte file."""
        target = sandbox.roots[0] / "reventado.pdf"
        slot = sandbox.resolve_file(target)

        with (
            pytest.raises(RuntimeError, match="falló la generación"),
            slot.staged_write() as temp,
        ):
            temp.write_bytes(b"parcial")
            raise RuntimeError("falló la generación")

        assert not target.exists(), "la reserva debe liberarse al fallar"
        leftovers = [p.name for p in sandbox.roots[0].iterdir() if p.name.endswith(".tmp")]
        assert not leftovers, f"temporales sin limpiar: {leftovers}"

        # And the name is free again for a later attempt.
        sandbox.resolve_file(target).write_text("segundo intento")
        assert target.read_text(encoding="utf-8") == "segundo intento"

    def test_the_destination_is_never_seen_half_written(
        self, sandbox: OutputPolicy
    ) -> None:
        """Publication is a rename, so a reader sees old or new, never partial."""
        target = sandbox.roots[0] / "informe.json"
        sandbox.resolve_file(target).write_text("v1")

        slot = sandbox.resolve_file(target, overwrite=True)
        with slot.staged_write() as temp:
            temp.write_text("v2 a medias", encoding="utf-8")
            # Mid-generation, the destination still holds the previous version.
            assert target.read_text(encoding="utf-8") == "v1"
        assert target.read_text(encoding="utf-8") == "v2 a medias"

    def test_overwrite_true_replaces_atomically(self, sandbox: OutputPolicy) -> None:
        target = sandbox.roots[0] / "informe.json"
        target.write_text("previo", encoding="utf-8")
        sandbox.resolve_file(target, overwrite=True).write_text("nuevo")
        assert target.read_text(encoding="utf-8") == "nuevo"

    def test_handoff_artifacts_are_published_atomically(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second handoff into the same directory is refused, not merged."""
        from naviscoord.analysis.pipeline import AnalysisResult
        from naviscoord.interop import write_handoff
        from naviscoord.model import ClashExport
        from naviscoord.profile import Profile

        root = tmp_path / "salidas"
        root.mkdir()
        monkeypatch.setenv(ROOTS_ENV, str(root))
        reset_policy()
        try:
            out = write_handoff(
                root / "entrega", AnalysisResult(profile_name="t"),
                ClashExport(), Profile.load(), overwrite=False,
            )
            assert out["written"], "debe escribir artefactos"
            for written in out["written"]:
                assert Path(written).exists()
            assert not list((root / "entrega").glob("*.tmp"))

            with pytest.raises(PathPolicyError, match="ya existe"):
                write_handoff(
                    root / "entrega", AnalysisResult(profile_name="t"),
                    ClashExport(), Profile.load(), overwrite=False,
                )
        finally:
            reset_policy()

    def test_default_root_always_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ROOTS_ENV, raising=False)
        reset_policy()
        assert policy().roots, "debe existir al menos una raíz por defecto"
        reset_policy()

    def test_policy_cache_follows_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = tmp_path / "uno"
        first.mkdir()
        monkeypatch.setenv(ROOTS_ENV, str(first))
        reset_policy()
        assert first.resolve() in policy().roots

        second = tmp_path / "dos"
        second.mkdir()
        monkeypatch.setenv(ROOTS_ENV, str(second))
        assert second.resolve() in policy().roots
        reset_policy()


class TestReportHonoursPolicy:
    def test_pdf_outside_root_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from naviscoord.analysis.pipeline import AnalysisResult
        from naviscoord.profile import Profile
        from naviscoord.report import build_report

        root = tmp_path / "ok"
        root.mkdir()
        monkeypatch.setenv(ROOTS_ENV, str(root))
        reset_policy()
        try:
            with pytest.raises(PathPolicyError):
                build_report(
                    tmp_path / "fuera.pdf",
                    AnalysisResult(profile_name="test"),
                    Profile.load(),
                )
        finally:
            reset_policy()

    def test_pdf_inside_root_is_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from naviscoord.analysis.pipeline import AnalysisResult
        from naviscoord.profile import Profile
        from naviscoord.report import build_report

        root = tmp_path / "ok"
        root.mkdir()
        monkeypatch.setenv(ROOTS_ENV, str(root))
        reset_policy()
        try:
            out = build_report(
                root / "informe.pdf",
                AnalysisResult(profile_name="test"),
                Profile.load(),
            )
            assert Path(out["path"]).exists()
            assert out["overwrote_existing"] is False
        finally:
            reset_policy()
