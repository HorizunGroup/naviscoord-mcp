using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// One place that decides which HTTP status a domain outcome deserves.
    /// </summary>
    /// <remarks>
    /// The decision used to be spread across the bridge, and it drifted the
    /// way spread decisions do: <c>unknown_route</c> came back as 200 with an
    /// error dictionary, a wrong fingerprint came back as 200, an invalid
    /// profile came back as 200. The Python client raises on HTTP status
    /// codes, so every one of those arrived as a SUCCESSFUL call and the
    /// caller was told its work had been done.
    ///
    /// A note on `partial`, because it is the one case where 200 is
    /// deliberate: the request was received, authorised, executed and
    /// answered with an envelope that says exactly how much of it landed. That
    /// is a processed request, not a failed one — the shortfall is in the
    /// body, where a caller reading `verified` will find it, and turning it
    /// into a 4xx would make retry logic treat a truthful partial result as a
    /// transport failure.
    /// </remarks>
    internal static class HttpStatus
    {
        public const int Ok = 200;
        public const int Accepted = 202;
        public const int BadRequest = 400;
        public const int Unauthorized = 401;
        public const int Forbidden = 403;
        public const int NotFound = 404;
        public const int Conflict = 409;
        public const int PayloadTooLarge = 413;
        public const int Unprocessable = 422;
        public const int ServerError = 500;
        public const int Unavailable = 503;

        private static readonly Dictionary<string, int> ByCode =
            new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase)
            {
                // ------------------------------------------------ malformed
                [JsonStrict.InvalidJson] = BadRequest,
                [JsonStrict.NotAnObject] = BadRequest,
                [JsonStrict.TrailingContent] = BadRequest,
                [JsonStrict.DuplicateKey] = BadRequest,
                [JsonStrict.TooDeep] = BadRequest,
                [JsonStrict.TooLarge] = PayloadTooLarge,
                ["body_unreadable"] = BadRequest,
                ["body_too_large"] = PayloadTooLarge,
                ["bad_request"] = BadRequest,
                ["missing_argument"] = BadRequest,

                // ---------------------------------------------------- authn
                ["unauthorized"] = Unauthorized,
                ["missing_token"] = Unauthorized,
                ["invalid_token"] = Unauthorized,

                // ---------------------------------------------------- authz
                ["forbidden"] = Forbidden,
                ["not_loopback"] = Forbidden,

                // ------------------------------------------------ not there
                ["unknown_route"] = NotFound,
                ["unsupported_job_route"] = NotFound,
                ["unknown_job"] = NotFound,
                ["unknown_test"] = NotFound,
                ["not_found"] = NotFound,
                ["no_document"] = NotFound,
                ["profile_not_found"] = NotFound,

                // ------------------------------------------------- conflict
                ["document_changed"] = Conflict,
                ["document_mismatch"] = Conflict,
                [Admission.DocumentChangedBeforeExecution] = Conflict,
                [Admission.IdempotencyConflict] = Conflict,
                [Admission.DocumentBusy] = Conflict,
                [Admission.FingerprintRequired] = Conflict,
                ["fingerprint_required"] = Conflict,
                ["profile_busy"] = Conflict,
                ["job_conflict"] = Conflict,
                ["cannot_cancel_running"] = Conflict,
                ["already_finished"] = Conflict,
                ["bridge_stopping"] = Conflict,

                // --------------------------------------- valid but not sane
                ["profile_invalid"] = Unprocessable,
                ["profile_unreadable"] = BadRequest,
                ["profile_missing"] = BadRequest,
                ["profile_too_large"] = PayloadTooLarge,
                ["profile_locked"] = Conflict,
                ["profile_checksum_mismatch"] = Unprocessable,
                ["invalid_profile"] = Unprocessable,
                ["schema_invalid"] = Unprocessable,
                [EnvelopeContract.InvalidMutationResult] = Unprocessable,

                // -------------------------------------------------- the host
                ["dispatcher_busy"] = Unavailable,
                ["dispatcher_timeout"] = Unavailable,
                ["host_shutting_down"] = Unavailable,
                ["bridge_not_ready"] = Unavailable,

                // ------------------------------------------------- our fault
                ["internal_error"] = ServerError
            };

        /// <summary>The status for a bare error code.</summary>
        /// <remarks>
        /// An unrecognised code is 500, not 200. A code nobody classified is a
        /// failure nobody understood, and answering it with success is how the
        /// client learns to ignore the body.
        /// </remarks>
        public static int For(string errorCode)
            => string.IsNullOrEmpty(errorCode)
                ? Ok
                : ByCode.TryGetValue(errorCode, out var status) ? status : ServerError;

        /// <summary>
        /// The status a handler's whole reply deserves.
        /// </summary>
        /// <remarks>
        /// Reads the reply rather than trusting the call site to remember,
        /// because the call site forgetting is exactly how a mutation that
        /// declared <c>status: failed</c> went out as 200.
        /// </remarks>
        public static int For(Dictionary<string, object> payload, int success = Ok)
        {
            if (payload == null) return ServerError;

            var error = Json.Str(payload, "error");
            if (!string.IsNullOrEmpty(error)) return For(error);

            switch (Json.Str(payload, "status"))
            {
                case "failed":
                    // A failure with no code is still a failure. Which one is
                    // a question for the body; that it is not a success is
                    // not.
                    return Unprocessable;
                case "cancelled":
                    return Conflict;
                case "completed":
                case "planned":
                case "partial":
                    // `partial` is a processed request whose envelope states
                    // the shortfall. See the note on the class.
                    return success;
                default:
                    return success;
            }
        }

        /// <summary>The status for an admission outcome.</summary>
        public static int ForAdmission(string outcome)
        {
            switch (outcome)
            {
                case Admission.NewSubmission:
                case Admission.DeduplicatedInFlight:
                    return Accepted;
                case Admission.ReplayedFromLedger:
                    // The work is already done and this is its result, not a
                    // new acceptance.
                    return Ok;
                default:
                    return For(outcome);
            }
        }

        /// <summary>Every code this build classifies. Diagnostics and tests.</summary>
        public static IEnumerable<KeyValuePair<string, int>> Table
            => ByCode.OrderBy(kv => kv.Key, StringComparer.OrdinalIgnoreCase);
    }
}
