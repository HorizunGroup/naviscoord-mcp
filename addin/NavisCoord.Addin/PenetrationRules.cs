using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Which elements count as an opening (sleeve, penetration) for the
    /// inventory, decided without touching Navisworks.
    /// </summary>
    /// <remarks>
    /// Split out of <see cref="PenetrationInventory"/> because the decision is
    /// what made the export slow, and a decision can be tested off a licensed
    /// machine. The inventory used to walk every node of every model and, for
    /// each one, read its category, harvest five identity properties through
    /// eight ancestors and resolve its composite parent — and only THEN ask
    /// whether any rule could match. With a profile that declares no rules
    /// (the «Comité de obra» profile declares none) nothing could ever match,
    /// so the whole walk — hundreds of thousands of property reads on the UI
    /// thread — produced an empty list. On the real three-model federation it
    /// was most of the ~60 s every <c>clash/export</c> held Navisworks.
    ///
    /// The order is now cheapest first: no rules means no walk at all; a
    /// category rule is tested before any property is harvested; the identity
    /// properties are only read when a keyword rule exists to search them.
    /// </remarks>
    internal sealed class PenetrationRules
    {
        private readonly HashSet<string> _categories;
        private readonly List<string> _keywords;

        public PenetrationRules(IEnumerable<string> categories, IEnumerable<string> keywords, bool configured)
        {
            _categories = new HashSet<string>(
                (categories ?? Enumerable.Empty<string>())
                    .Where(c => !string.IsNullOrWhiteSpace(c))
                    .Select(c => c.Trim()),
                StringComparer.OrdinalIgnoreCase);
            _keywords = (keywords ?? Enumerable.Empty<string>())
                .Where(k => !string.IsNullOrWhiteSpace(k))
                .Select(k => k.Trim())
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
            Configured = configured;
        }

        public static PenetrationRules FromPayload(Dictionary<string, object> payload)
        {
            payload = payload ?? new Dictionary<string, object>();
            return new PenetrationRules(
                Json.StrArr(payload, "penetration_categories"),
                Json.StrArr(payload, "penetration_keywords"),
                payload.ContainsKey("penetration_categories") && payload.ContainsKey("penetration_keywords"));
        }

        /// <summary>Whether the caller sent the rule lists at all.</summary>
        public bool Configured { get; }

        /// <summary>Whether any rule could ever match an element.</summary>
        public bool HasRules => _categories.Count > 0 || _keywords.Count > 0;

        /// <summary>Whether the walk has to read identity properties.</summary>
        public bool NeedsIdentity => _keywords.Count > 0;

        public IReadOnlyCollection<string> Categories => _categories;
        public IReadOnlyList<string> Keywords => _keywords;

        public bool MatchesCategory(string category)
            => !string.IsNullOrEmpty(category) && _categories.Contains(category.Trim());

        public bool MatchesText(string searchable)
            => !string.IsNullOrEmpty(searchable) &&
               _keywords.Any(k => searchable.IndexOf(k, StringComparison.OrdinalIgnoreCase) >= 0);

        /// <summary>
        /// Why the inventory was not walked, or null when it should be.
        /// </summary>
        public string SkipReason()
        {
            if (!Configured) return "Opening classification rules were not supplied.";
            if (!HasRules)
            {
                return "El perfil no declara reglas de pasos (noise_filter.pass_through_categories / " +
                       "pass_through_keywords): no se recorrió el modelo buscando pasos.";
            }
            return null;
        }
    }
}
