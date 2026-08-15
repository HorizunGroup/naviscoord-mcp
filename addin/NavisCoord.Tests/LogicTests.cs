using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;

namespace NavisCoord.Tests
{
    /// <summary>
    /// Assertions for the add-in logic that does not need Navisworks.
    /// </summary>
    /// <remarks>
    /// Every group here corresponds to something that was wrong, or to a
    /// guarantee the tool now makes and would otherwise only be checked by
    /// opening a licensed Navisworks with a real federation attached — which
    /// is to say, never in CI.
    /// </remarks>
    internal static class LogicTests
    {
        private static Action<string> _section;
        private static Action<object, object, string> _eq;
        private static Action<bool, string> _check;
        private static Action<string> _fail;

        public static void Run(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check,
            Action<string> fail)
        {
            _section = section;
            _eq = eq;
            _check = check;
            _fail = fail;

            RulePrecedenceTests();
            SearchOperatorTests();
            LevelNamingTests();
            RepeatSeriesTests();
            ViewPurityTests();
            MutationEnvelopeTests();
            IdempotencyTests();
            JobStateMachineTests();
            PathPolicyTests();
            SaveFormatTests();
            ClosePolicyTests();
            ExitWindowPolicyTests();
            ProfileSchemaTests();
            CanonicalFormTests();
            ProfileStoreTests();
            BodyLimitTests();
            SessionStoreTests();
            CapabilityTests();
        }

        private static void ClosePolicyTests()
        {
            _section("cierre seguro de documento y aplicación");

            foreach (var value in new[] { ClosePolicy.Save, ClosePolicy.Discard, ClosePolicy.RequireClean })
            {
                _eq(string.Empty, ClosePolicy.Problem(value, false), $"«{value}» se acepta sobre un documento limpio");
            }
            _eq(string.Empty, ClosePolicy.Problem(ClosePolicy.Save, true),
                "save permite cerrar un documento modificado después de guardarlo");
            _eq(string.Empty, ClosePolicy.Problem(ClosePolicy.Discard, true),
                "discard permite cerrar solo porque la pérdida fue explícita");
            _check(ClosePolicy.Problem(ClosePolicy.RequireClean, true).Contains("cambios sin guardar"),
                "require_clean rechaza un documento modificado");
            _check(ClosePolicy.Problem(string.Empty, false).Contains("no existe una decisión implícita"),
                "una disposición vacía se rechaza");
            _check(ClosePolicy.Problem("prompt", false).Contains("debe ser"),
                "no se acepta una política que pueda abrir un diálogo modal");
            _eq(ClosePolicy.Discard, ClosePolicy.Normalize("  DISCARD "),
                "la política se normaliza sin cambiar su significado");
            _eq(string.Empty, ClosePolicy.SaveIdempotencyKey(string.Empty),
                "sin clave no se inventa una constante compartida por todos los documentos");
            _eq("op-123:close-save", ClosePolicy.SaveIdempotencyKey("op-123"),
                "una clave explícita crea un subpaso estable para el guardado");
        }

        private static void ExitWindowPolicyTests()
        {
            _section("selección de ventana para salir");

            var api = new IntPtr(0x1234);
            var process = new IntPtr(0x5678);
            _eq(api, ExitWindowPolicy.PreferredHandle(api, process),
                "la ventana expuesta por Navisworks gana a la heurística del proceso");
            _eq(process, ExitWindowPolicy.PreferredHandle(IntPtr.Zero, process),
                "un lanzamiento interactivo conserva el handle del proceso como fallback");
            _eq(IntPtr.Zero, ExitWindowPolicy.PreferredHandle(IntPtr.Zero, IntPtr.Zero),
                "sin ninguna ventana no se inventa un destino para WM_CLOSE");
        }

        // ------------------------------------------------------ body limit

        /// <summary>
        /// The request-body cap, counted in bytes.
        /// </summary>
        /// <remarks>
        /// The multibyte cases are the whole point. The old reader counted
        /// characters off a <c>StreamReader</c>, so a body of accented text
        /// passed a byte limit it had already blown through — and the listener
        /// runs inside the user's Navisworks session, so the memory it
        /// allocates is theirs.
        /// </remarks>
        private static void BodyLimitTests()
        {
            _section("límite de cuerpo HTTP: bytes, no caracteres");

            _eq("hola", Read(Bytes("hola"), 100), "un cuerpo bajo el límite se lee entero");
            _eq(string.Empty, Read(new byte[0], 100), "un cuerpo vacío es la cadena vacía");

            var ascii = new string('a', 100);
            _eq(ascii, Read(Bytes(ascii), 100), "ASCII justo en el límite pasa");
            _check(TooLarge(Bytes(new string('a', 101)), 100), "un byte de más se rechaza");

            // 50 'ñ' = 50 characters but 100 bytes. Under a limit of 100 it
            // fits exactly; at 99 it must not. Counting characters would have
            // admitted 100 of them — 200 bytes — without noticing.
            var enye = new string('ñ', 50);
            _eq(100, Encoding.UTF8.GetByteCount(enye), "50 'ñ' ocupan 100 bytes y 50 caracteres");
            _eq(enye, Read(Bytes(enye), 100), "multibyte que cabe en bytes se acepta");
            _check(TooLarge(Bytes(enye), 99), "multibyte que NO cabe en bytes se rechaza");

            // The worst case: astral codepoints at four bytes each. Under the
            // old rule a 100-character limit admitted 400 bytes.
            var astral = string.Concat(Enumerable.Repeat("\U0001F600", 25));
            _eq(100, Encoding.UTF8.GetByteCount(astral), "25 emoji ocupan 100 bytes");
            _check(TooLarge(Bytes(astral), 99), "…y se miden por bytes, no por unidades UTF-16");

            // Content-Length is a claim, and both directions of lie are caught.
            _check(TooLargeDeclared(Bytes("x"), declared: 5000, limit: 100),
                "un Content-Length por encima del límite se rechaza antes de leer");
            _check(Unreadable(Bytes("solo esto"), declared: 9999),
                "un cuerpo que llega corto respecto de lo declarado es un error, no un payload a medias");

            // Chunked declares nothing; the running count still bounds it.
            _check(TooLargeDeclared(Bytes(new string('a', 300)), declared: 0, limit: 100),
                "sin Content-Length el conteo en curso sigue acotando");
            _eq("ok", Read(Bytes("ok"), 100), "…y un cuerpo chunked válido se lee igual");

            // Invalid UTF-8 must be a bad request, not silent U+FFFD.
            _check(Unreadable(new byte[] { 0x7B, 0x22, 0xFF, 0xFE, 0x22, 0x7D }, declared: 0),
                "un cuerpo que no es UTF-8 válido se rechaza en vez de sustituir caracteres");

            // A truncated multibyte sequence at the very end is the realistic
            // form of that: half an 'ñ' left by a cut connection.
            var cut = Bytes("dato: ñ");
            _check(Unreadable(cut.Take(cut.Length - 1).ToArray(), declared: 0),
                "una secuencia multibyte cortada por la mitad se rechaza");
        }

        private static byte[] Bytes(string value) => Encoding.UTF8.GetBytes(value);

        private static string Read(byte[] body, long limit)
            => BodyReader.Read(new MemoryStream(body), body.Length, limit);

        private static bool TooLarge(byte[] body, long limit)
        {
            try { BodyReader.Read(new MemoryStream(body), body.Length, limit); return false; }
            catch (BodyReader.TooLargeException) { return true; }
            catch { return false; }
        }

        private static bool TooLargeDeclared(byte[] body, long declared, long limit)
        {
            try { BodyReader.Read(new MemoryStream(body), declared, limit); return false; }
            catch (BodyReader.TooLargeException) { return true; }
            catch { return false; }
        }

        private static bool Unreadable(byte[] body, long declared)
        {
            try { BodyReader.Read(new MemoryStream(body), declared, 1024 * 1024); return false; }
            catch (BodyReader.UnreadableException) { return true; }
            catch { return false; }
        }

        // ---------------------------------------------------- profile store

        /// <summary>
        /// One profile per instance, pushed rather than read off the disk.
        /// </summary>
        /// <remarks>
        /// These pin the behaviour whose absence was the headline defect:
        /// <c>navis_load_profile</c> installed a profile in the MCP server and
        /// every step running inside Navisworks went on reading its own file,
        /// so a coordinator could load one set of criteria and have another
        /// applied without a word.
        /// </remarks>
        private static void ProfileStoreTests()
        {
            _section("perfil activo: una sola fuente por instancia");

            ProfileStore.Reset();
            const string profileA = @"{""$schema"":""naviscoord.profile/v1"",""name"":""perfil-A"",
                ""clash"":{""pairs"":[{""a"":""ARQ"",""b"":""EST"",""tolerance_m"":0.01}]},
                ""sets"":{""folders"":[{""folder"":""ARQ"",""sets"":[{""name"":""Muros""}]},
                                        {""folder"":""EST"",""sets"":[{""name"":""Vigas""}]}]}}";
            var canonicalA = ProfileSchema.Canonical(Json.ParseObject(profileA));
            var sumA = ProfileSchema.ChecksumOf(canonicalA);

            var loaded = ProfileStore.Load(new Dictionary<string, object>
            {
                ["canonical"] = canonicalA,
                ["checksum"] = sumA
            });
            _eq(true, loaded["ok"], "un perfil válido enviado por MCP se instala");
            _eq(sumA, loaded["checksum"], "…con el checksum que declaró el servidor");
            _eq(ProfileStore.SourceExplicit, loaded["source"], "…marcado como explícito");
            _check(!loaded.ContainsKey("path"), "…y sin ruta, porque no está en disco");
            _eq("perfil-A", ProfileStore.Active().Name, "el perfil activo es el enviado");
            _eq(sumA, ProfileStore.ActiveChecksum(), "…y su checksum es el que se reportará");

            // The declared checksum is verified against the received bytes.
            var mismatch = ProfileStore.Load(new Dictionary<string, object>
            {
                ["canonical"] = canonicalA,
                ["checksum"] = "0000000000000000"
            });
            _eq("profile_checksum_mismatch", mismatch["error"],
                "un checksum que no corresponde al contenido se rechaza");
            _eq("perfil-A", ProfileStore.Active().Name, "…y el perfil anterior sigue vigente");

            // A profile that does not validate must not displace a good one.
            var invalid = ProfileStore.Load(new Dictionary<string, object>
            {
                ["canonical"] = @"{""$schema"":""naviscoord.profile/v9"",""sets"":{""folders"":[]}}"
            });
            _eq("profile_invalid", invalid["error"], "un esquema desconocido no se instala");
            _eq("perfil-A", ProfileStore.Active().Name, "…y sigue vigente el que ya estaba");

            var unreadable = ProfileStore.Load(new Dictionary<string, object>
            {
                ["canonical"] = "no soy json"
            });
            _eq("profile_unreadable", unreadable["error"], "un cuerpo que no es JSON se rechaza");

            var empty = ProfileStore.Load(new Dictionary<string, object>());
            _eq("profile_missing", empty["error"], "una petición sin contenido se rechaza");

            // Measured in bytes, and before parsing.
            var huge = "{\"name\":\"" + new string('ñ', ProfileStore.MaxProfileBytes) + "\"}";
            var tooBig = ProfileStore.Load(new Dictionary<string, object> { ["canonical"] = huge });
            _eq("profile_too_large", tooBig["error"], "un perfil demasiado grande se rechaza por bytes");

            // Swapping profiles changes what the next step will use.
            const string profileB = @"{""$schema"":""naviscoord.profile/v1"",""name"":""perfil-B"",
                ""clash"":{""pairs"":[{""a"":""ARQ"",""b"":""EST"",""tolerance_m"":0.05}]},
                ""sets"":{""folders"":[{""folder"":""ARQ"",""sets"":[{""name"":""Muros""}]},
                                        {""folder"":""EST"",""sets"":[{""name"":""Vigas""}]}]}}";
            var canonicalB = ProfileSchema.Canonical(Json.ParseObject(profileB));
            var sumB = ProfileSchema.ChecksumOf(canonicalB);
            _check(sumA != sumB, "dos perfiles distintos tienen checksums distintos");
            ProfileStore.Load(new Dictionary<string, object>
            {
                ["canonical"] = canonicalB, ["checksum"] = sumB
            });
            _eq("perfil-B", ProfileStore.Active().Name, "cargar otro perfil reemplaza al anterior");
            _eq(sumB, ProfileStore.ActiveChecksum(), "…y el checksum vigente cambia con él");

            // profile/info answers about what is in force, and says nothing
            // secret: no token, no document path.
            var info = ProfileStore.Describe();
            _eq(true, info["ok"], "profile/info responde sobre el perfil vigente");
            _eq(sumB, info["checksum"], "…con su checksum");
            _eq(ProfileStore.SourceExplicit, info["source"], "…y de dónde salió");
            _check(info.ContainsKey("loaded_at") && info.ContainsKey("session_id"),
                "…cuándo se cargó y a qué sesión pertenece");
            var leaked = string.Join(" ", info.Keys);
            _check(!leaked.Contains("token"), "profile/info no expone ningún token");

            // Reset drops the pushed profile.
            var reset = ProfileStore.Reset();
            _eq(true, reset["had_explicit_profile"], "el reset informa que había un perfil explícito");
            _check(!ProfileStore.HasExplicit, "…y deja de haberlo");

            // A job in flight freezes the criteria: swapping mid-mutation
            // would let a job apply one profile and verify against another.
            JobManager.Reset();
            var gate = new ManualResetEventSlim(false);
            JobManager.Submit("workflow/rules", _ => { gate.Wait(3000); return Ok(); });
            WaitUntil(() => JobManager.Active() != null, 2000);
            var locked = ProfileStore.Load(new Dictionary<string, object>
            {
                ["canonical"] = canonicalA, ["checksum"] = sumA
            });
            _eq("profile_locked", locked["error"],
                "no se puede cambiar el perfil mientras una mutación está corriendo");
            var lockedReset = ProfileStore.Reset();
            _eq("profile_locked", lockedReset["error"], "…ni resetearlo");
            gate.Set();
            WaitUntil(() => JobManager.Active() == null, 3000);

            var afterJob = ProfileStore.Load(new Dictionary<string, object>
            {
                ["canonical"] = canonicalA, ["checksum"] = sumA
            });
            _eq(true, afterJob["ok"], "terminado el trabajo, el perfil vuelve a poder cambiarse");
            ProfileStore.Reset();
            JobManager.Reset();
        }

        // ----------------------------------------------- canonical form

        /// <summary>
        /// The canonical form is a contract with the Python side, so it is
        /// pinned to literals produced BY Python rather than to whatever this
        /// implementation happens to emit.
        /// </summary>
        /// <remarks>
        /// A test that asserted <c>Canonical(x) == Canonical(x)</c> would have
        /// passed happily throughout the period when the two ends disagreed
        /// completely — which is exactly what happened: both files claimed to
        /// mirror each other and neither test noticed. The expected strings
        /// below were generated by <c>naviscoord.profile.canonical_text</c>
        /// and must be regenerated there, not adjusted here, if the format
        /// ever changes.
        /// </remarks>
        private static void CanonicalFormTests()
        {
            _section("forma canónica: idéntica en Python y C#");

            // Generated by: python -c "from naviscoord.profile import
            // canonical_text, checksum_of; print(canonical_text(f)); print(checksum_of(f))"
            const string fixture = @"{
              ""name"": ""cross-check"",
              ""_comment"": ""dropped"",
              ""severity"": {""weights"": {""b"": 0.25, ""a"": 0.75},
                             ""priority_bands"": {""critical"": 75.0}},
              ""zed"": [1, 2.5, true, false, null, ""tab\tquote\""""],
              ""acento"": ""tuberia n con eñe"",
              ""tiny"": 1e-05,
              ""big"": 1e+16,
              ""third"": 0.1
            }";
            const string expected =
                "{\"acento\":\"tuberia n con e\\u00f1e\",\"big\":1e+16,\"name\":\"cross-check\"," +
                "\"severity\":{\"priority_bands\":{\"critical\":75},\"weights\":{\"a\":0.75,\"b\":0.25}}," +
                "\"third\":0.1,\"tiny\":1e-05,\"zed\":[1,2.5,true,false,null,\"tab\\tquote\\\"\"]}";

            var canonical = ProfileSchema.Canonical(Json.ParseObject(fixture));
            _eq(expected, canonical, "la forma canónica coincide carácter a carácter con la de Python");
            _eq("6f46652e0f82fe68", ProfileSchema.ChecksumOf(canonical),
                "…y por tanto el checksum es el mismo que calcula el servidor");
            _eq("6f46652e0f82fe68", ProfileSchema.Checksum(Json.ParseObject(fixture)),
                "Checksum() y ChecksumOf(Canonical()) coinciden");

            // The culture is set to es-CO in Program.Main precisely because a
            // comma decimal separator here would corrupt every checksum on a
            // Spanish machine while passing on the developer's.
            _eq("0.75", ProfileSchema.CanonicalNumber(0.75), "el separador decimal no depende de la cultura");
            _eq("75", ProfileSchema.CanonicalNumber(75.0), "un entero exacto se escribe sin parte fraccionaria");
            _eq("-3", ProfileSchema.CanonicalNumber(-3.0), "…también en negativo");
            _eq("0.1", ProfileSchema.CanonicalNumber(0.1), "0.1 usa la representación más corta que round-trips");
            _eq("1e-05", ProfileSchema.CanonicalNumber(1e-05), "el exponente va en minúscula, como en Python");
            _eq("1e+16", ProfileSchema.CanonicalNumber(1e+16), "un entero fuera del rango exacto conserva el exponente");
            _eq("null", ProfileSchema.CanonicalNumber(double.NaN), "NaN no puede escribirse en JSON");

            // Comment keys are dropped, so rewording a note must not change
            // the identity of the criteria.
            var withNote = Json.ParseObject(@"{""a"": 1, ""_nota"": ""antes""}");
            var reworded = Json.ParseObject(@"{""a"": 1, ""_nota"": ""después, y mucho más largo""}");
            _eq(ProfileSchema.Checksum(withNote), ProfileSchema.Checksum(reworded),
                "reescribir un comentario no invalida el checksum");

            // …but changing a real value must.
            var different = Json.ParseObject(@"{""a"": 2}");
            _check(ProfileSchema.Checksum(withNote) != ProfileSchema.Checksum(different),
                "cambiar un valor real sí cambia el checksum");

            // Key order in the file is not part of the identity.
            _eq(ProfileSchema.Checksum(Json.ParseObject(@"{""a"":1,""b"":2}")),
                ProfileSchema.Checksum(Json.ParseObject(@"{""b"":2,""a"":1}")),
                "el orden de las claves en el archivo no cambia la identidad");
        }

        // ------------------------------------------------- rule precedence

        private static void RulePrecedenceTests()
        {
            _section("precedencia de reglas de disciplina");

            var rules = new List<DisciplineRouter.Rule>
            {
                new DisciplineRouter.Rule
                {
                    Discipline = "EST", Order = 0,
                    Categories = Set("Structural Framing", "Structural Columns")
                },
                new DisciplineRouter.Rule
                {
                    Discipline = "RCI", Order = 1,
                    Categories = Set("Pipes", "Sprinklers"),
                    SourceFiles = Set("PROY-RCI-01.nwc")
                },
                new DisciplineRouter.Rule
                {
                    Discipline = "HID", Order = 2,
                    Categories = Set("Pipes", "Plumbing Fixtures")
                }
            };
            var router = new DisciplineRouter(rules);

            // The whole point: a per-file rule outranks a category rule, even
            // when the category rule belongs to a discipline declared first.
            var byFile = router.Resolve("PROY-RCI-01.nwc", "Structural Framing");
            _eq("RCI", byFile.Discipline, "la regla por archivo gana a la de categoría");
            _eq(DisciplineRouter.ByFile, byFile.Basis, "el motivo se reporta como archivo");

            var byCategory = router.Resolve("PROY-EST-01.nwc", "Structural Framing");
            _eq("EST", byCategory.Discipline, "sin regla de archivo decide la categoría");
            _eq(DisciplineRouter.ByCategory, byCategory.Basis, "el motivo se reporta como categoría");

            // "Pipes" is claimed by RCI (order 1) and HID (order 2).
            var contested = router.Resolve("PROY-XXX.nwc", "Pipes");
            _eq("RCI", contested.Discipline, "empate de categoría: gana el declarado primero");
            _check(contested.Ambiguous, "el empate se marca como ambiguo");
            _check(router.Conflicts.Any(c => c.Contains("Pipes")),
                "el conflicto de categoría queda registrado por nombre");

            var unmatched = router.Resolve("PROY-XXX.nwc", "Furniture");
            _check(!unmatched.Matched, "una categoría no reclamada no cae en ninguna disciplina");

            var withFallback = new DisciplineRouter(rules, "OTRO").Resolve("x.nwc", "Furniture");
            _eq("OTRO", withFallback.Discipline, "el fallback recoge lo no reclamado");
            _eq(DisciplineRouter.ByFallback, withFallback.Basis, "el motivo se reporta como fallback");

            // The regression that mattered: the old code enumerated a
            // Dictionary, so the answer depended on hash order. Feeding the
            // same rules in a different order must not change the verdict for
            // an unambiguous case, and must give the SAME winner every run for
            // an ambiguous one.
            var shuffled = new DisciplineRouter(rules.OrderByDescending(r => r.Discipline).ToList());
            _eq("EST", shuffled.Resolve("x.nwc", "Structural Framing").Discipline,
                "el resultado no depende del orden de iteración");
            for (var i = 0; i < 25; i++)
            {
                var again = new DisciplineRouter(new List<DisciplineRouter.Rule>(rules));
                if (again.Resolve("PROY-XXX.nwc", "Pipes").Discipline != "RCI")
                {
                    _fail("el desempate no fue estable entre construcciones del router");
                    break;
                }
            }
            _check(true, "el desempate es estable en 25 construcciones sucesivas");

            var fromWire = DisciplineRouter.FromSpecs(new List<object>
            {
                new Dictionary<string, object>
                {
                    ["discipline"] = "ARQ",
                    ["categories"] = new List<object> { "Walls" },
                    ["source_files"] = new List<object> { "A.nwc" }
                }
            });
            _eq("ARQ", fromWire.Resolve("A.nwc", "").Discipline, "el router se arma desde el payload");
        }

        // ------------------------------------------------ search operators

        private static void SearchOperatorTests()
        {
            _section("operadores de búsqueda");

            foreach (var op in new[] { "equals", "not_equals", "contains", "not_contains",
                                       "wildcard", "not_wildcard", "has" })
            {
                _check(SearchOperators.IsKnown(op), $"«{op}» es un operador reconocido");
            }
            _check(!SearchOperators.IsKnown("no_equals"), "un operador con typo NO se reconoce");
            _check(!SearchOperators.IsKnown("notwildcard"), "«notwildcard» no se confunde con not_wildcard");

            _check(SearchOperators.IsNegation("not_equals"), "not_equals niega");
            _check(SearchOperators.IsNegation("not_wildcard"), "not_wildcard niega");
            _check(SearchOperators.IsNegation("not_contains"), "not_contains niega");
            _check(!SearchOperators.IsNegation("equals"), "equals no niega");
            _check(!SearchOperators.IsNegation("has"), "has no niega");

            _eq("equals", SearchOperators.Normalise(""), "sin operador se asume equals");
            _eq("not_wildcard", SearchOperators.Normalise("  NOT_WILDCARD "), "se normaliza a minúsculas");

            // The bug this encodes: only 'equals' has an integer form. A
            // not_contains on a Keynote sharing the set must not veto the
            // integer retry of a CategoryId, which is how whole disciplines
            // came back with zero elements.
            var mixed = new List<Dictionary<string, object>>
            {
                new Dictionary<string, object> { ["test"] = "equals", ["value"] = "-2000011" },
                new Dictionary<string, object> { ["test"] = "not_contains", ["value"] = "ESTRUCTURA" },
                new Dictionary<string, object> { ["test"] = "not_wildcard", ["value"] = "N*" }
            };
            _check(SearchOperators.ShouldRetryAsInt(mixed),
                "un not_contains/not_wildbcard no veta el reintento entero del equals".Replace("wildbcard", "wildcard"));

            var nonNumeric = new List<Dictionary<string, object>>
            {
                new Dictionary<string, object> { ["test"] = "equals", ["value"] = "Muros" }
            };
            _check(!SearchOperators.ShouldRetryAsInt(nonNumeric),
                "un equals no numérico no se reintenta como entero");

            var onlyNegations = new List<Dictionary<string, object>>
            {
                new Dictionary<string, object> { ["test"] = "not_equals", ["value"] = "Yes" }
            };
            _check(!SearchOperators.ShouldRetryAsInt(onlyNegations),
                "un set sin equals no tiene reintento entero que hacer");
        }

        // -------------------------------------------------- level naming

        private static void LevelNamingTests()
        {
            _section("nombres de nivel");

            _eq("Nivel 05", LevelNaming.Normalise("5"), "un número suelto se rellena a dos dígitos");
            _eq("Nivel 12", LevelNaming.Normalise("12"), "dos dígitos se conservan");
            _eq("Nivel 05", LevelNaming.Normalise("  5  "), "se recorta el espacio");
            _eq("SÓTANO", LevelNaming.Normalise("SÓTANO"), "un nombre real no se reescribe");
            _eq("Nivel 1 - Acceso", LevelNaming.Normalise("Nivel 1 - Acceso"), "un nombre compuesto se respeta");
            _eq(LevelNaming.Missing, LevelNaming.Normalise(""), "vacío cae en SIN NIVEL");
            _eq(LevelNaming.Missing, LevelNaming.Normalise(null), "null cae en SIN NIVEL");
            _eq(LevelNaming.Missing, LevelNaming.Normalise("   "), "solo espacios cae en SIN NIVEL");

            // Padding exists so Navisworks' text sort puts the floors in order.
            var sorted = new[] { "10", "2", "1" }.Select(LevelNaming.Normalise)
                .OrderBy(x => x, StringComparer.OrdinalIgnoreCase).ToList();
            _eq("Nivel 01", sorted[0], "el relleno hace que el orden de texto sea el orden de pisos");
            _eq("Nivel 10", sorted[2], "…y el 10 queda después del 02");

            _eq("Muro básico 200", LevelNaming.StripInstanceId("Muro básico 200 [1234567]"),
                "se quita el id de instancia");
            _eq("Muro básico 200", LevelNaming.StripInstanceId("Muro básico 200"),
                "un nombre sin id se conserva");
            _eq("", LevelNaming.StripInstanceId(null), "null da cadena vacía");
        }

        // ------------------------------------------------- repeat series

        private static void RepeatSeriesTests()
        {
            _section("series repetidas entre niveles");

            var occurrences = new List<RepeatSeries.Occurrence>();
            // Same pair, same plan position, five storeys: one typical detail.
            for (var floor = 1; floor <= 5; floor++)
            {
                occurrences.Add(new RepeatSeries.Occurrence
                {
                    Test = "EST VS RCI",
                    ElementA = "Viga W250",
                    ElementB = "Tubo RCI 4\"",
                    X = 12.3,
                    Y = 45.6,
                    Level = LevelNaming.Normalise(floor.ToString(CultureInfo.InvariantCulture)),
                    Guid = "g" + floor
                });
            }
            // A different place on two storeys: below the threshold.
            for (var floor = 1; floor <= 2; floor++)
            {
                occurrences.Add(new RepeatSeries.Occurrence
                {
                    Test = "EST VS RCI",
                    ElementA = "Viga W250",
                    ElementB = "Tubo RCI 4\"",
                    X = 80.0,
                    Y = 10.0,
                    Level = "Nivel 0" + floor,
                    Guid = "h" + floor
                });
            }
            // The same pair twice on one storey: a fragment, not a series.
            occurrences.Add(new RepeatSeries.Occurrence
            {
                Test = "EST VS RCI", ElementA = "Viga W250", ElementB = "Tubo RCI 4\"",
                X = 12.3, Y = 45.6, Level = "Nivel 01", Guid = "frag"
            });

            var report = RepeatSeries.Detect(occurrences);
            _eq(1, report.Repeated.Count, "solo la serie de 5 niveles se reporta");
            _eq(5, report.Repeated[0].Levels.Count, "la serie cubre 5 niveles");
            _eq(1, report.Fragments, "el par duplicado dentro de un nivel cuenta como fragmento");

            // Order-independence: Navisworks does not promise which side lands
            // in Item1, so A-vs-B and B-vs-A must be one series.
            var swapped = RepeatSeries.KeyFor("T", "B", "A", 1.0, 2.0);
            var straight = RepeatSeries.KeyFor("T", "A", "B", 1.0, 2.0);
            _eq(straight, swapped, "la clave no depende del orden de los elementos");

            // Half-metre buckets: 12.3 and 12.4 are the same spot.
            _eq(RepeatSeries.KeyFor("T", "A", "B", 12.3, 0),
                RepeatSeries.KeyFor("T", "A", "B", 12.4, 0),
                "la posición en planta se agrupa a medio metro");
            _check(RepeatSeries.KeyFor("T", "A", "B", 12.3, 0) !=
                   RepeatSeries.KeyFor("T", "A", "B", 15.0, 0),
                "posiciones realmente distintas no colapsan");

            _eq(0, RepeatSeries.Detect(new List<RepeatSeries.Occurrence>()).Repeated.Count,
                "sin ocurrencias no hay series");

            // The CSV goes to Excel, so a hostile element name must be inert.
            var hostile = RepeatSeries.ToCsv(RepeatSeries.Detect(FiveFloorsNamed("=cmd|'/c calc'!A1")));
            _check(hostile.Count == 2, "una serie produce una fila de CSV");
            _check(hostile[1].Contains("'=cmd"), "el nombre con fórmula se neutraliza en el CSV");
            _check(!hostile[1].StartsWith("=") && hostile[1].IndexOf(";=", StringComparison.Ordinal) < 0,
                "ninguna celda del CSV empieza por '='");

            var negative = RepeatSeries.ToCsv(RepeatSeries.Detect(FiveFloorsNamed("Viga")));
            _check(negative[1].Contains("-3.5") && !negative[1].Contains("'-3.5"),
                "una coordenada negativa NO se neutraliza (es un número, no una fórmula)");
        }

        private static List<RepeatSeries.Occurrence> FiveFloorsNamed(string name)
        {
            var list = new List<RepeatSeries.Occurrence>();
            for (var floor = 1; floor <= 5; floor++)
            {
                list.Add(new RepeatSeries.Occurrence
                {
                    Test = "T", ElementA = name, ElementB = "Tubo",
                    X = -3.5, Y = 0.0,
                    Level = "Nivel 0" + floor, Guid = "g" + floor
                });
            }
            return list;
        }

        // --------------------------------------------------- view purity

        private static void ViewPurityTests()
        {
            _section("pureza de vistas de coordinación");

            var whitelists = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase)
            {
                ["ARQ"] = Set("-2000011", "-2000014"),   // walls, doors
                ["ELE"] = Set("-2001040", "-2008013"),   // electrical
                ["VTM"] = Set("-2008000")                // ducts
            };
            var names = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["-2000011"] = "Muros",
                ["-2001040"] = "Dispositivos eléctricos",
                ["-2008000"] = "Conductos",
                ["-2000999"] = "Ejes"
            };

            var clean = ViewPurity.Evaluate("ELE",
                new Dictionary<string, int> { ["-2001040"] = 500, ["-2008013"] = 20 },
                whitelists, names);
            _check(!clean.Dirty, "una vista con solo su disciplina está limpia");
            _eq(0, clean.IntruderElements, "sin intrusos");

            // The shape of a real finding: the same wall count visible in
            // every published MEP view — an unfiltered view template.
            var dirty = ViewPurity.Evaluate("ELE",
                new Dictionary<string, int> { ["-2001040"] = 500, ["-2000011"] = 1657 },
                whitelists, names);
            _check(dirty.Dirty, "muros dentro del modelo eléctrico = vista sucia");
            _eq(1657, dirty.IntruderElements, "se cuentan los elementos intrusos");
            _check(dirty.Intruders.Any(i => i.Item3.Contains("ARQ")),
                "se nombra la disciplina dueña del intruso");

            var tolerated = ViewPurity.Evaluate("ELE",
                new Dictionary<string, int> { ["-2001040"] = 500, ["-2000011"] = 3 },
                whitelists, names);
            _check(!tolerated.Dirty, "3 elementos ajenos quedan bajo el umbral");
            _eq(3, tolerated.IntruderElements, "…pero se siguen contando");

            var unclaimed = ViewPurity.Evaluate("ELE",
                new Dictionary<string, int> { ["-2001040"] = 500, ["-2000999"] = 40 },
                whitelists, names);
            _check(!unclaimed.Dirty, "una categoría que nadie reclama no ensucia la vista");
            _eq(40, unclaimed.UnclassifiedElements, "…se informa como sin clasificar");
            _check(unclaimed.Unclassified.Any(u => u.Item3 == "-2000999"),
                "el id se reporta para poder añadirlo al perfil");

            var alias = ViewPurity.Evaluate("AAC",
                new Dictionary<string, int> { ["-2008000"] = 900 },
                whitelists, names, code => string.Equals(code, "AAC", StringComparison.OrdinalIgnoreCase) ? "VTM" : code);
            _check(alias.Evaluated, "AAC se evalúa contra la lista blanca de VTM (misma disciplina)");
            _check(!alias.Dirty, "…y sale limpia");

            var unknown = ViewPurity.Evaluate("XYZ",
                new Dictionary<string, int> { ["-2000011"] = 10 }, whitelists, names);
            _check(!unknown.Evaluated, "una disciplina fuera del perfil no se evalúa");

            var empty = ViewPurity.Evaluate("ARQ", new Dictionary<string, int>(), whitelists, names);
            _check(!empty.Evaluated, "un modelo sin categorías legibles no se evalúa");
        }

        // ---------------------------------------------- mutation envelope

        private static void MutationEnvelopeTests()
        {
            _section("contrato uniforme de mutación");

            var full = new MutationResult("clash/group")
            {
                Requested = 10, Applied = 10, Verified = 10,
                FingerprintBefore = "abc", FingerprintAfter = "abc"
            };
            _eq("completed", full.Status(), "todo verificado = completed");

            var partial = new MutationResult("clash/group") { Requested = 10, Applied = 10, Verified = 7 };
            _eq("partial", partial.Status(), "verificado < aplicado = partial");

            var shortfall = new MutationResult("clash/group") { Requested = 10, Applied = 7, Verified = 7 };
            _eq("partial", shortfall.Status(), "aplicado < pedido = partial");

            // The rule that matters: a handler that forgot to verify cannot
            // report success. "Did you check?" answers itself.
            var unverified = new MutationResult("clash/group") { Requested = 5, Applied = 5, Verified = 0 };
            _eq("failed", unverified.Status(), "aplicado sin verificar = failed, nunca completed");

            var dry = new MutationResult("clash/group") { Requested = 5, DryRun = true };
            _eq("planned", dry.Status(), "un ensayo es 'planned', no 'completed'");
            _eq("not_applicable", dry.ToJson()["verification_source"],
                "un ensayo no declara fuente de verificación");

            var nothing = new MutationResult("clash/group");
            _eq("completed", nothing.Status(), "no pedir nada y no hacer nada es completed");

            var broken = new MutationResult("document/save");
            broken.Fail("el archivo no existe después de guardar");
            _eq("failed", broken.Status(), "un error sin nada verificado = failed");
            _eq(1, broken.Failed, "un error cuenta la unidad afectada en el envelope");
            broken.Fail("otro detalle de la misma unidad");
            _eq(1, broken.Failed, "varios detalles no inflan el conteo de unidades fallidas");

            // The fingerprint joins its parts with a separator, not with "".
            // Without one, two genuinely different documents hash the same:
            // a title of "AB" with one model produces the same material as a
            // title of "A" with a model called "B".
            var runTogether = DocumentFingerprint.Compute(new[] { "AB", "1" });
            var different = DocumentFingerprint.Compute(new[] { "A", "B1" });
            _check(runTogether != different,
                "dos documentos distintos no colisionan por concatenación de partes");
            _eq(DocumentFingerprint.Compute(new[] { "a", "b" }),
                DocumentFingerprint.Compute(new[] { "A", " B " }),
                "la huella normaliza mayúsculas y espacios");
            _eq(DocumentFingerprint.None, DocumentFingerprint.Compute(new string[0]),
                "sin partes no hay huella");
            _check(DocumentFingerprint.Matches("", "cualquiera"),
                "una expectativa vacía significa 'no pregunté', y se acepta");
            _check(!DocumentFingerprint.Matches("fp-a", "fp-b"),
                "una expectativa distinta NO coincide");

            var json = full.ToJson();
            foreach (var key in new[]
                     {
                         "operation_id", "job_id", "target_id", "document_fingerprint_before",
                         "document_fingerprint_after", "requested", "applied", "verified",
                         "failed", "status", "verification_source", "warnings", "errors"
                     })
            {
                _check(json.ContainsKey(key), $"el envelope incluye «{key}»");
            }
            _eq("document_reread", json["verification_source"],
                "la verificación declara que vino de releer el documento");
            _check(!string.IsNullOrEmpty((string)json["operation_id"]),
                "cada mutación lleva su propio operation_id");

            var a = new MutationResult("x").OperationId;
            var b = new MutationResult("x").OperationId;
            _check(a != b, "dos mutaciones no comparten operation_id");
        }

        // ------------------------------------------------------ idempotency

        private static void IdempotencyTests()
        {
            _section("idempotencia");

            IdempotencyLedger.Clear();
            _check(!IdempotencyLedger.TryGet("k1", out _), "una clave nueva no tiene resultado guardado");

            var original = new Dictionary<string, object> { ["status"] = "completed", ["applied"] = 30.0 };
            IdempotencyLedger.Remember("k1", original);

            _check(IdempotencyLedger.TryGet("k1", out var replay), "la clave repetida devuelve lo guardado");
            _eq(30.0, replay["applied"], "los conteos originales se conservan");
            _check(replay.ContainsKey("idempotent_replay"), "la respuesta se marca como repetición");
            _check(replay.ContainsKey("note"), "…y explica por qué no se volvió a tocar el documento");

            var scopedA = new Dictionary<string, object>
            {
                ["operation"] = "document/save",
                ["document_fingerprint_before"] = "fp-a",
                ["status"] = "completed"
            };
            IdempotencyLedger.Remember("same-key", scopedA);
            _check(IdempotencyLedger.TryGet("same-key", "document/save", "fp-a", out _),
                "la misma operación sobre el mismo documento sí se reproduce");
            _check(!IdempotencyLedger.TryGet("same-key", "document/save_as", "fp-a", out _),
                "la misma clave no cruza a otra operación");
            _check(!IdempotencyLedger.TryGet("same-key", "document/save", "fp-b", out _),
                "la misma clave no cruza a otro documento");

            // Mutating the replay must not corrupt the stored entry.
            replay["applied"] = 999.0;
            IdempotencyLedger.TryGet("k1", out var again);
            _eq(30.0, again["applied"], "el registro guardado es inmune a mutaciones del llamador");

            _check(!IdempotencyLedger.TryGet("", out _), "una clave vacía nunca acierta");
            var beforeEmpty = IdempotencyLedger.Count;
            IdempotencyLedger.Remember("", original);
            _eq(beforeEmpty, IdempotencyLedger.Count, "una clave vacía no se guarda");

            for (var i = 0; i < 400; i++)
            {
                IdempotencyLedger.Remember("bulk-" + i, original);
            }
            _check(IdempotencyLedger.Count <= 256, "el registro está acotado y no crece sin límite");
            IdempotencyLedger.Clear();
        }

        // ------------------------------------------------ job state machine

        private static void JobStateMachineTests()
        {
            _section("máquina de estados de trabajos");

            JobManager.Reset();

            var done = new ManualResetEventSlim(false);
            var job = JobManager.Submit("workflow/run", j =>
            {
                j.Progress("corriendo", 1, 3, "test 1");
                done.Wait(2000);
                return new Dictionary<string, object>
                {
                    ["status"] = "completed",
                    ["requested"] = 3.0, ["applied"] = 3.0, ["verified"] = 3.0
                };
            }, fingerprint: "fp-1");

            _check(job.State == JobManager.Queued || job.State == JobManager.Running,
                "un trabajo recién enviado está en cola o corriendo");
            _check(JobManager.Get(job.Id) != null, "el trabajo se encuentra por id");
            _check(JobManager.Get("no-existe") == null, "un id inexistente devuelve null");

            // While it runs, a second exclusive mutation on the same document
            // must be refused rather than interleaved.
            WaitUntil(() => JobManager.Active() != null, 2000);
            _check(JobManager.WouldCollide("fp-1", out var reason),
                "una segunda mutación sobre el mismo documento colisiona");
            _check(reason != null && reason.Contains(job.Id), "el rechazo nombra el trabajo en curso");
            _check(!JobManager.WouldCollide("otro-documento", out _),
                "una mutación sobre OTRO documento no colisiona");

            // Cancelling something atomic must say so instead of pretending.
            // This job was submitted without `cancellable`, so nothing in its
            // code path will ever read a cancel flag — and the old reply set
            // one anyway and reported `cancel_requested`, which reads as "it
            // will stop shortly" for work that cannot stop at all.
            var cancelRunning = JobManager.Cancel(job.Id);
            _eq(false, cancelRunning["cancelled"], "un trabajo atómico en curso no se cancela");
            _eq(JobManager.CannotCancelRunning, cancelRunning["outcome"],
                "…y se dice con un código que el llamador puede leer");
            _check(!cancelRunning.ContainsKey("cancel_requested"),
                "…sin registrar una solicitud que nadie va a atender");
            _check(!job.CancelRequested,
                "…y sin marcar el trabajo, que seguiría corriendo igual");

            done.Set();
            WaitUntil(() => job.FinishedUtc.HasValue, 3000);
            _eq(3, job.Requested, "los conteos del trabajo vienen del envelope");
            _eq(3, job.Verified, "…incluido lo verificado");
            _eq(JobManager.Completed, job.State,
                "un atómico que terminó reporta completed: el cancel no alteró su resultado");

            // Cancelling a job that already finished must not rewrite it.
            var cancelDone = JobManager.Cancel(job.Id);
            _eq(false, cancelDone["cancelled"], "cancelar algo terminado no lo cancela");
            _eq(JobManager.AlreadyFinished, cancelDone["outcome"], "…y lo dice con su propio código");
            _eq(JobManager.Completed, job.State, "…y el estado final no cambia");

            // A queued job HAS touched nothing, so cancelling it is honest.
            JobManager.Reset();
            var blocker = new ManualResetEventSlim(false);
            var first = JobManager.Submit("a", _ => { blocker.Wait(3000); return Ok(); });
            var second = JobManager.Submit("b", _ => Ok());
            WaitUntil(() => JobManager.Active() != null, 2000);
            var cancelQueued = JobManager.Cancel(second.Id);
            _eq(true, cancelQueued["cancelled"], "un trabajo aún en cola sí se cancela");
            _eq(JobManager.CancelledBeforeStart, cancelQueued["outcome"], "…con su propio veredicto");
            _eq(JobManager.Cancelled, second.State, "…y queda en estado cancelled");
            _eq(0, second.Applied, "…sin nada aplicado");
            _eq(0, second.Verified, "…ni verificado");
            // Repeating a cancel must be idempotent, not a second answer.
            var again = JobManager.Cancel(second.Id);
            _eq(JobManager.AlreadyFinished, again["outcome"], "repetir el cancel no cambia el veredicto");
            _eq(JobManager.Cancelled, second.State, "…ni el estado");
            blocker.Set();
            WaitUntil(() => first.FinishedUtc.HasValue, 4000);

            CooperativeCancelTests();

            // A failing job reports the failure, not a silent success.
            JobManager.Reset();
            var boom = JobManager.Submit("boom", _ => throw new InvalidOperationException("revienta"));
            WaitUntil(() => boom.FinishedUtc.HasValue, 3000);
            _eq(JobManager.Failed, boom.State, "una excepción deja el trabajo en failed");
            _check(boom.Error != null && Convert.ToString(boom.Error["detail"]).Contains("revienta"),
                "el error del trabajo lleva el mensaje real");

            // A partial envelope produces a partial job.
            JobManager.Reset();
            var half = JobManager.Submit("half", _ => new Dictionary<string, object>
            {
                ["status"] = "partial", ["requested"] = 10.0, ["applied"] = 10.0, ["verified"] = 4.0
            });
            WaitUntil(() => half.FinishedUtc.HasValue, 3000);
            _eq(JobManager.Partial, half.State, "un envelope 'partial' deja el trabajo en partial");

            // Progress must never be invented.
            JobManager.Reset();
            var indeterminate = JobManager.Submit("atomic", j => { j.Phasing("corriendo"); return Ok(); });
            var shape = (Dictionary<string, object>)indeterminate.ToJson()["progress"];
            _eq("indeterminate", shape["kind"], "un paso atómico reporta progreso indeterminado");
            _check(!shape.ContainsKey("percent"), "…y NO inventa un porcentaje");

            var counted = new JobManager.Job();
            counted.Progress("moviendo", 25, 100);
            var bar = (Dictionary<string, object>)counted.ToJson()["progress"];
            _eq("units", bar["kind"], "un paso con unidades reporta unidades");
            _eq(25.0, bar["percent"], "…y el porcentaje sale de las unidades reales");

            // ToJson runs on the listener thread while Progress writes from
            // the job thread, so a torn read can pair a stale count with a new
            // total. Whatever it reads, the percentage must stay a percentage.
            var torn = new JobManager.Job();
            torn.Progress("moviendo", 130, 100);
            var clamped = (Dictionary<string, object>)torn.ToJson()["progress"];
            _eq(100.0, clamped["percent"], "un porcentaje nunca se reporta por encima de 100");
            torn.Progress("moviendo", -5, 100);
            clamped = (Dictionary<string, object>)torn.ToJson()["progress"];
            _eq(0.0, clamped["percent"], "…ni por debajo de 0");

            // A retry that arrives WHILE the work runs must get the same job
            // back. The ledger only records finished results, so before this
            // the retry produced a second submission.
            JobManager.Reset();
            var hold = new ManualResetEventSlim(false);
            var original = JobManager.Submit("workflow/run", _ => { hold.Wait(2000); return Ok(); },
                idempotencyKey: "clave-repetida");
            WaitUntil(() => JobManager.Active() != null, 2000);

            var found = JobManager.FindByIdempotencyKey("clave-repetida");
            _check(found != null && found.Id == original.Id,
                "una idempotency_key en vuelo devuelve EL MISMO trabajo, no uno nuevo");
            _check(JobManager.FindByIdempotencyKey("otra-clave") == null,
                "una clave distinta no acierta");
            _check(JobManager.FindByIdempotencyKey("") == null,
                "una clave vacía nunca acierta");
            _check(JobManager.FindByIdempotencyKey(
                    "clave-repetida", "workflow/configure", "fp-1") == null,
                "una clave en vuelo no cruza a otra operación");
            _check(JobManager.FindByIdempotencyKey(
                    "clave-repetida", "workflow/run", "otro-documento") == null,
                "una clave en vuelo no cruza a otro documento");

            hold.Set();
            WaitUntil(() => original.FinishedUtc.HasValue, 3000);
            _check(JobManager.FindByIdempotencyKey("clave-repetida") == null,
                "terminado el trabajo, la clave ya no está 'en vuelo': su resultado vive en el ledger");

            var listed = JobManager.All();
            _check(listed.Count >= 1, "job/list devuelve los trabajos conocidos");
            _check(JobManager.Cancel("fantasma").ContainsKey("error"),
                "cancelar un id inexistente devuelve un error explícito");

            JobManager.Reset();
        }

        private static Dictionary<string, object> Ok()
            => new Dictionary<string, object> { ["status"] = "completed" };

        /// <summary>
        /// The cancellation contract for work that CAN stop once started.
        /// </summary>
        /// <remarks>
        /// The distinction these pin down is the one the old code got wrong:
        /// a cooperative job that stopped after verifying some of its work
        /// reported `cancelled`, because the rule was "cancel requested and
        /// units not finished". Every caller reads `cancelled` as "the
        /// document was not touched" — and the applied renames were still
        /// there. Stopping short with mutations behind you is `partial`; only
        /// stopping short having changed nothing is `cancelled`.
        /// </remarks>
        private static void CooperativeCancelTests()
        {
            _section("cancelación cooperativa: cancelled vs partial");

            // Stopped before applying anything: nothing to report but the stop.
            JobManager.Reset();
            var gate = new ManualResetEventSlim(false);
            var untouched = JobManager.Submit("workflow/group_levels", j =>
            {
                j.Progress("agrupando", 0, 10);
                gate.Wait(3000);
                return new Dictionary<string, object>
                {
                    ["status"] = "completed",
                    ["requested"] = 10.0, ["applied"] = 0.0, ["verified"] = 0.0
                };
            }, cancellable: true);
            WaitUntil(() => JobManager.Active() != null, 2000);

            var coop = JobManager.Cancel(untouched.Id);
            _eq(false, coop["cancelled"], "una cancelación cooperativa no es inmediata");
            _eq(JobManager.CancelRequested_, coop["outcome"], "…se registra la solicitud");
            _check(untouched.CancelRequested, "…y el trabajo la ve");
            gate.Set();
            WaitUntil(() => untouched.FinishedUtc.HasValue, 3000);
            _eq(JobManager.Cancelled, untouched.State,
                "parado sin haber aplicado nada: cancelled");

            // Stopped AFTER verifying part of the work: that is partial. This
            // is the case the old rule mislabelled.
            JobManager.Reset();
            var gate2 = new ManualResetEventSlim(false);
            var halfDone = JobManager.Submit("workflow/group_levels", j =>
            {
                j.Progress("agrupando", 40, 100);
                gate2.Wait(3000);
                return new Dictionary<string, object>
                {
                    ["status"] = "completed",
                    ["requested"] = 100.0, ["applied"] = 40.0, ["verified"] = 40.0
                };
            }, cancellable: true);
            WaitUntil(() => JobManager.Active() != null, 2000);
            JobManager.Cancel(halfDone.Id);
            gate2.Set();
            WaitUntil(() => halfDone.FinishedUtc.HasValue, 3000);
            _eq(JobManager.Partial, halfDone.State,
                "parado con 40 de 100 ya verificados: partial, NO cancelled");
            _eq(40, halfDone.Verified, "…y el resultado dice cuántos quedaron aplicados");
            _eq(100, halfDone.Requested, "…de cuántos se pidieron");

            // A cancel that lands as the last unit completes must not rewrite
            // a finished job into a partial one.
            JobManager.Reset();
            var finished = JobManager.Submit("workflow/group_levels", j =>
            {
                j.Progress("agrupando", 10, 10);
                return new Dictionary<string, object>
                {
                    ["status"] = "completed",
                    ["requested"] = 10.0, ["applied"] = 10.0, ["verified"] = 10.0
                };
            }, cancellable: true);
            WaitUntil(() => finished.FinishedUtc.HasValue, 3000);
            finished.CancelRequested = true;   // the losing side of the race
            _eq(JobManager.Completed, finished.State,
                "un trabajo que alcanzó a terminar todo sigue siendo completed");

            // A failure stays a failure: a pending cancel must not soften it.
            JobManager.Reset();
            var broke = JobManager.Submit("workflow/group_levels", j =>
            {
                j.Progress("agrupando", 2, 10);
                j.CancelRequested = true;
                return new Dictionary<string, object>
                {
                    ["status"] = "failed",
                    ["requested"] = 10.0, ["applied"] = 0.0, ["verified"] = 0.0
                };
            }, cancellable: true);
            WaitUntil(() => broke.FinishedUtc.HasValue, 3000);
            _eq(JobManager.Failed, broke.State, "un fallo con cancel pendiente sigue siendo failed");

            // The profile is frozen at submit, so a result can be traced to
            // the criteria that produced it.
            JobManager.Reset();
            var stamped = JobManager.Submit("workflow/rules", _ => Ok(), profileChecksum: "abc123def456");
            WaitUntil(() => stamped.FinishedUtc.HasValue, 3000);
            _eq("abc123def456", stamped.ToJson()["profile_checksum"],
                "el trabajo recuerda con qué perfil se creó");
            _eq(false, stamped.ToJson()["cancellable"],
                "…y declara si se podía cancelar una vez iniciado");
        }

        private static void WaitUntil(Func<bool> condition, int timeoutMs)
        {
            var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (DateTime.UtcNow < deadline)
            {
                if (condition()) return;
                Thread.Sleep(15);
            }
        }

        // -------------------------------------------------------- path policy

        private static void PathPolicyTests()
        {
            _section("política de rutas de salida");

            var root = Path.Combine(Path.GetTempPath(), "naviscoord-tests-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            Directory.CreateDirectory(root);
            try
            {
                var policy = new PathPolicy(new[] { root });

                var inside = policy.ResolveFile(Path.Combine(root, "informe.nwf"), false);
                _check(inside.Allowed, "un archivo dentro de la raíz se permite");

                var relative = policy.ResolveFile("informe.nwf", false);
                _check(relative.Allowed, "una ruta relativa cae en la primera raíz");
                _check(relative.Path.StartsWith(root, StringComparison.OrdinalIgnoreCase),
                    "…y no en el directorio de trabajo");

                var traversal = policy.ResolveFile(Path.Combine(root, "..", "..", "fuera.nwf"), false);
                _check(!traversal.Allowed, "«..» no puede salir de la raíz");
                _check(traversal.Reason.Contains("fuera de las rutas"), "…y el motivo lo dice");

                var elsewhere = policy.ResolveFile(@"C:\Windows\Temp\x.nwf", false);
                _check(!elsewhere.Allowed, "una ruta absoluta no autorizada se rechaza");

                foreach (var hostile in new[]
                         {
                             @"\\atacante\share\loot.nwf",
                             @"\\.\PhysicalDrive0",
                             @"\\?\C:\Windows\x.nwf",
                             "//atacante/share/loot.nwf"
                         })
                {
                    var decision = policy.ResolveFile(hostile, false);
                    _check(!decision.Allowed, $"«{hostile}» se rechaza");
                    _check(decision.Reason.Contains("red") || decision.Reason.Contains("dispositivo"),
                        $"…nombrando que es UNC o dispositivo");
                }

                var device = policy.ResolveFile(Path.Combine(root, "NUL.nwf"), false);
                _check(!device.Allowed, "un nombre de dispositivo reservado se rechaza");

                var ads = policy.ResolveFile(Path.Combine(root, "informe.nwf") + ":oculto", false);
                _check(!ads.Allowed, "un flujo alterno de datos (ADS) se rechaza");

                var target = Path.Combine(root, "existe.nwf");
                File.WriteAllText(target, "x");
                var refused = policy.ResolveFile(target, false);
                _check(!refused.Allowed, "no se sobrescribe sin pedirlo");
                _check(refused.Hint.Contains("overwrite=true"), "…y se dice cómo pedirlo");

                var allowed = policy.ResolveFile(target, true);
                _check(allowed.Allowed && allowed.Existed, "con overwrite=true sí se permite");

                var asFolder = policy.ResolveFile(root, false);
                _check(!asFolder.Allowed, "una carpeta existente no vale como archivo destino");

                _check(!policy.ResolveFile("", false).Allowed, "una ruta vacía se rechaza");
                _check(!policy.ResolveFile("archivo\0.nwf", false).Allowed, "un byte nulo se rechaza");

                _check(PathPolicy.IsWithin(Path.Combine(root, "a", "b.nwf"), root),
                    "IsWithin acepta un descendiente");
                _check(!PathPolicy.IsWithin(root + "-vecino", root),
                    "IsWithin NO acepta una carpeta hermana con prefijo común");

                _check(PathPolicy.Default().Roots.Count >= 1, "siempre hay al menos una raíz por defecto");

                ReparsePointTests(root, policy);
            }
            finally
            {
                try { Directory.Delete(root, true); } catch { /* best effort */ }
            }
        }

        /// <summary>
        /// A path inside the root by name can still land outside it.
        /// </summary>
        /// <remarks>
        /// The policy used to answer "does this string start with the root",
        /// which is not the question. Creating a junction needs no elevation:
        /// anyone who can write inside an allowed root can point a folder in
        /// it at C:\Windows and every check still passed, while the Python end
        /// — which resolves paths for real — refused the same path. Two ends
        /// of one policy disagreeing about what is allowed is the worst way
        /// for it to be wrong.
        ///
        /// Everything here lives under %TEMP% and is removed by literal path.
        /// </remarks>
        private static void ReparsePointTests(string root, PathPolicy policy)
        {
            var outside = Path.Combine(Path.GetTempPath(), "naviscoord-fuera-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            var escapeLink = Path.Combine(root, "escapa");
            var innerReal = Path.Combine(root, "real");
            var innerLink = Path.Combine(root, "dentro");
            Directory.CreateDirectory(outside);
            Directory.CreateDirectory(innerReal);

            try
            {
                if (!MakeJunction(escapeLink, outside))
                {
                    _check(true, "(el sistema no permitió crear junctions: caso omitido)");
                    return;
                }

                // THE defect: textually inside, actually outside.
                var escaped = policy.ResolveFile(Path.Combine(escapeLink, "robado.nwf"), false);
                _check(!escaped.Allowed,
                    "una junction dentro de la raíz que apunta fuera NO se permite");
                _check(escaped.Reason != null && escaped.Reason.Contains("enlace"),
                    "…y el motivo dice que hay un enlace de por medio");
                _check(PathPolicy.IsWithin(Path.Combine(escapeLink, "robado.nwf"), root),
                    "…aunque textualmente sí esté dentro, que es justo lo que se escapaba");

                // A junction that stays inside is legitimate and must work: a
                // policy that refused every reparse point would break
                // redirected folders and OneDrive mounts.
                if (MakeJunction(innerLink, innerReal))
                {
                    var stayed = policy.ResolveFile(Path.Combine(innerLink, "informe.nwf"), false);
                    _check(stayed.Allowed,
                        "una junction que se queda dentro de la raíz sí se permite");
                }

                // The file usually does not exist yet, and neither may the
                // last folder or two: resolution starts at the nearest
                // existing ancestor.
                var deep = policy.ResolveFile(
                    Path.Combine(root, "aun", "no", "existe", "informe.nwf"), false);
                _check(deep.Allowed, "un destino cuyo ancestro aún no existe se permite");

                var deepEscape = policy.ResolveFile(
                    Path.Combine(escapeLink, "aun", "no", "existe", "informe.nwf"), false);
                _check(!deepEscape.Allowed,
                    "…pero si el ancestro existente es una junction que sale, se rechaza");

                // The root itself may legitimately be a link — a redirected
                // Documents folder, a OneDrive mount — and resolving both
                // sides is what keeps that working.
                var linkedRootPolicy = new PathPolicy(new[] { innerLink });
                if (Directory.Exists(innerLink))
                {
                    var viaLinkedRoot = linkedRootPolicy.ResolveFile(
                        Path.Combine(innerLink, "informe.nwf"), false);
                    _check(viaLinkedRoot.Allowed,
                        "una raíz autorizada que es ella misma un enlace sigue siendo utilizable");

                    // Reaching that same directory by its OTHER name is
                    // refused, and deliberately so. Resolution runs only
                    // after textual containment, never instead of it: making
                    // the resolved forms the primary test would accept every
                    // alias of an authorised root, which is a wider door than
                    // this phase set out to leave open. The caller is told
                    // which roots are allowed and can name one.
                    var viaRealName = linkedRootPolicy.ResolveFile(
                        Path.Combine(innerReal, "informe.nwf"), false);
                    _check(!viaRealName.Allowed,
                        "llegar a la misma carpeta por otro nombre no autorizado se rechaza " +
                        "(la resolución endurece, no amplía)");
                    _check(viaRealName.Hint != null && viaRealName.Hint.Contains("Permitidas"),
                        "…diciendo qué raíces sí valen");
                }

                // Case differences are not a way out either.
                var upper = new PathPolicy(new[] { root.ToUpperInvariant() });
                _check(upper.ResolveFile(Path.Combine(root, "informe.nwf"), false).Allowed,
                    "una raíz escrita en otra caja sigue conteniendo sus descendientes");
                _check(!upper.ResolveFile(Path.Combine(escapeLink, "robado.nwf"), false).Allowed,
                    "…y la junction sigue sin escapar por cambiar la caja");
            }
            finally
            {
                // Junctions are removed WITHOUT recursion: deleting one
                // recursively would walk into the target and take the real
                // folder with it.
                foreach (var link in new[] { escapeLink, innerLink })
                {
                    try { if (Directory.Exists(link)) Directory.Delete(link, false); } catch { }
                }
                try { Directory.Delete(outside, true); } catch { }
            }
        }

        private static bool MakeJunction(string link, string target)
        {
            try
            {
                var process = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = "cmd.exe",
                    Arguments = "/c mklink /J \"" + link + "\" \"" + target + "\"",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                });
                process.WaitForExit(10000);
                return process.ExitCode == 0 && Directory.Exists(link);
            }
            catch
            {
                return false;
            }
        }

        // ------------------------------------------------------ save policy

        private static void SaveFormatTests()
        {
            _section("formatos de guardado");

            _check(SaveFormats.Reject(@"C:\obra\coordinacion.nwf") == null, ".nwf se acepta");
            _check(SaveFormats.Reject(@"C:\obra\publicado.nwd") == null, ".nwd se acepta");

            var cloud = SaveFormats.Reject(@"C:\obra\coordinacion.nwfacc");
            _check(cloud != null, ".nwfacc se rechaza");
            _check(cloud.Contains("ACC") && cloud.Contains(".nwf"),
                "…explicando que viene de ACC y qué hacer en su lugar");

            var wrong = SaveFormats.Reject(@"C:\obra\modelo.rvt");
            _check(wrong != null && wrong.Contains("no soportado"), "un formato ajeno se rechaza");

            _check(SaveFormats.Reject(@"C:\obra\sinextension") != null, "sin extensión se rechaza");
            _check(SaveFormats.Reject("") != null, "vacío se rechaza");
            _check(SaveFormats.IsCloud("x.NWFACC"), "la detección de nube no distingue mayúsculas");
            _check(SaveFormats.IsWritable(".NWF"), "la extensión aceptada no distingue mayúsculas");
        }

        // --------------------------------------------------- profile schema

        private static void ProfileSchemaTests()
        {
            _section("esquema de perfil");

            var valid = Json.ParseObject(@"{
              ""$schema"": ""naviscoord.profile/v1"",
              ""name"": ""corporativo"",
              ""sets"": {""folders"": [
                {""folder"": ""ARQ"", ""scope_model_contains"": ""-ARQ-"", ""sets"": [
                  {""name"": ""ARQ Muros"", ""conditions"": [
                    {""tab"": ""Properties"", ""property"": ""CategoryId"", ""test"": ""equals"", ""value"": ""-2000011""}]}]}]},
              ""clash"": {""pairs"": [{""a"": ""ARQ"", ""b"": ""ARQ"", ""type"": ""Hard"", ""tolerance_m"": 0.01}]}
            }");
            var ok = ProfileSchema.Validate(valid);
            _check(ok.Ok, "un perfil bien formado valida");
            _check(ok.Sections.Contains("sets") && ok.Sections.Contains("clash"),
                "se listan las secciones presentes");
            _check(!string.IsNullOrEmpty(ok.Checksum), "el perfil produce un checksum");

            // The defect the first live smoke test found: the corporate
            // profile deployed in the field predates the format being
            // versioned, so it has no $schema. Rejecting it meant upgrading
            // the add-in broke "Configurar" on every installed machine,
            // against a profile that was otherwise entirely valid.
            var noSchema = Json.ParseObject(@"{
              ""name"": ""corporativo-heredado"",
              ""sets"": {""folders"": [{""folder"": ""ARQ"", ""sets"": [{""name"": ""ARQ Muros""}]}]}}");
            var undeclared = ProfileSchema.Validate(noSchema);
            _check(undeclared.Ok, "un perfil SIN $schema sigue siendo valido (migracion, no rechazo)");
            _eq(ProfileSchemaVersion.Legacy, undeclared.Version,
                "se lee como el formato anterior a versionar");
            _check(undeclared.Warnings.Any(w => w.Contains("$schema")),
                "…con un aviso que dice como fijarlo explicitamente");
            _check(!undeclared.Errors.Any(), "y sin ningun error");


            var badVersion = Json.ParseObject(@"{""$schema"": ""naviscoord.profile/v9"", ""sets"": {""folders"": []}}");
            var bad = ProfileSchema.Validate(badVersion);
            _check(!bad.Ok, "una versión no soportada no valida");
            _check(bad.Errors.Any(e => e.Contains("naviscoord.profile/v1")),
                "…y dice qué versiones lee");

            var legacy = Json.ParseObject(@"{""$schema"": ""naviscoord.profile/1"", ""severity"": {""weights"": {""a"": 1.0}}}");
            var old = ProfileSchema.Validate(legacy);
            _check(old.Ok, "la versión anterior sigue siendo compatible");
            _check(old.Warnings.Any(w => w.Contains("naviscoord.profile/v1")),
                "…con un aviso de migración");

            var unknownSection = Json.ParseObject(
                @"{""$schema"": ""naviscoord.profile/v1"", ""setss"": {}, ""sets"": {""folders"": [{""folder"":""A"",""sets"":[]}]}}");
            var typo = ProfileSchema.Validate(unknownSection);
            _check(typo.Warnings.Any(w => w.Contains("setss")), "una sección con typo se reporta");

            var badOperator = Json.ParseObject(@"{
              ""$schema"": ""naviscoord.profile/v1"",
              ""sets"": {""folders"": [{""folder"": ""A"", ""sets"": [
                {""name"": ""s"", ""conditions"": [{""property"": ""X"", ""test"": ""no_equals"", ""value"": ""1""}]}]}]}}");
            var opProblem = ProfileSchema.Validate(badOperator);
            _check(!opProblem.Ok, "un operador inválido invalida el perfil");
            _check(opProblem.Errors.Any(e => e.Contains("not_equals")),
                "…listando los operadores válidos");

            var collision = Json.ParseObject(@"{
              ""$schema"": ""naviscoord.profile/v1"",
              ""sets"": {""folders"": [{""folder"": ""ARQ"", ""sets"": [{""name"": ""s""}]}]},
              ""clash"": {""pairs"": [
                {""a"": ""ARQ"", ""b"": ""ARQ""},
                {""a"": ""ARQ"", ""b"": ""ARQ""}]}}");
            var dup = ProfileSchema.Validate(collision);
            _check(!dup.Ok, "dos pares con el mismo nombre de test no validan");
            _check(dup.Errors.Any(e => e.Contains("mismo nombre")), "…diciendo exactamente eso");

            var missingFolder = Json.ParseObject(@"{
              ""$schema"": ""naviscoord.profile/v1"",
              ""sets"": {""folders"": [{""folder"": ""ARQ"", ""sets"": [{""name"": ""s""}]}]},
              ""clash"": {""pairs"": [{""a"": ""ARQ"", ""b"": ""FANTASMA""}]}}");
            _check(ProfileSchema.Validate(missingFolder).Errors.Any(e => e.Contains("FANTASMA")),
                "un par que apunta a una carpeta inexistente se detecta");

            var weights = Json.ParseObject(
                @"{""$schema"": ""naviscoord.profile/v1"", ""severity"": {""weights"": {""a"": 0.5, ""b"": 0.2}}}");
            _check(ProfileSchema.Validate(weights).Errors.Any(e => e.Contains("1.00")),
                "unos pesos que no suman 1.00 se detectan");

            var mm = Json.ParseObject(@"{
              ""$schema"": ""naviscoord.profile/v1"",
              ""sets"": {""folders"": [{""folder"": ""A"", ""sets"": [{""name"": ""s""}]}]},
              ""clash"": {""pairs"": [{""a"": ""A"", ""b"": ""A"", ""tolerance_m"": 10}]}}");
            _check(ProfileSchema.Validate(mm).Warnings.Any(w => w.Contains("METROS")),
                "una tolerancia sospechosa de estar en mm se avisa");

            _check(!ProfileSchema.Validate(new Dictionary<string, object>()).Ok, "un perfil vacío no valida");

            // The checksum is identity, so a reworded comment must not move it
            // and a real criterion change must.
            var a = Json.ParseObject(@"{""$schema"": ""naviscoord.profile/v1"", ""_nota"": ""uno"", ""severity"": {""weights"": {""x"": 1.0}}}");
            var b = Json.ParseObject(@"{""$schema"": ""naviscoord.profile/v1"", ""_nota"": ""otra cosa"", ""severity"": {""weights"": {""x"": 1.0}}}");
            _eq(ProfileSchema.Checksum(a), ProfileSchema.Checksum(b),
                "reescribir un comentario NO cambia el checksum");
            var c = Json.ParseObject(@"{""$schema"": ""naviscoord.profile/v1"", ""severity"": {""weights"": {""x"": 0.9}}}");
            _check(ProfileSchema.Checksum(a) != ProfileSchema.Checksum(c),
                "cambiar un peso SÍ cambia el checksum");
        }

        // --------------------------------------------------- session store

        private static void SessionStoreTests()
        {
            _section("registro de sesiones multiinstancia");

            var sandbox = Path.Combine(Path.GetTempPath(), "naviscoord-sess-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            var previous = Environment.GetEnvironmentVariable("LOCALAPPDATA");
            Environment.SetEnvironmentVariable("LOCALAPPDATA", sandbox);
            try
            {
                Directory.CreateDirectory(sandbox);

                // HARD GUARD, not an assertion. An earlier build resolved the
                // runtime root through SpecialFolder.LocalApplicationData,
                // which ignores this environment variable — so this suite
                // wrote fixture sessions into the developer's REAL
                // %LOCALAPPDATA%\NavisCoord, including session.json, the
                // pointer a live bridge reads. Checking and continuing is not
                // enough: if the isolation ever breaks again, the test must
                // refuse to run rather than corrupt a real runtime.
                if (!SessionStore.Root().StartsWith(sandbox, StringComparison.OrdinalIgnoreCase))
                {
                    _fail("ABORTADO: SessionStore.Root() no respeta LOCALAPPDATA (" +
                          SessionStore.Root() + "). Las pruebas escribirían en el runtime real.");
                    return;
                }
                _check(true, "la raíz del registro cuelga de LOCALAPPDATA (aislamiento verificado)");

                var me = System.Diagnostics.Process.GetCurrentProcess();
                var stamp = me.StartTime.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture);

                var first = Record(me.Id, "aaaa1111", 8781, stamp);
                var second = Record(me.Id, "bbbb2222", 8782, stamp);
                SessionStore.Write(first, me.Id, "aaaa1111");
                SessionStore.Write(second, me.Id, "bbbb2222");

                var listed = SessionStore.List();
                _eq(2, listed.Count, "dos instancias simultáneas producen dos entradas");
                _check(listed.All(s => s.ContainsKey("session_file")), "cada entrada sabe de qué archivo salió");
                _check(listed.Select(s => Json.Num(s, "port", 0)).Distinct().Count() == 2,
                    "cada instancia registra su propio puerto");

                // The bug: closing one instance used to delete the other's
                // handshake. It must remove ONLY its own entry.
                SessionStore.Remove(me.Id, "aaaa1111");
                var survivors = SessionStore.List();
                _eq(1, survivors.Count, "cerrar una instancia no borra la sesión de la otra");
                _eq("bbbb2222", Json.Str(survivors[0], "session_id"), "la que sobrevive es la correcta");

                // The legacy pointer must survive too, repointed at a live one.
                _check(File.Exists(SessionStore.LegacyPath()),
                    "el session.json de compatibilidad sigue existiendo");
                var pointer = Json.ParseObject(File.ReadAllText(SessionStore.LegacyPath()));
                _eq("bbbb2222", Json.Str(pointer, "session_id"),
                    "…y apunta a la instancia que sigue viva");

                // A dead PID is pruned; a live one is not.
                var ghost = Record(999999, "cccc3333", 8790, stamp);
                SessionStore.Write(ghost, 999999, "cccc3333");
                _check(!SessionStore.IsAlive(ghost), "una entrada con PID muerto se detecta");
                _check(SessionStore.IsAlive(second), "una entrada viva se detecta como viva");
                var pruned = SessionStore.Prune();
                _check(pruned >= 1, "la limpieza elimina las entradas obsoletas");
                _check(SessionStore.List().Any(s => Json.Str(s, "session_id") == "bbbb2222"),
                    "…y respeta la instancia viva");

                // PID reuse: same number, different process start time.
                var reused = Record(me.Id, "dddd4444", 8791,
                    me.StartTime.ToUniversalTime().AddHours(-3).ToString("o", CultureInfo.InvariantCulture));
                _check(!SessionStore.IsAlive(reused),
                    "un PID reciclado (otra hora de arranque) NO cuenta como vivo");

                // Atomic write: no torn file is ever visible.
                var probe = Path.Combine(SessionStore.SessionsDir(), "atomic.json");
                SessionStore.WriteAtomic(probe, "{\"a\":1}");
                SessionStore.WriteAtomic(probe, "{\"a\":2}");
                _eq(2.0, Json.Num(Json.ParseObject(File.ReadAllText(probe)), "a", 0),
                    "una segunda escritura reemplaza limpiamente a la primera");
                _check(!Directory.GetFiles(SessionStore.SessionsDir(), "*.tmp-*").Any(),
                    "no quedan archivos temporales tras la escritura atómica");

                // Permissions. On a filesystem that cannot express a DACL the
                // audit says so rather than claiming security it does not have.
                var audit = SessionStore.Audit(SessionStore.SessionsDir());
                _check(audit.ContainsKey("secure") && audit.ContainsKey("findings"),
                    "la auditoría de permisos reporta veredicto y hallazgos");
                if (audit["secure"] is bool secure && !secure)
                {
                    var findings = (List<object>)audit["findings"];
                    _check(findings.Count > 0, "un directorio inseguro enumera QUIÉN tiene acceso");
                }
                else
                {
                    _check(true, "el directorio de sesiones quedó restringido al usuario actual");
                }
            }
            finally
            {
                Environment.SetEnvironmentVariable("LOCALAPPDATA", previous);
                try { Directory.Delete(sandbox, true); } catch { /* best effort */ }
            }
        }

        private static Dictionary<string, object> Record(int pid, string sessionId, int port, string started)
            => new Dictionary<string, object>
            {
                ["contract"] = SessionStore.ContractVersion,
                ["session_id"] = sessionId,
                ["pid"] = (double)pid,
                ["port"] = (double)port,
                ["token"] = new string('a', 64),
                ["process_started"] = started,
                ["started"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture)
            };

        // ------------------------------------------------------ capabilities

        private static void CapabilityTests()
        {
            _section("capacidades declaradas");

            var described = Capabilities.Describe(
                "1.5.0", "Navisworks Manage 2026",
                new[] { "health", "document/save_as", "workflow/run" },
                documentOpen: true,
                saveCapability: new Dictionary<string, object> { ["save_as"] = true },
                cancellable: new[] { "workflow/group_levels" });

            _eq(Capabilities.ApiVersion, described["api_version"], "se declara la versión del contrato");
            _eq(SessionStore.ContractVersion, described["session_contract"], "…y la del registro de sesiones");
            _eq(ProfileSchemaVersion.Current, described["profile_schema"], "…y la del esquema de perfil");
            _eq("Navisworks Manage 2026", described["navisworks"], "…y la versión de Navisworks");

            var routes = (List<object>)described["routes"];
            _check(routes.Contains("document/save_as"), "las rutas disponibles se enumeran");

            var operations = (Dictionary<string, object>)described["operations"];
            foreach (var expected in new[] { "document_save_as", "jobs", "targeting", "idempotency", "mutation_envelope" })
            {
                _check(operations.ContainsKey(expected), $"se declara la operación «{expected}»");
            }

            var jobs = (Dictionary<string, object>)described["jobs"];
            _eq(true, jobs["supported"], "se declara soporte de trabajos");
            var cancel = (Dictionary<string, object>)jobs["cancel"];
            _eq(true, cancel["queued"], "un trabajo en cola siempre se puede cancelar");
            // Named routes, so a caller can tell BEFORE submitting whether the
            // thing it is about to start could be stopped. The old manifest
            // said "running_with_units", which described an implementation
            // detail the caller cannot observe in advance.
            var cancellableRoutes = (List<object>)cancel["cancellable_routes"];
            _check(cancellableRoutes.Contains("workflow/group_levels"),
                "se nombra la ruta que sí admite cancelación cooperativa");
            _check(!cancellableRoutes.Contains("document/save_as"),
                "…y no se promete cancelar un guardado, que es atómico");
            var outcomes = (List<object>)cancel["outcomes"];
            _check(outcomes.Contains(JobManager.CannotCancelRunning),
                "se declara el veredicto 'cannot_cancel_running'");

            // With nothing cancellable, the manifest must not claim otherwise.
            var noCancel = (Dictionary<string, object>)((Dictionary<string, object>)Capabilities.Describe(
                "1.5.0", "Navisworks Manage 2026", new[] { "health" },
                documentOpen: true,
                saveCapability: new Dictionary<string, object>(),
                cancellable: new string[0])["jobs"])["cancel"];
            _eq(false, noCancel["running"],
                "sin rutas cancelables no se anuncia cancelación de trabajos en curso");

            _check(described.ContainsKey("output_policy"), "se declara la política de salida vigente");

            var missing = Capabilities.Unsupported("document/save_as", "1.5.0");
            _eq("capability_unavailable", missing["error"], "una capacidad ausente tiene su propio código");
            _check(Convert.ToString(missing["hint"]).Contains("1.5.0"),
                "…y dice a qué versión actualizar");
        }

        // ------------------------------------------------------------ helpers

        private static HashSet<string> Set(params string[] values)
            => new HashSet<string>(values, StringComparer.OrdinalIgnoreCase);
    }
}
