"""Clash pictures: what to ask for, and whether what came back is usable.

A coordination report lives or dies on its images, and a bad one does not
announce itself. Navisworks returns a perfectly valid PNG whether the camera
framed the pipe going through the beam, framed the whole building, or framed
the inside of the duct. The render succeeds either way, so a report full of
useless pictures still looks finished — which is exactly how it reaches a
client.

So nothing here trusts a successful render. Every image is judged twice:

* **Geometrically**, by the addin, which projects the eight corners of each
  element through the camera it actually applied and reports whether they
  landed inside the frame. That catches "off screen" and "three pixels wide"
  exactly, because it is arithmetic rather than opinion.
* **By its pixels**, here, which is the only check that can see occlusion. The
  two clashing elements are painted known colours before the shot, so their
  absence from the image is not an interpretation: it means a wall is standing
  in front of the clash, and the picture is worthless no matter how correct
  the camera was.

An image that fails is re-shot with a wider frame or with the intervening
geometry hidden, and only then discarded. Discarded images are counted and
explained rather than quietly replaced with a blank space.
"""

from __future__ import annotations

import base64
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Any

# --------------------------------------------------------------- palette

#: Saturated, hue-separated, and distinguishable from the neutral grey the
#: context is painted. Chosen for the pixel check as much as for the eye: the
#: classifier below works on chromaticity, so two colours that differ only in
#: brightness would be indistinguishable to it under any lighting.
DISCIPLINE_PALETTE: tuple[tuple[int, int, int], ...] = (
    (214, 40, 40),     # rojo
    (30, 136, 229),    # azul
    (245, 166, 35),    # ámbar
    (123, 31, 162),    # violeta
    (0, 150, 136),     # turquesa
    (205, 220, 57),    # lima
    (233, 30, 99),     # magenta
    (63, 81, 181),     # índigo
)

#: When the picture is about who moves rather than about which trade is which.
COLOUR_MOVABLE = (214, 40, 40)
COLOUR_FIXED = (30, 136, 229)
COLOUR_CONTEXT = (176, 176, 176)

#: How far a pixel's hue may sit from an element's colour and still be counted
#: as that element. The closest two entries of the palette above are 24° apart
#: (red and magenta, blue and indigo), so this stays under half of that: a
#: wider window would put pixels inside the tolerance of two different sides at
#: once, and the nearest-wins rule would be deciding a coin flip.
HUE_TOLERANCE = 18.0

#: Below this saturation a pixel is background, whatever its hue says.
#:
#: Measured, not guessed. On a live federation render the two painted elements
#: came in at 0.49 and 0.58 median saturation while the sky and the faded
#: context sat at 0.15 and 0.09 — so anything from about 0.2 to 0.45 separates
#: them, and the middle of that range is the safe place to stand. It matters
#: because the default Navisworks sky is a pale BLUE: with a lower floor, a
#: blue-painted element "appeared" in every image whether it was there or not,
#: which is worse than the false rejection this replaced.
MIN_SATURATION = 0.28


def palette_for(code: str, taken: tuple[int, int, int] | None = None) -> tuple[int, int, int]:
    """A stable colour for a discipline code.

    Keyed on a CRC of the code rather than on its position in a list, so a
    discipline keeps its colour when the profile gains another one — a report
    where the ducts changed colour between two runs is a report nobody trusts.
    `hash()` would have been the obvious choice and is randomised per process,
    which would have made the colour differ between two runs of the same
    command on the same model.
    """
    if not code:
        return COLOUR_CONTEXT
    index = zlib.crc32(code.encode("utf-8")) % len(DISCIPLINE_PALETTE)
    colour = DISCIPLINE_PALETTE[index]
    if taken is not None and colour == taken:
        colour = DISCIPLINE_PALETTE[(index + 1) % len(DISCIPLINE_PALETTE)]
    return colour


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(c))) for c in rgb)


# --------------------------------------------------------------- options


@dataclass(frozen=True)
class ImageOptions:
    """Everything a caller may set, and what it gets when it sets nothing.

    Frozen because a capture attempt derives a modified copy rather than
    mutating the caller's request: the retry ladder has to be able to report
    what each attempt actually used, and a shared mutable object would make
    every attempt report the last one's settings.
    """

    camera_mode: str = "closeup"
    margin_percent: float = 25.0
    min_distance: float = 1.5
    max_distance: float = 60.0
    image_width: int = 1200
    image_height: int = 800
    background_color: str = ""
    hide_unrelated_geometry: bool = False
    colorize_by_discipline: bool = True
    show_clash_marker: bool = True
    show_level: bool = True
    show_grid: bool | None = None
    render_quality: str = "standard"

    #: Share of the frame an element's box must be able to cover. The addin
    #: checks this one; it is an upper bound on the element's real size, so
    #: failing it means the picture cannot possibly be readable.
    min_screen_fraction: float = 0.004
    #: Share of the pixels an element must actually paint. Lower than the
    #: geometric bound on purpose: real geometry is much thinner than its
    #: bounding box, and a pipe seen end-on is legitimately small.
    min_pixel_fraction: float = 0.0006
    #: Attempts per image, INCLUDING the first. Two re-shoots is where the
    #: ladder runs out of genuinely different things to try, and each one
    #: costs a Navisworks render.
    max_attempts: int = 4
    #: Keep the best failed attempt instead of dropping it. Off by default: a
    #: misleading picture in a coordination report is worse than a stated gap.
    keep_rejected: bool = False

    MODES = ("closeup", "context", "plan", "underside")
    QUALITIES = ("draft", "standard", "high")

    def normalised(self) -> ImageOptions:
        """Clamped into the ranges the addin will accept anyway.

        Done here as well as in the addin so the *effective* values reported
        back to the caller are the ones that will be used, rather than the ones
        that were asked for. A report that says `margin_percent: 9000` when the
        addin used 400 is a report that lies about its own inputs.
        """
        mode = str(self.camera_mode or "closeup").strip().lower()
        aliases = {"contexto": "context", "planta": "plan", "top": "plan",
                   "primer_plano": "closeup", "abajo": "underside", "bottom": "underside"}
        mode = aliases.get(mode, mode)
        if mode not in self.MODES:
            mode = "closeup"

        quality = str(self.render_quality or "standard").strip().lower()
        if quality not in self.QUALITIES:
            quality = "standard"

        min_distance = _bound(self.min_distance, 0.05, 500.0)
        return replace(
            self,
            camera_mode=mode,
            render_quality=quality,
            margin_percent=_bound(self.margin_percent, 0.0, 400.0),
            min_distance=min_distance,
            max_distance=_bound(self.max_distance, min_distance, 5000.0),
            image_width=int(_bound(self.image_width, 160, 2400)),
            image_height=int(_bound(self.image_height, 120, 1800)),
            min_screen_fraction=_bound(self.min_screen_fraction, 0.0, 0.9),
            min_pixel_fraction=_bound(self.min_pixel_fraction, 0.0, 0.9),
            max_attempts=int(_bound(self.max_attempts, 1, 5)),
        )

    def payload(
        self,
        colour_a: tuple[int, int, int],
        colour_b: tuple[int, int, int],
        discipline_a: str = "",
        discipline_b: str = "",
    ) -> dict[str, Any]:
        """The bridge payload for one attempt."""
        body: dict[str, Any] = {
            "camera_mode": self.camera_mode,
            "margin_percent": self.margin_percent,
            "min_distance": self.min_distance,
            "max_distance": self.max_distance,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "hide_unrelated_geometry": self.hide_unrelated_geometry,
            "show_clash_marker": self.show_clash_marker,
            "show_level": self.show_level,
            "render_quality": self.render_quality,
            "min_screen_fraction": self.min_screen_fraction,
            "color_a": to_hex(colour_a),
            "color_b": to_hex(colour_b),
            "discipline_a": discipline_a,
            "discipline_b": discipline_b,
        }
        if self.background_color:
            body["background_color"] = self.background_color
            # The 2026 API can SET a background and cannot READ one, so the
            # addin refuses to touch it without being told what to put back.
            # Sending the restore colour with the request is what makes the
            # option safe on a document somebody else has open.
            body["background_restore_color"] = "#FFFFFF"
        if self.show_grid is not None:
            body["show_grid"] = bool(self.show_grid)
        return body

    def to_json(self) -> dict[str, Any]:
        return {
            "camera_mode": self.camera_mode,
            "margin_percent": self.margin_percent,
            "min_distance": self.min_distance,
            "max_distance": self.max_distance,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "background_color": self.background_color,
            "hide_unrelated_geometry": self.hide_unrelated_geometry,
            "colorize_by_discipline": self.colorize_by_discipline,
            "show_clash_marker": self.show_clash_marker,
            "show_level": self.show_level,
            "show_grid": self.show_grid,
            "render_quality": self.render_quality,
            "min_screen_fraction": self.min_screen_fraction,
            "min_pixel_fraction": self.min_pixel_fraction,
            "max_attempts": self.max_attempts,
        }


def _bound(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    if number != number:  # NaN
        return low
    return max(low, min(high, number))


def colours_for(
    discipline_a: str,
    discipline_b: str,
    responsible: str,
    by_discipline: bool,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """The colour each side of the clash gets painted.

    Two schemes, and the difference matters to whoever reads the picture:

    * By discipline — the ducts are always the same colour across the whole
      report, which is what makes a stack of images comparable.
    * By movability — the side that has to move is red and the side that stays
      is blue, which is what makes a single image answer "so who moves it".

    The second is only offered, never guessed at from the sorted discipline
    pair. That inference painted the wrong half of the clash on roughly every
    other issue, and it looked plausible in every screenshot.
    """
    if by_discipline:
        first = palette_for(discipline_a)
        second = palette_for(discipline_b, taken=first)
        return first, second

    if responsible and responsible == discipline_a and discipline_a != discipline_b:
        return COLOUR_MOVABLE, COLOUR_FIXED
    if responsible and responsible == discipline_b and discipline_a != discipline_b:
        return COLOUR_FIXED, COLOUR_MOVABLE
    # Not attributable: both sides get the alert colour rather than one of them
    # getting it wrongly.
    return COLOUR_MOVABLE, COLOUR_MOVABLE


# ---------------------------------------------------------------- pixels


@dataclass
class PixelMetrics:
    """What the rendered pixels say about the two elements."""

    width: int = 0
    height: int = 0
    sampled: int = 0
    side_a_pixels: int = 0
    side_b_pixels: int = 0
    distinct_colours: int = 0
    dominant_fraction: float = 0.0
    decoded: bool = True
    error: str = ""

    @property
    def side_a_fraction(self) -> float:
        return self.side_a_pixels / self.sampled if self.sampled else 0.0

    @property
    def side_b_fraction(self) -> float:
        return self.side_b_pixels / self.sampled if self.sampled else 0.0

    @property
    def blank(self) -> bool:
        """A picture of nothing.

        Two independent symptoms, because they arise differently: a camera
        pointed at empty space renders the background gradient, which is a
        handful of colours covering everything, while a camera inside geometry
        renders one flat surface, which is one colour covering everything.

        Both are counts of colour, and colour alone cannot tell an empty
        picture from a clean one. Isolation exists to strip a render down to
        two painted elements against the sky, so the better the shot the fewer
        colours it holds — and the underside view of a pipe below a slab, the
        clearest picture of that clash there is, came back as three buckets and
        was thrown away as a photograph of nothing.

        So the count only speaks when nothing else does. Finding either side's
        paint settles the question: whatever else this picture is, it is not a
        picture of nothing. How MUCH of it is visible is a different question,
        and the per-side checks already answer it.
        """
        if not self.sampled:
            return True
        if self.side_a_pixels or self.side_b_pixels:
            return False
        return self.distinct_colours <= 3 or self.dominant_fraction >= 0.995

    def to_json(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "sampled_pixels": self.sampled,
            "side_a_pixels": self.side_a_pixels,
            "side_b_pixels": self.side_b_pixels,
            "side_a_fraction": round(self.side_a_fraction, 5),
            "side_b_fraction": round(self.side_b_fraction, 5),
            "distinct_colours": self.distinct_colours,
            "dominant_fraction": round(self.dominant_fraction, 4),
            "blank": self.blank,
            "decoded": self.decoded,
            "error": self.error,
        }


#: Sampling grid. Counting every pixel of a 1200x800 render costs a million
#: comparisons per image and answers the same question: the smallest thing
#: that must be detectable covers ~0.06% of the frame, which is still hundreds
#: of samples on this grid.
_SAMPLE_TARGET = 240 * 160


def analyse_pixels(
    png: bytes,
    colour_a: tuple[int, int, int],
    colour_b: tuple[int, int, int],
    tolerance: float = HUE_TOLERANCE,
) -> PixelMetrics:
    """Counts how much of the frame each painted element actually covers.

    Classification is by **hue**, and the two rejected alternatives are worth
    naming because each fails on a real render:

    * Nearest RGB colour fails on shading. Navisworks scales a surface towards
      black, so a red pipe spans (214, 40, 40) to (30, 6, 6) inside one image;
      a distance match finds the lit face and loses the shaded one.
    * Chromaticity — the channel ratios — survives shading and fails on
      *whitening*. With the context faded, the two clashing elements are seen
      THROUGH a milky layer of translucent geometry, and every pixel is a
      blend towards the pale background. Measured on a live federation, a
      clearly visible red pipe came back as 0.01% of the frame and a perfectly
      good picture was rejected.

    Hue survives both: blending towards white moves saturation, not hue, and
    shading moves value, not hue. The washed-out pipe above sits at 351°,
    nine degrees from pure red and forty-six from the duct's orange.
    """
    metrics = PixelMetrics()
    if not png:
        metrics.decoded = False
        metrics.error = "sin datos"
        return metrics

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - declared dependency
        metrics.decoded = False
        metrics.error = f"Pillow no disponible: {exc}"
        return metrics

    try:
        with Image.open(BytesIO(png)) as raw:
            image = raw.convert("RGB")
            metrics.width, metrics.height = image.size
            step = 1
            pixel_count = metrics.width * metrics.height
            if pixel_count > _SAMPLE_TARGET:
                step = max(1, int((pixel_count / _SAMPLE_TARGET) ** 0.5))
            if step > 1:
                image = image.resize(
                    (max(1, metrics.width // step), max(1, metrics.height // step)),
                    Image.NEAREST,
                )
            # tobytes rather than getdata: one buffer instead of a list of
            # tuples, and getdata is deprecated from Pillow 14.
            raw_pixels = image.tobytes()
    except Exception as exc:
        metrics.decoded = False
        metrics.error = f"PNG ilegible: {exc}"
        return metrics

    metrics.sampled = len(raw_pixels) // 3
    if not metrics.sampled:
        return metrics

    table = _hue_table(colour_a, colour_b, tolerance)
    # Two sides painted the same colour — the case where movability cannot be
    # attributed — must not read as "side B is missing".
    same_target = tuple(colour_a) == tuple(colour_b)

    histogram: dict[tuple[int, int, int], int] = {}
    for offset in range(0, metrics.sampled * 3, 3):
        pixel = (raw_pixels[offset], raw_pixels[offset + 1], raw_pixels[offset + 2])
        # Coarse bucket: counting exact triplets over a dithered gradient
        # reports thousands of "distinct colours" for a blank sky.
        bucket = (pixel[0] >> 4, pixel[1] >> 4, pixel[2] >> 4)
        histogram[bucket] = histogram.get(bucket, 0) + 1

        hue = _hue_of(pixel)
        if hue is None:
            continue

        side = table[int(hue) % 360]
        if side == "a":
            metrics.side_a_pixels += 1
            if same_target:
                metrics.side_b_pixels += 1
        elif side == "b":
            metrics.side_b_pixels += 1

    metrics.distinct_colours = len(histogram)
    metrics.dominant_fraction = max(histogram.values()) / metrics.sampled
    return metrics


def _hue_of(rgb: tuple[int, int, int]) -> float | None:
    """Hue in degrees, or None when the pixel has no colour to speak of.

    Near-black and near-grey pixels are rejected rather than measured: hue is
    meaningless for both, and the neutral context is painted grey precisely so
    that it can never be mistaken for an element.

    The saturation floor is low (12%) because the fade the context is drawn
    with washes the elements behind it towards the background. It can afford to
    be: grey and white sit at zero saturation, so a floor this low still
    excludes every neutral surface in the scene.
    """
    r, g, b = rgb[0], rgb[1], rgb[2]
    high = max(r, g, b)
    if high < 24:
        return None
    low = min(r, g, b)
    delta = high - low
    if delta / high < MIN_SATURATION:
        return None

    if high == r:
        hue = ((g - b) / delta) % 6.0
    elif high == g:
        hue = (b - r) / delta + 2.0
    else:
        hue = (r - g) / delta + 4.0
    return (hue * 60.0) % 360.0


def _hue_gap(first: float, second: float) -> float:
    """Angular distance between two hues, the short way round the circle."""
    gap = abs(first - second) % 360.0
    return min(gap, 360.0 - gap)


def _exposure_arc(colour: tuple[int, int, int]) -> list[float]:
    """Every hue a colour can render as once the light clips a channel.

    Hue survives shading and survives whitening. It does NOT survive
    **clipping**: a surface facing the headlight saturates its strongest
    channels at 255, and once two of the three are pinned there the ratio
    between them is lost and the hue slides.

    Measured, on a plan view of a real clash: a duct painted (245, 166, 35) —
    hue 37° — rendered edge-on and lit straight on came back as
    (255, 255, 140), hue **60°**. Twenty-three degrees away from its own
    colour, so the classifier scored 7,704 pixels of it as background and the
    gate rejected the picture for an element that was plainly in it.

    Inverting the clipping is impossible: (255, 255, 140) has many origins.
    Modelling it forwards is not. The render is `clamp(colour × light)`, so
    this walks the light up and collects the hues that come out. A pixel then
    matches a colour when it lands anywhere on that arc.

    The walk stops where the colour washes out below the saturation floor,
    because past that point it is indistinguishable from white anyway.
    """
    hues: list[float] = []
    gain = 1.0
    while gain <= 4.0:
        lit = tuple(min(255, int(round(channel * gain))) for channel in colour)
        hue = _hue_of(lit)  # returns None once it is too pale to mean anything
        if hue is not None and all(abs(hue - seen) > 0.5 for seen in hues):
            hues.append(hue)
        gain += 0.1
    if not hues:
        base = _hue_of(colour)
        return [base] if base is not None else []
    return hues


def _hue_table(
    colour_a: tuple[int, int, int],
    colour_b: tuple[int, int, int],
    tolerance: float,
) -> list[str | None]:
    """A hue → side lookup, one entry per degree.

    Built once per image rather than per pixel: comparing every sampled pixel
    against both exposure arcs is a few million operations, and a 360-entry
    table answers the same question with one index.

    Nearest arc wins, so two colours whose arcs overlap — any two warm ones do,
    once the light clips them both towards yellow — still resolve to whichever
    is closer, deterministically.
    """
    arcs = {"a": _exposure_arc(colour_a), "b": _exposure_arc(colour_b)}
    table: list[str | None] = [None] * 360
    for hue in range(360):
        best: str | None = None
        best_gap = 1e9
        for side, arc in arcs.items():
            if not arc:
                continue
            gap = min(_hue_gap(float(hue), point) for point in arc)
            if gap < best_gap:
                best, best_gap = side, gap
        table[hue] = best if best_gap <= tolerance else None
    return table


# --------------------------------------------------------------- verdict


@dataclass
class Verdict:
    ok: bool = True
    reasons: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": list(self.reasons)}


#: Reasons that mean "the camera was wrong" as opposed to "something is in
#: the way". They lead to different re-shoots, which is the whole point of
#: separating them.
FRAMING_REASONS = frozenset(
    {
        "side_a_out_of_frame",
        "side_b_out_of_frame",
        "clash_out_of_frame",
        "clash_not_contained",
        "side_a_too_small",
        "side_b_too_small",
    }
)
OCCLUSION_REASONS = frozenset({"side_a_not_visible", "side_b_not_visible", "image_blank"})


def _screen_fraction(coverage: dict[str, Any], side: str) -> float:
    entry = coverage.get(f"side_{side}") or {}
    try:
        return float(entry.get("screen_fraction", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _renders_as_predicted(
    coverage: dict[str, Any], side: str, fraction: float, options: ImageOptions
) -> bool:
    """Is this side small because it IS small, or because something hides it?

    The pixel threshold is an absolute share of the image, which asks the
    impossible of an element whose entire projection is smaller than that
    share — a 15 mm copper pipe covers 0.02% of the frame when the slab it
    crosses is in shot too. Rejecting it says "not visible" about something
    plainly visible, just small.

    Geometry already knows how much of the frame the element should occupy.
    Comparing what came back against that prediction keeps the check that
    matters — a wall standing in front still renders far less than predicted
    and is still rejected — while dropping the one that only measured size.
    """
    predicted = _screen_fraction(coverage, side)
    if not predicted or predicted >= options.min_pixel_fraction:
        # Big enough to be held to the absolute threshold, so it just failed.
        return False
    return fraction >= predicted * VISIBILITY_RATIO


def _pair_is_lopsided(coverage: dict[str, Any], options: ImageOptions) -> bool:
    a = _screen_fraction(coverage, "a")
    b = _screen_fraction(coverage, "b")
    if a <= 0.0 or b <= 0.0:
        return False
    return max(a, b) / min(a, b) >= LOPSIDED_RATIO


def _smaller_side(coverage: dict[str, Any]) -> str:
    a = _screen_fraction(coverage, "a")
    b = _screen_fraction(coverage, "b")
    if a <= 0.0 or b <= 0.0:
        return ""
    return "a" if a < b else "b"


#: How much of its predicted footprint an element must actually render before
#: the difference reads as something standing in front of it rather than as
#: ordinary anti-aliasing at small sizes.
VISIBILITY_RATIO = 0.25

#: Projected-area ratio past which no single camera can show both sides large.
LOPSIDED_RATIO = 50.0


def judge(
    coverage: dict[str, Any],
    pixels: PixelMetrics,
    options: ImageOptions,
) -> Verdict:
    """Combines the geometric verdict with the pixel one.

    Neither is sufficient alone, and the reason is worth stating: the geometry
    check cannot see a wall standing in front of the clash, and the pixel check
    cannot tell a correctly framed dark image from a badly framed one. Together
    they cover both ways a valid PNG turns out to be useless.
    """
    verdict = Verdict()
    reasons: list[str] = []

    if not coverage.get("both_sides_in_frame", True):
        if not (coverage.get("side_a") or {}).get("in_frame", True):
            reasons.append("side_a_out_of_frame")
        if not (coverage.get("side_b") or {}).get("in_frame", True):
            reasons.append("side_b_out_of_frame")
    if not coverage.get("clash_contained", True):
        reasons.append("clash_not_contained")
    for named in coverage.get("reasons") or []:
        if named in ("side_a_too_small", "side_b_too_small") and named not in reasons:
            reasons.append(named)

    if not pixels.decoded:
        reasons.append("image_unreadable")
    elif pixels.blank:
        reasons.append("image_blank")
    else:
        for side, fraction in (("a", pixels.side_a_fraction), ("b", pixels.side_b_fraction)):
            if fraction >= options.min_pixel_fraction:
                continue
            if _renders_as_predicted(coverage, side, fraction, options):
                continue
            reasons.append(f"side_{side}_not_visible")

    # A lopsided pair cannot be framed so both sides are large: a copper pipe
    # against a floor-spanning slab is four orders of magnitude apart, and
    # tightening until the pipe is legible pushes the slab out of frame. The
    # ladder then burns its attempts alternating between the two complaints.
    # Where the clash itself is well framed, the picture already answers the
    # question it exists to answer, so the size complaint about the smaller
    # side is dropped rather than re-shot forever.
    if _pair_is_lopsided(coverage, options) and coverage.get("clash_contained", False):
        smaller = _smaller_side(coverage)
        if smaller:
            reasons = [r for r in reasons if r != f"side_{smaller}_too_small"]

    verdict.reasons = reasons
    verdict.ok = not reasons
    return verdict


def isolation_did_nothing(options: ImageOptions, visual: dict[str, Any]) -> bool:
    """Whether isolation was asked for and hid not one thing.

    It happens on real models: a federation reported
    `isolation_found_nothing_to_hide` with `hidden_items: 0` after walking 20
    ancestors and 3,474 siblings, so every sibling was spared for one of three
    reasons and the picture came back identical. Why is still open — see
    docs/TESTING.md — but the ladder must not keep paying a Navisworks render
    for a rung that demonstrably changes nothing.
    """
    if not options.hide_unrelated_geometry:
        return False
    if not visual:
        return False
    return int(visual.get("hidden_items") or 0) == 0


def next_attempt(options: ImageOptions, verdict: Verdict) -> ImageOptions | None:
    """The next thing worth trying, or None when nothing is.

    Deliberately not "retry the same request". An identical render produces an
    identical failure and costs another Navisworks frame, so each rung changes
    the one thing that could plausibly be responsible:

    * out of frame, or too small to read — the camera. Widen the mode.
    * painted but not visible in the pixels — something is in front of it.
      Hide the unrelated geometry, which is the only cure that exists.
    """
    reasons = set(verdict.reasons)
    if "image_unreadable" in reasons:
        return None

    occluded = bool(reasons & OCCLUSION_REASONS)
    misframed = bool(reasons & FRAMING_REASONS)

    if occluded and not options.hide_unrelated_geometry:
        # Keep the frame that was already judged geometrically correct and
        # remove what stands in front of it.
        widened = _widen(options.camera_mode) if misframed else options.camera_mode
        return replace(options, hide_unrelated_geometry=True, camera_mode=widened)

    if occluded and options.hide_unrelated_geometry:
        # Isolation keeps BOTH sides, so whatever still stands in front of one
        # of them is the other: a pipe crossing the slab it passes through,
        # with the slab between it and every camera placed above. Hiding the
        # occluder is not available — it is half the subject.
        #
        # Two cures, in order of cost. Pull back first, in case the subject
        # was merely small. Then go underneath, which is the only thing that
        # works when the occluder is a horizontal surface — and it is worth
        # noting what is NOT on this list: `plan` looks straight down, so for
        # a service hanging below a slab it is the one view guaranteed to
        # fail, and spending a render on it costs the rung that would have
        # worked.
        following = {"closeup": "context", "context": "underside"}.get(
            options.camera_mode
        )
        if following:
            return replace(options, camera_mode=following)

    if misframed:
        wider = _widen(options.camera_mode)
        if wider != options.camera_mode:
            return replace(options, camera_mode=wider)
        # Out of rungs on the mode ladder; a bigger margin is the last lever
        # that changes the frame without changing the shot.
        if options.margin_percent < 150.0:
            return replace(options, margin_percent=min(150.0, options.margin_percent * 2 + 25.0))

    return None


def _widen(mode: str) -> str:
    # `underside` is last because it is not a wider shot, it is the same shot
    # from the other side of whatever is in the way. That only ever helps when
    # the obstruction is horizontal — a slab over a dropping pipe — so it is
    # tried once the ordinary rungs have failed rather than instead of them.
    return {"closeup": "context", "context": "plan", "plan": "underside"}.get(mode, mode)


# --------------------------------------------------------------- capture


@dataclass
class Attempt:
    options: ImageOptions
    verdict: Verdict
    pixels: PixelMetrics
    coverage: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "camera_mode": self.options.camera_mode,
            "margin_percent": self.options.margin_percent,
            "hide_unrelated_geometry": self.options.hide_unrelated_geometry,
            "ok": self.verdict.ok,
            "reasons": list(self.verdict.reasons),
            "error": self.error,
            "pixels": self.pixels.to_json(),
        }


@dataclass
class Capture:
    """One image and the complete record of how it was obtained."""

    clash_guid: str = ""
    png: bytes | None = None
    accepted: bool = False
    attempts: list[Attempt] = field(default_factory=list)
    options: ImageOptions | None = None
    colour_a: tuple[int, int, int] = COLOUR_MOVABLE
    colour_b: tuple[int, int, int] = COLOUR_FIXED
    framing: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    location: dict[str, Any] = field(default_factory=dict)
    document: dict[str, Any] = field(default_factory=dict)
    # What the addin did and what went wrong doing it. Dropped on the floor by
    # the first version of this class, which cost an hour of a live
    # investigation: the isolation step was reporting exactly why it had not
    # hidden anything, and the report said `null` because nothing copied it
    # across the bridge.
    visual: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def verdict(self) -> Verdict:
        return self.attempts[-1].verdict if self.attempts else Verdict(ok=False, reasons=["not_attempted"])

    @property
    def rejected(self) -> bool:
        """Rendered, and not good enough to publish."""
        return self.png is not None and not self.accepted

    def to_json(self) -> dict[str, Any]:
        return {
            "clash_guid": self.clash_guid,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "attempts": [a.to_json() for a in self.attempts],
            "effective": self.options.to_json() if self.options else {},
            "colors": {"side_a": to_hex(self.colour_a), "side_b": to_hex(self.colour_b)},
            "framing": self.framing,
            "coverage": self.coverage,
            "location": self.location,
            "document": self.document,
            "visual": self.visual,
            "notes": list(self.notes),
            "verdict": self.verdict.to_json(),
            "error": self.error,
        }


def decorate(
    png: bytes | None,
    capture_result: Capture,
    caption: str = "",
    legend: tuple[tuple[str, tuple[int, int, int]], ...] = (),
    coverage: dict[str, Any] | None = None,
) -> bytes | None:
    """Marker and caption, applied once the image has been judged.

    Order matters and is not incidental: the pixel check counts the two
    element colours, so anything drawn on top would be counted as geometry.
    Decorating only after the verdict keeps the measurement honest and the
    picture legible, which are otherwise in direct conflict.
    """
    if not png:
        return png
    options = capture_result.options
    if options is None:
        return png

    if options.show_clash_marker:
        # The coverage of the attempt that produced THIS image, which is not
        # always the last one: when every attempt is rejected the best frame is
        # kept, and marking it with the final attempt's screen coordinates drew
        # the box where the clash would have been in a picture nobody sees.
        source = coverage if coverage is not None else (capture_result.coverage or {})
        png = mark_clash(png, source.get("clash") or {})
    if options.show_level and (caption or legend):
        png = annotate(png, caption, legend)
    return png


def capture(
    fetch: Callable[[str, dict[str, Any]], dict[str, Any]],
    clash_guid: str,
    options: ImageOptions,
    *,
    discipline_a: str = "",
    discipline_b: str = "",
    responsible: str = "",
    caption: str = "",
) -> Capture:
    """Renders one clash, judging and re-shooting until it is usable.

    `fetch` is the bridge call, injected rather than imported: the retry ladder
    is the part with the logic in it, and a test has to be able to drive it
    with a stub that returns a known bad frame.
    """
    settings = options.normalised()
    colour_a, colour_b = colours_for(
        discipline_a, discipline_b, responsible, settings.colorize_by_discipline
    )

    result = Capture(
        clash_guid=clash_guid, options=settings, colour_a=colour_a, colour_b=colour_b
    )
    best: tuple[float, bytes, Attempt] | None = None
    attempt_options = settings

    for _ in range(settings.max_attempts):
        try:
            payload = fetch(clash_guid, attempt_options.payload(
                colour_a, colour_b, discipline_a, discipline_b))
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.attempts.append(
                Attempt(attempt_options, Verdict(ok=False, reasons=["bridge_error"]),
                        PixelMetrics(decoded=False, error=result.error), error=result.error)
            )
            return result

        if not isinstance(payload, dict) or payload.get("error"):
            detail = (payload or {}).get("detail") or (payload or {}).get("error") or "sin detalle"
            result.error = str(detail)
            result.attempts.append(
                Attempt(attempt_options, Verdict(ok=False, reasons=["render_failed"]),
                        PixelMetrics(decoded=False, error=result.error), error=result.error)
            )
            return result

        png = decode_image(payload)
        coverage = payload.get("coverage") or {}
        # The addin reports the colours it actually applied. Judging the pixels
        # against the colours that were REQUESTED would silently pass every
        # image on an addin that ignored them.
        applied_a, applied_b = _applied_colours(payload, colour_a, colour_b)
        pixels = analyse_pixels(png or b"", applied_a, applied_b)
        verdict = judge(coverage, pixels, attempt_options)

        attempt = Attempt(attempt_options, verdict, pixels, coverage)
        result.attempts.append(attempt)
        result.framing = payload.get("framing") or {}
        result.coverage = coverage
        result.location = payload.get("location") or {}
        result.document = payload.get("document") or {}
        result.visual = payload.get("visual") or {}
        result.notes = [str(note) for note in (payload.get("notes") or [])]
        result.colour_a, result.colour_b = applied_a, applied_b
        result.options = attempt_options

        if verdict.ok:
            result.accepted = True
            result.png = decorate(png, result, caption, _legend(
                discipline_a, discipline_b, applied_a, applied_b))
            return result

        # Keep the least-bad frame in case every rung fails and the caller
        # would rather have the best of them than nothing.
        score = min(pixels.side_a_fraction, pixels.side_b_fraction)
        if png and (best is None or score > best[0]):
            best = (score, png, attempt)

        following = next_attempt(attempt_options, verdict)

        # Checked BEFORE giving up, not after. Once isolation has been tried,
        # `next_attempt` has nothing left to offer for occlusion and returns
        # None — so a rung that turned out to be a no-op would end the ladder
        # rather than fall through to the one that still might work.
        if isolation_did_nothing(attempt_options, result.visual):
            wider = _widen(attempt_options.camera_mode)
            following = (
                None
                if wider == attempt_options.camera_mode
                else replace(
                    attempt_options, hide_unrelated_geometry=False, camera_mode=wider
                )
            )

        if following is None:
            break
        attempt_options = following

    if best is not None:
        result.options = best[2].options
        result.accepted = bool(settings.keep_rejected)
        # Decorated either way. A rejected frame is kept so somebody can look
        # at WHY it was rejected, and a marker showing where the clash was
        # supposed to be is most of that answer — so the coverage passed here
        # is the kept attempt's, not the last one's.
        result.coverage = best[2].coverage
        result.png = decorate(
            best[1], result, caption,
            _legend(discipline_a, discipline_b, result.colour_a, result.colour_b),
            coverage=best[2].coverage,
        )
    return result


def _legend(
    discipline_a: str,
    discipline_b: str,
    colour_a: tuple[int, int, int],
    colour_b: tuple[int, int, int],
) -> tuple[tuple[str, tuple[int, int, int]], ...]:
    """Two swatches that tell the reader which colour is which side.

    When both sides carry the same discipline — which happens on any model
    whose files the profile does not map, and on genuine intra-trade clashes —
    the labels are disambiguated. A legend reading "OTRO / OTRO" beside two
    different colours tells the reader nothing and looks like a bug.
    """
    if discipline_a and discipline_a == discipline_b:
        return ((f"A · {discipline_a}", colour_a), (f"B · {discipline_b}", colour_b))
    return ((discipline_a or "A", colour_a), (discipline_b or "B", colour_b))


def decode_image(payload: dict[str, Any]) -> bytes | None:
    """Turns an addin image response into PNG bytes."""
    raw = payload.get("base64") if isinstance(payload, dict) else None
    if not raw:
        return None
    try:
        return base64.b64decode(raw)
    except Exception:
        return None


def _applied_colours(
    payload: dict[str, Any],
    fallback_a: tuple[int, int, int],
    fallback_b: tuple[int, int, int],
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    sides = payload.get("sides") or {}
    return (
        _rgb((sides.get("a") or {}).get("color"), fallback_a),
        _rgb((sides.get("b") or {}).get("color"), fallback_b),
    )


def _rgb(value: Any, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return fallback
    try:
        return (int(value[0]), int(value[1]), int(value[2]))
    except (TypeError, ValueError):
        return fallback


# ------------------------------------------------------------- annotation


#: The clash marker's colour. Deliberately outside the discipline palette and
#: outside the greys, so it can never be counted as either element — the pixel
#: check runs before the marker is drawn, but a future reader should not have
#: to know that to be safe.
MARKER_COLOUR = (255, 235, 59)


def mark_clash(png: bytes, rect: dict[str, Any], colour=MARKER_COLOUR) -> bytes:
    """Draws a box around the interference volume.

    Navisworks will not do this for us. `ScenePlusOverlay` renders the overlay
    layer — clash markers included — but only for the result the Clash
    Detective currently has SELECTED, and the 2026 Clash API exposes no way to
    select one: `TestsViewpointForResult` is the only member that takes a
    result at all. So an automated render carries an empty overlay, and every
    picture came back with no marker on it.

    Drawing it here needs no new information. The add-in already reports where
    the clash box lands on screen, in normalised coordinates, because that is
    how it decides whether the frame is any good — so the marker sits exactly
    where the quality check says the interference is, and the two can never
    disagree.
    """
    if not png or not rect:
        return png
    if not rect.get("in_frame", False):
        return png

    try:
        from PIL import Image, ImageDraw
    except ImportError:  # pragma: no cover - declared dependency
        return png

    try:
        with Image.open(BytesIO(png)) as raw:
            image = raw.convert("RGB")
            width, height = image.size

            def to_x(ndc: float) -> float:
                return (float(ndc) + 1.0) / 2.0 * width

            def to_y(ndc: float) -> float:
                # Screen y grows downwards; normalised y grows upwards.
                return (1.0 - float(ndc)) / 2.0 * height

            left, right = to_x(rect.get("min_x", 0)), to_x(rect.get("max_x", 0))
            top, bottom = to_y(rect.get("max_y", 0)), to_y(rect.get("min_y", 0))

            # A clash smaller than a few pixels needs a marker BIGGER than
            # itself to be findable; a box drawn to scale would be invisible,
            # which is the failure the marker exists to prevent.
            floor = max(12.0, min(width, height) * 0.04)
            if right - left < floor:
                middle = (left + right) / 2
                left, right = middle - floor / 2, middle + floor / 2
            if bottom - top < floor:
                middle = (top + bottom) / 2
                top, bottom = middle - floor / 2, middle + floor / 2

            pad = max(4.0, min(width, height) * 0.012)
            left, top = max(0.0, left - pad), max(0.0, top - pad)
            right = min(width - 1.0, right + pad)
            bottom = min(height - 1.0, bottom + pad)

            draw = ImageDraw.Draw(image)
            thickness = max(2, int(min(width, height) * 0.004))
            draw.rectangle([left, top, right, bottom], outline=tuple(colour), width=thickness)

            out = BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return png


def annotate(
    png: bytes,
    caption: str,
    legend: tuple[tuple[str, tuple[int, int, int]], ...] = (),
) -> bytes:
    """Burns the issue's identity and colour legend into the picture.

    Images leave reports. They get pasted into a chat, forwarded, printed and
    pinned to a wall, and at that point the page that said which level this was
    and what red meant is gone. A caption costs a strip of sky and survives
    every one of those journeys.

    Returns the original bytes unchanged if anything goes wrong: a missing
    caption is a blemish, a lost image is a hole in the report.
    """
    if not png or (not caption and not legend):
        return png
    try:
        from PIL import Image, ImageDraw
    except ImportError:  # pragma: no cover - declared dependency
        return png

    try:
        with Image.open(BytesIO(png)) as raw:
            image = raw.convert("RGB")
            width, height = image.size
            band = max(22, height // 22)
            font = _font(int(band * 0.62))

            draw = ImageDraw.Draw(image)
            draw.rectangle([0, height - band, width, height], fill=(24, 24, 24))

            x = int(band * 0.4)
            baseline = height - band + (band - int(band * 0.62)) // 2
            if caption:
                draw.text((x, baseline), caption, fill=(240, 240, 240), font=font)
                x += int(draw.textlength(caption, font=font)) + int(band * 0.9)

            for label, colour in legend:
                swatch = int(band * 0.5)
                top = height - band + (band - swatch) // 2
                draw.rectangle([x, top, x + swatch, top + swatch], fill=tuple(colour))
                x += swatch + int(band * 0.25)
                draw.text((x, baseline), label, fill=(240, 240, 240), font=font)
                x += int(draw.textlength(label, font=font)) + int(band * 0.7)

            out = BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return png


def _font(size: int):
    from PIL import ImageFont

    try:
        # Pillow 10.1 made the built-in font scalable. Before that the only
        # size available is a 11px bitmap, which on a 1200px render is a smear
        # — legible enough to keep, not worth failing over.
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()
