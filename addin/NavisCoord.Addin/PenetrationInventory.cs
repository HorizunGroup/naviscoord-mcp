using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Navisworks.Api;

namespace NavisCoord
{
    internal static class PenetrationInventory
    {
        public static Dictionary<string, object> Read(Document doc, double scale,
            ICollection<string> wanted, Dictionary<string, object> payload)
        {
            var categories = new HashSet<string>(Json.StrArr(payload, "penetration_categories"), StringComparer.OrdinalIgnoreCase);
            var keywords = Json.StrArr(payload, "penetration_keywords").Where(k => !string.IsNullOrWhiteSpace(k)).ToList();
            var result = new List<object>();
            var errors = new List<object>();
            var seen = new HashSet<string>();
            var scanned = 0;
            var configured = payload.ContainsKey("penetration_categories") && payload.ContainsKey("penetration_keywords");
            if (!configured) errors.Add(new Dictionary<string, object> { ["error"] = "Opening classification rules were not supplied." });
            foreach (var model in doc.Models)
            {
                foreach (var item in model.RootItem.DescendantsAndSelf)
                {
                    scanned++;
                    try
                    {
                        var name = item.DisplayName ?? string.Empty;
                        var category = NavisContext.CategoryOf(item);
                        var identity = NavisContext.Harvest(item, new[] { "Type", "Tipo", "Family", "Familia", "Family and Type" });
                        NavisContext.CompositeParent(doc, item, out var parentName);
                        var searchable = name + " " + parentName + " " + string.Join(" ", identity.Values.Select(v => Convert.ToString(v)));
                        if (!categories.Contains(category) && !keywords.Any(k => searchable.IndexOf(k, StringComparison.OrdinalIgnoreCase) >= 0)) continue;
                        var described = NavisContext.Describe(doc, item, scale, wanted);
                        if (seen.Add(Json.Str(described, "path_id"))) result.Add(described);
                    }
                    catch (Exception ex)
                    {
                        errors.Add(new Dictionary<string, object> { ["item_number"] = scanned, ["error"] = ex.Message });
                    }
                }
            }
            return new Dictionary<string, object> {
                ["scope"] = "all_loaded_models", ["scanned"] = scanned,
                ["complete"] = configured && errors.Count == 0, ["errors"] = errors, ["elements"] = result,
                ["categories"] = categories.ToArray(), ["keywords"] = keywords
            };
        }
    }
}
