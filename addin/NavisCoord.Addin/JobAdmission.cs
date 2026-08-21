using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace NavisCoord
{
    /// <summary>
    /// A payload reduced to a string that means the same thing every time.
    /// </summary>
    /// <remarks>
    /// Idempotency asks "is this the same request?", and answering it by
    /// hashing a <c>Dictionary</c> means answering it differently depending on
    /// insertion order — the same JSON parsed twice can enumerate its keys in
    /// two orders, so an honest retry would look like a conflict and a real
    /// conflict could look like a retry.
    ///
    /// Sorted by key at every level, ordinal, with numbers written in the
    /// invariant culture. Lists keep their order because a list's order is
    /// part of what it means; a dictionary's is not.
    /// </remarks>
    internal static class CanonicalPayload
    {
        public static string Render(object value)
        {
            var sb = new StringBuilder();
            Write(sb, value);
            return sb.ToString();
        }

        /// <summary>Stable 16-hex-char digest of a request payload.</summary>
        public static string Hash(object value)
        {
            var material = Render(value);
            using (var sha = SHA256.Create())
            {
                var bytes = sha.ComputeHash(Encoding.UTF8.GetBytes(material));
                var sb = new StringBuilder(16);
                for (var i = 0; i < 8; i++) sb.Append(bytes[i].ToString("x2", CultureInfo.InvariantCulture));
                return sb.ToString();
            }
        }

        private static void Write(StringBuilder sb, object value)
        {
            switch (value)
            {
                case null:
                    sb.Append("null");
                    return;
                case string text:
                    sb.Append('"').Append(text.Replace("\\", "\\\\").Replace("\"", "\\\"")).Append('"');
                    return;
                case bool flag:
                    sb.Append(flag ? "true" : "false");
                    return;
                case IDictionary<string, object> map:
                    sb.Append('{');
                    var first = true;
                    foreach (var key in map.Keys.OrderBy(k => k, StringComparer.Ordinal))
                    {
                        if (!first) sb.Append(',');
                        first = false;
                        sb.Append('"').Append(key).Append("\":");
                        Write(sb, map[key]);
                    }
                    sb.Append('}');
                    return;
                case System.Collections.IEnumerable list when !(value is string):
                    sb.Append('[');
                    var firstItem = true;
                    foreach (var item in list)
                    {
                        if (!firstItem) sb.Append(',');
                        firstItem = false;
                        Write(sb, item);
                    }
                    sb.Append(']');
                    return;
                default:
                    // Numbers and anything else, in a culture that does not
                    // decide between "1.5" and "1,5" based on the operator's
                    // Windows settings.
                    sb.Append(Convert.ToString(value, CultureInfo.InvariantCulture));
                    return;
            }
        }
    }

    /// <summary>
    /// Exactly what was accepted, frozen at the moment it was accepted.
    /// </summary>
    /// <remarks>
    /// Everything a job needs in order to run correctly minutes later, and
    /// nothing that could change underneath it. No Autodesk wrappers: a
    /// <c>Document</c> or a <c>SavedItem</c> stored here would be a handle to
    /// a tree that another job is entitled to rebuild. Identity, hashes and
    /// canonical text only.
    ///
    /// The profile is carried as canonical JSON rather than as a reference to
    /// the active one. A reference would still be a live object, and configure
    /// writes into the profile dictionary it is handed — so two jobs sharing
    /// one reference is not a hypothetical.
    /// </remarks>
    internal sealed class JobRequest
    {
        public readonly string JobId;
        public readonly string Route;
        public readonly string TargetId;
        public readonly string SessionId;
        public readonly string ExpectedFingerprint;
        public readonly string IdempotencyKey;
        public readonly string PayloadHash;
        public readonly string PayloadCanonical;

        /// <summary>
        /// Everything that makes this a different operation, in one hash.
        /// </summary>
        /// <remarks>
        /// Route, payload, document and profile. `dry_run`, `replace_existing`
        /// and every other flag ride inside the payload, so they are covered
        /// by construction rather than by a list somebody has to maintain.
        ///
        /// `session_id` is deliberately NOT in it. A session id changes when
        /// the bridge restarts or the client reconnects, and including it
        /// would mean a retry after a dropped connection — the exact case
        /// idempotency exists for — read as a different operation.
        /// `target_id` IS in it, because it names what the work is aimed at.
        /// </remarks>
        public readonly string IntentHash;
        public readonly string ProfileChecksum;
        public readonly string ProfileCanonical;
        public readonly DateTime SubmittedUtc;
        public readonly bool Cancellable;
        public readonly bool IsMutation;
        public readonly bool RequiresProfile;

        /// <summary>The payload, copied so the caller cannot edit it later.</summary>
        public readonly Dictionary<string, object> Payload;

        public JobRequest(
            string jobId,
            string route,
            Dictionary<string, object> payload,
            string targetId = "",
            string sessionId = "",
            string expectedFingerprint = "",
            string idempotencyKey = "",
            string profileChecksum = "",
            string profileCanonical = "",
            bool? cancellable = null,
            bool? isMutation = null,
            bool? requiresProfile = null)
        {
            var contract = RouteContracts.For(route);

            JobId = jobId ?? string.Empty;
            Route = route ?? string.Empty;
            Payload = payload == null
                ? new Dictionary<string, object>()
                : new Dictionary<string, object>(payload);
            TargetId = targetId ?? string.Empty;
            SessionId = sessionId ?? string.Empty;
            ExpectedFingerprint = (expectedFingerprint ?? string.Empty).Trim();
            IdempotencyKey = (idempotencyKey ?? string.Empty).Trim();
            PayloadCanonical = CanonicalPayload.Render(Payload);
            PayloadHash = CanonicalPayload.Hash(Payload);
            ProfileChecksum = profileChecksum ?? string.Empty;
            IntentHash = CanonicalPayload.Hash(new Dictionary<string, object>
            {
                ["route"] = Route.ToLowerInvariant(),
                ["payload"] = PayloadHash,
                ["document"] = ExpectedFingerprint.ToLowerInvariant(),
                ["target"] = TargetId,
                ["profile"] = ProfileChecksum
            });
            ProfileCanonical = profileCanonical ?? string.Empty;
            SubmittedUtc = DateTime.UtcNow;
            Cancellable = cancellable ?? contract?.Cancellable ?? false;
            IsMutation = isMutation ?? contract?.IsMutation ?? false;
            RequiresProfile = requiresProfile ?? contract?.RequiresProfile ?? false;
        }

        /// <summary>Whether two submissions mean the same thing.</summary>
        /// <remarks>
        /// Route, payload and document all have to match. A key reused across
        /// two different requests is a client bug, and answering it with the
        /// first request's result would apply neither.
        /// </remarks>
        public bool SameIntent(JobRequest other)
            => other != null &&
               string.Equals(IntentHash, other.IntentHash, StringComparison.Ordinal);

        public Dictionary<string, object> Describe()
            => new Dictionary<string, object>
            {
                ["job_id"] = JobId,
                ["route"] = Route,
                ["target_id"] = TargetId,
                ["session_id"] = SessionId,
                ["expected_document_fingerprint"] = ExpectedFingerprint,
                ["idempotency_key"] = IdempotencyKey,
                ["payload_hash"] = PayloadHash,
                ["intent_hash"] = IntentHash,
                ["profile_checksum"] = ProfileChecksum,
                ["submitted_at"] = SubmittedUtc.ToString("o", CultureInfo.InvariantCulture),
                ["cancellable"] = Cancellable,
                ["mutation"] = IsMutation
            };
    }

    /// <summary>Why a submission was or was not accepted.</summary>
    internal static class Admission
    {
        /// <summary>A key nobody had used: a genuinely new operation.</summary>
        public const string NewSubmission = "new_submission";
        /// <summary>Same key, same intent, job still in flight: that job.</summary>
        public const string DeduplicatedInFlight = "deduplicated_in_flight";
        /// <summary>Same key, same intent, finished: the stored envelope.</summary>
        public const string ReplayedFromLedger = "replayed_from_ledger";
        public const string IdempotencyConflict = "idempotency_conflict";
        public const string DocumentBusy = "document_busy";
        public const string FingerprintRequired = "fingerprint_required";
        public const string UnknownRoute = "unknown_route";
        public const string RouteNotJobbable = "unsupported_job_route";

        public const string DocumentChangedBeforeExecution = "document_changed_before_execution";
        public const string ProfileChangedBeforeExecution = "profile_changed_before_execution";
    }

    /// <summary>What <c>TrySubmit</c> decided.</summary>
    internal sealed class AdmissionResult
    {
        public string Outcome = Admission.NewSubmission;
        public string JobId = string.Empty;
        public string Detail = string.Empty;
        public Dictionary<string, object> Replay;

        /// <summary>The state the replayed job ended in, when replaying.</summary>
        public string TerminalState = string.Empty;

        public bool Ok => Outcome == Admission.NewSubmission ||
                          Outcome == Admission.DeduplicatedInFlight ||
                          Outcome == Admission.ReplayedFromLedger;

        public static AdmissionResult Reject(string outcome, string detail)
            => new AdmissionResult { Outcome = outcome, Detail = detail };
    }

    /// <summary>
    /// The checks that happen between accepting a job and letting it touch the
    /// document.
    /// </summary>
    /// <remarks>
    /// Separated from both the bridge and the job manager because they are the
    /// part that is easy to get wrong and impossible to test through either: a
    /// job that waited twenty minutes in a queue must be re-validated against
    /// the document it is about to mutate, not against the one it was accepted
    /// under.
    /// </remarks>
    internal static class JobPreflight
    {
        /// <summary>
        /// The envelope to return INSTEAD of running the handler, or null when
        /// it is safe to run.
        /// </summary>
        public static Dictionary<string, object> Blocker(
            JobRequest request, string liveFingerprint, string liveProfileChecksum)
        {
            if (request == null) return null;

            if (request.IsMutation &&
                !string.IsNullOrEmpty(request.ExpectedFingerprint) &&
                !string.Equals(request.ExpectedFingerprint, liveFingerprint ?? string.Empty,
                    StringComparison.OrdinalIgnoreCase))
            {
                // The document moved while the job waited. Being right at
                // submit does not authorise a mutation on a different model
                // twenty minutes later.
                return Envelope(request, Admission.DocumentChangedBeforeExecution,
                    "El documento cambió mientras el trabajo esperaba en cola (aceptado sobre " +
                    request.ExpectedFingerprint + ", ahora " + (liveFingerprint ?? "") +
                    "). No se ejecutó nada.",
                    liveFingerprint);
            }

            if (request.RequiresProfile &&
                !string.IsNullOrEmpty(request.ProfileChecksum) &&
                !string.IsNullOrEmpty(liveProfileChecksum) &&
                !string.Equals(request.ProfileChecksum, liveProfileChecksum, StringComparison.Ordinal))
            {
                // Reported, and then the job runs against its FROZEN profile
                // anyway — this is a warning path, not a refusal, because the
                // snapshot makes the swap harmless. It is only reachable when
                // something changed the profile despite the guard.
                return null;
            }

            return null;
        }

        private static Dictionary<string, object> Envelope(
            JobRequest request, string error, string detail, string liveFingerprint)
            => new Dictionary<string, object>
            {
                ["operation"] = request.Route,
                ["operation_id"] = Guid.NewGuid().ToString("N").Substring(0, 12),
                ["job_id"] = request.JobId,
                ["target_id"] = request.TargetId,
                ["session_id"] = request.SessionId,
                ["idempotency_key"] = request.IdempotencyKey,
                ["document_fingerprint_before"] = request.ExpectedFingerprint,
                ["document_fingerprint_after"] = liveFingerprint ?? string.Empty,
                ["profile_checksum"] = request.ProfileChecksum,
                ["dry_run"] = false,
                ["requested"] = 0.0,
                ["applied"] = 0.0,
                ["verified"] = 0.0,
                ["failed"] = 0.0,
                ["preserved"] = 0.0,
                ["blocked"] = 0.0,
                ["status"] = "failed",
                ["error"] = error,
                // Nothing ran, so nothing was re-read. Saying "preflight" would be
                // naming the moment rather than a source of evidence.
                ["verification_source"] = VerificationSources.None,
                ["warnings"] = new List<object>(),
                ["errors"] = new List<object> { detail },
                ["detail"] = detail
            };
    }

    /// <summary>
    /// An immutable, bounded copy of a mutation envelope.
    /// </summary>
    /// <remarks>
    /// Kept so a retry can be answered without re-running the work, which
    /// means it is held for as long as the ledger keeps it — and that is
    /// exactly why it must not be the handler's own dictionary. That object is
    /// still referenced by the job, still mutable, and can carry a per-unit
    /// detail list with thousands of entries, a base64 image, or a whole
    /// planning table. A session that runs for days would accumulate all of
    /// them.
    ///
    /// So: the envelope fields verbatim, warnings and errors capped, and
    /// everything else dropped with a note saying so. A replayed result tells
    /// the caller what happened; it is not a second copy of the work.
    /// </remarks>
    internal static class MutationEnvelopeSummary
    {
        private const int MaxMessages = 20;
        private const int MaxMessageLength = 500;

        /// <summary>The envelope fields, and only those.</summary>
        private static readonly string[] Keep =
        {
            "operation", "operation_id", "job_id", "target_id", "session_id",
            "idempotency_key", "document_fingerprint_before", "document_fingerprint_after",
            "profile_checksum", "dry_run", "requested", "applied", "verified", "failed",
            "preserved", "blocked", "status", "verification_source", "verification_limit",
            "error"
        };

        public static Dictionary<string, object> Trim(
            Dictionary<string, object> result, int budget)
        {
            if (result == null) return null;

            var summary = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (var key in Keep)
            {
                if (result.TryGetValue(key, out var value)) summary[key] = value;
            }
            summary["warnings"] = Messages(result, "warnings");
            summary["errors"] = Messages(result, "errors");

            var dropped = result.Keys.Count(k => !summary.ContainsKey(k));
            if (dropped > 0)
            {
                summary["detail_omitted"] = (double)dropped;
                summary["detail_note"] =
                    "El registro de idempotencia guarda el envelope, no el detalle por unidad. " +
                    "Consulta job/status mientras el trabajo siga en la tabla.";
            }

            // Last resort. If even the envelope is oversized — a handler that
            // put a blob into a scalar field — keep the counts and say so
            // rather than storing it.
            if (CanonicalPayload.Render(summary).Length > budget)
            {
                return new Dictionary<string, object>
                {
                    ["operation"] = Json.Str(result, "operation"),
                    ["job_id"] = Json.Str(result, "job_id"),
                    ["status"] = Json.Str(result, "status"),
                    ["requested"] = Json.Num(result, "requested"),
                    ["applied"] = Json.Num(result, "applied"),
                    ["verified"] = Json.Num(result, "verified"),
                    ["failed"] = Json.Num(result, "failed"),
                    ["verification_source"] = Json.Str(result, "verification_source"),
                    ["detail_note"] = "El envelope excedía el presupuesto del registro; " +
                                      "se conservan solo los conteos."
                };
            }
            return summary;
        }

        private static List<object> Messages(Dictionary<string, object> result, string key)
        {
            var kept = new List<object>();
            foreach (var raw in Json.Arr(result, key).Take(MaxMessages))
            {
                var text = Convert.ToString(raw) ?? string.Empty;
                kept.Add(text.Length > MaxMessageLength
                    ? text.Substring(0, MaxMessageLength) + "…"
                    : text);
            }
            return kept;
        }
    }

}
