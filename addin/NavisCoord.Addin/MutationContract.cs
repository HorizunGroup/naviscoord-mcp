using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace NavisCoord
{
    /// <summary>
    /// Identity of the document a mutation is allowed to touch.
    /// </summary>
    /// <remarks>
    /// This is an IDENTITY fingerprint, not a content hash, and the
    /// distinction is the whole design. If it changed on every edit, the
    /// second call of any two-step workflow would be refused by the guard
    /// that the first call installed. What it must catch is the operator
    /// closing Torre A and opening Torre B — or appending another model —
    /// between the moment a plan was computed and the moment it is applied,
    /// because every path id in that plan then points at different geometry.
    ///
    /// Pure string work on purpose: the caller collects the parts from the
    /// live Document, and this half is testable without Navisworks.
    /// </remarks>
    internal static class DocumentFingerprint
    {
        public const string None = "";

        /// <summary>
        /// Delimiter between identity parts, written as an escape rather than
        /// as the literal byte.
        /// </summary>
        /// <remarks>
        /// A separator at all, because joining with "" lets two different
        /// documents produce the same material: title "AB" with one model
        /// concatenates identically to title "A" with model "B". Unit
        /// Separator specifically because it cannot occur in a Windows path,
        /// a document title or a decimal count, so it can never be part of a
        /// part rather than between two.
        ///
        /// Spelled as the escape and not pasted in: the raw byte is invisible in
        /// every editor, and it makes git classify the whole file as binary —
        /// which silently exempts it from the LF normalisation in
        /// .gitattributes and from every text diff.
        /// </remarks>
        private const string Separator = "\u001f";

        /// <summary>Stable 16-hex-char digest of the identity parts.</summary>
        public static string Compute(IEnumerable<string> parts)
        {
            var material = string.Join(Separator, (parts ?? Enumerable.Empty<string>())
                .Select(p => (p ?? string.Empty).Trim().ToLowerInvariant()));
            if (material.Length == 0) return None;

            using (var sha = SHA256.Create())
            {
                var hash = sha.ComputeHash(Encoding.UTF8.GetBytes(material));
                var sb = new StringBuilder(16);
                for (var i = 0; i < 8; i++) sb.Append(hash[i].ToString("x2", CultureInfo.InvariantCulture));
                return sb.ToString();
            }
        }

        /// <summary>
        /// True when a caller-supplied expectation matches the live document.
        /// An empty expectation means "did not ask", which read operations
        /// accept and mutations do not.
        /// </summary>
        public static bool Matches(string expected, string actual)
            => string.IsNullOrWhiteSpace(expected) ||
               string.Equals(expected.Trim(), actual, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// The one response shape every mutation returns.
    /// </summary>
    /// <remarks>
    /// Before this existed each handler invented its own counters —
    /// <c>attempted</c>/<c>verified_in_document</c> here,
    /// <c>matched</c>/<c>edited</c> there, <c>moved</c>/<c>verified_children</c>
    /// somewhere else — so no caller could tell "it worked" from "it was
    /// attempted" without reading the handler. Now there is one envelope and
    /// one rule: <c>verified</c> is only ever set from a re-read of the
    /// document, and <c>status</c> can only be <c>completed</c> when
    /// <c>verified</c> accounts for everything that was applied.
    /// </remarks>
    internal sealed class MutationResult
    {
        public string OperationId = Guid.NewGuid().ToString("N").Substring(0, 12);
        public string JobId = string.Empty;
        public string TargetId = string.Empty;
        public string IdempotencyKey = string.Empty;
        public string Operation = string.Empty;
        public string FingerprintBefore = string.Empty;
        public string FingerprintAfter = string.Empty;
        public string SessionId = string.Empty;
        public string ProfileChecksum = string.Empty;
        public int Requested;
        public int Applied;
        public int Verified;
        public int Failed;
        /// <summary>Units deliberately left alone, and correctly so.</summary>
        /// <remarks>
        /// Distinct from failed. A clash test kept because rebuilding it would
        /// orphan its results was not a failure of the run; reporting it as
        /// one would push an operator to "fix" the very thing that protected
        /// their work. It is still not completeness, which is why it blocks
        /// <c>completed</c> the same way a failure does.
        /// </remarks>
        public int Preserved;
        /// <summary>Units that could not proceed safely and were refused.</summary>
        public int Blocked;
        public bool DryRun;
        /// <summary>How this operation proved what it did. Defaults to none.</summary>
        /// <remarks>
        /// The default used to be "document_reread", which meant a handler
        /// that never set it inherited a claim to have re-read the document.
        /// That is the generic filler the contract exists to forbid: forgetting
        /// to verify now costs the route its `completed`, which is the correct
        /// answer to "did you check?".
        /// </remarks>
        public string VerificationSource = VerificationSources.None;
        public readonly List<object> Warnings = new List<object>();
        public readonly List<object> Errors = new List<object>();
        public readonly Dictionary<string, object> Detail = new Dictionary<string, object>();

        public MutationResult(string operation)
        {
            Operation = operation ?? string.Empty;
        }

        public MutationResult Warn(string message)
        {
            if (!string.IsNullOrWhiteSpace(message)) Warnings.Add(message);
            return this;
        }

        public MutationResult Fail(string message)
        {
            if (!string.IsNullOrWhiteSpace(message))
            {
                Errors.Add(message);
                // `failed` counts affected units, not exception messages. A
                // single unit can produce several verification details, so
                // never increment blindly; cover every requested unit that
                // still lacks proof, with one as the minimum for a global
                // failure that happened before a unit count was available.
                Failed = Math.Max(Failed, Math.Max(1, Requested - Verified));
            }
            return this;
        }

        /// <summary>
        /// The status, derived — never assigned by a handler.
        /// </summary>
        /// <remarks>
        /// A dry run is <c>planned</c>, because calling it "completed" is how
        /// a rehearsal gets mistaken for the real thing. Otherwise: nothing
        /// verified out of work attempted is <c>failed</c>; less verified than
        /// applied is <c>partial</c>; everything verified is <c>completed</c>.
        /// A handler that forgot to verify therefore reports <c>failed</c>,
        /// which is the correct answer to "did you check?".
        /// </remarks>
        public string Status()
        {
            if (DryRun) return "planned";
            if (Errors.Count > 0 && Verified == 0) return "failed";
            if (Applied == 0 && Requested == 0) return "completed";
            if (Verified == 0 && Applied > 0) return "failed";
            if (Verified < Applied || Failed > 0 || Blocked > 0 || Applied < Requested) return "partial";
            // A source that proves nothing cannot support completeness, even
            // when every count lines up.
            if (!VerificationSources.ProvesVerification(VerificationSource)) return "partial";
            return "completed";
        }

        public Dictionary<string, object> ToJson()
        {
            var payload = new Dictionary<string, object>
            {
                ["operation"] = Operation,
                ["operation_id"] = OperationId,
                ["job_id"] = JobId,
                ["target_id"] = TargetId,
                ["idempotency_key"] = IdempotencyKey,
                ["document_fingerprint_before"] = FingerprintBefore,
                ["document_fingerprint_after"] = FingerprintAfter,
                ["dry_run"] = DryRun,
                ["requested"] = (double)Requested,
                ["applied"] = (double)Applied,
                ["verified"] = (double)Verified,
                ["failed"] = (double)Failed,
                ["preserved"] = (double)Preserved,
                ["blocked"] = (double)Blocked,
                ["session_id"] = SessionId,
                ["profile_checksum"] = ProfileChecksum,
                ["status"] = Status(),
                ["verification_source"] = DryRun ? "not_applicable" : VerificationSource,
                ["warnings"] = Warnings,
                ["errors"] = Errors
            };
            foreach (var pair in Detail)
            {
                if (!payload.ContainsKey(pair.Key)) payload[pair.Key] = pair.Value;
            }
            return payload;
        }
    }

    /// <summary>
    /// The closed set of ways a mutation is allowed to say it checked.
    /// </summary>
    /// <remarks>
    /// `verification_source` used to be free text, which meant a handler could
    /// satisfy the contract by typing something. The words that showed up in
    /// practice — "handler_returned", "items_resolved", "attempted" — all name
    /// something that happened BEFORE or DURING the mutation, and none of them
    /// is evidence about the document afterwards. Resolving a ModelItem proves
    /// the item exists; it does not prove the colour landed on it.
    ///
    /// Every name here is a re-read that happens after the write, and the
    /// route that claims one has to actually perform it. `None` is the honest
    /// answer when the API exposes no getter, and it can never accompany
    /// `completed`.
    /// </remarks>
    internal static class VerificationSources
    {
        /// <summary>Saved items re-enumerated from the document.</summary>
        public const string DocumentReread = "document_reread";

        /// <summary>Saved items re-resolved by GUID, not by name.</summary>
        public const string SavedItemReread = "saved_item_reread";

        /// <summary>Clash tests re-read for their Complete/Old/Partial state.</summary>
        public const string ClashTestStatusReread = "clash_test_status_reread";

        /// <summary>Individual clash results re-read for their status.</summary>
        public const string ResultStatusReread = "result_status_reread";

        /// <summary>Group children re-enumerated and matched by GUID.</summary>
        public const string GroupMembershipReread = "group_membership_reread";

        /// <summary>A clash test's SelectionSources re-resolved.</summary>
        public const string SelectionSourceReread = "selection_source_reread";

        /// <summary>The live selection re-read and compared by identity.</summary>
        public const string CurrentSelectionReread = "current_selection_reread";

        /// <summary>ModelGeometry.PermanentColor / PermanentTransparency re-read.</summary>
        public const string AppearanceOverrideReread = "appearance_override_reread";

        /// <summary>Saved viewpoints re-enumerated from the document.</summary>
        public const string SavedViewpointReread = "saved_viewpoint_reread";

        /// <summary>The file on disk plus the document's own state.</summary>
        public const string FilesystemAndDocumentReread = "filesystem_and_document_reread";

        /// <summary>The document is gone, so it is re-checked for absence.</summary>
        public const string DocumentClosedReread = "document_closed_reread";

        /// <summary>The process is ending; nothing survives to be re-read.</summary>
        public const string ProcessExit = "process_exit";

        /// <summary>
        /// The API exposes no way to read the effect back.
        /// </summary>
        /// <remarks>
        /// Permitted, and it costs the route its `completed`. Saying "I cannot
        /// check this" is a fact the caller can act on; inventing a source is
        /// not.
        /// </remarks>
        public const string None = "none";

        /// <summary>A rehearsal changed nothing, so there is nothing to re-read.</summary>
        public const string NotApplicable = "not_applicable";

        private static readonly HashSet<string> Known = new HashSet<string>(StringComparer.Ordinal)
        {
            DocumentReread, SavedItemReread, ClashTestStatusReread, ResultStatusReread,
            GroupMembershipReread, SelectionSourceReread, CurrentSelectionReread,
            AppearanceOverrideReread, SavedViewpointReread, FilesystemAndDocumentReread,
            DocumentClosedReread, ProcessExit, None, NotApplicable
        };

        public static bool IsKnown(string source)
            => Known.Contains(source ?? string.Empty);

        /// <summary>Whether this source can support a claim of completeness.</summary>
        public static bool ProvesVerification(string source)
            => IsKnown(source) && source != None && source != NotApplicable;

        public static IEnumerable<string> All
            => Known.OrderBy(s => s, StringComparer.Ordinal);
    }

    /// <summary>
    /// Whether a handler's reply is a mutation envelope that can be believed.
    /// </summary>
    /// <remarks>
    /// The rule this replaces was: no <c>status</c> and no <c>error</c> means
    /// completed. Every route that never adopted the envelope — the search
    /// sets, the matrix, the grouping, the colouring, the viewpoints, the
    /// saves — therefore reported success by saying nothing at all, and a
    /// handler that half-failed reported success by saying nothing about it.
    /// Silence is now a protocol failure, which is the only reading that does
    /// not reward a handler for omitting the hard part.
    /// </remarks>
    internal static class EnvelopeContract
    {
        public const string InvalidMutationResult = "invalid_mutation_result";

        public const string Planned = "planned";
        public const string Completed = "completed";
        public const string Partial = "partial";
        public const string Failed = "failed";
        public const string Cancelled = "cancelled";

        private static readonly HashSet<string> Known = new HashSet<string>(StringComparer.Ordinal)
        {
            Planned, Completed, Partial, Failed, Cancelled
        };

        /// <summary>What is wrong with this envelope. Empty means nothing.</summary>
        public static List<string> Problems(string route, Dictionary<string, object> result)
        {
            var problems = new List<string>();
            var contract = RouteContracts.For(route);

            if (result == null)
            {
                problems.Add("la ruta no devolvió nada");
                return problems;
            }

            // A read-only route owes no envelope; it answers with whatever
            // shape its data has.
            if (contract == null)
            {
                problems.Add("la ruta '" + route + "' no tiene contrato declarado");
                return problems;
            }
            if (!contract.RequiresEnvelope) return problems;

            var status = Json.Str(result, "status");
            if (string.IsNullOrEmpty(status))
            {
                problems.Add("una mutación debe declarar 'status'; esta no declaró ninguno");
                return problems;
            }
            if (!Known.Contains(status))
            {
                problems.Add("'status' desconocido: '" + status + "'");
                return problems;
            }

            var dryRun = Json.Bool(result, "dry_run");
            if (dryRun && status != Planned)
            {
                problems.Add("un dry_run solo puede declarar 'planned', no '" + status + "'");
            }
            if (!dryRun && status == Planned)
            {
                problems.Add("'planned' es exclusivo del dry_run");
            }
            if (dryRun && Json.Str(result, "verification_source") != VerificationSources.NotApplicable)
            {
                problems.Add("un dry_run no verifica nada: su fuente es '" +
                             VerificationSources.NotApplicable + "'");
            }

            foreach (var field in new[] { "requested", "applied", "verified", "failed" })
            {
                if (!result.ContainsKey(field))
                {
                    problems.Add("falta el contador '" + field + "'");
                }
                else if (Json.Num(result, field, -1) < 0)
                {
                    problems.Add("'" + field + "' no puede ser negativo");
                }
            }
            if (problems.Count > 0) return problems;

            var requested = (int)Json.Num(result, "requested");
            var applied = (int)Json.Num(result, "applied");
            var verified = (int)Json.Num(result, "verified");

            if (applied > requested)
            {
                problems.Add("applied (" + applied + ") no puede superar requested (" + requested + ")");
            }
            if (verified > applied)
            {
                problems.Add("verified (" + verified + ") no puede superar applied (" + applied + ")");
            }

            var source = Json.Str(result, "verification_source");
            if (!VerificationSources.IsKnown(source))
            {
                // Free text used to be accepted here, and the words that
                // turned up all named something from before the write:
                // "items_resolved", "attempted", "handler_returned". None of
                // them is evidence about the document afterwards.
                problems.Add("'verification_source' no está en el conjunto permitido: '" +
                             source + "'");
            }

            if (status == Completed)
            {
                if (dryRun) problems.Add("un dry_run nunca es completed");
                if (!VerificationSources.ProvesVerification(source))
                {
                    problems.Add("completed exige una fuente de relectura real; '" + source +
                                 "' no demuestra nada sobre el documento posterior");
                }
                if (verified < requested)
                {
                    problems.Add("completed exige verified (" + verified + ") = requested (" +
                                 requested + ")");
                }
                if (Json.Num(result, "failed") > 0)
                {
                    problems.Add("completed no admite unidades fallidas");
                }
                if (Json.Num(result, "blocked") > 0)
                {
                    problems.Add("completed no admite unidades bloqueadas");
                }
                if (!string.IsNullOrEmpty(Json.Str(result, "document_fingerprint_before")) &&
                    string.IsNullOrEmpty(Json.Str(result, "document_fingerprint_after")))
                {
                    problems.Add("completed exige la huella del documento después");
                }
            }

            return problems;
        }

        public static bool IsValid(string route, Dictionary<string, object> result)
            => Problems(route, result).Count == 0;
    }

    /// <summary>
    /// Remembers what each idempotency key already produced.
    /// </summary>
    /// <remarks>
    /// A retried mutation is the normal case, not the exotic one: the HTTP
    /// client retries on a dropped connection, and the operator clicks the
    /// ribbon button again when the first click seemed to do nothing. Without
    /// a key, "group these clashes" applied twice leaves two groups; with it,
    /// the second call returns the first call's envelope and touches nothing.
    ///
    /// Bounded and in-process: it exists to collapse a retry storm inside one
    /// Navisworks session, not to be a durable log.
    /// </remarks>
    internal static class IdempotencyLedger
    {
        private const int Capacity = 256;
        private static readonly object Gate = new object();
        private static readonly Dictionary<string, Dictionary<string, object>> Entries =
            new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
        private static readonly Queue<string> Order = new Queue<string>();

        public static bool TryGet(string key, out Dictionary<string, object> cached)
            => TryGet(key, string.Empty, string.Empty, out cached);

        public static bool TryGet(
            string key,
            string operation,
            string fingerprint,
            out Dictionary<string, object> cached)
        {
            cached = null;
            var scope = Scope(key, operation, fingerprint);
            if (scope.Length == 0) return false;
            lock (Gate)
            {
                if (!Entries.TryGetValue(scope, out var stored)) return false;
                cached = new Dictionary<string, object>(stored)
                {
                    ["idempotent_replay"] = true,
                    ["note"] = "Esta operación ya se había ejecutado con la misma idempotency_key; " +
                               "se devuelve el resultado original sin volver a tocar el documento."
                };
                return true;
            }
        }

        public static void Remember(string key, Dictionary<string, object> payload)
        {
            if (string.IsNullOrWhiteSpace(key) || payload == null) return;
            var operation = payload.TryGetValue("operation", out var rawOperation)
                ? Convert.ToString(rawOperation, System.Globalization.CultureInfo.InvariantCulture)
                : string.Empty;
            var fingerprint = payload.TryGetValue("document_fingerprint_before", out var rawFingerprint)
                ? Convert.ToString(rawFingerprint, System.Globalization.CultureInfo.InvariantCulture)
                : string.Empty;
            var scope = Scope(key, operation, fingerprint);
            lock (Gate)
            {
                if (!Entries.ContainsKey(scope)) Order.Enqueue(scope);
                Entries[scope] = new Dictionary<string, object>(payload);
                while (Order.Count > Capacity)
                {
                    var evicted = Order.Dequeue();
                    Entries.Remove(evicted);
                }
            }
        }

        public static void Clear()
        {
            lock (Gate)
            {
                Entries.Clear();
                Order.Clear();
            }
        }

        public static int Count
        {
            get { lock (Gate) { return Entries.Count; } }
        }

        private static string Scope(string key, string operation, string fingerprint)
        {
            var raw = (key ?? string.Empty).Trim();
            if (raw.Length == 0) return string.Empty;
            var op = (operation ?? string.Empty).Trim();
            var fp = (fingerprint ?? string.Empty).Trim();
            // Length prefixes make the tuple unambiguous even when a caller's
            // key contains the separator itself.
            return raw.Length + ":" + raw + "|" + op.Length + ":" + op +
                   "|" + fp.Length + ":" + fp;
        }
    }
}
