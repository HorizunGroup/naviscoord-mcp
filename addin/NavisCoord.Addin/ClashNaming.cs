using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;

namespace NavisCoord
{
    /// <summary>
    /// Which clash names may be replaced, and the plan for clash/rename.
    /// </summary>
    /// <remarks>
    /// The rule the whole operation rests on: a name the coordinator typed
    /// is never touched. Only Navisworks' defaults («Clash 1», «Conflicto1»)
    /// are replaced, and NavisCoord's own earlier names only when the caller
    /// says so. Decided here, from names read once, with no Autodesk type in
    /// sight — so every case is tested without a licence and the live handler
    /// only executes what this returns.
    /// </remarks>
    internal static class ClashNaming
    {
        /// <summary>Longest name written; the Results column is narrow anyway.</summary>
        public const int MaxLength = 120;

        public const string ReasonCoordinatorName = "nombre_del_coordinador";
        public const string ReasonOwnNameProtected = "nombre_de_naviscoord_sin_permiso";
        public const string ReasonCoordinatorGroup = "en_grupo_del_coordinador";
        public const string ReasonOwnGroupProtected = "en_grupo_de_naviscoord_sin_permiso";

        private static readonly Regex Default = new Regex(
            @"^(Clash|Conflicto)\s*\d+$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

        // «001.1» (a child), «001 · …» (a label), «014 · Losa vs Ducto» (the
        // rules workflow). Three digits at least: «1.1» is somebody's note.
        private static readonly Regex OwnResult = new Regex(
            @"^(\d{3,}\.\d+|\d{3,} · .*)$", RegexOptions.CultureInvariant);

        // «001 · …» and the older «[CRITICAL] ISS-… » from navis_apply_groups.
        // Level groups («Nivel 03») are deliberately NOT claimed: nothing
        // tells one written by the workflow from one the coordinator typed.
        private static readonly Regex OwnGroup = new Regex(
            @"^(\d{3,} · |\[(critical|high|medium|low)\]\s+ISS-)",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

        public static bool IsDefault(string name) => Default.IsMatch((name ?? string.Empty).Trim());

        public static bool IsOwnResultName(string name) => OwnResult.IsMatch((name ?? string.Empty).Trim());

        public static bool IsOwnGroupName(string name) => OwnGroup.IsMatch((name ?? string.Empty).Trim());

        public static string Fit(string name) => TextLimits.Truncate((name ?? string.Empty).Trim(), MaxLength);

        /// <summary>One result as read from the document, before anything moved.</summary>
        internal sealed class ResultSnapshot
        {
            public string Guid;
            public string Test;
            public string Name;
            /// <summary>Name of the group holding it; empty when loose under the test.</summary>
            public string Parent = string.Empty;
        }

        /// <summary>One numbered problem, as the server proposes it.</summary>
        internal sealed class IssueSpec
        {
            public string Name;
            public List<string> Guids = new List<string>();
            public List<string> Names = new List<string>();
        }

        internal sealed class GroupStep
        {
            public string Test;
            public string Name;
            public List<string> Moves = new List<string>();
        }

        internal sealed class RenameStep
        {
            public string Guid;
            public string Test;
            public string From;
            public string To;
        }

        /// <summary>A result taken out of an old NavisCoord group, back to the test.</summary>
        internal sealed class LoosenStep
        {
            public string Guid;
            public string Test;
            public string From;
        }

        internal sealed class Skip
        {
            public string Guid;
            public string Name;
            public string Reason;
        }

        internal sealed class RenamePlan
        {
            public List<GroupStep> Groups = new List<GroupStep>();
            public List<RenameStep> Renames = new List<RenameStep>();
            public List<LoosenStep> Loosen = new List<LoosenStep>();
            public List<Skip> Skipped = new List<Skip>();
            public int Missing;
            public int AlreadyNamed;
            public int Found;
        }

        public static RenamePlan Plan(
            IEnumerable<IssueSpec> issues,
            IDictionary<string, ResultSnapshot> document,
            bool overwriteOwn)
        {
            var plan = new RenamePlan();
            if (issues == null || document == null) return plan;

            var groups = new Dictionary<string, GroupStep>(StringComparer.Ordinal);
            foreach (var issue in issues)
            {
                if (issue == null || string.IsNullOrWhiteSpace(issue.Name)) continue;

                var found = new List<Tuple<ResultSnapshot, string>>();
                for (var k = 0; k < issue.Guids.Count; k++)
                {
                    var guid = issue.Guids[k];
                    if (string.IsNullOrWhiteSpace(guid) || !document.TryGetValue(guid, out var snap))
                    {
                        plan.Missing++;
                        continue;
                    }
                    var wanted = k < issue.Names.Count && !string.IsNullOrWhiteSpace(issue.Names[k])
                        ? issue.Names[k]
                        : issue.Name;
                    found.Add(Tuple.Create(snap, Fit(wanted)));
                }
                plan.Found += found.Count;
                if (found.Count == 0) continue;

                var tests = found.Select(f => f.Item1.Test)
                    .Distinct(StringComparer.OrdinalIgnoreCase).ToList();
                var grouped = found.Count >= 2;

                foreach (var entry in found)
                {
                    var snap = entry.Item1;
                    var parent = snap.Parent ?? string.Empty;

                    if (grouped)
                    {
                        // A Navisworks group lives inside one test, so a
                        // problem spread over two becomes two groups that
                        // say which test each is.
                        var target = Fit(tests.Count > 1 ? issue.Name.Trim() + " · " + snap.Test : issue.Name);
                        if (!string.Equals(parent, target, StringComparison.Ordinal))
                        {
                            if (parent.Length == 0 || (overwriteOwn && IsOwnGroupName(parent)))
                            {
                                var key = snap.Test + "\u0000" + target;
                                if (!groups.TryGetValue(key, out var step))
                                {
                                    step = new GroupStep { Test = snap.Test, Name = target };
                                    groups[key] = step;
                                    plan.Groups.Add(step);
                                }
                                step.Moves.Add(snap.Guid);
                            }
                            else
                            {
                                plan.Skipped.Add(new Skip
                                {
                                    Guid = snap.Guid,
                                    Name = parent,
                                    Reason = IsOwnGroupName(parent) ? ReasonOwnGroupProtected : ReasonCoordinatorGroup
                                });
                            }
                        }
                    }
                    else if (parent.Length > 0 && IsOwnGroupName(parent))
                    {
                        // One clash needs no group. Left inside an old one it
                        // would keep that group's stale label above it.
                        if (overwriteOwn)
                        {
                            plan.Loosen.Add(new LoosenStep { Guid = snap.Guid, Test = snap.Test, From = parent });
                        }
                        else
                        {
                            plan.Skipped.Add(new Skip { Guid = snap.Guid, Name = parent, Reason = ReasonOwnGroupProtected });
                        }
                    }

                    var current = snap.Name ?? string.Empty;
                    var wanted = entry.Item2;
                    if (string.Equals(current, wanted, StringComparison.Ordinal))
                    {
                        plan.AlreadyNamed++;
                    }
                    else if (IsDefault(current) || (overwriteOwn && IsOwnResultName(current)))
                    {
                        plan.Renames.Add(new RenameStep { Guid = snap.Guid, Test = snap.Test, From = current, To = wanted });
                    }
                    else
                    {
                        plan.Skipped.Add(new Skip
                        {
                            Guid = snap.Guid,
                            Name = current,
                            Reason = IsOwnResultName(current) ? ReasonOwnNameProtected : ReasonCoordinatorName
                        });
                    }
                }
            }

            // A group with nothing to move into it is not created: either
            // everything is already inside, or nothing may go in.
            plan.Groups.RemoveAll(g => g.Moves.Count == 0);
            return plan;
        }
    }
}
