"""Image options, quality gating, the re-shoot ladder, and the PDF's account.

The failure this file exists to prevent is not an exception — it is a report
full of technically valid pictures that do not show the clash. So the
assertions are about *pixels and counts*, never about "the call succeeded":
a fake bridge returns renders with known content, and the gate has to reach
the same verdict a human would on looking at them.

Nothing here needs Navisworks. The camera arithmetic lives in the add-in and is
asserted by `NavisCoord.Tests`; what lives here is everything that decides
which picture to ask for, whether the one that came back is usable, and what to
try next when it is not.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest
from naviscoord import samples as synth
from naviscoord.analysis.pipeline import analyze
from naviscoord.imaging import (
    COLOUR_FIXED,
    COLOUR_MOVABLE,
    Capture,
    ImageOptions,
    PixelMetrics,
    Verdict,
    analyse_pixels,
    annotate,
    capture,
    colours_for,
    judge,
    next_attempt,
    palette_for,
    to_hex,
)
from naviscoord.model import ClashExport
from naviscoord.profile import Profile
from PIL import Image

# ------------------------------------------------------------- fixtures

GREY = (176, 176, 176)


def render(
    *,
    side_a: tuple[int, int, int] | None = COLOUR_MOVABLE,
    side_b: tuple[int, int, int] | None = COLOUR_FIXED,
    a_size: int = 60,
    b_size: int = 60,
    size: tuple[int, int] = (300, 200),
    background: tuple[int, int, int] = (210, 220, 235),
) -> bytes:
    """A stand-in for a Navisworks render, with known content.

    Two coloured blocks on a gradient sky and a grey slab of context, which is
    the composition of every real clash image: the point of the fixture is that
    the test KNOWS whether each element is present, so the gate's verdict can
    be compared against the truth rather than against itself.
    """
    image = Image.new("RGB", size, background)
    pixels = image.load()
    # A gradient, so a blank-detector that just counts colours cannot pass an
    # empty sky by accident.
    for y in range(size[1]):
        shade = tuple(max(0, min(255, c - y // 12)) for c in background)
        for x in range(size[0]):
            pixels[x, y] = shade

    for x in range(20, size[0] - 20):
        for y in range(size[1] - 40, size[1] - 15):
            pixels[x, y] = GREY  # neutral context

    if side_a:
        for x in range(30, 30 + a_size):
            for y in range(40, 40 + a_size):
                # Shaded, not flat: a real render darkens a face towards black
                # and the classifier has to survive that.
                factor = 0.45 + 0.55 * ((x - 30) / max(1, a_size))
                pixels[x, y] = tuple(int(c * factor) for c in side_a)
    if side_b:
        for x in range(size[0] - 30 - b_size, size[0] - 30):
            for y in range(60, 60 + b_size):
                pixels[x, y] = side_b

    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _blend(
    colour: tuple[int, int, int], veil: tuple[int, int, int], amount: float
) -> tuple[int, int, int]:
    """A colour seen through translucent geometry, which is the normal case.

    `amount` is how much of the veil ends up in the pixel: 0.5 is a single
    faded layer in front, which is what the live render produced.
    """
    return tuple(
        int(round(colour[i] * (1 - amount) + veil[i] * amount)) for i in range(3)
    )


def coverage(
    *,
    a_in: bool = True,
    b_in: bool = True,
    clash_contained: bool = True,
    smallest: float = 0.05,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "side_a": {"in_frame": a_in, "contained": a_in, "screen_fraction": smallest},
        "side_b": {"in_frame": b_in, "contained": b_in, "screen_fraction": smallest},
        "clash": {"in_frame": clash_contained, "contained": clash_contained},
        "both_sides_in_frame": a_in and b_in,
        "clash_contained": clash_contained,
        "smallest_side_fraction": smallest,
        "ok": a_in and b_in and clash_contained,
        "reasons": reasons or [],
    }


class FakeBridge:
    """Answers `clash/image` from a script, and records what it was asked.

    The script is a list of outcomes, one per attempt, so a test can say "the
    first frame comes back with the pipe hidden behind a wall and the second,
    with the wall gone, does not" — which is the behaviour the ladder exists
    for and cannot be tested any other way.
    """

    def __init__(self, script: list[dict[str, Any]]):
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, guid: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(payload, clash_guid=guid))
        outcome = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if outcome.get("error"):
            return {"error": outcome["error"], "detail": "sin detalle"}

        import base64

        png = outcome.get("png")
        return {
            "clash_guid": guid,
            "width": 300,
            "height": 200,
            "format": "png",
            "base64": base64.b64encode(png).decode() if png else "",
            "coverage": outcome.get("coverage", coverage()),
            "framing": outcome.get("framing", {"mode": payload.get("camera_mode")}),
            "location": {"grid_reference": "C-4 : Nivel 3"},
            "document": {"modified_before": False, "modified_after": False},
            "visual": outcome.get("visual", {"hidden_items": 7, "isolated": True}),
            "notes": outcome.get("notes", ["isolation_level_too_wide"]),
            "sides": {
                "a": {"items": 1, "color": list(outcome.get("colour_a", COLOUR_MOVABLE))},
                "b": {"items": 1, "color": list(outcome.get("colour_b", COLOUR_FIXED))},
            },
        }


# --------------------------------------------------------------- options


class TestOptions:
    def test_defaults_are_the_documented_ones(self) -> None:
        options = ImageOptions()
        assert options.camera_mode == "closeup"
        assert options.margin_percent == 25.0
        assert options.render_quality == "standard"
        assert options.colorize_by_discipline is True
        assert options.keep_rejected is False, "una imagen mala no se publica por defecto"

    def test_out_of_range_values_are_clamped_not_rejected(self) -> None:
        """A report is not worth failing over a silly number.

        It IS worth reporting the number that was used: the effective options
        travel back to the caller, so a clamp that lied would be worse than an
        error.
        """
        options = ImageOptions(
            margin_percent=9000, min_distance=-5, max_distance=1, image_width=99999,
            max_attempts=50,
        ).normalised()
        assert options.margin_percent == 400.0
        assert options.min_distance == 0.05
        assert options.max_distance >= options.min_distance
        assert options.image_width == 2400
        assert options.max_attempts == 5

    def test_unknown_mode_and_quality_fall_back(self) -> None:
        options = ImageOptions(camera_mode="cinematic", render_quality="ultra").normalised()
        assert options.camera_mode == "closeup"
        assert options.render_quality == "standard"

    def test_spanish_mode_names_are_accepted(self) -> None:
        assert ImageOptions(camera_mode="planta").normalised().camera_mode == "plan"
        assert ImageOptions(camera_mode="  Contexto ").normalised().camera_mode == "context"

    def test_garbage_does_not_raise(self) -> None:
        options = ImageOptions(margin_percent="mucho", min_distance=None).normalised()  # type: ignore[arg-type]
        assert options.margin_percent == 0.0
        assert options.min_distance == 0.05

    def test_payload_carries_the_colours_and_omits_untouched_toggles(self) -> None:
        payload = ImageOptions().normalised().payload((1, 2, 3), (4, 5, 6), "EST", "HVAC")
        assert payload["color_a"] == "#010203"
        assert payload["discipline_b"] == "HVAC"
        assert "show_grid" not in payload, "sin pedirlo, la rejilla no se toca"
        assert "background_color" not in payload

    def test_background_always_travels_with_a_colour_to_restore(self) -> None:
        """The 2026 API can set a background and cannot read one.

        Sending the restore colour with the request is the only thing that
        stops a report run leaving somebody's model with a white sky.
        """
        payload = ImageOptions(background_color="#FFFFFF").normalised().payload(
            COLOUR_MOVABLE, COLOUR_FIXED)
        assert payload["background_color"] == "#FFFFFF"
        assert payload["background_restore_color"]


# --------------------------------------------------------------- colours


class TestColours:
    def test_a_discipline_keeps_its_colour_between_runs(self) -> None:
        """Not `hash()`: that is randomised per process.

        A palette built on it gives the ducts one colour today and another
        tomorrow, and two reports of the same model stop being comparable.
        Pinned to literal values so that "stable" is asserted against the
        record rather than against another call in the same process, which
        `hash()` would also have satisfied.
        """
        assert to_hex(palette_for("HVAC")) == "#F5A623"
        assert to_hex(palette_for("EST")) == "#7B1FA2"
        assert to_hex(palette_for("ARQ")) == "#1E88E5"
        assert palette_for("") == (176, 176, 176), "sin disciplina, gris neutro"

    def test_the_two_sides_never_share_a_colour(self) -> None:
        for a, b in [("EST", "HVAC"), ("HID", "RCI"), ("ARQ", "ELE"), ("A", "B")]:
            first, second = colours_for(a, b, "", by_discipline=True)
            assert first != second, f"{a} y {b} salen del mismo color"

    def test_movability_scheme_paints_the_side_that_moves(self) -> None:
        a, b = colours_for("HVAC", "EST", responsible="HVAC", by_discipline=False)
        assert (a, b) == (COLOUR_MOVABLE, COLOUR_FIXED)
        a, b = colours_for("HVAC", "EST", responsible="EST", by_discipline=False)
        assert (a, b) == (COLOUR_FIXED, COLOUR_MOVABLE)

    def test_unattributable_movability_does_not_guess(self) -> None:
        """The bug that painted the wrong half, in its general form.

        When the responsible trade cannot be located on a side, both sides get
        the alert colour. Colouring the whole interference is honest; colouring
        one half on a coin flip is not.
        """
        a, b = colours_for("HVAC", "HVAC", responsible="HVAC", by_discipline=False)
        assert a == b == COLOUR_MOVABLE
        a, b = colours_for("HVAC", "EST", responsible="", by_discipline=False)
        assert a == b == COLOUR_MOVABLE

    def test_hex_round_trip(self) -> None:
        assert to_hex((214, 40, 40)) == "#D62828"
        assert to_hex((-5, 300, 0)) == "#00FF00", "los valores fuera de rango se acotan"


# ---------------------------------------------------------------- pixels


class TestPixelAnalysis:
    def test_both_elements_are_counted(self) -> None:
        metrics = analyse_pixels(render(), COLOUR_MOVABLE, COLOUR_FIXED)
        assert metrics.decoded
        assert metrics.side_a_pixels > 0
        assert metrics.side_b_pixels > 0
        assert not metrics.blank

    def test_shading_does_not_erase_an_element(self) -> None:
        """The reason the classifier works on chromaticity.

        The fixture darkens side A across its width, exactly as a lit face
        fading into shadow. A nearest-RGB match finds the lit end and loses the
        rest, which understates a large element badly enough to reject a good
        picture.
        """
        metrics = analyse_pixels(render(a_size=80), COLOUR_MOVABLE, COLOUR_FIXED)
        assert metrics.side_a_pixels > 80 * 80 * 0.8, (
            f"solo se contaron {metrics.side_a_pixels} de 6400 píxeles sombreados"
        )

    def test_an_element_behind_the_faded_context_is_still_counted(self) -> None:
        """The failure that rejected six good pictures of a live federation.

        With the context faded, the two clashing elements are seen THROUGH a
        translucent layer, so every one of their pixels is a blend towards the
        pale background. A classifier working on channel ratios measured a
        clearly visible red pipe at 0.01% of the frame and the gate threw the
        image away. Hue barely moves under that blend — the washed pipe below
        sits nine degrees from pure red — so it survives.
        """
        veil = (200, 210, 230)
        washed_a = _blend(COLOUR_MOVABLE, veil, 0.5)
        washed_b = _blend(COLOUR_FIXED, veil, 0.5)
        metrics = analyse_pixels(
            render(side_a=washed_a, side_b=washed_b, a_size=50, b_size=50),
            COLOUR_MOVABLE, COLOUR_FIXED,
        )
        assert metrics.side_a_fraction > 0.01, f"lado A perdido: {metrics.side_a_fraction}"
        assert metrics.side_b_fraction > 0.01, f"lado B perdido: {metrics.side_b_fraction}"

    def test_a_heavier_veil_still_keeps_the_element(self) -> None:
        """Twice the fade of the live render, still counted.

        The measured elements held 0.49 saturation behind one faded layer; this
        is the margin above that, not the limit of what is realistic.
        """
        veil = (200, 210, 230)
        metrics = analyse_pixels(
            render(side_a=_blend(COLOUR_MOVABLE, veil, 0.6),
                   side_b=_blend(COLOUR_FIXED, veil, 0.6), a_size=50, b_size=50),
            COLOUR_MOVABLE, COLOUR_FIXED,
        )
        assert metrics.side_a_fraction > 0.005
        assert metrics.side_b_fraction > 0.005

    def test_the_pale_sky_is_not_counted_as_a_blue_element(self) -> None:
        """The other half of the trade-off, and the worse failure of the two.

        The default Navisworks background is a pale blue, and the palette has a
        blue in it. A tolerance loose enough to survive the fade started
        counting the sky as the element — so every image passed whether the
        element was in it or not, which is a check that has stopped checking.

        The background here is the one measured off the live render.
        """
        metrics = analyse_pixels(
            render(side_a=None, side_b=None, background=(200, 210, 230)),
            COLOUR_MOVABLE, COLOUR_FIXED,
        )
        assert metrics.side_a_pixels == 0
        assert metrics.side_b_pixels == 0

    def test_a_blue_element_is_still_found_against_that_sky(self) -> None:
        """And the check must not have been fixed by turning blue off."""
        metrics = analyse_pixels(
            render(side_a=None, side_b=COLOUR_FIXED, b_size=50, background=(200, 210, 230)),
            COLOUR_MOVABLE, COLOUR_FIXED,
        )
        assert metrics.side_b_fraction > 0.01

    def test_an_overexposed_element_is_still_its_own_colour(self) -> None:
        """The third way a valid render hides an element from the check.

        A surface facing the headlight clips its strongest channels at 255, and
        once two of the three are pinned there the hue slides. Measured on a
        plan view of a real clash: a duct painted (245, 166, 35) — hue 37° —
        came back as (255, 255, 140), hue 60°. The classifier scored 7,704
        pixels of it as background and the picture was rejected for an element
        plainly in it.

        Shading and whitening leave hue alone; clipping does not, so the check
        models the exposure forwards instead of pretending it cannot happen.
        """
        blown = (255, 255, 140)  # the literal pixel measured off the render
        hvac = palette_for("HVAC")
        assert hvac == (245, 166, 35)

        metrics = analyse_pixels(
            render(side_a=blown, side_b=None, a_size=60), hvac, COLOUR_FIXED)
        assert metrics.side_a_fraction > 0.01, (
            f"el elemento sobreexpuesto se perdió: {metrics.side_a_fraction}")

    def test_overexposure_does_not_leak_one_side_into_the_other(self) -> None:
        """The arcs must not swallow the other element's colour.

        Red clips to (255, g, g) and stays at hue 0 until it turns white, so a
        blown red must never be counted as the orange duct next to it.
        """
        metrics = analyse_pixels(
            render(side_a=(255, 90, 90), side_b=None, a_size=60),
            palette_for("HVAC"), COLOUR_MOVABLE,
        )
        assert metrics.side_a_pixels == 0, "rojo quemado contado como naranja"
        assert metrics.side_b_fraction > 0.01, "rojo quemado no contado como rojo"

    def test_grey_context_is_never_mistaken_for_an_element(self) -> None:
        metrics = analyse_pixels(render(side_a=None, side_b=None), COLOUR_MOVABLE, COLOUR_FIXED)
        assert metrics.side_a_pixels == 0
        assert metrics.side_b_pixels == 0

    def test_a_missing_element_reads_as_missing(self) -> None:
        metrics = analyse_pixels(render(side_b=None), COLOUR_MOVABLE, COLOUR_FIXED)
        assert metrics.side_a_pixels > 0
        assert metrics.side_b_pixels == 0

    def test_a_flat_image_is_blank(self) -> None:
        """The camera inside the geometry: one surface filling the frame."""
        flat = Image.new("RGB", (120, 80), (90, 90, 90))
        buffer = BytesIO()
        flat.save(buffer, format="PNG")
        metrics = analyse_pixels(buffer.getvalue(), COLOUR_MOVABLE, COLOUR_FIXED)
        assert metrics.blank

    def test_an_undecodable_payload_is_reported_not_raised(self) -> None:
        metrics = analyse_pixels(b"no soy un png", COLOUR_MOVABLE, COLOUR_FIXED)
        assert not metrics.decoded
        assert metrics.error
        assert metrics.blank

    def test_empty_payload(self) -> None:
        metrics = analyse_pixels(b"", COLOUR_MOVABLE, COLOUR_FIXED)
        assert not metrics.decoded

    def test_identical_side_colours_count_for_both(self) -> None:
        """The unattributable case must not read as "side B is missing"."""
        png = render(side_a=COLOUR_MOVABLE, side_b=COLOUR_MOVABLE)
        metrics = analyse_pixels(png, COLOUR_MOVABLE, COLOUR_MOVABLE)
        assert metrics.side_a_pixels > 0 and metrics.side_b_pixels > 0

    def test_large_images_are_sampled_not_walked(self) -> None:
        big = render(size=(1600, 1200), a_size=200, b_size=200)
        metrics = analyse_pixels(big, COLOUR_MOVABLE, COLOUR_FIXED)
        assert metrics.width == 1600 and metrics.height == 1200
        assert metrics.sampled < 1600 * 1200
        assert metrics.side_a_fraction > 0.01


# --------------------------------------------------------------- verdict


class TestVerdict:
    def test_a_good_image_passes(self) -> None:
        metrics = analyse_pixels(render(), COLOUR_MOVABLE, COLOUR_FIXED)
        assert judge(coverage(), metrics, ImageOptions()).ok

    def test_an_element_out_of_frame_fails(self) -> None:
        metrics = analyse_pixels(render(), COLOUR_MOVABLE, COLOUR_FIXED)
        verdict = judge(coverage(b_in=False), metrics, ImageOptions())
        assert not verdict.ok
        assert "side_b_out_of_frame" in verdict.reasons

    def test_an_occluded_element_fails_even_with_a_perfect_camera(self) -> None:
        """The check no amount of geometry can make.

        The camera framed both boxes — the coverage says so — and the element
        still is not in the picture, because something opaque is in front of
        it. Only the pixels know.
        """
        metrics = analyse_pixels(render(side_b=None), COLOUR_MOVABLE, COLOUR_FIXED)
        verdict = judge(coverage(), metrics, ImageOptions())
        assert not verdict.ok
        assert verdict.reasons == ["side_b_not_visible"]

    def test_a_clipped_clash_fails(self) -> None:
        metrics = analyse_pixels(render(), COLOUR_MOVABLE, COLOUR_FIXED)
        verdict = judge(coverage(clash_contained=False), metrics, ImageOptions())
        assert "clash_not_contained" in verdict.reasons

    def test_a_too_small_element_is_taken_from_the_addin(self) -> None:
        metrics = analyse_pixels(render(), COLOUR_MOVABLE, COLOUR_FIXED)
        verdict = judge(
            coverage(reasons=["side_a_too_small"]), metrics, ImageOptions())
        assert "side_a_too_small" in verdict.reasons

    def test_blank_beats_the_per_side_reasons(self) -> None:
        flat = Image.new("RGB", (60, 40), (12, 12, 12))
        buffer = BytesIO()
        flat.save(buffer, format="PNG")
        verdict = judge(coverage(), analyse_pixels(buffer.getvalue(), COLOUR_MOVABLE, COLOUR_FIXED),
                        ImageOptions())
        assert verdict.reasons == ["image_blank"]


class TestBlankIsAboutSubjectNotColourCount:
    """The cleanest shot had the fewest colours, and was discarded for it.

    Blankness was judged by counting colour buckets. Isolation exists to
    strip a render down to two painted elements against the sky, so the
    clearer the picture the fewer colours it holds — and the underside view
    of a pipe hanging below a slab, which is the only view that shows that
    clash at all, came back as three buckets and was rejected as a photograph
    of nothing.
    """

    def test_a_flat_picture_with_the_subject_in_it_is_not_blank(self) -> None:
        flat = PixelMetrics(
            sampled=540_000, distinct_colours=3, dominant_fraction=0.62,
            side_a_pixels=330_000, side_b_pixels=2_700,
        )
        assert flat.blank is False

    def test_a_flat_picture_without_the_subject_still_is(self) -> None:
        empty = PixelMetrics(
            sampled=540_000, distinct_colours=3, dominant_fraction=0.62,
            side_a_pixels=0, side_b_pixels=0,
        )
        assert empty.blank is True

    def test_one_flat_surface_is_blank_when_nothing_is_painted(self) -> None:
        inside_geometry = PixelMetrics(
            sampled=540_000, distinct_colours=40, dominant_fraction=0.999,
            side_a_pixels=0, side_b_pixels=0,
        )
        assert inside_geometry.blank is True

    def test_nothing_sampled_is_still_blank(self) -> None:
        assert PixelMetrics(sampled=0).blank is True


class TestLadder:
    def test_occlusion_hides_the_geometry_in_the_way(self) -> None:
        options = ImageOptions()
        following = next_attempt(options, Verdict(ok=False, reasons=["side_b_not_visible"]))
        assert following is not None
        assert following.hide_unrelated_geometry is True
        assert following.camera_mode == "closeup", "el encuadre era correcto: no se toca"

    def test_when_isolation_was_not_enough_the_shot_pulls_back(self) -> None:
        """Isolation keeps both sides, so the survivor in the way is the other side.

        A pipe photographed at its intersection with a slab is enclosed by
        concrete on every face, and the occluder cannot be hidden because it
        is half the subject. Pulling back catches the length of pipe that
        emerges above and below. Without this rung the ladder stopped at two
        attempts with nothing left to try.
        """
        isolated = ImageOptions(hide_unrelated_geometry=True)
        following = next_attempt(
            isolated, Verdict(ok=False, reasons=["side_b_not_visible"]))
        assert following is not None
        assert following.camera_mode == "context"
        assert following.hide_unrelated_geometry is True

    def test_the_last_rung_goes_under_the_obstruction(self) -> None:
        """Widening cannot beat a slab; going below it can.

        `plan` is deliberately skipped on this path: it looks straight down,
        which for a service hanging below a slab is the one view guaranteed
        to fail, and the attempt budget is small enough that spending a
        render there costs the rung that works.
        """
        isolated = ImageOptions(hide_unrelated_geometry=True, camera_mode="context")
        following = next_attempt(
            isolated, Verdict(ok=False, reasons=["side_b_not_visible"]))
        assert following is not None
        assert following.camera_mode == "underside"

    def test_pulling_back_runs_out_too(self) -> None:
        exhausted = ImageOptions(hide_unrelated_geometry=True, camera_mode="underside")
        assert next_attempt(
            exhausted, Verdict(ok=False, reasons=["side_b_not_visible"])) is None

    def test_misframing_widens_the_shot(self) -> None:
        following = next_attempt(
            ImageOptions(), Verdict(ok=False, reasons=["side_a_out_of_frame"]))
        assert following is not None
        assert following.camera_mode == "context"
        assert following.hide_unrelated_geometry is False

    def test_the_ladder_ends_instead_of_looping(self) -> None:
        """An identical retry costs a Navisworks render and fails identically."""
        options = ImageOptions(camera_mode="underside", margin_percent=150.0)
        assert next_attempt(options, Verdict(ok=False, reasons=["side_a_out_of_frame"])) is None

    def test_an_unreadable_image_is_not_retried(self) -> None:
        assert next_attempt(ImageOptions(), Verdict(ok=False, reasons=["image_unreadable"])) is None

    def test_both_faults_at_once_widen_and_isolate(self) -> None:
        following = next_attempt(
            ImageOptions(),
            Verdict(ok=False, reasons=["side_a_out_of_frame", "side_b_not_visible"]),
        )
        assert following is not None
        assert following.camera_mode == "context"
        assert following.hide_unrelated_geometry is True


# --------------------------------------------------------------- capture


class TestCapture:
    def test_a_good_first_shot_is_taken_once(self) -> None:
        bridge = FakeBridge([{"png": render()}])
        shot = capture(bridge, "guid-1", ImageOptions())
        assert shot.accepted
        assert shot.png
        assert len(bridge.calls) == 1, "no se gasta un render de más en una imagen buena"
        assert shot.location["grid_reference"] == "C-4 : Nivel 3"

    def test_an_occluded_shot_is_retaken_with_isolation(self) -> None:
        """The whole point of the ladder, end to end.

        First frame: side B behind a wall. Second: the wall is gone because the
        request asked for isolation, and the picture is usable.
        """
        bridge = FakeBridge([{"png": render(side_b=None)}, {"png": render()}])
        shot = capture(bridge, "guid-2", ImageOptions())

        assert len(bridge.calls) == 2
        assert bridge.calls[0]["hide_unrelated_geometry"] is False
        assert bridge.calls[1]["hide_unrelated_geometry"] is True
        assert shot.accepted
        assert len(shot.attempts) == 2
        assert shot.attempts[0].verdict.reasons == ["side_b_not_visible"]

    def test_an_isolation_that_hides_nothing_does_not_get_a_second_render(self) -> None:
        """Observed on a real federation, and it costs money to ignore.

        The addin reported `hidden_items: 0` after walking 20 ancestors and
        3,474 siblings, so the occlusion rung changed nothing at all. Repeating
        it renders the same frame again; the ladder drops that rung and widens
        instead.
        """
        blind = FakeBridge([
            {"png": render(side_b=None), "visual": {"isolated": False, "hidden_items": 0}},
            {"png": render(side_b=None), "visual": {"isolated": True, "hidden_items": 0}},
            {"png": render(), "visual": {"isolated": False, "hidden_items": 0}},
        ])
        shot = capture(blind, "guid-blind", ImageOptions())

        assert blind.calls[1]["hide_unrelated_geometry"] is True, "probó a aislar"
        assert blind.calls[2]["hide_unrelated_geometry"] is False, (
            "insistió en aislar después de que no ocultara nada"
        )
        assert blind.calls[2]["camera_mode"] == "context", "y no abrió el encuadre"
        assert shot.accepted

    def test_isolation_that_works_is_kept(self) -> None:
        from naviscoord.imaging import isolation_did_nothing

        assert isolation_did_nothing(
            ImageOptions(hide_unrelated_geometry=True), {"hidden_items": 0})
        assert not isolation_did_nothing(
            ImageOptions(hide_unrelated_geometry=True), {"hidden_items": 12})
        assert not isolation_did_nothing(
            ImageOptions(hide_unrelated_geometry=False), {"hidden_items": 0})

    def test_a_hopeless_clash_is_rejected_and_explained(self) -> None:
        bridge = FakeBridge([{"png": render(side_b=None)}])
        shot = capture(bridge, "guid-3", ImageOptions())
        assert not shot.accepted
        assert shot.rejected, "hubo imagen, y no se publica"
        assert shot.png is not None, "se conserva la mejor para poder inspeccionarla"
        assert "side_b_not_visible" in shot.verdict.reasons
        # Four rungs, each a genuinely different thing to try — the ladder
        # stops when it runs out of ideas, not when it runs out of budget.
        # Isolation used to be treated as the only cure for occlusion and the
        # ladder ended after it. It is not: isolation keeps BOTH sides, so an
        # element buried inside the other stays hidden, and the two remaining
        # cures are to pull back until the part that emerges is in shot, and
        # failing that to photograph it from the other side of whatever lies
        # in between.
        assert len(bridge.calls) == 4
        assert [call["camera_mode"] for call in bridge.calls] == [
            "closeup", "closeup", "context", "underside"
        ]
        assert all(call["hide_unrelated_geometry"] for call in bridge.calls[1:])

    def test_keep_rejected_publishes_the_best_attempt(self) -> None:
        bridge = FakeBridge([{"png": render(side_b=None)}])
        shot = capture(bridge, "guid-4", ImageOptions(keep_rejected=True))
        assert shot.accepted
        assert shot.png

    def test_max_attempts_one_never_retries(self) -> None:
        bridge = FakeBridge([{"png": render(side_b=None)}])
        shot = capture(bridge, "guid-5", ImageOptions(max_attempts=1))
        assert len(bridge.calls) == 1
        assert not shot.accepted

    def test_a_render_error_stops_immediately(self) -> None:
        """No point re-framing a clash Navisworks cannot find."""
        bridge = FakeBridge([{"error": "clash_not_found"}])
        shot = capture(bridge, "missing", ImageOptions())
        assert len(bridge.calls) == 1
        assert not shot.accepted
        assert shot.png is None
        assert shot.verdict.reasons == ["render_failed"]

    def test_a_bridge_exception_becomes_a_reason_not_a_crash(self) -> None:
        def explode(guid: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise ConnectionError("el complemento no responde")

        shot = capture(explode, "guid-6", ImageOptions())
        assert not shot.accepted
        assert shot.verdict.reasons == ["bridge_error"]
        assert "ConnectionError" in shot.error

    def test_the_colours_judged_are_the_ones_the_addin_applied(self) -> None:
        """Not the ones that were requested.

        An add-in that ignored the palette would otherwise pass every image:
        the gate would look for red, the render would be green, and the
        mismatch would be read as "both elements missing" — or, with the
        fallback the other way round, as a pass.
        """
        applied_a, applied_b = (0, 150, 136), (233, 30, 99)
        bridge = FakeBridge([
            {"png": render(side_a=applied_a, side_b=applied_b),
             "colour_a": applied_a, "colour_b": applied_b}
        ])
        shot = capture(bridge, "guid-7", ImageOptions())
        assert shot.accepted
        assert shot.colour_a == applied_a and shot.colour_b == applied_b

    def test_effective_options_describe_the_attempt_that_produced_the_image(self) -> None:
        bridge = FakeBridge([{"png": render(side_b=None)}, {"png": render()}])
        shot = capture(bridge, "guid-8", ImageOptions())
        assert shot.options is not None
        assert shot.options.hide_unrelated_geometry is True
        assert shot.to_json()["effective"]["hide_unrelated_geometry"] is True

    def test_what_the_addin_reports_doing_survives_the_bridge(self) -> None:
        """The diagnostics were being dropped on the floor.

        The addin says how many items it hid and names anything that went
        wrong doing it. None of it reached the caller, so a live investigation
        into why isolation was not working read `visual: null, notes: null`
        while the answer was sitting in the response all along.
        """
        bridge = FakeBridge([{"png": render()}])
        shot = capture(bridge, "guid-diag", ImageOptions())
        payload = shot.to_json()
        assert payload["visual"]["hidden_items"] == 7
        assert "isolation_level_too_wide" in payload["notes"]

    def test_disciplines_reach_the_bridge(self) -> None:
        bridge = FakeBridge([{"png": render()}])
        capture(bridge, "guid-9", ImageOptions(), discipline_a="EST", discipline_b="HVAC")
        assert bridge.calls[0]["discipline_a"] == "EST"
        assert bridge.calls[0]["color_a"] == to_hex(palette_for("EST"))


# ------------------------------------------------------------ annotation


def _count(image, colour: tuple[int, int, int]) -> int:
    """Pixels of exactly this colour. `tobytes`, because `getdata` is on its
    way out of Pillow and a suite that warns is a suite people stop reading."""
    raw = image.convert("RGB").tobytes()
    target = bytes(colour)
    return sum(1 for i in range(0, len(raw), 3) if raw[i:i + 3] == target)


class TestClashMarker:
    """Navisworks draws no marker on an automated render, so we draw it.

    `ScenePlusOverlay` renders the overlay layer for the result the Clash
    Detective has SELECTED, and the 2026 Clash API has no way to select one —
    `TestsViewpointForResult` is the only member that takes a result. Every
    live render came back with an empty overlay, which is why this exists.
    """

    def test_the_marker_lands_where_the_clash_was_measured(self) -> None:
        from naviscoord.imaging import MARKER_COLOUR, mark_clash

        rect = {"in_frame": True, "min_x": -0.2, "max_x": 0.2, "min_y": -0.2, "max_y": 0.2}
        marked = mark_clash(render(size=(200, 200)), rect)
        with Image.open(BytesIO(marked)) as image:
            pixels = image.convert("RGB").load()
            # Centre of the frame maps to (100, 100); the box edge sits at
            # 0.2 -> 120 plus the padding.
            found = any(
                pixels[x, 100] == MARKER_COLOUR for x in range(115, 135)
            )
            assert found, "no se dibujó el borde derecho del marcador"

    def test_a_clash_out_of_frame_gets_no_marker(self) -> None:
        from naviscoord.imaging import mark_clash

        original = render()
        assert mark_clash(original, {"in_frame": False, "min_x": 5, "max_x": 6}) == original

    def test_a_sub_pixel_clash_still_gets_a_findable_marker(self) -> None:
        """A box drawn to scale around a 3 mm clash is invisible.

        Which is precisely the case the marker exists for: the smaller the
        interference, the more the reader needs to be told where it is.
        """
        from naviscoord.imaging import MARKER_COLOUR, mark_clash

        rect = {"in_frame": True, "min_x": 0.0, "max_x": 0.001, "min_y": 0.0, "max_y": 0.001}
        marked = mark_clash(render(size=(400, 400)), rect)
        with Image.open(BytesIO(marked)) as image:
            count = _count(image, MARKER_COLOUR)
        assert count > 40, f"el marcador quedó en {count} píxeles: invisible"

    def test_the_marker_is_drawn_after_the_pixels_are_counted(self) -> None:
        """Otherwise the marker itself would be measured as geometry."""
        bridge = FakeBridge([{"png": render(), "coverage": dict(
            coverage(), clash={"in_frame": True, "min_x": -0.5, "max_x": 0.5,
                               "min_y": -0.5, "max_y": 0.5})}])
        shot = capture(bridge, "guid-marker", ImageOptions())
        assert shot.accepted
        # The verdict was reached on the undecorated frame...
        assert shot.attempts[-1].pixels.side_a_pixels > 0
        # ...and the image handed back carries the marker.
        with Image.open(BytesIO(shot.png)) as image:
            from naviscoord.imaging import MARKER_COLOUR

            assert _count(image, MARKER_COLOUR) > 0

    def test_turning_the_marker_off_leaves_the_render_alone(self) -> None:
        bridge = FakeBridge([{"png": render(), "coverage": dict(
            coverage(), clash={"in_frame": True, "min_x": -0.5, "max_x": 0.5,
                               "min_y": -0.5, "max_y": 0.5})}])
        shot = capture(bridge, "g", ImageOptions(show_clash_marker=False, show_level=False))
        assert shot.accepted
        with Image.open(BytesIO(shot.png)) as image:
            from naviscoord.imaging import MARKER_COLOUR

            assert _count(image, MARKER_COLOUR) == 0


class TestAnnotation:
    def test_the_caption_does_not_destroy_the_image(self) -> None:
        original = render()
        stamped = annotate(original, "ISS-0001 · Nivel 3", (("EST", COLOUR_MOVABLE),))
        assert stamped != original
        with Image.open(BytesIO(stamped)) as image:
            assert image.size == (300, 200), "el pie va dentro del cuadro, no lo agranda"

    def test_a_broken_image_comes_back_untouched(self) -> None:
        """A missing caption is a blemish; a lost image is a hole."""
        assert annotate(b"basura", "hola") == b"basura"

    def test_nothing_to_say_changes_nothing(self) -> None:
        original = render()
        assert annotate(original, "") == original


# ------------------------------------------------------------------ pdf


@pytest.fixture(scope="module")
def analysed():
    return analyze(ClashExport.from_json(synth.full_project_case()), Profile.load())


class TestReportAccounting:
    def _build(self, tmp_path, analysed, profile, fetcher, **kwargs):
        from naviscoord.report import build_report

        return build_report(
            tmp_path / "informe.pdf", analysed, profile,
            document_title="PRUEBA.nwf", max_issues=4, capture_fetcher=fetcher, **kwargs
        )

    def test_every_requested_image_is_accounted_for(self, tmp_path, analysed, profile) -> None:
        bridge = FakeBridge([{"png": render()}])
        out = self._build(
            tmp_path, analysed, profile,
            lambda issue: capture(bridge, issue.image_clash_id(), ImageOptions()),
        )
        images = out["images"]
        assert images["requested"] == out["issue_pages"] == 4
        assert images["generated"] == 4
        assert images["embedded"] == 4
        assert images["failed"] == 0
        assert images["rejected_for_quality"] == 0

    def test_a_rejected_image_is_counted_apart_from_a_failed_one(
        self, tmp_path, analysed, profile
    ) -> None:
        """Two different problems that used to share one number.

        A failed render is a bridge or licence problem; a rejected one is a
        modelling or framing problem. Reporting both as "images_failed" sent
        people to restart Navisworks over a wall standing in front of a pipe.
        """
        occluded = FakeBridge([{"png": render(side_b=None)}])
        broken = FakeBridge([{"error": "image_unavailable"}])
        seen: list[str] = []

        def alternate(issue):
            # By call order, not by issue id: folded issues drop out of the
            # ranking, so the top four are not ISS-0001 to ISS-0004 and routing
            # on the id sent three of them down the same branch.
            seen.append(issue.issue_id)
            bridge = occluded if len(seen) % 2 else broken
            return capture(bridge, issue.image_clash_id(), ImageOptions(max_attempts=1))

        out = self._build(tmp_path, analysed, profile, alternate)
        images = out["images"]
        assert images["requested"] == 4
        assert images["rejected_for_quality"] == 2
        assert images["failed"] == 2
        assert images["embedded"] == 0
        assert images["rejection_reasons"]["side_b_not_visible"] == 2
        assert images["rejection_reasons"]["render_failed"] == 2

    def test_the_reason_travels_with_the_issue(self, tmp_path, analysed, profile) -> None:
        bridge = FakeBridge([{"png": render(side_b=None)}])
        out = self._build(
            tmp_path, analysed, profile,
            lambda issue: capture(bridge, issue.image_clash_id(), ImageOptions(max_attempts=1)),
        )
        entry = out["images"]["per_issue"][0]
        assert entry["issue_id"].startswith("ISS-")
        assert entry["embedded"] is False
        assert entry["verdict"]["reasons"] == ["side_b_not_visible"]
        assert entry["effective"]["camera_mode"] == "closeup"

    def test_an_embedded_image_makes_the_pdf_bigger(self, tmp_path, analysed, profile) -> None:
        """That the picture is IN the file, not merely counted.

        The tally is computed by this module, so asserting only on the tally
        would be the report agreeing with itself.
        """
        from naviscoord.report import build_report

        # Not "con.pdf": CON is a reserved Windows device name and the output
        # policy refuses it, correctly.
        with_images = build_report(
            tmp_path / "ilustrado.pdf", analysed, profile, max_issues=3,
            capture_fetcher=lambda issue: capture(
                FakeBridge([{"png": render(size=(900, 600), a_size=200, b_size=200)}]),
                issue.image_clash_id(), ImageOptions()),
        )
        build_report(tmp_path / "escueto.pdf", analysed, profile, max_issues=3)

        assert with_images["images"]["embedded"] == 3
        big = (tmp_path / "ilustrado.pdf").stat().st_size
        small = (tmp_path / "escueto.pdf").stat().st_size
        assert big > small * 1.5, f"con imágenes {big} B, sin ellas {small} B"

    def test_a_dirtied_document_is_said_out_loud(self, tmp_path, analysed, profile) -> None:
        """Measured on a live federation, and unavoidable.

        Rendering moves the camera because Navisworks renders the CURRENT
        view, and moving the camera marks the document modified — with
        colouring, isolation, grid and background all off, so nothing else
        could be responsible. Whoever ran the report is the person Navisworks
        will ask about saving, so they hear it from the result.
        """
        dirtying = FakeBridge([{"png": render()}])

        def capture_dirty(issue):
            shot = capture(dirtying, issue.image_clash_id(), ImageOptions())
            shot.document = {"modified_before": False, "modified_after": True}
            return shot

        out = self._build(tmp_path, analysed, profile, capture_dirty)
        note = out["images"]["document_note"]
        assert "modificado" in note
        assert "no se guardó" in note.lower()

    def test_a_document_that_was_already_dirty_says_nothing(
        self, tmp_path, analysed, profile
    ) -> None:
        """Blaming the report for the user's own unsaved work is worse than
        silence: it sends them looking for a change nobody made."""
        bridge = FakeBridge([{"png": render()}])

        def capture_already_dirty(issue):
            shot = capture(bridge, issue.image_clash_id(), ImageOptions())
            shot.document = {"modified_before": True, "modified_after": True}
            return shot

        out = self._build(tmp_path, analysed, profile, capture_already_dirty)
        assert "document_note" not in out["images"]

    def test_a_capture_that_raises_does_not_lose_the_report(
        self, tmp_path, analysed, profile
    ) -> None:
        def explode(issue):
            raise RuntimeError("Navisworks se cayó")

        out = self._build(tmp_path, analysed, profile, explode)
        assert out["images"]["failed"] == 4
        assert (tmp_path / "informe.pdf").exists(), "el PDF sale igual, sin las imágenes"

    def test_the_old_fetcher_still_works(self, tmp_path, analysed, profile) -> None:
        """The signature that shipped first, unchanged.

        Callers of `build_report` that pass `image_fetcher` get their bytes
        embedded unexamined, exactly as before.
        """
        from naviscoord.report import build_report

        out = build_report(
            tmp_path / "viejo.pdf", analysed, profile, max_issues=2,
            image_fetcher=lambda guid: render(),
        )
        assert out["images_embedded"] == 2
        assert out["images_failed"] == 0
        assert out["images"]["embedded"] == 2


class TestFederatedModels:
    """A clash whose two sides live in different files is the normal case.

    NWF versus NWD is a property of the open document and only the live add-in
    can exercise it; what CAN be asserted here is that nothing in the imaging
    path assumes both elements come from one model, which is the part that
    would break on a federation.
    """

    def test_sides_from_different_source_files_get_different_colours(
        self, analysed, profile
    ) -> None:
        federated = [
            issue for issue in analysed.issues
            if issue.discipline_a and issue.discipline_b
            and issue.discipline_a != issue.discipline_b
        ]
        assert federated, "el caso sintético federado debería producir cruces entre disciplinas"
        for issue in federated[:10]:
            a, b = colours_for(issue.discipline_a, issue.discipline_b, issue.responsible, True)
            assert a != b

    def test_the_photographed_clash_is_the_one_the_disciplines_describe(
        self, analysed
    ) -> None:
        """Otherwise the legend names the trades of a different clash.

        `clash_ids[0]` is whichever member the cluster collected first; the
        side disciplines come from the deepest one. On any cluster where those
        differ, the caption under the picture was confidently wrong.
        """
        multi = [i for i in analysed.issues if i.clash_count > 1]
        assert multi, "el caso sintético debería producir clústeres"
        for issue in multi:
            assert issue.representative_clash_id
            assert issue.image_clash_id() == issue.representative_clash_id
            assert issue.representative_clash_id in issue.clash_ids

    def test_an_issue_without_a_representative_still_gets_a_picture(self) -> None:
        """An analysis cached before the field existed must not lose its images."""
        from naviscoord.model import Issue

        issue = Issue(
            issue_id="ISS-0001", kind="pair", discipline_pair=("EST", "HVAC"),
            clash_ids=["a", "b"], centroid=(0, 0, 0), bbox_min=(0, 0, 0), bbox_max=(1, 1, 1),
        )
        assert issue.image_clash_id() == "a"

    def test_an_issue_with_no_clashes_at_all_asks_for_nothing(self) -> None:
        from naviscoord.model import Issue

        issue = Issue(
            issue_id="ISS-0002", kind="pair", discipline_pair=("EST", "HVAC"),
            clash_ids=[], centroid=(0, 0, 0), bbox_min=(0, 0, 0), bbox_max=(1, 1, 1),
        )
        assert issue.image_clash_id() == ""


class TestCaptureSerialisation:
    def test_an_empty_capture_reports_honestly(self) -> None:
        empty = Capture(clash_guid="x")
        payload = empty.to_json()
        assert payload["accepted"] is False
        assert payload["rejected"] is False, "sin render no hay nada que descartar"
        assert payload["verdict"]["reasons"] == ["not_attempted"]
