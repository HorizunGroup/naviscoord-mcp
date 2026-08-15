using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Navisworks.Api;

namespace NavisCoord
{
    /// <summary>
    /// Reads how a model is actually structured, instead of assuming.
    /// </summary>
    /// <remarks>
    /// A coordination tool that ships a fixed list of disciplines and Revit
    /// categories only works on models that happen to match it. Real
    /// federations arrive in Spanish, in French, from Tekla, from Plant 3D,
    /// with a corporate classification in a shared parameter, or with nothing
    /// but layer names carrying the discipline.
    ///
    /// So this samples the model and reports what is there: every property
    /// that exists, how much of the model actually carries it, how many
    /// distinct values it takes, and what those values look like. That is the
    /// evidence needed to decide what can serve as a discipline key — and it
    /// is evidence, not a guess, which matters because everything downstream
    /// keys off that decision.
    /// </remarks>
    internal static class SchemaHandlers
    {
        private const int MaxSampleValues = 12;

        /// <summary>
        /// Joins a property tab and name into one dictionary key.
        /// </summary>
        /// <remarks>
        /// A separator that cannot occur in either half, so "Item" + "Id"
        /// and "" + "ItemId" stay distinct keys.
        ///
        /// Written as an escape rather than pasted in as the byte: the raw
        /// character is invisible in every editor, and it makes git classify
        /// the whole file as binary — which silently exempts it from the LF
        /// normalisation in .gitattributes and from every textual diff.
        /// </remarks>
        private const string TabNameSeparator = "\u0001";

        private sealed class PropertyStats
        {
            public string Tab = string.Empty;
            public string Name = string.Empty;
            public int Present;
            public readonly Dictionary<string, int> Values = new Dictionary<string, int>(StringComparer.Ordinal);

            /// <summary>
            /// value -> category -> count.
            /// </summary>
            /// <remarks>
            /// This cross-tab is what separates a discipline key from a storey
            /// key without knowing a word of the model's language. A storey
            /// contains walls and pipes and ducts alike, so knowing the storey
            /// tells you nothing about what an element is. A trade is the
            /// opposite: its groups are dominated by particular categories.
            ///
            /// Without it a scorer picks "Layer" — 100% coverage, 14 tidy
            /// values, nicely balanced — and cheerfully proposes the building's
            /// floors as its disciplines.
            /// </remarks>
            public readonly Dictionary<string, Dictionary<string, int>> ByCategory =
                new Dictionary<string, Dictionary<string, int>>(StringComparer.Ordinal);

            public void Record(string value, string category)
            {
                Present++;
                if (Values.Count < 4000)
                {
                    Values.TryGetValue(value, out var count);
                    Values[value] = count + 1;
                }

                if (string.IsNullOrEmpty(category) || ByCategory.Count > 80) return;
                if (!ByCategory.TryGetValue(value, out var byCat))
                {
                    if (ByCategory.Count >= 80) return;
                    byCat = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                    ByCategory[value] = byCat;
                }
                if (byCat.Count >= 60) return;
                byCat.TryGetValue(category, out var n);
                byCat[category] = n + 1;
            }
        }

        public static Dictionary<string, object> ModelSchema(Dictionary<string, object> payload)
        {
            var doc = Router.RequireDocument();
            NavisContext.ResetCaches();

            var sampleSize = Math.Max(200, Math.Min(120000, Json.Int(payload, "sample", 20000)));
            var stride = Math.Max(1, Json.Int(payload, "stride", 0));

            var stats = new Dictionary<string, PropertyStats>(StringComparer.Ordinal);
            var categories = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var perModel = new List<object>();
            var totalSampled = 0;

            for (var m = 0; m < doc.Models.Count; m++)
            {
                var model = doc.Models[m];
                var modelCategories = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                var sampled = 0;
                var visited = 0;

                foreach (var item in model.RootItem.DescendantsAndSelf)
                {
                    visited++;
                    if (stride > 1 && visited % stride != 0) continue;
                    if (sampled >= sampleSize) break;

                    // Only leaf geometry carries the properties that matter;
                    // grouping nodes inflate every fill rate with duplicates.
                    if (item.Children.Any()) continue;
                    sampled++;
                    totalSampled++;

                    var category = NavisContext.CategoryOf(item);
                    if (!string.IsNullOrWhiteSpace(category))
                    {
                        modelCategories.TryGetValue(category, out var c);
                        modelCategories[category] = c + 1;
                        categories.TryGetValue(category, out var g);
                        categories[category] = g + 1;
                    }

                    CollectProperties(item, stats, category);
                }

                perModel.Add(new Dictionary<string, object>
                {
                    ["index"] = (double)m,
                    ["source_file"] = System.IO.Path.GetFileName(
                        string.IsNullOrWhiteSpace(model.SourceFileName) ? model.FileName : model.SourceFileName),
                    ["creator"] = model.Creator ?? string.Empty,
                    ["sampled"] = (double)sampled,
                    ["visited"] = (double)visited,
                    ["categories"] = modelCategories
                        .OrderByDescending(kv => kv.Value)
                        .Take(40)
                        .ToDictionary(kv => kv.Key, kv => (object)(double)kv.Value)
                });
            }

            var properties = stats.Values
                .Where(s => s.Present > 0)
                .OrderByDescending(s => s.Present)
                .Take(300)
                .Select(s => (object)new Dictionary<string, object>
                {
                    ["tab"] = s.Tab,
                    ["name"] = s.Name,
                    ["present"] = (double)s.Present,
                    ["coverage"] = totalSampled > 0 ? (double)s.Present / totalSampled : 0.0,
                    ["distinct_values"] = (double)s.Values.Count,
                    // Both ends matter: the most common values show what the
                    // property means, and the count of distinct ones shows
                    // whether it partitions the model or merely labels it.
                    ["top_values"] = s.Values
                        .OrderByDescending(kv => kv.Value)
                        .Take(MaxSampleValues)
                        .ToDictionary(kv => kv.Key, kv => (object)(double)kv.Value),
                    ["by_category"] = s.ByCategory
                        .OrderByDescending(kv => kv.Value.Values.Sum())
                        .Take(MaxSampleValues)
                        .ToDictionary(
                            kv => kv.Key,
                            kv => (object)kv.Value
                                .OrderByDescending(c => c.Value)
                                .Take(12)
                                .ToDictionary(c => c.Key, c => (object)(double)c.Value))
                })
                .ToList();

            return new Dictionary<string, object>
            {
                ["document"] = new Dictionary<string, object>
                {
                    ["title"] = doc.Title ?? string.Empty,
                    ["units"] = doc.Units.ToString(),
                    ["model_count"] = (double)doc.Models.Count
                },
                ["sampled_items"] = (double)totalSampled,
                ["models"] = perModel,
                ["categories"] = categories
                    .OrderByDescending(kv => kv.Value)
                    .Take(120)
                    .ToDictionary(kv => kv.Key, kv => (object)(double)kv.Value),
                ["properties"] = properties
            };
        }

        private static void CollectProperties(
            ModelItem item, Dictionary<string, PropertyStats> stats, string elementCategory)
        {
            try
            {
                foreach (var category in item.PropertyCategories)
                {
                    var tab = category.DisplayName ?? string.Empty;
                    foreach (var property in category.Properties)
                    {
                        var name = property.DisplayName;
                        if (string.IsNullOrEmpty(name)) continue;

                        var value = NavisContext.ValueToString(property.Value);
                        if (string.IsNullOrWhiteSpace(value)) continue;
                        if (value.Length > 120) value = value.Substring(0, 120);

                        var key = tab + TabNameSeparator + name;
                        if (!stats.TryGetValue(key, out var entry))
                        {
                            entry = new PropertyStats { Tab = tab, Name = name };
                            stats[key] = entry;
                        }
                        entry.Record(value, elementCategory);
                    }
                }
            }
            catch
            {
                // An unreadable property tab is not a reason to abort the scan.
            }
        }
    }
}
