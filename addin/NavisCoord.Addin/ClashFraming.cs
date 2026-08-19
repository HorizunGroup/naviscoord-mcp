using System;
using System.Collections.Generic;
using System.Globalization;

namespace NavisCoord
{
    /// <summary>
    /// Where the camera goes for a clash picture, and whether the picture it
    /// would produce is worth keeping.
    /// </summary>
    /// <remarks>
    /// Not one Autodesk reference in this file, deliberately. Framing is the
    /// part that was wrong — cameras parked inside the geometry, or so far
    /// back that the clash was three pixels of a building — and a failure of
    /// framing does not throw: it returns a perfectly valid PNG of the wrong
    /// thing. That class of bug is only caught by asserting the numbers, and
    /// the numbers can only be asserted on a runner with no licence if the
    /// arithmetic lives away from the API.
    ///
    /// The previous rule was <c>distance = span * max(1.2, zoom_out)</c> over
    /// the clash box alone. It fails in both directions on real projects:
    ///
    /// * A duct clipping a beam by 8 mm has a span of 0.008 m, so the camera
    ///   was placed 18 mm from the target — inside the duct. The render is a
    ///   flat grey rectangle.
    /// * A clash box that Navisworks reports across a whole composite spans
    ///   tens of metres, so the camera retreats far enough to photograph the
    ///   building, and the two elements the report is about are invisible.
    ///
    /// Both are fixed by separating three decisions that the old formula
    /// conflated:
    ///
    /// 1. **What must be in frame** — a box, not a number. The clash volume,
    ///    plus the part of each element NEAR the clash, so both sides are
    ///    identifiable without dragging a 60 m duct into the shot.
    /// 2. **How wide the frame is** — the extents at the focal distance,
    ///    computed from that box and the image's aspect ratio.
    /// 3. **How far the camera stands** — a separate, clamped number that
    ///    only controls perspective foreshortening, because Navisworks lets
    ///    the extents be set independently of the distance.
    ///
    /// Splitting 2 from 3 is what makes <c>min_distance</c> and
    /// <c>max_distance</c> safe to expose: clamping the distance no longer
    /// silently changes what is in the picture.
    /// </remarks>
    internal static class ClashFraming
    {
        public const string ModeCloseup = "closeup";
        public const string ModeContext = "context";
        public const string ModePlan = "plan";

        /// <summary>
        /// The context shot taken from below the clash instead of above it.
        /// </summary>
        /// <remarks>
        /// Every other mode looks down. That is right for most coordination
        /// pictures and hopeless for the commonest case of all: a service
        /// dropping through a slab, where the slab is a horizontal plane
        /// between the camera and the pipe. Widening does not help — a wider
        /// shot of the top of a slab is still the top of a slab — and neither
        /// does isolation, because the occluder IS the other half of the
        /// clash and hiding it would hide the subject.
        ///
        /// Measured on a live model: a copper pipe crossing a slab, 2.25 m of
        /// pipe against 178 mm of concrete, so 1.29 m of it hanging in clear
        /// air underneath. Zero orange pixels from closeup, context and plan;
        /// the whole length of it visible from below.
        /// </remarks>
        public const string ModeUnderside = "underside";

        /// <summary>How far around the clash each mode is willing to look.</summary>
        /// <remarks>
        /// A fraction of the clash's own size rather than a fixed distance: a
        /// 20 mm sprinkler hit and a 3 m shaft crossing need frames of very
        /// different size, and any absolute number is wrong for one of them.
        ///
        /// These are FRACTIONS, and the first version of this file got that
        /// wrong: at 1.5 the neighbourhood grew by one and a half clash-widths
        /// on every face, so a 10 m clash was framed 87 m wide and the smaller
        /// element came back as 1.5% of the picture. Measured on bathcity, not
        /// reasoned about — the numbers are in TESTING.md.
        ///
        /// The floor is `MinExtent`, which is what keeps a millimetric clash
        /// from being framed at millimetric scale.
        /// </remarks>
        private const double CloseupNeighbourhood = 0.35;
        private const double ContextNeighbourhood = 1.5;

        // ------------------------------------------------------------ input

        internal sealed class Request
        {
            /// <summary>The interference volume. Never empty by the time it gets here.</summary>
            public Box3 ClashBox = Box3.Empty;
            public Box3 BoxA = Box3.Empty;
            public Box3 BoxB = Box3.Empty;
            public string Mode = ModeCloseup;

            /// <summary>Padding around the fitted box, as a percentage of it.</summary>
            public double MarginPercent = 25.0;

            /// <summary>
            /// Floor on the framed box, in document units. A clash between two
            /// thin elements has a near-zero box and would otherwise be framed
            /// at millimetre scale — technically centred, unreadable in print.
            /// </summary>
            public double MinExtent = 0.8;

            public double MinDistance = 1.5;
            public double MaxDistance = 60.0;

            /// <summary>Image width / height. Drives which extent binds.</summary>
            public double AspectRatio = 1.5;

            /// <summary>Vertical field of view in radians, perspective only.</summary>
            public double FieldOfView = 0.7853981633974483; // 45°

            /// <summary>The document's own up vector; not assumed to be +Z.</summary>
            public Vec3 WorldUp = new Vec3(0, 0, 1);
        }

        // ----------------------------------------------------------- output

        internal sealed class Result
        {
            public Vec3 Target;
            public Vec3 Position;
            public Vec3 Direction;   // unit, from camera towards the target
            public Vec3 Up;          // unit, screen up
            public Vec3 Right;       // unit, screen right
            public double Distance;
            public double ExtentWidth;   // full width at the focal distance
            public double ExtentHeight;  // full height at the focal distance
            public bool Orthographic;
            public Box3 FitBox = Box3.Empty;
            public string Mode = ModeCloseup;
            public List<string> Notes = new List<string>();

            /// <summary>Half-extents, which is what the projection actually uses.</summary>
            public double HalfWidth => ExtentWidth / 2.0;
            public double HalfHeight => ExtentHeight / 2.0;
        }

        /// <summary>Where a box lands on screen, in normalised [-1, 1] coordinates.</summary>
        internal sealed class ScreenRect
        {
            public double MinX, MinY, MaxX, MaxY;
            public bool AnyBehindCamera;
            public bool Empty = true;

            /// <summary>Overlaps the frame at all.</summary>
            public bool InFrame => !Empty && MaxX > -1.0 && MinX < 1.0 && MaxY > -1.0 && MinY < 1.0;

            /// <summary>Sits entirely inside the frame.</summary>
            public bool Contained =>
                !Empty && !AnyBehindCamera &&
                MinX >= -1.0 && MaxX <= 1.0 && MinY >= -1.0 && MaxY <= 1.0;

            /// <summary>
            /// Share of the frame the box's screen rectangle covers, clipped
            /// to the frame. The honest upper bound on how big the element can
            /// look: real geometry is thinner than its box, never fatter, so a
            /// side failing a minimum here cannot pass in pixels.
            /// </summary>
            public double ScreenFraction
            {
                get
                {
                    if (!InFrame) return 0.0;
                    var w = Math.Min(MaxX, 1.0) - Math.Max(MinX, -1.0);
                    var h = Math.Min(MaxY, 1.0) - Math.Max(MinY, -1.0);
                    if (w <= 0 || h <= 0) return 0.0;
                    return (w * h) / 4.0;
                }
            }

            public Dictionary<string, object> ToJson()
                => new Dictionary<string, object>
                {
                    ["in_frame"] = InFrame,
                    ["contained"] = Contained,
                    ["screen_fraction"] = Round(ScreenFraction),
                    ["behind_camera"] = AnyBehindCamera,
                    ["min_x"] = Round(MinX),
                    ["max_x"] = Round(MaxX),
                    ["min_y"] = Round(MinY),
                    ["max_y"] = Round(MaxY)
                };
        }

        // ------------------------------------------------------------ frame

        public static Result Frame(Request request)
        {
            if (request == null) throw new ArgumentNullException(nameof(request));

            var mode = Normalise(request.Mode);
            var result = new Result { Mode = mode };

            var clash = request.ClashBox.IsEmpty
                ? new Box3(new Vec3(0, 0, 0), new Vec3(0, 0, 0))
                : request.ClashBox;

            // The camera aims at the interference itself, never at the centre
            // of the fitted box. Those differ whenever one element is longer
            // than the other, and aiming at the box centre walks the actual
            // clash off towards a corner — which is how a "correctly framed"
            // picture still fails to show what it is about.
            result.Target = clash.Center;

            var minExtent = Math.Max(1e-6, request.MinExtent);
            var span = Math.Max(clash.LongestSide, minExtent);

            // Proportional to the span and nothing else. `minExtent` used to
            // be a floor here as well, and it is already inside `span` — so a
            // 54 mm clash was widened to the 0.8 m minimum, then given 0.8 m
            // of reach on every side, and the frame started at 2.4 m before
            // margin and aspect had their turn. The floor was doing its job
            // twice and the picture paid for it: 7.1 m of frame for a pipe the
            // width of a thumb.
            var reach = span * (mode == ModeCloseup ? CloseupNeighbourhood : ContextNeighbourhood);

            // Only the part of each element near the clash is allowed to
            // enlarge the frame. Without this clamp a single 60 m duct sets
            // the extents and the picture becomes an aerial photograph — the
            // exact complaint that started this work.
            var neighbourhood = clash.Expanded(reach);
            var fit = clash;
            fit = Absorb(fit, request.BoxA, neighbourhood, result, "a");
            fit = Absorb(fit, request.BoxB, neighbourhood, result, "b");

            fit = fit.AtLeast(minExtent);
            var margin = Math.Max(0.0, request.MarginPercent) / 100.0;
            fit = fit.Scaled(1.0 + margin);
            result.FitBox = fit;

            // ------------------------------------------------- orientation
            var worldUp = request.WorldUp.Normalized(new Vec3(0, 0, 1));

            // Plan looks down in PERSPECTIVE, not orthographically, and that
            // is the whole difference between a usable top-down shot and a
            // white rectangle.
            //
            // An orthographic view volume is a box: everything above the clash
            // is inside it whatever the camera's height, because in that
            // projection the camera's position does not exclude anything. On a
            // plenum that means the slab and every service above it are drawn
            // in front of the clash, each one at the context fade, and the
            // fades multiply until both elements wash out. Measured on a live
            // federation: 0.0% of the frame for BOTH sides, at every camera
            // height tried, while the three-quarter view of the same clash was
            // fine. Isolation could not rescue it either.
            //
            // In perspective, geometry behind the lens is simply not drawn —
            // so a camera placed just above the clash, inside the plenum,
            // leaves the slab out of the picture by construction.
            result.Orthographic = false;
            var direction = mode == ModePlan
                ? worldUp.Negated()
                : ThreeQuarterDirection(request.BoxA, request.BoxB, worldUp);
            if (mode == ModeUnderside) direction = Mirrored(direction, worldUp);

            var up = mode == ModePlan
                ? PlanUp(worldUp)
                : worldUp;
            // Degenerate basis: looking straight along the up vector leaves
            // the cross product at zero and every screen coordinate NaN.
            if (Math.Abs(direction.Dot(up)) > 0.98)
            {
                up = PlanUp(direction);
            }

            var right = direction.Cross(up).Normalized(new Vec3(1, 0, 0));
            up = right.Cross(direction).Normalized(worldUp);

            result.Direction = direction;
            result.Right = right;
            result.Up = up;

            // ----------------------------------------------------- extents
            // Measured from the TARGET, not from the fitted box's centre, so
            // that centring the clash and containing the box are the same
            // statement rather than two that fight each other.
            double halfW = 0.0, halfH = 0.0;
            foreach (var corner in fit.Corners())
            {
                var rel = corner.Minus(result.Target);
                halfW = Math.Max(halfW, Math.Abs(rel.Dot(right)));
                halfH = Math.Max(halfH, Math.Abs(rel.Dot(up)));
            }
            halfW = Math.Max(halfW, minExtent / 2.0);
            halfH = Math.Max(halfH, minExtent / 2.0);

            var aspect = request.AspectRatio > 1e-6 ? request.AspectRatio : 1.0;
            if (halfW / aspect > halfH) halfH = halfW / aspect;
            else halfW = halfH * aspect;

            result.ExtentWidth = halfW * 2.0;
            result.ExtentHeight = halfH * 2.0;

            // ---------------------------------------------------- distance
            var fov = request.FieldOfView;
            if (fov <= 0.01 || fov >= Math.PI - 0.01) fov = 0.7853981633974483;
            var natural = halfH / Math.Tan(fov / 2.0);

            // The box has depth too: standing at the distance that fits the
            // silhouette puts the camera inside a clash volume that is deep
            // along the view axis. Half the depth, plus a nose margin, is the
            // closest the camera may stand whatever the caller asked for.
            var depth = 0.0;
            foreach (var corner in fit.Corners())
            {
                depth = Math.Max(depth, Math.Abs(corner.Minus(result.Target).Dot(direction)));
            }

            // The geometry itself sets a floor that no option may go under:
            // standing closer than half the box's own depth puts the lens
            // inside the clash. When max_distance asks for less than that, the
            // geometry wins and the response says so — a camera obediently
            // parked inside a duct honours the option and returns grey.
            // Plan ignores min_distance deliberately. For the other modes the
            // distance only flattens perspective, so a floor keeps the shot
            // readable; looking straight down it decides WHAT ENDS UP BETWEEN
            // the lens and the clash, and in a building standing further back
            // means putting a floor slab there. The geometric floor — clear of
            // the clash volume itself — is as far as plan is willing to go.
            var geometryFloor = depth * 1.25 + minExtent * 0.5;
            var floor = mode == ModePlan
                ? geometryFloor
                : Math.Max(request.MinDistance, geometryFloor);
            var ceiling = request.MaxDistance;
            if (ceiling < floor)
            {
                result.Notes.Add("max_distance_below_geometry_floor");
                ceiling = floor;
            }

            var distance = natural;
            if (distance < floor)
            {
                distance = floor;
                result.Notes.Add("distance_raised_to_minimum");
            }
            if (distance > ceiling)
            {
                distance = ceiling;
                // Not a warning: the extents below still frame the box, so
                // this only flattens the perspective. It is reported because
                // "why does this one look orthographic" is otherwise a mystery.
                result.Notes.Add("distance_capped_at_maximum");
            }

            result.Distance = distance;
            result.Position = result.Target.Minus(direction.Scaled(distance));

            // The extents above were measured on the focal plane, where the
            // projection is affine. It is not: a corner of the box nearer the
            // camera than the focal distance projects LARGER, and lands
            // outside a frame the affine arithmetic said it fitted.
            //
            // Measured on a live federation: a picture showing both elements
            // plainly was rejected for `clash_not_contained` and re-shot twice
            // before being discarded, because the near face of the clash box
            // projected past the edge. Two wasted renders and a good image
            // thrown away, on a frame that was correct.
            //
            // The correction is exact and needs no iteration: distance and
            // position are already fixed, so each corner states the half-width
            // that would contain it, and the largest wins. Extents only ever
            // grow here, so the clash stays centred and the box stays inside.
            // Over the CLASH box, not the fitted one. What has to be whole in
            // the picture is the interference; the elements run through it and
            // out of frame by design. Correcting for the fitted box instead
            // forces the near corners of a long duct inside, which roughly
            // doubles the frame and undoes the tight framing this file exists
            // for — trading one bad picture for another.
            if (!result.Orthographic)
            {
                var near = Math.Max(distance * 0.01, 1e-6);
                foreach (var corner in clash.Corners())
                {
                    var rel = corner.Minus(result.Position);
                    var along = rel.Dot(direction);
                    if (along <= near) continue;   // the near-plane clip handles these
                    var shrink = along / distance;
                    halfW = Math.Max(halfW, Math.Abs(rel.Dot(right)) / shrink);
                    halfH = Math.Max(halfH, Math.Abs(rel.Dot(up)) / shrink);
                }

                if (halfW / aspect > halfH) halfH = halfW / aspect;
                else halfW = halfH * aspect;

                result.ExtentWidth = halfW * 2.0;
                result.ExtentHeight = halfH * 2.0;
            }

            return result;
        }

        /// <summary>
        /// Adds the part of an element that sits near the clash.
        /// </summary>
        /// <remarks>
        /// Records a note when an element's box does not reach the
        /// neighbourhood at all. That happens with composite geometry whose
        /// reported box belongs to a parent somewhere else in the model, and
        /// it is precisely the case where the render comes back showing one
        /// element and a lot of nothing. Naming it lets the caller widen to
        /// context instead of shipping the picture.
        /// </remarks>
        /// <summary>
        /// The volume where two elements interfere, from their bounds.
        /// </summary>
        /// <remarks>
        /// For a hard clash this is arithmetic, not a lookup: the interference
        /// IS the intersection of the two boxes. Navisworks also reports a
        /// box on the result, and it answers a different question — the extent
        /// of the whole result rather than of the overlap. Trusting it first
        /// put a 54 mm copper pipe against a slab at 8.5 x 11 m, which pulled
        /// the camera back to a 35 m frame and rendered the pipe at 0.02% of
        /// the picture.
        ///
        /// <paramref name="reported"/> is therefore only consulted when there
        /// is no intersection to compute — a clearance test — and only when it
        /// could plausibly be a clash volume at all.
        ///
        /// Returns <see cref="Box3.Empty"/> when neither source yields
        /// anything usable, leaving the caller to fall back on the contact
        /// point, which is always available.
        /// </remarks>
        public static Box3 ClashVolume(Box3 boxA, Box3 boxB, Box3 reported, double minimum)
        {
            var overlap = boxA.Intersection(boxB);
            if (!overlap.IsEmpty) return overlap.AtLeast(minimum);

            if (!reported.IsEmpty && IsPlausibleClashVolume(reported, boxA, boxB))
            {
                return reported.AtLeast(minimum);
            }

            return Box3.Empty;
        }

        /// <summary>
        /// Could this box be where two elements meet?
        /// </summary>
        /// <remarks>
        /// The invariant is physical: whatever two elements do to each other
        /// happens inside the smaller of them, so a candidate wider than that
        /// is measuring something else. Twice the smaller side is slack for
        /// reporting conventions that pad the box; an order of magnitude is
        /// not slack, it is a different quantity.
        /// </remarks>
        public static bool IsPlausibleClashVolume(Box3 candidate, Box3 boxA, Box3 boxB)
        {
            if (candidate.IsEmpty) return false;
            if (boxA.IsEmpty || boxB.IsEmpty) return true;
            var smaller = Math.Min(boxA.LongestSide, boxB.LongestSide);
            if (smaller <= 1e-9) return true;
            return candidate.LongestSide <= smaller * 2.0;
        }

        private static Box3 Absorb(Box3 fit, Box3 side, Box3 neighbourhood, Result result, string label)
        {
            if (side.IsEmpty)
            {
                result.Notes.Add("side_" + label + "_without_bounds");
                return fit;
            }
            var near = side.Intersection(neighbourhood);
            if (near.IsEmpty)
            {
                result.Notes.Add("side_" + label + "_outside_neighbourhood");
                return fit;
            }
            return fit.Union(near);
        }

        /// <summary>
        /// A viewing direction that puts the two elements side by side.
        /// </summary>
        /// <remarks>
        /// A fixed three-quarter direction is right most of the time and
        /// catastrophically wrong the rest: when the two elements are stacked
        /// along the view axis — a riser through a slab seen from above and to
        /// the side is the common one — the near element hides the far one and
        /// the picture shows a single object with nothing to argue about.
        ///
        /// So the preferred direction is projected perpendicular to the line
        /// joining the two element centres. The separation then lies across
        /// the screen and both sides are visible. When the centres coincide
        /// (concentric geometry) there is no such line and the preference is
        /// used unchanged.
        /// </remarks>
        private static Vec3 ThreeQuarterDirection(Box3 a, Box3 b, Vec3 worldUp)
        {
            // Camera above, in front and to one side; the direction is where
            // it LOOKS, so the signs are the negative of its position offset.
            var preferred = new Vec3(-1.0, 1.0, -0.6).Normalized(new Vec3(0, 1, 0));
            if (Math.Abs(worldUp.Z) < 0.5)
            {
                // A Y-up document: keep the same shot, expressed in its axes.
                preferred = new Vec3(-1.0, -0.6, 1.0).Normalized(new Vec3(0, 0, 1));
            }

            if (a.IsEmpty || b.IsEmpty) return preferred;

            var separation = b.Center.Minus(a.Center);
            if (separation.Length < 1e-6) return preferred;

            var axis = separation.Normalized(preferred);
            var projected = preferred.Minus(axis.Scaled(preferred.Dot(axis)));
            if (projected.Length < 1e-3)
            {
                // The preference is parallel to the separation: any
                // perpendicular will do, and one that keeps some elevation
                // reads better than an arbitrary one.
                projected = axis.Cross(worldUp);
                if (projected.Length < 1e-3) projected = axis.Cross(new Vec3(1, 0, 0));
            }

            var direction = projected.Normalized(preferred);

            // Keep a slight downward tilt. A dead-level shot of a plenum is
            // legal and unreadable: everything overlaps on one horizon line.
            var tilt = direction.Dot(worldUp);
            if (tilt > -0.12)
            {
                direction = direction.Minus(worldUp.Scaled(tilt)).Normalized(direction);
                direction = direction.Scaled(0.9).Minus(worldUp.Scaled(0.35)).Normalized(direction);
            }
            return direction;
        }

        /// <summary>
        /// The same shot from the other side of the horizontal plane.
        /// </summary>
        /// <remarks>
        /// Flips only the component along world up, so the camera keeps its
        /// bearing and its distance and simply moves from above the clash to
        /// below it. A view that was looking down at 20 degrees now looks up
        /// at 20 degrees, which is what puts a slab behind the lens instead of
        /// in front of it.
        /// </remarks>
        private static Vec3 Mirrored(Vec3 direction, Vec3 worldUp)
        {
            var elevation = direction.Dot(worldUp);
            if (Math.Abs(elevation) < 1e-6) return direction;
            return direction.Minus(worldUp.Scaled(2.0 * elevation)).Normalized(direction);
        }

        private static Vec3 PlanUp(Vec3 direction)
        {
            // Any vector not parallel to the direction; prefer the one that
            // keeps north up on a plan of a normally-oriented project.
            var candidate = Math.Abs(direction.Y) < 0.9 ? new Vec3(0, 1, 0) : new Vec3(1, 0, 0);
            var right = direction.Cross(candidate).Normalized(new Vec3(1, 0, 0));
            return right.Cross(direction).Normalized(candidate);
        }

        public static string Normalise(string mode)
        {
            if (string.IsNullOrWhiteSpace(mode)) return ModeCloseup;
            switch (mode.Trim().ToLowerInvariant())
            {
                case ModeContext:
                case "contexto":
                    return ModeContext;
                case ModePlan:
                case "planta":
                case "top":
                    return ModePlan;
                case ModeUnderside:
                case "abajo":
                case "bottom":
                    return ModeUnderside;
                default:
                    return ModeCloseup;
            }
        }

        /// <summary>The mode to try when a frame comes back unusable.</summary>
        /// <remarks>
        /// A ladder rather than a retry of the same shot: repeating an
        /// identical render produces an identical failure, and the caller has
        /// paid a Navisworks render for it. Closeup widens to context, and
        /// context — which already contains both whole elements — can only be
        /// failing because the two sides overlap along the view axis, so the
        /// last attempt looks down instead.
        /// </remarks>
        public static string Widen(string mode)
        {
            switch (Normalise(mode))
            {
                case ModeCloseup: return ModeContext;
                case ModeContext: return ModePlan;
                // Last rung, and the only one that moves the camera to the
                // other side of the obstruction rather than further from it.
                case ModePlan: return ModeUnderside;
                default: return null;
            }
        }

        // ------------------------------------------------------- projection

        /// <summary>
        /// Where a box lands on screen under a computed frame.
        /// </summary>
        /// <remarks>
        /// Exact, not an approximation: the perspective divide is applied per
        /// corner. An affine version agrees near the centre of the frame and
        /// disagrees exactly where it matters — at the edges, which is where
        /// the question "is the element still in shot" is decided.
        ///
        /// This is deliberately NOT <c>View.ProjectPoint</c>. That projects
        /// through the interactive viewport, whose aspect ratio is whatever
        /// the Navisworks window happens to be, while the render is produced
        /// at the caller's requested pixel size. Checking one and shipping the
        /// other is how a check passes on a picture that is wrong.
        /// </remarks>
        public static ScreenRect Project(Result frame, Box3 box)
        {
            var rect = new ScreenRect();
            if (frame == null || box.IsEmpty) return rect;

            var halfW = Math.Max(frame.HalfWidth, 1e-9);
            var halfH = Math.Max(frame.HalfHeight, 1e-9);
            var distance = Math.Max(frame.Distance, 1e-9);
            var near = Math.Max(distance * 0.01, 1e-6);

            var corners = new List<Vec3>(box.Corners());
            if (corners.Count != 8) return rect;

            var depths = new double[8];
            for (var i = 0; i < 8; i++)
            {
                depths[i] = corners[i].Minus(frame.Position).Dot(frame.Direction);
                if (depths[i] <= near) rect.AnyBehindCamera = true;
            }

            // The eight corners are not enough on their own. A 60 m duct
            // passing beside the camera has four corners behind the lens and
            // four so far ahead that perspective pulls them back towards the
            // centre of the frame — so a corner-only rectangle reported the
            // duct as OUT of a picture it crosses from edge to edge. Clipping
            // each edge against the near plane adds the vertices that are
            // actually on screen, and those are the ones that decide it.
            var points = new List<Vec3>(12);
            for (var i = 0; i < 8; i++)
            {
                if (!frame.Orthographic && depths[i] <= near) continue;
                points.Add(corners[i]);
            }

            if (!frame.Orthographic)
            {
                foreach (var edge in BoxEdges)
                {
                    var a = edge[0];
                    var b = edge[1];
                    var da = depths[a] - near;
                    var db = depths[b] - near;
                    if ((da > 0) == (db > 0)) continue;
                    var t = da / (da - db);
                    var delta = corners[b].Minus(corners[a]);
                    points.Add(corners[a].Plus(delta.Scaled(t)));
                }
            }

            foreach (var point in points)
            {
                var rel = point.Minus(frame.Position);
                var scale = frame.Orthographic
                    ? 1.0
                    : Math.Max(rel.Dot(frame.Direction), near) / distance;

                var x = rel.Dot(frame.Right) / (halfW * scale);
                var y = rel.Dot(frame.Up) / (halfH * scale);

                if (rect.Empty)
                {
                    rect.MinX = rect.MaxX = x;
                    rect.MinY = rect.MaxY = y;
                    rect.Empty = false;
                }
                else
                {
                    rect.MinX = Math.Min(rect.MinX, x);
                    rect.MaxX = Math.Max(rect.MaxX, x);
                    rect.MinY = Math.Min(rect.MinY, y);
                    rect.MaxY = Math.Max(rect.MaxY, y);
                }
            }

            return rect;
        }

        /// <summary>The twelve edges of a box, as indices into its corners.</summary>
        /// <remarks>
        /// Corner index bits are (x, y, z), matching <see cref="Box3.Corners"/>:
        /// two corners share an edge exactly when their indices differ in one
        /// bit.
        /// </remarks>
        private static readonly int[][] BoxEdges = BuildEdges();

        private static int[][] BuildEdges()
        {
            var edges = new List<int[]>(12);
            for (var i = 0; i < 8; i++)
            {
                foreach (var bit in new[] { 1, 2, 4 })
                {
                    var j = i | bit;
                    if (j != i) edges.Add(new[] { i, j });
                }
            }
            return edges.ToArray();
        }

        internal static double Round(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value)) return 0.0;
            return Math.Round(value, 4, MidpointRounding.AwayFromZero);
        }

        // ------------------------------------------------------- primitives

        /// <summary>A point or a direction. Value type: these are copied, never shared.</summary>
        internal struct Vec3
        {
            public readonly double X, Y, Z;

            public Vec3(double x, double y, double z)
            {
                X = x; Y = y; Z = z;
            }

            public double Length => Math.Sqrt(X * X + Y * Y + Z * Z);

            public Vec3 Plus(Vec3 other) => new Vec3(X + other.X, Y + other.Y, Z + other.Z);
            public Vec3 Minus(Vec3 other) => new Vec3(X - other.X, Y - other.Y, Z - other.Z);
            public Vec3 Scaled(double k) => new Vec3(X * k, Y * k, Z * k);
            public Vec3 Negated() => new Vec3(-X, -Y, -Z);
            public double Dot(Vec3 other) => X * other.X + Y * other.Y + Z * other.Z;

            public Vec3 Cross(Vec3 o)
                => new Vec3(Y * o.Z - Z * o.Y, Z * o.X - X * o.Z, X * o.Y - Y * o.X);

            /// <summary>Unit vector, or the fallback when this one has no direction.</summary>
            public Vec3 Normalized(Vec3 fallback)
            {
                var length = Length;
                if (length < 1e-12 || double.IsNaN(length)) return fallback;
                return new Vec3(X / length, Y / length, Z / length);
            }

            public double[] ToArray() => new[] { Round(X), Round(Y), Round(Z) };

            public double[] ToArray(double scale)
                => new[] { Round(X * scale), Round(Y * scale), Round(Z * scale) };

            public override string ToString()
                => string.Format(CultureInfo.InvariantCulture, "({0:0.###}, {1:0.###}, {2:0.###})", X, Y, Z);
        }

        /// <summary>An axis-aligned box that knows how to be empty.</summary>
        internal struct Box3
        {
            public readonly Vec3 Min, Max;
            private readonly bool _set;

            public Box3(Vec3 min, Vec3 max)
            {
                Min = new Vec3(Math.Min(min.X, max.X), Math.Min(min.Y, max.Y), Math.Min(min.Z, max.Z));
                Max = new Vec3(Math.Max(min.X, max.X), Math.Max(min.Y, max.Y), Math.Max(min.Z, max.Z));
                _set = true;
            }

            public static Box3 Empty => default(Box3);

            public bool IsEmpty => !_set;

            public Vec3 Center => IsEmpty
                ? new Vec3(0, 0, 0)
                : new Vec3((Min.X + Max.X) / 2, (Min.Y + Max.Y) / 2, (Min.Z + Max.Z) / 2);

            public Vec3 Size => IsEmpty
                ? new Vec3(0, 0, 0)
                : new Vec3(Max.X - Min.X, Max.Y - Min.Y, Max.Z - Min.Z);

            public double LongestSide
            {
                get
                {
                    var size = Size;
                    return Math.Max(size.X, Math.Max(size.Y, size.Z));
                }
            }

            public double Diagonal
            {
                get
                {
                    var size = Size;
                    return Math.Sqrt(size.X * size.X + size.Y * size.Y + size.Z * size.Z);
                }
            }

            public Box3 Union(Box3 other)
            {
                if (IsEmpty) return other;
                if (other.IsEmpty) return this;
                return new Box3(
                    new Vec3(Math.Min(Min.X, other.Min.X), Math.Min(Min.Y, other.Min.Y), Math.Min(Min.Z, other.Min.Z)),
                    new Vec3(Math.Max(Max.X, other.Max.X), Math.Max(Max.Y, other.Max.Y), Math.Max(Max.Z, other.Max.Z)));
            }

            /// <summary>The shared volume, or empty when they do not overlap.</summary>
            public Box3 Intersection(Box3 other)
            {
                if (IsEmpty || other.IsEmpty) return Empty;
                var min = new Vec3(
                    Math.Max(Min.X, other.Min.X), Math.Max(Min.Y, other.Min.Y), Math.Max(Min.Z, other.Min.Z));
                var max = new Vec3(
                    Math.Min(Max.X, other.Max.X), Math.Min(Max.Y, other.Max.Y), Math.Min(Max.Z, other.Max.Z));
                if (max.X < min.X || max.Y < min.Y || max.Z < min.Z) return Empty;
                return new Box3(min, max);
            }

            /// <summary>Grown by an absolute amount on every face.</summary>
            public Box3 Expanded(double amount)
            {
                if (IsEmpty) return Empty;
                var d = new Vec3(amount, amount, amount);
                return new Box3(Min.Minus(d), Max.Plus(d));
            }

            /// <summary>Grown about its own centre by a factor.</summary>
            public Box3 Scaled(double factor)
            {
                if (IsEmpty) return Empty;
                var centre = Center;
                var half = Size.Scaled(factor / 2.0);
                return new Box3(centre.Minus(half), centre.Plus(half));
            }

            /// <summary>At least this big on every axis, keeping the centre.</summary>
            public Box3 AtLeast(double minimum)
            {
                if (IsEmpty) return Empty;
                var centre = Center;
                var size = Size;
                var half = new Vec3(
                    Math.Max(size.X, minimum) / 2.0,
                    Math.Max(size.Y, minimum) / 2.0,
                    Math.Max(size.Z, minimum) / 2.0);
                return new Box3(centre.Minus(half), centre.Plus(half));
            }

            public IEnumerable<Vec3> Corners()
            {
                if (IsEmpty) yield break;
                for (var i = 0; i < 8; i++)
                {
                    yield return new Vec3(
                        (i & 1) == 0 ? Min.X : Max.X,
                        (i & 2) == 0 ? Min.Y : Max.Y,
                        (i & 4) == 0 ? Min.Z : Max.Z);
                }
            }

            public Dictionary<string, object> ToJson(double scale)
                => new Dictionary<string, object>
                {
                    ["min"] = Min.ToArray(scale),
                    ["max"] = Max.ToArray(scale),
                    ["size"] = Size.ToArray(scale)
                };
        }
    }
}
