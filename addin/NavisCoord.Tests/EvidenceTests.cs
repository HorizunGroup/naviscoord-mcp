using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// A mutation may only claim what it re-read, and the envelope must not be
    /// able to manufacture that claim.
    /// </summary>
    /// <remarks>
    /// Wrapping seventeen handlers in a common envelope fixed the JSON shape.
    /// It did not, by itself, fix the truth: a route can still hand the
    /// wrapper a number it computed BEFORE mutating and have it come out
    /// labelled `verified`. Resolving a ModelItem proves the item exists;
    /// nothing about a colour landing on it. Counting the tests whose name
    /// starts with a prefix proves a name exists; nothing about the test being
    /// bound to its selection sets.
    ///
    /// So the closed set of verification sources exists to make "how did you
    /// check?" unanswerable by typing, and these assert both halves: the
    /// contract refuses evidence that is not evidence, and every mutating
    /// route declares a source that names a re-read.
    /// </remarks>
    internal static class EvidenceTests
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

            ResolutionIsNotVerification();
            GenericSourcesAreRefused();
            NoneNeverCompletes();
            MissingCountsAreNotFilledIn();
            AppliedWithoutVerifiedIsNotCompleted();
            BlockedAndPreservedDoNotInflateVerified();
            DryRunHasNoVerificationSource();
            EveryMutatingRouteDeclaresARealSource();
            EveryCompletedSourceIsAllowed();
            ANewRouteWithoutAVerifierBreaksTheSuite();
            LegacyOkIsNotSuccess();
            CapabilitiesDeclareVerifiability();
            SealedDoesNotFabricate();
        }

        private static Dictionary<string, object> Envelope(
            int requested, int applied, int verified, string status,
            string source, bool dryRun = false, int blocked = 0, int preserved = 0)
        {
            var body = new Dictionary<string, object>
            {
                ["dry_run"] = dryRun,
                ["requested"] = (double)requested,
                ["applied"] = (double)applied,
                ["verified"] = (double)verified,
                ["failed"] = (double)Math.Max(0, applied - verified),
                ["blocked"] = (double)blocked,
                ["preserved"] = (double)preserved,
                ["status"] = status,
                ["verification_source"] = source,
                ["document_fingerprint_before"] = "fp-1",
                ["document_fingerprint_after"] = "fp-1"
            };
            return body;
        }

        // ------------------------------------------------------------- cases

        private static void ResolutionIsNotVerification()
        {
            _section("evidencia: resolver no es verificar");

            // The exact shape appearance/color used to return: every item
            // resolved, so every item declared verified — with a count taken
            // before the override was applied.
            foreach (var invented in new[]
                     {
                         "items_resolved", "resolved", "attempted", "assumed",
                         "handler_returned", "api_did_not_throw"
                     })
            {
                var claim = Envelope(10, 10, 10, "completed", invented);
                var problems = EnvelopeContract.Problems("appearance/color", claim);
                _check(problems.Count > 0, "'" + invented + "' no se acepta como fuente");
                _check(!VerificationSources.IsKnown(invented),
                    "'" + invented + "' no está en el conjunto cerrado");
            }
        }

        private static void GenericSourcesAreRefused()
        {
            _section("evidencia: la fuente sale de un conjunto cerrado");

            _check(VerificationSources.All.Any(), "hay un conjunto declarado");
            foreach (var source in VerificationSources.All)
            {
                _check(VerificationSources.IsKnown(source), "'" + source + "' es reconocida");
            }
            _check(!VerificationSources.IsKnown("cualquier_cosa"), "y nada más lo es");
            _check(!VerificationSources.IsKnown(""), "ni el vacío");
        }

        private static void NoneNeverCompletes()
        {
            _section("evidencia: verification_source=none impide completed");

            var claim = Envelope(3, 3, 3, "completed", VerificationSources.None);
            var problems = EnvelopeContract.Problems("appearance/color", claim);
            _check(problems.Count > 0, "'none' no sostiene un completed");
            _check(problems.Any(p => p.IndexOf("relectura real", StringComparison.Ordinal) >= 0),
                "y se dice por qué");

            _check(!VerificationSources.ProvesVerification(VerificationSources.None),
                "'none' no demuestra nada");
            _check(!VerificationSources.ProvesVerification(VerificationSources.NotApplicable),
                "'not_applicable' tampoco");
            _check(VerificationSources.ProvesVerification(VerificationSources.DocumentReread),
                "una relectura sí");

            // Declared honestly as partial, it IS valid: saying "I could not
            // check this" is a fact the caller can use.
            var honest = Envelope(3, 3, 0, "partial", VerificationSources.None);
            honest["verification_limit"] = "la API no expone un getter para este efecto";
            _check(EnvelopeContract.IsValid("appearance/color", honest),
                "declararlo como partial con su límite sí es válido");
        }

        private static void MissingCountsAreNotFilledIn()
        {
            _section("evidencia: el envelope no rellena los conteos que faltan");

            foreach (var field in new[] { "requested", "applied", "verified", "failed" })
            {
                var claim = Envelope(5, 5, 5, "completed", VerificationSources.DocumentReread);
                claim.Remove(field);
                var problems = EnvelopeContract.Problems("clash/status", claim);
                _check(problems.Count > 0, "falta '" + field + "' y no se inventa");
                _check(problems.Any(p => p.IndexOf(field, StringComparison.Ordinal) >= 0),
                    "y se nombra el que falta");
            }
        }

        private static void AppliedWithoutVerifiedIsNotCompleted()
        {
            _section("evidencia: aplicado sin verificar no es completed");

            var claim = Envelope(5, 5, 0, "completed", VerificationSources.DocumentReread);
            _check(!EnvelopeContract.IsValid("clash/status", claim),
                "5 aplicadas y 0 verificadas no puede ser completed");

            var honest = Envelope(5, 5, 0, "partial", VerificationSources.DocumentReread);
            _check(EnvelopeContract.IsValid("clash/status", honest), "partial sí");

            var result = new MutationResult("clash/status")
            {
                Requested = 5, Applied = 5, Verified = 0,
                VerificationSource = VerificationSources.ResultStatusReread
            };
            _eq("failed", result.Status(), "y el envelope lo deriva como failed por su cuenta");
        }

        private static void BlockedAndPreservedDoNotInflateVerified()
        {
            _section("evidencia: preserved y blocked no cuentan como verificado");

            var result = new MutationResult("sets/build_search")
            {
                Requested = 6, Applied = 2, Verified = 2, Preserved = 3, Blocked = 1,
                VerificationSource = VerificationSources.SavedItemReread
            };
            var json = result.ToJson();
            _eq(2.0, json["verified"], "verified sigue siendo lo comprobado");
            _eq(3.0, json["preserved"], "lo conservado va aparte");
            _eq(1.0, json["blocked"], "y lo bloqueado también");
            _eq("partial", json["status"], "y nada de eso completa la operación");

            var claim = Envelope(6, 6, 6, "completed", VerificationSources.SavedItemReread,
                blocked: 1, preserved: 3);
            _check(!EnvelopeContract.IsValid("sets/build_search", claim),
                "declarar completed con unidades bloqueadas se rechaza");
        }

        private static void DryRunHasNoVerificationSource()
        {
            _section("evidencia: un ensayo no declara relectura");

            var planned = Envelope(4, 0, 0, "planned", VerificationSources.NotApplicable,
                dryRun: true);
            _check(EnvelopeContract.IsValid("sets/build_search", planned),
                "un ensayo declara 'not_applicable'");

            var pretending = Envelope(4, 0, 0, "planned", VerificationSources.DocumentReread,
                dryRun: true);
            _check(!EnvelopeContract.IsValid("sets/build_search", pretending),
                "un ensayo que dice haber releído se rechaza");

            var real = Envelope(4, 4, 4, "completed", VerificationSources.NotApplicable);
            _check(!EnvelopeContract.IsValid("sets/build_search", real),
                "y una corrida real no puede decir 'not_applicable'");
        }

        private static void EveryMutatingRouteDeclaresARealSource()
        {
            _section("evidencia: toda ruta mutante declara una fuente del conjunto");

            foreach (var contract in RouteContracts.All.Where(c => c.IsMutation))
            {
                _check(VerificationSources.IsKnown(contract.VerificationSource),
                    "'" + contract.Name + "' declara una fuente reconocida (" +
                    contract.VerificationSource + ")");
            }
        }

        private static void EveryCompletedSourceIsAllowed()
        {
            _section("evidencia: toda ruta que puede completar tiene relectura real");

            foreach (var contract in RouteContracts.All.Where(c => c.IsMutation))
            {
                var canComplete = VerificationSources.ProvesVerification(contract.VerificationSource);
                if (contract.Name == "application/exit")
                {
                    // The process is ending. There is no document left to read
                    // and the route says so rather than inventing one.
                    _eq(VerificationSources.ProcessExit, contract.VerificationSource,
                        "application/exit declara que el proceso termina");
                    continue;
                }
                _check(canComplete,
                    "'" + contract.Name + "' puede completar porque relee (" +
                    contract.VerificationSource + ")");
            }
        }

        private static void ANewRouteWithoutAVerifierBreaksTheSuite()
        {
            _section("evidencia: una ruta mutante sin verificador rompe la suite");

            // Simulated rather than added to the real table: the point is that
            // the check would catch it, not that the product ships one.
            var rogue = new RouteContract
            {
                Name = "ruta/nueva",
                IsMutation = true,
                RequiresEnvelope = true,
                VerificationSource = string.Empty
            };
            _check(!VerificationSources.IsKnown(rogue.VerificationSource),
                "una ruta mutante sin fuente no pasa la comprobación");

            var invented = new RouteContract
            {
                Name = "ruta/nueva",
                IsMutation = true,
                RequiresEnvelope = true,
                VerificationSource = "yo_lo_vi"
            };
            _check(!VerificationSources.IsKnown(invented.VerificationSource),
                "y una fuente inventada tampoco");

            // The real table has none of either.
            _check(RouteContracts.All.Where(c => c.IsMutation)
                    .All(c => VerificationSources.IsKnown(c.VerificationSource)),
                "y hoy ninguna ruta real está en ese estado");
        }

        private static void LegacyOkIsNotSuccess()
        {
            _section("evidencia: un Ok heredado no se convierte en éxito");

            var legacy = new Dictionary<string, object>
            {
                ["ok"] = true,
                ["action"] = "color",
                ["resolved"] = 128.0,
                ["unresolved"] = 0.0
            };
            var problems = EnvelopeContract.Problems("appearance/color", legacy);
            _check(problems.Count > 0, "una respuesta antigua no cumple el contrato");
            _check(problems.Any(p => p.IndexOf("status", StringComparison.Ordinal) >= 0),
                "y lo primero que le falta es decir qué pasó");
            _check(!EnvelopeContract.IsValid("appearance/color", legacy),
                "'resolved: 128' no es una verificación");
        }

        private static void CapabilitiesDeclareVerifiability()
        {
            _section("evidencia: capabilities publica cómo verifica cada ruta");

            var manifest = Capabilities.Describe(
                "0.4.0", "Navisworks Manage 2026",
                RouteContracts.Names, true, null, RouteContracts.Cancellable);

            var contracts = ((List<object>)manifest["contracts"])
                .Cast<Dictionary<string, object>>()
                .ToList();
            _eq(RouteContracts.All.Count(), contracts.Count, "publica todas las rutas");

            foreach (var contract in RouteContracts.All.Where(c => c.IsMutation))
            {
                var row = contracts.Single(c =>
                    string.Equals(Convert.ToString(c["route"]), contract.Name, StringComparison.Ordinal));
                _eq(contract.VerificationSource, row["verification_source"],
                    "'" + contract.Name + "' publica su fuente");
                _check(row.ContainsKey("verifiable"), "y si es verificable");
                _eq(VerificationSources.ProvesVerification(contract.VerificationSource),
                    row["verifiable"],
                    "'" + contract.Name + "' declara verifiable coherente con su fuente");
            }
        }

        private static void SealedDoesNotFabricate()
        {
            _section("evidencia: el sellador valida, no fabrica");

            // Sealed itself needs a live document, so what is asserted here is
            // its contract as the envelope sees it: every combination it is
            // supposed to reject is rejected by the same rules, and there is
            // no path from "no evidence" to "completed".
            var noEvidence = Envelope(4, 4, 4, "completed", VerificationSources.None);
            _check(!EnvelopeContract.IsValid("appearance/color", noEvidence),
                "sin evidencia no hay completed");

            var overreach = Envelope(4, 2, 4, "partial", VerificationSources.DocumentReread);
            _check(!EnvelopeContract.IsValid("appearance/color", overreach),
                "no se puede verificar más de lo aplicado");

            var beyond = Envelope(2, 4, 2, "partial", VerificationSources.DocumentReread);
            _check(!EnvelopeContract.IsValid("appearance/color", beyond),
                "ni aplicar más de lo pedido");

            var negative = Envelope(4, 4, 4, "completed", VerificationSources.DocumentReread);
            negative["verified"] = -2.0;
            _check(!EnvelopeContract.IsValid("appearance/color", negative),
                "ni declarar conteos negativos");

            // And the structural half: the sealer must not contain a default
            // that supplies the numbers a handler failed to provide.
            var source = FindAddinSource("WriteHandlers.cs");
            _check(source != null, "se encontró WriteHandlers.cs");
            if (source == null) return;
            var text = System.IO.File.ReadAllText(source);
            var from = text.IndexOf("private static Dictionary<string, object> Sealed(",
                StringComparison.Ordinal);
            var to = text.IndexOf("public static Dictionary<string, object> ListSets",
                StringComparison.Ordinal);
            _check(from > 0 && to > from, "se acotó el cuerpo del sellador");
            var body = text.Substring(from, to - from);

            _check(body.IndexOf("int verified = ", StringComparison.Ordinal) < 0,
                "verified no tiene valor por defecto");
            _check(body.IndexOf("int applied = ", StringComparison.Ordinal) < 0,
                "applied tampoco");
            _check(body.IndexOf("verified: requested", StringComparison.Ordinal) < 0,
                "y no se deriva verified de requested");
            _check(body.IndexOf("VerificationSources.IsKnown", StringComparison.Ordinal) > 0,
                "en cambio sí valida la fuente contra el conjunto cerrado");
            _check(body.IndexOf("EnvelopeContract.InvalidMutationResult", StringComparison.Ordinal) > 0,
                "y devuelve invalid_mutation_result cuando la evidencia no cuadra");
        }

        private static string FindAddinSource(string name)
        {
            var dir = new System.IO.DirectoryInfo(AppContext.BaseDirectory);
            while (dir != null)
            {
                var candidate = System.IO.Path.Combine(
                    dir.FullName, "addin", "NavisCoord.Addin", name);
                if (System.IO.File.Exists(candidate)) return candidate;
                dir = dir.Parent;
            }
            return null;
        }
    }
}
