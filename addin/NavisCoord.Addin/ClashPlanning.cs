using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Deciding what to do to the clash tree, using nothing that can go stale.
    /// </summary>
    /// <remarks>
    /// Every wrapper the Navisworks API hands back — a <c>ClashResult</c>, a
    /// <c>ClashTest</c>, a <c>SavedItem</c> — is a handle into a tree that the
    /// API rebuilds whenever it is edited. Adding a group, moving a result or
    /// changing a status can invalidate every handle taken before it, and
    /// touching one afterwards does not throw a managed exception: it takes
    /// the process down.
    ///
    /// So a plan is made from data that cannot rot — GUID strings, test names,
    /// group names — and every handle is resolved again immediately before the
    /// edit that uses it, and never carried into the next iteration. This file
    /// holds the half of that arrangement which needs no live document, which
    /// is also the half worth testing: the ordering, the grouping and the
    /// splitting are where the mistakes are, and none of them need a licence.
    /// </remarks>
    internal static class ClashPlanning
    {
        /// <summary>One group to build: whose test, what name, which results.</summary>
        internal sealed class GroupPlan
        {
            public string Issue = string.Empty;
            public string GroupName = string.Empty;
            public string TestName = string.Empty;
            public List<string> Guids = new List<string>();
        }

        /// <summary>
        /// Names of the tests owning these results, ordered and deduplicated.
        /// </summary>
        /// <remarks>
        /// Takes a GUID→test-name map rather than an index of live results,
        /// which is the whole point: the previous version read
        /// <c>index[guid].Test.DisplayName</c> off wrappers captured before
        /// the first move, so the second issue in a batch asked a dead handle
        /// which test it belonged to.
        /// </remarks>
        public static List<string> OwningTests(
            IEnumerable<string> guids, IDictionary<string, string> testByGuid)
        {
            if (guids == null || testByGuid == null) return new List<string>();
            return guids
                .Where(g => g != null && testByGuid.ContainsKey(g))
                .Select(g => testByGuid[g] ?? string.Empty)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(n => n, StringComparer.OrdinalIgnoreCase)
                .ToList();
        }

        /// <summary>
        /// Splits one issue into a group per owning test.
        /// </summary>
        /// <remarks>
        /// A Navisworks clash group lives under exactly one test, so an issue
        /// whose results span several becomes several groups. The name is only
        /// suffixed when it actually spans, so the common case stays readable.
        /// </remarks>
        public static List<GroupPlan> PlanIssue(
            string issue, IEnumerable<string> guids, IDictionary<string, string> testByGuid)
        {
            var plans = new List<GroupPlan>();
            if (string.IsNullOrWhiteSpace(issue) || guids == null) return plans;

            var wanted = guids.Where(g => !string.IsNullOrWhiteSpace(g)).ToList();
            var owners = OwningTests(wanted, testByGuid);
            if (owners.Count == 0) return plans;

            foreach (var testName in owners)
            {
                var mine = wanted
                    .Where(g => testByGuid.ContainsKey(g) &&
                                string.Equals(testByGuid[g], testName, StringComparison.OrdinalIgnoreCase))
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .ToList();
                if (mine.Count == 0) continue;

                plans.Add(new GroupPlan
                {
                    Issue = issue,
                    GroupName = owners.Count > 1 ? $"{issue} · {testName}" : issue,
                    TestName = testName,
                    Guids = mine
                });
            }
            return plans;
        }

        /// <summary>What an edit-each pass did, and to what it could not.</summary>
        internal sealed class ApplyReport
        {
            public int Applied;
            public List<string> Vanished = new List<string>();
        }

        /// <summary>
        /// Edits every id, resolving each one immediately before touching it.
        /// </summary>
        /// <remarks>
        /// The guarantee this exists for is structural rather than
        /// conventional: the only object <paramref name="edit"/> can receive
        /// is the one <paramref name="resolve"/> returned on this iteration,
        /// so there is no way to write a caller that reuses the previous
        /// handle — which is the mistake that takes Navisworks down, and one
        /// a comment saying "re-resolve first" does not prevent.
        ///
        /// An id that no longer resolves is recorded and skipped. It is never
        /// a reason to fall back on the handle from the plan: that handle is
        /// exactly what became invalid.
        /// </remarks>
        public static ApplyReport ApplyEach<T>(
            IEnumerable<string> ids, Func<string, T> resolve, Action<string, T> edit)
            where T : class
        {
            var report = new ApplyReport();
            if (ids == null || resolve == null || edit == null) return report;

            foreach (var id in ids)
            {
                var fresh = resolve(id);
                if (fresh == null)
                {
                    report.Vanished.Add(id);
                    continue;
                }
                edit(id, fresh);
                report.Applied++;
            }
            return report;
        }

        /// <summary>
        /// The verdict for a batch, from what was verified rather than attempted.
        /// </summary>
        /// <remarks>
        /// A result that vanished between the plan and the edit is not an
        /// error to swallow and not a reason to press on with a stale handle:
        /// it is reported, and it decides the status. `partial` exists because
        /// "eleven of twelve" is a different fact from either "done" or
        /// "failed", and a caller retrying blindly on `failed` would redo the
        /// eleven that worked.
        /// </remarks>
        public static string Outcome(int requested, int verified)
        {
            if (requested <= 0) return "completed";
            if (verified <= 0) return "failed";
            return verified < requested ? "partial" : "completed";
        }
    }
}
