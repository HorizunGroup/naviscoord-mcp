using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Which discipline owns an element, decided the same way every run.
    /// </summary>
    /// <remarks>
    /// The previous rule was a single OR inside a <c>foreach</c> over a
    /// dictionary's keys:
    ///
    /// <code>
    /// if (fileRules[code].Contains(sourceFile) ||
    ///     categoryRules[code].Contains(category)) { claim(); break; }
    /// </code>
    ///
    /// Two things are wrong with it, and both bite on real federations. A
    /// per-file rule — the coordinator's explicit "everything in
    /// PROY-RCI-T4.nwc is RCI" — carried no more weight than a generic
    /// category rule, so whichever discipline the enumerator happened to
    /// reach first won. And <c>Dictionary</c> makes no ordering promise, so
    /// the same model could be tagged differently between two runs of the
    /// same build, which is indistinguishable from the model having changed.
    ///
    /// The precedence is now stated, ordered and testable:
    ///
    ///   1. a rule naming this source file,
    ///   2. a rule naming this category,
    ///   3. the declared fallback, if any.
    ///
    /// Within a tier the rule DECLARED FIRST in the profile wins, so profile
    /// order is the tie-break for genuinely ambiguous cases and the answer
    /// never depends on hash iteration. Every collision is recorded so a
    /// profile that contradicts itself says so instead of silently picking.
    ///
    /// No Navisworks reference: this is the part that had the bug, so it is
    /// the part that gets unit tests.
    /// </remarks>
    internal sealed class DisciplineRouter
    {
        internal sealed class Rule
        {
            public string Discipline;
            public HashSet<string> Categories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            public HashSet<string> SourceFiles = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            public int Order;
        }

        public const string ByFile = "source_file";
        public const string ByCategory = "category";
        public const string ByFallback = "fallback";
        public const string Unmatched = "";

        private readonly List<Rule> _rules;
        private readonly string _fallback;
        private readonly List<string> _conflicts = new List<string>();

        public DisciplineRouter(IEnumerable<Rule> rules, string fallback = null)
        {
            _rules = (rules ?? Enumerable.Empty<Rule>())
                .Where(r => r != null && !string.IsNullOrWhiteSpace(r.Discipline))
                .OrderBy(r => r.Order)
                .ToList();
            _fallback = string.IsNullOrWhiteSpace(fallback) ? null : fallback;
            DetectConflicts();
        }

        public IReadOnlyList<string> Conflicts => _conflicts;

        public sealed class Verdict
        {
            public string Discipline = Unmatched;
            public string Basis = Unmatched;
            public bool Ambiguous;

            public bool Matched => !string.IsNullOrEmpty(Discipline);
        }

        /// <summary>The owning discipline and WHY, or an unmatched verdict.</summary>
        public Verdict Resolve(string sourceFile, string category)
        {
            var file = (sourceFile ?? string.Empty).Trim();
            var cat = (category ?? string.Empty).Trim();

            var byFile = _rules.Where(r => file.Length > 0 && r.SourceFiles.Contains(file)).ToList();
            if (byFile.Count > 0)
            {
                return new Verdict
                {
                    Discipline = byFile[0].Discipline,
                    Basis = ByFile,
                    Ambiguous = byFile.Count > 1
                };
            }

            var byCategory = _rules.Where(r => cat.Length > 0 && r.Categories.Contains(cat)).ToList();
            if (byCategory.Count > 0)
            {
                return new Verdict
                {
                    Discipline = byCategory[0].Discipline,
                    Basis = ByCategory,
                    Ambiguous = byCategory.Count > 1
                };
            }

            return _fallback == null
                ? new Verdict()
                : new Verdict { Discipline = _fallback, Basis = ByFallback };
        }

        private void DetectConflicts()
        {
            var fileOwners = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
            var categoryOwners = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);

            foreach (var rule in _rules)
            {
                foreach (var file in rule.SourceFiles) Track(fileOwners, file, rule.Discipline);
                foreach (var category in rule.Categories) Track(categoryOwners, category, rule.Discipline);
            }

            foreach (var pair in fileOwners.Where(p => p.Value.Count > 1).OrderBy(p => p.Key, StringComparer.Ordinal))
            {
                _conflicts.Add(
                    "El archivo \"" + pair.Key + "\" lo reclaman " + string.Join(", ", pair.Value) +
                    "; gana " + pair.Value[0] + " por orden de declaración en el perfil.");
            }
            foreach (var pair in categoryOwners.Where(p => p.Value.Count > 1).OrderBy(p => p.Key, StringComparer.Ordinal))
            {
                _conflicts.Add(
                    "La categoría \"" + pair.Key + "\" la reclaman " + string.Join(", ", pair.Value) +
                    "; gana " + pair.Value[0] + " por orden de declaración en el perfil.");
            }
        }

        private static void Track(Dictionary<string, List<string>> map, string key, string discipline)
        {
            if (!map.TryGetValue(key, out var owners))
            {
                owners = new List<string>();
                map[key] = owners;
            }
            if (!owners.Contains(discipline, StringComparer.OrdinalIgnoreCase)) owners.Add(discipline);
        }

        /// <summary>Builds a router from the wire payload of <c>sets/build</c>.</summary>
        public static DisciplineRouter FromSpecs(List<object> specs, string fallback = null)
        {
            var rules = new List<Rule>();
            var order = 0;
            foreach (var raw in specs ?? new List<object>())
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                var code = Json.Str(spec, "discipline");
                if (string.IsNullOrWhiteSpace(code)) continue;
                rules.Add(new Rule
                {
                    Discipline = code,
                    Categories = new HashSet<string>(Json.StrArr(spec, "categories"), StringComparer.OrdinalIgnoreCase),
                    SourceFiles = new HashSet<string>(Json.StrArr(spec, "source_files"), StringComparer.OrdinalIgnoreCase),
                    Order = order++
                });
            }
            return new DisciplineRouter(rules, fallback);
        }

        public Dictionary<string, object> Describe() => new Dictionary<string, object>
        {
            ["precedence"] = "regla por archivo > regla por categoría > fallback",
            ["tie_break"] = "orden de declaración en el perfil",
            ["disciplines"] = _rules.Select(r => (object)r.Discipline).ToList(),
            ["fallback"] = _fallback ?? string.Empty,
            ["conflicts"] = _conflicts.Cast<object>().ToList()
        };
    }

    /// <summary>
    /// The search-condition operators the profile may use, and what each one
    /// implies for how the search is built.
    /// </summary>
    /// <remarks>
    /// Two properties matter and neither is obvious from the operator name:
    ///
    /// * whether it NEGATES, which also decides whether items lacking the
    ///   property are kept (they are — excluding "everything that does not say
    ///   ESTRUCTURA" must not also exclude everything with no Keynote at all);
    /// * whether it takes part in the integer retry. Navisworks stores
    ///   <c>CategoryId</c> as an int, so an <c>equals</c> that finds nothing as
    ///   text is retried as an int — but a <c>not_contains</c> on a Keynote in
    ///   the same set has no integer form, and letting it veto the retry is
    ///   how a whole discipline's sets silently came back empty.
    ///
    /// Keeping the table here rather than inline in a switch is what makes it
    /// assertable without a Navisworks licence.
    /// </remarks>
    internal static class SearchOperators
    {
        // `Equal`, not `Equals`: a const named Equals inside a static class
        // hides object.Equals and every call site reads ambiguously.
        public const string Equal = "equals";
        public const string NotEquals = "not_equals";
        public const string Contains = "contains";
        public const string NotContains = "not_contains";
        public const string Wildcard = "wildcard";
        public const string NotWildcard = "not_wildcard";
        public const string Has = "has";

        private static readonly HashSet<string> All = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            Equal, NotEquals, Contains, NotContains, Wildcard, NotWildcard, Has
        };

        private static readonly HashSet<string> Negations = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            NotEquals, NotContains, NotWildcard
        };

        // Only a value-equality test has an integer form worth retrying.
        private static readonly HashSet<string> IntRetryable = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            Equal
        };

        public static string Normalise(string test)
        {
            var value = (test ?? string.Empty).Trim();
            return value.Length == 0 ? Equal : value.ToLowerInvariant();
        }

        public static bool IsKnown(string test) => All.Contains(Normalise(test));

        public static bool IsNegation(string test) => Negations.Contains(Normalise(test));

        /// <summary>
        /// True when this condition's value must parse as an int for the whole
        /// set to be worth retrying in integer mode.
        /// </summary>
        public static bool ParticipatesInIntRetry(string test) => IntRetryable.Contains(Normalise(test));

        /// <summary>
        /// Whether a set of conditions should be retried with integer values.
        /// </summary>
        public static bool ShouldRetryAsInt(IEnumerable<Dictionary<string, object>> conditions)
        {
            var relevant = (conditions ?? Enumerable.Empty<Dictionary<string, object>>())
                .Where(c => c != null && ParticipatesInIntRetry(Json.Str(c, "test", Equal)))
                .ToList();
            if (relevant.Count == 0) return false;
            return relevant.All(c => int.TryParse(
                Json.Str(c, "value"), NumberStyles.Integer, CultureInfo.InvariantCulture, out _));
        }

        public static IReadOnlyCollection<string> Known => All.OrderBy(x => x, StringComparer.Ordinal).ToList();
    }
}
