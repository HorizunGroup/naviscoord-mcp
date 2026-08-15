using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Level naming, repeated-series detection and view-purity scoring, with
    /// every Navisworks type kept out.
    /// </summary>
    /// <remarks>
    /// These three are the parts of the ribbon workflow that are pure
    /// judgement about strings and counts, and therefore the parts most worth
    /// asserting. They used to live inline inside the button handlers, where
    /// the only way to exercise them was to open a licensed Navisworks with a
    /// real federation attached — which is to say, never in CI. Lifting them
    /// out changes no behaviour and makes the behaviour checkable.
    ///
    /// The HTTP routes and the ribbon buttons both call these, which is the
    /// point: one implementation, two front doors.
    /// </remarks>
    internal static class LevelNaming
    {
        public const string Missing = "SIN NIVEL";

        /// <summary>
        /// The label a clash is grouped under.
        /// </summary>
        /// <remarks>
        /// A bare number is padded and prefixed, because Navisworks sorts
        /// group names as text: "Nivel 10" lands between "Nivel 1" and
        /// "Nivel 2" otherwise, and the coordinator reads the tree top to
        /// bottom expecting floors in order. Anything already carrying a name
        /// is left alone — renaming "SÓTANO" to "Nivel 00" would lose
        /// information the modeller put there on purpose.
        /// </remarks>
        public static string Normalise(string raw)
        {
            if (string.IsNullOrWhiteSpace(raw)) return Missing;
            var text = raw.Trim();
            if (text.Length == 0) return Missing;
            if (text.All(char.IsDigit)) return "Nivel " + text.PadLeft(2, '0');
            return text;
        }

        /// <summary>
        /// Strips the trailing Navisworks instance id from a display name.
        /// </summary>
        /// <remarks>
        /// "Muro básico 200 [1234567]" and "Muro básico 200 [7654321]" are the
        /// same type in two places. Comparing raw names makes every instance
        /// its own series and the repeat detector finds nothing.
        /// </remarks>
        public static string StripInstanceId(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return string.Empty;
            var bracket = name.LastIndexOf('[');
            return (bracket > 0 ? name.Substring(0, bracket) : name).Trim();
        }
    }

    /// <summary>
    /// The same interference repeated up a stack of typical floors.
    /// </summary>
    /// <remarks>
    /// Finding it is what turns forty ACC issues into one. The identity is
    /// (test, the two element types, the plan position rounded to half a
    /// metre) — deliberately NOT the elevation, since that is the axis the
    /// repetition runs along.
    /// </remarks>
    internal static class RepeatSeries
    {
        public sealed class Occurrence
        {
            public string Test = string.Empty;
            public string ElementA = string.Empty;
            public string ElementB = string.Empty;
            public double X;
            public double Y;
            public string Level = string.Empty;
            public string Guid = string.Empty;
        }

        public sealed class Series
        {
            public string Key = string.Empty;
            public string Test = string.Empty;
            public string ElementA = string.Empty;
            public string ElementB = string.Empty;
            public double X;
            public double Y;
            public List<string> Levels = new List<string>();
            public List<string> Guids = new List<string>();
            public int Fragments;

            public int Count => Guids.Count;
        }

        public sealed class Report
        {
            public List<Series> Repeated = new List<Series>();
            public int Fragments;
            public int SeriesConsidered;
        }

        /// <summary>Half-metre plan bucket; the elevation is deliberately absent.</summary>
        public static string KeyFor(string test, string a, string b, double x, double y)
        {
            var first = a ?? string.Empty;
            var second = b ?? string.Empty;
            // Order-independent: A-vs-B and B-vs-A are one interference, and
            // Navisworks does not promise which side lands in Item1.
            if (string.CompareOrdinal(first, second) > 0)
            {
                var swap = first; first = second; second = swap;
            }
            return string.Join("|",
                test ?? string.Empty, first, second,
                Bucket(x).ToString("F1", CultureInfo.InvariantCulture),
                Bucket(y).ToString("F1", CultureInfo.InvariantCulture));
        }

        private static double Bucket(double value) => Math.Round(value * 2) / 2;

        public static Report Detect(IEnumerable<Occurrence> occurrences, int minLevels = 3)
        {
            var report = new Report();
            var grouped = new Dictionary<string, Series>(StringComparer.Ordinal);

            foreach (var hit in occurrences ?? Enumerable.Empty<Occurrence>())
            {
                if (hit == null) continue;
                if (string.IsNullOrEmpty(hit.ElementA) && string.IsNullOrEmpty(hit.ElementB)) continue;

                var key = KeyFor(hit.Test, hit.ElementA, hit.ElementB, hit.X, hit.Y);
                if (!grouped.TryGetValue(key, out var series))
                {
                    var a = hit.ElementA ?? string.Empty;
                    var b = hit.ElementB ?? string.Empty;
                    if (string.CompareOrdinal(a, b) > 0) { var s = a; a = b; b = s; }
                    series = new Series
                    {
                        Key = key,
                        Test = hit.Test ?? string.Empty,
                        ElementA = a,
                        ElementB = b,
                        X = Bucket(hit.X),
                        Y = Bucket(hit.Y)
                    };
                    grouped[key] = series;
                }
                series.Guids.Add(hit.Guid ?? string.Empty);
                var level = string.IsNullOrWhiteSpace(hit.Level) ? LevelNaming.Missing : hit.Level;
                if (!series.Levels.Contains(level, StringComparer.OrdinalIgnoreCase))
                {
                    series.Levels.Add(level);
                }
                else
                {
                    // Same pair, same plan spot, same storey, more than once:
                    // one interference the modeller split into fragments.
                    series.Fragments++;
                }
            }

            report.SeriesConsidered = grouped.Count;
            foreach (var series in grouped.Values)
            {
                report.Fragments += series.Fragments;
                if (series.Levels.Count < minLevels) continue;
                series.Levels.Sort(StringComparer.OrdinalIgnoreCase);
                report.Repeated.Add(series);
            }

            report.Repeated = report.Repeated
                .OrderByDescending(s => s.Levels.Count)
                .ThenBy(s => s.Test, StringComparer.OrdinalIgnoreCase)
                .ThenBy(s => s.ElementA, StringComparer.OrdinalIgnoreCase)
                .ToList();
            return report;
        }

        /// <summary>CSV rows, with every model-supplied value neutralised.</summary>
        public static List<string> ToCsv(Report report)
        {
            var lines = new List<string> { "test;elemento_a;elemento_b;x_m;y_m;niveles;choques;guids" };
            foreach (var series in report?.Repeated ?? new List<Series>())
            {
                lines.Add(string.Join(";",
                    CsvCell(series.Test),
                    CsvCell(series.ElementA),
                    CsvCell(series.ElementB),
                    CsvCell(series.X.ToString("F1", CultureInfo.InvariantCulture)),
                    CsvCell(series.Y.ToString("F1", CultureInfo.InvariantCulture)),
                    CsvCell(string.Join(",", series.Levels)),
                    series.Count.ToString(CultureInfo.InvariantCulture),
                    CsvCell(string.Join(",", series.Guids))));
            }
            return lines;
        }

        /// <summary>
        /// Neutralises spreadsheet formula injection and the delimiter.
        /// </summary>
        /// <remarks>
        /// Element names come from a Revit file authored by somebody else and
        /// land in a CSV a coordinator opens in Excel. A type legitimately
        /// named <c>=cmd|'/c calc'!A1</c> executes on open. Same rule as the
        /// Python exporter, so both halves of the tool are equally boring.
        /// </remarks>
        internal static string CsvCell(string value)
        {
            var text = (value ?? string.Empty).Replace("\r", " ").Replace("\n", " ");
            if (text.Length == 0) return text;
            var trimmed = text.TrimStart(' ', '\t');
            var dangerous = trimmed.Length > 0 &&
                            "=+-@\t\r".IndexOf(trimmed[0]) >= 0 &&
                            !IsNumber(trimmed);
            if (dangerous) text = "'" + text;
            return text.Replace(";", ",");
        }

        private static bool IsNumber(string text)
            => double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out _);
    }

    /// <summary>
    /// Whether a published coordination view shows only its own discipline.
    /// </summary>
    /// <remarks>
    /// A wall inside the electrical model means the view template in that
    /// Revit was never filtered, and every clash test against it will be
    /// noisy in a way no tolerance can fix. The whitelists come from the same
    /// profile that builds the sets, so tuning one tunes the other.
    /// </remarks>
    internal static class ViewPurity
    {
        public sealed class Finding
        {
            public string Discipline = string.Empty;
            public int IntruderElements;
            public int UnclassifiedElements;
            public bool Dirty;
            public bool Evaluated = true;
            public string Note = string.Empty;
            public List<Tuple<string, int, string>> Intruders = new List<Tuple<string, int, string>>();
            public List<Tuple<string, int, string>> Unclassified = new List<Tuple<string, int, string>>();
        }

        /// <summary>
        /// Elements of other disciplines below this count are contextual
        /// clutter, not a broken template. Five is what stopped a single
        /// stray reference object being reported as a dirty view.
        /// </summary>
        public const int DirtyThreshold = 5;

        public static Finding Evaluate(
            string discipline,
            Dictionary<string, int> categoryTally,
            Dictionary<string, HashSet<string>> whitelists,
            Dictionary<string, string> categoryNames,
            Func<string, string> canonical = null)
        {
            var finding = new Finding { Discipline = discipline ?? "?" };
            canonical = canonical ?? (d => d);
            var own = canonical(discipline ?? string.Empty);

            if (categoryTally == null || categoryTally.Count == 0)
            {
                finding.Evaluated = false;
                finding.Note = "sin elementos legibles";
                return finding;
            }
            if (string.IsNullOrWhiteSpace(discipline) || discipline == "?" ||
                whitelists == null || !whitelists.ContainsKey(own))
            {
                finding.Evaluated = false;
                finding.Note = "disciplina fuera del perfil";
                return finding;
            }

            var mine = whitelists[own];
            foreach (var entry in categoryTally.OrderByDescending(e => e.Value).ThenBy(e => e.Key, StringComparer.Ordinal))
            {
                if (mine.Contains(entry.Key)) continue;

                var owners = whitelists
                    .Where(w => !string.Equals(w.Key, own, StringComparison.OrdinalIgnoreCase) &&
                                w.Value.Contains(entry.Key))
                    .Select(w => w.Key)
                    .OrderBy(w => w, StringComparer.OrdinalIgnoreCase)
                    .ToList();

                categoryNames = categoryNames ?? new Dictionary<string, string>();
                categoryNames.TryGetValue(entry.Key, out var display);

                if (owners.Count == 0)
                {
                    // Nobody claims it: rails in a schedule view, grids,
                    // levels. Reported with name AND id so the profile can be
                    // extended from real data instead of guesses.
                    finding.UnclassifiedElements += entry.Value;
                    finding.Unclassified.Add(Tuple.Create(display ?? "(sin nombre)", entry.Value, entry.Key));
                    continue;
                }
                finding.IntruderElements += entry.Value;
                finding.Intruders.Add(Tuple.Create(display ?? entry.Key, entry.Value, string.Join("/", owners)));
            }

            finding.Dirty = finding.IntruderElements >= DirtyThreshold;
            return finding;
        }
    }
}
