using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Which discipline codes exist, and which of them mean the same thing.
    /// </summary>
    /// <remarks>
    /// Read from the profile rather than compiled in. A discipline vocabulary
    /// is the one part of coordination that is never the same twice: the codes
    /// come from the project's BEP, they are in the client's language, and two
    /// teams on the same continent disagree about them. A list baked into this
    /// assembly would be one organisation's list wearing a generic name, and
    /// every other user would have to fork the add-in to change three strings.
    ///
    /// The codes are taken from the folders in <c>sets</c>, which is the same
    /// block that builds the search sets and the category whitelists — so the
    /// audit, the sets and the model-name matching cannot drift apart, and
    /// adding a discipline is one edit in one file.
    ///
    /// Aliases exist because a code is not always unique: the same discipline
    /// reaches a federation under two names when two design offices label it
    /// differently, and the sets, the whitelists and the clash matrix all have
    /// to treat them as one. They are declared in the profile:
    ///
    /// <code>
    /// "discipline_aliases": { "HVA": "MEC" }
    /// </code>
    ///
    /// meaning "a model tagged HVA belongs to MEC". Without an entry a code is
    /// its own canonical form, which is the common case.
    ///
    /// No Navisworks reference, so it is unit-testable off a licensed machine.
    /// </remarks>
    internal sealed class DisciplineVocabulary
    {
        /// <summary>What a model name matches when nothing does.</summary>
        public const string Unknown = "?";

        public const string AliasSection = "discipline_aliases";

        /// <summary>
        /// Disciplines whose models legitimately sit BESIDE the building
        /// rather than inside it — site, landscape, survey and the like.
        /// </summary>
        /// <remarks>
        /// The co-location audit exists to catch a model published without
        /// shared coordinates, which lands kilometres away and makes every
        /// clash test return a false clean. Some disciplines are far away on
        /// purpose, and flagging them every run teaches the coordinator to
        /// ignore the report. Which ones those are is a project decision, so
        /// it is declared in the profile:
        ///
        /// <code>
        /// "freestanding_disciplines": ["SITE", "LAND"]
        /// </code>
        ///
        /// Empty by default: with nothing declared a distant model is reported
        /// as displaced, which is the truthful answer when nobody has said
        /// otherwise.
        /// </remarks>
        public const string FreestandingSection = "freestanding_disciplines";

        /// <summary>
        /// The vocabulary of a session with no profile: it recognises nothing.
        /// </summary>
        /// <remarks>
        /// Deliberately empty rather than a built-in guess. A guessed code
        /// silently assigns a model to a discipline nobody configured, and the
        /// resulting audit looks populated while describing a project that
        /// does not exist. <see cref="Unknown"/> for everything is the honest
        /// answer, and the workflow reports it as "no profile loaded".
        /// </remarks>
        public static readonly DisciplineVocabulary Empty = new DisciplineVocabulary(
            new string[0],
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase),
            new HashSet<string>(StringComparer.OrdinalIgnoreCase));

        private readonly string[] _tokens;
        private readonly Dictionary<string, string> _aliases;
        private readonly HashSet<string> _freestanding;

        private DisciplineVocabulary(
            string[] tokens, Dictionary<string, string> aliases, HashSet<string> freestanding)
        {
            _tokens = tokens;
            _aliases = aliases;
            _freestanding = freestanding;
        }

        public IReadOnlyList<string> Tokens => _tokens;
        public bool IsEmpty => _tokens.Length == 0;

        // ------------------------------------------------------------ build

        public static DisciplineVocabulary FromProfile(Dictionary<string, object> profile)
        {
            if (profile == null) return Empty;

            var tokens = new List<string>();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (profile.TryGetValue("sets", out var rawSets) && rawSets is Dictionary<string, object> sets)
            {
                foreach (var rawFolder in Json.Arr(sets, "folders"))
                {
                    if (!(rawFolder is Dictionary<string, object> folder)) continue;
                    var name = Json.Str(folder, "folder");
                    if (!string.IsNullOrWhiteSpace(name) && seen.Add(name.Trim()))
                    {
                        tokens.Add(name.Trim());
                    }
                }
            }

            var aliases = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            if (profile.TryGetValue(AliasSection, out var rawAliases) &&
                rawAliases is Dictionary<string, object> map)
            {
                foreach (var pair in map)
                {
                    var from = (pair.Key ?? string.Empty).Trim();
                    var to = Convert.ToString(pair.Value ?? string.Empty).Trim();
                    if (from.Length == 0 || to.Length == 0) continue;
                    if (string.Equals(from, to, StringComparison.OrdinalIgnoreCase)) continue;
                    aliases[from] = to;
                    // An alias is also a code a model name may carry, even
                    // when it owns no folder of its own.
                    if (seen.Add(from)) tokens.Add(from);
                }
            }

            var freestanding = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var raw in Json.Arr(profile, FreestandingSection))
            {
                var code = Convert.ToString(raw ?? string.Empty).Trim();
                if (code.Length > 0) freestanding.Add(code);
            }

            // Longest first: with "MEC" and "MECH" both declared, a name
            // carrying -MECH- must not match on the shorter prefix.
            tokens.Sort((a, b) => b.Length.CompareTo(a.Length));
            return new DisciplineVocabulary(tokens.ToArray(), aliases, freestanding);
        }

        /// <summary>
        /// Reads the profile at <paramref name="path"/>, or returns
        /// <see cref="Empty"/> when it cannot be read.
        /// </summary>
        /// <remarks>
        /// Tolerant on purpose: a missing or unreadable profile is reported by
        /// the workflow that asked for it, in one place, with the path in the
        /// message. Throwing from here would turn that into an exception from
        /// whichever handler happened to touch a model name first.
        /// </remarks>
        public static DisciplineVocabulary FromProfilePath(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path)) return Empty;
            try
            {
                return FromProfile(Json.ParseObject(File.ReadAllText(path)));
            }
            catch
            {
                return Empty;
            }
        }

        // ------------------------------------------------------------ query

        /// <summary>
        /// The discipline code embedded in a model name, or <see cref="Unknown"/>.
        /// </summary>
        /// <remarks>
        /// Matched as <c>-CODE-</c> so a code cannot be found inside an
        /// unrelated word: a project called "TORRE-NORTE" must not register as
        /// discipline "OR" because the letters happen to be there.
        /// </remarks>
        public string TokenIn(string modelName)
        {
            var name = modelName ?? string.Empty;
            return _tokens.FirstOrDefault(
                t => name.IndexOf("-" + t + "-", StringComparison.OrdinalIgnoreCase) >= 0) ?? Unknown;
        }

        /// <summary>The code this one is an alias of, or the code itself.</summary>
        public string Canonical(string code)
        {
            if (string.IsNullOrWhiteSpace(code)) return code;
            return _aliases.TryGetValue(code.Trim(), out var canonical) ? canonical : code;
        }

        /// <summary>
        /// Whether a model of this discipline is expected to sit away from the
        /// rest, so the co-location audit reports it without calling it a fault.
        /// </summary>
        public bool IsFreestanding(string code)
            => !string.IsNullOrWhiteSpace(code) &&
               (_freestanding.Contains(code.Trim()) || _freestanding.Contains(Canonical(code)));

        public Dictionary<string, object> Describe() => new Dictionary<string, object>
        {
            ["tokens"] = _tokens.OrderBy(t => t, StringComparer.OrdinalIgnoreCase).Cast<object>().ToList(),
            ["aliases"] = _aliases.ToDictionary(p => p.Key, p => (object)p.Value),
            ["freestanding"] = _freestanding.OrderBy(t => t, StringComparer.OrdinalIgnoreCase)
                .Cast<object>().ToList(),
            ["source"] = IsEmpty ? "sin perfil" : "perfil"
        };
    }
}
