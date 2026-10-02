using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Navisworks.Api;

namespace NavisCoord
{
    internal static class PenetrationInventory
    {
        private static readonly string[] IdentityProperties =
            { "Type", "Tipo", "Family", "Familia", "Family and Type" };

        /// <param name="shouldStop">Checked between items; true stops the walk
        /// and reports the inventory as incomplete.</param>
        /// <param name="progress">Items scanned so far, reported every few
        /// thousand items so a job poller sees the walk move.</param>
        public static Dictionary<string, object> Read(Document doc, double scale,
            ICollection<string> wanted, Dictionary<string, object> payload,
            Func<bool> shouldStop = null, Action<int> progress = null)
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

            foreach (var model in doc.Models)
            {
                foreach (var item in model.RootItem.DescendantsAndSelf)
                {
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
                        var matched = rules.MatchesCategory(NavisContext.CategoryOf(item));
                        if (!matched && rules.NeedsIdentity)
                        {
                            var identity = NavisContext.Harvest(item, IdentityProperties);
                            NavisContext.CompositeParent(doc, item, out var parentName);
                            var searchable = (item.DisplayName ?? string.Empty) + " " + parentName + " " +
                                             string.Join(" ", identity.Values.Select(v => Convert.ToString(v)));
                            matched = rules.MatchesText(searchable);
                        }
                        if (!matched) continue;
                        var described = NavisContext.Describe(doc, item, scale, wanted);
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
