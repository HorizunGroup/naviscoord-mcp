using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// How a read behaves while a mutation owns the document.
    /// </summary>
    /// <remarks>
    /// Declared per route rather than guessed from its name. "clash/export"
    /// and "clash/status" differ by one word and by everything else, and a
    /// rule that reads the string is a rule that breaks the first time a route
    /// is renamed.
    /// </remarks>
    internal enum ReadClass
    {
        /// <summary>Not a read: the route mutates.</summary>
        NotARead = 0,

        /// <summary>
        /// Answerable from memory, without the document or the UI thread.
        /// Safe at any moment, including mid-mutation.
        /// </summary>
        SafeDuringMutation = 1,

        /// <summary>
        /// Needs the UI thread, so it queues behind a running mutation and is
        /// answered once that thread is free. Correct, just not immediate.
        /// </summary>
        NeedsUiThread = 2,

        /// <summary>
        /// Would read a tree another job is rebuilding underneath it. Refused
        /// while a mutation is in flight rather than served a torn answer.
        /// </summary>
        IncompatibleWithMutation = 3
    }

    /// <summary>
    /// Everything the rest of the add-in needs to know about one route.
    /// </summary>
    /// <remarks>
    /// This exists because the same facts were spelled out in four places —
    /// the router's job table, a cancellable set beside it, a hand-written
    /// operations array in <see cref="Capabilities"/>, and the submission
    /// logic in the HTTP bridge — and nothing kept them in step. A route added
    /// to one was not added to the others, so the manifest could advertise an
    /// operation the router did not have, and a new mutation could reach the
    /// job manager without ever declaring itself a mutation.
    ///
    /// Navisworks-free on purpose: the table is the thing a test has to be
    /// able to compare against the router, and a test runner has no licence.
    /// </remarks>
    internal sealed class RouteContract
    {
        public string Name = string.Empty;

        /// <summary>Whether the route changes the document at all.</summary>
        public bool IsMutation;

        /// <summary>
        /// Whether the change outlives the session — saved sets, clash tests,
        /// results, permanent overrides — as opposed to a transient selection.
        /// </summary>
        /// <remarks>
        /// Both are mutations. The distinction drives the fingerprint
        /// requirement, not whether the route is allowed to lie about what it
        /// did: "it is only visual" is exactly the reasoning that let
        /// permanent material overrides be treated as a read.
        /// </remarks>
        public bool Persistent;

        public bool SupportsJob;
        public bool Cancellable;
        public bool RequiresDocument = true;
        public bool RequiresFingerprint;
        public bool RequiresProfile;

        /// <summary>Whether the reply must be a full mutation envelope.</summary>
        public bool RequiresEnvelope;

        public bool SupportsDryRun;

        /// <summary>Whether a retry with the same key must not re-apply.</summary>
        public bool RequiresIdempotency;

        public ReadClass Reads = ReadClass.NotARead;

        /// <summary>How the route proves what it did.</summary>
        public string VerificationSource = string.Empty;

        public Dictionary<string, object> ToJson()
            => new Dictionary<string, object>
            {
                ["route"] = Name,
                ["mutation"] = IsMutation,
                ["persistent"] = Persistent,
                ["job"] = SupportsJob,
                ["cancellable"] = Cancellable,
                ["requires_document"] = RequiresDocument,
                ["requires_fingerprint"] = RequiresFingerprint,
                ["requires_profile"] = RequiresProfile,
                ["requires_envelope"] = RequiresEnvelope,
                ["supports_dry_run"] = SupportsDryRun,
                ["requires_idempotency"] = RequiresIdempotency,
                ["read_class"] = Reads.ToString(),
                ["verification_source"] = VerificationSource,
                // Published so a caller knows BEFORE running whether this
                // route can ever say `completed`, instead of discovering the
                // limit from a partial it did not expect.
                ["verifiable"] = VerificationSources.ProvesVerification(VerificationSource)
            };
    }

    /// <summary>
    /// The one table. Router, HttpBridge, JobManager and Capabilities read it.
    /// </summary>
    internal static class RouteContracts
    {
        private static readonly Dictionary<string, RouteContract> Table = Build();

        public static IEnumerable<RouteContract> All
            => Table.Values.OrderBy(c => c.Name, StringComparer.OrdinalIgnoreCase);

        public static IEnumerable<string> Names
            => Table.Keys.OrderBy(n => n, StringComparer.OrdinalIgnoreCase);

        /// <summary>The contract for a route, or null when it has none.</summary>
        /// <remarks>
        /// Null rather than a permissive default. A route nobody classified is
        /// a route nobody decided the safety of, and inventing a lenient
        /// contract for it is how an unclassified mutation ends up running
        /// without a fingerprint.
        /// </remarks>
        public static RouteContract For(string route)
            => Table.TryGetValue(route ?? string.Empty, out var contract) ? contract : null;

        public static bool IsMutation(string route) => For(route)?.IsMutation == true;

        public static bool RequiresEnvelope(string route) => For(route)?.RequiresEnvelope == true;

        public static bool IsCancellable(string route) => For(route)?.Cancellable == true;

        public static IEnumerable<string> Cancellable
            => All.Where(c => c.Cancellable).Select(c => c.Name);

        public static IEnumerable<string> Mutations
            => All.Where(c => c.IsMutation).Select(c => c.Name);

        public static IEnumerable<string> JobRoutes
            => All.Where(c => c.SupportsJob).Select(c => c.Name);

        private static void Read(
            Dictionary<string, RouteContract> table,
            string name,
            ReadClass reads,
            bool requiresDocument = true)
        {
            table[name] = new RouteContract
            {
                Name = name,
                IsMutation = false,
                RequiresDocument = requiresDocument,
                RequiresEnvelope = false,
                Reads = reads
            };
        }

        private static void Mutation(
            Dictionary<string, RouteContract> table,
            string name,
            string verification,
            bool persistent = true,
            bool job = false,
            bool cancellable = false,
            bool profile = false,
            bool dryRun = false,
            bool idempotency = true)
        {
            table[name] = new RouteContract
            {
                Name = name,
                IsMutation = true,
                Persistent = persistent,
                SupportsJob = job,
                Cancellable = cancellable,
                RequiresDocument = true,
                // Every mutation, transient ones included. A selection applied
                // to the wrong document is still the wrong document.
                RequiresFingerprint = true,
                RequiresProfile = profile,
                RequiresEnvelope = true,
                SupportsDryRun = dryRun,
                RequiresIdempotency = idempotency,
                Reads = ReadClass.NotARead,
                VerificationSource = verification
            };
        }

        private static Dictionary<string, RouteContract> Build()
        {
            var table = new Dictionary<string, RouteContract>(StringComparer.OrdinalIgnoreCase);

            // ------------------------------------------------------- reads
            Read(table, "health", ReadClass.SafeDuringMutation, requiresDocument: false);
            Read(table, "capabilities", ReadClass.SafeDuringMutation, requiresDocument: false);
            Read(table, "census", ReadClass.NeedsUiThread);
            Read(table, "model/schema", ReadClass.NeedsUiThread);
            Read(table, "sets/list", ReadClass.NeedsUiThread);
            Read(table, "profile/info", ReadClass.SafeDuringMutation, requiresDocument: false);

            // The clash tree is what a run and a grouping job rebuild, and a
            // wrapper enumerated across that rebuild is a dangling native
            // pointer. These wait rather than read a tree in motion.
            Read(table, "clash/tests", ReadClass.IncompatibleWithMutation);
            Read(table, "clash/export", ReadClass.IncompatibleWithMutation);
            Read(table, "analysis/revision", ReadClass.IncompatibleWithMutation);
            Read(table, "clash/image", ReadClass.IncompatibleWithMutation);

            // Audit only measures; it changes nothing.
            Read(table, "workflow/audit_models", ReadClass.NeedsUiThread);

            // ---------------------------------------------------- mutations
            Mutation(table, "sets/build", VerificationSources.DocumentReread, job: true, dryRun: true);
            Mutation(table, "sets/build_search", VerificationSources.SavedItemReread, job: true, dryRun: true);
            Mutation(table, "clash/matrix", VerificationSources.SelectionSourceReread, job: true, dryRun: true);
            Mutation(table, "clash/run", VerificationSources.ClashTestStatusReread, job: true);
            Mutation(table, "clash/group", VerificationSources.GroupMembershipReread, job: true, dryRun: true);
            Mutation(table, "clash/status", VerificationSources.ResultStatusReread, dryRun: true);
            Mutation(table, "clash/apply_rules", VerificationSources.ResultStatusReread, job: true, dryRun: true);
            Mutation(table, "viewpoints/save", VerificationSources.SavedViewpointReread);

            // Overrides survive the session and are written into the saved
            // file. Calling them "only visual" is how they escaped the
            // envelope in the first place.
            Mutation(table, "appearance/color", VerificationSources.AppearanceOverrideReread);
            Mutation(table, "appearance/reset", VerificationSources.AppearanceOverrideReread);

            // The current selection dies with the session, so it is a mutation
            // that is not persistent — still fingerprinted, still verified.
            Mutation(table, "selection/set", VerificationSources.CurrentSelectionReread, persistent: false);

            Mutation(table, "workflow/configure", VerificationSources.SavedItemReread,
                job: true, profile: true, dryRun: true);
            Mutation(table, "workflow/run", VerificationSources.ClashTestStatusReread, job: true, dryRun: true);
            Mutation(table, "workflow/group_levels", VerificationSources.DocumentReread,
                job: true, cancellable: true, profile: true, dryRun: true);
            Mutation(table, "workflow/rules", VerificationSources.DocumentReread, job: true, profile: true, dryRun: true);

            Mutation(table, "document/save", VerificationSources.FilesystemAndDocumentReread, job: true, idempotency: false);
            Mutation(table, "document/save_as", VerificationSources.FilesystemAndDocumentReread, job: true, idempotency: false);

            // ------------------------------------------- host-level actions
            //
            // Closing a document and exiting the application are not document
            // mutations — there is no document left to verify against — but
            // they are irreversible, so they keep their own envelope and are
            // never eligible to run as a background job.
            table["document/close"] = new RouteContract
            {
                Name = "document/close",
                IsMutation = true,
                Persistent = false,
                SupportsJob = false,
                RequiresDocument = true,
                RequiresFingerprint = true,
                RequiresEnvelope = true,
                RequiresIdempotency = false,
                Reads = ReadClass.NotARead,
                VerificationSource = VerificationSources.DocumentClosedReread
            };
            table["application/exit"] = new RouteContract
            {
                Name = "application/exit",
                IsMutation = true,
                Persistent = false,
                SupportsJob = false,
                RequiresDocument = false,
                RequiresFingerprint = false,
                RequiresEnvelope = false,
                RequiresIdempotency = false,
                Reads = ReadClass.NotARead,
                VerificationSource = VerificationSources.ProcessExit
            };

            // Loading or resetting the profile does not touch the document,
            // but it changes what every later job would be judged against —
            // which is why JobManager refuses it while one is in flight.
            table["profile/load"] = new RouteContract
            {
                Name = "profile/load",
                IsMutation = false,
                RequiresDocument = false,
                RequiresEnvelope = false,
                Reads = ReadClass.SafeDuringMutation
            };
            table["profile/reset"] = new RouteContract
            {
                Name = "profile/reset",
                IsMutation = false,
                RequiresDocument = false,
                RequiresEnvelope = false,
                Reads = ReadClass.SafeDuringMutation
            };

            return table;
        }
    }
}
