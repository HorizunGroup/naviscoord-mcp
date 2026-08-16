using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading;

namespace NavisCoord.Tests
{
    /// <summary>
    /// Tests for the hand-rolled JSON codec.
    /// </summary>
    /// <remarks>
    /// Every request and response between Navisworks and the MCP server goes
    /// through this code, so a mistake here corrupts everything downstream
    /// while looking like a data problem. It is also the one part of the
    /// addin that runs without Navisworks, which makes it the one part worth
    /// unit-testing at all.
    ///
    /// The Spanish cases are not decoration: element names, level names and
    /// every message the bridge returns carry accents and tildes, and a codec
    /// that only survives ASCII would pass a naive suite and fail on the
    /// first real model.
    /// </remarks>
    internal static class Program
    {
        private static int _failures;
        private static int _checks;

        private static int Main(string[] args)
        {
            // Invariant culture matters: a machine set to Spanish formats
            // doubles with a comma, which would emit invalid JSON.
            Thread.CurrentThread.CurrentCulture = new CultureInfo("es-CO");

            // `--acl-probe` populates a session registry under whatever
            // LOCALAPPDATA points at and exits, so Verify-SessionAcl.ps1 can
            // inspect the permissions the PRODUCTION SessionStore produced.
            // Writing the fixture from PowerShell instead would only audit
            // PowerShell's idea of a DACL, which proves nothing about this
            // code.
            if (args != null && args.Length > 0 && args[0] == "--acl-probe")
            {
                return AclProbe();
            }

            WriterTests();
            ParserTests();
            RoundTripTests();
            AccessorTests();
            RobustnessTests();

            LogicTests.Run(Section, Eq, Check, Fail);
            DispatcherTests.Run(Section, Eq, Check);
            FramingTests.Run(Section, Eq, Check);
            VocabularyTests.Run(Section, Eq, Check);
            WorkflowTextTests.Run(Section, Eq, Check);

            Console.WriteLine();
            Console.WriteLine(_failures == 0
                ? $"OK: {_checks} comprobaciones"
                : $"FALLARON {_failures} de {_checks} comprobaciones");
            return _failures == 0 ? 0 : 1;
        }

        // ------------------------------------------------------------ cases

        private static void WriterTests()
        {
            Section("escritura");

            Eq("null", Json.Write(null), "null se escribe como null");
            Eq("\"hola\"", Json.Write("hola"), "cadena simple");
            Eq("true", Json.Write(true), "booleano");
            Eq("[1,2,3]", Json.Write(new List<object> { 1, 2, 3 }), "arreglo de enteros");

            // A comma decimal separator here would be invalid JSON, and the
            // culture is deliberately set to one that uses commas.
            Eq("1.5", Json.Write(1.5), "los decimales usan punto sin importar la cultura");
            Eq("-0.085", Json.Write(-0.085), "negativo con decimales");

            Eq("null", Json.Write(double.NaN), "NaN se degrada a null en vez de romper el JSON");
            Eq("null", Json.Write(double.PositiveInfinity), "infinito se degrada a null");

            Eq("\"a\\\"b\"", Json.Write("a\"b"), "comillas escapadas");
            Eq("\"a\\\\b\"", Json.Write("a\\b"), "barra invertida escapada");
            Eq("\"a\\nb\"", Json.Write("a\nb"), "salto de línea escapado");
            Eq("\"\\u0007\"", Json.Write("\u0007"), "control no imprimible escapado");

            // Accents pass through as UTF-8 rather than being escaped; the
            // response is written with a UTF-8 encoder either way.
            Eq("\"Tubería\"", Json.Write("Tubería"), "acentos se conservan");
            Eq("\"Muro Bloque 15cm\"", Json.Write("Muro Bloque 15cm"), "texto con espacios");

            var map = new Dictionary<string, object> { ["b"] = 2, ["a"] = 1 };
            Eq("{\"b\":2,\"a\":1}", Json.Write(map), "el objeto conserva el orden de inserción");
        }

        private static void ParserTests()
        {
            Section("lectura");

            var empty = Json.ParseObject("");
            Eq(0, empty.Count, "cadena vacía produce objeto vacío, no excepción");

            var simple = Json.ParseObject("{\"a\":1,\"b\":\"x\"}");
            Eq(1.0, simple["a"], "número leído");
            Eq("x", simple["b"], "cadena leída");

            var nested = Json.ParseObject("{\"o\":{\"k\":[1,{\"z\":true}]}}");
            var inner = (Dictionary<string, object>)nested["o"];
            var list = (List<object>)inner["k"];
            Eq(2, list.Count, "arreglo anidado");
            Eq(true, ((Dictionary<string, object>)list[1])["z"], "objeto dentro de arreglo");

            var escaped = Json.ParseObject("{\"s\":\"a\\\"b\\nc\"}");
            Eq("a\"b\nc", escaped["s"], "escapes deshechos al leer");

            var unicode = Json.ParseObject("{\"s\":\"Tuber\\u00eda\"}");
            Eq("Tubería", unicode["s"], "escape \\u decodificado");

            var negative = Json.ParseObject("{\"d\":-0.085,\"e\":1.2e3}");
            Eq(-0.085, negative["d"], "negativo con decimales");
            Eq(1200.0, negative["e"], "notación exponencial");

            var nulls = Json.ParseObject("{\"n\":null,\"f\":false}");
            Eq(null, nulls["n"], "null leído");
            Eq(false, nulls["f"], "false leído");
        }

        private static void RoundTripTests()
        {
            Section("ida y vuelta");

            // The shape a real clash actually takes, because that is what has
            // to survive: nested objects, arrays of doubles, negative
            // penetration, accented text and an empty property bag.
            var clash = new Dictionary<string, object>
            {
                ["guid"] = "abc-123",
                ["distance_m"] = -0.085,
                ["point"] = new List<object> { 12.34, -5.67, 3.2 },
                ["level"] = "N+03.50 «Cubierta»",
                ["a"] = new Dictionary<string, object>
                {
                    ["display_name"] = "Tubería CPVC 2\"",
                    ["props"] = new Dictionary<string, object> { ["Element Id"] = "912345" }
                },
                ["b"] = new Dictionary<string, object>
                {
                    ["display_name"] = "Viga 40x60",
                    ["props"] = new Dictionary<string, object>()
                }
            };

            var parsed = Json.ParseObject(Json.Write(clash));
            Eq("abc-123", parsed["guid"], "guid sobrevive");
            Eq(-0.085, parsed["distance_m"], "la penetración negativa conserva su signo");
            Eq("N+03.50 «Cubierta»", parsed["level"], "comillas angulares y acentos sobreviven");

            var sideA = (Dictionary<string, object>)parsed["a"];
            Eq("Tubería CPVC 2\"", sideA["display_name"], "comilla doble dentro del nombre");
            Eq("912345", ((Dictionary<string, object>)sideA["props"])["Element Id"],
                "propiedad con espacio en la clave");

            var point = (List<object>)parsed["point"];
            Eq(3, point.Count, "el punto conserva sus tres componentes");
            Eq(-5.67, point[1], "componente negativa");

            var sideB = (Dictionary<string, object>)parsed["b"];
            Eq(0, ((Dictionary<string, object>)sideB["props"]).Count,
                "un bolsón de propiedades vacío sigue siendo un objeto, no null");
        }

        private static void AccessorTests()
        {
            Section("accesores");

            var map = Json.ParseObject(
                "{\"s\":\"x\",\"n\":4.9,\"b\":true,\"arr\":[\"a\",\"b\"],\"nested\":{}}");

            Eq("x", Json.Str(map, "s"), "Str");
            Eq("", Json.Str(map, "ausente"), "Str de clave ausente devuelve vacío");
            Eq("def", Json.Str(map, "ausente", "def"), "Str respeta el valor por defecto");

            Eq(4.9, Json.Num(map, "n"), "Num");
            Eq(7.0, Json.Num(map, "ausente", 7.0), "Num con valor por defecto");
            Eq(4, Json.Int(map, "n"), "Int trunca hacia cero, no redondea");

            Eq(true, Json.Bool(map, "b"), "Bool");
            Eq(false, Json.Bool(map, "ausente"), "Bool de clave ausente es false");
            Eq(true, Json.Bool(map, "ausente", true), "Bool con valor por defecto");

            Eq(2, Json.StrArr(map, "arr").Count, "StrArr");
            Eq(0, Json.StrArr(map, "ausente").Count, "StrArr de clave ausente es vacío");
            Eq(0, Json.Arr(map, "nested").Count, "Arr sobre un objeto devuelve vacío, no falla");

            // Wrong-typed values must not throw: a client sending a string
            // where a number belongs should get the default, not a 500.
            var wrong = Json.ParseObject("{\"n\":\"no soy número\"}");
            Eq(3.0, Json.Num(wrong, "n", 3.0), "Num sobre una cadena cae al valor por defecto");
        }

        private static void RobustnessTests()
        {
            Section("entradas malformadas");

            // The parser must never throw on junk. The bridge would turn an
            // exception into a 500 for what is really a bad request, and the
            // message would say nothing useful.
            foreach (var bad in new[]
                     {
                         "{", "}", "[]", "null", "{\"a\":", "{\"a\":}", "{,}",
                         "no es json", "{\"a\":\"sin cerrar", "{{{{", "   "
                     })
            {
                try
                {
                    var result = Json.ParseObject(bad);
                    Check(result != null, $"«{bad}» no revienta el lector");
                }
                catch (Exception ex)
                {
                    Fail($"«{bad}» lanzó {ex.GetType().Name}");
                }
            }

            var deep = Json.ParseObject("{\"a\":{\"b\":{\"c\":{\"d\":{\"e\":1}}}}}");
            Check(deep.Count == 1, "anidamiento profundo se lee");

            WellFormedTests();
        }

        /// <summary>
        /// Telling a complete body from one the parser merely repaired.
        /// </summary>
        /// <remarks>
        /// Found by the live smoke test, not here: the robustness cases above
        /// fed the parser junk and only checked that it did not throw, so
        /// nothing noticed that <c>{"canonical": "sin cerrar</c> came back as
        /// a perfectly ordinary dictionary. The bridge answered 200 and the
        /// handler acted on half a payload.
        ///
        /// The forgiving parse is still what the handlers want; what was
        /// missing is the transport being able to tell the difference.
        /// </remarks>
        private static void WellFormedTests()
        {
            Section("cuerpo completo vs. cuerpo reparado");

            foreach (var good in new[]
                     {
                         "{}", "{ }", "{\"a\":1}", "{\"a\":\"x\",\"b\":[1,2,{\"c\":null}]}",
                         "  {\"a\": true}  ", "{\"a\":-0.085}", "{\"a\":1.2e3}",
                         "{\"s\":\"con \\\"comillas\\\" y \\\\ barras\"}",
                         "{\"s\":\"acento é y escape \\u00f1\"}"
                     })
            {
                Check(Json.IsWellFormedObject(good), $"«{good}» es un objeto completo");
            }

            // The one the smoke test caught: a string cut off mid-flight.
            Check(!Json.IsWellFormedObject("{\"canonical\": \"sin cerrar"),
                "una cadena sin cerrar NO es un cuerpo completo (lo que el puente respondía 200)");
            // …and the parser's repair is exactly why it needed catching.
            Eq(1, Json.ParseObject("{\"canonical\": \"sin cerrar").Count,
                "el lector permisivo la 'repara', que es el comportamiento que se conserva");

            foreach (var bad in new[]
                     {
                         "{", "}", "{\"a\":", "{\"a\":}", "{,}", "no es json",
                         "{\"a\":1", "{\"a\":1}}", "{\"a\":1},", "[1,2]", "null", "\"x\"", "1",
                         "{\"a\":[1,2}", "{\"a\" 1}", "{a:1}", "{\"a\":\"x\" \"b\":2}",
                         "{\"s\":\"\\u00zz\"}"
                     })
            {
                Check(!Json.IsWellFormedObject(bad), $"«{bad}» no es un objeto completo");
            }

            // An empty body is not malformed: most routes take no arguments.
            Check(!Json.IsWellFormedObject(""), "la cadena vacía no es un objeto…");
            Check(!Json.IsWellFormedObject("   "), "…ni un cuerpo en blanco");
        }

        /// <summary>
        /// Writes one session record through the real SessionStore, so an
        /// external auditor can read the ACL it actually applied.
        /// </summary>
        private static int AclProbe()
        {
            try
            {
                var pid = System.Diagnostics.Process.GetCurrentProcess().Id;
                var sessionId = SessionStore.NewSessionId();
                var record = new Dictionary<string, object>
                {
                    ["contract"] = SessionStore.ContractVersion,
                    ["session_id"] = sessionId,
                    ["pid"] = (double)pid,
                    ["port"] = 8781.0,
                    // A placeholder, never a real token: this file is about to
                    // be read by an auditing script and shown on a terminal.
                    ["token"] = "PLACEHOLDER-NO-ES-UN-TOKEN-REAL",
                    ["started"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture)
                };
                var path = SessionStore.Write(record, pid, sessionId);
                Console.WriteLine("sesion de prueba escrita en: " + path);
                return 0;
            }
            catch (Exception ex)
            {
                Console.WriteLine("acl-probe fallo: " + ex.Message);
                return 1;
            }
        }

        // ---------------------------------------------------------- harness

        private static void Section(string name)
        {
            Console.WriteLine();
            Console.WriteLine("== " + name);
        }

        private static void Eq(object expected, object actual, string what)
        {
            _checks++;
            var ok = expected == null ? actual == null : expected.Equals(actual);
            if (ok)
            {
                Console.WriteLine("   ok   " + what);
            }
            else
            {
                Fail($"{what}\n          esperado: {Show(expected)}\n          obtenido: {Show(actual)}");
            }
        }

        private static void Check(bool condition, string what)
        {
            _checks++;
            if (condition) Console.WriteLine("   ok   " + what);
            else Fail(what);
        }

        private static void Fail(string what)
        {
            _failures++;
            Console.WriteLine("   FALLA " + what);
        }

        private static string Show(object value)
            => value == null ? "null" : $"{value} ({value.GetType().Name})";
    }
}
