using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// The add-in half of the shared JSON corpus.
    /// </summary>
    /// <remarks>
    /// The same fixture <c>server/tests/test_json_corpus.py</c> reads. Two
    /// readers over one document is two grammars, and for a long time these
    /// two disagreed: Python's <c>json</c> accepted <c>NaN</c> through its
    /// default <c>parse_constant</c>, while the C# scanner accepted
    /// <c>1..2</c>, <c>+1</c>, <c>01</c> and <c>1e</c> because it looped over
    /// "characters that appear in numbers" and asked only for one digit
    /// somewhere. A profile one end installs and the other refuses is a
    /// split-brain profile, and the checksum meant to prove they agree is
    /// computed after parsing — so it proves nothing about the disagreement.
    ///
    /// Both sides assert acceptance AND category, so a change to either reader
    /// fails here rather than quietly moving the boundary.
    /// </remarks>
    internal static class JsonCorpusTests
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
            ValidCasesAreAccepted();
            InvalidCasesAreRefused();
            CategoriesMatchPython();
            NumbersFollowTheGrammar();
            DepthIsBounded();
            SizeAndShapeAreBounded();
            TheOldScannerWouldHaveAccepted();
        }

        // ------------------------------------------------------------ corpus

        private static Dictionary<string, object> _corpus;

        private static Dictionary<string, object> Corpus()
        {
            if (_corpus != null) return _corpus;
            var path = FindRepoFile(System.IO.Path.Combine(
                "server", "tests", "fixtures", "json-corpus.json"));
            if (path == null) return null;
            JsonStrict.TryParseObject(System.IO.File.ReadAllText(path), out _corpus, out _);
            return _corpus;
        }

        private static List<Dictionary<string, object>> Cases(string bucket)
        {
            var corpus = Corpus();
            if (corpus == null) return new List<Dictionary<string, object>>();
            return Json.Arr(corpus, bucket).OfType<Dictionary<string, object>>().ToList();
        }

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

        // ------------------------------------------------------------- cases

        private static void TheCorpusIsShared()
        {
            _section("json: el corpus compartido se encuentra y se lee");

            // Read with the reader under test, which is also the first
            // assertion: the corpus itself is strict JSON.
            _check(Corpus() != null, "se encontró y parseó server/tests/fixtures/json-corpus.json");
            _check(Cases("valid").Count >= 20, "hay casos válidos suficientes");
            _check(Cases("invalid").Count >= 25, "y casos inválidos suficientes");
        }

        private static void ValidCasesAreAccepted()
        {
            _section("json: todo el corpus válido se acepta");

            foreach (var item in Cases("valid"))
            {
                var name = Json.Str(item, "name");
                var text = Json.Str(item, "text");
                var ok = JsonStrict.TryParseObject(text, out var parsed, out var failure);
                _check(ok, "'" + name + "' se acepta" +
                           (failure == null ? "" : " (dio " + failure.Code + ": " + failure.Detail + ")"));
                if (ok) _check(parsed != null, "'" + name + "' produce un objeto");
            }
        }

        private static void InvalidCasesAreRefused()
        {
            _section("json: todo el corpus inválido se rechaza");

            foreach (var item in Cases("invalid"))
            {
                var name = Json.Str(item, "name");
                var text = Json.Str(item, "text");
                var ok = JsonStrict.TryParseObject(text, out _, out var failure);
                _check(!ok, "'" + name + "' se rechaza");
                if (!ok) _check(!string.IsNullOrEmpty(failure.Code), "'" + name + "' lleva código");
            }
        }

        private static void CategoriesMatchPython()
        {
            _section("json: la categoría de rechazo coincide con Python");

            var mismatches = new List<string>();
            foreach (var item in Cases("invalid"))
            {
                var expected = Json.Str(item, "code");
                if (string.IsNullOrEmpty(expected)) continue;
                JsonStrict.TryParseObject(Json.Str(item, "text"), out _, out var failure);
                if (failure != null && failure.Code != expected)
                {
                    mismatches.Add(Json.Str(item, "name") + ": esperaba " + expected +
                                   ", dio " + failure.Code);
                }
            }
            _eq(0, mismatches.Count,
                "las categorías coinciden con el corpus" +
                (mismatches.Count == 0 ? "" : " — " + string.Join("; ", mismatches)));
        }

        private static void NumbersFollowTheGrammar()
        {
            _section("json: los números siguen la gramática y nada más");

            // The forms the old scanner let through, one by one.
            foreach (var bad in new[]
                     {
                         "1..2", "+1", "01", "1e", "1e+", "1e-", "1.", ".5", "-", "--1",
                         "0x1F", "1.2.3", "1e2e3", "00", "-01", "+0.5", "Infinity", "NaN"
                     })
            {
                _check(!JsonStrict.IsStrictObject("{\"a\": " + bad + "}"),
                    "'" + bad + "' no es un número JSON");
            }

            foreach (var good in new[]
                     {
                         "0", "-0", "1", "-1", "10", "1.5", "-1.5", "0.001",
                         "1e3", "1E3", "1e+3", "1e-3", "-1.25e-3", "9007199254740992"
                     })
            {
                _check(JsonStrict.IsStrictObject("{\"a\": " + good + "}"),
                    "'" + good + "' sí lo es");
            }

            // And the value survives the parse rather than degrading to zero,
            // which is what the old ParseNumber did with anything it disliked.
            JsonStrict.TryParseObject("{\"a\": -1.25e-3}", out var parsed, out _);
            _eq(-0.00125, Json.Num(parsed, "a"), "el valor se conserva");
        }

        private static void DepthIsBounded()
        {
            _section("json: la profundidad está acotada y no revienta la pila");

            var atLimit = new string('x', 0);
            atLimit = string.Concat(Enumerable.Repeat("{\"a\":", JsonStrict.MaxDepth)) + "1" +
                      new string('}', JsonStrict.MaxDepth);
            _check(JsonStrict.IsStrictObject(atLimit),
                "el máximo (" + JsonStrict.MaxDepth + ") se acepta");

            var past = string.Concat(Enumerable.Repeat("{\"a\":", JsonStrict.MaxDepth + 1)) + "1" +
                       new string('}', JsonStrict.MaxDepth + 1);
            JsonStrict.TryParseObject(past, out _, out var failure);
            _check(failure != null, "el máximo + 1 se rechaza");
            _eq(JsonStrict.TooDeep, failure.Code, "con json_too_deep");

            // Far past the limit. The refusal has to arrive as a value, not as
            // a StackOverflowException — which .NET does not let anybody catch,
            // so it would take Navisworks down with it.
            var absurd = string.Concat(Enumerable.Repeat("{\"a\":", 20000)) + "1" +
                         new string('}', 20000);
            JsonStrict.TryParseObject(absurd, out _, out var deep);
            _check(deep != null, "una profundidad absurda se rechaza sin desbordar la pila");
            _eq(JsonStrict.TooDeep, deep.Code, "también con json_too_deep");
        }

        private static void SizeAndShapeAreBounded()
        {
            _section("json: hay límites de tamaño, clave y array");

            var longKey = "{\"" + new string('k', JsonStrict.MaxKeyLength + 10) + "\": 1}";
            JsonStrict.TryParseObject(longKey, out _, out var keyFailure);
            _check(keyFailure != null, "una clave enorme se rechaza");
            _eq(JsonStrict.TooLarge, keyFailure.Code, "con json_too_large");

            var manyItems = "{\"a\": [" +
                            string.Join(",", Enumerable.Repeat("1", JsonStrict.MaxArrayLength + 1)) +
                            "]}";
            JsonStrict.TryParseObject(manyItems, out _, out var arrayFailure);
            _check(arrayFailure != null, "un array enorme se rechaza");

            // A key at exactly the limit is fine: the bound exists to stop a
            // hostile body, not to constrain an author.
            var okKey = "{\"" + new string('k', JsonStrict.MaxKeyLength) + "\": 1}";
            _check(JsonStrict.IsStrictObject(okKey), "una clave en el límite se acepta");
        }

        private static void TheOldScannerWouldHaveAccepted()
        {
            _section("json: el escáner anterior habría aceptado un perfil truncado");

            // The concrete failure this replaced: a profile whose last brace
            // was lost in transit came back from the permissive reader as a
            // complete-looking dictionary, and the add-in installed it.
            const string truncated = "{\"schema\": \"naviscoord.profile/v1\", \"weights\": {\"a\": 1.0}";

            var repaired = Json.ParseObject(truncated);
            _check(repaired.Count > 0,
                "el lector tolerante todavía devuelve un objeto de un texto truncado");
            _check(repaired.ContainsKey("schema"),
                "…con campos que parecen completos");

            _check(!JsonStrict.IsStrictObject(truncated),
                "pero el estricto lo rechaza, que es el que usan las fronteras");
            _check(!Json.IsWellFormedObject(truncated),
                "y IsWellFormedObject ahora delega en el estricto");
        }
    }
}
