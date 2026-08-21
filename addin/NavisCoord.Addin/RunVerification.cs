using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// What "Run All actually ran" means, decided from clash-test status.
    /// </summary>
    /// <remarks>
    /// The old check was <c>status != "New"</c>. Navisworks has four states,
    /// and three of them are not success:
    ///
    /// * <c>New</c> — never executed.
    /// * <c>Old</c> — executed once, but the selections or the geometry moved
    ///   since, so the results on screen describe a model that no longer
    ///   exists.
    /// * <c>Partial</c> — the run covered part of the selection and stopped.
    /// * <c>Complete</c> — current.
    ///
    /// Treating Old as ran is the worse half of that bug. A test left Old
    /// still shows a result count, so the report is full of clashes, the run
    /// reports green, and every one of those clashes refers to a previous
    /// version of the model. Partial is the same failure with a smaller
    /// number attached.
    ///
    /// Result counts prove nothing in either direction and are never consulted
    /// for the verdict: a correctly executed test between two disciplines that
    /// genuinely do not touch finishes Complete with zero clashes, and a test
    /// abandoned halfway finishes Partial holding hundreds. Counts are carried
    /// through to the report because they are useful to a human, not because
    /// they decide anything.
    ///
    /// Everything here is strings and ints so the rules can be asserted
    /// without a licence, a model, or a running Navisworks.
    /// </remarks>
    internal static class RunVerification
    {
        // The four states, spelled once.
        public const string New = "New";
        public const string Old = "Old";
        public const string Partial = "Partial";
        public const string Complete = "Complete";

        // Per-test outcomes.
        public const string CompletedNow = "completed_now";
        public const string AlreadyComplete = "already_complete";
        public const string StillOld = "old";
        public const string StillPartial = "partial";
        public const string StillNew = "new";
        public const string Missing = "missing";
        public const string Unexpected = "unexpected";
        /// <summary>No stable identity, so nothing could be correlated.</summary>
        public const string UnverifiableIdentity = "unverifiable_identity";
        /// <summary>The identity named more than one object.</summary>
        public const string IdentityConflict = "identity_conflict";

        // Global outcomes.
        public const string StatusCompleted = "completed";
        public const string StatusPartial = "partial";
        public const string StatusFailed = "failed";
        public const string NothingToRun = "nothing_to_run";

        /// <summary>
        /// One clash test reduced to stable values, taken before or after the
        /// run.
        /// </summary>
        /// <remarks>
        /// Identity, name, status and counts — nothing that holds a native
        /// handle. <c>TestsRunAllTests</c> rebuilds the results tree, so a
        /// <c>ClashTest</c> captured before the call is a dangling pointer
        /// afterwards, and dereferencing one does not raise a managed
        /// exception, it takes the process down.
        /// </remarks>
        internal sealed class TestState
        {
            public string Guid = string.Empty;
            public string Name = string.Empty;
            public string Status = string.Empty;
            public int ResultCount;
            public int GroupCount;
            public List<string> SourceGuidsA = new List<string>();
            public List<string> SourceGuidsB = new List<string>();

            /// <summary>
            /// Whether this test can be correlated across the run at all.
            /// </summary>
            /// <remarks>
            /// `ClashTest` inherits `SavedItem.Guid`, a non-nullable
            /// `System.Guid`, in every API this add-in builds against — 2024,
            /// 2025 and 2026 were each inspected and all three declare it on
            /// `SavedItem`. So a missing identity is not a version difference
            /// to be worked around; it is `Guid.Empty`, which means the object
            /// was never registered with the document, and that is a contract
            /// violation rather than a case to be handled leniently.
            /// </remarks>
            public bool HasIdentity
                => !string.IsNullOrWhiteSpace(Guid) &&
                   System.Guid.TryParse(Guid, out var parsed) &&
                   parsed != System.Guid.Empty;

            /// <summary>
            /// The correlation key. Identity or nothing — never the name.
            /// </summary>
            /// <remarks>
            /// This used to fall back to the display name when the GUID was
            /// missing, and that fallback could turn a test that VANISHED into
            /// a test that COMPLETED, by matching it against a different test
            /// that happened to share a name. Navisworks does not stop anybody
            /// naming two tests "ARQ vs EST", does not stop a test being
            /// duplicated, and does not stop one being deleted and recreated
            /// under the same name with a new identity — and in that last case
            /// the name matches perfectly while the object is not the same
            /// object at all.
            ///
            /// Empty means "cannot be correlated", and the classifier reports
            /// that as its own outcome instead of guessing.
            /// </remarks>
            public string Identity => HasIdentity ? Guid : string.Empty;

            public bool IsComplete
                => string.Equals(Status, Complete, StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>How one requested test came out.</summary>
        internal sealed class TestOutcome
        {
            public string Identity = string.Empty;
            /// <summary>The name it had before. Diagnostic only.</summary>
            public string NameBefore = string.Empty;
            /// <summary>The name it has now. Diagnostic only.</summary>
            public string NameAfter = string.Empty;
            public string Outcome = string.Empty;
            public string StatusBefore = string.Empty;
            public string StatusAfter = string.Empty;
            public int ResultsBefore;
            public int ResultsAfter;
            public bool Degraded;
            public bool Renamed;
            public string Note = string.Empty;

            /// <summary>Kept so existing readers still find a name.</summary>
            public string Name => string.IsNullOrEmpty(NameBefore) ? NameAfter : NameBefore;

            public bool Verified
                => Outcome == CompletedNow || Outcome == AlreadyComplete;

            public Dictionary<string, object> ToJson()
                => new Dictionary<string, object>
                {
                    ["name"] = Name,
                    ["name_before"] = NameBefore,
                    ["name_after"] = NameAfter,
                    ["guid"] = Identity,
                    ["outcome"] = Outcome,
                    ["verified"] = Verified,
                    ["status_before"] = StatusBefore,
                    ["status_after"] = StatusAfter,
                    ["results_before"] = (double)ResultsBefore,
                    ["results_after"] = (double)ResultsAfter,
                    ["degraded"] = Degraded,
                    ["renamed"] = Renamed,
                    ["note"] = Note
                };
        }

        /// <summary>The whole run, classified.</summary>
        internal sealed class RunVerdict
        {
            public int Requested;
            public bool InvocationStarted;
            public int CompletedNowCount;
            public int AlreadyCompleteCount;
            public int OldCount;
            public int PartialCount;
            public int NewCount;
            public int MissingCount;
            public int UnexpectedCount;
            /// <summary>Tests that carried no usable identity to match on.</summary>
            public int UnverifiableIdentityCount;
            /// <summary>Tests whose identity was duplicated or ambiguous.</summary>
            public int IdentityConflictCount;
            /// <summary>Matched by identity, but the display name changed.</summary>
            public int RenamedCount;
            /// <summary>A vanished test's name reappeared under a new identity.</summary>
            public int RecreatedSameNameCount;
            public int DuplicateGuidCount;
            public int AmbiguousCandidateCount;
            public List<TestOutcome> Tests = new List<TestOutcome>();
            public List<string> UnexpectedNames = new List<string>();
            public List<Dictionary<string, object>> IdentityConflicts =
                new List<Dictionary<string, object>>();
            public string Status = StatusFailed;

            /// <summary>Complete before or Complete after. Nothing else.</summary>
            public int Verified => CompletedNowCount + AlreadyCompleteCount;

            public int Failed => Math.Max(0, Requested - Verified);

            public Dictionary<string, object> ToJson()
                => new Dictionary<string, object>
                {
                    ["requested"] = (double)Requested,
                    ["invocation_started"] = InvocationStarted,
                    ["completed_now"] = (double)CompletedNowCount,
                    ["already_complete"] = (double)AlreadyCompleteCount,
                    ["old"] = (double)OldCount,
                    ["partial"] = (double)PartialCount,
                    ["new"] = (double)NewCount,
                    ["missing"] = (double)MissingCount,
                    ["unexpected"] = (double)UnexpectedCount,
                    ["unverifiable_identity"] = (double)UnverifiableIdentityCount,
                    ["identity_conflicts"] = IdentityConflicts.Cast<object>().ToList(),
                    ["identity_conflict_count"] = (double)IdentityConflictCount,
                    ["renamed"] = (double)RenamedCount,
                    ["recreated_same_name"] = (double)RecreatedSameNameCount,
                    ["duplicate_guids"] = (double)DuplicateGuidCount,
                    ["ambiguous_candidates"] = (double)AmbiguousCandidateCount,
                    ["unexpected_tests"] = UnexpectedNames.Cast<object>().ToList(),
                    ["verified"] = (double)Verified,
                    ["failed"] = (double)Failed,
                    ["status"] = Status,
                    ["tests"] = Tests.Select(t => (object)t.ToJson()).ToList()
                };
        }

        /// <summary>
        /// Classify a run by comparing two snapshots taken around it.
        /// </summary>
        /// <param name="before">Every test that existed when the run started.</param>
        /// <param name="after">Every test found by re-reading afterwards.</param>
        /// <param name="invocationStarted">
        /// Whether <c>TestsRunAllTests</c> was actually reached. False when the
        /// call threw before doing anything, or was never made.
        /// </param>
        /// <remarks>
        /// Note what is NOT an input: whether the API call threw. An
        /// invocation that returns without an exception is evidence that the
        /// call returned, and nothing else — the verdict comes from re-reading
        /// the tests either way, which is also why a run that threw halfway
        /// still gets classified honestly instead of reported as a total loss.
        ///
        /// And note what is not a correlation key: the display name. Matching
        /// is by identity or it does not happen.
        /// </remarks>
        public static RunVerdict Classify(
            IEnumerable<TestState> before,
            IEnumerable<TestState> after,
            bool invocationStarted)
        {
            var was = (before ?? Enumerable.Empty<TestState>()).ToList();
            var now = (after ?? Enumerable.Empty<TestState>()).ToList();

            var verdict = new RunVerdict
            {
                Requested = was.Count,
                InvocationStarted = invocationStarted
            };

            // Identities that appear more than once, on either side. A GUID
            // that names two objects names neither of them, so every test
            // holding one is a conflict rather than a match.
            var duplicatedBefore = Duplicates(was);
            var duplicatedAfter = Duplicates(now);
            verdict.DuplicateGuidCount = duplicatedBefore.Count + duplicatedAfter.Count;

            var byIdentity = new Dictionary<string, TestState>(StringComparer.OrdinalIgnoreCase);
            foreach (var test in now)
            {
                if (!test.HasIdentity) continue;
                if (duplicatedAfter.Contains(test.Identity)) continue;
                byIdentity[test.Identity] = test;
            }

            var claimed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var missingNames = new List<string>();

            foreach (var start in was)
            {
                var outcome = new TestOutcome
                {
                    Identity = start.Identity,
                    NameBefore = start.Name,
                    StatusBefore = start.Status,
                    ResultsBefore = start.ResultCount
                };

                // ---------------------------------------- identity first
                if (!start.HasIdentity)
                {
                    outcome.Outcome = UnverifiableIdentity;
                    outcome.Note = "el test no expone una identidad estable, así que no se puede " +
                                   "demostrar que el objeto posterior sea el mismo";
                    verdict.UnverifiableIdentityCount++;
                    verdict.IdentityConflicts.Add(Conflict(
                        start.Name, string.Empty, "sin identidad estable antes de correr"));
                    verdict.Tests.Add(outcome);
                    continue;
                }

                if (duplicatedBefore.Contains(start.Identity))
                {
                    outcome.Outcome = IdentityConflict;
                    outcome.Note = "otro test comparte esta identidad antes de correr";
                    verdict.IdentityConflictCount++;
                    verdict.AmbiguousCandidateCount++;
                    verdict.IdentityConflicts.Add(Conflict(
                        start.Name, start.Identity, "GUID duplicado en el estado previo"));
                    verdict.Tests.Add(outcome);
                    continue;
                }

                if (duplicatedAfter.Contains(start.Identity))
                {
                    outcome.Outcome = IdentityConflict;
                    outcome.Note = "más de un test posterior reclama esta identidad";
                    verdict.IdentityConflictCount++;
                    verdict.AmbiguousCandidateCount++;
                    verdict.IdentityConflicts.Add(Conflict(
                        start.Name, start.Identity, "GUID duplicado tras la corrida"));
                    verdict.Tests.Add(outcome);
                    continue;
                }

                if (!byIdentity.TryGetValue(start.Identity, out var end))
                {
                    // Not found BY IDENTITY. A test with the same name is not
                    // the same test, and accepting one here is the bug this
                    // whole classifier exists to prevent.
                    outcome.Outcome = Missing;
                    outcome.Note = "no se pudo re-resolver por identidad tras la corrida";
                    verdict.MissingCount++;
                    missingNames.Add(start.Name);
                    verdict.Tests.Add(outcome);
                    continue;
                }

                claimed.Add(start.Identity);
                outcome.NameAfter = end.Name;
                outcome.StatusAfter = end.Status;
                outcome.ResultsAfter = end.ResultCount;
                if (!string.Equals(start.Name, end.Name, StringComparison.Ordinal))
                {
                    // Correlated correctly, and worth saying: the operator
                    // renamed it, which the report should show rather than
                    // silently absorb.
                    outcome.Renamed = true;
                    verdict.RenamedCount++;
                }

                // ------------------------------------------ then status
                if (end.IsComplete)
                {
                    if (start.IsComplete)
                    {
                        outcome.Outcome = AlreadyComplete;
                        verdict.AlreadyCompleteCount++;
                        if (end.ResultCount == start.ResultCount)
                        {
                            outcome.Note = "sin cambios en los resultados";
                        }
                    }
                    else
                    {
                        outcome.Outcome = CompletedNow;
                        verdict.CompletedNowCount++;
                    }
                    verdict.Tests.Add(outcome);
                    continue;
                }

                if (string.Equals(end.Status, Old, StringComparison.OrdinalIgnoreCase))
                {
                    outcome.Outcome = StillOld;
                    verdict.OldCount++;
                }
                else if (string.Equals(end.Status, Partial, StringComparison.OrdinalIgnoreCase))
                {
                    outcome.Outcome = StillPartial;
                    verdict.PartialCount++;
                }
                else
                {
                    outcome.Outcome = StillNew;
                    verdict.NewCount++;
                }

                if (start.IsComplete)
                {
                    outcome.Degraded = true;
                    outcome.Note = "estaba Complete y quedó " + end.Status;
                }
                else if (end.ResultCount == start.ResultCount && start.ResultCount > 0)
                {
                    outcome.Note = "mismos resultados que antes, y el test no está Complete";
                }

                verdict.Tests.Add(outcome);
            }

            foreach (var extra in now.Where(t => !t.HasIdentity || !claimed.Contains(t.Identity)))
            {
                // Counted and named, never added to verified. A test that
                // appeared out of nowhere does not stand in for one that
                // vanished, however convenient the arithmetic would be — and
                // when it carries a vanished test's NAME, that coincidence is
                // reported as exactly that.
                verdict.UnexpectedCount++;
                verdict.UnexpectedNames.Add(extra.Name);
                if (missingNames.Contains(extra.Name, StringComparer.Ordinal))
                {
                    verdict.RecreatedSameNameCount++;
                    verdict.IdentityConflicts.Add(Conflict(
                        extra.Name, extra.Identity,
                        "reapareció con este nombre pero con otra identidad: no sustituye al que falta"));
                }
            }

            verdict.Status = Decide(verdict);
            return verdict;
        }

        /// <summary>Identities claimed by more than one test.</summary>
        private static HashSet<string> Duplicates(IEnumerable<TestState> tests)
            => new HashSet<string>(
                tests.Where(t => t.HasIdentity)
                    .GroupBy(t => t.Identity, StringComparer.OrdinalIgnoreCase)
                    .Where(g => g.Count() > 1)
                    .Select(g => g.Key),
                StringComparer.OrdinalIgnoreCase);

        private static Dictionary<string, object> Conflict(string name, string guid, string reason)
            => new Dictionary<string, object>
            {
                ["name"] = name ?? string.Empty,
                ["guid"] = guid ?? string.Empty,
                ["reason"] = reason
            };

        /// <summary>The global verdict, from the per-test counts alone.</summary>
        private static string Decide(RunVerdict verdict)
        {
            if (verdict.Requested == 0)
            {
                // Nothing was asked for, so nothing ran. Saying "completed"
                // here is how an empty document reports a successful clash
                // run, which is the single most misleading answer this route
                // can give.
                return NothingToRun;
            }
            // Unverifiable identity and identity conflicts are already outside
            // `Verified`, so they cannot reach the completed branch — this
            // says it out loud rather than relying on the arithmetic.
            if (verdict.UnverifiableIdentityCount > 0 || verdict.IdentityConflictCount > 0)
            {
                return verdict.Verified > 0 ? StatusPartial : StatusFailed;
            }
            if (verdict.Verified == verdict.Requested) return StatusCompleted;
            if (verdict.Verified > 0) return StatusPartial;
            return StatusFailed;
        }
    }
}
