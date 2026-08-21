using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// The add-in half of the shared semantic corpus.
    /// </summary>
    /// <remarks>
    /// Same fixture as <c>server/tests/test_profile_semantics.py</c>. The
    /// add-in cannot run the engine's arithmetic, but it decides whether a
    /// profile is INSTALLED — so if the two ends disagree, the add-in accepts
    /// something the server will later refuse and the operator is told the
    /// profile loaded and then that nothing works.
    ///
    /// Category and path are the parity contract. The wording is not: the two
    /// sides write prose independently, and pinning sentences would make a
    /// reworded message a test failure.
    /// </remarks>
    internal static class ProfileSemanticsTests
    {
        private static Action<string> _section;
        private static Action<object, object, string> _eq;
        private static Action<bool, string> _check;

        public static void Run(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check)
        {
            _section = section;
            _eq = eq;
            _check = check;

            TheCorpusIsShared();
            EveryDecisionMatches();
            EveryCategoryAndPathMatches();
            TheSaturationReproduction();
            BooleansAreNotNumbers();
            TheShippedProfilesValidate();
            ValidationDoesNotMutate();
        }

        // ------------------------------------------------------------ corpus

        private static Dictionary<string, object> _corpus;
        private static Dictionary<string, object> _default;

        private static string FindRepoFile(string relative)
        {
            var dir = new System.IO.DirectoryInfo(AppContext.BaseDirectory);
            while (dir != null)
            {
                var candidate = System.IO.Path.Combine(dir.FullName, relative);
                if (System.IO.File.Exists(candidate)) return candidate;
                dir = dir.Parent;
            }
            return null;
        }

        private static Dictionary<string, object> Corpus()
        {
            if (_corpus != null) return _corpus;
            var path = FindRepoFile(System.IO.Path.Combine(
                "server", "tests", "fixtures", "profile-semantics.json"));
            if (path == null) return null;
            JsonStrict.TryParseObject(System.IO.File.ReadAllText(path), out _corpus, out _);
            return _corpus;
        }

        private static Dictionary<string, object> Default()
        {
            if (_default != null) return _default;
            var path = FindRepoFile(System.IO.Path.Combine(
                "server", "naviscoord", "profiles", "default.json"));
            if (path == null) return null;
            JsonStrict.TryParseObject(System.IO.File.ReadAllText(path), out _default, out _);
            return _default;
        }

        private static List<Dictionary<string, object>> Cases()
        {
            var corpus = Corpus();
            return corpus == null
                ? new List<Dictionary<string, object>>()
                : Json.Arr(corpus, "cases").OfType<Dictionary<string, object>>().ToList();
        }

        /// <summary>Deep-merges a corpus patch onto the default profile.</summary>
        private static Dictionary<string, object> Merge(
            Dictionary<string, object> baseline, Dictionary<string, object> patch)
        {
            var merged = ProfileStore.DeepCopy(baseline);
            foreach (var pair in patch ?? new Dictionary<string, object>())
            {
                if (pair.Value is Dictionary<string, object> nested &&
                    merged.TryGetValue(pair.Key, out var existing) &&
                    existing is Dictionary<string, object> target)
                {
                    merged[pair.Key] = Merge(target, nested);
                }
                else
                {
                    merged[pair.Key] = pair.Value;
                }
            }
            return merged;
        }

        private static Dictionary<string, object> Apply(Dictionary<string, object> item)
            => Merge(Default(), Json.Obj(item, "patch"));

        // ------------------------------------------------------------- cases

        private static void TheCorpusIsShared()
        {
            _section("perfil: el corpus semántico compartido se encuentra");

            _check(Corpus() != null, "se leyó server/tests/fixtures/profile-semantics.json");
            _check(Default() != null, "y el perfil por defecto");
            _check(Cases().Count >= 20, "hay casos suficientes (" + Cases().Count + ")");
        }

        private static void EveryDecisionMatches()
        {
            _section("perfil: aceptar o rechazar coincide con Python");

            foreach (var item in Cases())
            {
                var name = Json.Str(item, "name");
                var accepted = Json.Bool(item, "accepted");
                var problems = ProfileRules.Validate(Apply(item));

                if (accepted)
                {
                    _eq(0, problems.Count, "'" + name + "' se acepta" +
                        (problems.Count == 0 ? "" : " — dio " +
                         string.Join("; ", problems.Select(p => p.ToString()))));
                }
                else
                {
                    _check(problems.Count > 0, "'" + name + "' se rechaza");
                }
            }
        }

        private static void EveryCategoryAndPathMatches()
        {
            _section("perfil: la categoría y la ruta coinciden con Python");

            var mismatches = new List<string>();
            foreach (var item in Cases())
            {
                if (Json.Bool(item, "accepted")) continue;
                var expectedCode = Json.Str(item, "code");
                var expectedPath = Json.Str(item, "path");
                if (string.IsNullOrEmpty(expectedCode)) continue;

                var problems = ProfileRules.Validate(Apply(item));
                var matched = problems.Any(p => p.Code == expectedCode && p.Path == expectedPath);
                if (!matched)
                {
                    mismatches.Add(Json.Str(item, "name") + ": esperaba " + expectedCode +
                                   " en " + expectedPath + ", salió " +
                                   string.Join(", ", problems.Select(p => p.Code + "@" + p.Path)));
                }
            }
            _eq(0, mismatches.Count,
                "sin divergencias" + (mismatches.Count == 0 ? "" : " — " + string.Join(" | ", mismatches)));
        }

        private static void TheSaturationReproduction()
        {
            _section("perfil: saturación de tamaño igual a 1 se rechaza en el add-in");

            // The defect, stated as a bound rather than as a case: the value
            // is the divisor of `log(count) / log(saturation)`, and log(1) is
            // zero. The add-in refuses to install it, so the engine never sees
            // it — which is the point of mirroring the rules here at all.
            var raw = Merge(Default(), new Dictionary<string, object>
            {
                ["severity"] = new Dictionary<string, object>
                {
                    ["cluster_size_saturation"] = 1.0
                }
            });
            var problems = ProfileRules.Validate(raw);
            _check(problems.Any(p => p.Path == "severity.cluster_size_saturation" &&
                                     p.Code == ProfileRules.RangeError),
                "se rechaza con profile_range en su ruta");

            foreach (var good in new object[] { 1.5, 2.0, 25.0, 1000.0 })
            {
                var ok = Merge(Default(), new Dictionary<string, object>
                {
                    ["severity"] = new Dictionary<string, object>
                    {
                        ["cluster_size_saturation"] = good
                    }
                });
                _check(ProfileRules.IsValid(ok), good + " sí se acepta");
            }
        }

        private static void BooleansAreNotNumbers()
        {
            _section("perfil: un booleano no vale como número");

            var raw = Merge(Default(), new Dictionary<string, object>
            {
                ["clustering"] = new Dictionary<string, object> { ["eps_m"] = true }
            });
            var problems = ProfileRules.Validate(raw);
            _check(problems.Any(p => p.Code == ProfileRules.TypeError &&
                                     p.Path == "clustering.eps_m"),
                "se rechaza con profile_type");
        }

        private static void TheShippedProfilesValidate()
        {
            _section("perfil: los perfiles que se envían siguen validando");

            var problems = ProfileRules.Validate(Default());
            _eq(0, problems.Count, "default.json valida" +
                (problems.Count == 0 ? "" : ": " + string.Join("; ", problems.Select(p => p.ToString()))));
        }

        private static void ValidationDoesNotMutate()
        {
            _section("perfil: validar no toca el perfil");

            var raw = Default();
            var before = ProfileSchema.Canonical(raw);
            ProfileRules.Validate(raw);
            _eq(before, ProfileSchema.Canonical(raw), "el contenido es idéntico después");
        }
    }
}
