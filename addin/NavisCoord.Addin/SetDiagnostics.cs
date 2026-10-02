using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Why a search set came back empty, in words a coordinator can act on.
    /// </summary>
    /// <remarks>
    /// An empty set is a configuration error that looks like a clean model:
    /// the clash test built on it runs, reports zero, and the matrix reads as
    /// "nothing clashes". The «Comité de obra» run met the two common causes
    /// back to back — the example profile anchored on <c>CategoryId</c>,
    /// which only ACC aggregates publish (a NWC exported locally from Revit
    /// has none), and sets with two <c>equals</c> conditions, which Navisworks
    /// joins with AND, not OR. Both produced zero elements and a run reported
    /// as configured.
    ///
    /// Navisworks-free so the diagnosis is tested off a licensed machine.
    /// </remarks>
    internal static class SetDiagnostics
    {
        public const string CategoryIdAbsent = "category_id_absent";
        public const string ConditionsAreAnd = "conditions_are_and";
        public const string NoMatch = "no_match";

        /// <summary>
        /// The likely cause of an empty set, or null when it is not empty.
        /// </summary>
        /// <param name="conditions">The set's conditions as the profile wrote them.</param>
        /// <param name="matches">Elements the set captured.</param>
        /// <param name="categoryIdPresent">Whether the document publishes
        /// <c>CategoryId</c> at all (null when nobody looked).</param>
        public static Dictionary<string, object> Diagnose(
            IEnumerable<Dictionary<string, object>> conditions, int matches, bool? categoryIdPresent)
        {
            if (matches > 0) return null;
            var list = (conditions ?? Enumerable.Empty<Dictionary<string, object>>()).ToList();

            var usesCategoryId = list.Any(c => string.Equals(
                Json.Str(c, "property"), "CategoryId", StringComparison.OrdinalIgnoreCase));
            if (usesCategoryId && categoryIdPresent != true)
            {
                return Hint(CategoryIdAbsent,
                    "El set filtra por CategoryId y este documento no publica esa propiedad: solo la " +
                    "traen los agregados de ACC, no los NWC exportados localmente desde Revit. Usa " +
                    "{\"tab\": \"Element\", \"property\": \"Category\", \"value\": \"Walls\"} con el " +
                    "nombre de categoría en el idioma del Revit que publicó.");
            }

            var repeated = list
                .Where(c => SearchOperators.Normalise(Json.Str(c, "test", SearchOperators.Equal)) == SearchOperators.Equal)
                .GroupBy(c => (Json.Str(c, "tab") + "|" + Json.Str(c, "property")).ToLowerInvariant())
                .FirstOrDefault(g => g.Select(c => Json.Str(c, "value"))
                    .Distinct(StringComparer.OrdinalIgnoreCase).Count() > 1);
            if (repeated != null)
            {
                return Hint(ConditionsAreAnd,
                    "El set pide que «" + Json.Str(repeated.First(), "property") + "» sea igual a varios " +
                    "valores a la vez: Navisworks une las condiciones de un set con Y, no con O, así que " +
                    "nada puede cumplirlas. Haz un set por valor.");
            }

            return Hint(NoMatch,
                "Ningún elemento cumple las condiciones dentro del alcance del set. Revisa el nombre " +
                "de la pestaña, de la propiedad y el valor exactamente como los muestra Navisworks.");
        }

        private static Dictionary<string, object> Hint(string code, string text)
            => new Dictionary<string, object> { ["code"] = code, ["hint"] = text };
    }
}
