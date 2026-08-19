using System;
using System.Collections.Generic;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Linq;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Clash;

namespace NavisCoord
{
    /// <summary>
    /// Read-only handlers that produce report material.
    /// </summary>
    internal static class ReportHandlers
    {
        /// <summary>Colours that survive a projector and a black-and-white printer.</summary>
        private static readonly Color DefaultColourA = Color.FromByteRGB(214, 40, 40);
        private static readonly Color DefaultColourB = Color.FromByteRGB(30, 136, 229);
        private static readonly Color DefaultContext = Color.FromByteRGB(176, 176, 176);

        /// <summary>
        /// Renders the 3D view of a clash as a PNG, and reports what it framed.
        /// </summary>
        /// <remarks>
        /// This is what turns a coordination list into something a
        /// subcontractor will actually act on. A row saying "pipe into beam
        /// at X=34, Y=-10" gets argued with; a picture of the pipe going
        /// through the beam does not.
        ///
        /// Three things had to change before that was true on more than one
        /// project:
        ///
        /// 1. **The camera is computed, not nudged.** The old code multiplied
        ///    the clash's span by a zoom factor. On a millimetric clash that
        ///    parks the lens inside the geometry; on a composite-wide box it
        ///    photographs the building. <see cref="ClashFraming"/> fits a box
        ///    instead, and the box is bounded to the neighbourhood of the
        ///    clash so one long duct cannot drag the frame out to an aerial
        ///    shot. That arithmetic has no Autodesk types in it and is
        ///    asserted on a runner with no licence.
        ///
        /// 2. **The frame is checked before it is believed.** The eight
        ///    corners of each side are projected through the camera that was
        ///    actually applied, so the response can say "side B is outside the
        ///    frame" instead of returning a valid PNG of half the problem. A
        ///    render that fails silently is worse than one that errors,
        ///    because a report full of useless pictures still looks finished.
        ///
        /// 3. **Everything it changes, it changes back.** Camera, selection,
        ///    materials, hidden state and grids are captured before the shot
        ///    and restored after it — see <see cref="VisualScope"/> — and the
        ///    response carries the document's modified flag from before and
        ///    after so a dirtied model is visible rather than discovered a
        ///    week later.
        ///
        /// Images are returned base64-encoded rather than written to disk: the
        /// addin should not be choosing paths on the user's machine, and the
        /// caller already knows where the report goes.
        /// </remarks>
        public static Dictionary<string, object> ClashImage(Dictionary<string, object> payload)
        {
            var doc = Router.RequireDocument();
            var guid = Json.Str(payload, "clash_guid");
            if (string.IsNullOrWhiteSpace(guid))
            {
                throw new ArgumentException("Se requiere 'clash_guid'.");
            }

            var options = Options.Read(payload);
            var target = FindResult(doc, guid);
            if (target == null)
            {
                return new Dictionary<string, object>
                {
                    ["error"] = "clash_not_found",
                    ["detail"] = $"No existe un cruce con guid '{guid}' en el documento.",
                    ["effective"] = options.ToJson()
                };
            }

            // Everything below is in DOCUMENT units; only the reported numbers
            // are converted. Framing in metres on a millimetre project would
            // mean a min_distance of 1.5 asking the camera to stand 1.5 mm away.
            var scale = NavisContext.MetreScale(doc);
            var toDoc = scale > 1e-9 ? 1.0 / scale : 1.0;

            var sideA = Collect(target.Selection1, target.Item1, target.CompositeItemSelection1);
            var sideB = Collect(target.Selection2, target.Item2, target.CompositeItemSelection2);
            var focused = sideA.Count + sideB.Count;

            var boxA = BoundsOf(sideA);
            var boxB = BoundsOf(sideB);
            var clashBox = ClashVolume(target, boxA, boxB, options.MinExtentM * toDoc);

            using (var scope = new VisualScope(doc))
            {
                var notes = new List<string>();

                var frame = ApplyCamera(doc, target, options, clashBox, boxA, boxB, scale, notes);

                var colourA = options.ColourA ?? DefaultColourA;
                var colourB = options.ColourB ?? DefaultColourB;
                if (options.Colourise)
                {
                    scope.Paint(sideA, sideB, colourA, colourB, options.ContextColour ?? DefaultContext,
                        options.ContextTransparency, options.NeutraliseContext);
                }
                if (options.HideUnrelated)
                {
                    var keep = new ModelItemCollection();
                    AddAll(keep, sideA);
                    AddAll(keep, sideB);
                    scope.Isolate(keep);
                }

                // The selection highlight tints whatever is selected, which on
                // a coloured render is a second signal saying something else.
                // Our colours are the legend; the selection is cleared so it
                // cannot contradict them.
                TrySilently(() => doc.CurrentSelection.Clear());

                var gridState = options.GridRequested ? scope.Grid(options.ShowGrid) : "untouched";
                var backgroundState = scope.Background(
                    options.Background, options.HasBackground,
                    options.BackgroundRestore, options.HasBackgroundRestore);

                Dictionary<string, object> encoded;
                using (var shot = Render(doc, options))
                {
                    encoded = Encode(shot, guid);
                }

                if (encoded.ContainsKey("error")) return encoded;

                encoded["focused_items"] = (double)focused;
                encoded["highlighted"] = options.Colourise;
                encoded["framing"] = FramingJson(frame, clashBox, boxA, boxB, scale, options);
                encoded["coverage"] = Coverage(frame, boxA, boxB, clashBox, options);
                encoded["sides"] = new Dictionary<string, object>
                {
                    ["a"] = SideJson(sideA, colourA, options.DisciplineA),
                    ["b"] = SideJson(sideB, colourB, options.DisciplineB)
                };
                encoded["location"] = new Dictionary<string, object>
                {
                    ["grid_reference"] = options.ShowLevel
                        ? scope.GridReference(target.Center, scale)
                        : string.Empty,
                    ["point"] = NavisContext.ToMetres(target.Center, scale)
                };
                encoded["visual"] = new Dictionary<string, object>
                {
                    ["colourised"] = options.Colourise,
                    ["context_neutralised"] = options.NeutraliseContext,
                    ["context_transparency"] = options.ContextTransparency,
                    ["isolated"] = options.HideUnrelated,
                    ["hidden_items"] = (double)scope.HiddenCount,
                    ["grid"] = gridState,
                    ["background"] = backgroundState
                };
                encoded["effective"] = options.ToJson();
                encoded["notes"] = notes.Concat(scope.Notes).Concat(frame.Notes)
                    .Distinct().Cast<object>().ToList();

                // Read INSIDE the using block for "before", and the caller gets
                // "after" once the scope has put everything back.
                var modifiedBefore = scope.ModifiedBefore;
                scope.Dispose();
                encoded["document"] = new Dictionary<string, object>
                {
                    ["modified_before"] = modifiedBefore,
                    ["modified_after"] = scope.ModifiedNow(),
                    ["metre_scale"] = scale,
                    ["units"] = doc.Units.ToString()
                };
                return encoded;
            }
        }

        // ------------------------------------------------------------ camera

        /// <summary>
        /// Computes the frame, then puts the camera exactly there.
        /// </summary>
        /// <remarks>
        /// <c>FocusOnCurrentSelection</c> is the obvious call and it does not
        /// work here: with a clash viewpoint already applied it is a no-op, and
        /// without one it zooms to the whole model. Either way the render
        /// succeeds and returns a photograph of the entire building with the
        /// clash somewhere inside it — a failure that looks like a result.
        ///
        /// The extents are set separately from the distance
        /// (<c>SetExtentsAtFocalDistance</c>), which is what lets
        /// <c>min_distance</c> and <c>max_distance</c> be honest options: they
        /// move the camera without changing what is in the picture, so
        /// clamping one cannot quietly reframe the shot.
        /// </remarks>
        private static ClashFraming.Result ApplyCamera(
            Document doc,
            ClashResult result,
            Options options,
            ClashFraming.Box3 clashBox,
            ClashFraming.Box3 boxA,
            ClashFraming.Box3 boxB,
            double scale,
            List<string> notes)
        {
            var toDoc = scale > 1e-9 ? 1.0 / scale : 1.0;

            var request = new ClashFraming.Request
            {
                ClashBox = clashBox,
                BoxA = boxA,
                BoxB = boxB,
                Mode = options.Mode,
                MarginPercent = options.MarginPercent,
                MinExtent = options.MinExtentM * toDoc,
                MinDistance = options.MinDistanceM * toDoc,
                MaxDistance = options.MaxDistanceM * toDoc,
                AspectRatio = (double)options.Width / Math.Max(1, options.Height),
                FieldOfView = options.FieldOfView,
                WorldUp = ToVec(doc.UpVector)
            };

            var frame = ClashFraming.Frame(request);

            // Kept for the one case the computed frame cannot serve: a caller
            // that wants precisely what Navisworks itself would show, warts and
            // all, usually to compare against a printed report.
            if (options.RawViewpoint)
            {
                try
                {
                    var saved = doc.GetClash().TestsData.TestsViewpointForResult(result);
                    if (saved != null)
                    {
                        doc.ActiveView.CopyViewpointFrom(saved, ViewChange.JumpCut);
                        frame.Notes.Add("raw_viewpoint_applied");
                        return frame;
                    }
                    frame.Notes.Add("raw_viewpoint_unavailable");
                }
                catch (Exception ex)
                {
                    notes.Add("raw_viewpoint_failed: " + ex.Message);
                }
            }

            try
            {
                var viewpoint = doc.CurrentViewpoint.ToViewpoint().CreateCopy();
                viewpoint.Projection = frame.Orthographic
                    ? ViewpointProjection.Orthographic
                    : ViewpointProjection.Perspective;

                viewpoint.Position = ToPoint(frame.Position);
                // Aim first, then roll. AlignUp before PointAt is undone by
                // the aiming, and the picture comes out with the building
                // tilted — legal, and the first thing a reader complains about.
                viewpoint.PointAt(ToPoint(frame.Target));
                viewpoint.AlignUp(ToVector(frame.Up));
                viewpoint.FocalDistance = Math.Max(frame.Distance, 1e-6);
                viewpoint.AspectRatio = request.AspectRatio;
                viewpoint.SetExtentsAtFocalDistance(frame.ExtentWidth, frame.ExtentHeight);

                viewpoint.RenderStyle = options.RenderStyle;
                viewpoint.Lighting = options.Lighting;

                // Through the VIEW, symmetric with the restore in VisualScope.
                //
                // This does NOT stop the document being marked as modified,
                // and it was changed here in the belief that it would. The
                // measurement, on a live federation opened clean: an image
                // rendered with colouring, isolation, grid and background all
                // OFF — so that moving the camera is the only mutation left —
                // still takes the document from `modified: false` to
                // `modified: true`. `clash/export` before it leaves it clean,
                // so the camera is the cause and neither entry point avoids it.
                //
                // It cannot be avoided at all: Navisworks renders the CURRENT
                // view, there is no offscreen camera, so producing the picture
                // requires moving it. Nothing is saved, the fingerprint does
                // not change and the camera is put back — but the dirty flag
                // stays set, and the response says so in `document`. Kept as
                // the view-level call anyway: it is the right object for a
                // camera move and it matches how the restore puts it back.
                doc.ActiveView.CopyViewpointFrom(viewpoint, ViewChange.JumpCut);
            }
            catch (Exception ex)
            {
                // A camera that could not be applied must be reported, not
                // absorbed: the picture is then whatever the view already had,
                // and the coverage numbers below would describe a camera that
                // was never used.
                notes.Add("camera_not_applied: " + ex.Message);
                frame.Notes.Add("camera_not_applied");
            }

            return frame;
        }

        private static System.Drawing.Bitmap Render(Document doc, Options options)
        {
            var view = doc.ActiveView;
            if (options.MaxSeconds > 0.0)
            {
                return view.GenerateImage(
                    options.Style, options.Width, options.Height, options.MaxSeconds, true);
            }
            return view.GenerateImage(options.Style, options.Width, options.Height, true);
        }

        // --------------------------------------------------------- geometry

        private static ClashResult FindResult(Document doc, string guid)
        {
            var clash = doc.GetClash();
            foreach (var saved in clash.TestsData.Tests)
            {
                if (!(saved is ClashTest test)) continue;
                var found = Router.EnumerateResults(test).FirstOrDefault(
                    r => string.Equals(r.Guid.ToString(), guid, StringComparison.OrdinalIgnoreCase));
                if (found != null) return found;
            }
            return null;
        }

        /// <summary>
        /// The items on one side of the clash, in order of usefulness.
        /// </summary>
        /// <remarks>
        /// Selection1/Selection2 are the accessor for "what clashed" and are
        /// populated where Item1/Item2 are not — on some NWDs the Item
        /// accessors come back null, the collection stays empty, and any
        /// framing built from it silently describes the whole model.
        ///
        /// Composites are the last resort, never the first: one can hold a
        /// whole floor, and a frame fitted to that is the aerial shot again.
        /// </remarks>
        private static ModelItemCollection Collect(
            ModelItemCollection primary, ModelItem single, ModelItemCollection composite)
        {
            var items = new ModelItemCollection();
            AddAll(items, primary);
            if (items.Count == 0 && single != null) items.Add(single);
            if (items.Count == 0) AddAll(items, composite);
            return items;
        }

        private static ClashFraming.Box3 BoundsOf(ModelItemCollection items)
        {
            var box = ClashFraming.Box3.Empty;
            if (items == null) return box;
            foreach (var item in items)
            {
                var bounds = NavisContext.SafeBoundingBox(item);
                if (bounds == null || bounds.IsEmpty) continue;
                box = box.Union(new ClashFraming.Box3(ToVec(bounds.Min), ToVec(bounds.Max)));
            }
            return box;
        }

        /// <summary>
        /// The interference volume, with two fallbacks that matter in practice.
        /// </summary>
        /// <remarks>
        /// <c>ClashResult.BoundingBox</c> is the right answer and is sometimes
        /// empty — clearance tests report a box only when the geometry
        /// actually overlaps. Intersecting the two element boxes recovers a
        /// real volume for those; a cube around the reported centre recovers
        /// something framable for the rest. Returning an empty box instead
        /// would put the camera at the world origin, which renders as sky.
        /// </remarks>
        private static ClashFraming.Box3 ClashVolume(
            ClashResult result, ClashFraming.Box3 boxA, ClashFraming.Box3 boxB, double minimum)
        {
            // The geometry decision lives in ClashFraming, which carries no
            // Autodesk types and is therefore exercised by the test runner.
            // All this layer does is read the Navisworks side of it.
            var reported = ClashFraming.Box3.Empty;
            try
            {
                var box = result.BoundingBox;
                if (box != null && !box.IsEmpty)
                {
                    reported = new ClashFraming.Box3(ToVec(box.Min), ToVec(box.Max));
                }
            }
            catch
            {
                // Leave it empty; the intersection or the contact point will do.
            }

            var volume = ClashFraming.ClashVolume(boxA, boxB, reported, minimum);
            if (!volume.IsEmpty) return volume;

            var centre = ToVec(result.Center);
            return new ClashFraming.Box3(centre, centre).AtLeast(Math.Max(minimum, 1e-3));
        }

        // ---------------------------------------------------------- reports

        private static Dictionary<string, object> FramingJson(
            ClashFraming.Result frame,
            ClashFraming.Box3 clashBox,
            ClashFraming.Box3 boxA,
            ClashFraming.Box3 boxB,
            double scale,
            Options options)
            => new Dictionary<string, object>
            {
                ["mode"] = frame.Mode,
                ["projection"] = frame.Orthographic ? "orthographic" : "perspective",
                ["camera"] = new Dictionary<string, object>
                {
                    ["position"] = frame.Position.ToArray(scale),
                    ["target"] = frame.Target.ToArray(scale),
                    ["direction"] = frame.Direction.ToArray(),
                    ["up"] = frame.Up.ToArray(),
                    ["distance_m"] = ClashFraming.Round(frame.Distance * scale),
                    ["extent_width_m"] = ClashFraming.Round(frame.ExtentWidth * scale),
                    ["extent_height_m"] = ClashFraming.Round(frame.ExtentHeight * scale)
                },
                ["fit_box"] = frame.FitBox.ToJson(scale),
                ["clash_box"] = clashBox.ToJson(scale),
                ["box_a"] = boxA.ToJson(scale),
                ["box_b"] = boxB.ToJson(scale),
                ["margin_percent"] = options.MarginPercent
            };

        /// <summary>
        /// The frame's own verdict on itself, before a pixel is looked at.
        /// </summary>
        /// <remarks>
        /// Geometry cannot tell whether a wall stands between the camera and
        /// the pipe — that is what the pixel check on the other side of the
        /// bridge is for. It can tell, exactly, whether the elements are in
        /// the frustum at all and how much of the frame they could possibly
        /// occupy, and those are the two failures that produced empty
        /// pictures.
        /// </remarks>
        private static Dictionary<string, object> Coverage(
            ClashFraming.Result frame,
            ClashFraming.Box3 boxA,
            ClashFraming.Box3 boxB,
            ClashFraming.Box3 clashBox,
            Options options)
        {
            var a = ClashFraming.Project(frame, boxA);
            var b = ClashFraming.Project(frame, boxB);
            var clash = ClashFraming.Project(frame, clashBox);

            var reasons = new List<object>();
            if (!a.InFrame) reasons.Add("side_a_out_of_frame");
            if (!b.InFrame) reasons.Add("side_b_out_of_frame");
            if (!clash.InFrame) reasons.Add("clash_out_of_frame");
            if (a.InFrame && a.ScreenFraction < options.MinScreenFraction) reasons.Add("side_a_too_small");
            if (b.InFrame && b.ScreenFraction < options.MinScreenFraction) reasons.Add("side_b_too_small");

            return new Dictionary<string, object>
            {
                ["side_a"] = a.ToJson(),
                ["side_b"] = b.ToJson(),
                ["clash"] = clash.ToJson(),
                ["both_sides_in_frame"] = a.InFrame && b.InFrame,
                ["clash_contained"] = clash.Contained,
                ["smallest_side_fraction"] = ClashFraming.Round(Math.Min(a.ScreenFraction, b.ScreenFraction)),
                ["min_screen_fraction"] = options.MinScreenFraction,
                ["ok"] = reasons.Count == 0,
                ["reasons"] = reasons
            };
        }

        private static Dictionary<string, object> SideJson(
            ModelItemCollection items, Color colour, string discipline)
        {
            var names = new List<object>();
            var sources = new List<object>();
            foreach (var item in items)
            {
                if (names.Count < 5 && !string.IsNullOrWhiteSpace(item?.DisplayName))
                {
                    names.Add(item.DisplayName);
                }
                var source = NavisContext.SourceFileOf(item);
                if (!string.IsNullOrWhiteSpace(source) && !sources.Contains(source)) sources.Add(source);
            }

            return new Dictionary<string, object>
            {
                ["items"] = (double)items.Count,
                ["names"] = names,
                ["source_files"] = sources,
                ["discipline"] = discipline ?? string.Empty,
                ["color"] = new[]
                {
                    (double)colour.GetClampedByteValue(0),
                    (double)colour.GetClampedByteValue(1),
                    (double)colour.GetClampedByteValue(2)
                }
            };
        }

        // ------------------------------------------------------------ plumbing

        private static void AddAll(ModelItemCollection target, ModelItemCollection source)
        {
            if (source == null) return;
            foreach (var item in source)
            {
                if (item != null && !target.Contains(item)) target.Add(item);
            }
        }

        private static void TrySilently(Action action)
        {
            try { action(); } catch { /* best effort */ }
        }

        private static ClashFraming.Vec3 ToVec(Point3D point)
            => point == null
                ? new ClashFraming.Vec3(0, 0, 0)
                : new ClashFraming.Vec3(point.X, point.Y, point.Z);

        private static ClashFraming.Vec3 ToVec(Vector3D vector)
            => vector == null
                ? new ClashFraming.Vec3(0, 0, 1)
                : new ClashFraming.Vec3(vector.X, vector.Y, vector.Z);

        private static Point3D ToPoint(ClashFraming.Vec3 v) => new Point3D(v.X, v.Y, v.Z);

        private static Vector3D ToVector(ClashFraming.Vec3 v) => new Vector3D(v.X, v.Y, v.Z);

        private static Dictionary<string, object> Encode(System.Drawing.Bitmap bitmap, string guid)
        {
            if (bitmap == null)
            {
                return new Dictionary<string, object>
                {
                    ["error"] = "image_unavailable",
                    ["detail"] = "Navisworks no pudo generar la imagen de este cruce."
                };
            }

            using (var buffer = new MemoryStream())
            {
                bitmap.Save(buffer, ImageFormat.Png);
                var bytes = buffer.ToArray();
                return new Dictionary<string, object>
                {
                    ["clash_guid"] = guid,
                    ["width"] = (double)bitmap.Width,
                    ["height"] = (double)bitmap.Height,
                    ["format"] = "png",
                    ["bytes"] = (double)bytes.Length,
                    ["base64"] = Convert.ToBase64String(bytes)
                };
            }
        }

        // ------------------------------------------------------------ options

        /// <summary>
        /// Every knob the caller may turn, with the defaults it gets when it
        /// turns none — which is the case that has to keep working.
        /// </summary>
        internal sealed class Options
        {
            public int Width = 900;
            public int Height = 600;
            public string Mode = ClashFraming.ModeCloseup;
            public double MarginPercent = 25.0;
            public double MinDistanceM = 1.5;
            public double MaxDistanceM = 60.0;
            public double MinExtentM = 0.8;
            public double FieldOfView = 0.7853981633974483;
            public double MinScreenFraction = 0.004;

            public bool Colourise = true;
            public bool NeutraliseContext = true;
            public double ContextTransparency = 0.8;
            public bool HideUnrelated;
            public bool ShowLevel = true;
            public bool ShowClashMarker = true;
            public bool GridRequested;
            public bool ShowGrid;
            public bool RawViewpoint;

            public Color ColourA;
            public Color ColourB;
            public Color ContextColour;
            public Color Background;
            public bool HasBackground;
            public Color BackgroundRestore;
            public bool HasBackgroundRestore;

            public string DisciplineA = string.Empty;
            public string DisciplineB = string.Empty;

            public string Quality = "standard";
            public ImageGenerationStyle Style = ImageGenerationStyle.ScenePlusOverlay;
            public ViewpointRenderStyle RenderStyle = ViewpointRenderStyle.Shaded;
            public ViewpointLighting Lighting = ViewpointLighting.Headlight;
            public double MaxSeconds;

            public static Options Read(Dictionary<string, object> payload)
            {
                var o = new Options();
                if (payload == null) return o;

                // `width`/`height` are the names the first version shipped
                // with; `image_width`/`image_height` are the ones the option
                // schema uses. Both are honoured, and neither wins by accident:
                // the explicit new name is read second, so a caller passing
                // both gets the one it just learned about.
                o.Width = Clamp(Json.Int(payload, "width", o.Width), 160, 2400);
                o.Width = Clamp(Json.Int(payload, "image_width", o.Width), 160, 2400);
                o.Height = Clamp(Json.Int(payload, "height", o.Height), 120, 1800);
                o.Height = Clamp(Json.Int(payload, "image_height", o.Height), 120, 1800);

                o.Mode = ClashFraming.Normalise(Json.Str(payload, "camera_mode", o.Mode));
                o.MarginPercent = Bound(Json.Num(payload, "margin_percent", o.MarginPercent), 0.0, 400.0);
                o.MinDistanceM = Bound(Json.Num(payload, "min_distance", o.MinDistanceM), 0.05, 500.0);
                o.MaxDistanceM = Bound(Json.Num(payload, "max_distance", o.MaxDistanceM), o.MinDistanceM, 5000.0);
                o.MinExtentM = Bound(Json.Num(payload, "min_extent", o.MinExtentM), 0.05, 100.0);
                o.MinScreenFraction = Bound(
                    Json.Num(payload, "min_screen_fraction", o.MinScreenFraction), 0.0, 0.9);

                var fovDegrees = Bound(Json.Num(payload, "field_of_view_deg", 45.0), 10.0, 120.0);
                o.FieldOfView = fovDegrees * Math.PI / 180.0;

                // `highlight` is what the first version called this.
                o.Colourise = Json.Bool(payload, "highlight", true);
                o.Colourise = Json.Bool(payload, "colorize", o.Colourise);
                o.NeutraliseContext = Json.Bool(payload, "neutralize_context", o.NeutraliseContext);
                o.ContextTransparency = Bound(
                    Json.Num(payload, "context_transparency", o.ContextTransparency), 0.0, 0.95);
                o.HideUnrelated = Json.Bool(payload, "hide_unrelated_geometry", false);
                o.ShowLevel = Json.Bool(payload, "show_level", true);
                o.ShowClashMarker = Json.Bool(payload, "show_clash_marker", true);
                o.RawViewpoint = Json.Bool(payload, "raw_viewpoint", false);

                if (payload.ContainsKey("show_grid"))
                {
                    o.GridRequested = true;
                    o.ShowGrid = Json.Bool(payload, "show_grid", false);
                }

                o.ColourA = ParseColour(Json.Str(payload, "color_a"), DefaultColourA);
                o.ColourB = ParseColour(Json.Str(payload, "color_b"), DefaultColourB);
                o.ContextColour = ParseColour(Json.Str(payload, "context_color"), DefaultContext);
                o.DisciplineA = Json.Str(payload, "discipline_a");
                o.DisciplineB = Json.Str(payload, "discipline_b");

                var background = Json.Str(payload, "background_color");
                if (!string.IsNullOrWhiteSpace(background))
                {
                    o.Background = ParseColour(background, Color.White);
                    o.HasBackground = true;
                }
                var restore = Json.Str(payload, "background_restore_color");
                if (!string.IsNullOrWhiteSpace(restore))
                {
                    o.BackgroundRestore = ParseColour(restore, Color.White);
                    o.HasBackgroundRestore = true;
                }

                o.Quality = (Json.Str(payload, "render_quality", "standard") ?? "standard")
                    .Trim().ToLowerInvariant();
                switch (o.Quality)
                {
                    case "draft":
                        // Bounded so a heavy federation cannot hold the UI
                        // thread for a minute per picture.
                        o.RenderStyle = ViewpointRenderStyle.Shaded;
                        o.Lighting = ViewpointLighting.Headlight;
                        o.MaxSeconds = 3.0;
                        break;
                    case "high":
                        o.RenderStyle = ViewpointRenderStyle.FullRender;
                        o.Lighting = ViewpointLighting.FullLights;
                        o.MaxSeconds = 0.0;
                        break;
                    default:
                        o.Quality = "standard";
                        // Flat shading on purpose, not for speed: the quality
                        // check on the other side classifies pixels by hue, and
                        // scene lighting plus textures turn a red pipe into
                        // fifty shades that no threshold separates cleanly.
                        o.RenderStyle = ViewpointRenderStyle.Shaded;
                        o.Lighting = ViewpointLighting.Headlight;
                        o.MaxSeconds = 0.0;
                        break;
                }

                o.Style = o.ShowClashMarker
                    ? ImageGenerationStyle.ScenePlusOverlay
                    : ImageGenerationStyle.Scene;

                // `style` is the old name and the client still sends it on
                // every call, defaulted. Letting it win unconditionally would
                // make `show_clash_marker: false` a no-op — the option would
                // be advertised, accepted, echoed back as effective, and do
                // nothing. The new name therefore wins whenever it is present.
                var explicitStyle = Json.Str(payload, "style");
                if (!payload.ContainsKey("show_clash_marker")
                    && !string.IsNullOrWhiteSpace(explicitStyle)
                    && Enum.TryParse<ImageGenerationStyle>(explicitStyle, true, out var parsed))
                {
                    o.Style = parsed;
                }

                return o;
            }

            public Dictionary<string, object> ToJson()
                => new Dictionary<string, object>
                {
                    ["camera_mode"] = Mode,
                    ["image_width"] = (double)Width,
                    ["image_height"] = (double)Height,
                    ["margin_percent"] = MarginPercent,
                    ["min_distance"] = MinDistanceM,
                    ["max_distance"] = MaxDistanceM,
                    ["min_extent"] = MinExtentM,
                    ["field_of_view_deg"] = ClashFraming.Round(FieldOfView * 180.0 / Math.PI),
                    ["min_screen_fraction"] = MinScreenFraction,
                    ["colorize"] = Colourise,
                    ["neutralize_context"] = NeutraliseContext,
                    ["context_transparency"] = ContextTransparency,
                    ["hide_unrelated_geometry"] = HideUnrelated,
                    ["show_level"] = ShowLevel,
                    ["show_clash_marker"] = ShowClashMarker,
                    ["show_grid"] = GridRequested ? (object)ShowGrid : "untouched",
                    ["background_color"] = HasBackground ? Describe(Background) : string.Empty,
                    ["render_quality"] = Quality,
                    ["render_style"] = RenderStyle.ToString(),
                    ["lighting"] = Lighting.ToString(),
                    ["image_style"] = Style.ToString(),
                    ["raw_viewpoint"] = RawViewpoint
                };

            private static int Clamp(int value, int low, int high)
                => value < low ? low : (value > high ? high : value);

            private static double Bound(double value, double low, double high)
            {
                if (double.IsNaN(value)) return low;
                return value < low ? low : (value > high ? high : value);
            }
        }

        /// <summary>
        /// Reads "#RRGGBB", "RRGGBB" or a handful of names.
        /// </summary>
        /// <remarks>
        /// Colours arrive as strings because the caller composing them is a
        /// language model reading a profile, and a malformed one must produce
        /// the default rather than an exception: a report is not worth failing
        /// over a typo in a hex triplet.
        /// </remarks>
        internal static Color ParseColour(string text, Color fallback)
        {
            if (string.IsNullOrWhiteSpace(text)) return fallback;
            var value = text.Trim();

            switch (value.ToLowerInvariant())
            {
                case "white": case "blanco": return Color.White;
                case "black": case "negro": return Color.Black;
                case "grey": case "gray": case "gris": return Color.FromByteRGB(176, 176, 176);
                case "red": case "rojo": return DefaultColourA;
                case "blue": case "azul": return DefaultColourB;
                case "green": case "verde": return Color.FromByteRGB(46, 160, 67);
            }

            if (value.StartsWith("#", StringComparison.Ordinal)) value = value.Substring(1);
            if (value.Length != 6) return fallback;

            if (byte.TryParse(value.Substring(0, 2), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var r)
                && byte.TryParse(value.Substring(2, 2), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var g)
                && byte.TryParse(value.Substring(4, 2), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var b))
            {
                return Color.FromByteRGB(r, g, b);
            }
            return fallback;
        }

        private static string Describe(Color colour)
            => string.Format(
                CultureInfo.InvariantCulture,
                "#{0:X2}{1:X2}{2:X2}",
                colour.GetClampedByteValue(0),
                colour.GetClampedByteValue(1),
                colour.GetClampedByteValue(2));
    }
}
