using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Clash;

namespace NavisCoord
{
    /// <summary>
    /// Everything that modifies the document.
    /// </summary>
    /// <remarks>
    /// Two rules hold across every handler here, because this code writes to
    /// a file a whole team shares:
    ///
    /// 1. **Dry run first.** Each write accepts `dry_run` and reports exactly
    ///    what it would touch. The MCP layer shows that before committing.
    /// 2. **Verify by re-reading.** Nothing reports success because a call did
    ///    not throw. After the commit the document is read back and the
    ///    response carries observed counts. A silent rollback surfaces as a
    ///    mismatch rather than as a false success.
    /// </remarks>
    internal static class WriteHandlers
    {
        // ------------------------------------------------------------- sets

        private static Dictionary<string, object> Ok(string action, Dictionary<string, object> extra = null)
        {
            var result = new Dictionary<string, object> { ["ok"] = true, ["action"] = action };
            if (extra == null) return result;
            foreach (var pair in extra) result[pair.Key] = pair.Value;
            return result;
        }

        /// <summary>
        /// The guards a synchronous mutation shares with a job.
        /// </summary>
        /// <remarks>
        /// Protecting only the job path was half a policy. A caller that skips
        /// <c>job/submit</c> and calls <c>clash/status</c> or
        /// <c>appearance/color</c> directly reaches the document through the
        /// same route table, and until this existed it did so without anybody
        /// checking which document it was about to edit.
        ///
        /// Returns the envelope to send INSTEAD of running, or null when it is
        /// safe to proceed.
        /// </remarks>
        internal static Dictionary<string, object> MutationPreflight(
            string route, Dictionary<string, object> payload)
        {
            var contract = RouteContracts.For(route);
            if (contract == null || !contract.IsMutation) return null;

            var doc = Router.RequireDocument();
            var live = DocumentContext.Fingerprint(doc);
            var expected = Json.Str(payload, "expected_document_fingerprint");

            if (contract.RequiresFingerprint && string.IsNullOrWhiteSpace(expected))
            {
                return Refusal(route, "fingerprint_required", live,
                    "Esta ruta muta el documento y exige 'expected_document_fingerprint'. " +
                    "Léelo de 'health' y repite. No se tocó nada.");
            }
            if (!DocumentFingerprint.Matches(expected, live))
            {
                return Refusal(route, "document_changed", live,
                    "El documento activo no es el que esperabas (esperado " + expected +
                    ", activo " + live + "). No se tocó nada.");
            }
            return null;
        }

        private static Dictionary<string, object> Refusal(
            string route, string error, string live, string detail)
        {
            var result = new MutationResult(route)
            {
                FingerprintBefore = live,
                FingerprintAfter = live,
                VerificationSource = VerificationSources.NotApplicable
            };
            result.Fail(detail);
            var payload = result.ToJson();
            payload["status"] = "failed";
            payload["error"] = error;
            payload["detail"] = detail;
            payload["verification_source"] = VerificationSources.None;
            return payload;
        }

        /// <summary>
        /// Validate a handler's evidence and wrap it in the mutation envelope.
        /// </summary>
        /// <remarks>
        /// A validator, not a generator. It never derives one count from
        /// another and never supplies a missing one — a route that does not
        /// know what it verified has to say so and fail, because the whole
        /// point of making the envelope mandatory was to stop a reply looking
        /// successful by omitting the hard part.
        ///
        /// Concretely, it refuses to be handed:
        ///
        /// * a `verificationSource` outside the closed set — no free text, and
        ///   in particular nothing naming something that happened before the
        ///   write, which is how `resolved` used to pass for `verified`;
        /// * `verified` above `applied`, or `applied` above `requested`;
        /// * a real run claiming `not_applicable`, or a rehearsal claiming a
        ///   re-read.
        ///
        /// On any of those it returns an <c>invalid_mutation_result</c>
        /// envelope rather than a tidier version of the handler's claim.
        /// </remarks>
        private static Dictionary<string, object> Sealed(
            string route,
            Dictionary<string, object> payload,
            Dictionary<string, object> body,
            int requested,
            int applied,
            int verified,
            string verificationSource,
            bool dryRun = false,
            int preserved = 0,
            int blocked = 0,
            string verificationLimit = null,
            IEnumerable<string> warnings = null)
        {
            var doc = Router.RequireDocument();
            var after = DocumentContext.Fingerprint(doc);

            var complaints = new List<string>();
            if (!VerificationSources.IsKnown(verificationSource))
            {
                complaints.Add("fuente de verificación no permitida: '" + verificationSource + "'");
            }
            if (dryRun && verificationSource != VerificationSources.NotApplicable)
            {
                complaints.Add("un ensayo no verifica: su fuente debe ser '" +
                               VerificationSources.NotApplicable + "'");
            }
            if (!dryRun && verificationSource == VerificationSources.NotApplicable)
            {
                complaints.Add("una corrida real no puede declarar '" +
                               VerificationSources.NotApplicable + "'");
            }
            if (requested < 0 || applied < 0 || verified < 0 || preserved < 0 || blocked < 0)
            {
                complaints.Add("los conteos no pueden ser negativos");
            }
            if (applied > requested)
            {
                complaints.Add("applied (" + applied + ") supera requested (" + requested + ")");
            }
            if (verified > applied)
            {
                complaints.Add("verified (" + verified + ") supera applied (" + applied + ")");
            }
            if (verified > 0 && !VerificationSources.ProvesVerification(verificationSource))
            {
                complaints.Add("se declaran " + verified + " unidades verificadas con la fuente '" +
                               verificationSource + "', que no demuestra nada posterior");
            }

            if (complaints.Count > 0)
            {
                var broken = new Dictionary<string, object>
                {
                    ["operation"] = route,
                    ["operation_id"] = Guid.NewGuid().ToString("N").Substring(0, 12),
                    ["status"] = "failed",
                    ["error"] = EnvelopeContract.InvalidMutationResult,
                    ["dry_run"] = dryRun,
                    ["requested"] = (double)Math.Max(0, requested),
                    ["applied"] = 0.0,
                    ["verified"] = 0.0,
                    ["failed"] = (double)Math.Max(0, requested),
                    ["preserved"] = 0.0,
                    ["blocked"] = 0.0,
                    ["verification_source"] = VerificationSources.None,
                    ["document_fingerprint_before"] = after,
                    ["document_fingerprint_after"] = after,
                    ["warnings"] = new List<object>(),
                    ["errors"] = complaints.Cast<object>().ToList(),
                    ["detail"] = "La ruta '" + route + "' entregó evidencia incoherente: " +
                                 string.Join("; ", complaints) + "."
                };
                return broken;
            }

            var result = new MutationResult(route)
            {
                TargetId = Json.Str(payload, "target_id"),
                IdempotencyKey = Json.Str(payload, "idempotency_key"),
                FingerprintBefore = Json.Str(payload, "expected_document_fingerprint", after),
                FingerprintAfter = after,
                Requested = requested,
                Applied = applied,
                Verified = verified,
                Preserved = preserved,
                Blocked = blocked,
                DryRun = dryRun,
                VerificationSource = verificationSource,
                ProfileChecksum = ProfileStore.ActiveChecksum()
            };
            result.Failed = Math.Max(0, applied - verified);
            foreach (var warning in warnings ?? Enumerable.Empty<string>()) result.Warn(warning);
            foreach (var pair in body ?? new Dictionary<string, object>())
            {
                result.Detail[pair.Key] = pair.Value;
            }
            if (!string.IsNullOrEmpty(verificationLimit))
            {
                result.Detail["verification_limit"] = verificationLimit;
            }
            return result.ToJson();
        }

        public static Dictionary<string, object> ListSets(Dictionary<string, object> payload)
        {
            var doc = Router.RequireDocument();
            var sets = new List<object>();
            foreach (var saved in doc.SelectionSets.RootItem.Children)
            {
                sets.Add(new Dictionary<string, object>
                {
                    ["name"] = saved.DisplayName ?? string.Empty,
                    ["guid"] = saved.Guid.ToString(),
                    ["is_group"] = saved.IsGroup,
                    ["item_count"] = (double?)ExplicitItems(saved as SelectionSet)?.Count ?? 0.0,
                    // A search set stores the rule, not the result, so it has
                    // no count until Navisworks re-evaluates it. Saying which
                    // kind this is beats a zero that reads as "empty".
                    ["is_search_set"] = saved is SelectionSet s && ExplicitItems(s) == null
                });
            }
            return new Dictionary<string, object> { ["sets"] = sets };
        }

        /// <summary>
        /// The items a set resolves to, or null when it stores a rule instead.
        /// </summary>
        /// <remarks>
        /// `ExplicitModelItems` THROWS on a search set rather than returning
        /// null — `InvalidOperationException: Invalid operation when
        /// '!HasExplicitModelItems'` — so the null check that guarded every
        /// call site blew up before it could be evaluated. Listing the sets of
        /// a document containing one search set failed outright, and the
        /// matrix builder, which skipped anything whose items came back null,
        /// could not build a test from a search set at all: the one thing
        /// `sets/build_search` exists to produce.
        /// </remarks>
        /// <summary>
        /// A clash-test side pointing at saved sets by reference.
        /// </summary>
        /// <remarks>
        /// Several per side on purpose: structure arrives as four sets —
        /// walls, columns, framing, floors — and a side that could hold only
        /// one meant either four tests where the coordinator wanted one, or a
        /// fifth set built by walking the whole model to merge them.
        ///
        /// By reference, not by copying the items: a set rebuilt afterwards
        /// is picked up on the next run instead of leaving the test comparing
        /// what the model held the day the matrix was made. It is also the
        /// only form that works for a search set, which has no items to copy.
        /// </remarks>
        private static SelectionSourceCollection SourcesFor(Document doc, IEnumerable<SavedItem> sets)
        {
            var sources = new SelectionSourceCollection();
            foreach (var set in sets)
            {
                if (set != null) sources.Add(doc.SelectionSets.CreateSelectionSource(set));
            }
            return sources;
        }

        private static ModelItemCollection ExplicitItems(SelectionSet set)
        {
            if (set == null) return null;
            try
            {
                return set.HasExplicitModelItems ? set.ExplicitModelItems : null;
            }
            catch
            {
                return null;
            }
        }

        /// <summary>
        /// Builds one explicit selection set per discipline.
        /// </summary>
        /// <remarks>
        /// Explicit sets rather than Navisworks search sets, deliberately. A
        /// search set re-evaluates whenever the model changes, which sounds
        /// better until a clash matrix silently changes meaning between two
        /// runs and the comparison stops being valid. An explicit set is a
        /// frozen, auditable statement of what was tested.
        /// </remarks>
        public static Dictionary<string, object> BuildSearchSets(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("sets/build", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var dryRun = Json.Bool(payload, "dry_run", true);
            var prefix = Json.Str(payload, "prefix", "NC");

            // [{ "discipline": "EST", "categories": [...], "source_files": [...] }]
            var specs = Json.Arr(payload, "disciplines");
            if (specs.Count == 0)
            {
                throw new ArgumentException("Se requiere 'disciplines' con al menos una disciplina.");
            }

            // Precedence lives in DisciplineRouter, not in this loop: a rule
            // naming the source file beats a rule naming the category, and
            // ties break on declaration order rather than on whatever order a
            // Dictionary felt like enumerating. See RulePrecedence.cs for what
            // that used to cost.
            var router = DisciplineRouter.FromSpecs(specs, Json.Str(payload, "fallback_discipline"));
            var buckets = new Dictionary<string, ModelItemCollection>(StringComparer.OrdinalIgnoreCase);
            var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var basis = new Dictionary<string, Dictionary<string, int>>(StringComparer.OrdinalIgnoreCase);
            foreach (var raw in specs)
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                var code = Json.Str(spec, "discipline");
                if (string.IsNullOrWhiteSpace(code)) continue;
                buckets[code] = new ModelItemCollection();
                basis[code] = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            }

            var scanned = 0;
            var ambiguous = 0;
            foreach (var model in doc.Models)
            {
                var sourceFile = System.IO.Path.GetFileName(
                    string.IsNullOrWhiteSpace(model.SourceFileName) ? model.FileName : model.SourceFileName);

                foreach (var item in model.RootItem.Descendants)
                {
                    scanned++;
                    // Only leaf geometry is worth testing: adding composite
                    // parents duplicates every clash their children produce.
                    if (item.Children.Any()) continue;

                    var verdict = router.Resolve(sourceFile, NavisContext.CategoryOf(item));
                    if (!verdict.Matched) continue;
                    if (!buckets.TryGetValue(verdict.Discipline, out var bucket))
                    {
                        bucket = new ModelItemCollection();
                        buckets[verdict.Discipline] = bucket;
                        basis[verdict.Discipline] = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                    }
                    // A rehearsal counts; it does not collect.
                    //
                    // `dry_run` was checked only after this walk, so the
                    // rehearsal did the whole job — every leaf of every model
                    // resolved and retained in a ModelItemCollection — and
                    // then threw the collections away. On a real federation
                    // that is hundreds of thousands of retained COM handles
                    // on the UI thread, for an answer that is a handful of
                    // integers. It took Navisworks down with it, which is a
                    // remarkable thing for a command whose whole promise is
                    // that it changes nothing.
                    counts[verdict.Discipline] =
                        counts.TryGetValue(verdict.Discipline, out var seen) ? seen + 1 : 1;
                    if (!dryRun) bucket.Add(item);
                    var tally = basis[verdict.Discipline];
                    tally[verdict.Basis] = tally.TryGetValue(verdict.Basis, out var c) ? c + 1 : 1;
                    if (verdict.Ambiguous) ambiguous++;
                }
            }

            var plan = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            foreach (var code in buckets.Keys)
            {
                plan[code] = (double)(counts.TryGetValue(code, out var n) ? n : 0);
            }
            foreach (var pair in counts)
            {
                if (!plan.ContainsKey(pair.Key)) plan[pair.Key] = (double)pair.Value;
            }

            var routing = router.Describe();
            routing["assigned_by"] = basis.ToDictionary(
                kv => kv.Key,
                kv => (object)kv.Value.ToDictionary(b => b.Key, b => (object)(double)b.Value));
            routing["ambiguous_elements"] = (double)ambiguous;

            if (dryRun)
            {
                return Sealed("sets/build", payload, new Dictionary<string, object>
                {
                    ["action"] = "build_sets",
                    ["scanned_items"] = (double)scanned,
                    ["routing"] = routing,
                    ["would_create"] = plan
                },
                requested: plan.Count, applied: 0, verified: 0,
                verificationSource: VerificationSources.NotApplicable, dryRun: true);
            }

            var created = new List<object>();
            foreach (var pair in buckets)
            {
                if (pair.Value.Count == 0) continue;
                var name = $"{prefix} - {pair.Key}";
                RemoveSetByName(doc, name);

                var set = new SelectionSet(pair.Value) { DisplayName = name };
                doc.SelectionSets.AddCopy(set);
                created.Add(name);
            }

            // Verification: re-read the sets from the document rather than
            // trusting that AddCopy did what it said.
            var observed = new Dictionary<string, object>();
            foreach (var saved in doc.SelectionSets.RootItem.Children)
            {
                if (saved is SelectionSet set && (saved.DisplayName ?? string.Empty).StartsWith(prefix, StringComparison.Ordinal))
                {
                    observed[saved.DisplayName] = (double)(ExplicitItems(set)?.Count ?? 0);
                }
            }

            return Sealed("sets/build", payload, new Dictionary<string, object>
            {
                ["action"] = "build_sets",
                ["scanned_items"] = (double)scanned,
                ["routing"] = routing,
                ["planned"] = plan,
                ["verified_in_document"] = observed
            },
            requested: plan.Count, applied: created.Count, verified: observed.Count,
            verificationSource: VerificationSources.DocumentReread);
        }

        private static void RemoveSetByName(Document doc, string name)
        {
            var existing = doc.SelectionSets.RootItem.Children
                .FirstOrDefault(s => string.Equals(s.DisplayName, name, StringComparison.OrdinalIgnoreCase));
            if (existing != null) doc.SelectionSets.Remove(existing);
        }

        // ------------------------------------------- search sets (criterios)

        /// <summary>
        /// Crea search sets reales (por criterios) agrupados en carpetas de
        /// disciplina, para plantillas de coordinación que se re-evalúan solas
        /// al actualizar el federado.
        /// </summary>
        /// <remarks>
        /// Complementa BuildSearchSets (sets explícitos congelados, pensados
        /// para auditar una corrida de clash): aquí el objetivo es la PLANTILLA.
        /// El ancla recomendada es idioma-independiente: el alcance por modelo
        /// (nomenclatura con -DIS- en el árbol) y la propiedad numérica
        /// Properties > CategoryId de los agregados de ACC, porque el valor
        /// Category ("Revit Muros"/"Revit Walls") cambia con el idioma del
        /// Revit que publicó. Con 'equals' numérico que no coincide como texto
        /// se reintenta como entero y el reporte dice qué modo ganó.
        /// </remarks>
        public static Dictionary<string, object> BuildCriteriaSets(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("sets/build_search", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var dryRun = Json.Bool(payload, "dry_run", true);
            var replace = Json.Bool(payload, "replace_existing", true);
            var observe = Json.Bool(payload, "observe_categories", dryRun);

            var folderSpecs = Json.Arr(payload, "folders");
            if (folderSpecs.Count == 0)
            {
                throw new ArgumentException("Se requiere 'folders' con al menos una carpeta de disciplina.");
            }

            // Resolver el tab real UNA vez por llamada; los sets lo reusan.
            var detected = DetectPropertyTab(doc);
            _tabCandidates = detected == null
                ? PropertyTabAliases
                : new[] { detected }
                    .Concat(PropertyTabAliases.Where(a => !string.Equals(a, detected, StringComparison.OrdinalIgnoreCase)))
                    .ToArray();

            var planned = new List<object>();
            var pending = new List<Tuple<string, string, Search>>();

            foreach (var rawFolder in folderSpecs)
            {
                if (!(rawFolder is Dictionary<string, object> folderSpec)) continue;
                var folderName = Json.Str(folderSpec, "folder");
                if (string.IsNullOrWhiteSpace(folderName)) continue;

                var scopeToken = Json.Str(folderSpec, "scope_model_contains");
                List<string> scopeNames;
                var scopeRoots = ResolveScope(doc, scopeToken, out scopeNames);
                if (!string.IsNullOrWhiteSpace(scopeToken) && scopeRoots.Count == 0)
                {
                    planned.Add(new Dictionary<string, object>
                    {
                        ["folder"] = folderName,
                        ["scope_model_contains"] = scopeToken,
                        ["error"] = "ningún modelo anexado coincide con ese token"
                    });
                    continue;
                }

                var setReports = new List<object>();
                foreach (var rawSet in Json.Arr(folderSpec, "sets"))
                {
                    if (!(rawSet is Dictionary<string, object> setSpec)) continue;
                    var setName = Json.Str(setSpec, "name");
                    if (string.IsNullOrWhiteSpace(setName)) continue;

                    string matchMode;
                    var search = BuildSetSearch(doc, scopeRoots, setSpec, out matchMode);
                    var count = search.FindAll(doc, false).Count;

                    setReports.Add(new Dictionary<string, object>
                    {
                        ["name"] = setName,
                        ["matches"] = (double)count,
                        ["match_mode"] = matchMode
                    });
                    pending.Add(Tuple.Create(folderName, setName, search));
                }

                planned.Add(new Dictionary<string, object>
                {
                    ["folder"] = folderName,
                    ["scope_models"] = scopeNames,
                    ["sets"] = setReports
                });
            }

            var result = new Dictionary<string, object>
            {
                ["dry_run"] = dryRun,
                ["planned"] = planned
            };

            if (observe)
            {
                result["observed_categories"] = ObserveCategoryPairs(doc);
            }

            // The plan, and the capability it was decided under.
            //
            // Reading the graph is free; discovering what the API preserves is
            // not, and a dry run is not allowed to pay that price. So the
            // capability is DECLARED — see ConfigurePlanning.MigrationCapability
            // — and the same constant is reported on both paths, which is what
            // makes the dry run an honest preview of the real one instead of a
            // cheaper, more optimistic story.
            var before = SnapshotTests(doc);
            var dependents = ConfigurePlanning.DependentTests(before);

            // Every saved item ANY test points at, not only the ones behind a
            // finished run. A test without results still holds a reference
            // that nothing here can provably rebind.
            var referenced = new HashSet<string>(
                before.SelectMany(t => t.AllSourceGuids),
                StringComparer.OrdinalIgnoreCase);

            // Definitions are compared through the API's own `ValueEquals`
            // rather than through any string this code invents: two sets are
            // "the same" exactly when Navisworks says their searches are. The
            // planner only needs a token that matches when they match, so the
            // path is reused when equal and marked otherwise.
            var existingSets = new List<ConfigurePlanning.SetSnapshot>();
            var desiredSets = new List<ConfigurePlanning.DesiredSet>();
            foreach (var entry in pending)
            {
                var path = entry.Item1 + "/" + entry.Item2;
                desiredSets.Add(new ConfigurePlanning.DesiredSet
                {
                    Name = entry.Item2,
                    Folder = entry.Item1,
                    Definition = path
                });

                var live = FindSetIn(doc, entry.Item1, entry.Item2);
                if (live == null) continue;
                existingSets.Add(new ConfigurePlanning.SetSnapshot
                {
                    Guid = live.Guid.ToString(),
                    Name = entry.Item2,
                    Folder = entry.Item1,
                    Definition = SameDefinition(live, entry.Item3) ? path : path + "#anterior"
                });
            }

            var steps = ConfigurePlanning.Plan(existingSets, desiredSets, before, Capability);
            result["plan"] = steps.Select(st => (object)st.ToJson()).ToList();
            result["migration_capability"] = Capability == ConfigurePlanning.MigrationCapability.Unverified
                ? ConfigurePlanning.CapabilityUnverified
                : "migration_capability_verified_atomic_replace";

            // Nothing below this line runs on a rehearsal. The dry run has now
            // read the sets, read the tests, resolved every source and printed
            // the plan it would execute — without one call that writes.
            if (dryRun)
            {
                return Sealed("sets/build_search", payload, result,
                    requested: pending.Count, applied: 0, verified: 0,
                    verificationSource: VerificationSources.NotApplicable, dryRun: true);
            }

            var blocked = new List<object>();

            // Built with the plan and with `allowed` tied to the rehearsal
            // flag, so the ordering rules below are enforced by the gate
            // rather than by this loop remembering them.
            var gate = ConfigurePlanning.MutationGate.ForFolders(
                !dryRun,
                steps,
                doc.SelectionSets.RootItem.Children.Select(c => c.DisplayName ?? string.Empty));
            var failures = new List<object>();
            var created = 0;

            // Folders whose every set already says what it should. Rebuilding
            // them would hand out new identities for no gain, which is the
            // churn that broke the tests in the first place: a second run of
            // the same configuration has nothing to do.
            var settled = new HashSet<string>(
                steps.Where(st => st.Action == ConfigurePlanning.UpdateInPlace)
                     .Select(st => st.Target),
                StringComparer.OrdinalIgnoreCase);
            var untouched = new List<object>();

            foreach (var group in pending.GroupBy(p => p.Item1))
            {
                var existing = doc.SelectionSets.RootItem.Children
                    .FirstOrDefault(s => string.Equals(s.DisplayName, group.Key, StringComparison.OrdinalIgnoreCase));

                if (existing != null &&
                    group.All(e => settled.Contains(group.Key + "/" + e.Item2)))
                {
                    untouched.Add(new Dictionary<string, object>
                    {
                        ["folder"] = group.Key,
                        ["action"] = ConfigurePlanning.UpdateInPlace,
                        ["reason"] = "ya coincide con la configuración pedida"
                    });
                    continue;
                }

                if (existing != null)
                {
                    // Sin replace la carpeta existente se respeta: los clash
                    // tests la referencian por SelectionSource y recrearla
                    // rompería ese enlace y borraría resultados corridos.
                    if (!replace) continue;

                    // And with replace, the same reasoning still applies — it
                    // was just never enforced. `workflow/configure` forces the
                    // flag on, so this branch removed folders whose sets a
                    // clash test was pointing at, and the matrix step
                    // afterwards kept that test because it had results. The
                    // test survived holding a reference to nothing, and the
                    // name-and-count verification reported the run green.
                    //
                    // The exit here is one-way on purpose. There IS an atomic
                    // replace in the API — `ReplaceWithCopy(parent, index,
                    // item)` swaps a child in a single call, with no prior
                    // Remove — but its contract says it inserts "a copy of
                    // item" and says nothing about the copy keeping the GUID,
                    // and nothing at all about rebinding the SelectionSources
                    // that clash tests declare. An atomic operation whose
                    // preservation is undocumented is not a safe migration; it
                    // is an untested one. Until an integration test against a
                    // scratch document demonstrates otherwise and raises
                    // `Capability`, the referenced folder is left exactly as
                    // it is. Not updating a criterion is recoverable. Losing a
                    // finished clash run is not.
                    var held = GuidsUnder(existing).Where(referenced.Contains).ToList();
                    if (held.Count > 0)
                    {
                        var affected = held
                            .SelectMany(g => dependents.TryGetValue(g, out var users)
                                ? users
                                : Enumerable.Empty<string>())
                            .Distinct(StringComparer.OrdinalIgnoreCase)
                            .OrderBy(n => n, StringComparer.OrdinalIgnoreCase)
                            .ToList();
                        var withResults = before
                            .Where(t => t.HasResults && t.AllSourceGuids.Any(held.Contains))
                            .Select(t => t.Name)
                            .Distinct(StringComparer.OrdinalIgnoreCase)
                            .OrderBy(n => n, StringComparer.OrdinalIgnoreCase)
                            .ToList();

                        blocked.Add(new Dictionary<string, object>
                        {
                            ["folder"] = group.Key,
                            ["action"] = ConfigurePlanning.Blocked,
                            ["reason"] =
                                "la usan " + held.Count + " conjunto(s) que " + affected.Count
                                + " test(s) referencian"
                                + (withResults.Count > 0
                                    ? ", " + withResults.Count + " de ellos con resultados"
                                    : "")
                                + "; no hay una operación de reemplazo cuya conservación de "
                                + "identidad y de enlace esté demostrada ("
                                + ConfigurePlanning.CapabilityUnverified
                                + "), así que la carpeta se conserva tal cual",
                            ["held_guids"] = held.Cast<object>().ToList(),
                            ["affected_tests"] = affected.Cast<object>().ToList(),
                            ["tests_with_results"] = withResults.Cast<object>().ToList()
                        });
                        gate.PreserveReferenced(group.Key);
                        continue;
                    }
                }

                var folder = new FolderItem { DisplayName = group.Key };
                foreach (var entry in group)
                {
                    folder.Children.Add(new SelectionSet(entry.Item3) { DisplayName = entry.Item2 });
                }

                // Replace, or add — never remove-then-add.
                //
                // `ReplaceWithCopy(parent, index, item)` swaps a child in one
                // call, which is the whole reason to use it here: the previous
                // sequence removed the old folder and then added the new one,
                // and between those two lines the document held neither. That
                // nothing referenced the folder made the damage smaller, not
                // the invariant satisfied — if the add threw, what the user
                // got back was a project missing a discipline.
                //
                // Identity is not preserved and does not need to be: this
                // branch only runs when no SelectionSource points inside.
                var wantedNames = group.Select(e => e.Item2).ToList();
                var previousGuid = existing?.Guid ?? Guid.Empty;
                var replacing = existing != null;
                try
                {
                    if (replacing)
                    {
                        gate.ReplaceUnreferenced(group.Key);
                        var index = doc.SelectionSets.RootItem.Children.IndexOf(existing);
                        doc.SelectionSets.ReplaceWithCopy(doc.SelectionSets.RootItem, index, folder);
                    }
                    else
                    {
                        gate.AddNew(group.Key);
                        doc.SelectionSets.AddCopy(folder);
                    }
                }
                catch (Exception error)
                {
                    // Re-read and describe. Not repaired: undoing a
                    // half-applied swap with a remove and an add is exactly
                    // the sequence this branch exists to avoid, and doing it
                    // blind would turn an unknown state into a lost one.
                    failures.Add(new Dictionary<string, object>
                    {
                        ["folder"] = group.Key,
                        ["operation"] = replacing ? "replace_with_copy" : "add_copy",
                        ["error"] = error.Message,
                        ["state"] = ConfigurePlanning.DescribeState(
                            previousGuid != Guid.Empty && ResolveGuid(doc, previousGuid.ToString()) != null,
                            group.All(e => SameDefinition(FindSetIn(doc, group.Key, e.Item2), e.Item3)))
                    });
                    continue;
                }

                // Re-read what the call left behind, and require all of it:
                // one folder at the path, the sets that were asked for and no
                // others, each searching for what it should, and the previous
                // item gone rather than sitting alongside its replacement.
                var live = doc.SelectionSets.RootItem.Children
                    .FirstOrDefault(s => string.Equals(
                        s.DisplayName, group.Key, StringComparison.OrdinalIgnoreCase)) as GroupItem;
                var survivor = previousGuid == Guid.Empty ? null : ResolveGuid(doc, previousGuid.ToString());
                var observation = new ConfigurePlanning.FolderObservation
                {
                    Folder = group.Key,
                    MatchingRootEntries = doc.SelectionSets.RootItem.Children.Count(
                        s => string.Equals(s.DisplayName, group.Key, StringComparison.OrdinalIgnoreCase)),
                    SetNames = live == null
                        ? new List<string>()
                        : live.Children.Select(c => c.DisplayName ?? string.Empty).ToList(),
                    ExpectedSetNames = wantedNames,
                    MismatchedDefinitions = group
                        .Where(e => !SameDefinition(FindSetIn(doc, group.Key, e.Item2), e.Item3))
                        .Select(e => e.Item2)
                        .ToList(),
                    // Only a leftover if it is a DIFFERENT item from the one
                    // now at the path: were the API to preserve the GUID, the
                    // survivor would be the replacement itself.
                    PreviousItemStillPresent =
                        survivor != null && (live == null || survivor.Guid != live.Guid)
                };

                var problems = ConfigurePlanning.VerifyReplacement(observation);
                if (problems.Count > 0)
                {
                    failures.Add(new Dictionary<string, object>
                    {
                        ["folder"] = group.Key,
                        ["operation"] = replacing ? "replace_with_copy" : "add_copy",
                        ["problems"] = problems.Cast<object>().ToList(),
                        ["state"] = ConfigurePlanning.DescribeState(
                            observation.PreviousItemStillPresent,
                            observation.MismatchedDefinitions.Count == 0 && live != null)
                    });
                    continue;
                }
                created++;
            }

            // Verificación: releer el documento, no confiar en AddCopy.
            var verified = new List<object>();
            foreach (var group in pending.GroupBy(p => p.Item1))
            {
                var saved = doc.SelectionSets.RootItem.Children
                    .FirstOrDefault(s => string.Equals(s.DisplayName, group.Key, StringComparison.OrdinalIgnoreCase));
                verified.Add(new Dictionary<string, object>
                {
                    ["folder"] = group.Key,
                    ["exists"] = saved != null,
                    ["is_group"] = saved != null && saved.IsGroup,
                    ["children"] = saved is GroupItem g
                        ? (object)g.Children.Select(c => c.DisplayName ?? string.Empty).ToList()
                        : new List<string>()
                });
            }
            result["verified_in_document"] = verified;
            result["blocked"] = blocked;
            result["untouched"] = untouched;
            result["mutations"] = gate.Journal.Cast<object>().ToList();
            result["folders_written"] = (double)created;
            result["failures"] = failures;

            // The whole chain, re-read and compared against how it started.
            //
            // "The test is still there" was the claim that let a broken run
            // report green, so it is not the claim being made here. Every test
            // is matched by identity, its declared sources are compared GUID
            // by GUID against what it declared before, each of those GUIDs is
            // resolved, what it resolves to is fingerprinted and compared, and
            // the results, groups and status underneath it are required not to
            // have shrunk. A single degradation is enough to withhold
            // `completed` — including one nothing here predicted.
            var after = SnapshotTests(doc);
            var degraded = ConfigurePlanning.CompareIntegrity(before, after, ResolveSources(doc, after));
            result["integrity"] = degraded.Cast<object>().ToList();

            // `verified` counts only folders that were written AND came back
            // clean. Preserved and blocked stay separate on purpose: a folder
            // kept because a clash run depends on it is not a failure, but it
            // is not completeness either.
            var writtenFolders = pending
                .Select(e => e.Item1)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .Count();
            var defects = degraded.Count + failures.Count;
            var settledCount = created + untouched.Count;
            return Sealed("sets/build_search", payload, result,
                requested: writtenFolders,
                applied: settledCount,
                verified: Math.Max(0, settledCount - defects),
                verificationSource: VerificationSources.SavedItemReread,
                preserved: untouched.Count,
                blocked: blocked.Count);
        }

        private static ModelItemCollection ResolveScope(Document doc, string token, out List<string> names)
        {
            names = new List<string>();
            var roots = new ModelItemCollection();
            if (string.IsNullOrWhiteSpace(token)) return roots;
            // Alias separados por "|": una disciplina puede llegar con más de
            // una sigla (p.ej. "-VTM-|-AAC-", mismo aire por dos nombres).
            var variantes = token.Split('|');
            foreach (var model in doc.Models)
            {
                // Decodificar %XX de ACC: "-SEÑ-" del perfil debe matchear un
                // árbol que dice "-SE%C3%91-".
                var display = ModelNames.Decode(model.RootItem?.DisplayName ?? string.Empty);
                var source = ModelNames.Decode(model.SourceFileName ?? model.FileName ?? string.Empty);
                if (variantes.Any(t =>
                        display.IndexOf(t, StringComparison.OrdinalIgnoreCase) >= 0 ||
                        source.IndexOf(t, StringComparison.OrdinalIgnoreCase) >= 0))
                {
                    roots.Add(model.RootItem);
                    names.Add(display);
                }
            }
            return roots;
        }

        // El TAB de las propiedades internas del visor ("Properties") se
        // LOCALIZA con el idioma de Navisworks — "Propiedades" en español,
        // etc. — así que fijar un nombre parte el estándar en cuanto alguien
        // abre el modelo con otro idioma (medido 2026-08-12: EN daba 8.436,
        // ES daba 0). Primero se DETECTA el tab real leyendo un puñado de
        // elementos del modelo (funciona en cualquier idioma, incluso los que
        // no están en la lista); los alias quedan de respaldo.
        private static readonly string[] PropertyTabAliases =
            { "Properties", "Propiedades", "Propriétés", "Eigenschaften", "Proprietà", "Propriedades", "属性" };

        /// <summary>
        /// Nombre visible real del tab que contiene CategoryId en ESTE
        /// documento: se lee de los primeros elementos con geometría.
        /// </summary>
        private static string DetectPropertyTab(Document doc)
        {
            try
            {
                foreach (var model in doc.Models)
                {
                    var visited = 0;
                    foreach (var item in model.RootItem.Descendants)
                    {
                        if (item.Children.Any()) continue;
                        if (++visited > 200) break;
                        foreach (var category in item.PropertyCategories)
                        {
                            foreach (var property in category.Properties)
                            {
                                if (string.Equals(property.DisplayName, "CategoryId", StringComparison.OrdinalIgnoreCase))
                                {
                                    return category.DisplayName;
                                }
                            }
                        }
                    }
                }
            }
            catch
            {
                // Detectar es una optimización; los alias siguen de respaldo.
            }
            return null;
        }

        private static Search BuildSetSearch(
            Document doc, ModelItemCollection scopeRoots,
            Dictionary<string, object> setSpec, out string matchMode)
        {
            var hasConds = Json.Arr(setSpec, "conditions").Count > 0;
            // Only 'equals' changes meaning under asInt: a not_contains on a
            // Keynote in the same set must not veto the whole retry. The rule
            // lives in SearchOperators so it can be asserted without a licence.
            var numeric = SearchOperators.ShouldRetryAsInt(
                Json.Arr(setSpec, "conditions").OfType<Dictionary<string, object>>());

            Search best = null;
            var candidates = _tabCandidates ?? PropertyTabAliases;
            foreach (var tabAlias in candidates)
            {
                var attempt = NewScopedSearch(scopeRoots, setSpec, asInt: false, propertyTab: tabAlias);
                if (best == null) best = attempt;
                if (attempt.FindAll(doc, false).Count > 0)
                {
                    matchMode = "texto:" + tabAlias;
                    return attempt;
                }
                if (!numeric) continue;

                var retry = NewScopedSearch(scopeRoots, setSpec, asInt: true, propertyTab: tabAlias);
                if (retry.FindAll(doc, false).Count > 0)
                {
                    matchMode = "entero:" + tabAlias;
                    return retry;
                }
            }

            matchMode = hasConds ? "sin_coincidencias" : "gral_categoria";
            return best;
        }

        private static Search NewScopedSearch(
            ModelItemCollection scopeRoots, Dictionary<string, object> setSpec, bool asInt, string propertyTab)
        {
            var search = new Search();
            if (scopeRoots.Count > 0) search.Selection.CopyFrom(scopeRoots);
            else search.Selection.SelectAll();
            search.Locations = SearchLocations.DescendantsAndSelf;

            foreach (var rawCond in Json.Arr(setSpec, "conditions"))
            {
                if (!(rawCond is Dictionary<string, object> condSpec)) continue;
                var tab = Json.Str(condSpec, "tab", "Properties");
                // Cualquier tab que en el perfil sea un alias de "propiedades
                // del visor" se resuelve al candidato de idioma en curso.
                if (PropertyTabAliases.Contains(tab, StringComparer.OrdinalIgnoreCase))
                {
                    tab = propertyTab;
                }
                var prop = Json.Str(condSpec, "property");
                var test = SearchOperators.Normalise(Json.Str(condSpec, "test", SearchOperators.Equal));
                var value = Json.Str(condSpec, "value");
                if (string.IsNullOrWhiteSpace(prop)) continue;
                if (!SearchOperators.IsKnown(test))
                {
                    // A typo'd operator used to fall into `default` and become
                    // a silent equality test, so a set that should have
                    // excluded something quietly included everything.
                    throw new ArgumentException(
                        "Operador de búsqueda desconocido: '" + test + "'. Válidos: " +
                        string.Join(", ", SearchOperators.Known) + ".");
                }

                var baseCond = SearchCondition.HasPropertyByDisplayName(tab, prop);
                SearchCondition cond;
                switch (test)
                {
                    case "has":
                        // Existencia de la propiedad, sin comparar valor. Es el
                        // único match-todo fiable sobre propiedades enteras del
                        // SVF: contains/wildcard comparan texto y devuelven 0.
                        cond = baseCond;
                        break;
                    case "contains":
                        cond = baseCond.DisplayStringContains(value);
                        break;
                    case "not_contains":
                        // Exclusión (p.ej. Keynote con "ESTRUCTURA"/"NO"): la
                        // negación también acepta ítems SIN la propiedad, que
                        // es lo correcto para filtros de limpieza — solo se
                        // excluye a quien declara la palabra prohibida.
                        cond = baseCond.DisplayStringContains(value).Negate();
                        break;
                    case "wildcard":
                        cond = baseCond.DisplayStringWildcard(value);
                        break;
                    case "not_wildcard":
                        // Exclusión por patrón (p.ej. Keynote que empiece por
                        // "N": NO, NA, N/A, NO CONTABILIZAR…). La negación
                        // acepta ítems sin la propiedad, como not_contains.
                        cond = baseCond.DisplayStringWildcard(value).Negate();
                        break;
                    case "not_equals":
                        // Exclusión por valor exacto (p.ej. Structural = Yes).
                        cond = baseCond.EqualValue(VariantData.FromDisplayString(value)).Negate();
                        break;
                    default:
                        cond = asInt && int.TryParse(value, out var intValue)
                            ? baseCond.EqualValue(VariantData.FromInt32(intValue))
                            : baseCond.EqualValue(VariantData.FromDisplayString(value));
                        break;
                }
                search.SearchConditions.Add(cond);
            }

            if (search.SearchConditions.Count == 0)
            {
                // Un set sin condiciones es el GRAL de la disciplina. El
                // constructor de SelectionSet(Search) exige al menos una
                // condición (el botón fallaba justo ahí), así que se sintetiza
                // "tiene CategoryId": todo elemento real de modelo la trae.
                // propertyTab ya viene resuelto al idioma en curso.
                search.SearchConditions.Add(
                    SearchCondition.HasPropertyByDisplayName(propertyTab, "CategoryId"));
            }
            return search;
        }

        /// <summary>
        /// Pares (Category, CategoryId) observados en el federado: la tabla de
        /// equivalencias idioma→id se autogenera del modelo real, no de memoria.
        /// </summary>
        private static Dictionary<string, object> ObserveCategoryPairs(Document doc)
        {
            var pairs = new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
            var visited = 0;
            foreach (var model in doc.Models)
            {
                foreach (var item in model.RootItem.Descendants)
                {
                    if (++visited > 150000) break;
                    if (item.Children.Any()) continue;
                    var display = NavisContext.PropertyOf(item, "Category");
                    var id = NavisContext.PropertyOf(item, "CategoryId");
                    if (string.IsNullOrWhiteSpace(display) && string.IsNullOrWhiteSpace(id)) continue;
                    var key = $"{display}|{id}";
                    if (!pairs.TryGetValue(key, out var entry))
                    {
                        entry = new Dictionary<string, object>
                        {
                            ["category"] = display,
                            ["category_id"] = id,
                            ["count"] = 0.0
                        };
                        pairs[key] = entry;
                    }
                    entry["count"] = (double)entry["count"] + 1;
                }
            }
            return new Dictionary<string, object>
            {
                ["visited"] = (double)visited,
                ["pairs"] = pairs.Values.OrderByDescending(p => (double)p["count"]).Cast<object>().ToList()
            };
        }

        // ------------------------------------------- reglas de ignorados

        [ThreadStatic]
        private static string[] _tabCandidates;

        private sealed class IgnoreSetDef
        {
            public string Token;
            public HashSet<string> CategoryIds;
        }

        /// <summary>
        /// Aplica el efecto de las reglas corporativas "ignorar choques entre
        /// sets": todo resultado cuyo par de elementos caiga en un par ignorado
        /// pasa al estado configurado (Approved por defecto).
        /// </summary>
        /// <remarks>
        /// La API .NET no permite CREAR reglas nativas de Clash Detective
        /// (Rule no tiene constructor público y el motor LcOpRule es interno),
        /// así que el estándar se aplica por triaje de resultados tras la
        /// corrida: mismo efecto sobre el reporte, con la ventaja de que lo
        /// ignorado queda visible y auditable en vez de desaparecer.
        ///
        /// La pertenencia a un set no se consulta al set guardado: se reevalúa
        /// con los MISMOS criterios del perfil (token de disciplina en el
        /// modelo raíz + CategoryId numérico), que es lo que hace al filtro
        /// indiferente al idioma del Revit que publicó cada modelo.
        /// </remarks>
        public static Dictionary<string, object> ApplyIgnoreRules(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("clash/apply_rules", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var dryRun = Json.Bool(payload, "dry_run", true);
            var onlyNew = Json.Bool(payload, "only_new", true);
            var statusName = Json.Str(payload, "status", "Approved");
            if (!Enum.TryParse<ClashResultStatus>(statusName, true, out var status))
            {
                throw new ArgumentException(
                    $"Estado '{statusName}' no válido. Usa: New, Active, Reviewed, Approved, Resolved.");
            }

            var setDefs = new Dictionary<string, IgnoreSetDef>(StringComparer.OrdinalIgnoreCase);
            if (payload.TryGetValue("sets_index", out var rawIndex) &&
                rawIndex is Dictionary<string, object> index)
            {
                foreach (var entry in index)
                {
                    if (!(entry.Value is Dictionary<string, object> def)) continue;
                    setDefs[entry.Key] = new IgnoreSetDef
                    {
                        Token = Json.Str(def, "scope_model_contains"),
                        CategoryIds = new HashSet<string>(Json.StrArr(def, "category_ids"), StringComparer.Ordinal)
                    };
                }
            }

            // pairs agrupados por test para recorrer cada test una sola vez.
            var pairsByTest = new Dictionary<string, List<Tuple<string, string, string>>>(StringComparer.OrdinalIgnoreCase);
            foreach (var raw in Json.Arr(payload, "pairs"))
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                var testName = Json.Str(spec, "test");
                var a = Json.Str(spec, "a");
                var b = Json.Str(spec, "b");
                if (string.IsNullOrWhiteSpace(testName)) continue;
                if (!setDefs.ContainsKey(a) || !setDefs.ContainsKey(b)) continue;
                if (!pairsByTest.TryGetValue(testName, out var list))
                {
                    list = new List<Tuple<string, string, string>>();
                    pairsByTest[testName] = list;
                }
                list.Add(Tuple.Create($"{a} VS {b}", a, b));
            }
            if (pairsByTest.Count == 0)
            {
                throw new ArgumentException("Se requiere 'pairs' con pares {test, a, b} y su 'sets_index'.");
            }

            var clash = doc.GetClash();
            var report = new List<object>();
            var totalMatched = 0;
            var totalEdited = 0;
            // Which GUIDs each test actually accepted an edit for, so the
            // verification pass can re-read exactly those instead of counting
            // whatever happens to carry the status now.
            var editedByTest = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);

            foreach (var group in pairsByTest)
            {
                var test = clash.TestsData.Tests
                    .OfType<ClashTest>()
                    .FirstOrDefault(t => string.Equals(t.DisplayName, group.Key, StringComparison.OrdinalIgnoreCase));
                if (test == null)
                {
                    report.Add(new Dictionary<string, object> { ["test"] = group.Key, ["error"] = "test no existe" });
                    continue;
                }

                var results = new List<ClashResult>();
                CollectResults(test, results);
                BridgeHost.Log($"Reglas [{group.Key}]: {results.Count} resultados, clasificando...");

                var perRule = group.Value.ToDictionary(p => p.Item1, p => 0.0, StringComparer.OrdinalIgnoreCase);
                var toEdit = new List<Guid>();

                foreach (var result in results)
                {
                    if (onlyNew && result.Status != ClashResultStatus.New) continue;

                    var info1 = DescribeForRules(result.Item1);
                    var info2 = DescribeForRules(result.Item2);

                    foreach (var pair in group.Value)
                    {
                        var defA = setDefs[pair.Item2];
                        var defB = setDefs[pair.Item3];
                        var direct = MemberForRules(info1, defA) && MemberForRules(info2, defB);
                        var crossed = MemberForRules(info1, defB) && MemberForRules(info2, defA);
                        if (!direct && !crossed) continue;

                        perRule[pair.Item1] = perRule[pair.Item1] + 1;
                        toEdit.Add(result.Guid);
                        break;
                    }
                }

                totalMatched += toEdit.Count;
                BridgeHost.Log($"Reglas [{group.Key}]: {toEdit.Count} coinciden con pares ignorados" +
                               (dryRun ? " (dry-run, sin editar)" : ", editando estados..."));
                if (!dryRun)
                {
                    // Editar re-resolviendo cada resultado FRESCO por GUID: los
                    // objetos coleccionados arriba mueren (WeakRef) en cuanto
                    // el documento muta, y editar sobre uno muerto no es una
                    // excepción manejable — es un crash nativo de Navisworks.
                    var done = 0;
                    var edited = new List<string>();
                    foreach (var guid in toEdit)
                    {
                        try
                        {
                            var fresh = clash.TestsData.ResolveGuid(guid) as ClashResult;
                            if (fresh == null) continue;
                            EditResultStatus(clash.TestsData, fresh, status);
                            edited.Add(guid.ToString());
                            totalEdited++;
                        }
                        catch
                        {
                            // Un resultado que no acepta el cambio se reporta
                            // como diferencia matched/edited, no revienta todo.
                        }
                        if (++done % 1000 == 0)
                        {
                            BridgeHost.Log($"Reglas [{group.Key}]: {done}/{toEdit.Count} editados");
                        }
                    }
                    editedByTest[group.Key] = edited;
                    BridgeHost.Log($"Reglas [{group.Key}]: listo, {done} procesados");
                }

                report.Add(new Dictionary<string, object>
                {
                    ["test"] = group.Key,
                    ["results_total"] = (double)results.Count,
                    ["matched"] = (double)toEdit.Count,
                    ["by_rule"] = perRule.ToDictionary(kv => kv.Key, kv => (object)kv.Value)
                });
            }

            var response = new Dictionary<string, object>
            {
                ["dry_run"] = dryRun,
                ["status_applied"] = statusName,
                ["matched"] = (double)totalMatched,
                ["edited"] = (double)totalEdited,
                ["tests"] = report
            };

            // Declared out here because the envelope below reports it, and a
            // counter that only exists inside the branch that computes it
            // cannot be reported by the branch that has to answer for it.
            var verifiedTotal = 0;

            if (!dryRun)
            {
                // Verification: re-read the document and count the results
                // whose status the triage actually changed. `edited` counts
                // calls that did not throw, which proves nothing — this is
                // what makes the difference visible.
                var verify = new Dictionary<string, object>();
                foreach (var pair in editedByTest)
                {
                    var test = clash.TestsData.Tests
                        .OfType<ClashTest>()
                        .FirstOrDefault(t => string.Equals(t.DisplayName, pair.Key, StringComparison.OrdinalIgnoreCase));
                    if (test == null) continue;
                    var results = new List<ClashResult>();
                    CollectResults(test, results);

                    var byGuid = results.ToDictionary(r => r.Guid.ToString(), r => r.Status, StringComparer.OrdinalIgnoreCase);
                    var confirmed = pair.Value.Count(g => byGuid.TryGetValue(g, out var s) && s == status);
                    verifiedTotal += confirmed;

                    verify[pair.Key] = new Dictionary<string, object>
                    {
                        ["edited"] = (double)pair.Value.Count,
                        ["verified"] = (double)confirmed,
                        ["mismatch"] = (double)(pair.Value.Count - confirmed),
                        ["new"] = (double)results.Count(r => r.Status == ClashResultStatus.New),
                        [statusName.ToLowerInvariant()] = (double)results.Count(r => r.Status == status)
                    };
                }
                response["verified_in_document"] = verify;
                response["verified_edited"] = (double)verifiedTotal;
                response["verification_source"] = "document_reread";
            }

            return Sealed("clash/apply_rules", payload, response,
                requested: (int)Json.Num(response, "matched", 0),
                applied: dryRun ? 0 : (int)Json.Num(response, "edited", 0),
                verified: dryRun ? 0 : verifiedTotal,
                verificationSource: dryRun ? VerificationSources.NotApplicable : VerificationSources.DocumentReread,
                dryRun: dryRun);
        }

        private static void CollectResults(SavedItem node, List<ClashResult> into)
        {
            if (node is ClashResult result && !result.IsGroup)
            {
                into.Add(result);
                return;
            }
            if (node is GroupItem group)
            {
                foreach (var child in group.Children) CollectResults(child, into);
            }
        }

        private static Tuple<string, string> DescribeForRules(ModelItem item)
        {
            try
            {
                if (item == null) return Tuple.Create(string.Empty, string.Empty);
                var categoryId = NavisContext.PropertyOf(item, "CategoryId");
                var root = item;
                var depth = 0;
                while (root.Parent != null && depth++ < 64) root = root.Parent;
                return Tuple.Create(root.DisplayName ?? string.Empty, categoryId ?? string.Empty);
            }
            catch
            {
                // Un ítem ilegible no pertenece a ningún par: se salta, no tumba.
                return Tuple.Create(string.Empty, string.Empty);
            }
        }

        private static bool MemberForRules(Tuple<string, string> info, IgnoreSetDef def)
        {
            if (!string.IsNullOrWhiteSpace(def.Token) &&
                info.Item1.IndexOf(def.Token, StringComparison.OrdinalIgnoreCase) < 0)
            {
                return false;
            }
            return def.CategoryIds.Count == 0 || def.CategoryIds.Contains(info.Item2);
        }

        private static SelectionSet FindSet(Document doc, string name)
            => doc.SelectionSets.RootItem.Children
                   .FirstOrDefault(s => string.Equals(s.DisplayName, name, StringComparison.OrdinalIgnoreCase))
               as SelectionSet;

        // ---------------------------------------------------- clash matrix

        /// <summary>
        /// Generates the whole discipline-versus-discipline test suite in one
        /// call, each pair with the test type and tolerance the profile
        /// prescribes.
        /// </summary>
        public static Dictionary<string, object> BuildClashMatrix(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("clash/matrix", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var dryRun = Json.Bool(payload, "dry_run", true);
            var prefix = Json.Str(payload, "prefix", "NC");
            var scale = NavisContext.MetreScale(doc);
            var replace = Json.Bool(payload, "replace_existing", false);

            var pairs = Json.Arr(payload, "pairs");
            if (pairs.Count == 0) throw new ArgumentException("Se requiere 'pairs' con al menos un par de disciplinas.");

            var clash = doc.GetClash();
            var existing = clash.TestsData.Tests
                .Select(t => t.DisplayName ?? string.Empty)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);

            var plan = new List<object>();
            var toCreate = new List<(string Name, List<SelectionSet> SetsA, List<SelectionSet> SetsB, ClashTestType Type, double Tolerance)>();

            foreach (var raw in pairs)
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                var a = Json.Str(spec, "a");
                var b = Json.Str(spec, "b");
                var typeName = Json.Str(spec, "type", "Hard");
                var toleranceM = Json.Num(spec, "tolerance_m", 0.001);
                var name = $"{prefix} {a} vs {b}";

                // The server names the sets for each side, having matched the
                // document's own against the profile's vocabulary. Falling
                // back to `{prefix} - {code}` keeps an older caller working.
                var namesA = Json.StrArr(spec, "sets_a");
                var namesB = Json.StrArr(spec, "sets_b");
                if (namesA.Count == 0) namesA = new List<string> { $"{prefix} - {a}" };
                if (namesB.Count == 0) namesB = new List<string> { $"{prefix} - {b}" };

                var setsA = namesA.Select(n => FindSet(doc, n)).Where(s => s != null).ToList();
                var setsB = namesB.Select(n => FindSet(doc, n)).Where(s => s != null).ToList();
                var skip = setsA.Count == 0 || setsB.Count == 0
                    ? $"faltan conjuntos ({string.Join(", ", namesA)} / {string.Join(", ", namesB)})"
                    : existing.Contains(name) && !replace
                        ? "ya existe un test con ese nombre"
                        : null;

                plan.Add(new Dictionary<string, object>
                {
                    ["name"] = name,
                    ["type"] = typeName,
                    ["tolerance_m"] = toleranceM,
                    ["sets_a"] = setsA.Select(s => (object)s.DisplayName).ToList(),
                    ["sets_b"] = setsB.Select(s => (object)s.DisplayName).ToList(),
                    ["skipped"] = skip ?? string.Empty
                });

                if (skip == null && Enum.TryParse<ClashTestType>(typeName, true, out var testType))
                {
                    // Tolerance travels in metres over the bridge and must be
                    // converted back into document units before it reaches
                    // Navisworks, or a millimetre becomes a metre on a
                    // project authored in feet.
                    toCreate.Add((name, setsA, setsB, testType, toleranceM / (scale == 0 ? 1 : scale)));
                }
            }

            if (dryRun)
            {
                return Sealed("clash/matrix", payload, new Dictionary<string, object>
                {
                    ["action"] = "build_matrix",
                    ["would_create"] = plan
                },
                requested: plan.Count, applied: 0, verified: 0,
                verificationSource: VerificationSources.NotApplicable, dryRun: true);
            }

            var created = 0;
            foreach (var spec in toCreate)
            {
                if (spec.SetsA.Count == 0 || spec.SetsB.Count == 0) continue;

                if (replace)
                {
                    var duplicate = clash.TestsData.Tests
                        .OfType<ClashTest>()
                        .FirstOrDefault(t => string.Equals(t.DisplayName, spec.Name, StringComparison.OrdinalIgnoreCase));
                    if (duplicate != null) clash.TestsData.TestsRemove(duplicate);
                }

                var test = new ClashTest
                {
                    DisplayName = spec.Name,
                    TestType = spec.Type,
                    Tolerance = spec.Tolerance
                };
                // Pointed at the SETS, not at a snapshot of their contents.
                //
                // Copying the explicit items froze whatever the set held the
                // moment the matrix was built, so a set rebuilt afterwards
                // left the test comparing yesterday's elements — and it made
                // search sets unusable, because they have no items to copy.
                // A selection source resolves at run time and works for both.
                test.SelectionA.Selection.CopyFrom(SourcesFor(doc, spec.SetsA));
                test.SelectionB.Selection.CopyFrom(SourcesFor(doc, spec.SetsB));
                clash.TestsData.TestsAddCopy(test);
                created++;
            }

            // Re-read each created test and check what it actually IS, not
            // just that something with a matching name turned up: the type,
            // the tolerance, and that both sides resolve to saved items. A
            // name-prefix count would pass over a test whose sources were
            // never bound.
            var verified = new List<object>();
            var soundTests = 0;
            foreach (var spec in toCreate)
            {
                if (spec.SetsA.Count == 0 || spec.SetsB.Count == 0) continue;
                var live = doc.GetClash().TestsData.Tests
                    .OfType<ClashTest>()
                    .FirstOrDefault(t => string.Equals(t.DisplayName, spec.Name,
                        StringComparison.OrdinalIgnoreCase));
                var sourcesA = live == null ? 0 : SourceGuids(doc, live.SelectionA).Count;
                var sourcesB = live == null ? 0 : SourceGuids(doc, live.SelectionB).Count;
                var sound = live != null &&
                            live.TestType == spec.Type &&
                            Math.Abs(live.Tolerance - spec.Tolerance) < 0.0001 &&
                            sourcesA > 0 && sourcesB > 0;
                if (sound) soundTests++;
                verified.Add(new Dictionary<string, object>
                {
                    ["name"] = spec.Name,
                    ["exists"] = live != null,
                    ["type_ok"] = live != null && live.TestType == spec.Type,
                    ["tolerance_ok"] = live != null &&
                                       Math.Abs(live.Tolerance - spec.Tolerance) < 0.0001,
                    ["sources_a"] = (double)sourcesA,
                    ["sources_b"] = (double)sourcesB,
                    ["ok"] = sound
                });
            }

            return Sealed("clash/matrix", payload, new Dictionary<string, object>
            {
                ["action"] = "build_matrix",
                ["planned"] = plan,
                ["created"] = (double)created,
                ["verified_in_document"] = verified
            },
            requested: toCreate.Count, applied: created,
            verified: Math.Min(created, soundTests),
            verificationSource: VerificationSources.SelectionSourceReread,
            warnings: soundTests < created
                ? new[] { (created - soundTests) + " test(s) se crearon pero no releen con el " +
                          "tipo, la tolerancia o las fuentes esperadas." }
                : null);
        }

        /// <summary>
        /// Runs clash tests, re-resolving each one around the mutation.
        /// </summary>
        /// <remarks>
        /// TestsRunTest rebuilds the results tree and disposes the native
        /// handles behind every ClashTest instance obtained beforehand.
        /// Holding a reference across the call and reading it afterwards
        /// throws "Object has been Disposed (WeakRef)" — so the work is
        /// driven off test *names*, and the object is looked up fresh both
        /// before and after each run.
        /// </remarks>
        public static Dictionary<string, object> RunTests(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("clash/run", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var requested = new HashSet<string>(Json.StrArr(payload, "tests"), StringComparer.OrdinalIgnoreCase);

            // Snapshot names only. Any object captured here would be dead by
            // the second iteration.
            var names = doc.GetClash().TestsData.Tests
                .OfType<ClashTest>()
                .Select(t => t.DisplayName ?? string.Empty)
                .Where(n => requested.Count == 0 || requested.Contains(n))
                .ToList();

            var ran = new List<object>();
            foreach (var name in names)
            {
                var before = CountByName(doc, name);

                var test = FindTest(doc, name);
                if (test == null) continue;
                doc.GetClash().TestsData.TestsRunTest(test);

                // Fresh lookups: everything from before the run is invalid.
                var after = CountByName(doc, name);
                var status = FindTest(doc, name)?.Status.ToString() ?? "Unknown";

                ran.Add(new Dictionary<string, object>
                {
                    ["name"] = name,
                    ["status"] = status,
                    ["results_before"] = (double)before,
                    ["results_after"] = (double)after
                });
            }

            if (ran.Count == 0)
            {
                throw new InvalidOperationException(
                    "Ningún test coincidió. Verifica los nombres con clash/tests.");
            }

            // Only Complete counts, for the same reason it does in Run All:
            // Old means the results describe a model that has since moved.
            var complete = ran
                .OfType<Dictionary<string, object>>()
                .Count(t => string.Equals(Json.Str(t, "status"), RunVerification.Complete,
                    StringComparison.OrdinalIgnoreCase));
            return Sealed("clash/run", payload,
                new Dictionary<string, object> { ["action"] = "run_tests", ["tests"] = ran },
                requested: names.Count, applied: ran.Count, verified: complete,
                verificationSource: VerificationSources.ClashTestStatusReread,
                warnings: complete < ran.Count
                    ? new[] { (ran.Count - complete) + " test(s) no quedaron Complete: sus " +
                              "resultados no cuentan como verificados." }
                    : null);
        }

        private static ClashTest FindTest(Document doc, string name)
            => doc.GetClash().TestsData.Tests
                .OfType<ClashTest>()
                .FirstOrDefault(t => string.Equals(t.DisplayName, name, StringComparison.OrdinalIgnoreCase));

        private static int CountByName(Document doc, string name)
        {
            var test = FindTest(doc, name);
            return test == null ? 0 : Router.CountResults(test);
        }

        // ---------------------------------------------------- clash groups

        /// <summary>
        /// Writes the computed issues back as clash groups, so the analysis
        /// lands in the .nwf the team actually opens.
        /// </summary>
        public static Dictionary<string, object> ApplyGroups(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("clash/group", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var dryRun = Json.Bool(payload, "dry_run", true);
            var groups = Json.Arr(payload, "groups");
            if (groups.Count == 0) throw new ArgumentException("Se requiere 'groups'.");

            // Names, taken once. Nothing below dereferences a wrapper captured
            // before a mutation — the previous version asked
            // `index[guid].Test.DisplayName` while building the second group,
            // by which time the first move had already rebuilt the tree.
            var owners = BuildOwnerIndex(doc.GetClash());

            var plan = new List<object>();
            foreach (var raw in groups)
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                var guids = Json.StrArr(spec, "clash_guids");
                var found = guids.Count(g => owners.ContainsKey(g));

                // A Navisworks clash group lives under exactly one test, so an
                // issue whose clashes span several becomes several groups. The
                // dry run has to say so: a plan that under-reports what the
                // commit will do is worse than no plan at all.
                var spanned = ClashPlanning.OwningTests(guids, owners);
                plan.Add(new Dictionary<string, object>
                {
                    ["name"] = Json.Str(spec, "name"),
                    ["requested"] = (double)guids.Count,
                    ["found_in_document"] = (double)found,
                    ["missing"] = (double)(guids.Count - found),
                    ["tests_spanned"] = spanned,
                    ["groups_to_create"] = (double)spanned.Count,
                    ["note"] = spanned.Count > 1
                        ? "Los cruces pertenecen a varios tests; se creará un grupo por test."
                        : spanned.Count == 0
                            ? "Ningún cruce se encontró en el documento; no se creará nada."
                            : string.Empty
                });
            }

            if (dryRun)
            {
                return Sealed("clash/group", payload, new Dictionary<string, object>
                {
                    ["action"] = "apply_groups",
                    ["would_create"] = plan
                },
                requested: plan.Count, applied: 0, verified: 0,
                verificationSource: VerificationSources.NotApplicable, dryRun: true);
            }

            var created = new List<object>();
            foreach (var raw in groups)
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                var name = Json.Str(spec, "name");
                var guids = Json.StrArr(spec, "clash_guids");
                if (string.IsNullOrWhiteSpace(name) || guids.Count == 0) continue;

                // Every result in a group must live under the same test, so
                // the group is created inside whichever test owns them. The
                // split is computed from names alone, in a function that has
                // never seen the API — see ClashPlanning.
                foreach (var step in ClashPlanning.PlanIssue(name, guids, owners))
                {
                    var testName = step.TestName;
                    var groupName = step.GroupName;
                    var mine = step.Guids;

                    var host = FindTest(doc, testName);
                    if (host == null) continue;
                    // Reusar el grupo si ya existe (re-agrupar tras cada corrida
                    // duplicaba "Nivel 05" en vez de llenar el existente).
                    var existente = host.Children
                        .OfType<ClashResultGroup>()
                        .Any(g => string.Equals(g.DisplayName, groupName, StringComparison.Ordinal));
                    if (!existente)
                    {
                        // Fetched here, not held from the top of the handler:
                        // the previous group's moves have rebuilt the tree.
                        doc.GetClash().TestsData.TestsAddCopy(
                            host, new ClashResultGroup { DisplayName = groupName });
                    }

                    // Every TestsMove rebuilds the children tree and disposes
                    // the handles held from before it, so both the test and
                    // the group are re-resolved on each pass. Indices shift
                    // too, which is why the position is found by GUID.
                    var moved = 0;
                    var lost = new List<string>();
                    foreach (var guid in mine)
                    {
                        var currentTest = FindTest(doc, testName);
                        var target = currentTest?.Children
                            .OfType<ClashResultGroup>()
                            .LastOrDefault(g => string.Equals(g.DisplayName, groupName, StringComparison.Ordinal));
                        if (currentTest == null || target == null) break;

                        var position = currentTest.Children
                            .Select((child, i) => new { child, i })
                            .FirstOrDefault(x => x.child is ClashResult r && r.Guid.ToString() == guid);
                        if (position == null)
                        {
                            // Already moved, or gone. Either way there is no
                            // handle to fall back on: the one from the plan
                            // died with the tree that produced it.
                            lost.Add(guid);
                            continue;
                        }

                        doc.GetClash().TestsData.TestsMove(currentTest, position.i, target, target.Children.Count);
                        moved++;
                    }

                    var verified = FindTest(doc, testName)?.Children
                        .OfType<ClashResultGroup>()
                        .LastOrDefault(g => string.Equals(g.DisplayName, groupName, StringComparison.Ordinal));

                    // Which of the requested GUIDs are actually inside it. The
                    // child COUNT includes whatever the group already held, so
                    // a group that gained nothing could still look healthy.
                    var inside = verified == null
                        ? new HashSet<string>(StringComparer.OrdinalIgnoreCase)
                        : new HashSet<string>(
                            Router.EnumerateResults(verified).Select(r => r.Guid.ToString()),
                            StringComparer.OrdinalIgnoreCase);
                    var members = mine.Count(inside.Contains);

                    created.Add(new Dictionary<string, object>
                    {
                        ["name"] = groupName,
                        ["issue"] = name,
                        ["test"] = testName,
                        ["requested"] = (double)mine.Count,
                        ["moved"] = (double)moved,
                        ["not_resolved"] = lost.Cast<object>().ToList(),
                        ["verified_children"] = (double)(verified?.Children.Count ?? 0),
                        ["verified_members"] = (double)members,
                        ["outcome"] = ClashPlanning.Outcome(mine.Count, moved)
                    });
                }
            }

            var requestedMoves = created.OfType<Dictionary<string, object>>()
                .Sum(g => (int)Json.Num(g, "requested", 0));
            var movedTotal = created.OfType<Dictionary<string, object>>()
                .Sum(g => (int)Json.Num(g, "moved", 0));
            // Membership by GUID. `verified_children` is the group's child
            // COUNT, which includes anything that was already in it — so a
            // group that gained nothing could still report a healthy number.
            var verifiedMembers = created.OfType<Dictionary<string, object>>()
                .Sum(g => (int)Json.Num(g, "verified_members", 0));
            return Sealed("clash/group", payload, new Dictionary<string, object>
            {
                ["action"] = "apply_groups",
                ["groups"] = created
            },
            requested: requestedMoves, applied: movedTotal,
            verified: Math.Min(movedTotal, verifiedMembers),
            verificationSource: VerificationSources.GroupMembershipReread,
            warnings: verifiedMembers < movedTotal
                ? new[] { (movedTotal - verifiedMembers) + " resultado(s) se movieron pero no " +
                          "aparecen dentro del grupo al releer por GUID." }
                : null);
        }

        public static Dictionary<string, object> SetStatus(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("clash/status", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var dryRun = Json.Bool(payload, "dry_run", true);
            var statusName = Json.Str(payload, "status", "Reviewed");
            if (!Enum.TryParse<ClashResultStatus>(statusName, true, out var status))
            {
                throw new ArgumentException(
                    $"Estado '{statusName}' no válido. Usa: New, Active, Reviewed, Approved, Resolved.");
            }

            var guids = Json.StrArr(payload, "clash_guids");

            // A snapshot of names, taken once and never dereferenced. The
            // wrappers that produced it are dropped with the index.
            var owners = BuildOwnerIndex(doc.GetClash());
            var targets = guids.Where(owners.ContainsKey).ToList();

            if (dryRun)
            {
                return Sealed("clash/status", payload, new Dictionary<string, object>
                {
                    ["action"] = "set_status",
                    ["clash_status"] = status.ToString(),
                    ["would_change"] = (double)targets.Count,
                    ["not_found"] = (double)(guids.Count - targets.Count)
                },
                requested: guids.Count, applied: 0, verified: 0,
                verificationSource: VerificationSources.NotApplicable, dryRun: true);
            }

            // Every status edit can rebuild the tree, and a ClashResult held
            // across one is a dangling native pointer — reusing it does not
            // raise a managed exception, it kills the process. So the result
            // is found again, through its owning test, immediately before the
            // call that touches it, and nothing survives the iteration.
            var report = ClashPlanning.ApplyEach(
                targets,
                guid =>
                {
                    var test = FindTest(doc, owners[guid]);
                    return test == null ? null : FindResult(test, guid);
                },
                (guid, result) => EditResultStatus(doc.GetClash().TestsData, result, status));
            var changed = report.Applied;
            var vanished = report.Vanished;

            // Verify by re-reading rather than by counting successful calls.
            var confirmed = 0;
            foreach (var guid in targets)
            {
                var test = FindTest(doc, owners[guid]);
                var result = test == null ? null : FindResult(test, guid);
                if (result != null && result.Status == status) confirmed++;
            }

            return Sealed("clash/status", payload, new Dictionary<string, object>
            {
                ["action"] = "set_status",
                ["clash_status"] = status.ToString(),
                ["attempted"] = (double)changed,
                ["verified_in_document"] = (double)confirmed,
                ["mismatch"] = (double)(changed - confirmed),
                ["vanished"] = vanished.Cast<object>().ToList(),
                ["status_outcome"] = ClashPlanning.Outcome(targets.Count, confirmed)
            },
            requested: guids.Count, applied: changed, verified: confirmed,
            verificationSource: VerificationSources.DocumentReread);
        }

        public static Dictionary<string, object> SaveViewpoints(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("viewpoints/save", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var dryRun = Json.Bool(payload, "dry_run", true);
            var folder = Json.Str(payload, "folder", "NavisCoord");
            var items = Json.Arr(payload, "viewpoints");

            var clash = doc.GetClash();
            var index = BuildResultIndex(clash);

            var resolvable = items
                .OfType<Dictionary<string, object>>()
                .Count(spec => index.ContainsKey(Json.Str(spec, "clash_guid")));

            if (dryRun)
            {
                return Sealed("viewpoints/save", payload, new Dictionary<string, object>
                {
                    ["action"] = "save_viewpoints",
                    ["would_save"] = (double)resolvable,
                    ["not_found"] = (double)(items.Count - resolvable)
                },
                requested: items.Count, applied: 0, verified: 0,
                verificationSource: VerificationSources.NotApplicable, dryRun: true);
            }

            var before = doc.SavedViewpoints.RootItem.Children.Count;
            var saved = 0;
            var savedNames = new List<string>();
            foreach (var raw in items)
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                var guid = Json.Str(spec, "clash_guid");
                if (!index.TryGetValue(guid, out var entry)) continue;

                var viewpoint = clash.TestsData.TestsViewpointForResult(entry.Result);
                if (viewpoint == null) continue;

                var name = Json.Str(spec, "name", $"{folder} - {entry.Result.DisplayName}");
                doc.SavedViewpoints.AddCopy(new SavedViewpoint(viewpoint) { DisplayName = name });
                savedNames.Add(name);
                saved++;
            }

            // Re-read by name, not by counting. A delta of N proves N items
            // appeared; it does not prove they are the N that were asked for,
            // and it silently absorbs an unrelated viewpoint added meanwhile.
            var present = new HashSet<string>(
                doc.SavedViewpoints.RootItem.Children.Select(v => v.DisplayName ?? string.Empty),
                StringComparer.Ordinal);
            var confirmed = savedNames.Count(n => present.Contains(n));
            var after = doc.SavedViewpoints.RootItem.Children.Count;

            return Sealed("viewpoints/save", payload, new Dictionary<string, object>
            {
                ["action"] = "save_viewpoints",
                ["attempted"] = (double)saved,
                ["viewpoints_before"] = (double)before,
                ["viewpoints_after"] = (double)after,
                ["verified_in_document"] = (double)confirmed
            },
            requested: items.Count, applied: saved, verified: confirmed,
            verificationSource: VerificationSources.SavedViewpointReread,
            warnings: confirmed < saved
                ? new[] { (saved - confirmed) + " punto(s) de vista no se encontraron al releer." }
                : null);
        }

        // ------------------------------------------------------ appearance

        public static Dictionary<string, object> ColorElements(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("appearance/color", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var pathIds = Json.StrArr(payload, "path_ids");
            var unique = pathIds.Distinct(StringComparer.Ordinal).Count();
            var items = NavisContext.ResolveMany(doc, pathIds);
            if (items.Count == 0)
            {
                throw new InvalidOperationException("Ningún elemento se pudo resolver a partir de 'path_ids'.");
            }

            var color = Color.FromByteRGB(
                (byte)Math.Min(255, Math.Max(0, Json.Int(payload, "r", 255))),
                (byte)Math.Min(255, Math.Max(0, Json.Int(payload, "g", 0))),
                (byte)Math.Min(255, Math.Max(0, Json.Int(payload, "b", 0))));

            // What was there before, captured BEFORE the override lands.
            // Without this, "reset" can only mean "remove every permanent
            // material", because nothing remembers which ones were ours.
            var appearanceOperation = AppearanceLedger.NewOperationId();
            var originals = new List<AppearanceLedger.Original>();
            foreach (var item in items)
            {
                originals.Add(CaptureAppearance(doc, item));
            }
            var ledgerEntry = AppearanceLedger.Remember(
                appearanceOperation, DocumentContext.Fingerprint(doc), originals);

            doc.Models.OverridePermanentColor(items, color);

            var transparency = Json.Num(payload, "transparency", -1.0);
            var wantsTransparency = transparency >= 0.0 && transparency <= 1.0;
            if (wantsTransparency)
            {
                doc.Models.OverridePermanentTransparency(items, transparency);
            }

            // Re-read, not recounted.
            //
            // `items.Count` is how many ModelItems RESOLVED — measured before
            // the override and therefore evidence of nothing about it. The
            // colour is read back off the geometry: `ModelGeometry` exposes
            // `PermanentColor` and `PermanentTransparency` as ordinary
            // getters, in 2024, 2025 and 2026 alike, so there is no excuse for
            // reporting a resolution count as a verification.
            var confirmed = CountWithAppearance(items, color, wantsTransparency ? transparency : -1.0);

            // Reported against the DISTINCT count. The same element appears in
            // many clashes, so measuring against the raw request makes routine
            // de-duplication look like a resolution failure.
            return Sealed("appearance/color", payload, new Dictionary<string, object>
            {
                ["action"] = "color",
                ["appearance_operation_id"] = appearanceOperation,
                ["restore"] = ledgerEntry.Describe(),
                ["unique_requested"] = (double)unique,
                ["resolved"] = (double)items.Count,
                ["unresolved"] = (double)Math.Max(0, unique - items.Count),
                ["verified_in_document"] = (double)confirmed
            },
            requested: unique, applied: items.Count, verified: confirmed,
            verificationSource: VerificationSources.AppearanceOverrideReread,
            warnings: confirmed < items.Count
                ? new[] { (items.Count - confirmed) + " elemento(s) resolvieron pero no " +
                          "devuelven el override al releerlos." }
                : null);
        }

        public static Dictionary<string, object> ResetAppearance(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("appearance/reset", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var pathIds = Json.StrArr(payload, "path_ids");

            var operationId = Json.Str(payload, "appearance_operation_id");
            var fingerprint = DocumentContext.Fingerprint(doc);

            // An empty call used to mean ResetAllPermanentMaterials(). That is
            // not "undo what I did", it is "delete every permanent override in
            // this model" — including a colour scheme somebody built for a
            // client presentation. The two read identically from outside and
            // only one of them is recoverable, so the empty call is now a
            // refusal that says which arguments would work.
            if (pathIds.Count == 0 && string.IsNullOrWhiteSpace(operationId))
            {
                return Refusal("appearance/reset", "missing_argument", fingerprint,
                    "Indica 'appearance_operation_id' para deshacer un coloreado de " +
                    "NavisCoord, o 'path_ids' para unos elementos concretos. Una llamada " +
                    "vacía borraría TODAS las apariencias permanentes del modelo, " +
                    "incluidas las que no puso NavisCoord.");
            }

            // ------------------------------------------------ by operation
            if (!string.IsNullOrWhiteSpace(operationId))
            {
                var operation = AppearanceLedger.Find(operationId, fingerprint, out var refusalText);
                if (operation == null)
                {
                    return Refusal("appearance/reset", "unknown_operation", fingerprint, refusalText);
                }

                var restored = 0;
                foreach (var original in operation.Elements)
                {
                    var item = NavisContext.Resolve(doc, original.PathId);
                    if (item == null) continue;
                    var one = new ModelItemCollection { item };
                    if (original.HadOverride)
                    {
                        // Put back exactly what was there — including the
                        // transparency, which a plain reset would drop.
                        doc.Models.OverridePermanentColor(
                            one, Color.FromByteRGB((byte)original.R, (byte)original.G, (byte)original.B));
                        doc.Models.OverridePermanentTransparency(one, original.Transparency);
                    }
                    else
                    {
                        // No override before: restore the ABSENCE of one. This
                        // is the distinction a naive "set it back to the
                        // default colour" loses, and it leaves a permanent
                        // material nobody asked for.
                        doc.Models.ResetPermanentMaterials(one);
                    }
                    if (!HasOverride(item) == !original.HadOverride) restored++;
                }

                return Sealed("appearance/reset", payload, new Dictionary<string, object>
                {
                    ["action"] = "reset_appearance",
                    ["scope"] = "operación " + operationId,
                    ["appearance_operation_id"] = operationId,
                    ["elements"] = (double)operation.Elements.Count,
                    ["verified_in_document"] = (double)restored
                },
                requested: operation.Elements.Count,
                applied: operation.Elements.Count,
                verified: restored,
                verificationSource: VerificationSources.AppearanceOverrideReread,
                warnings: restored < operation.Elements.Count
                    ? new[] { (operation.Elements.Count - restored) + " elemento(s) no " +
                              "volvieron a su apariencia original al releerlos." }
                    : null);
            }

            var items = NavisContext.ResolveMany(doc, pathIds);
            doc.Models.ResetPermanentMaterials(items);

            // Verified by re-reading each item's geometry: a reset item is one
            // whose permanent colour and transparency match its original.
            var cleared = items.Count(item => !HasOverride(item));
            return Sealed("appearance/reset", payload, new Dictionary<string, object>
            {
                ["action"] = "reset_appearance",
                ["scope"] = "selección",
                ["resolved"] = (double)items.Count,
                ["verified_in_document"] = (double)cleared
            },
            requested: pathIds.Distinct(StringComparer.Ordinal).Count(),
            applied: items.Count, verified: cleared,
            verificationSource: VerificationSources.AppearanceOverrideReread,
            warnings: cleared < items.Count
                ? new[] { (items.Count - cleared) + " elemento(s) conservan el override." }
                : null);
        }

        public static Dictionary<string, object> SelectItems(Dictionary<string, object> payload)
        {
            // Same policy as the job path: which document, and is it the one the
            // caller planned against. Protecting only job/submit left every direct
            // call editing whatever happened to be open.
            var refused = MutationPreflight("selection/set", payload);
            if (refused != null) return refused;

            var doc = Router.RequireDocument();
            var pathIds = Json.StrArr(payload, "path_ids");
            var unique = pathIds.Distinct(StringComparer.Ordinal).Count();
            var items = NavisContext.ResolveMany(doc, pathIds);

            doc.CurrentSelection.Clear();
            doc.CurrentSelection.CopyFrom(items);

            // Re-read AND compared by identity. A matching count is not a
            // matching selection: the same number of items can be the wrong
            // items, which is exactly what a partially failed resolve leaves
            // behind.
            var live = doc.CurrentSelection.SelectedItems;
            var selected = live.Count;
            var wanted = new HashSet<string>(
                items.Select(item => NavisContext.PathId(doc, item)), StringComparer.Ordinal);
            var confirmed = live.Count(item => wanted.Contains(NavisContext.PathId(doc, item)));

            return Sealed("selection/set", payload, new Dictionary<string, object>
            {
                ["action"] = "select",
                ["unique_requested"] = (double)unique,
                ["selected"] = (double)selected,
                ["verified_in_document"] = (double)confirmed,
                ["unresolved"] = (double)Math.Max(0, unique - selected)
            },
            requested: unique, applied: items.Count, verified: confirmed,
            verificationSource: VerificationSources.CurrentSelectionReread,
            warnings: confirmed < items.Count
                ? new[] { "La selección viva no contiene " + (items.Count - confirmed) +
                          " de los elementos resueltos." }
                : null);
        }

        // --------------------------------------------------------- helpers

        private struct ResultEntry
        {
            public ClashResult Result;
            public ClashTest Test;
        }

        /// <summary>
        /// Calls TestsEditResultStatus across API versions.
        /// </summary>
        /// <remarks>
        /// The signature changed between releases: v21 (2024) takes
        /// (result, status); v22+ adds a current_user assignee. One source
        /// compiling against both means neither call can be written directly,
        /// so the method is resolved at runtime. The status write is verified
        /// by re-reading the document afterwards either way, so a version
        /// where this dispatch misbehaves shows up as a mismatch in the
        /// response, never as a silent success.
        /// </remarks>
        private static System.Reflection.MethodInfo _editStatusMethod;

        private static void EditResultStatus(
            DocumentClashTests data, ClashResult result, ClashResultStatus status)
        {
            // Cachear el MethodInfo: el triaje de reglas puede editar miles de
            // resultados y re-enumerar los métodos en cada uno es puro costo.
            if (_editStatusMethod == null)
            {
                _editStatusMethod = typeof(DocumentClashTests).GetMethods()
                    .FirstOrDefault(m => m.Name == "TestsEditResultStatus" &&
                                         (m.GetParameters().Length == 2 || m.GetParameters().Length == 3));
                if (_editStatusMethod == null)
                {
                    throw new MissingMethodException(
                        "TestsEditResultStatus no existe con una firma conocida en esta versión del API.");
                }
            }

            var parameters = _editStatusMethod.GetParameters();
            if (parameters.Length == 2)
            {
                _editStatusMethod.Invoke(data, new object[] { result, status });
                return;
            }

            // El tercer parámetro (Assignee) NUNCA puede ir null: en resultados
            // recién corridos AssignedTo viene vacío y la capa nativa con null
            // no lanza una excepción manejable — tumba Navisworks entero. Se
            // construye un assignee vacío por reflexión (el tipo no existe en
            // 2024, por eso no se puede nombrar en el código).
            object assignee = result.AssignedTo;
            if (assignee == null)
            {
                assignee = Activator.CreateInstance(parameters[2].ParameterType);
            }
            _editStatusMethod.Invoke(data, new object[] { result, status, assignee });
        }

        // `OwningTests` used to live here, reading `.Test.DisplayName` off the
        // wrappers in a pre-mutation index. It is gone rather than kept for
        // convenience: leaving it in place is leaving the loaded gun on the
        // table for whoever adds the next handler. Its replacement takes a map
        // of names and lives in ClashPlanning, where it is tested.

        /// <summary>GUID → name of the test that owns it. Strings only.</summary>
        /// <remarks>
        /// The counterpart to <see cref="BuildResultIndex"/>, for everything
        /// that has to survive a mutation. The index of live wrappers is fine
        /// to read before the first edit and lethal afterwards; a map of names
        /// is still true when the tree has been rebuilt underneath it, and it
        /// is enough to find the handle again.
        /// </remarks>
        private static Dictionary<string, string> BuildOwnerIndex(DocumentClash clash)
        {
            var owners = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var saved in clash.TestsData.Tests)
            {
                if (!(saved is ClashTest test)) continue;
                var name = test.DisplayName ?? string.Empty;
                foreach (var result in Router.EnumerateResults(test))
                {
                    owners[result.Guid.ToString()] = name;
                }
            }
            return owners;
        }

        /// <summary>The saved items a clash-test side points at, by identity.</summary>
        internal static List<string> SourceGuids(Document doc, ClashSelection selection)
        {
            var guids = new List<string>();
            if (selection == null) return guids;
            SelectionSourceCollection sources;
            try { sources = selection.Selection?.SelectionSources; }
            catch (Exception) { return guids; }
            if (sources == null) return guids;

            foreach (var source in sources)
            {
                SavedItem item = null;
                try { item = doc.SelectionSets.ResolveSelectionSource(source); }
                catch (ArgumentException) { }
                catch (InvalidOperationException) { }
                if (item != null) guids.Add(item.Guid.ToString());
            }
            return guids;
        }

        /// <summary>
        /// Every clash test reduced to identities and plain values.
        /// </summary>
        /// <remarks>
        /// Taken before the first mutation and never re-read from the objects
        /// it came from: the wrappers do not survive a rebuild, and the whole
        /// point of a snapshot is to still be true afterwards.
        /// </remarks>
        internal static List<ConfigurePlanning.TestSnapshot> SnapshotTests(Document doc)
        {
            var snapshot = new List<ConfigurePlanning.TestSnapshot>();
            foreach (var saved in doc.GetClash().TestsData.Tests)
            {
                if (!(saved is ClashTest test)) continue;
                var entry = new ConfigurePlanning.TestSnapshot
                {
                    Guid = test.Guid.ToString(),
                    Name = test.DisplayName ?? string.Empty,
                    TestType = test.TestType.ToString(),
                    Tolerance = test.Tolerance,
                    ResultCount = Router.EnumerateResults(test).Count(),
                    GroupCount = test.Children.OfType<ClashResultGroup>().Count(),
                    Status = test.Status.ToString(),
                    SourceGuidsA = SourceGuids(doc, test.SelectionA),
                    SourceGuidsB = SourceGuids(doc, test.SelectionB)
                };

                // What each source looked like at this instant, so that "the
                // GUID still resolves" can later be told apart from "the GUID
                // still resolves to the same thing".
                foreach (var guid in entry.AllSourceGuids.Distinct(StringComparer.OrdinalIgnoreCase))
                {
                    entry.SourceFingerprints[guid] = Fingerprint(ResolveGuid(doc, guid) as SelectionSet);
                }
                snapshot.Add(entry);
            }
            return snapshot;
        }

        /// <summary>
        /// The migration capability this build ships with.
        /// </summary>
        /// <remarks>
        /// A constant, and deliberately not a function. Anything that computed
        /// this at run time would have to write to the document to find out,
        /// and a capability check is not entitled to mutate a project — not on
        /// a dry run, not on a read, not quietly on a real one. Reflection can
        /// confirm that `ReplaceWithCopy` exists; it cannot confirm that the
        /// copy keeps the GUID or that the clash tests stay bound to it, and
        /// those are the only two questions that matter here.
        ///
        /// Raising this is a deliberate act that belongs with an integration
        /// test running against a scratch document, never with a heuristic.
        /// While it stays Unverified, a referenced source is preserved and the
        /// step is reported blocked.
        /// </remarks>
        private const ConfigurePlanning.MigrationCapability Capability =
            ConfigurePlanning.MigrationCapability.Unverified;

        /// <summary>A named set inside a named folder, or null.</summary>
        private static SelectionSet FindSetIn(Document doc, string folder, string name)
        {
            var group = doc.SelectionSets.RootItem.Children
                .FirstOrDefault(s => string.Equals(s.DisplayName, folder, StringComparison.OrdinalIgnoreCase))
                as GroupItem;
            if (group == null) return null;
            return group.Children
                .OfType<SelectionSet>()
                .FirstOrDefault(s => string.Equals(s.DisplayName, name, StringComparison.OrdinalIgnoreCase));
        }

        /// <summary>Whether a saved set already searches for what is wanted.</summary>
        /// <remarks>
        /// `ValueEquals` is the API's own comparison, which is the only one
        /// worth trusting here — a hand-rolled diff of search conditions would
        /// be a second opinion about a question Navisworks already answers. A
        /// set holding an explicit item list has no search to compare, so it
        /// never counts as matching: it has to be rebuilt to become one.
        /// </remarks>
        private static bool SameDefinition(SelectionSet set, Search wanted)
        {
            if (set == null || wanted == null) return false;
            if (ExplicitItems(set) != null) return false;
            try { return Search.ValueEquals(set.Search, wanted); }
            catch (ArgumentException) { return false; }
            catch (InvalidOperationException) { return false; }
        }

        /// <summary>
        /// What a saved set selects, reduced to a string that survives a
        /// mutation.
        /// </summary>
        /// <remarks>
        /// `Search.ValueEquals` is the right comparison but it needs two live
        /// Search objects, and holding one across a rebuild is exactly the
        /// dangling-handle bug this file spent a while removing. A fingerprint
        /// is the version that keeps working: taken before, taken again after,
        /// compared as text.
        ///
        /// Built from what the API exposes — the conditions in order, the
        /// search locations, the prune flag — so two sets fingerprint alike
        /// only when they actually search alike. A set holding an explicit
        /// item list has no search, and says so rather than pretending to be
        /// an empty one; an empty search and no search are not the same thing.
        /// </remarks>
        private static string Fingerprint(SelectionSet set)
        {
            if (set == null) return string.Empty;
            var explicitItems = ExplicitItems(set);
            if (explicitItems != null) return "explicit:" + explicitItems.Count;
            try
            {
                var search = set.Search;
                if (search == null) return "search:none";
                var conditions = string.Join("|",
                    search.SearchConditions.Select(c => c?.ToString() ?? string.Empty));
                return "search:" + search.Locations + ":" + search.PruneBelowMatch + ":" + conditions;
            }
            catch (ArgumentException) { return "search:unreadable"; }
            catch (InvalidOperationException) { return "search:unreadable"; }
        }

        /// <summary>
        /// What every GUID a test declares actually resolves to, right now.
        /// </summary>
        /// <remarks>
        /// The counterpart to the snapshot: the snapshot says what the tests
        /// CLAIM to point at, this says what is really there. Comparing the
        /// two is the only way to tell a live link from a stale declaration —
        /// `ResolveGuid` returning non-null proves a saved item with that GUID
        /// exists, and nothing more.
        /// </remarks>
        private static Dictionary<string, ConfigurePlanning.SourceResolution> ResolveSources(
            Document doc, IEnumerable<ConfigurePlanning.TestSnapshot> tests)
        {
            var map = new Dictionary<string, ConfigurePlanning.SourceResolution>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var guid in tests.SelectMany(t => t.AllSourceGuids))
            {
                if (map.ContainsKey(guid)) continue;
                var item = ResolveGuid(doc, guid);
                map[guid] = new ConfigurePlanning.SourceResolution
                {
                    Guid = guid,
                    Resolves = item != null,
                    Fingerprint = Fingerprint(item as SelectionSet)
                };
            }
            return map;
        }

        /// <summary>One saved item, by identity, or null.</summary>
        private static SavedItem ResolveGuid(Document doc, string guid)
        {
            if (!Guid.TryParse(guid, out var parsed)) return null;
            try { return doc.SelectionSets.ResolveGuid(parsed); }
            catch (ArgumentException) { return null; }
            catch (InvalidOperationException) { return null; }
        }

        /// <summary>
        /// Whether an item still carries a permanent appearance override.
        /// </summary>
        /// <remarks>
        /// `ModelGeometry` exposes `PermanentColor` and
        /// `PermanentTransparency` as plain getters in 2024, 2025 and 2026 —
        /// each assembly was inspected — so appearance is verifiable and there
        /// is no honest reason to report a resolution count instead. An item
        /// with no geometry carries no override of its own; the override lands
        /// on its descendants.
        /// </remarks>
        private static bool HasOverride(ModelItem item)
        {
            if (item == null || !item.HasGeometry) return false;
            try
            {
                var geometry = item.Geometry;
                if (geometry == null) return false;
                return geometry.PermanentColor != geometry.OriginalColor ||
                       Math.Abs(geometry.PermanentTransparency - geometry.OriginalTransparency) > 0.001;
            }
            catch (ArgumentException) { return false; }
            catch (InvalidOperationException) { return false; }
        }

        /// <summary>How many items now read back the appearance that was set.</summary>
        private static int CountWithAppearance(
            IEnumerable<ModelItem> items, Color expected, double expectedTransparency)
        {
            var confirmed = 0;
            foreach (var item in items ?? Enumerable.Empty<ModelItem>())
            {
                // An item without geometry cannot report a colour, and the
                // override went to its descendants. Counting it as verified
                // would be counting something nobody looked at.
                if (item == null || !item.HasGeometry)
                {
                    if (item != null && item.Descendants.Any(d =>
                            d.HasGeometry && Matches(d, expected, expectedTransparency)))
                    {
                        confirmed++;
                    }
                    continue;
                }
                if (Matches(item, expected, expectedTransparency)) confirmed++;
            }
            return confirmed;
        }

        private static bool Matches(ModelItem item, Color expected, double expectedTransparency)
        {
            try
            {
                var geometry = item.Geometry;
                if (geometry == null) return false;
                var colour = geometry.PermanentColor;
                var sameColour = Math.Abs(colour.R - expected.R) < 0.01 &&
                                 Math.Abs(colour.G - expected.G) < 0.01 &&
                                 Math.Abs(colour.B - expected.B) < 0.01;
                if (!sameColour) return false;
                if (expectedTransparency < 0.0) return true;
                return Math.Abs(geometry.PermanentTransparency - expectedTransparency) < 0.01;
            }
            catch (ArgumentException) { return false; }
            catch (InvalidOperationException) { return false; }
        }

        /// <summary>One element's appearance right now, as plain values.</summary>
        /// <remarks>
        /// `HadOverride` is the field that matters. An element with no
        /// override is not the same as one overridden to its original colour,
        /// and a restore that cannot tell them apart leaves a permanent
        /// material behind that the operator never set and cannot see.
        /// </remarks>
        private static AppearanceLedger.Original CaptureAppearance(Document doc, ModelItem item)
        {
            var original = new AppearanceLedger.Original
            {
                PathId = NavisContext.PathId(doc, item)
            };
            if (item == null || !item.HasGeometry) return original;
            try
            {
                var geometry = item.Geometry;
                if (geometry == null) return original;
                var colour = geometry.PermanentColor;
                original.HadOverride = HasOverride(item);
                original.R = (int)Math.Round(colour.R * 255.0);
                original.G = (int)Math.Round(colour.G * 255.0);
                original.B = (int)Math.Round(colour.B * 255.0);
                original.Transparency = geometry.PermanentTransparency;
            }
            catch (ArgumentException) { }
            catch (InvalidOperationException) { }
            return original;
        }

        /// <summary>Every saved item at or under this one, by identity.</summary>
        private static IEnumerable<string> GuidsUnder(SavedItem item)
        {
            if (item == null) yield break;
            yield return item.Guid.ToString();
            if (item is GroupItem group)
            {
                foreach (var child in group.Children)
                {
                    foreach (var guid in GuidsUnder(child)) yield return guid;
                }
            }
        }

        /// <summary>One result, resolved fresh from the test that owns it.</summary>
        private static ClashResult FindResult(ClashTest test, string guid)
            => Router.EnumerateResults(test)
                .FirstOrDefault(r => string.Equals(
                    r.Guid.ToString(), guid, StringComparison.OrdinalIgnoreCase));

        private static Dictionary<string, ResultEntry> BuildResultIndex(DocumentClash clash)
        {
            var index = new Dictionary<string, ResultEntry>(StringComparer.OrdinalIgnoreCase);
            foreach (var saved in clash.TestsData.Tests)
            {
                if (!(saved is ClashTest test)) continue;
                foreach (var result in Router.EnumerateResults(test))
                {
                    index[result.Guid.ToString()] = new ResultEntry { Result = result, Test = test };
                }
            }
            return index;
        }
    }
}
