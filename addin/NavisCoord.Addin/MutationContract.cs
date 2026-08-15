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
        public int Requested;
        public int Applied;
        public int Verified;
        public int Failed;
        public bool DryRun;
        public string VerificationSource = "document_reread";
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
            if (Verified < Applied || Failed > 0 || Applied < Requested) return "partial";
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
