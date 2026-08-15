using System;
using System.Collections.Generic;
using System.Drawing.Imaging;
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
        /// <summary>
        /// Renders the 3D view of a clash as a PNG.
        /// </summary>
        /// <remarks>
        /// This is what turns a coordination list into something a
        /// subcontractor will actually act on. A row saying "pipe into beam
        /// at X=34, Y=-10" gets argued with; a picture of the pipe going
        /// through the beam does not.
        ///
        /// ScenePlusOverlay draws the clash markers on top of the geometry,
        /// which is the same image Navisworks puts in its own reports.
        ///
        /// Images are returned base64-encoded rather than written to disk:
        /// the addin should not be choosing paths on the user's machine, and
        /// the caller already knows where the report goes.
        /// </remarks>
        public static Dictionary<string, object> ClashImage(Dictionary<string, object> payload)
        {
            var doc = Router.RequireDocument();
            var guid = Json.Str(payload, "clash_guid");
            if (string.IsNullOrWhiteSpace(guid))
            {
                throw new ArgumentException("Se requiere 'clash_guid'.");
            }

            var width = Math.Max(160, Math.Min(2400, Json.Int(payload, "width", 900)));
            var height = Math.Max(120, Math.Min(1800, Json.Int(payload, "height", 600)));

            var styleName = Json.Str(payload, "style", "ScenePlusOverlay");
            if (!Enum.TryParse<ImageGenerationStyle>(styleName, true, out var style))
            {
                style = ImageGenerationStyle.ScenePlusOverlay;
            }

            var clash = doc.GetClash();
            ClashResult target = null;
            foreach (var saved in clash.TestsData.Tests)
            {
                if (!(saved is ClashTest test)) continue;
                target = Router.EnumerateResults(test)
                    .FirstOrDefault(r => string.Equals(r.Guid.ToString(), guid, StringComparison.OrdinalIgnoreCase));
                if (target != null) break;
            }

            if (target == null)
            {
                return new Dictionary<string, object>
                {
                    ["error"] = "clash_not_found",
                    ["detail"] = $"No existe un cruce con guid '{guid}' en el documento."
                };
            }

            // Framing the shot is the whole job here, and it took three
            // attempts against a real model to get right:
            //
            // 1. Rendering straight from TestsImageForResult fires the camera
            //    from wherever the view happened to be. On a freshly opened
            //    model that is nowhere near the geometry, so it returns the
            //    empty background gradient. It does not fail — it produces a
            //    blank picture, which is worse, because a report full of
            //    empty images still looks finished.
            // 2. Applying the clash's own viewpoint does put geometry on
            //    screen, but Navisworks parks the camera almost inside the
            //    elements: the frame comes back as a grey corner.
            // 3. Selecting both sides and focusing frames the actual pair,
            //    which is what the reader needs — the pipe AND the beam it
            //    goes through, not one of them at arm's length.
            // Applying the clash viewpoint FIRST and focusing afterwards leaves
            // the frame unchanged — the saved camera wins and the focus is a
            // no-op. So the viewpoint is only used when the caller explicitly
            // asks for it; otherwise the frame is built from the selection.
            var useRawViewpoint = Json.Bool(payload, "raw_viewpoint", false);
            if (useRawViewpoint)
            {
                var viewpoint = clash.TestsData.TestsViewpointForResult(target);
                if (viewpoint != null) doc.CurrentViewpoint.CopyFrom(viewpoint);
            }

            var focused = 0;
            if (!useRawViewpoint)
            {
                // Selection1/Selection2 are the accessor for "what clashed"
                // and are populated where Item1/Item2 are not — on this NWD
                // the Item accessors come back null, the selection stays
                // empty, and FocusOnCurrentSelection then zooms to the whole
                // model. That failure is invisible from the outside: the
                // render succeeds and returns a perfectly good picture of the
                // entire building with the clash somewhere inside it.
                //
                // Composites are the last resort, never the first: one can
                // hold a whole floor and framing it is the same failure.
                var focus = new ModelItemCollection();
                AddAll(focus, target.Selection1);
                AddAll(focus, target.Selection2);

                if (focus.Count == 0)
                {
                    foreach (var side in new[] { target.Item1, target.Item2 })
                    {
                        if (side != null && !focus.Contains(side)) focus.Add(side);
                    }
                }
                if (focus.Count == 0)
                {
                    AddAll(focus, target.CompositeItemSelection1);
                    AddAll(focus, target.CompositeItemSelection2);
                }
                focused = focus.Count;

                if (focus.Count > 0)
                {
                    doc.CurrentSelection.CopyFrom(focus);
                    FrameOn(doc, target, Json.Num(payload, "zoom_out", 2.2));
                }
            }

            // Mark the two colliding elements. A correctly framed picture of a
            // congested plenum is still useless if the reader cannot tell
            // which pipe and which beam the report is talking about — in a
            // plant room everything looks like everything else.
            //
            // Red is side A and green is side B, in the order the clash test
            // defined them. That is NOT the same as "red moves": which side
            // moves is decided by the analysis engine from movability, and
            // the addin has no access to that judgement.
            //
            // Temporary overrides only: this must not dirty a document the
            // team shares, so they are cleared before returning even if the
            // render throws.
            var highlighted = false;
            try
            {
                if (Json.Bool(payload, "highlight", true))
                {
                    highlighted = Highlight(doc, target);
                }

                using (var shot = doc.ActiveView.GenerateImage(style, width, height, true))
                {
                    var encoded = Encode(shot, guid);
                    // Reported so a blank or building-wide frame can be
                    // diagnosed from the response instead of by opening the
                    // picture.
                    encoded["focused_items"] = (double)focused;
                    encoded["highlighted"] = highlighted;
                    return encoded;
                }
            }
            finally
            {
                if (highlighted) doc.Models.ResetAllTemporaryMaterials();
            }
        }

        private static bool Highlight(Document doc, ClashResult result)
        {
            var first = new ModelItemCollection();
            var second = new ModelItemCollection();
            AddAll(first, result.Selection1);
            AddAll(second, result.Selection2);
            if (first.Count == 0 && result.Item1 != null) first.Add(result.Item1);
            if (second.Count == 0 && result.Item2 != null) second.Add(result.Item2);
            if (first.Count == 0 && second.Count == 0) return false;

            if (first.Count > 0)
            {
                doc.Models.OverrideTemporaryColor(first, Color.Red);
            }
            if (second.Count > 0)
            {
                doc.Models.OverrideTemporaryColor(second, Color.Green);
            }
            return true;
        }

        /// <summary>
        /// Builds the camera from the clash's own bounding box.
        /// </summary>
        /// <remarks>
        /// FocusOnCurrentSelection is the obvious call and it does not work
        /// here: with a clash viewpoint already applied it is a no-op, and
        /// without one it zooms to the whole model. Either way the render
        /// succeeds and returns a photograph of the entire building with the
        /// clash somewhere inside it — a failure that looks like a result.
        ///
        /// Placing the camera explicitly removes the guesswork. The box
        /// around the two colliding elements sets the target and the
        /// distance; a three-quarter view keeps both elements readable
        /// instead of one hiding behind the other; and zoom_out pulls back
        /// far enough to show what surrounds the clash, because a picture
        /// with no context cannot be located on site.
        /// </remarks>
        private static void FrameOn(Document doc, ClashResult result, double zoomOut)
        {
            var box = result.BoundingBox;
            if (box == null) return;

            var centre = new Point3D(
                (box.Min.X + box.Max.X) / 2.0,
                (box.Min.Y + box.Max.Y) / 2.0,
                (box.Min.Z + box.Max.Z) / 2.0);

            var span = Math.Max(
                Math.Max(box.Max.X - box.Min.X, box.Max.Y - box.Min.Y),
                box.Max.Z - box.Min.Z);
            // A clash between two thin elements has a near-zero box; without
            // a floor the camera lands inside the geometry.
            if (span < 1e-6) span = 1.0;

            var distance = span * Math.Max(1.2, zoomOut);

            var viewpoint = doc.CurrentViewpoint.ToViewpoint().CreateCopy();
            viewpoint.Projection = ViewpointProjection.Perspective;
            viewpoint.Position = new Point3D(
                centre.X + distance,
                centre.Y - distance,
                centre.Z + distance * 0.75);
            viewpoint.PointAt(centre);
            viewpoint.FocalDistance = viewpoint.Position.DistanceTo(centre);

            doc.CurrentViewpoint.CopyFrom(viewpoint);
        }

        private static void AddAll(ModelItemCollection target, ModelItemCollection source)
        {
            if (source == null) return;
            foreach (var item in source)
            {
                if (item != null && !target.Contains(item)) target.Add(item);
            }
        }

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
    }
}
