using System;
using System.Collections.Generic;
using System.Globalization;
using Autodesk.Navisworks.Api;

namespace NavisCoord
{
    /// <summary>
    /// Everything a clash render changes about the document, and the promise
    /// to put it all back.
    /// </summary>
    /// <remarks>
    /// A coordination render has to repaint the model to be worth looking at:
    /// fade the context, colour the two culprits, sometimes hide the wall in
    /// front. Every one of those is a change to a document the team shares,
    /// and the previous implementation reset one of them — temporary
    /// materials — while silently keeping the camera and the selection it had
    /// changed. Twenty-five images later the user's viewpoint was wherever the
    /// last clash happened to be.
    ///
    /// So the state is captured in the constructor and restored in
    /// <see cref="Dispose"/>, which the handler runs inside a using block: a
    /// render that throws restores just as completely as one that succeeds.
    ///
    /// What is restorable and what is not was settled by reading the 2026 API,
    /// not by assuming:
    ///
    /// * Camera — <c>CurrentViewpoint.ToViewpoint()</c> reads, <c>CopyFrom</c>
    ///   writes. Fully restorable.
    /// * Selection — <c>CurrentSelection.CreateCopy()</c> / <c>CopyFrom</c>.
    ///   Fully restorable.
    /// * Temporary materials — write-only, but <c>ResetAllTemporaryMaterials</c>
    ///   clears the lot and they are by definition not saved.
    /// * Hidden state — per item, and <c>ModelItem.IsHidden</c> READS it, so
    ///   only items that were visible are hidden and exactly those are shown
    ///   again. <c>ResetAllHidden</c> is never called: it would also reveal
    ///   whatever the user had hidden before, which is not ours to undo.
    /// * Grids — <c>Grids.ActiveSystem</c> and <c>RenderMode</c> both read and
    ///   write. Fully restorable.
    /// * Background — <c>SetPlainBackground</c> exists; NO getter does. It
    ///   cannot be read, therefore it cannot be restored, therefore this class
    ///   refuses to touch it unless the caller supplies the colour to put back.
    ///   That limit is the API's, and pretending otherwise would leave a
    ///   coordinator's model with a white sky after a report run.
    /// </remarks>
    internal sealed class VisualScope : IDisposable
    {
        private readonly Document _doc;
        private readonly Viewpoint _viewpoint;
        private readonly Selection _selection;
        private readonly ModelItemCollection _hidden = new ModelItemCollection();

        private GridSystem _grid;
        private GridsRenderMode _gridMode;
        private bool _gridTouched;

        private bool _materialsTouched;
        private Color _restoreBackground;
        private bool _backgroundTouched;

        private readonly List<string> _notes = new List<string>();
        private bool _disposed;

        /// <summary>
        /// Children of one parent that isolation is willing to consider.
        /// </summary>
        /// <remarks>
        /// A CAD import can hang two hundred thousand entities off one node.
        /// Walking that per image turns a two-second render into a minute of
        /// held UI thread, and the picture is no better for it.
        /// </remarks>
        private const int SiblingLimit = 20000;

        public VisualScope(Document doc)
        {
            _doc = doc ?? throw new ArgumentNullException(nameof(doc));
            ModifiedBefore = DocumentContext.SafeIsModified(doc);

            _viewpoint = Try(() => doc.CurrentViewpoint.ToViewpoint().CreateCopy(), "viewpoint_not_captured");
            _selection = Try(() => doc.CurrentSelection.CreateCopy(), "selection_not_captured");
        }

        /// <summary>Whether the document already had unsaved changes.</summary>
        /// <remarks>
        /// Captured before anything is touched so the caller can report
        /// "modified because of us" separately from "was already modified".
        /// Blaming a render for a dirty document the user dirtied themselves
        /// sends people looking for a bug that is not there.
        /// </remarks>
        public bool ModifiedBefore { get; }

        public IEnumerable<string> Notes => _notes;

        public int HiddenCount => _hidden.Count;

        // ------------------------------------------------------- appearance

        /// <summary>Fades and neutralises everything, then lights up two sets.</summary>
        /// <remarks>
        /// The fade is applied to the model ROOTS, not to every item: an
        /// override on a root reaches its descendants, so a federation of six
        /// models costs six calls instead of a walk over two million items.
        /// That distinction is the difference between a render that takes a
        /// second and one that times out on a real project.
        /// </remarks>
        public void Paint(
            ModelItemCollection sideA,
            ModelItemCollection sideB,
            Color colourA,
            Color colourB,
            Color contextColour,
            double contextTransparency,
            bool neutraliseContext)
        {
            if (neutraliseContext && contextTransparency > 0.001)
            {
                var roots = _doc.Models.CreateCollectionFromRootItems();
                if (roots != null && roots.Count > 0)
                {
                    _materialsTouched = true;
                    _doc.Models.OverrideTemporaryTransparency(roots, Clamp(contextTransparency, 0.0, 0.95));
                    _doc.Models.OverrideTemporaryColor(roots, contextColour);
                }
            }

            // The two sides go back to fully opaque explicitly. Inheriting the
            // context fade is what made the first version of this produce a
            // ghost of a pipe inside a ghost of a plenum.
            foreach (var pair in new[]
                     {
                         new KeyValuePair<ModelItemCollection, Color>(sideA, colourA),
                         new KeyValuePair<ModelItemCollection, Color>(sideB, colourB)
                     })
            {
                if (pair.Key == null || pair.Key.Count == 0) continue;
                _materialsTouched = true;
                _doc.Models.OverrideTemporaryTransparency(pair.Key, 0.0);
                _doc.Models.OverrideTemporaryColor(pair.Key, pair.Value);
            }
        }

        /// <summary>
        /// Hides everything that is not one of the two sides or an ancestor of
        /// one, within the geometry that would otherwise be in shot.
        /// </summary>
        /// <remarks>
        /// Navisworks has no isolate call, and hiding the model roots and then
        /// calling <c>MakeVisible</c> on the two sides does not work: making
        /// an item visible un-hides its ancestors, and un-hiding a root brings
        /// back everything under it. The only correct construction is the one
        /// a user performs by hand — walk the ancestor chain of what must
        /// survive and hide each ancestor's OTHER children.
        ///
        /// Only items that were visible are hidden, so restoring is exact
        /// rather than a reset that would also reveal whatever the coordinator
        /// had hidden before opening the report.
        /// </remarks>
        public void Isolate(ModelItemCollection keep)
        {
            if (keep == null || keep.Count == 0)
            {
                _notes.Add("isolation_skipped_no_items");
                return;
            }

            // Every tree walk below is guarded item by item, and that is not
            // defensive habit: on a real federation the items a clash hands
            // back are not always attached to the live document tree, and
            // touching Children or IsHidden on a detached one throws "Only
            // valid for ModelItem in a document". Isolation is an
            // improvement to a picture; losing the picture because one item
            // of six is detached is the wrong trade, so an item that cannot
            // be walked is skipped and named.
            // Anchor first. A clash hands back items that are not always the
            // ones in the document tree, and a detached item has no parent, so
            // it has no siblings, so isolation finds nothing to hide and says
            // so while changing nothing. That is exactly what a real
            // federation produced: `isolation_found_nothing_to_hide` with
            // `hidden_items: 0`, on a clash whose two elements were ordinary
            // pipework with plenty of neighbours.
            var anchored = new ModelItemCollection();
            var reattached = 0;
            foreach (var item in keep)
            {
                var live = Anchor(item, ref reattached);
                if (live != null && !anchored.Contains(live)) anchored.Add(live);
            }
            if (reattached > 0) _notes.Add("isolation_reanchored_" + reattached);

            var survivors = new HashSet<ModelItem>();
            var walked = 0;
            var skipped = 0;
            var ancestorCount = 0;
            foreach (var item in anchored)
            {
                if (item == null) continue;
                try
                {
                    foreach (var ancestor in item.AncestorsAndSelf)
                    {
                        survivors.Add(ancestor);
                        ancestorCount++;
                    }
                    // Descendants of a kept item stay: hiding a pipe's children
                    // would hide the pipe.
                    foreach (var descendant in item.Descendants) survivors.Add(descendant);
                    walked++;
                }
                catch (Exception ex)
                {
                    skipped++;
                    Note("isolation_item_detached", ex);
                }
            }

            if (walked == 0)
            {
                _notes.Add("isolation_skipped_detached_items");
                return;
            }

            // One counter per reason a sibling is spared. "Nothing to hide" was
            // true and useless: three different skips produce it, and only the
            // breakdown says which. Measuring the wrong thing already cost one
            // wrong hypothesis — that the items were detached — which the
            // ancestor count disproved on the first run.
            var seenSiblings = 0;
            var skippedNull = 0;
            var skippedSurvivor = 0;
            var skippedDuplicate = 0;
            var skippedHidden = 0;
            var skippedUnreadable = 0;
            var toHide = new ModelItemCollection();
            foreach (var item in anchored)
            {
                if (item == null) continue;
                try
                {
                    foreach (var ancestor in item.AncestorsAndSelf)
                    {
                        // A parent with an enormous flat child list is a CAD
                        // import, and walking it costs more than the picture
                        // is worth. Reported rather than silently partial.
                        var siblings = 0;
                        foreach (var sibling in ancestor.Children)
                        {
                            if (++siblings > SiblingLimit)
                            {
                                _notes.Add("isolation_level_too_wide");
                                break;
                            }
                            seenSiblings++;
                            if (sibling == null) { skippedNull++; continue; }
                            if (survivors.Contains(sibling)) { skippedSurvivor++; continue; }
                            if (toHide.Contains(sibling)) { skippedDuplicate++; continue; }
                            try
                            {
                                if (sibling.IsHidden) { skippedHidden++; continue; }
                            }
                            catch
                            {
                                skippedUnreadable++;
                                continue;
                            }
                            toHide.Add(sibling);
                        }
                    }
                }
                catch (Exception ex)
                {
                    skipped++;
                    Note("isolation_walk_failed", ex);
                }
            }

            if (skipped > 0) _notes.Add("isolation_partial");

            // The numbers, always. "Found nothing to hide" is a conclusion, and
            // a conclusion with no measurement behind it cost a whole live
            // investigation: there was no way to tell an item with no siblings
            // from an ancestor walk that never left the item itself.
            _notes.Add(string.Format(
                CultureInfo.InvariantCulture,
                "isolation_scan items={0} ancestors={1} siblings={2} hiding={3} " +
                "skipped[survivor={4} hidden={5} duplicate={6} null={7} unreadable={8}]",
                anchored.Count, ancestorCount, seenSiblings, toHide.Count,
                skippedSurvivor, skippedHidden, skippedDuplicate, skippedNull, skippedUnreadable));

            if (toHide.Count == 0)
            {
                _notes.Add("isolation_found_nothing_to_hide");
                return;
            }

            // Recorded BEFORE the call. If SetHidden applies to part of the
            // collection and then throws, the restore still covers everything
            // it touched — and un-hiding something that was already visible
            // costs nothing, because only visible items got this far.
            foreach (var item in toHide) _hidden.Add(item);
            try
            {
                _doc.Models.SetHidden(toHide, true);
            }
            catch (Exception ex)
            {
                Note("isolation_hide_failed", ex);
            }
        }

        /// <summary>
        /// The live document item for something a clash handed back.
        /// </summary>
        /// <remarks>
        /// `ClashResult.Selection1` does not always yield items attached to the
        /// document tree. A detached one still paints — the colour overrides
        /// resolve it perfectly well — but its `Parent` is null, so it has no
        /// ancestors, therefore no siblings, therefore nothing for isolation to
        /// hide. The symptom is silent and absurd: a pipe in a crowded plenum
        /// reports that there is no geometry around it.
        ///
        /// `CreatePathId` / `ResolvePathId` is the document's own round trip
        /// and returns the item that IS in the tree at that position. Anything
        /// that fails falls back to the original, so an unanchorable item costs
        /// its own isolation and nothing else.
        /// </remarks>
        private ModelItem Anchor(ModelItem item, ref int reattached)
        {
            if (item == null) return null;
            try
            {
                if (item.Parent != null) return item;
            }
            catch
            {
                // Not attached at all; the round trip below is the only hope.
            }

            try
            {
                var resolved = _doc.Models.ResolvePathId(_doc.Models.CreatePathId(item));
                if (resolved != null)
                {
                    reattached++;
                    return resolved;
                }
            }
            catch (Exception ex)
            {
                Note("isolation_anchor_failed", ex);
            }
            return item;
        }

        // ------------------------------------------------------------ grids

        /// <summary>Turns the grid display on or off, remembering both settings.</summary>
        public string Grid(bool show)
        {
            try
            {
                var grids = _doc.Grids;
                if (grids == null) return "unavailable";
                if (grids.Systems == null || grids.Systems.Count == 0) return "no_grid_systems";

                if (!_gridTouched)
                {
                    _grid = grids.ActiveSystem;
                    _gridMode = grids.RenderMode;
                    _gridTouched = true;
                }

                if (show)
                {
                    if (grids.ActiveSystem == null) grids.ActiveSystem = grids.Systems[0];
                    grids.RenderMode = GridsRenderMode.All;
                    return "shown";
                }

                grids.ActiveSystem = null;
                return "hidden";
            }
            catch (Exception ex)
            {
                _notes.Add("grid_failed: " + ex.Message);
                return "failed";
            }
        }

        /// <summary>
        /// The level and grid reference nearest a point, as Navisworks itself
        /// would print it.
        /// </summary>
        /// <remarks>
        /// A clash result carries no level and no grid — the exported one is
        /// harvested from whichever element published a Level property, which
        /// is empty on plenty of federations. The grid system knows, and
        /// <c>FormatCombinedDisplayString</c> is the same formatter the status
        /// bar uses, so the report says what the coordinator sees on screen.
        /// </remarks>
        public string GridReference(Point3D point, double metreScale)
        {
            try
            {
                var grids = _doc.Grids;
                var system = grids?.ActiveSystem
                             ?? (grids?.Systems != null && grids.Systems.Count > 0 ? grids.Systems[0] : null);
                if (system == null || point == null) return string.Empty;
                var intersection = system.ClosestIntersection(point);
                if (intersection == null) return string.Empty;
                var text = intersection.FormatCombinedDisplayString(point, metreScale);
                return string.IsNullOrWhiteSpace(text) ? string.Empty : text.Trim();
            }
            catch
            {
                return string.Empty;
            }
        }

        // ------------------------------------------------------- background

        /// <summary>
        /// Paints the background, but only against a colour to restore.
        /// </summary>
        /// <remarks>
        /// Returns what happened rather than throwing: a caller asking for a
        /// white backdrop on an add-in that cannot read the current one should
        /// still get its picture, with the reason the backdrop is unchanged.
        /// </remarks>
        public string Background(Color colour, bool hasColour, Color restore, bool hasRestore)
        {
            if (!hasColour) return "untouched";
            if (!hasRestore)
            {
                _notes.Add("background_needs_restore_color");
                return "refused_no_restore_color";
            }
            try
            {
                _restoreBackground = restore;
                _backgroundTouched = true;
                _doc.SetPlainBackground(colour);
                return "applied";
            }
            catch (Exception ex)
            {
                _backgroundTouched = false;
                _notes.Add("background_failed: " + ex.Message);
                return "failed";
            }
        }

        // ---------------------------------------------------------- restore

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            // Each step guarded on its own: one failure must not abandon the
            // rest, or a document keeps a fade because a camera restore threw.
            if (_materialsTouched) Guard(() => _doc.Models.ResetAllTemporaryMaterials(), "materials_not_reset");
            if (_hidden.Count > 0) Guard(() => _doc.Models.SetHidden(_hidden, false), "hidden_not_restored");
            if (_backgroundTouched) Guard(() => _doc.SetPlainBackground(_restoreBackground), "background_not_restored");
            if (_gridTouched)
            {
                Guard(() =>
                {
                    _doc.Grids.ActiveSystem = _grid;
                    _doc.Grids.RenderMode = _gridMode;
                }, "grid_not_restored");
            }
            if (_selection != null) Guard(() => _doc.CurrentSelection.CopyFrom(_selection), "selection_not_restored");
            if (_viewpoint != null)
            {
                // JumpCut, not Navigation: a restore that animates takes the
                // camera on a tour of the building between every two images.
                Guard(() => _doc.ActiveView.CopyViewpointFrom(_viewpoint, ViewChange.JumpCut),
                    "viewpoint_not_restored");
            }
        }

        /// <summary>Whether the document is dirty now. Read after Dispose.</summary>
        public bool ModifiedNow() => DocumentContext.SafeIsModified(_doc);

        // ----------------------------------------------------------- detail

        private T Try<T>(Func<T> read, string note) where T : class
        {
            try
            {
                return read();
            }
            catch
            {
                _notes.Add(note);
                return null;
            }
        }

        /// <summary>
        /// Records a failure once, with its reason.
        /// </summary>
        /// <remarks>
        /// De-duplicated because the same detached-item exception fires per
        /// item, and six identical lines in the response tell the reader
        /// nothing the first one did not.
        /// </remarks>
        private void Note(string code, Exception ex)
        {
            var line = code + ": " + (ex?.Message ?? string.Empty);
            if (!_notes.Contains(line)) _notes.Add(line);
        }

        private void Guard(Action action, string note)
        {
            try
            {
                action();
            }
            catch
            {
                _notes.Add(note);
            }
        }

        internal static double Clamp(double value, double low, double high)
            => value < low ? low : (value > high ? high : value);
    }
}
