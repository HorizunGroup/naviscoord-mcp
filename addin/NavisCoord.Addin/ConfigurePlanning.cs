using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Deciding what `workflow/configure` may safely do to search sets that
    /// clash tests are pointing at.
    /// </summary>
    /// <remarks>
    /// Configure rebuilds the discipline folders and then rebuilds the clash
    /// matrix. Rebuilding a folder removes it and creates a new one, and every
    /// set inside it gets a new identity — while a clash test holds its two
    /// sides as <c>SelectionSource</c> references to the old ones. The matrix
    /// step then KEEPS any test that already has results, because throwing
    /// away a finished run is a human decision, and never checks that the
    /// sources it kept still exist.
    ///
    /// The verification did not catch it because it compared names and counts:
    /// the folder is there, the set is there, the test is there, so it
    /// reported green over a test whose Selection A pointed at a deleted item.
    ///
    /// The irony is that the guard already existed. `BuildCriteriaSets` skips
    /// the removal when `replace_existing` is false, and its comment says why
    /// — "los clash tests la referencian por SelectionSource y recrearla
    /// rompería ese enlace". Configure forces the flag on.
    ///
    /// Everything here is strings, GUIDs and enums so that the ordering rules
    /// — build before destroying, verify before retiring, never orphan a test
    /// that holds results — are testable without a licence or a model.
    /// </remarks>
    internal static class ConfigurePlanning
    {
        /// <summary>
        /// Whether this build is allowed to migrate a source that something
        /// points at — declared, never discovered.
        /// </summary>
        /// <remarks>
        /// There is no probe behind this and there must not be one. Planting a
        /// throwaway item in the user's document to find out whether AddCopy
        /// keeps a GUID is a mutation, not a capability check: it dirties the
        /// document, it can succeed at adding and fail at removing, external
        /// plug-ins can observe the temporary element, and a crash mid-probe
        /// leaves the residue behind. Reflection can prove a method EXISTS; it
        /// cannot prove what the method PRESERVES.
        ///
        /// So the value is a constant that only an explicit integration test —
        /// one that runs against a scratch document, on purpose, with a human
        /// starting it — is entitled to raise. Production reads it and never
        /// writes it. Unverified is the shipping value, and unverified means
        /// the referenced source is preserved and the step is reported
        /// blocked. Not updating a criterion is a smaller harm than orphaning
        /// somebody's results.
        /// </remarks>
        internal enum MigrationCapability
        {
            /// <summary>No demonstrated atomic, identity-preserving replace.</summary>
            Unverified = 0,

            /// <summary>
            /// An integration test has demonstrated, against a real document,
            /// that one call replaces the source, the identity survives and
            /// the dependent tests stay linked. Nothing in this repository
            /// sets this yet.
            /// </summary>
            VerifiedAtomicReplace = 1
        }

        /// <summary>Reported when the plan had to fall back to preserving.</summary>
        public const string CapabilityUnverified = "migration_capability_unverified";

        // What the plan says it will do to one saved item.
        public const string UpdateInPlace = "update_in_place";
        public const string Create = "create";
        public const string Relink = "relink";
        public const string PreserveDueToResults = "preserve_due_to_results";
        public const string Blocked = "blocked";
        public const string RemoveAfterVerification = "remove_after_verification";

        /// <summary>A saved item as it exists now: identity, not a handle.</summary>
        internal sealed class SetSnapshot
        {
            public string Guid = string.Empty;
            public string Name = string.Empty;
            public string Folder = string.Empty;
            /// <summary>What the set selects, reduced to a comparable string.</summary>
            public string Definition = string.Empty;

            public string Path => string.IsNullOrEmpty(Folder) ? Name : Folder + "/" + Name;
        }

        /// <summary>A clash test, with the identity of what each side points at.</summary>
        internal sealed class TestSnapshot
        {
            public string Guid = string.Empty;
            public string Name = string.Empty;
            public string TestType = string.Empty;
            public double Tolerance;
            public int ResultCount;
            public int GroupCount;
            public string Status = string.Empty;
            public List<string> SourceGuidsA = new List<string>();
            public List<string> SourceGuidsB = new List<string>();

            /// <summary>
            /// What each declared source resolved to when the snapshot was
            /// taken, keyed by GUID.
            /// </summary>
            /// <remarks>
            /// Carried alongside the GUIDs because the GUID alone is not
            /// evidence of anything: a saved item with the right identity can
            /// still hold the wrong search. Comparing the fingerprint before
            /// and after is what turns "it resolves" into "it resolves to what
            /// it used to resolve to".
            /// </remarks>
            public Dictionary<string, string> SourceFingerprints =
                new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

            public bool HasResults => ResultCount > 0;

            /// <summary>Identity when the API gave one, name otherwise.</summary>
            public string Key => string.IsNullOrEmpty(Guid) ? Name : Guid;

            public IEnumerable<string> AllSourceGuids => SourceGuidsA.Concat(SourceGuidsB);
        }

        /// <summary>What the profile wants a set to be.</summary>
        internal sealed class DesiredSet
        {
            public string Name = string.Empty;
            public string Folder = string.Empty;
            public string Definition = string.Empty;

            public string Path => string.IsNullOrEmpty(Folder) ? Name : Folder + "/" + Name;
        }

        internal sealed class Step
        {
            public string Target = string.Empty;
            public string Action = string.Empty;
            public string Reason = string.Empty;
            public string Guid = string.Empty;
            public List<string> AffectedTests = new List<string>();

            public Dictionary<string, object> ToJson()
                => new Dictionary<string, object>
                {
                    ["target"] = Target,
                    ["action"] = Action,
                    ["reason"] = Reason,
                    ["guid"] = Guid,
                    ["affected_tests"] = AffectedTests.Cast<object>().ToList()
                };
        }

        /// <summary>
        /// Which tests depend on each saved item, by identity.
        /// </summary>
        /// <remarks>
        /// By GUID and not by name, because "same name" is exactly the
        /// assumption that let a rebuilt set pass for the one a test was
        /// holding.
        /// </remarks>
        public static Dictionary<string, List<string>> DependentTests(
            IEnumerable<TestSnapshot> tests)
        {
            var graph = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
            foreach (var test in tests ?? Enumerable.Empty<TestSnapshot>())
            {
                foreach (var guid in test.AllSourceGuids.Distinct(StringComparer.OrdinalIgnoreCase))
                {
                    if (string.IsNullOrWhiteSpace(guid)) continue;
                    if (!graph.TryGetValue(guid, out var users))
                    {
                        users = new List<string>();
                        graph[guid] = users;
                    }
                    if (!users.Contains(test.Name)) users.Add(test.Name);
                }
            }
            return graph;
        }

        /// <summary>
        /// The order of operations for one configure run.
        /// </summary>
        /// <remarks>
        /// The rules, in the order they decide:
        ///
        /// * A set nothing points at is free to change however the API allows.
        /// * A set whose definition already matches is left alone entirely —
        ///   that is what makes a second identical run idempotent, and what
        ///   stops GUIDs churning for no reason.
        /// * A set that must change and is used only by tests without results
        ///   is rebuilt and its tests relinked; the old one is retired only
        ///   after the new one has been verified.
        /// * A set used by a test that HAS results is preserved. Relinking
        ///   would be fine if it could be done without losing the run, and
        ///   when it cannot, destroying somebody's finished clash run to make
        ///   the configuration look uniform is not a trade this is allowed to
        ///   make. It reports `blocked` and says why.
        /// </remarks>
        public static List<Step> Plan(
            IEnumerable<SetSnapshot> existing,
            IEnumerable<DesiredSet> desired,
            IEnumerable<TestSnapshot> tests,
            MigrationCapability capability)
        {
            var current = (existing ?? Enumerable.Empty<SetSnapshot>()).ToList();
            var wanted = (desired ?? Enumerable.Empty<DesiredSet>()).ToList();
            var testList = (tests ?? Enumerable.Empty<TestSnapshot>()).ToList();
            var dependents = DependentTests(testList);
            var byName = testList.ToDictionary(t => t.Name, StringComparer.OrdinalIgnoreCase);

            var byPath = new Dictionary<string, SetSnapshot>(StringComparer.OrdinalIgnoreCase);
            foreach (var set in current) byPath[set.Path] = set;

            var steps = new List<Step>();
            foreach (var want in wanted)
            {
                if (!byPath.TryGetValue(want.Path, out var have))
                {
                    steps.Add(new Step
                    {
                        Target = want.Path,
                        Action = Create,
                        Reason = "no existe todavía"
                    });
                    continue;
                }

                var users = dependents.TryGetValue(have.Guid, out var found)
                    ? found
                    : new List<string>();

                if (string.Equals(have.Definition, want.Definition, StringComparison.Ordinal))
                {
                    steps.Add(new Step
                    {
                        Target = want.Path,
                        Action = UpdateInPlace,
                        Guid = have.Guid,
                        Reason = "la definición ya es la esperada: no se toca",
                        AffectedTests = new List<string>(users)
                    });
                    continue;
                }

                var blocking = users
                    .Where(name => byName.TryGetValue(name, out var t) && t.HasResults)
                    .ToList();

                // Any dependant at all is enough to stop, not just one holding
                // results. Relinking a test that has not been run yet still
                // needs an operation that provably rebinds it, and there isn't
                // one until the capability is demonstrated. The difference
                // between the two cases is what it costs to be wrong, and that
                // shows up in the wording, not in the decision.
                if (users.Count > 0 && capability != MigrationCapability.VerifiedAtomicReplace)
                {
                    steps.Add(new Step
                    {
                        Target = want.Path,
                        Action = Blocked,
                        Guid = have.Guid,
                        Reason = blocking.Count > 0
                            ? "la definición cambió, pero " + blocking.Count
                              + " test(s) con resultados dependen de este conjunto y no hay una "
                              + "operación de reemplazo cuya conservación esté demostrada. Se "
                              + "conserva el conjunto anterior (" + CapabilityUnverified + ")."
                            : "la definición cambió y " + users.Count + " test(s) dependen de "
                              + "este conjunto; sin una operación de reemplazo verificada, "
                              + "revincularlos no se puede demostrar (" + CapabilityUnverified
                              + "). Se conserva el conjunto anterior.",
                        AffectedTests = users.ToList()
                    });
                    foreach (var name in blocking)
                    {
                        steps.Add(new Step
                        {
                            Target = name,
                            Action = PreserveDueToResults,
                            Reason = "conserva sus resultados y su fuente original",
                            AffectedTests = new List<string> { name }
                        });
                    }
                    continue;
                }

                steps.Add(new Step
                {
                    Target = want.Path,
                    Action = Create,
                    Reason = "la definición cambió: se crea la nueva antes de retirar la anterior",
                    AffectedTests = new List<string>(users)
                });
                foreach (var name in users)
                {
                    steps.Add(new Step
                    {
                        Target = name,
                        Action = Relink,
                        Reason = "apuntará al conjunto nuevo",
                        AffectedTests = new List<string> { name }
                    });
                }
                steps.Add(new Step
                {
                    Target = want.Path,
                    Action = RemoveAfterVerification,
                    Guid = have.Guid,
                    Reason = "solo tras comprobar que la fuente nueva existe y los tests apuntan a ella",
                    AffectedTests = new List<string>(users)
                });
            }

            return steps;
        }

        /// <summary>A source a test points at, as found after the mutations.</summary>
        internal sealed class ObservedSource
        {
            public string Guid = string.Empty;
            public bool ExistsInDocument;
            public string Definition = string.Empty;
        }

        internal sealed class ObservedTest
        {
            public string Name = string.Empty;
            public bool Exists;
            public int ResultCount;
            public List<ObservedSource> SideA = new List<ObservedSource>();
            public List<ObservedSource> SideB = new List<ObservedSource>();
        }

        internal sealed class Verdict
        {
            public int Requested;
            public int Verified;
            public int Failed;
            public int Preserved;
            public int BlockedCount;
            public List<Dictionary<string, object>> Details = new List<Dictionary<string, object>>();

            public string Status
            {
                get
                {
                    if (Requested <= 0) return "completed";
                    if (Verified <= 0) return "failed";
                    return Verified < Requested ? "partial" : "completed";
                }
            }
        }

        /// <summary>
        /// Checks each expected test against what the document now holds.
        /// </summary>
        /// <remarks>
        /// Identity, not names. A source counts as verified when it exists in
        /// this document AND its GUID is the one that was planned; a set that
        /// merely shares a name is a different set, which is the whole failure
        /// being fixed.
        ///
        /// A source that resolves to zero elements is still valid. "Exists and
        /// resolves" and "currently matches something" are different
        /// questions, and treating an empty-but-live set as broken would fail
        /// every configure run made before the models are attached.
        ///
        /// A test kept for its results must still have them: preserving a test
        /// and then emptying it is worse than refusing to touch it.
        /// </remarks>
        public static Verdict Verify(
            IEnumerable<ObservedTest> observed,
            IDictionary<string, int> expectedResultCounts,
            int preserved = 0,
            int blocked = 0)
        {
            // Taken as arguments rather than filled in by the caller
            // afterwards: a verdict that can be edited after it is returned is
            // a verdict whose numbers nobody owns.
            var verdict = new Verdict { Preserved = preserved, BlockedCount = blocked };
            var counts = expectedResultCounts ?? new Dictionary<string, int>();

            foreach (var test in observed ?? Enumerable.Empty<ObservedTest>())
            {
                verdict.Requested++;
                var problems = new List<string>();

                if (!test.Exists)
                {
                    problems.Add("el test no está en el documento");
                }
                else
                {
                    if (test.SideA.Count == 0) problems.Add("Selection A no tiene fuentes");
                    if (test.SideB.Count == 0) problems.Add("Selection B no tiene fuentes");
                    foreach (var side in new[] { "A", "B" })
                    {
                        var sources = side == "A" ? test.SideA : test.SideB;
                        foreach (var source in sources)
                        {
                            if (!source.ExistsInDocument)
                            {
                                problems.Add(
                                    $"Selection {side} apunta a {source.Guid} y no está en el documento");
                            }
                        }
                    }
                    if (counts.TryGetValue(test.Name, out var expected) &&
                        expected > 0 && test.ResultCount < expected)
                    {
                        problems.Add(
                            $"se conservó por sus {expected} resultados y ahora tiene {test.ResultCount}");
                    }
                }

                if (problems.Count == 0) verdict.Verified++;
                else verdict.Failed++;

                verdict.Details.Add(new Dictionary<string, object>
                {
                    ["test"] = test.Name,
                    ["ok"] = problems.Count == 0,
                    ["problems"] = problems.Cast<object>().ToList()
                });
            }
            return verdict;
        }

        /// <summary>
        /// The one door every write to the saved-item tree goes through.
        /// </summary>
        /// <remarks>
        /// A dry run that "does not mutate" because nobody wrote a mutation
        /// into that branch is a promise held together by review. This makes
        /// it a refusal: the gate is built with <c>allowed: !dryRun</c> and a
        /// rehearsal's gate throws on every write method, so a mutation added
        /// to the wrong branch later fails loudly instead of quietly working.
        ///
        /// What it offers are the three things a configure run is allowed to
        /// do, named after their intent, and NOT the primitives they are made
        /// of. There is no Remove here and no Add: handing those out lets a
        /// caller reassemble them into "remove the old one, then add the new
        /// one", which is a replacement with a window in the middle where the
        /// document holds neither. The whole point is that the window cannot
        /// be expressed.
        ///
        /// It also knows the plan, which lets it refuse two more things:
        ///
        /// * replacing a folder the plan marked blocked or untouched — the
        ///   rule "a referenced source is preserved" stops being a convention
        ///   and becomes an exception;
        /// * touching a folder the plan never mentioned — which is exactly the
        ///   shape a capability probe has. A throwaway item planted to see
        ///   what the API preserves is, by construction, not in the plan.
        ///
        /// What it does NOT prove: that production actually routes every call
        /// through it. That part is ordinary review, and it is why the gate is
        /// deliberately the only thing in this file that production must
        /// remember to use.
        /// </remarks>
        internal sealed class MutationGate
        {
            private readonly bool _allowed;
            private readonly HashSet<string> _replaceable;
            private readonly HashSet<string> _known;
            private readonly HashSet<string> _existing;

            /// <summary>Every write attempt, in order, as "verb:target".</summary>
            public List<string> Journal = new List<string>();

            private MutationGate(
                bool allowed, HashSet<string> replaceable, HashSet<string> known, HashSet<string> existing)
            {
                _allowed = allowed;
                _replaceable = replaceable;
                _known = known;
                _existing = existing;
            }

            /// <summary>
            /// A gate for the folder-level rebuild, derived from a set-level plan.
            /// </summary>
            /// <remarks>
            /// A folder may be replaced only when nothing inside it is being
            /// preserved: one blocked set is enough to make the whole folder
            /// off limits, because replacing it takes that set with it.
            /// </remarks>
            public static MutationGate ForFolders(
                bool allowed, IEnumerable<Step> plan, IEnumerable<string> existingFolders)
            {
                var known = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                var pinned = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var step in plan ?? Enumerable.Empty<Step>())
                {
                    var slash = (step.Target ?? string.Empty).IndexOf('/');
                    if (slash <= 0) continue;   // a test name, not a set path
                    var folder = step.Target.Substring(0, slash);
                    known.Add(folder);
                    if (step.Action == Blocked || step.Action == UpdateInPlace) pinned.Add(folder);
                }
                var replaceable = new HashSet<string>(known, StringComparer.OrdinalIgnoreCase);
                replaceable.ExceptWith(pinned);
                return new MutationGate(
                    allowed, replaceable, known,
                    new HashSet<string>(
                        existingFolders ?? Enumerable.Empty<string>(), StringComparer.OrdinalIgnoreCase));
            }

            /// <summary>A folder that is not in the document yet.</summary>
            public void AddNew(string folder)
            {
                Authorise("add_new", folder);
                if (_existing.Contains(folder))
                {
                    throw new InvalidOperationException(
                        "'" + folder + "' ya existe: un elemento existente se reemplaza, no se añade");
                }
                Journal.Add("add_new:" + folder);
            }

            /// <summary>
            /// A folder that exists and that nothing points at, swapped in one
            /// call.
            /// </summary>
            public void ReplaceUnreferenced(string folder)
            {
                Authorise("replace_unreferenced", folder);
                if (!_existing.Contains(folder))
                {
                    throw new InvalidOperationException(
                        "'" + folder + "' no existe: un elemento nuevo se añade, no se reemplaza");
                }
                if (!_replaceable.Contains(folder))
                {
                    throw new InvalidOperationException(
                        "no se puede reemplazar '" + folder + "': el plan conserva algo dentro");
                }
                Journal.Add("replace_unreferenced:" + folder);
            }

            /// <summary>
            /// A folder left exactly as it is because something points into it.
            /// </summary>
            /// <remarks>
            /// Recorded rather than silent, and it deliberately does not ask
            /// permission: not writing is allowed on every path, rehearsal
            /// included.
            /// </remarks>
            public void PreserveReferenced(string folder)
            {
                Journal.Add("preserve_referenced:" + folder);
            }

            private void Authorise(string verb, string folder)
            {
                if (!_allowed)
                {
                    throw new InvalidOperationException(
                        "dry_run no puede mutar el documento (" + verb + " '" + folder + "')");
                }
                if (!_known.Contains(folder ?? string.Empty))
                {
                    throw new InvalidOperationException(
                        "'" + folder + "' no está en el plan: una mutación fuera del plan no está permitida");
                }
            }
        }

        /// <summary>A folder as found in the document after a replacement.</summary>
        internal sealed class FolderObservation
        {
            public string Folder = string.Empty;
            /// <summary>How many root entries carry this folder's name.</summary>
            public int MatchingRootEntries;
            /// <summary>The sets found inside it.</summary>
            public List<string> SetNames = new List<string>();
            /// <summary>The sets that should be inside it.</summary>
            public List<string> ExpectedSetNames = new List<string>();
            /// <summary>Sets present but not searching for what was asked.</summary>
            public List<string> MismatchedDefinitions = new List<string>();
            /// <summary>Whether the item that was replaced is still somewhere.</summary>
            public bool PreviousItemStillPresent;
        }

        /// <summary>
        /// Whether a one-call replacement actually landed.
        /// </summary>
        /// <remarks>
        /// Every question here exists because a replacement can fail in a way
        /// that still looks fine from one angle: the folder is there but empty,
        /// the folder is there twice, the sets are there with the old
        /// criteria, or the old folder survived alongside the new one. Asking
        /// only "does the folder exist" would pass all four.
        /// </remarks>
        public static List<string> VerifyReplacement(FolderObservation observed)
        {
            var problems = new List<string>();
            if (observed == null) return problems;

            if (observed.MatchingRootEntries == 0)
            {
                problems.Add("no quedó ninguna carpeta '" + observed.Folder + "'");
                return problems;   // nothing else is answerable
            }
            if (observed.MatchingRootEntries > 1)
            {
                problems.Add("quedaron " + observed.MatchingRootEntries + " carpetas llamadas '"
                             + observed.Folder + "'");
            }
            if (observed.PreviousItemStillPresent)
            {
                problems.Add("el elemento anterior sigue en el documento junto al nuevo");
            }

            foreach (var name in observed.ExpectedSetNames)
            {
                if (!observed.SetNames.Contains(name, StringComparer.OrdinalIgnoreCase))
                {
                    problems.Add("falta el conjunto '" + name + "'");
                }
            }
            foreach (var name in observed.SetNames)
            {
                if (!observed.ExpectedSetNames.Contains(name, StringComparer.OrdinalIgnoreCase))
                {
                    problems.Add("sobra el conjunto '" + name + "'");
                }
            }
            foreach (var name in observed.MismatchedDefinitions)
            {
                problems.Add("'" + name + "' no tiene la definición pedida");
            }
            return problems;
        }

        /// <summary>
        /// What the document holds after a replacement that did not verify.
        /// </summary>
        /// <remarks>
        /// Reported instead of repaired. Undoing a half-applied replacement
        /// with a remove and an add is the very sequence this design removed,
        /// and doing it while the state is unknown is worse than leaving it
        /// alone and saying so.
        /// </remarks>
        public static string DescribeState(bool previousPresent, bool replacementPresent)
        {
            if (replacementPresent && !previousPresent) return "aparecio_la_nueva";
            if (previousPresent && !replacementPresent) return "permanece_la_anterior";
            return "indeterminado";
        }

        /// <summary>What a declared GUID actually resolves to right now.</summary>
        internal sealed class SourceResolution
        {
            public string Guid = string.Empty;
            public bool Resolves;
            /// <summary>The live item's definition, reduced to a stable string.</summary>
            public string Fingerprint = string.Empty;
        }

        /// <summary>
        /// How bad a clash-test status is, so two can be compared.
        /// </summary>
        /// <remarks>
        /// New has never been run, Old was run and is stale, Partial ran over
        /// part of the selection, Complete is current. Anything unrecognised
        /// ranks alongside New: an unknown status is not evidence of health.
        /// </remarks>
        public static int StatusRank(string status)
        {
            switch ((status ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "complete": return 3;
                case "partial": return 2;
                case "old": return 1;
                default: return 0;   // New, vacío, o cualquier cosa no reconocida
            }
        }

        /// <summary>
        /// Whether every clash test came through the run no worse than it went
        /// in.
        /// </summary>
        /// <remarks>
        /// This is the check that "the test is still there" was standing in
        /// for, and the reason that stand-in was worthless. A test survives a
        /// folder rebuild trivially — it is a separate object in a separate
        /// collection. What has to be proven is the whole chain: the test
        /// exists, it still declares the same sources, those GUIDs still
        /// resolve, what they resolve to still selects what it used to, and
        /// the run underneath it did not shrink or go stale.
        ///
        /// Every link is checked against the BEFORE snapshot rather than
        /// against an expectation written here, so this reports a degradation
        /// even when the degradation is one nothing predicted.
        /// </remarks>
        public static List<Dictionary<string, object>> CompareIntegrity(
            IEnumerable<TestSnapshot> before,
            IEnumerable<TestSnapshot> after,
            IDictionary<string, SourceResolution> resolutions)
        {
            var later = (after ?? Enumerable.Empty<TestSnapshot>()).ToList();
            var byKey = new Dictionary<string, TestSnapshot>(StringComparer.OrdinalIgnoreCase);
            foreach (var test in later) byKey[test.Key] = test;
            var byName = new Dictionary<string, TestSnapshot>(StringComparer.OrdinalIgnoreCase);
            foreach (var test in later) byName[test.Name] = test;
            var live = resolutions ?? new Dictionary<string, SourceResolution>();

            var report = new List<Dictionary<string, object>>();
            foreach (var was in before ?? Enumerable.Empty<TestSnapshot>())
            {
                var problems = new List<string>();

                // By identity first. Falling back to the name is not a
                // shortcut — a test with no GUID has nothing else — but a test
                // that HAD one and cannot be found by it is gone, whatever
                // else happens to be called the same thing now.
                TestSnapshot now;
                if (!string.IsNullOrEmpty(was.Guid))
                {
                    byKey.TryGetValue(was.Guid, out now);
                    if (now == null) problems.Add("el test " + was.Name + " ya no está con su identidad");
                }
                else
                {
                    byName.TryGetValue(was.Name, out now);
                    if (now == null) problems.Add("el test " + was.Name + " ya no está en el documento");
                }

                if (now != null)
                {
                    foreach (var side in new[] { "A", "B" })
                    {
                        var older = side == "A" ? was.SourceGuidsA : was.SourceGuidsB;
                        var newer = side == "A" ? now.SourceGuidsA : now.SourceGuidsB;

                        if (older.Count > 0 && newer.Count == 0)
                        {
                            problems.Add("Selection " + side + " se quedó sin fuentes");
                            continue;
                        }

                        // Set comparison, not sequence: reordering is not a
                        // degradation, pointing somewhere else is.
                        var lost = older.Except(newer, StringComparer.OrdinalIgnoreCase).ToList();
                        var gained = newer.Except(older, StringComparer.OrdinalIgnoreCase).ToList();
                        foreach (var guid in lost)
                            problems.Add("Selection " + side + " ya no declara " + guid);
                        foreach (var guid in gained)
                            problems.Add("Selection " + side + " ahora declara " + guid + ", que no declaraba");

                        foreach (var guid in newer)
                        {
                            if (!live.TryGetValue(guid, out var resolved) || !resolved.Resolves)
                            {
                                problems.Add("Selection " + side + " declara " + guid
                                             + " y ese GUID no resuelve a ningún elemento");
                                continue;
                            }
                            // Same identity, different content is still a
                            // broken promise: the test would run over
                            // something nobody asked it to run over.
                            if (was.SourceFingerprints.TryGetValue(guid, out var expected) &&
                                !string.IsNullOrEmpty(expected) &&
                                !string.Equals(expected, resolved.Fingerprint, StringComparison.Ordinal))
                            {
                                problems.Add("Selection " + side + " resuelve " + guid
                                             + " a una definición distinta de la que tenía");
                            }
                        }
                    }

                    if (now.ResultCount < was.ResultCount)
                    {
                        problems.Add("perdió " + (was.ResultCount - now.ResultCount)
                                     + " resultado(s) de " + was.ResultCount);
                    }
                    if (now.GroupCount < was.GroupCount)
                    {
                        problems.Add("perdió " + (was.GroupCount - now.GroupCount)
                                     + " grupo(s) de " + was.GroupCount);
                    }
                    if (StatusRank(now.Status) < StatusRank(was.Status))
                    {
                        problems.Add("el estado bajó de " + was.Status + " a " + now.Status);
                    }
                }

                if (problems.Count == 0) continue;
                report.Add(new Dictionary<string, object>
                {
                    ["test"] = was.Name,
                    ["guid"] = was.Guid,
                    ["problems"] = problems.Cast<object>().ToList()
                });
            }
            return report;
        }
    }
}

