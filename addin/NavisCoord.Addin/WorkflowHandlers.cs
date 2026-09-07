using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Autodesk.Navisworks.Api.Clash;

namespace NavisCoord
{
    /// <summary>
    /// HTTP routes for the four coordination steps, plus profile inspection.
    /// </summary>
    /// <remarks>
    /// Thin by design: resolve the profile, guard the fingerprint, call
    /// <see cref="CoordinationWorkflow"/>, return what it produced. Every line
    /// of judgement is in the workflow service, which the ribbon buttons also
    /// call — so the MCP tools and the buttons cannot drift apart.
    /// </remarks>
    internal static class WorkflowHandlers
    {
        public static Dictionary<string, object> AuditModels(Dictionary<string, object> payload)
            => AuditModels(payload, null);

        public static Dictionary<string, object> AuditModels(
            Dictionary<string, object> payload, JobManager.Job job)
        {
            var doc = Router.RequireDocument();
            if (!TryProfile(out var profile, out var problem)) return problem;

            var result = CoordinationWorkflow.AuditModels(doc, profile, job);
            Attribute(result, profile);
            result["document_fingerprint"] = DocumentContext.Fingerprint(doc);
            result["text"] = WorkflowText.AuditModels(result);
            return result;
        }

        public static Dictionary<string, object> Configure(Dictionary<string, object> payload)
            => Configure(payload, null);

        public static Dictionary<string, object> Configure(
            Dictionary<string, object> payload, JobManager.Job job)
            => Mutate(payload, job, "workflow/configure",
                (doc, profile, j) => CoordinationWorkflow.Configure(doc, profile, j),
                WorkflowText.Configure, needsProfile: true);

        public static Dictionary<string, object> Run(Dictionary<string, object> payload)
            => Run(payload, null);

        public static Dictionary<string, object> Run(
            Dictionary<string, object> payload, JobManager.Job job)
            => Mutate(payload, job, "workflow/run",
                (doc, _, j) => CoordinationWorkflow.RunAll(doc, j),
                WorkflowText.RunAll, needsProfile: false);

        public static Dictionary<string, object> GroupLevels(Dictionary<string, object> payload)
            => GroupLevels(payload, null);

        public static Dictionary<string, object> GroupLevels(
            Dictionary<string, object> payload, JobManager.Job job)
            => Mutate(payload, job, "workflow/group_levels",
                (doc, _, j) => CoordinationWorkflow.GroupByLevel(doc, j),
                WorkflowText.GroupByLevel, needsProfile: false);

        public static Dictionary<string, object> Rules(Dictionary<string, object> payload)
            => Rules(payload, null);

        public static Dictionary<string, object> Rules(
            Dictionary<string, object> payload, JobManager.Job job)
            => Mutate(payload, job, "workflow/rules",
                (doc, profile, j) => CoordinationWorkflow.ApplyRules(doc, profile, j),
                WorkflowText.Rules, needsProfile: true);

        /// <summary>
        /// The guards every mutating step shares: fingerprint, idempotency,
        /// and the envelope fields only the transport knows.
        /// </summary>
        private static Dictionary<string, object> Mutate(
            Dictionary<string, object> payload,
            JobManager.Job job,
            string operation,
            Func<Autodesk.Navisworks.Api.Document, ProfileStore.ActiveProfile, JobManager.Job,
                Dictionary<string, object>> work,
            Func<Dictionary<string, object>, string> render,
            bool needsProfile)
        {
            var doc = Router.RequireDocument();
            var fingerprint = DocumentContext.Fingerprint(doc);

            var expected = Json.Str(payload, "expected_document_fingerprint");
            if (!DocumentFingerprint.Matches(expected, fingerprint))
            {
                return new Dictionary<string, object>
                {
                    ["operation"] = operation,
                    ["status"] = "failed",
                    ["error"] = "document_changed",
                    ["document_fingerprint_before"] = fingerprint,
                    ["detail"] = "El documento activo no es el que esperabas (esperado " + expected +
                                 ", activo " + fingerprint + "). No se tocó nada.",
                    ["hint"] = "Vuelve a leer 'health' o 'capabilities' y repite con la huella vigente."
                };
            }

            if (Json.Bool(payload, "dry_run", true))
            {
                var planned = new MutationResult(operation) { DryRun = true,
                    FingerprintBefore = fingerprint, FingerprintAfter = fingerprint };
                var preview = planned.ToJson();
                preview["tests"] = doc.GetClash().TestsData.Tests.OfType<Autodesk.Navisworks.Api.Clash.ClashTest>()
                    .Select(t => new Dictionary<string, object> { ["name"] = t.DisplayName, ["guid"] = t.Guid.ToString() }).ToList();
                preview["planned_scope"] = operation == "workflow/configure" ? "Create or reconcile sets and clash matrix from the active profile" :
                    operation == "workflow/run" ? "Run every test listed; results are unknown until execution" :
                    operation == "workflow/group_levels" ? "Group ungrouped results by level and preserve existing groups" : "Apply the active profile rules";
                if (needsProfile)
                {
                    var plannedProfile = FrozenProfile(job);
                    if (plannedProfile == null && !TryProfile(out plannedProfile, out var problem)) return problem;
                    Attribute(preview, plannedProfile);
                    preview["profile_plan"] = plannedProfile.Content;
                }
                return preview;
            }

            var key = Json.Str(payload, "idempotency_key");
            if (IdempotencyLedger.TryGet(key, operation, fingerprint, out var cached)) return cached;

            ProfileStore.ActiveProfile profile = null;
            if (needsProfile)
            {
                // The profile the job was ACCEPTED under, not the one loaded
                // now. Calling ProfileStore.Active() here was the bug: a job
                // could be admitted under profile A, wait in the queue while
                // the operator loaded profile B, and then run under B while
                // reporting A's checksum. The frozen copy is reparsed from
                // canonical JSON, so it is also isolated from the handlers
                // that write into the dictionary they are handed.
                profile = FrozenProfile(job);
                if (profile == null && !TryProfile(out profile, out var problem)) return problem;
            }

            var result = work(doc, profile, job);
            result["job_id"] = job?.Id ?? string.Empty;
            result["target_id"] = Json.Str(payload, "target_id");
            result["idempotency_key"] = key;
            if (needsProfile) Attribute(result, profile);
            result["session_id"] = job?.SessionId ?? string.Empty;
            result["text"] = render(result);

            IdempotencyLedger.Remember(key, result);
            return result;
        }

        /// <summary>
        /// The profile this job was admitted with, rebuilt from its snapshot.
        /// </summary>
        /// <remarks>
        /// Rebuilt rather than referenced. A reference would still point at a
        /// live dictionary, and configure writes into the profile section it
        /// is given — so two jobs sharing one reference is not hypothetical,
        /// and neither is a job seeing edits made by the one before it.
        ///
        /// Null when there is no job (the synchronous path) or no snapshot,
        /// and the caller falls back to the active profile — which is correct
        /// there, because a synchronous call runs immediately.
        /// </remarks>
        private static ProfileStore.ActiveProfile FrozenProfile(JobManager.Job job)
        {
            var canonical = job?.Request?.ProfileCanonical;
            if (string.IsNullOrWhiteSpace(canonical)) return null;

            Dictionary<string, object> content;
            try { content = Json.ParseObject(canonical); }
            catch (FormatException) { return null; }
            if (content == null) return null;

            return new ProfileStore.ActiveProfile
            {
                Content = content,
                Canonical = canonical,
                Checksum = job.Request.ProfileChecksum,
                Name = Json.Str(content, "profile_name"),
                Schema = Json.Str(content, "schema"),
                Source = "frozen_at_submit",
                LoadedUtc = job.Request.SubmittedUtc
            };
        }

        /// <summary>
        /// Stamps the result with the criteria that produced it.
        /// </summary>
        /// <remarks>
        /// <c>profile_checksum</c> rather than only <c>profile_path</c>,
        /// because a path answers "which file" and the caller's real question
        /// is "the profile I loaded, or another one?" — and for a pushed
        /// profile there is no file to name at all. The path is still
        /// reported when there is one, so a default that came off the disk
        /// says where from.
        /// </remarks>
        private static void Attribute(
            Dictionary<string, object> result, ProfileStore.ActiveProfile profile)
        {
            if (result == null || profile == null) return;
            result["profile_checksum"] = profile.Checksum;
            result["profile_name"] = profile.Name;
            result["profile_source"] = profile.Source;
            result["profile_path"] = profile.Path ?? string.Empty;
        }

        private static bool TryProfile(
            out ProfileStore.ActiveProfile profile, out Dictionary<string, object> problem)
        {
            profile = ProfileStore.Active();
            if (profile != null)
            {
                problem = null;
                return true;
            }
            problem = new Dictionary<string, object>
            {
                ["error"] = "profile_not_found",
                ["status"] = "failed",
                ["detail"] = ProfileLocator.NotFoundMessage().Replace("\n", " "),
                ["hint"] = "Cárgalo con navis_load_profile: viaja por el puerto y no necesita " +
                           "existir en el disco de esta máquina.",
                ["searched"] = new List<object>
                {
                    "(perfil enviado por MCP)",
                    "%" + ProfileLocator.EnvVar + "%",
                    Path.Combine(SessionStore.Root(), ProfileLocator.FileName),
                    "(junto al DLL del complemento)"
                }
            };
            return false;
        }

        /// <summary>Installs the profile the MCP server pushed.</summary>
        public static Dictionary<string, object> ProfileLoad(Dictionary<string, object> payload)
            => ProfileStore.Load(payload);

        /// <summary>Drops it, so the on-disk default applies again.</summary>
        public static Dictionary<string, object> ProfileReset(Dictionary<string, object> payload)
            => ProfileStore.Reset();

        /// <summary>
        /// Describes the profile actually in force for this session.
        /// </summary>
        /// <remarks>
        /// It used to take a <c>path</c> from the caller and read whatever
        /// file it named, which made an authenticated route into a
        /// general-purpose file reader running inside Navisworks — and it
        /// answered about a file rather than about what the next step would
        /// really use, which is the question being asked.
        /// </remarks>
        public static Dictionary<string, object> ProfileInfo(Dictionary<string, object> payload)
            => ProfileStore.Describe();
    }
}
