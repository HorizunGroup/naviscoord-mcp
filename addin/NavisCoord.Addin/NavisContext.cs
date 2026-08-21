using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Autodesk.Navisworks.Api;

namespace NavisCoord
{
    /// <summary>
    /// Everything that turns a live Navisworks document into the export
    /// contract the analysis engine consumes: stable element identity, unit
    /// normalisation, and property harvesting.
    /// </summary>
    internal static class NavisContext
    {
        public const string Schema = "naviscoord.clashexport/1";

        /// <summary>
        /// Properties always harvested, on top of whatever the caller asks
        /// for. `Element Id` is the join key back to Revit and to Power BI:
        /// without it a coordination issue is a dead end, because nobody can
        /// act on it in the tool that authored the geometry.
        /// </summary>
        public static readonly string[] BaseProperties =
        {
            "Element Id", "Id", "Type", "Family", "Family and Type",
            "System Name", "System Type", "System Classification",
            "Level", "Reference Level", "Workset", "Size", "Diameter",
            "Category", "Material", "Comments", "Mark",
            // The storey, on most Revit exports. `Level` is a Revit parameter
            // and plenty of elements do not carry it; Navisworks puts the
            // storey in `Layer`, which is what the engine reads first.
            //
            // It was not in this list, so it arrived only when a caller
            // happened to ask for it by name through the profile's harvest
            // list. An export taken any other way lost the storey on every
            // element — and the loss is silent, because the level module
            // falls back to guessing the storey from the median height of
            // the clashes. Measured: 86% of crossings assigned that way,
            // with a third of them landing on the wrong floor, under a level
            // map that looked entirely plausible.
            "Layer"
        };

        // ------------------------------------------------------------ units

        /// <summary>
        /// Scale factor from document units to metres.
        /// </summary>
        /// <remarks>
        /// Every length crossing the bridge is normalised here so nothing
        /// downstream has to ask what units a project was authored in. A
        /// tolerance of "0.001" means one millimetre on every job.
        /// </remarks>
        public static double MetreScale(Document doc)
        {
            try
            {
                return UnitConversion.ScaleFactor(doc.Units, Units.Meters);
            }
            catch
            {
                return 1.0;
            }
        }

        public static double[] ToMetres(Point3D point, double scale)
            => point == null
                ? new[] { 0.0, 0.0, 0.0 }
                : new[] { point.X * scale, point.Y * scale, point.Z * scale };

        // --------------------------------------------------------- identity

        // Memoised per extraction run. Clash results share ancestors heavily,
        // so without this every element re-walks the same spine and re-scans
        // the same sibling lists.
        private static readonly Dictionary<ModelItem, string> PathCache =
            new Dictionary<ModelItem, string>();
        private static readonly Dictionary<ModelItem, Dictionary<ModelItem, int>> ChildIndexCache =
            new Dictionary<ModelItem, Dictionary<ModelItem, int>>();

        /// <summary>Drops the identity caches. Call at the start of each run.</summary>
        public static void ResetCaches()
        {
            PathCache.Clear();
            ChildIndexCache.Clear();
        }

        /// <summary>
        /// Stable, resolvable identity for a model item: the index path from
        /// the document root, e.g. "2/17/4/1".
        /// </summary>
        /// <remarks>
        /// Identity here is compared with Equals, never ReferenceEquals.
        /// Navisworks hands out a fresh managed wrapper on each property
        /// access, so two wrappers around the same native item are never the
        /// same reference. ModelItem overrides Equals and GetHashCode for
        /// exactly this reason.
        ///
        /// Getting that wrong does not throw — it silently returns an empty
        /// path for every element. Both sides of every clash then share the
        /// blank identity, the analysis reads that as an element clashing
        /// with itself, and a 9,676-clash model reports zero problems.
        /// </remarks>
        public static string PathId(Document doc, ModelItem item)
        {
            if (item == null) return string.Empty;
            if (PathCache.TryGetValue(item, out var cached)) return cached;

            string path;
            var parent = item.Parent;

            if (parent == null)
            {
                var modelIndex = RootIndexOf(doc, item);
                path = modelIndex < 0
                    ? string.Empty
                    : modelIndex.ToString(CultureInfo.InvariantCulture);
            }
            else
            {
                var parentPath = PathId(doc, parent);
                if (parentPath.Length == 0)
                {
                    path = string.Empty;
                }
                else
                {
                    var index = IndexOfChild(parent, item);
                    path = index < 0
                        ? string.Empty
                        : parentPath + "/" + index.ToString(CultureInfo.InvariantCulture);
                }
            }

            PathCache[item] = path;
            return path;
        }

        private static int RootIndexOf(Document doc, ModelItem root)
        {
            for (var i = 0; i < doc.Models.Count; i++)
            {
                if (Equals(doc.Models[i].RootItem, root)) return i;
            }
            return -1;
        }

        private static int IndexOfChild(ModelItem parent, ModelItem child)
        {
            if (!ChildIndexCache.TryGetValue(parent, out var map))
            {
                map = new Dictionary<ModelItem, int>();
                var index = 0;
                foreach (var candidate in parent.Children)
                {
                    map[candidate] = index;
                    index++;
                }
                ChildIndexCache[parent] = map;
            }
            return map.TryGetValue(child, out var found) ? found : -1;
        }

        /// <summary>Walks an index path back to the live item, or null.</summary>
        public static ModelItem Resolve(Document doc, string pathId)
        {
            if (string.IsNullOrWhiteSpace(pathId)) return null;
            var parts = pathId.Split('/');
            var modelIndex = ModelIndexFromPath(pathId);
            if (modelIndex < 0 || modelIndex >= doc.Models.Count) return null;

            var current = doc.Models[modelIndex].RootItem;
            for (var i = 1; i < parts.Length && current != null; i++)
            {
                if (!int.TryParse(parts[i], NumberStyles.Integer, CultureInfo.InvariantCulture, out var childIndex))
                {
                    return null;
                }
                current = current.Children.ElementAtOrDefault(childIndex);
            }
            return current;
        }

        public static ModelItemCollection ResolveMany(Document doc, IEnumerable<string> pathIds)
        {
            var collection = new ModelItemCollection();
            foreach (var pathId in pathIds)
            {
                var item = Resolve(doc, pathId);
                if (item != null) collection.Add(item);
            }
            return collection;
        }

        // ------------------------------------------------------- properties

        /// <summary>
        /// Harvests the requested properties from an item, falling back to its
        /// ancestors.
        /// </summary>
        /// <remarks>
        /// In an NWC export, the parameters that matter for coordination —
        /// Revit's Element Id, the level, and any corporate coding such as
        /// an `ABC_` parameter family — often sit on a parent node rather than on the
        /// geometry that clashed. Walking up is what makes the join keys
        /// usable instead of mostly empty.
        /// </remarks>
        public static Dictionary<string, object> Harvest(ModelItem item, ICollection<string> wanted)
        {
            var result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            if (item == null) return result;

            // Eight, not four.
            //
            // A Revit tree runs File > Level > Category > Family > Type >
            // Instance > Geometry, so an item that clashed can sit five or
            // six hops below the ancestors that describe it. Measured on a
            // real federation: at four, six sides came back with no
            // properties at all and 453 lost their `Mark`.
            //
            // It is also why the same window blind arrived as `Type: Solid`
            // from one document and `Type: WIN_BLIND_...` from another —
            // nearest ancestor wins, and how deep the geometry node sits
            // depends on the tree. First-wins stays: the nearest node is the
            // most specific. Only the ceiling moves.
            //
            // The storey was the reason this was looked at, and the effect
            // there is larger than the property counts suggest. On a document
            // whose `Layer` had gone missing entirely, the share of crossings
            // that had to be placed by guessing their height fell from 57% to
            // 8% — not because `Layer` came back, it is still absent, but
            // because `Level` and `Reference Level` live on ancestors that
            // four hops never reached either.
            //
            // What it cannot do is invent what the tree does not hold. When a
            // document really has no storey anywhere, the honest place for
            // that is the warning the level map prints, not a deeper walk.
            var current = item;
            var depth = 0;
            while (current != null && depth < 8)
            {
                foreach (var category in current.PropertyCategories)
                {
                    foreach (var property in category.Properties)
                    {
                        var name = property.DisplayName;
                        if (string.IsNullOrEmpty(name)) continue;
                        if (result.ContainsKey(name)) continue;
                        if (!IsWanted(name, wanted)) continue;

                        var text = ValueToString(property.Value);
                        if (!string.IsNullOrWhiteSpace(text)) result[name] = text;
                    }
                }
                current = current.Parent;
                depth++;
            }
            return result;
        }

        private static bool IsWanted(string name, ICollection<string> wanted)
        {
            if (wanted == null || wanted.Count == 0) return false;
            foreach (var pattern in wanted)
            {
                if (string.IsNullOrEmpty(pattern)) continue;
                // A trailing * makes a prefix rule, which is how a corporate
                // parameter family (`ABC_*`) gets harvested wholesale.
                if (pattern.EndsWith("*", StringComparison.Ordinal))
                {
                    if (name.StartsWith(pattern.Substring(0, pattern.Length - 1),
                            StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                }
                else if (string.Equals(name, pattern, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        public static string ValueToString(VariantData value)
        {
            if (value == null) return string.Empty;
            try
            {
                switch (value.DataType)
                {
                    case VariantDataType.DisplayString: return value.ToDisplayString();
                    case VariantDataType.IdentifierString: return value.ToIdentifierString();
                    case VariantDataType.Int32: return value.ToInt32().ToString(CultureInfo.InvariantCulture);
                    case VariantDataType.Double: return value.ToDouble().ToString("R", CultureInfo.InvariantCulture);
                    case VariantDataType.Boolean: return value.ToBoolean() ? "true" : "false";
                    case VariantDataType.DateTime: return value.ToDateTime().ToString("o", CultureInfo.InvariantCulture);
                    case VariantDataType.NamedConstant: return value.ToNamedConstant().DisplayName;
                    default: return value.ToString();
                }
            }
            catch
            {
                return string.Empty;
            }
        }

        // ------------------------------------------------------- geometry

        public static Dictionary<string, object> Describe(
            Document doc, ModelItem item, double scale, ICollection<string> wanted)
        {
            var pathId = PathId(doc, item);
            var modelIndex = ModelIndexOf(doc, item);
            if (modelIndex < 0) modelIndex = ModelIndexFromPath(pathId);

            var payload = new Dictionary<string, object>
            {
                ["path_id"] = pathId,
                ["display_name"] = item?.DisplayName ?? string.Empty,
                ["category"] = CategoryOf(item),
                ["model_index"] = modelIndex,
                ["source_file"] = SourceFileOf(item, doc, modelIndex),
                ["parent_path_id"] = CompositeParent(doc, item, out var compositeName),
                ["parent_name"] = compositeName,
                ["props"] = Harvest(item, wanted)
            };

            var box = SafeBoundingBox(item);
            if (box != null)
            {
                payload["bbox_min"] = ToMetres(box.Min, scale);
                payload["bbox_max"] = ToMetres(box.Max, scale);
            }
            else
            {
                payload["bbox_min"] = new[] { 0.0, 0.0, 0.0 };
                payload["bbox_max"] = new[] { 0.0, 0.0, 0.0 };
            }
            return payload;
        }

        /// <summary>
        /// The composite object an item belongs to, if any.
        /// </summary>
        /// <remarks>
        /// This is the key that collapses a four-layer wall or a family's
        /// sub-geometry into one problem, so it must identify a real
        /// composite — never a grouping node.
        ///
        /// Returning any parent is catastrophic and silently so. In a Revit
        /// export the parent of an instance is frequently the category or
        /// level node, shared by hundreds of unrelated elements; keying on it
        /// makes two different elements look like the same one, and the
        /// analysis then discards every clash between them as a self-clash.
        /// On a real 9,676-clash coordination model that filtered out 8,101
        /// of them and reported a clean project.
        ///
        /// IsComposite is the distinction Navisworks itself draws, so it is
        /// the only parent worth trusting here.
        /// </remarks>
        public static string CompositeParent(Document doc, ModelItem item, out string name)
        {
            name = string.Empty;
            var parent = item?.Parent;
            if (parent == null) return string.Empty;

            try
            {
                if (!parent.IsComposite || parent.IsLayer) return string.Empty;
            }
            catch
            {
                return string.Empty;
            }

            name = parent.DisplayName ?? string.Empty;
            return PathId(doc, parent);
        }

        /// <summary>
        /// The authoring category, which is the strongest discipline signal.
        /// </summary>
        /// <remarks>
        /// ClassDisplayName is NOT the Revit category — it is the Navisworks
        /// node class. On a real plant export it returns "Solid" for most
        /// geometry, the file name of every inserted DWG ("SLUDGE WASTE
        /// PUMP.dwg"), and property-holder labels like "Type" or "Family".
        /// Trusting it makes discipline tagging worthless, and the failure is
        /// silent: everything simply lands in the wrong bucket.
        ///
        /// So the published "Category" property wins, and the node class is
        /// only a fallback, filtered against the class names and file
        /// extensions that are never categories.
        /// </remarks>
        public static string CategoryOf(ModelItem item)
        {
            if (item == null) return string.Empty;

            var current = item;
            var depth = 0;
            while (current != null && depth < 5)
            {
                var published = FindProperty(current, "Category");
                if (IsUsableCategory(published)) return CleanCategory(published);
                current = current.Parent;
                depth++;
            }

            current = item;
            depth = 0;
            while (current != null && depth < 4)
            {
                if (IsUsableCategory(current.ClassDisplayName))
                {
                    return CleanCategory(current.ClassDisplayName);
                }
                current = current.Parent;
                depth++;
            }
            return string.Empty;
        }

        /// <summary>
        /// Node classes and artefacts that are never authoring categories.
        /// </summary>
        /// <remarks>
        /// Localised entries are not padding. An NWC exported from a Spanish
        /// Revit publishes its node class as "Sólido", which sails past an
        /// English-only list and becomes the most common "category" in the
        /// model — and every discipline rule then matches nothing.
        /// </remarks>
        private static readonly HashSet<string> NotCategories = new HashSet<string>(
            StringComparer.OrdinalIgnoreCase)
        {
            // English
            "solid", "group", "insert", "layer", "geometry", "file", "type",
            "family", "category", "instance", "collection", "composite object",
            "line", "point", "text", "mesh", "cylinder", "box", "item",
            "element", "material", "node", "unknown",
            // Spanish
            "sólido", "solido", "grupo", "capa", "geometría", "geometria",
            "archivo", "tipo", "familia", "categoría", "categoria", "elemento",
            "inserción", "insercion", "línea", "linea", "punto", "texto",
            "malla", "cilindro", "caja", "nodo", "desconocido", "material",
            // French / Portuguese / Italian / German, same failure mode
            "solide", "groupe", "couche", "fichier", "élément", "elemento",
            "sólido ", "grupo ", "arquivo", "camada",
            "solido ", "gruppo", "livello", "file ",
            "körper", "gruppe", "ebene", "datei", "objekt",
        };

        private static readonly string[] NotCategorySuffixes =
        {
            ".dwg", ".rvt", ".nwc", ".nwd", ".ifc", ".dgn", ".sat", ".3ds", ".skp"
        };

        private static bool IsUsableCategory(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return false;
            var trimmed = value.Trim();

            foreach (var suffix in NotCategorySuffixes)
            {
                if (trimmed.EndsWith(suffix, StringComparison.OrdinalIgnoreCase)) return false;
            }

            // Every check runs on the CLEANED value, because that is what
            // CategoryOf actually returns. Testing the raw string while
            // returning the cleaned one lets "D5010: Elevator" through: the
            // raw form looks like an ordinary name to the code detector, and
            // what comes out the other side is the bare classification code
            // "D5010" — which then becomes the second most common "category"
            // in the model and matches no discipline rule at all.
            var cleaned = CleanCategory(trimmed);
            if (cleaned.Length == 0) return false;

            // A classification code is not a category. Some models publish
            // UniFormat ("D5010") or OmniClass ("23-13 11 11") where the
            // category belongs, and taking it at face value replaces the
            // kind of the element with an accounting code. The
            // classification is valuable — just not as the element's kind.
            if (LooksLikeClassificationCode(cleaned)) return false;

            return !NotCategories.Contains(cleaned);
        }

        private static bool LooksLikeClassificationCode(string value)
        {
            // UniFormat and its extensions: one or two letters followed by
            // digits — D5010, but also D2020100 and D2020200, which are the
            // same scheme at a deeper level. Hard-coding "letter plus exactly
            // four digits" caught the short form and let 474 elements through
            // on a real model wearing the long one.
            var letters = 0;
            while (letters < value.Length && char.IsLetter(value[letters])) letters++;
            if (letters >= 1 && letters <= 2 && value.Length - letters >= 3)
            {
                var digits = true;
                for (var i = letters; i < value.Length; i++)
                {
                    if (!char.IsDigit(value[i])) { digits = false; break; }
                }
                if (digits) return true;
            }

            // OmniClass / MasterFormat: digit pairs separated by dashes or
            // spaces, e.g. "23-13 11 11" or "22 11 16".
            var hasDigit = false;
            foreach (var c in value)
            {
                if (char.IsLetter(c)) return false;
                if (char.IsDigit(c)) hasDigit = true;
                else if (c != '-' && c != ' ' && c != '.') return false;
            }
            return hasDigit && value.Length >= 5;
        }

        /// <summary>
        /// Reduces "Structural Framing: M_W-Wide Flange: W250X38.5" to
        /// "Structural Framing".
        /// </summary>
        /// <remarks>
        /// Revit exports frequently publish category, family and type joined
        /// with colons. Left whole, every family produces a distinct
        /// "category" and no profile rule can ever match one.
        /// </remarks>
        private static string CleanCategory(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return string.Empty;
            var head = value.Split(':')[0];
            return head.Trim();
        }

        /// <summary>
        /// Lectura puntual de una propiedad por nombre visible, para handlers
        /// que necesitan pares como Category/CategoryId del tab Properties.
        /// </summary>
        public static string PropertyOf(ModelItem item, string displayName)
            => FindProperty(item, displayName);

        private static string FindProperty(ModelItem item, string displayName)
        {
            try
            {
                foreach (var category in item.PropertyCategories)
                {
                    foreach (var property in category.Properties)
                    {
                        if (string.Equals(property.DisplayName, displayName, StringComparison.OrdinalIgnoreCase))
                        {
                            var text = ValueToString(property.Value);
                            if (!string.IsNullOrWhiteSpace(text)) return text;
                        }
                    }
                }
            }
            catch
            {
                // An unreadable property tab is not a reason to fail.
            }
            return string.Empty;
        }

        /// <summary>
        /// The file an element came from, with a fallback through the model
        /// index when the item cannot name its own model.
        /// </summary>
        /// <remarks>
        /// <c>ModelItem.Model</c> returns null for plenty of real nodes — it
        /// did so for EVERY element of a five-model federation's clash results,
        /// while the census walking <c>doc.Models</c> named the same files
        /// perfectly. The consequence was total and silent in shape: with no
        /// source file, no discipline rule can match, so 100% of the elements
        /// fell to "unclassified" and the whole priorisation was scored
        /// against a single bucket.
        ///
        /// The model index is recoverable regardless, because the path id is
        /// built from the document root down and its first segment IS that
        /// index. Resolving the name through <c>doc.Models[index]</c> uses the
        /// same lookup the census already proves works.
        /// </remarks>
        public static string SourceFileOf(ModelItem item, Document doc = null, int modelIndex = -1)
        {
            try
            {
                var model = item?.Model;
                if (model != null)
                {
                    var direct = model.SourceFileName;
                    if (string.IsNullOrWhiteSpace(direct)) direct = model.FileName;
                    if (!string.IsNullOrWhiteSpace(direct)) return System.IO.Path.GetFileName(direct);
                }

                if (doc == null || modelIndex < 0 || modelIndex >= doc.Models.Count) return string.Empty;
                var byIndex = doc.Models[modelIndex];
                var name = byIndex.SourceFileName;
                if (string.IsNullOrWhiteSpace(name)) name = byIndex.FileName;
                return string.IsNullOrWhiteSpace(name) ? string.Empty : System.IO.Path.GetFileName(name);
            }
            catch
            {
                return string.Empty;
            }
        }

        /// <summary>The leading segment of a path id is the model index.</summary>
        internal static int ModelIndexFromPath(string pathId)
        {
            if (string.IsNullOrWhiteSpace(pathId)) return -1;
            var head = pathId.Split('/')[0];
            return int.TryParse(head, NumberStyles.Integer, CultureInfo.InvariantCulture, out var index)
                ? index
                : -1;
        }

        public static int ModelIndexOf(Document doc, ModelItem item)
        {
            try
            {
                var model = item?.Model;
                if (model == null) return -1;
                for (var i = 0; i < doc.Models.Count; i++)
                {
                    // Equals, not ReferenceEquals: see PathId.
                    if (Equals(doc.Models[i], model)) return i;
                }
            }
            catch
            {
                // Fall through: an unresolvable model index is not fatal.
            }
            return -1;
        }

        public static BoundingBox3D SafeBoundingBox(ModelItem item)
        {
            try
            {
                return item?.BoundingBox();
            }
            catch
            {
                return null;
            }
        }
    }
}
