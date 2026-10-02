using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Navisworks.Api;

namespace NavisCoord
{
    internal static class PenetrationInventory
    {
        internal static readonly string[] IdentityProperties =
            { "Type", "Tipo", "Family", "Familia", "Family and Type" };

        /// <param name="shouldStop">Checked between items; true stops the walk
        /// and reports the inventory as incomplete.</param>
        /// <param name="progress">Items scanned so far, reported every few
        /// thousand items so a job poller sees the walk move.</param>
        public static Dictionary<string, object> Read(Document doc, double scale,
            ICollection<string> wanted, Dictionary<string, object> payload,
            Func<bool> shouldStop = null, Action<int> progress = null,
            NodePropertyCache<ModelItem> cache = null)
        {
            var rules = PenetrationRules.FromPayload(payload);
            var result = new List<object>();
            var errors = new List<object>();
            var seen = new HashSet<string>();
            var scanned = 0;
            var stopped = false;

            // No rule can match: answer without walking. See PenetrationRules
            // for why this one branch was most of the export's time.
            var skip = rules.SkipReason();
            if (skip != null)
            {
                errors.Add(new Dictionary<string, object> { ["error"] = skip });
                return Describe(rules, "not_configured", scanned, false, errors, result);
            }

            // Walking every node top-down (TreeWalk), each read by direct
            // lookup of the few names the rules need: no Parent calls, no
            // ModelItem hashing, no path id per node. Matches are still
            // described through the export's full cache.
            var read = NavisContext.NewLookupReader(doc, IdentityProperties);
            var identityNames = new HashSet<string>(IdentityProperties, StringComparer.OrdinalIgnoreCase);
            foreach (var model in doc.Models)
            {
                foreach (var frame in TreeWalk.PreOrder(model.RootItem, node => node.Children, read))
                {
                    var item = frame.Node;
                    scanned++;
                    if ((scanned & 1023) == 0)
                    {
                        progress?.Invoke(scanned);
                        if (shouldStop != null && shouldStop())
                        {
                            stopped = true;
                            break;
                        }
                    }
                    try
                    {
                        // CategoryOf's rule: five levels of published category,
                        // then four of node class — read lazily, nearest first.
                        var category = NavisContext.ResolveCategory(
                            frame.ValuesUp("Category", 5),
                            frame.Chain(4).Select(f => f.Node.ClassDisplayName));
                        var matched = rules.MatchesCategory(category);
                        if (!matched && rules.NeedsIdentity)
                        {
                            var identity = frame.Harvest(identityNames.Contains, 8);
                            var searchable = (item.DisplayName ?? string.Empty) + " " + CompositeName(frame) + " " +
                                             string.Join(" ", identity.Values.Select(v => Convert.ToString(v)));
                            matched = rules.MatchesText(searchable);
                        }
                        if (!matched) continue;
                        var described = NavisContext.Describe(doc, item, scale, wanted, cache);
                        if (seen.Add(Json.Str(described, "path_id"))) result.Add(described);
                    }
                    catch (Exception ex)
                    {
                        errors.Add(new Dictionary<string, object> { ["item_number"] = scanned, ["error"] = ex.Message });
                    }
                }
                if (stopped) break;
            }

            if (stopped)
            {
                errors.Add(new Dictionary<string, object>
                {
                    ["error"] = "Inventario de pasos detenido por cancelación tras " + scanned + " elementos."
                });
            }
            progress?.Invoke(scanned);
            return Describe(rules, "all_loaded_models", scanned, !stopped && errors.Count == 0, errors, result);
        }

        /// <summary>
        /// The composite parent's name, as <c>NavisContext.CompositeParent</c>
        /// gives it, without computing that parent's path id.
        /// </summary>
        private static string CompositeName(TreeWalk.Frame<ModelItem> frame)
        {
            var parent = frame.Parent?.Node;
            if (parent == null) return string.Empty;
            try
            {
                return parent.IsComposite && !parent.IsLayer ? parent.DisplayName ?? string.Empty : string.Empty;
            }
            catch
            {
                return string.Empty;
            }
        }

        private static Dictionary<string, object> Describe(PenetrationRules rules, string scope, int scanned,
            bool complete, List<object> errors, List<object> elements)
            => new Dictionary<string, object>
            {
                ["scope"] = scope,
                ["scanned"] = scanned,
                ["complete"] = complete,
                ["errors"] = errors,
                ["elements"] = elements,
                ["categories"] = rules.Categories.ToArray(),
                ["keywords"] = rules.Keywords.ToArray()
            };
    }
}
