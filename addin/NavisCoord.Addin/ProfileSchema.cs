using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace NavisCoord
{
    /// <summary>
    /// The one profile format, and the checks that keep a hand-edited copy
    /// from failing silently.
    /// </summary>
    /// <remarks>
    /// There used to be two profiles with nothing in common but the word:
    /// the engine's <c>default.json</c> (disciplines, weights, tolerances) and
    /// the add-in's <c>naviscoord-profile.json</c> (search sets, clash pairs,
    /// ignore rules). Two files, two defaults, two places to be wrong, and no
    /// way for either side to notice the other had been re-tuned.
    ///
    /// One document now carries both halves under one version string. Either
    /// half may be absent — the engine does not need clash pairs, and the
    /// ribbon does not need severity weights — but what is present is
    /// validated, and every result carries the profile's checksum so a
    /// report can be traced back to the criteria that produced it.
    ///
    /// Validation reports EVERY problem it finds rather than throwing on the
    /// first, because a profile is edited by a coordinator in a text editor
    /// and being told about one typo at a time is how an afternoon is lost.
    /// No Navisworks reference: unit-testable off a licensed machine.
    /// </remarks>
    internal static class ProfileSchemaVersion
    {
        public const string Current = "naviscoord.profile/v1";

        /// <summary>
        /// Versions this build reads. The engine's original tag is accepted
        /// because real profiles in the field carry it and rejecting them
        /// would break every installed copy for a cosmetic rename.
        /// </summary>
        /// <summary>The tag a profile written before versioning is read as.</summary>
        public const string Legacy = "naviscoord.profile/1";

        public static readonly string[] Accepted =
        {
            Current,
            Legacy
        };

        public static bool IsAccepted(string value)
            => Accepted.Any(v => string.Equals(v, (value ?? string.Empty).Trim(),
                StringComparison.OrdinalIgnoreCase));
    }

    internal sealed class ProfileValidation
    {
        public readonly List<string> Errors = new List<string>();
        public readonly List<string> Warnings = new List<string>();
        public string Version = string.Empty;
        public string Name = string.Empty;
        public string Checksum = string.Empty;
        public readonly List<string> Sections = new List<string>();

        public bool Ok => Errors.Count == 0;

        public Dictionary<string, object> ToJson() => new Dictionary<string, object>
        {
            ["ok"] = Ok,
            ["schema"] = Version,
            ["name"] = Name,
            ["checksum"] = Checksum,
            ["sections"] = Sections.Cast<object>().ToList(),
            ["errors"] = Errors.Cast<object>().ToList(),
            ["warnings"] = Warnings.Cast<object>().ToList()
        };
    }

    internal static class ProfileSchema
    {
        // Every section the format defines. Anything else is reported as
        // unknown — a typo'd section name is otherwise silently ignored,
        // which is how a profile "has no effect" for a week.
        private static readonly HashSet<string> KnownSections = new HashSet<string>(
            StringComparer.OrdinalIgnoreCase)
        {
            // engine half
            "disciplines", "element_nouns", "non_discipline_categories",
            // discipline vocabulary (add-in): which codes exist is the
            // project's BEP, so it is declared here rather than compiled in
            "discipline_aliases", "freestanding_disciplines",
            "system_keywords", "movability", "criticality_matrix", "noise_filter",
            "clustering", "severity", "root_cause", "interop", "clash_matrix",
            // workflow half (add-in)
            "sets", "clash", "reglas", "rules",
            // metadata
            "$schema", "schema", "name", "description", "units", "version"
        };

        private static readonly HashSet<string> KnownConditionTests = new HashSet<string>(
            StringComparer.OrdinalIgnoreCase)
        {
            "equals", "not_equals", "contains", "not_contains",
            "wildcard", "not_wildcard", "has"
        };

        /// <summary>
        /// Stable identity of the criteria a result was produced with.
        /// </summary>
        /// <remarks>
        /// The canonical form is a contract with <c>naviscoord/profile.py</c>,
        /// not an internal detail: the server hashes a profile before pushing
        /// it here, and this end recomputes the hash to prove the same
        /// document arrived. The two therefore have to agree character for
        /// character.
        ///
        /// They did not. This side walked the tree into <c>{k:v;}</c> while
        /// Python called <c>json.dumps</c>, so the checksums could never have
        /// matched — and both files carried a comment claiming they mirrored
        /// each other, which is exactly the kind of divergence a checksum is
        /// supposed to catch. Both ends now emit the same canonical JSON:
        /// comment keys dropped, keys ordinal-sorted, strings escaped to
        /// ASCII, and integral numbers written without a fractional part
        /// because this parser has only <c>double</c> and cannot tell
        /// <c>1</c> from <c>1.0</c>.
        /// </remarks>
        public static string Checksum(Dictionary<string, object> profile)
            => ChecksumOf(Canonical(profile));

        /// <summary>Checksum of an already-canonical string.</summary>
        /// <remarks>
        /// Split out so a pushed profile can be hashed as RECEIVED rather
        /// than re-serialised: if the server's canonical text is hashed
        /// directly, a disagreement in the canonicaliser shows up as a
        /// checksum mismatch instead of hiding behind a re-render.
        /// </remarks>
        public static string ChecksumOf(string canonical)
        {
            using (var sha = SHA256.Create())
            {
                var hash = sha.ComputeHash(Encoding.UTF8.GetBytes(canonical ?? string.Empty));
                var hex = new StringBuilder(16);
                for (var i = 0; i < 8; i++) hex.Append(hash[i].ToString("x2", CultureInfo.InvariantCulture));
                return hex.ToString();
            }
        }

        public static string Canonical(object value)
        {
            var sb = new StringBuilder(1024);
            Canonicalise(value, sb);
            return sb.ToString();
        }

        // 2^53: past this a double cannot hold every integer, so the two ends
        // could not agree about the value even in principle.
        private const double ExactIntLimit = 9007199254740992.0;

        private static void Canonicalise(object value, StringBuilder sb)
        {
            switch (value)
            {
                case null:
                    sb.Append("null");
                    return;
                case bool b:
                    sb.Append(b ? "true" : "false");
                    return;
                case string s:
                    CanonicalString(s, sb);
                    return;
                case double d:
                    sb.Append(CanonicalNumber(d));
                    return;
                case IDictionary<string, object> map:
                    sb.Append('{');
                    var first = true;
                    foreach (var key in map.Keys.Where(k => !k.StartsWith("_", StringComparison.Ordinal))
                                 .OrderBy(k => k, StringComparer.Ordinal))
                    {
                        if (!first) sb.Append(',');
                        first = false;
                        CanonicalString(key, sb);
                        sb.Append(':');
                        Canonicalise(map[key], sb);
                    }
                    sb.Append('}');
                    return;
                case System.Collections.IEnumerable list:
                    sb.Append('[');
                    var firstItem = true;
                    foreach (var item in list)
                    {
                        if (!firstItem) sb.Append(',');
                        firstItem = false;
                        Canonicalise(item, sb);
                    }
                    sb.Append(']');
                    return;
                default:
                    CanonicalString(Convert.ToString(value, CultureInfo.InvariantCulture), sb);
                    return;
            }
        }

        internal static string CanonicalNumber(double d)
        {
            if (double.IsNaN(d) || double.IsInfinity(d)) return "null";
            if (d == Math.Floor(d) && Math.Abs(d) <= ExactIntLimit)
            {
                return ((long)d).ToString(CultureInfo.InvariantCulture);
            }
            // Shortest representation that round-trips, which is what
            // Python's repr produces. "R" alone is documented as unreliable
            // on .NET Framework, so the ladder is checked rather than trusted.
            foreach (var format in RoundTripFormats)
            {
                var text = d.ToString(format, CultureInfo.InvariantCulture);
                if (double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var back) &&
                    back.Equals(d))
                {
                    return NormaliseExponent(text);
                }
            }
            return NormaliseExponent(d.ToString("G17", CultureInfo.InvariantCulture));
        }

        private static readonly string[] RoundTripFormats = { "G15", "G16", "G17" };

        /// <summary>C# writes "1E-05" where Python writes "1e-05".</summary>
        private static string NormaliseExponent(string text)
            => text.IndexOf('E') >= 0 ? text.Replace("E", "e") : text;

        private static void CanonicalString(string value, StringBuilder sb)
        {
            sb.Append('"');
            foreach (var c in value ?? string.Empty)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    default:
                        if (c >= ' ' && c <= '~')
                        {
                            sb.Append(c);
                        }
                        else
                        {
                            // Escaped to ASCII so the encoding on the wire
                            // cannot change the hash. Surrogates are emitted
                            // one code unit at a time, which is what the
                            // Python side reproduces for astral characters.
                            sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        }
                        break;
                }
            }
            sb.Append('"');
        }

        public static ProfileValidation Validate(Dictionary<string, object> profile)
        {
            var report = new ProfileValidation();
            if (profile == null || profile.Count == 0)
            {
                report.Errors.Add("El perfil está vacío o no es un objeto JSON.");
                return report;
            }

            report.Checksum = Checksum(profile);
            report.Name = Json.Str(profile, "name", "(sin nombre)");

            var version = Json.Str(profile, "$schema");
            if (string.IsNullOrWhiteSpace(version)) version = Json.Str(profile, "schema");
            report.Version = version;

            if (string.IsNullOrWhiteSpace(version))
            {
                // A WARNING, not an error. Every profile deployed before the
                // format was versioned lacks this key, and rejecting them
                // meant an add-in upgrade broke "Configurar" on every machine
                // in the field — against a profile that was otherwise
                // perfectly valid. A MISSING version migrates; an UNKNOWN one
                // still fails, because that is the case nobody can migrate.
                report.Version = ProfileSchemaVersion.Legacy;
                report.Warnings.Add(
                    "El perfil no declara \"$schema\", así que se lee como " +
                    ProfileSchemaVersion.Legacy + " (el formato anterior a versionar). " +
                    "Añade \"$schema\": \"" + ProfileSchemaVersion.Current +
                    "\" al inicio para fijarlo explícitamente.");
            }
            else if (!ProfileSchemaVersion.IsAccepted(version))
            {
                report.Errors.Add(
                    "Versión de esquema no soportada: \"" + version + "\". Este complemento lee " +
                    string.Join(", ", ProfileSchemaVersion.Accepted) + ".");
            }
            else if (!string.Equals(version, ProfileSchemaVersion.Current, StringComparison.OrdinalIgnoreCase))
            {
                report.Warnings.Add(
                    "El perfil declara \"" + version + "\", una versión anterior compatible. " +
                    "Actualízalo a \"" + ProfileSchemaVersion.Current + "\" cuando puedas.");
            }

            foreach (var key in profile.Keys)
            {
                if (key.StartsWith("_", StringComparison.Ordinal)) continue;
                if (!KnownSections.Contains(key))
                {
                    report.Warnings.Add(
                        "Sección desconocida \"" + key + "\": se ignora. ¿Un typo?");
                    continue;
                }
                if (profile[key] is Dictionary<string, object> || profile[key] is List<object>)
                {
                    report.Sections.Add(key);
                }
            }

            ValidateSets(profile, report);
            ValidateClash(profile, report);
            ValidateSeverity(profile, report);

            if (report.Sections.Count == 0)
            {
                report.Errors.Add(
                    "El perfil no define ninguna sección útil (sets, clash, disciplines, severity…).");
            }
            return report;
        }

        private static void ValidateSets(Dictionary<string, object> profile, ProfileValidation report)
        {
            if (!(profile.TryGetValue("sets", out var raw) && raw is Dictionary<string, object> sets))
            {
                return;
            }

            var folders = Json.Arr(sets, "folders");
            if (folders.Count == 0)
            {
                report.Errors.Add("\"sets\" existe pero no tiene \"folders\".");
                return;
            }

            var seenFolders = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (var f = 0; f < folders.Count; f++)
            {
                if (!(folders[f] is Dictionary<string, object> folder))
                {
                    report.Errors.Add("sets.folders[" + f + "] no es un objeto.");
                    continue;
                }
                var name = Json.Str(folder, "folder");
                if (string.IsNullOrWhiteSpace(name))
                {
                    report.Errors.Add("sets.folders[" + f + "] no tiene \"folder\".");
                    continue;
                }
                if (!seenFolders.Add(name))
                {
                    report.Errors.Add("Carpeta de sets duplicada: \"" + name + "\".");
                }

                var setList = Json.Arr(folder, "sets");
                if (setList.Count == 0)
                {
                    report.Warnings.Add("La carpeta \"" + name + "\" no define ningún set.");
                }

                var seenSets = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var rawSet in setList)
                {
                    if (!(rawSet is Dictionary<string, object> set)) continue;
                    var setName = Json.Str(set, "name");
                    if (string.IsNullOrWhiteSpace(setName))
                    {
                        report.Errors.Add("Un set de \"" + name + "\" no tiene \"name\".");
                        continue;
                    }
                    if (!seenSets.Add(setName))
                    {
                        report.Errors.Add("Set duplicado \"" + setName + "\" en \"" + name + "\".");
                    }

                    foreach (var rawCond in Json.Arr(set, "conditions"))
                    {
                        if (!(rawCond is Dictionary<string, object> cond)) continue;
                        var test = Json.Str(cond, "test", "equals");
                        if (!KnownConditionTests.Contains(test))
                        {
                            report.Errors.Add(
                                "Operador \"" + test + "\" desconocido en " + name + " / " + setName +
                                ". Válidos: " + string.Join(", ", KnownConditionTests.OrderBy(t => t)) + ".");
                        }
                        if (string.IsNullOrWhiteSpace(Json.Str(cond, "property")))
                        {
                            report.Errors.Add(
                                "Una condición de " + name + " / " + setName + " no nombra \"property\".");
                        }
                        if (!string.Equals(test, "has", StringComparison.OrdinalIgnoreCase) &&
                            string.IsNullOrWhiteSpace(Json.Str(cond, "value")))
                        {
                            report.Warnings.Add(
                                "Condición sin \"value\" en " + name + " / " + setName +
                                " con operador \"" + test + "\": ¿querías \"has\"?");
                        }
                    }
                }
            }
        }

        private static void ValidateClash(Dictionary<string, object> profile, ProfileValidation report)
        {
            if (!(profile.TryGetValue("clash", out var raw) && raw is Dictionary<string, object> clash))
            {
                return;
            }

            var folderNames = FolderNames(profile);
            var seenNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var pairs = Json.Arr(clash, "pairs");
            if (pairs.Count == 0)
            {
                report.Warnings.Add("\"clash\" existe pero no define ningún par.");
            }

            var format = Json.Str(clash, "name_format", "{0} VS {1}");
            for (var i = 0; i < pairs.Count; i++)
            {
                if (!(pairs[i] is Dictionary<string, object> pair)) continue;
                var a = Json.Str(pair, "a");
                var b = Json.Str(pair, "b");
                if (string.IsNullOrWhiteSpace(a) || string.IsNullOrWhiteSpace(b))
                {
                    report.Errors.Add("clash.pairs[" + i + "] necesita \"a\" y \"b\".");
                    continue;
                }
                if (folderNames.Count > 0)
                {
                    if (!folderNames.Contains(a))
                    {
                        report.Errors.Add(
                            "clash.pairs[" + i + "] apunta a la carpeta \"" + a + "\", que \"sets\" no define.");
                    }
                    if (!folderNames.Contains(b))
                    {
                        report.Errors.Add(
                            "clash.pairs[" + i + "] apunta a la carpeta \"" + b + "\", que \"sets\" no define.");
                    }
                }

                var name = Json.Str(pair, "name");
                if (string.IsNullOrWhiteSpace(name))
                {
                    try { name = string.Format(format, a, b); }
                    catch (FormatException)
                    {
                        report.Errors.Add("clash.name_format no es una plantilla válida: \"" + format + "\".");
                        name = a + " VS " + b;
                    }
                }
                if (!seenNames.Add(name))
                {
                    report.Errors.Add(
                        "Dos pares de clash producen el mismo nombre de test: \"" + name +
                        "\". Da un \"name\" explícito a uno de ellos.");
                }

                var tolerance = Json.Num(pair, "tolerance_m", 0.001);
                if (tolerance < 0)
                {
                    report.Errors.Add("clash.pairs[" + i + "] tiene tolerance_m negativa.");
                }
                else if (tolerance > 1.0)
                {
                    report.Warnings.Add(
                        "clash.pairs[" + i + "] usa tolerance_m = " + tolerance.ToString("R", CultureInfo.InvariantCulture) +
                        " m. La tolerancia va en METROS: ¿querías milímetros?");
                }
            }
        }

        private static void ValidateSeverity(Dictionary<string, object> profile, ProfileValidation report)
        {
            if (!(profile.TryGetValue("severity", out var raw) && raw is Dictionary<string, object> severity))
            {
                return;
            }
            if (!(severity.TryGetValue("weights", out var rawWeights) &&
                  rawWeights is Dictionary<string, object> weights))
            {
                return;
            }

            var total = weights.Where(kv => !kv.Key.StartsWith("_", StringComparison.Ordinal))
                .Select(kv => kv.Value is double d ? d : 0.0)
                .Sum();
            if (total > 0 && Math.Abs(total - 1.0) > 0.01)
            {
                report.Errors.Add(
                    "Los pesos de severidad suman " + total.ToString("0.00", CultureInfo.InvariantCulture) +
                    " y deben sumar 1.00; la puntuación quedaría fuera del rango 0-100.");
            }
        }

        internal static HashSet<string> FolderNames(Dictionary<string, object> profile)
        {
            var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (!(profile.TryGetValue("sets", out var raw) && raw is Dictionary<string, object> sets))
            {
                return names;
            }
            foreach (var rawFolder in Json.Arr(sets, "folders"))
            {
                if (!(rawFolder is Dictionary<string, object> folder)) continue;
                var name = Json.Str(folder, "folder");
                if (!string.IsNullOrWhiteSpace(name)) names.Add(name);
            }
            return names;
        }
    }
}
