using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// The semantic rules a profile must satisfy, mirrored from Python.
    /// </summary>
    /// <remarks>
    /// The add-in cannot run the engine's arithmetic, but it decides whether a
    /// profile is installed — and a profile it accepts is one the server will
    /// later be asked to compute with. If the two ends disagree, the add-in
    /// installs something the engine refuses, and the operator is told the
    /// profile loaded and then that nothing works.
    ///
    /// The table is the same one in <c>server/naviscoord/profilerules.py</c>,
    /// and the same corpus file drives both sides' tests. Categories and paths
    /// are the parity contract; the wording is not, because the two ends write
    /// prose differently and pinning sentences would make a translation a test
    /// failure.
    ///
    /// The defect that motivated it: <c>cluster_size_saturation: 1</c> is
    /// perfect JSON and makes the severity scorer divide by <c>log(1)</c> —
    /// zero — on the first cluster with two clashes in it.
    /// </remarks>
    internal static class ProfileRules
    {
        public const string TypeError = "profile_type";
        public const string RangeError = "profile_range";
        public const string RelationError = "profile_relation";
        public const string ReferenceError = "profile_reference";
        public const string UnknownError = "profile_unknown";

        internal sealed class Problem
        {
            public string Code = string.Empty;
            public string Path = string.Empty;
            public string Detail = string.Empty;

            public Dictionary<string, object> ToJson()
                => new Dictionary<string, object>
                {
                    ["code"] = Code, ["path"] = Path, ["detail"] = Detail
                };

            public override string ToString() => Path + ": " + Detail;
        }

        private sealed class NumberRule
        {
            public string Path;
            public string Why;
            public double? Minimum;
            public double? Maximum;
            public bool ExclusiveMin;
            public bool Integer;

            public NumberRule(string path, string why, double? min = null, double? max = null,
                bool exclusiveMin = false, bool integer = false)
            {
                Path = path; Why = why; Minimum = min; Maximum = max;
                ExclusiveMin = exclusiveMin; Integer = integer;
            }
        }

        // The inventory. Each entry names the operation the value takes part
        // in, so this doubles as an audit of the engine's arithmetic rather
        // than a list of opinions about reasonable numbers.
        private static readonly NumberRule[] Numbers =
        {
            new NumberRule("clustering.eps_m", "radio de vecindad de DBSCAN",
                min: 0.0, max: 1000.0, exclusiveMin: true),
            new NumberRule("clustering.min_samples", "puntos mínimos para un núcleo",
                min: 1, max: 100000, integer: true),
            new NumberRule("clustering.max_cluster_span_m", "cota del tamaño de un problema",
                min: 0.0, max: 10000.0, exclusiveMin: true),
            new NumberRule("severity.cluster_size_saturation",
                "divisor log(x): con 1, log(1)=0", min: 1.0, max: 1e6, exclusiveMin: true),
            new NumberRule("severity.congestion_saturation", "divisor de congestión",
                min: 0.0, max: 1e6, exclusiveMin: true),
            new NumberRule("severity.priority_bands.critical", "corte de la banda crítica",
                min: 0.0, max: 100.0),
            new NumberRule("severity.priority_bands.high", "corte de la banda alta",
                min: 0.0, max: 100.0),
            new NumberRule("severity.priority_bands.medium", "corte de la banda media",
                min: 0.0, max: 100.0),
            new NumberRule("clash_matrix.default_tolerance_m", "tolerancia por defecto",
                min: 0.0, max: 100.0),
            new NumberRule("noise_filter.max_penetration_m", "penetración máxima de ruido",
                min: 0.0, max: 100.0),
            new NumberRule("noise_filter.min_penetration_m", "penetración mínima real",
                min: 0.0, max: 100.0),
            new NumberRule("root_cause.min_clashes", "muestra mínima para una causa",
                min: 1, max: 100000, integer: true),
            new NumberRule("root_cause.min_confidence", "confianza: es una probabilidad",
                min: 0.0, max: 1.0)
        };

        // Mirror of profiles/profile_contract.json: metadata_keys plus the
        // section names. ProfileContractTests reads the JSON from the repo and
        // fails if this set and the contract ever differ, so editing one
        // without the other cannot survive the suite.
        internal static readonly HashSet<string> KnownRootKeys = new HashSet<string>(
            StringComparer.Ordinal)
        {
            // metadata
            "$schema", "schema", "name", "description", "units", "version",
            // sections
            "disciplines", "discipline_aliases", "freestanding_disciplines",
            "element_nouns", "non_discipline_categories", "system_keywords",
            "movability", "criticality_matrix", "noise_filter", "clustering",
            "severity", "root_cause", "interop", "clash_matrix",
            "sets", "clash", "rules", "reglas", "extensions"
        };

        private static readonly (string Path, string Why)[] WeightMaps =
        {
            ("severity.weights", "peso de un componente de severidad"),
            ("movability.by_category", "movilidad por categoría"),
            ("criticality_matrix", "criticidad del par de disciplinas")
        };

        /// <summary>Every semantic rule. Never mutates the profile.</summary>
        public static List<Problem> Validate(Dictionary<string, object> raw)
        {
            var problems = new List<Problem>();
            if (raw == null) return problems;

            // A typo'd section name used to be silently ignored, which is how
            // a profile "has no effect" for a week. Underscore keys are
            // comments; "extensions" is the declared free area.
            foreach (var key in raw.Keys)
            {
                if (key.StartsWith("_", StringComparison.Ordinal)) continue;
                if (KnownRootKeys.Contains(key)) continue;
                problems.Add(Fail(UnknownError, key,
                    "sección desconocida: no está en el contrato del perfil. Los datos " +
                    "propios van dentro de «extensions»; ¿un typo?"));
            }

            foreach (var rule in Numbers)
            {
                if (TryDig(raw, rule.Path, out var value)) Check(rule, value, problems);
            }

            foreach (var map in WeightMaps)
            {
                if (!TryDig(raw, map.Path, out var raw2)) continue;
                if (!(raw2 is Dictionary<string, object> table))
                {
                    problems.Add(Fail(TypeError, map.Path, "debe ser un objeto de pesos"));
                    continue;
                }
                foreach (var pair in table)
                {
                    if (pair.Key.StartsWith("_", StringComparison.Ordinal)) continue;
                    Check(new NumberRule(map.Path + "." + pair.Key, map.Why, min: 0.0, max: 1e6),
                        pair.Value, problems);
                }
            }

            CheckRelations(raw, problems);
            CheckReferences(raw, problems);
            CheckAliases(raw, problems);
            return problems;
        }

        public static bool IsValid(Dictionary<string, object> raw) => Validate(raw).Count == 0;

        private static Problem Fail(string code, string path, string detail)
            => new Problem { Code = code, Path = path, Detail = detail };

        private static void Check(NumberRule rule, object value, List<Problem> problems)
        {
            // Booleans first. The Python side needs this because
            // `isinstance(True, int)` is true there; here it is a separate
            // type, and the check exists so the two ends refuse the same
            // documents for the same reason.
            if (value is bool)
            {
                problems.Add(Fail(TypeError, rule.Path, "se esperaba un número y llegó un booleano"));
                return;
            }
            if (value is string)
            {
                problems.Add(Fail(TypeError, rule.Path, "se esperaba un número y llegó una cadena"));
                return;
            }
            if (value == null)
            {
                problems.Add(Fail(TypeError, rule.Path, "se esperaba un número y llegó null"));
                return;
            }
            if (!(value is double || value is int || value is long))
            {
                problems.Add(Fail(TypeError, rule.Path,
                    "se esperaba un número y llegó " + value.GetType().Name));
                return;
            }

            var number = Convert.ToDouble(value, CultureInfo.InvariantCulture);
            if (double.IsNaN(number) || double.IsInfinity(number))
            {
                problems.Add(Fail(RangeError, rule.Path, "no es finito"));
                return;
            }
            if (rule.Integer && Math.Abs(number - Math.Truncate(number)) > double.Epsilon)
            {
                problems.Add(Fail(TypeError, rule.Path, "debe ser entero"));
                return;
            }
            if (rule.Minimum.HasValue)
            {
                var bad = rule.ExclusiveMin ? number <= rule.Minimum.Value : number < rule.Minimum.Value;
                if (bad)
                {
                    problems.Add(Fail(RangeError, rule.Path,
                        (rule.ExclusiveMin ? "debe ser mayor que " : "no puede ser menor que ") +
                        rule.Minimum.Value.ToString(CultureInfo.InvariantCulture) +
                        " (" + rule.Why + ")"));
                }
            }
            if (rule.Maximum.HasValue && number > rule.Maximum.Value)
            {
                problems.Add(Fail(RangeError, rule.Path,
                    "no puede superar " + rule.Maximum.Value.ToString(CultureInfo.InvariantCulture) +
                    " (" + rule.Why + ")"));
            }
        }

        private static void CheckRelations(Dictionary<string, object> raw, List<Problem> problems)
        {
            if (TryDig(raw, "severity.weights", out var weightsRaw) &&
                weightsRaw is Dictionary<string, object> weights)
            {
                var numeric = weights
                    .Where(p => !p.Key.StartsWith("_", StringComparison.Ordinal))
                    .Select(p => p.Value)
                    .Where(v => !(v is bool) && (v is double || v is int || v is long))
                    .Select(v => Convert.ToDouble(v, CultureInfo.InvariantCulture))
                    .Where(d => !double.IsNaN(d) && !double.IsInfinity(d))
                    .ToList();
                if (numeric.Count > 0 && Math.Abs(numeric.Sum() - 1.0) > 0.01)
                {
                    problems.Add(Fail(RelationError, "severity.weights",
                        "los pesos suman " + numeric.Sum().ToString("0.####", CultureInfo.InvariantCulture) +
                        " y deben sumar 1.00 ±0.01"));
                }
            }

            var bands = new List<double>();
            foreach (var name in new[] { "critical", "high", "medium" })
            {
                if (TryDig(raw, "severity.priority_bands." + name, out var value) &&
                    !(value is bool) && (value is double || value is int || value is long))
                {
                    bands.Add(Convert.ToDouble(value, CultureInfo.InvariantCulture));
                }
            }
            if (bands.Count >= 2 && !bands.SequenceEqual(bands.OrderByDescending(b => b)))
            {
                problems.Add(Fail(RelationError, "severity.priority_bands",
                    "deben ir de mayor a menor: critical > high > medium"));
            }

            if (Number(raw, "noise_filter.min_penetration_m", out var lo) &&
                Number(raw, "noise_filter.max_penetration_m", out var hi) && lo > hi)
            {
                problems.Add(Fail(RelationError, "noise_filter",
                    "min_penetration_m no puede superar max_penetration_m"));
            }

            if (Number(raw, "clustering.eps_m", out var eps) &&
                Number(raw, "clustering.max_cluster_span_m", out var span) && eps > span)
            {
                problems.Add(Fail(RelationError, "clustering",
                    "eps_m supera max_cluster_span_m: cada grupo nacería por encima del máximo"));
            }
        }

        private static void CheckReferences(Dictionary<string, object> raw, List<Problem> problems)
        {
            var declared = new HashSet<string>(StringComparer.Ordinal);
            if (raw.TryGetValue("disciplines", out var disciplinesRaw) &&
                disciplinesRaw is Dictionary<string, object> disciplines)
            {
                foreach (var code in disciplines.Keys)
                {
                    if (!code.StartsWith("_", StringComparison.Ordinal)) declared.Add(code);
                }
                foreach (var code in disciplines.Keys)
                {
                    if (string.IsNullOrWhiteSpace(code))
                    {
                        problems.Add(Fail(ReferenceError, "disciplines",
                            "una disciplina tiene el código vacío"));
                    }
                }
            }

            if (declared.Count > 0 && raw.TryGetValue("criticality_matrix", out var matrixRaw) &&
                matrixRaw is Dictionary<string, object> matrix)
            {
                foreach (var key in matrix.Keys)
                {
                    if (key.StartsWith("_", StringComparison.Ordinal)) continue;
                    var parts = key.Split('|');
                    if (parts.Length != 2)
                    {
                        problems.Add(Fail(ReferenceError, "criticality_matrix." + key,
                            "la clave debe ser 'DISCIPLINA|DISCIPLINA'"));
                        continue;
                    }
                    foreach (var part in parts)
                    {
                        if (!declared.Contains(part))
                        {
                            problems.Add(Fail(ReferenceError, "criticality_matrix." + key,
                                "la disciplina «" + part + "» no está declarada en 'disciplines'"));
                        }
                    }
                }
            }
        }

        private static void CheckAliases(Dictionary<string, object> raw, List<Problem> problems)
        {
            if (!raw.TryGetValue("discipline_aliases", out var aliasRaw) ||
                !(aliasRaw is Dictionary<string, object> aliases)) return;

            foreach (var start in aliases.Keys.ToList())
            {
                if (start.StartsWith("_", StringComparison.Ordinal)) continue;
                var seen = new List<string> { start };
                var current = aliases.TryGetValue(start, out var next) ? next as string : null;
                while (current != null && aliases.ContainsKey(current))
                {
                    if (seen.Contains(current))
                    {
                        problems.Add(Fail(RelationError, "discipline_aliases." + start,
                            "los alias forman un ciclo: " + string.Join(" → ", seen) + " → " + current));
                        break;
                    }
                    seen.Add(current);
                    current = aliases.TryGetValue(current, out var following) ? following as string : null;
                    if (seen.Count > 64)
                    {
                        problems.Add(Fail(RelationError, "discipline_aliases." + start,
                            "la cadena de alias es demasiado larga; probablemente hay un ciclo"));
                        break;
                    }
                }
            }
        }

        private static bool Number(Dictionary<string, object> raw, string path, out double value)
        {
            value = 0.0;
            if (!TryDig(raw, path, out var found)) return false;
            if (found is bool || !(found is double || found is int || found is long)) return false;
            value = Convert.ToDouble(found, CultureInfo.InvariantCulture);
            return !double.IsNaN(value) && !double.IsInfinity(value);
        }

        private static bool TryDig(Dictionary<string, object> raw, string path, out object value)
        {
            value = null;
            object node = raw;
            foreach (var part in path.Split('.'))
            {
                if (!(node is Dictionary<string, object> map) || !map.TryGetValue(part, out node))
                {
                    return false;
                }
            }
            value = node;
            return true;
        }
    }
}
