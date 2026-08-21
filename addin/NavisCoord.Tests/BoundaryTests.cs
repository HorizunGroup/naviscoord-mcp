using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// The trust boundaries: who may read the credential, and what a reply
    /// means to a client that raises on HTTP status.
    /// </summary>
    /// <remarks>
    /// Two failures that looked unrelated and were the same mistake — treating
    /// "nothing threw" as "it worked".
    ///
    /// The ACL path applied a DACL, caught whatever came back, logged a
    /// warning and wrote a bearer token that can rebuild somebody's clash
    /// tests. It never read the permissions back, so on a volume that cannot
    /// express a DACL the credential was published world-readable and the
    /// evidence was a log line.
    ///
    /// The HTTP path answered 200 for <c>unknown_route</c>, for a wrong
    /// fingerprint and for an invalid profile. The Python client raises on
    /// status codes, so each of those arrived as a SUCCESSFUL call.
    /// </remarks>
    internal static class BoundaryTests
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

            WideIdentitiesAreRefused();
            InheritanceIsRefused();
            AnUnreadableAclIsNotSecure();
            OnlyOurOwnIdentitiesPass();
            TheRealFileIsVerifiedNotAssumed();
            ErrorsNeverAnswerTwoHundred();
            EveryCategoryHasItsStatus();
            AdmissionOutcomesMapCorrectly();
            PartialStaysTwoHundred();
            TheBridgeStateMachine();
        }

        // ------------------------------------------------------------ helpers

        /// <summary>The SIDs this process is entitled to. Two, invented.</summary>
        private static readonly string[] Mine = { "S-1-5-21-1-2-3-1001", "S-1-5-18" };

        private static AclVerdict Judge(bool readable, bool isProtected, params string[] allowed)
            => AclPolicy.Judge("C:\\temp\\sesion.json", readable, isProtected, allowed, Mine);

        // -------------------------------------------------------------- cases

        private static void WideIdentitiesAreRefused()
        {
            _section("acl: ninguna identidad amplia puede leer la credencial");

            foreach (var pair in AclPolicy.ForbiddenSids)
            {
                var verdict = Judge(true, true, Mine[0], pair.Key);
                _check(!verdict.Secure, pair.Value + " hace insegura la ACL");
                _check(verdict.UntrustedIdentities.Any(u => u.Contains(pair.Key)),
                    "…y se nombra en el veredicto");
            }

            // By SID and not by display name: "Users" is "Usuarios" on a
            // Spanish Windows, and a check on the name passes there.
            _check(AclPolicy.ForbiddenSids.ContainsKey("S-1-5-32-545"),
                "el grupo Users se identifica por SID, no por nombre localizado");
            _check(AclPolicy.Describe("S-1-1-0").IndexOf("Everyone", StringComparison.Ordinal) >= 0,
                "y se traduce a un nombre legible para el diagnóstico");
        }

        private static void InheritanceIsRefused()
        {
            _section("acl: la herencia activa es insegura por sí sola");

            var inherited = Judge(true, false, Mine[0], Mine[1]);
            _check(!inherited.Secure, "aunque solo estemos nosotros, la herencia rompe la garantía");
            _check(inherited.Problems.Any(p => p.IndexOf("herencia", StringComparison.Ordinal) >= 0),
                "y se dice que el permiso del padre alcanza al archivo");
            _eq("inseguro", inherited.State, "el estado es inseguro");
        }

        private static void AnUnreadableAclIsNotSecure()
        {
            _section("acl: no poder leer los permisos NO es que sean correctos");

            var unknown = Judge(false, false);
            _check(!unknown.Secure, "una ACL ilegible nunca es segura");
            _eq("indeterminado", unknown.State, "su estado es indeterminado, que es lo honesto");
            _check(unknown.Problems.Any(p =>
                    p.IndexOf("indeterminado", StringComparison.Ordinal) >= 0),
                "y se explica que para una credencial equivale a inseguro");

            // The distinction the old audit collapsed: it reported true
            // whenever nothing threw.
            _check(Judge(true, true, Mine[0]).Secure, "una ACL legible y correcta sí es segura");
        }

        private static void OnlyOurOwnIdentitiesPass()
        {
            _section("acl: solo pasan las identidades del propio proceso");

            _check(AclPolicy.IsTrusted(Mine[0], Mine), "el usuario que corre Navisworks");
            _check(AclPolicy.IsTrusted("S-1-5-18", Mine), "y SYSTEM, que ya posee el proceso");
            _check(!AclPolicy.IsTrusted("S-1-5-21-9-9-9-1002", Mine),
                "otro usuario del equipo no");
            _check(!AclPolicy.IsTrusted("S-1-1-0", Mine),
                "y Everyone no, ni aunque estuviera en la lista propia");
            _check(!AclPolicy.IsTrusted("", Mine), "una identidad vacía tampoco");
            _check(!AclPolicy.IsTrusted(Mine[0], new string[0]),
                "sin identidades propias no se confía en nadie");
        }

        private static void TheRealFileIsVerifiedNotAssumed()
        {
            _section("acl: se relee la ACL de un archivo real en carpeta temporal");

            // On a temp file, never on the user's own session records: the
            // point is to observe the verifier, not to weaken anything live.
            var dir = System.IO.Path.Combine(
                System.IO.Path.GetTempPath(), "naviscoord-acl-" + Guid.NewGuid().ToString("N"));
            System.IO.Directory.CreateDirectory(dir);
            var path = System.IO.Path.Combine(dir, "sesion.json");
            try
            {
                System.IO.File.WriteAllText(path, "{}");

                // Straight after creation the file inherits from the temp
                // directory, so it must NOT verify as secure.
                var inherited = SessionStore.VerifyAcl(path);
                _check(!inherited.Secure,
                    "un archivo recién creado hereda permisos y no se da por seguro");

                // And a path that does not exist is indeterminate, not secure.
                var missing = SessionStore.VerifyAcl(System.IO.Path.Combine(dir, "no-existe.json"));
                _check(!missing.Secure, "un archivo ausente no es seguro");
                _eq("indeterminado", missing.State, "…es indeterminado");
                _check(missing.Problems.Count > 0, "…y dice por qué");
            }
            finally
            {
                try { System.IO.Directory.Delete(dir, true); } catch { /* temp */ }
            }
        }

        private static void ErrorsNeverAnswerTwoHundred()
        {
            _section("http: ningún error de dominio responde 200");

            foreach (var code in new[]
                     {
                         "unknown_route", "invalid_json", "profile_invalid", "document_changed",
                         "unknown_job", Admission.IdempotencyConflict, Admission.DocumentBusy,
                         "unauthorized", "not_loopback", "body_too_large",
                         EnvelopeContract.InvalidMutationResult, "bridge_stopping"
                     })
            {
                _check(HttpStatus.For(code) != HttpStatus.Ok,
                    "'" + code + "' no puede ser 200 (da " + HttpStatus.For(code) + ")");
            }

            // A payload carrying an error is read from the payload, so a call
            // site that forgot cannot send it out as success.
            var failed = new Dictionary<string, object>
            {
                ["status"] = "failed",
                ["error"] = "document_changed"
            };
            _eq(HttpStatus.Conflict, HttpStatus.For(failed), "el cuerpo decide el estado");

            var quietFailure = new Dictionary<string, object> { ["status"] = "failed" };
            _eq(HttpStatus.Unprocessable, HttpStatus.For(quietFailure),
                "un fallo sin código sigue siendo un fallo");

            // An unclassified code is 500, never 200: a failure nobody
            // understood is not a success.
            _eq(HttpStatus.ServerError, HttpStatus.For("algo_que_nadie_clasifico"),
                "un código sin clasificar es 500");
        }

        private static void EveryCategoryHasItsStatus()
        {
            _section("http: cada categoría tiene el estado que le corresponde");

            var expected = new Dictionary<string, int>
            {
                ["unknown_route"] = HttpStatus.NotFound,
                ["unknown_job"] = HttpStatus.NotFound,
                ["invalid_json"] = HttpStatus.BadRequest,
                [JsonStrict.TooDeep] = HttpStatus.BadRequest,
                [JsonStrict.TooLarge] = HttpStatus.PayloadTooLarge,
                ["body_too_large"] = HttpStatus.PayloadTooLarge,
                ["unauthorized"] = HttpStatus.Unauthorized,
                ["not_loopback"] = HttpStatus.Forbidden,
                ["document_changed"] = HttpStatus.Conflict,
                [Admission.IdempotencyConflict] = HttpStatus.Conflict,
                [Admission.DocumentBusy] = HttpStatus.Conflict,
                ["cannot_cancel_running"] = HttpStatus.Conflict,
                ["profile_invalid"] = HttpStatus.Unprocessable,
                ["profile_checksum_mismatch"] = HttpStatus.Unprocessable,
                ["dispatcher_busy"] = HttpStatus.Unavailable,
                ["internal_error"] = HttpStatus.ServerError
            };

            foreach (var pair in expected)
            {
                _eq(pair.Value, HttpStatus.For(pair.Key), "'" + pair.Key + "' → " + pair.Value);
            }

            _check(HttpStatus.Table.Any(), "la tabla es inspeccionable");
            _check(HttpStatus.Table.All(kv => kv.Value >= 200 && kv.Value < 600),
                "y todos sus valores son estados HTTP");
        }

        private static void AdmissionOutcomesMapCorrectly()
        {
            _section("http: la admisión de trabajos mapea a 202, 200 o su conflicto");

            _eq(HttpStatus.Accepted, HttpStatus.ForAdmission(Admission.NewSubmission),
                "un trabajo nuevo es 202");
            _eq(HttpStatus.Accepted, HttpStatus.ForAdmission(Admission.DeduplicatedInFlight),
                "uno deduplicado en vuelo también");
            _eq(HttpStatus.Ok, HttpStatus.ForAdmission(Admission.ReplayedFromLedger),
                "un resultado ya producido es 200: no se acepta nada nuevo");
            _eq(HttpStatus.Conflict, HttpStatus.ForAdmission(Admission.IdempotencyConflict),
                "un conflicto de clave es 409");
            _eq(HttpStatus.Conflict, HttpStatus.ForAdmission(Admission.DocumentBusy),
                "un documento ocupado es 409");
            _eq(HttpStatus.NotFound, HttpStatus.ForAdmission(Admission.RouteNotJobbable),
                "una ruta que no admite job es 404");
        }

        private static void PartialStaysTwoHundred()
        {
            _section("http: partial sigue siendo 200, y se documenta por qué");

            var partial = new Dictionary<string, object>
            {
                ["status"] = "partial",
                ["requested"] = 10.0,
                ["applied"] = 10.0,
                ["verified"] = 4.0
            };
            _eq(HttpStatus.Ok, HttpStatus.For(partial),
                "la petición se procesó y el envelope declara cuánto se logró");

            _eq(HttpStatus.Ok, HttpStatus.For(
                    new Dictionary<string, object> { ["status"] = "completed" }),
                "completed también");
            _eq(HttpStatus.Ok, HttpStatus.For(
                    new Dictionary<string, object> { ["status"] = "planned" }),
                "y un ensayo");
            _eq(HttpStatus.Conflict, HttpStatus.For(
                    new Dictionary<string, object> { ["status"] = "cancelled" }),
                "pero cancelled no: el estado del trabajo cambió bajo el llamador");
        }

        private static void TheBridgeStateMachine()
        {
            _section("puente: los estados dicen qué se acepta y qué se responde");

            foreach (var state in BridgeState.Known)
            {
                _check(BridgeState.IsKnown(state), "'" + state + "' es un estado declarado");
            }
            _check(!BridgeState.IsKnown("detenido_mas_o_menos"), "y nada más lo es");

            // Only ready takes new mutations. This is the whole point of
            // draining: "stopped" used to mean the listener closed, while a
            // job kept mutating the document with its session file deleted.
            _check(BridgeState.AcceptsMutations(BridgeState.Ready), "ready acepta mutaciones");
            foreach (var state in new[]
                     {
                         BridgeState.Starting, BridgeState.Stopping, BridgeState.Draining,
                         BridgeState.Stopped, BridgeState.FailedSecurity
                     })
            {
                _check(!BridgeState.AcceptsMutations(state), "'" + state + "' no acepta mutaciones");
            }

            // But draining still answers, which is what makes the running job
            // queryable instead of invisible.
            _check(BridgeState.AnswersQueries(BridgeState.Draining),
                "draining sigue respondiendo consultas");
            _check(BridgeState.AnswersQueries(BridgeState.Stopping), "stopping también");
            _check(!BridgeState.AnswersQueries(BridgeState.Stopped), "stopped ya no");
            _check(!BridgeState.AnswersQueries(BridgeState.FailedSecurity),
                "y un fallo de seguridad tampoco: nada llegó a escuchar");

            _eq(HttpStatus.Conflict, HttpStatus.For("bridge_stopping"),
                "una mutación durante el drenaje se rechaza con 409");
        }
    }
}
