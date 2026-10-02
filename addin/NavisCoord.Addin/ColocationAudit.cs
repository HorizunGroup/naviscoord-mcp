using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// What the model-location audit can honestly say, and what it cannot.
    /// </summary>
    /// <remarks>
    /// The audit used to call two models «co-ubicados» whenever their
    /// bounding boxes overlapped with five metres of slack. That proves they
    /// occupy the same area, not that they share coordinates: in the «Comité
    /// de obra» run the MEP model's survey point sat about ten metres away
    /// from structure's and architecture's, and the three boxes — a building
    /// tens of metres across — still overlapped, so the report said the
    /// opposite of the truth.
    ///
    /// Two things change. The geometric verdict is named for what it is
    /// ("misma zona"), with <c>coordinates_verified: false</c>. And when the
    /// NWC files publish reference-point properties (survey point, project
    /// base point, shared coordinates), they are compared across models: a
    /// difference is reported as a mismatch even when the boxes overlap. When
    /// nothing is published, the audit says it could not verify, instead of
    /// implying it did.
    ///
    /// Navisworks-free so every branch is tested without a licence.
    /// </remarks>
    internal static class ColocationAudit
    {
        public const string SingleModel = "único modelo";
        public const string SameArea = "misma zona (coordenadas sin verificar)";
        public const string Colocated = "co-ubicado (puntos de referencia iguales)";
        public const string ReferenceMismatch = "misma zona, pero con punto de referencia distinto";

        private static readonly string[] ReferencePatterns =
        {
            "survey point", "punto de reconocimiento", "project base point", "punto base",
            "shared coordinates", "coordenadas compartidas", "east/west", "este/oeste",
            "north/south", "norte/sur", "angle to true north", "ángulo con el norte"
        };

        /// <summary>Whether a property name describes a model's reference point.</summary>
        public static bool IsReferenceProperty(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return false;
            var lowered = name.ToLowerInvariant();
            return ReferencePatterns.Any(p => lowered.Contains(p));
        }

        /// <summary>The comparison of reference points across models.</summary>
        internal sealed class ReferenceCheck
        {
            /// <summary>At least one property was published by two or more models.</summary>
            public bool Comparable;
            /// <summary>Model indices whose value differs from the majority.</summary>
            public HashSet<int> Mismatched = new HashSet<int>();
            public List<string> Differences = new List<string>();
        }

        /// <summary>
        /// Compares the reference properties each model published.
        /// </summary>
        /// <param name="perModel">Property name → value, one dictionary per model.</param>
        public static ReferenceCheck Compare(IList<Dictionary<string, string>> perModel)
        {
            var check = new ReferenceCheck();
            if (perModel == null) return check;

            var names = perModel
                .Where(m => m != null)
                .SelectMany(m => m.Keys)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(n => n, StringComparer.OrdinalIgnoreCase);
            foreach (var name in names)
            {
                var values = new List<Tuple<int, string>>();
                for (var i = 0; i < perModel.Count; i++)
                {
                    if (perModel[i] != null && perModel[i].TryGetValue(name, out var value) &&
                        !string.IsNullOrWhiteSpace(value))
                    {
                        values.Add(Tuple.Create(i, value.Trim()));
                    }
                }
                if (values.Count < 2) continue;
                check.Comparable = true;

                var groups = values
                    .GroupBy(v => v.Item2, StringComparer.OrdinalIgnoreCase)
                    .OrderByDescending(g => g.Count())
                    .ToList();
                if (groups.Count == 1) continue;
                // No majority (two models, two values): nobody is "the right
                // one", so every model carrying the property is flagged.
                var tie = groups[0].Count() == groups[1].Count();
                var majority = tie ? null : groups[0].Key;
                foreach (var value in values.Where(v =>
                             tie || !string.Equals(v.Item2, majority, StringComparison.OrdinalIgnoreCase)))
                {
                    check.Mismatched.Add(value.Item1);
                    check.Differences.Add(name + ": modelo " + (value.Item1 + 1) + " = «" + value.Item2 + "»" +
                                          (tie ? " (sin mayoría)" : ", la mayoría = «" + majority + "»"));
                }
            }
            return check;
        }

        /// <summary>The status of a model that overlaps another one.</summary>
        /// <param name="coordinatesVerified">True only when reference points
        /// were published, compared and found equal.</param>
        public static string ForOverlap(int index, ReferenceCheck reference, out bool coordinatesVerified)
        {
            coordinatesVerified = false;
            if (reference != null && reference.Mismatched.Contains(index)) return ReferenceMismatch;
            if (reference != null && reference.Comparable)
            {
                coordinatesVerified = true;
                return Colocated;
            }
            return SameArea;
        }
    }
}
