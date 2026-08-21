using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Clash;

namespace NavisCoord
{
    /// <summary>
    /// The four coordination steps, as services rather than button handlers.
    /// </summary>
    /// <remarks>
    /// This is the parity fix. The steps used to exist only as ribbon
    /// buttons that built a block of Spanish prose and showed it in a dialog,
    /// so the MCP server could not run them at all — it had only the
    /// lower-level routes and would have had to re-implement the sequencing,
    /// the level naming and the repeat detection in Python. Two
    /// implementations of the same judgement drift, and the one nobody
    /// clicks drifts silently.
    ///
    /// So the logic returns DATA. The buttons render that data to prose
    /// (<see cref="WorkflowText"/>); the HTTP routes serialise it. Neither
    /// owns it. Every mutating step returns the standard mutation envelope,
    /// with counts that come from re-reading the document.
    ///
    /// Each method takes an optional job so a caller that submitted it
    /// asynchronously gets real phases and, where the step loops over units,
    /// real progress.
    /// </remarks>
    internal static class CoordinationWorkflow
    {
        // --------------------------------------------------- 0. audit models

        /// <summary>
        /// Step 0: are the attached models actually in the same place, and is
        /// each published view showing only its own discipline?
        /// </summary>
        /// <remarks>
        /// A model published without the right shared coordinates sits
        /// kilometres from the rest and every test against it returns zero —
        /// the most dangerous false green in coordination, because it looks
        /// like a clean project. Two models count as co-located when their
        /// bounding boxes overlap with five metres of slack.
        /// </remarks>
        public static Dictionary<string, object> AuditModels(
            Document doc, ProfileStore.ActiveProfile active, JobManager.Job job = null)
        {
            var scale = NavisContext.MetreScale(doc);
            if (scale == 0) scale = 1;

            // The discipline codes come from the profile, not from this
            // assembly: they are the project's BEP vocabulary and no two
            // organisations share one. An unreadable profile yields the empty
            // vocabulary, so every model reports discipline "?" rather than
            // being silently filed under a code nobody configured.
            var vocabulary = DisciplineVocabulary.FromProfile(active?.Content);

            var names = new List<string>();
            var disciplines = new List<string>();
            var boxes = new List<BoundingBox3D>();
            var counts = new List<int>();
            var tallies = new List<Dictionary<string, int>>();
            var categoryNames = new Dictionary<string, string>(StringComparer.Ordinal);

            var total = doc.Models.Count;
            for (var index = 0; index < total; index++)
            {
                if (job != null && job.CancelRequested) break;
                job?.Progress("censando modelos", index, total,
                    "Leyendo categorías del modelo " + (index + 1) + " de " + total);

                var model = doc.Models[index];
                var name = model.RootItem?.DisplayName;
                if (string.IsNullOrWhiteSpace(name)) name = model.SourceFileName ?? "(sin nombre)";
                name = ModelNames.Decode(name);

                var discipline = vocabulary.TokenIn(name);
                var tally = new Dictionary<string, int>(StringComparer.Ordinal);
                var count = 0;

                if (model.RootItem != null)
                {
                    foreach (var item in model.RootItem.Descendants)
                    {
                        count++;
                        if (item.Children.Any()) continue;
                        var id = NavisContext.PropertyOf(item, "CategoryId");
                        if (string.IsNullOrWhiteSpace(id)) continue;
                        tally[id] = tally.TryGetValue(id, out var c) ? c + 1 : 1;
                        if (!categoryNames.ContainsKey(id))
                        {
                            var display = NavisContext.PropertyOf(item, "Category");
                            if (!string.IsNullOrWhiteSpace(display))
                            {
                                categoryNames[id] = display.StartsWith("Revit ", StringComparison.OrdinalIgnoreCase)
                                    ? display.Substring(6)
                                    : display;
                            }
                        }
                    }
                }

                names.Add(name);
                disciplines.Add(discipline);
                boxes.Add(model.RootItem?.BoundingBox());
                counts.Add(count);
                tallies.Add(tally);
            }

            job?.Phasing("evaluando ubicación");

            var models = new List<object>();
            var misplaced = new List<object>();
            var withoutDiscipline = new List<object>();

            for (var i = 0; i < names.Count; i++)
            {
                if (disciplines[i] == "?") withoutDiscipline.Add(names[i]);

                var status = "sin geometría";
                var colocated = false;
                var distance = 0.0;

                if (boxes[i] != null && !boxes[i].IsEmpty)
                {
                    var nearest = double.MaxValue;
                    for (var j = 0; j < names.Count; j++)
                    {
                        if (j == i || boxes[j] == null || boxes[j].IsEmpty) continue;
                        if (Overlaps(boxes[i], boxes[j], 5.0 / scale))
                        {
                            colocated = true;
                            break;
                        }
                        var dx = (boxes[i].Min.X + boxes[i].Max.X - boxes[j].Min.X - boxes[j].Max.X) / 2;
                        var dy = (boxes[i].Min.Y + boxes[i].Max.Y - boxes[j].Min.Y - boxes[j].Max.Y) / 2;
                        var candidate = Math.Sqrt(dx * dx + dy * dy) * scale;
                        if (candidate < nearest) nearest = candidate;
                    }

                    distance = nearest == double.MaxValue ? 0.0 : nearest;
                    if (names.Count == 1) { status = "único modelo"; colocated = true; }
                    else if (colocated) status = "co-ubicado";
                    else if (vocabulary.IsFreestanding(disciplines[i]))
                    {
                        // Urbanism, landscape and survey legitimately sit
                        // beside a tower rather than inside it.
                        status = "separado (puede ser legítimo para " + disciplines[i] + ")";
                    }
                    else
                    {
                        status = "desplazado";
                        misplaced.Add(disciplines[i] + " (" + names[i] + ")");
                    }
                }

                models.Add(new Dictionary<string, object>
                {
                    ["name"] = names[i],
                    ["discipline"] = disciplines[i],
                    ["elements"] = (double)counts[i],
                    ["status"] = status,
                    ["colocated"] = colocated,
                    ["nearest_model_m"] = Math.Round(distance, 1)
                });
            }

            job?.Phasing("auditando pureza de vistas");
            var purity = AuditPurity(names, disciplines, tallies, categoryNames, active, vocabulary);

            return new Dictionary<string, object>
            {
                ["operation"] = "workflow/audit_models",
                ["models"] = models,
                ["model_count"] = (double)models.Count,
                ["misplaced"] = misplaced,
                ["without_discipline"] = withoutDiscipline,
                ["view_purity"] = purity,
                ["colocation_ok"] = misplaced.Count == 0
            };
        }

        private static bool Overlaps(BoundingBox3D a, BoundingBox3D b, double slack)
            => Math.Min(a.Max.X, b.Max.X) - Math.Max(a.Min.X, b.Min.X) > -slack &&
               Math.Min(a.Max.Y, b.Max.Y) - Math.Max(a.Min.Y, b.Min.Y) > -slack &&
               Math.Min(a.Max.Z, b.Max.Z) - Math.Max(a.Min.Z, b.Min.Z) > -slack;

        private static Dictionary<string, object> AuditPurity(
            List<string> names, List<string> disciplines,
            List<Dictionary<string, int>> tallies,
            Dictionary<string, string> categoryNames,
            ProfileStore.ActiveProfile active,
            DisciplineVocabulary vocabulary)
        {
            if (active?.Content == null)
            {
                return new Dictionary<string, object>
                {
                    ["evaluated"] = false,
                    ["reason"] = "no hay perfil activo"
                };
            }
            var whitelists = CategoryWhitelists(active.Content);
            if (whitelists.Count == 0)
            {
                return new Dictionary<string, object>
                {
                    ["evaluated"] = false,
                    ["reason"] = "el perfil no define categorías por disciplina"
                };
            }

            var findings = new List<object>();
            var dirty = 0;
            for (var i = 0; i < names.Count; i++)
            {
                var finding = ViewPurity.Evaluate(
                    disciplines[i], tallies[i], whitelists, categoryNames,
                    vocabulary.Canonical);
                if (finding.Dirty) dirty++;

                findings.Add(new Dictionary<string, object>
                {
                    ["model"] = names[i],
                    ["discipline"] = finding.Discipline,
                    ["evaluated"] = finding.Evaluated,
                    ["note"] = finding.Note,
                    ["intruder_elements"] = (double)finding.IntruderElements,
                    ["dirty"] = finding.Dirty,
                    ["intruders"] = finding.Intruders.Take(6).Select(t => (object)new Dictionary<string, object>
                    {
                        ["category"] = t.Item1,
                        ["elements"] = (double)t.Item2,
                        ["owned_by"] = t.Item3
                    }).ToList(),
                    ["unclassified_elements"] = (double)finding.UnclassifiedElements,
                    ["unclassified"] = finding.Unclassified.Take(6).Select(t => (object)new Dictionary<string, object>
                    {
                        ["category"] = t.Item1,
                        ["elements"] = (double)t.Item2,
                        ["category_id"] = t.Item3
                    }).ToList()
                });
            }

            return new Dictionary<string, object>
            {
                ["evaluated"] = true,
                ["dirty_views"] = (double)dirty,
                ["threshold_elements"] = (double)ViewPurity.DirtyThreshold,
                ["findings"] = findings
            };
        }

        /// <summary>
        /// Per-discipline category whitelists, derived from the same "sets"
        /// block that builds the search sets — one source of truth, so tuning
        /// the profile tunes the audit too.
        /// </summary>
        internal static Dictionary<string, HashSet<string>> CategoryWhitelists(
            Dictionary<string, object> profile)
        {
            var whitelists = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase);
            if (!(profile.TryGetValue("sets", out var raw) && raw is Dictionary<string, object> sets))
            {
                return whitelists;
            }

            foreach (var rawFolder in Json.Arr(sets, "folders"))
            {
                if (!(rawFolder is Dictionary<string, object> folder)) continue;
                var name = Json.Str(folder, "folder");
                if (string.IsNullOrWhiteSpace(name)) continue;

                var ids = new HashSet<string>(StringComparer.Ordinal);
                foreach (var rawSet in Json.Arr(folder, "sets"))
                {
                    if (!(rawSet is Dictionary<string, object> set)) continue;
                    foreach (var rawCond in Json.Arr(set, "conditions"))
                    {
                        if (!(rawCond is Dictionary<string, object> cond)) continue;
                        if (!string.Equals(Json.Str(cond, "property"), "CategoryId",
                                StringComparison.OrdinalIgnoreCase)) continue;
                        if (!string.Equals(SearchOperators.Normalise(Json.Str(cond, "test", SearchOperators.Equal)),
                                SearchOperators.Equal, StringComparison.OrdinalIgnoreCase)) continue;
                        ids.Add(Json.Str(cond, "value"));
                    }
                }
                if (ids.Count > 0) whitelists[name] = ids;
            }
            return whitelists;
        }

        // ------------------------------------------------------ 1. configure

        /// <summary>Step 1: search sets by discipline, then the clash matrix.</summary>
        public static Dictionary<string, object> Configure(
            Document doc, ProfileStore.ActiveProfile active, JobManager.Job job = null)
        {
            var profile = active?.Content ?? new Dictionary<string, object>();
            var validation = ProfileSchema.Validate(profile);

            var result = new MutationResult("workflow/configure")
            {
                JobId = job?.Id ?? string.Empty,
                FingerprintBefore = DocumentContext.Fingerprint(doc),
                VerificationSource = VerificationSources.SavedItemReread
            };
            result.Detail["profile"] = validation.ToJson();

            if (!validation.Ok)
            {
                foreach (var problem in validation.Errors) result.Fail(problem);
                result.FingerprintAfter = result.FingerprintBefore;
                return result.ToJson();
            }
            foreach (var warning in validation.Warnings) result.Warn(warning);

            job?.Phasing("creando search sets");
            var setsSummary = new Dictionary<string, object> { ["present"] = false };
            if (profile.TryGetValue("sets", out var setsRaw) &&
                setsRaw is Dictionary<string, object> setsSection)
            {
                // A COPY, because the flags below are ours and the section is
                // the profile's. Writing them into the profile's own
                // dictionary left the active profile no longer matching the
                // checksum it was published under — and that checksum is what
                // a report cites to say which criteria produced it.
                var setsPayload = ProfileStore.DeepCopy(setsSection);
                setsPayload["dry_run"] = false;
                if (!setsPayload.ContainsKey("replace_existing")) setsPayload["replace_existing"] = true;
                setsPayload["observe_categories"] = false;
                setsPayload["expected_document_fingerprint"] = result.FingerprintBefore;
                setsSummary = SummariseSets(WriteHandlers.BuildCriteriaSets(setsPayload));
            }
            result.Detail["sets"] = setsSummary;

            job?.Phasing("creando la matriz de clash");
            var clashSummary = new Dictionary<string, object> { ["present"] = false };
            if (profile.TryGetValue("clash", out var clashRaw) &&
                clashRaw is Dictionary<string, object> clashPayload)
            {
                clashSummary = BuildFolderMatrix(doc, clashPayload);
            }
            result.Detail["clash"] = clashSummary;

            result.Requested = (int)Json.Num(setsSummary, "sets", 0) + (int)Json.Num(clashSummary, "requested", 0);
            result.Applied = (int)Json.Num(setsSummary, "sets", 0) + (int)Json.Num(clashSummary, "created", 0)
                             + (int)Json.Num(clashSummary, "kept", 0);

            job?.Phasing("verificando");
            // Verified by re-reading: how many of the folders and tests the
            // profile asked for are actually in the document now.
            var foldersWanted = ProfileSchema.FolderNames(profile);
            var foldersPresent = doc.SelectionSets.RootItem.Children
                .Select(c => c.DisplayName ?? string.Empty)
                .Where(n => foldersWanted.Contains(n))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .Count();
            var testsPresent = doc.GetClash().TestsData.Tests.Count;

            result.Verified = (int)Json.Num(setsSummary, "verified_folders", foldersPresent)
                              + (int)Json.Num(clashSummary, "verified_tests", 0);
            result.Detail["verified_folders_in_document"] = (double)foldersPresent;
            result.Detail["verified_tests_in_document"] = (double)testsPresent;
            result.FingerprintAfter = DocumentContext.Fingerprint(doc);
            return result.ToJson();
        }

        private static Dictionary<string, object> SummariseSets(Dictionary<string, object> raw)
        {
            var summary = new Dictionary<string, object>
            {
                ["present"] = true,
                ["folders"] = 0.0,
                ["sets"] = 0.0,
                ["matches"] = 0.0,
                ["empty_sets"] = new List<object>(),
                ["skipped_folders"] = new List<object>(),
                ["verified_folders"] = 0.0
            };

            if (raw.TryGetValue("planned", out var rawPlanned) && rawPlanned is List<object> planned)
            {
                var folders = 0;
                var sets = 0;
                var matches = 0.0;
                var empty = new List<object>();
                var skipped = new List<object>();

                foreach (var entry in planned.OfType<Dictionary<string, object>>())
                {
                    if (entry.ContainsKey("error"))
                    {
                        skipped.Add(Json.Str(entry, "folder"));
                        continue;
                    }
                    folders++;
                    if (!(entry.TryGetValue("sets", out var s) && s is List<object> list)) continue;
                    sets += list.Count;
                    foreach (var set in list.OfType<Dictionary<string, object>>())
                    {
                        var count = Json.Num(set, "matches", 0);
                        matches += count;
                        if (count == 0) empty.Add(Json.Str(set, "name"));
                    }
                }

                summary["folders"] = (double)folders;
                summary["sets"] = (double)sets;
                summary["matches"] = matches;
                summary["empty_sets"] = empty;
                summary["skipped_folders"] = skipped;
            }

            if (raw.TryGetValue("verified_in_document", out var rawVerified) &&
                rawVerified is List<object> verified)
            {
                summary["verified_folders"] = (double)verified
                    .OfType<Dictionary<string, object>>()
                    .Count(v => v.TryGetValue("exists", out var e) && e is bool b && b);
                summary["verified_in_document"] = verified;
            }
            return summary;
        }

        /// <summary>
        /// Builds the clash matrix against the set FOLDERS rather than frozen
        /// item lists, so refreshing the federation refreshes the tests.
        /// </summary>
        internal static Dictionary<string, object> BuildFolderMatrix(
            Document doc, Dictionary<string, object> payload)
        {
            var replace = Json.Bool(payload, "replace_existing", true);
            var nameFormat = Json.Str(payload, "name_format", "{0} VS {1}");
            var scale = NavisContext.MetreScale(doc);
            var clash = doc.GetClash();

            var created = 0;
            var kept = 0;
            var skipped = new List<object>();
            var outdated = new List<object>();
            var orphaned = new List<object>();
            // Tests kept BECAUSE they had results, and how many they had. The
            // verification re-reads them: preserving a test and then emptying
            // it is worse than having refused to touch it.
            var expectedResults = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var requested = 0;
            var wanted = new List<string>();

            foreach (var raw in Json.Arr(payload, "pairs"))
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                requested++;

                var a = Json.Str(spec, "a");
                var b = Json.Str(spec, "b");
                var typeName = Json.Str(spec, "type", "Hard");
                var toleranceM = Json.Num(spec, "tolerance_m", 0.01);
                var name = Json.Str(spec, "name");
                if (string.IsNullOrWhiteSpace(name)) name = string.Format(nameFormat, a, b);
                wanted.Add(name);

                Enum.TryParse<ClashTestType>(typeName, true, out var expectedType);
                var expectedTolerance = toleranceM / (scale == 0 ? 1 : scale);

                var duplicate = clash.TestsData.Tests
                    .OfType<ClashTest>()
                    .FirstOrDefault(t => string.Equals(t.DisplayName, name, StringComparison.OrdinalIgnoreCase));
                if (duplicate != null)
                {
                    // Step 1 never destroys results silently. Same definition:
                    // keep. Definition changed but the test has been run: keep
                    // and report it as stale, because throwing away a finished
                    // run is a human decision. Changed and empty: refresh, it
                    // costs nothing.
                    var sameDefinition = duplicate.TestType == expectedType &&
                                         Math.Abs(duplicate.Tolerance - expectedTolerance) < 0.0001;
                    var hasResults = duplicate.Children.Count > 0;

                    // Whether its two sides still point at anything.
                    //
                    // The sets step runs before this one and rebuilds the
                    // discipline folders, which gives every set inside a new
                    // identity. A test kept here for having results was kept
                    // holding references to the items that rebuild deleted —
                    // and since the old verification compared names, the run
                    // reported success over a test that could no longer be
                    // executed. Keeping a test is only safe if it still
                    // resolves.
                    var liveA = WriteHandlers.SourceGuids(doc, duplicate.SelectionA).Count;
                    var liveB = WriteHandlers.SourceGuids(doc, duplicate.SelectionB).Count;
                    var resolvable = liveA > 0 && liveB > 0;

                    if (resolvable && (sameDefinition || hasResults || !replace))
                    {
                        kept++;
                        if (hasResults) expectedResults[name] = duplicate.Children.Count;
                        if (!sameDefinition && hasResults) outdated.Add(name);
                        continue;
                    }

                    if (!resolvable && hasResults)
                    {
                        // Rebuilding would discard a finished run; keeping it
                        // silently would publish a test nobody can execute.
                        // Neither is ours to choose, so it is reported.
                        orphaned.Add(new Dictionary<string, object>
                        {
                            ["test"] = name,
                            ["action"] = ConfigurePlanning.Blocked,
                            ["results"] = (double)duplicate.Children.Count,
                            ["selection_a_sources"] = (double)liveA,
                            ["selection_b_sources"] = (double)liveB,
                            ["reason"] =
                                "quedó apuntando a conjuntos que ya no existen y tiene "
                                + "resultados: rehacerlo los perdería. Revísalo a mano."
                        });
                        kept++;
                        continue;
                    }

                    clash.TestsData.TestsRemove(duplicate);
                }

                var sourcesA = BuildSideSources(doc, a, Json.StrArr(spec, "a_sets"));
                var sourcesB = BuildSideSources(doc, b, Json.StrArr(spec, "b_sets"));
                if (sourcesA == null || sourcesB == null)
                {
                    skipped.Add(name);
                    continue;
                }
                if (!Enum.TryParse<ClashTestType>(typeName, true, out var testType))
                {
                    skipped.Add(name + " (tipo '" + typeName + "')");
                    continue;
                }

                var test = new ClashTest
                {
                    DisplayName = name,
                    TestType = testType,
                    // The profile states tolerances in metres; Navisworks
                    // wants document units, so a millimetre on a job authored
                    // in feet stays a millimetre.
                    Tolerance = toleranceM / (scale == 0 ? 1 : scale)
                };
                test.SelectionA.Selection.CopyFrom(sourcesA);
                test.SelectionB.Selection.CopyFrom(sourcesB);
                clash.TestsData.TestsAddCopy(test);
                created++;
            }

            // Verification by identity, re-read from the document.
            //
            // Counting names was the hole: a rebuilt set carries the name of
            // the one it replaced, so "the test is present" said nothing about
            // whether it could still run. A test counts as verified only when
            // both of its sides resolve to saved items that are in THIS
            // document. A source that resolves to zero elements is still
            // valid — existing and matching something today are different
            // questions, and a set is legitimately empty before the models
            // are attached.
            var observed = new List<ConfigurePlanning.ObservedTest>();
            foreach (var name in wanted)
            {
                var live = doc.GetClash().TestsData.Tests
                    .OfType<ClashTest>()
                    .FirstOrDefault(t => string.Equals(t.DisplayName, name, StringComparison.OrdinalIgnoreCase));
                var entry = new ConfigurePlanning.ObservedTest
                {
                    Name = name,
                    Exists = live != null,
                    ResultCount = live?.Children.Count ?? 0
                };
                if (live != null)
                {
                    foreach (var guid in WriteHandlers.SourceGuids(doc, live.SelectionA))
                    {
                        entry.SideA.Add(new ConfigurePlanning.ObservedSource
                        {
                            Guid = guid, ExistsInDocument = true
                        });
                    }
                    foreach (var guid in WriteHandlers.SourceGuids(doc, live.SelectionB))
                    {
                        entry.SideB.Add(new ConfigurePlanning.ObservedSource
                        {
                            Guid = guid, ExistsInDocument = true
                        });
                    }
                }
                observed.Add(entry);
            }

            var verdict = ConfigurePlanning.Verify(
                observed, expectedResults, preserved: kept, blocked: orphaned.Count);
            var verified = verdict.Verified;

            return new Dictionary<string, object>
            {
                ["present"] = true,
                ["requested"] = (double)requested,
                ["created"] = (double)created,
                ["kept"] = (double)kept,
                ["skipped"] = skipped,
                ["outdated"] = outdated,
                ["orphaned"] = orphaned,
                ["tests_in_document"] = (double)clash.TestsData.Tests.Count,
                ["verified_tests"] = (double)verified,
                ["failed_tests"] = (double)verdict.Failed,
                ["verification"] = verdict.Details.Cast<object>().ToList(),
                ["status"] = verdict.Status
            };
        }

        private static SelectionSourceCollection BuildSideSources(
            Document doc, string folderName, List<string> setNames)
        {
            var folder = doc.SelectionSets.RootItem.Children
                .FirstOrDefault(s => string.Equals(s.DisplayName, folderName, StringComparison.OrdinalIgnoreCase));
            if (folder == null) return null;

            var sources = new SelectionSourceCollection();
            if (setNames == null || setNames.Count == 0)
            {
                sources.Add(doc.SelectionSets.CreateSelectionSource(folder));
                return sources;
            }

            if (!(folder is GroupItem group)) return null;
            foreach (var setName in setNames)
            {
                var child = group.Children.FirstOrDefault(
                    c => string.Equals(c.DisplayName, setName, StringComparison.OrdinalIgnoreCase));
                if (child != null) sources.Add(doc.SelectionSets.CreateSelectionSource(child));
            }
            return sources.Count > 0 ? sources : null;
        }

        // ------------------------------------------------------------ 2. run

        /// <summary>Step 2: run every clash test (the Run All equivalent).</summary>
        /// <remarks>
        /// <c>TestsRunAllTests</c> is one atomic API call that owns the UI
        /// thread for minutes, so there are no units to report — the job says
        /// phase and indeterminate rather than inventing a percentage.
        ///
        /// The verification is a comparison of two snapshots taken around that
        /// call, never a reading of its return. Nothing captured before it is
        /// dereferenced after: the call rebuilds the results tree, and a
        /// <c>ClashTest</c> held across it is a dangling native pointer that
        /// takes the process down rather than throwing.
        /// </remarks>
        public static Dictionary<string, object> RunAll(Document doc, JobManager.Job job = null)
        {
            var result = new MutationResult("workflow/run")
            {
                JobId = job?.Id ?? string.Empty,
                FingerprintBefore = DocumentContext.Fingerprint(doc),
                VerificationSource = VerificationSources.ClashTestStatusReread
            };

            var before = SnapshotForRun(doc);
            result.Requested = before.Count;

            if (before.Count == 0)
            {
                // Nothing to run. Reporting this as a successful Run All is
                // the most misleading answer this route can give: the operator
                // reads "completed" and concludes the model is clash-free.
                var emptyVerdict = RunVerification.Classify(
                    before, before, invocationStarted: false);
                result.Applied = 0;
                result.Verified = 0;
                result.FingerprintAfter = result.FingerprintBefore;
                foreach (var pair in emptyVerdict.ToJson()) result.Detail[pair.Key] = pair.Value;
                result.Detail["run_status"] = emptyVerdict.Status;
                result.Detail["invoked"] = false;
                result.Warn("No hay clash tests en el documento: no se ejecutó nada. " +
                            "Crea la matriz antes de correr.");
                return result.ToJson();
            }

            job?.Phasing("corriendo todos los tests",
                before.Count + " tests; Navisworks queda ocupado hasta terminar");

            var invocationStarted = false;
            string invocationError = null;
            try
            {
                doc.GetClash().TestsData.TestsRunAllTests();
                invocationStarted = true;
            }
            catch (Exception error)
            {
                // Recorded, then the verification runs anyway. A call that
                // threw halfway still left some tests Complete, and refusing
                // to look would report a total loss that did not happen.
                invocationStarted = true;
                invocationError = error.Message;
            }

            job?.Phasing("verificando", "Releyendo el estado de cada test");
            var after = SnapshotForRun(doc);
            var verdict = RunVerification.Classify(before, after, invocationStarted);

            // `applied` is what the invocation covered; `verified` is what
            // re-reading proved. They are different numbers on purpose — the
            // gap between them is the whole finding.
            result.Applied = verdict.Requested;
            result.Verified = verdict.Verified;
            result.Failed = verdict.Failed;
            foreach (var pair in verdict.ToJson()) result.Detail[pair.Key] = pair.Value;
            result.Detail["run_status"] = verdict.Status;
            result.Detail["invoked"] = invocationStarted;
            result.Detail["results_total"] = (double)after.Sum(t => t.ResultCount);
            result.FingerprintAfter = DocumentContext.Fingerprint(doc);

            if (invocationError != null)
            {
                result.Fail("TestsRunAllTests lanzó: " + invocationError +
                            ". El veredicto sale de releer los tests, no de la llamada.");
            }
            if (verdict.OldCount > 0)
            {
                result.Warn(verdict.OldCount + " test(s) quedaron Old: sus resultados son de una " +
                            "versión anterior del modelo y no cuentan como corridos.");
            }
            if (verdict.PartialCount > 0)
            {
                result.Warn(verdict.PartialCount + " test(s) quedaron Partial: la ejecución no " +
                            "cubrió toda la selección.");
            }
            if (verdict.NewCount > 0)
            {
                result.Warn(verdict.NewCount + " test(s) siguen en New tras la corrida: " +
                            "revisa que sus selecciones no estén vacías.");
            }
            if (verdict.MissingCount > 0)
            {
                result.Warn(verdict.MissingCount + " test(s) no se pudieron re-resolver por " +
                            "identidad después de correr.");
            }
            if (verdict.UnexpectedCount > 0)
            {
                result.Warn(verdict.UnexpectedCount + " test(s) aparecieron y no estaban antes: " +
                            string.Join(", ", verdict.UnexpectedNames) +
                            ". No compensan a los que faltan.");
            }
            return result.ToJson();
        }

        /// <summary>
        /// Every clash test as stable values, for comparison across the run.
        /// </summary>
        /// <remarks>
        /// Taken fresh from the document each time — the "before" list must
        /// not hold anything the run will invalidate, so it holds strings and
        /// ints and nothing else.
        /// </remarks>
        private static List<RunVerification.TestState> SnapshotForRun(Document doc)
        {
            var states = new List<RunVerification.TestState>();
            foreach (var saved in doc.GetClash().TestsData.Tests)
            {
                if (!(saved is ClashTest test)) continue;
                states.Add(new RunVerification.TestState
                {
                    Guid = test.Guid.ToString(),
                    Name = test.DisplayName ?? string.Empty,
                    Status = test.Status.ToString(),
                    ResultCount = Router.CountResults(test),
                    GroupCount = test.Children.OfType<ClashResultGroup>().Count(),
                    SourceGuidsA = WriteHandlers.SourceGuids(doc, test.SelectionA),
                    SourceGuidsB = WriteHandlers.SourceGuids(doc, test.SelectionB)
                });
            }
            return states;
        }


        // ------------------------------------------------- 3. group by level

        /// <summary>
        /// Step 3: group each test's loose results by level, rename the
        /// default clash names, and report interference series.
        /// </summary>
        public static Dictionary<string, object> GroupByLevel(Document doc, JobManager.Job job = null)
        {
            var clash = doc.GetClash();
            var result = new MutationResult("workflow/group_levels")
            {
                JobId = job?.Id ?? string.Empty,
                FingerprintBefore = DocumentContext.Fingerprint(doc),
                VerificationSource = VerificationSources.GroupMembershipReread
            };

            var testNames = clash.TestsData.Tests
                .OfType<ClashTest>()
                .Select(t => t.DisplayName ?? string.Empty)
                .Where(n => !string.IsNullOrWhiteSpace(n))
                .ToList();

            var perTest = new List<object>();
            var totalGroups = 0;
            var totalMoved = 0.0;
            var totalRequested = 0;

            for (var index = 0; index < testNames.Count; index++)
            {
                if (job != null && job.CancelRequested)
                {
                    result.Warn("Cancelado tras " + index + " de " + testNames.Count + " tests.");
                    break;
                }
                var testName = testNames[index];
                job?.Progress("agrupando por nivel", index, testNames.Count, testName);

                var fresh = FindTest(clash, testName);
                if (fresh == null) continue;

                var byLevel = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
                foreach (var child in fresh.Children)
                {
                    if (!(child is ClashResult single) || single.IsGroup) continue;
                    var level = LevelForGrouping(single);
                    if (!byLevel.TryGetValue(level, out var list))
                    {
                        list = new List<string>();
                        byLevel[level] = list;
                    }
                    list.Add(single.Guid.ToString());
                }
                if (byLevel.Count == 0) continue;

                var groups = byLevel
                    .OrderBy(kv => kv.Key, StringComparer.OrdinalIgnoreCase)
                    .Select(kv => (object)new Dictionary<string, object>
                    {
                        ["name"] = kv.Key,
                        ["clash_guids"] = kv.Value.Cast<object>().ToList()
                    })
                    .ToList();
                totalRequested += byLevel.Sum(kv => kv.Value.Count);

                var applied = WriteHandlers.ApplyGroups(new Dictionary<string, object>
                {
                    ["dry_run"] = false,
                    ["groups"] = groups
                });

                var createdGroups = applied.TryGetValue("groups", out var g) && g is List<object> list2
                    ? list2.OfType<Dictionary<string, object>>().ToList()
                    : new List<Dictionary<string, object>>();
                var moved = createdGroups.Sum(x => Json.Num(x, "moved", 0));
                totalGroups += createdGroups.Count;
                totalMoved += moved;

                perTest.Add(new Dictionary<string, object>
                {
                    ["test"] = testName,
                    ["levels"] = (double)createdGroups.Count,
                    ["moved"] = moved
                });
            }

            job?.Phasing("renombrando choques con nombre por defecto");
            var rename = RenameDefaultClashes(doc, testNames, job);

            job?.Phasing("buscando series repetidas");
            var repetition = AnalyzeRepetition(doc);

            result.Requested = totalRequested;
            result.Applied = (int)totalMoved;
            // Verified from the document: how many results now sit inside a
            // group named after a level. A move that silently failed shows up
            // as a shortfall here rather than as a success.
            result.Verified = CountGroupedResults(doc, testNames);
            result.Detail["tests"] = perTest;
            result.Detail["groups_created"] = (double)totalGroups;
            result.Detail["rename"] = rename;
            result.Detail["repetition"] = repetition;
            result.Detail["save_reminder"] =
                "Nada de esto queda en disco hasta guardar. Usa document/save (o Ctrl+S). " +
                "Si el archivo viene de ACC (.nwfacc), document/save_as a un .nwf local.";
            result.FingerprintAfter = DocumentContext.Fingerprint(doc);
            return result.ToJson();
        }

        private static int CountGroupedResults(Document doc, List<string> testNames)
        {
            var clash = doc.GetClash();
            var grouped = 0;
            foreach (var name in testNames)
            {
                var test = FindTest(clash, name);
                if (test == null) continue;
                foreach (var child in test.Children.OfType<ClashResultGroup>())
                {
                    grouped += child.Children.OfType<ClashResult>().Count();
                }
            }
            return grouped;
        }

        private static ClashTest FindTest(DocumentClash clash, string name)
            => clash.TestsData.Tests
                .OfType<ClashTest>()
                .FirstOrDefault(t => string.Equals(t.DisplayName, name, StringComparison.OrdinalIgnoreCase));

        internal static string LevelForGrouping(ClashResult result)
        {
            var item1 = result.Item1 ?? result.CompositeItem1;
            var item2 = result.Item2 ?? result.CompositeItem2;
            foreach (var item in new[] { item1, item2 })
            {
                if (item == null) continue;
                foreach (var property in new[] { "Nivel de referencia", "Nivel", "Reference Level", "Level" })
                {
                    var value = NavisContext.PropertyOf(item, property);
                    if (string.IsNullOrWhiteSpace(value)) continue;
                    return LevelNaming.Normalise(value);
                }
            }
            return LevelNaming.Missing;
        }

        /// <summary>
        /// Replaces "Clash 1", "Conflicto 2"… with "NNN · element vs element".
        /// </summary>
        /// <remarks>
        /// A name the coordinator typed is never touched, and the result is
        /// verified by counting how many default names SURVIVE — a call
        /// counter proves nothing about whether the API applied anything.
        /// </remarks>
        private static Dictionary<string, object> RenameDefaultClashes(
            Document doc, List<string> testNames, JobManager.Job job)
        {
            var clash = doc.GetClash();
            var renamed = 0;
            var attempted = 0;

            foreach (var testName in testNames)
            {
                var fresh = FindTest(clash, testName);
                if (fresh == null) continue;

                var results = new List<ClashResult>();
                Collect(fresh, results);

                var pending = new List<Tuple<Guid, string>>();
                var sequence = 0;
                foreach (var single in results)
                {
                    sequence++;
                    if (!IsDefaultClashName(single.DisplayName)) continue;
                    var a = TextLimits.Truncate(LevelNaming.StripInstanceId(
                        (single.Item1 ?? single.CompositeItem1)?.DisplayName), 28);
                    var b = TextLimits.Truncate(LevelNaming.StripInstanceId(
                        (single.Item2 ?? single.CompositeItem2)?.DisplayName), 28);
                    if (a.Length == 0 && b.Length == 0) continue;
                    pending.Add(Tuple.Create(single.Guid,
                        sequence.ToString("000", CultureInfo.InvariantCulture) + " · " + a + " vs " + b));
                }

                attempted += pending.Count;
                var done = 0;
                foreach (var entry in pending)
                {
                    if (job != null && job.CancelRequested) break;
                    try
                    {
                        // Re-resolve fresh by GUID: the collected objects die
                        // (WeakRef) the moment the document mutates, and
                        // editing a dead one is a native crash, not a
                        // catchable exception.
                        var live = clash.TestsData.ResolveGuid(entry.Item1);
                        if (live == null) continue;
                        clash.TestsData.TestsEditDisplayName(live, entry.Item2);
                        renamed++;
                    }
                    catch
                    {
                        // A single failed rename is a count difference, not a
                        // reason to abandon the step.
                    }
                    if (++done % 500 == 0)
                    {
                        BridgeHost.Log("Renombrado [" + testName + "]: " + done + "/" + pending.Count);
                    }
                }
            }

            var stillDefault = 0;
            foreach (var testName in testNames)
            {
                var fresh = FindTest(clash, testName);
                if (fresh == null) continue;
                var results = new List<ClashResult>();
                Collect(fresh, results);
                stillDefault += results.Count(r => IsDefaultClashName(r.DisplayName));
            }

            return new Dictionary<string, object>
            {
                ["attempted"] = (double)attempted,
                ["renamed"] = (double)renamed,
                ["still_default"] = (double)stillDefault,
                ["verified"] = attempted > 0 && stillDefault == 0,
                ["verification_source"] = "document_reread"
            };
        }

        private static bool IsDefaultClashName(string name)
            => System.Text.RegularExpressions.Regex.IsMatch(
                name ?? string.Empty, @"^(Clash|Conflicto)\s*\d+$",
                System.Text.RegularExpressions.RegexOptions.IgnoreCase);

        /// <summary>
        /// The same interference repeated across typical floors, plus the
        /// fragments of one interference split within a floor. Mutates
        /// nothing: it reports so one ACC issue can cover a whole series.
        /// </summary>
        public static Dictionary<string, object> AnalyzeRepetition(Document doc)
        {
            var scale = NavisContext.MetreScale(doc);
            if (scale == 0) scale = 1;

            var occurrences = new List<RepeatSeries.Occurrence>();
            foreach (var test in doc.GetClash().TestsData.Tests.OfType<ClashTest>())
            {
                var results = new List<ClashResult>();
                Collect(test, results);
                foreach (var single in results)
                {
                    var item1 = single.Item1 ?? single.CompositeItem1;
                    var item2 = single.Item2 ?? single.CompositeItem2;
                    occurrences.Add(new RepeatSeries.Occurrence
                    {
                        Test = test.DisplayName ?? string.Empty,
                        ElementA = LevelNaming.StripInstanceId(item1?.DisplayName),
                        ElementB = LevelNaming.StripInstanceId(item2?.DisplayName),
                        X = single.Center.X * scale,
                        Y = single.Center.Y * scale,
                        Level = LevelForGrouping(single),
                        Guid = single.Guid.ToString()
                    });
                }
            }

            var report = RepeatSeries.Detect(occurrences);
            var csvPath = string.Empty;
            if (report.Repeated.Count > 0)
            {
                try
                {
                    SessionStore.EnsureSecureDirectory(SessionStore.Root());
                    csvPath = Path.Combine(SessionStore.Root(), "repetidos.csv");
                    File.WriteAllLines(csvPath, RepeatSeries.ToCsv(report));
                }
                catch (Exception ex)
                {
                    csvPath = string.Empty;
                    BridgeHost.Log("No se pudo escribir repetidos.csv: " + ex.Message);
                }
            }

            return new Dictionary<string, object>
            {
                ["series_considered"] = (double)report.SeriesConsidered,
                ["repeated_series"] = (double)report.Repeated.Count,
                ["fragments"] = (double)report.Fragments,
                ["csv"] = csvPath,
                ["top"] = report.Repeated.Take(10).Select(s => (object)new Dictionary<string, object>
                {
                    ["test"] = s.Test,
                    ["element_a"] = s.ElementA,
                    ["element_b"] = s.ElementB,
                    ["levels"] = s.Levels.Cast<object>().ToList(),
                    ["level_count"] = (double)s.Levels.Count,
                    ["clashes"] = (double)s.Count,
                    ["x_m"] = s.X,
                    ["y_m"] = s.Y
                }).ToList()
            };
        }

        // ----------------------------------------------------------- 4. rules

        /// <summary>Step 4: residual triage of the profile's ignore rules.</summary>
        public static Dictionary<string, object> ApplyRules(
            Document doc, ProfileStore.ActiveProfile active, JobManager.Job job = null)
        {
            var profile = active?.Content ?? new Dictionary<string, object>();
            var result = new MutationResult("workflow/rules")
            {
                JobId = job?.Id ?? string.Empty,
                FingerprintBefore = DocumentContext.Fingerprint(doc),
                VerificationSource = VerificationSources.ResultStatusReread
            };

            var rulesPayload = ExtractRules(profile);
            if (rulesPayload == null)
            {
                result.Detail["note"] = "El perfil no define reglas residuales.";
                result.FingerprintAfter = result.FingerprintBefore;
                return result.ToJson();
            }

            if (Json.Arr(rulesPayload, "pairs").Count == 0)
            {
                // With the current standard the exclusions live in the tests'
                // own selections, so an empty 'pairs' is the NORMAL state, not
                // an error — the add-in used to throw here.
                result.Detail["note"] =
                    "Sin reglas residuales: las exclusiones del estándar ya están aplicadas en las " +
                    "selecciones de los tests. Nada que aprobar aquí.";
                result.FingerprintAfter = result.FingerprintBefore;
                return result.ToJson();
            }

            if (Json.Bool(rulesPayload, "run_tests", false))
            {
                job?.Phasing("corriendo tests antes del triaje");
                doc.GetClash().TestsData.TestsRunAllTests();
            }

            job?.Phasing("aplicando reglas");

            // Same reasoning as configure: the derived flags go on a copy, so
            // the published profile still canonicalises to its own checksum
            // after the run.
            var rulesRequest = ProfileStore.DeepCopy(rulesPayload);
            rulesRequest["dry_run"] = false;
            rulesRequest["sets_index"] = BuildSetsIndex(profile);
            rulesRequest["expected_document_fingerprint"] = result.FingerprintBefore;
            var applied = WriteHandlers.ApplyIgnoreRules(rulesRequest);

            result.Requested = (int)Json.Num(applied, "matched", 0);
            result.Applied = (int)Json.Num(applied, "edited", 0);
            result.Verified = (int)Json.Num(applied, "verified_edited", 0);
            result.Detail["rules"] = applied;
            result.Detail["status_applied"] = Json.Str(rulesPayload, "status", "Approved");
            result.FingerprintAfter = DocumentContext.Fingerprint(doc);
            return result.ToJson();
        }

        /// <summary>Accepts both the Spanish and English section names.</summary>
        internal static Dictionary<string, object> ExtractRules(Dictionary<string, object> profile)
        {
            foreach (var key in new[] { "reglas", "rules" })
            {
                if (profile.TryGetValue(key, out var raw) && raw is Dictionary<string, object> payload)
                {
                    return payload;
                }
            }
            return null;
        }

        /// <summary>
        /// set name → membership criteria, derived from the same "sets" block
        /// that built them, so the rules judge membership by identical
        /// criteria with no second source of truth.
        /// </summary>
        internal static Dictionary<string, object> BuildSetsIndex(Dictionary<string, object> profile)
        {
            var index = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            if (!(profile.TryGetValue("sets", out var raw) && raw is Dictionary<string, object> sets))
            {
                return index;
            }
            foreach (var rawFolder in Json.Arr(sets, "folders"))
            {
                if (!(rawFolder is Dictionary<string, object> folder)) continue;
                var token = Json.Str(folder, "scope_model_contains");
                foreach (var rawSet in Json.Arr(folder, "sets"))
                {
                    if (!(rawSet is Dictionary<string, object> set)) continue;
                    var name = Json.Str(set, "name");
                    if (string.IsNullOrWhiteSpace(name)) continue;

                    var ids = new List<object>();
                    foreach (var rawCond in Json.Arr(set, "conditions"))
                    {
                        if (!(rawCond is Dictionary<string, object> cond)) continue;
                        if (!string.Equals(Json.Str(cond, "property"), "CategoryId",
                                StringComparison.OrdinalIgnoreCase)) continue;
                        if (!string.Equals(SearchOperators.Normalise(Json.Str(cond, "test", SearchOperators.Equal)),
                                SearchOperators.Equal, StringComparison.OrdinalIgnoreCase)) continue;
                        ids.Add(Json.Str(cond, "value"));
                    }
                    index[name] = new Dictionary<string, object>
                    {
                        ["scope_model_contains"] = token,
                        ["category_ids"] = ids
                    };
                }
            }
            return index;
        }

        // --------------------------------------------------------- helpers

        private static void Collect(SavedItem node, List<ClashResult> into)
        {
            if (node is ClashResult result && !result.IsGroup)
            {
                into.Add(result);
                return;
            }
            if (node is GroupItem group)
            {
                foreach (var child in group.Children) Collect(child, into);
            }
        }
    }
}
