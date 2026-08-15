using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// What this build of the add-in can actually do.
    /// </summary>
    /// <remarks>
    /// The MCP server and the add-in ship separately — the server updates
    /// itself from a wheel, the add-in only when somebody closes Navisworks
    /// and runs the installer — so at any moment a user is running two
    /// versions that were never tested together. Without a manifest the
    /// server can only discover a missing route by calling it and reading a
    /// 404, which surfaces to the operator as "unknown_route", a sentence
    /// that explains nothing and suggests nothing.
    ///
    /// This is the negotiation instead: one unauthenticated-free, cheap route
    /// listing the contract version, the routes that exist, and the named
    /// operations with a flag each. A server that wants <c>document/save_as</c>
    /// asks first and, when the answer is false, says which add-in version to
    /// install rather than failing mid-workflow.
    ///
    /// Kept free of Navisworks references so it can be asserted in tests; the
    /// product name arrives as a parameter.
    /// </remarks>
    internal static class Capabilities
    {
        /// <summary>
        /// Route/tool contract. Bumped when an existing route changes shape,
        /// not when one is added — additions are visible in <c>routes</c>.
        /// </summary>
        public const string ApiVersion = "naviscoord.api/2";

        public const string ProfileSchema = ProfileSchemaVersion.Current;

        /// <summary>Named operations a caller can check before relying on one.</summary>
        public static readonly string[] Operations =
        {
            "census", "model_schema", "clash_export", "clash_image",
            "sets_explicit", "sets_search", "clash_matrix", "clash_run",
            "clash_group", "clash_status", "clash_rules",
            "viewpoints_save", "appearance_color", "appearance_reset", "selection_set",
            "workflow_audit_models", "workflow_configure", "workflow_run",
            "workflow_group_levels",
            "document_save", "document_save_as",
            "jobs", "sessions", "targeting", "idempotency", "mutation_envelope"
        };

        public static Dictionary<string, object> Describe(
            string addinVersion,
            string navisworksProduct,
            IEnumerable<string> routes,
            bool documentOpen,
            IDictionary<string, object> saveCapability,
            IEnumerable<string> cancellable = null)
        {
            var operations = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            foreach (var operation in Operations) operations[operation] = true;

            // A parameter rather than a call into Router, which references
            // Navisworks; this file has to stay assertable in the test runner.
            var cancellableRoutes = (cancellable ?? Enumerable.Empty<string>())
                .OrderBy(r => r, StringComparer.OrdinalIgnoreCase).Cast<object>().ToList();

            return new Dictionary<string, object>
            {
                ["api_version"] = ApiVersion,
                ["session_contract"] = SessionStore.ContractVersion,
                ["profile_schema"] = ProfileSchema,
                ["addin_version"] = addinVersion ?? string.Empty,
                ["navisworks"] = navisworksProduct ?? string.Empty,
                ["document_open"] = documentOpen,
                ["routes"] = (routes ?? Enumerable.Empty<string>())
                    .OrderBy(r => r, StringComparer.OrdinalIgnoreCase)
                    .Cast<object>()
                    .ToList(),
                ["operations"] = operations,
                ["save"] = saveCapability ?? new Dictionary<string, object>
                {
                    ["capability"] = false,
                    ["reason"] = "sin documento abierto"
                },
                ["jobs"] = new Dictionary<string, object>
                {
                    ["supported"] = true,
                    ["states"] = new List<object>
                    {
                        JobManager.Queued, JobManager.Running, JobManager.Verifying,
                        JobManager.Completed, JobManager.Partial, JobManager.Failed,
                        JobManager.Cancelled
                    },
                    // Named routes rather than a promise about "jobs with
                    // units": the caller can now check BEFORE submitting
                    // whether the thing it is about to start could be stopped,
                    // instead of finding out from the reply to a cancel.
                    ["cancel"] = new Dictionary<string, object>
                    {
                        ["queued"] = true,
                        ["running"] = cancellableRoutes.Count > 0,
                        ["cancellable_routes"] = cancellableRoutes,
                        ["outcomes"] = new List<object>
                        {
                            JobManager.CancelledBeforeStart, JobManager.CancelRequested_,
                            JobManager.CannotCancelRunning, JobManager.AlreadyFinished
                        },
                        ["note"] = "Todo lo demás es una llamada atómica del API: una vez iniciada " +
                                   "no se interrumpe, y job/cancel responde 'cannot_cancel_running'."
                    },
                    ["concurrency"] = "una mutación por documento"
                },
                ["output_policy"] = PathPolicy.Default().Describe()
            };
        }

        /// <summary>
        /// The sentence an older add-in should produce for a route it lacks.
        /// </summary>
        public static Dictionary<string, object> Unsupported(string route, string sinceVersion)
            => new Dictionary<string, object>
            {
                ["error"] = "capability_unavailable",
                ["route"] = route ?? string.Empty,
                ["detail"] = "Este complemento de Navisworks no expone «" + route + "».",
                ["hint"] = "Actualiza el complemento a " + sinceVersion +
                           " o posterior (cierra Navisworks y ejecuta install.ps1 o el instalador), " +
                           "y consulta 'capabilities' para ver qué ofrece el que tienes."
            };
    }
}
