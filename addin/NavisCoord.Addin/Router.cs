using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Clash;

namespace NavisCoord
{
    /// <summary>
    /// Route table. Every handler runs on the UI thread, already marshalled
    /// by <see cref="UiDispatcher"/>, so handlers touch the document directly.
    /// </summary>
    internal sealed class Router
    {
        private readonly Dictionary<string, Func<Dictionary<string, object>, Dictionary<string, object>>> _routes;

        /// <summary>
        /// Routes that can run as a background job, and the handler to use
        /// when they do.
        /// </summary>
        /// <remarks>
        /// Every one of these owns the Navisworks UI thread for long enough
        /// that a synchronous caller times out. Submitting one returns a job
        /// id immediately and the caller polls <c>job/status</c>, which is
        /// answered without touching the document and therefore stays live
        /// while the work runs.
        /// </remarks>
        private readonly Dictionary<string, Func<Dictionary<string, object>, JobManager.Job, Dictionary<string, object>>> _jobRoutes;

        public Router()
        {
            _routes = new Dictionary<string, Func<Dictionary<string, object>, Dictionary<string, object>>>(
                StringComparer.OrdinalIgnoreCase)
            {
                ["health"] = Health,
                ["census"] = Census,
                ["model/schema"] = SchemaHandlers.ModelSchema,
                ["clash/tests"] = ListTests,
                ["clash/export"] = ExportClashes,
                ["clash/image"] = ReportHandlers.ClashImage,
                ["sets/build"] = WriteHandlers.BuildSearchSets,
                ["sets/build_search"] = WriteHandlers.BuildCriteriaSets,
                ["sets/list"] = WriteHandlers.ListSets,
                ["clash/matrix"] = WriteHandlers.BuildClashMatrix,
                ["clash/run"] = WriteHandlers.RunTests,
                ["clash/group"] = WriteHandlers.ApplyGroups,
                ["clash/status"] = WriteHandlers.SetStatus,
                ["clash/apply_rules"] = WriteHandlers.ApplyIgnoreRules,
                ["viewpoints/save"] = WriteHandlers.SaveViewpoints,
                ["appearance/color"] = WriteHandlers.ColorElements,
                ["appearance/reset"] = WriteHandlers.ResetAppearance,
                ["selection/set"] = WriteHandlers.SelectItems,
                // The four ribbon steps, as tools. Same service, same result.
                ["workflow/audit_models"] = WorkflowHandlers.AuditModels,
                ["workflow/configure"] = WorkflowHandlers.Configure,
                ["workflow/run"] = WorkflowHandlers.Run,
                ["workflow/group_levels"] = WorkflowHandlers.GroupLevels,
                ["workflow/rules"] = WorkflowHandlers.Rules,
                ["profile/info"] = WorkflowHandlers.ProfileInfo,
                ["profile/load"] = WorkflowHandlers.ProfileLoad,
                ["profile/reset"] = WorkflowHandlers.ProfileReset,
                ["document/save"] = SaveHandlers.Save,
                ["document/save_as"] = SaveHandlers.SaveAs,
                ["document/close"] = CloseHandlers.CloseDocument,
                ["application/exit"] = CloseHandlers.ExitApplication
            };

            _jobRoutes = new Dictionary<string, Func<Dictionary<string, object>, JobManager.Job, Dictionary<string, object>>>(
                StringComparer.OrdinalIgnoreCase)
            {
                ["workflow/audit_models"] = WorkflowHandlers.AuditModels,
                ["workflow/configure"] = WorkflowHandlers.Configure,
                ["workflow/run"] = WorkflowHandlers.Run,
                ["workflow/group_levels"] = WorkflowHandlers.GroupLevels,
                ["workflow/rules"] = WorkflowHandlers.Rules,
                ["document/save"] = SaveHandlers.Save,
                ["document/save_as"] = SaveHandlers.SaveAs,
                ["clash/run"] = (p, _) => WriteHandlers.RunTests(p),
                ["clash/group"] = (p, _) => WriteHandlers.ApplyGroups(p),
                ["clash/apply_rules"] = (p, _) => WriteHandlers.ApplyIgnoreRules(p),
                ["sets/build"] = (p, _) => WriteHandlers.BuildSearchSets(p),
                ["sets/build_search"] = (p, _) => WriteHandlers.BuildCriteriaSets(p),
                ["clash/matrix"] = (p, _) => WriteHandlers.BuildClashMatrix(p)
            };
        }

        /// <summary>
        /// Job routes that check <c>CancelRequested</c> between units and can
        /// therefore be stopped once started.
        /// </summary>
        /// <remarks>
        /// The list is short because only one mutating step is written as a
        /// loop over independent units; everything else is a single
        /// Navisworks call that either happens or does not. Keeping the set
        /// here — beside the job routes — is what lets <c>job/cancel</c>
        /// answer "cannot" instead of promising a stop nothing will deliver,
        /// and it must grow only when a handler genuinely starts checking the
        /// flag.
        /// </remarks>
        private static readonly HashSet<string> CancellableRoutes = new HashSet<string>(
            StringComparer.OrdinalIgnoreCase)
        {
            "workflow/audit_models",
            "workflow/group_levels"
        };

        public IEnumerable<string> Routes => _routes.Keys;

        public bool CanRunAsJob(string route) => _jobRoutes.ContainsKey(route ?? string.Empty);

        /// <summary>Whether a started job on this route can still be stopped.</summary>
        public static bool IsCancellable(string route)
            => CancellableRoutes.Contains(route ?? string.Empty);

        /// <summary>The cancellable routes, for capability negotiation.</summary>
        public static IEnumerable<string> Cancellable
            => CancellableRoutes.OrderBy(r => r, StringComparer.OrdinalIgnoreCase);

        public Func<Dictionary<string, object>, JobManager.Job, Dictionary<string, object>> JobHandler(string route)
            => _jobRoutes.TryGetValue(route ?? string.Empty, out var handler) ? handler : null;

        public Dictionary<string, object> Dispatch(string route, Dictionary<string, object> payload)
        {
            if (!_routes.TryGetValue(route ?? string.Empty, out var handler))
            {
                return new Dictionary<string, object>
                {
                    ["error"] = "unknown_route",
                    ["detail"] = $"Ruta '{route}' no reconocida.",
                    // Named so an older add-in produces a diagnosable answer:
                    // the caller compares this list against what it wanted and
                    // says which version to install, instead of "404".
                    ["addin_version"] = typeof(Router).Assembly.GetName().Version.ToString(),
                    ["api_version"] = Capabilities.ApiVersion,
                    ["available"] = _routes.Keys.OrderBy(k => k).ToList()
                };
            }
            return handler(payload ?? new Dictionary<string, object>());
        }

        /// <summary>Everything the caller needs to negotiate before calling.</summary>
        public Dictionary<string, object> DescribeCapabilities()
        {
            var doc = Autodesk.Navisworks.Api.Application.ActiveDocument;
            var open = doc != null && !doc.IsClear;
            var capabilities = Capabilities.Describe(
                typeof(Router).Assembly.GetName().Version.ToString(),
                SafeProductName(),
                _routes.Keys,
                open,
                DocumentContext.SaveCapability(doc),
                Cancellable);
            capabilities["job_routes"] = _jobRoutes.Keys
                .OrderBy(k => k, StringComparer.OrdinalIgnoreCase)
                .Cast<object>()
                .ToList();
            capabilities["document"] = DocumentContext.Describe(doc, includePath: true);
            return capabilities;
        }

        internal static string SafeProductName()
        {
            try { return Autodesk.Navisworks.Api.Application.Version.RuntimeProductName; }
            catch { return string.Empty; }
        }

        /// <summary>
        /// Signals the ordinary "nothing loaded yet" state.
        /// </summary>
        /// <remarks>
        /// Distinct from a generic failure because it is not one: a large
        /// federated model takes minutes to open, and a caller polling during
        /// that window deserves "still loading, retry" rather than a 500 that
        /// looks like the bridge broke.
        /// </remarks>
        internal sealed class NoDocumentException : Exception
        {
            public NoDocumentException(string message) : base(message) { }
        }

        internal static Document RequireDocument()
        {
            var doc = Autodesk.Navisworks.Api.Application.ActiveDocument;
            if (doc == null || doc.IsClear)
            {
                throw new NoDocumentException(
                    "Navisworks no tiene ningún documento cargado todavía. " +
                    "Si acabas de abrir un modelo federado grande, sigue cargando: reintenta en unos segundos.");
            }
            return doc;
        }

        // ------------------------------------------------------------ health

        /// <summary>
        /// Full health. Authenticated only — it names the document path, which
        /// leaks the project, the client and the machine's directory layout.
        /// The unauthenticated liveness answer lives in
        /// <see cref="HttpBridge"/> and carries none of that.
        /// </summary>
        private static Dictionary<string, object> Health(Dictionary<string, object> payload)
        {
            var doc = Autodesk.Navisworks.Api.Application.ActiveDocument;
            var result = new Dictionary<string, object>
            {
                ["ok"] = true,
                ["addin_version"] = typeof(Router).Assembly.GetName().Version.ToString(),
                ["api_version"] = Capabilities.ApiVersion,
                ["session_contract"] = SessionStore.ContractVersion,
                ["navisworks"] = SafeProductName(),
                ["document_open"] = doc != null && !doc.IsClear,
                ["document_fingerprint"] = DocumentContext.Fingerprint(doc),
                ["save"] = DocumentContext.SaveCapability(doc)
            };

            if (doc != null && !doc.IsClear)
            {
                result["document"] = new Dictionary<string, object>
                {
                    ["title"] = doc.Title ?? string.Empty,
                    ["path"] = doc.FileName ?? string.Empty,
                    ["models"] = (double)doc.Models.Count,
                    ["units"] = doc.Units.ToString(),
                    ["metre_scale"] = NavisContext.MetreScale(doc),
                    ["fingerprint"] = DocumentContext.Fingerprint(doc),
                    ["modified"] = DocumentContext.SafeIsModified(doc),
                    ["cloud_hosted"] = DocumentContext.IsCloudHosted(doc)
                };

                var clashCount = 0;
                var testCount = 0;
                try
                {
                    var tests = doc.GetClash().TestsData.Tests;
                    testCount = tests.Count;
                    foreach (var saved in tests)
                    {
                        if (saved is ClashTest test) clashCount += CountResults(test);
                    }
                }
                catch
                {
                    // Clash data is unavailable in Simulate; not an error here.
                }
                result["clash_tests"] = (double)testCount;
                result["clash_results"] = (double)clashCount;
            }

            return result;
        }

        // ------------------------------------------------------------ census

        /// <summary>
        /// Inventory of the federation: which files, how big, what categories.
        /// </summary>
        /// <remarks>
        /// This is what a discipline mapping is proposed from, so it reports
        /// the category histogram per source file rather than just names.
        /// Guessing the discipline of a model from its filename alone is how
        /// a coordination report ends up blaming the wrong team.
        /// </remarks>
        private static Dictionary<string, object> Census(Dictionary<string, object> payload)
        {
            var doc = RequireDocument();
            var scale = NavisContext.MetreScale(doc);
            var sampleLimit = Math.Max(0, Json.Int(payload, "category_sample", 20000));

            var models = new List<object>();
            for (var i = 0; i < doc.Models.Count; i++)
            {
                var model = doc.Models[i];
                var categories = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                var itemCount = 0;

                try
                {
                    foreach (var item in model.RootItem.DescendantsAndSelf)
                    {
                        itemCount++;
                        if (itemCount > sampleLimit) break;
                        var category = NavisContext.CategoryOf(item);
                        if (string.IsNullOrWhiteSpace(category)) continue;
                        categories.TryGetValue(category, out var count);
                        categories[category] = count + 1;
                    }
                }
                catch (Exception ex)
                {
                    categories["(lectura interrumpida)"] = 0;
                    models.Add(new Dictionary<string, object> { ["read_error"] = ex.Message });
                }

                var box = NavisContext.SafeBoundingBox(model.RootItem);
                models.Add(new Dictionary<string, object>
                {
                    ["index"] = (double)i,
                    ["source_file"] = System.IO.Path.GetFileName(
                        string.IsNullOrWhiteSpace(model.SourceFileName) ? model.FileName : model.SourceFileName),
                    ["source_path"] = model.SourceFileName ?? string.Empty,
                    ["creator"] = model.Creator ?? string.Empty,
                    ["item_count"] = (double)itemCount,
                    ["truncated"] = itemCount > sampleLimit,
                    ["bbox_min"] = box != null ? NavisContext.ToMetres(box.Min, scale) : new[] { 0.0, 0.0, 0.0 },
                    ["bbox_max"] = box != null ? NavisContext.ToMetres(box.Max, scale) : new[] { 0.0, 0.0, 0.0 },
                    ["top_categories"] = categories
                        .OrderByDescending(kv => kv.Value)
                        .Take(25)
                        .ToDictionary(kv => kv.Key, kv => (object)(double)kv.Value)
                });
            }

            return new Dictionary<string, object>
            {
                ["document"] = new Dictionary<string, object>
                {
                    ["title"] = doc.Title ?? string.Empty,
                    ["path"] = doc.FileName ?? string.Empty,
                    ["units"] = doc.Units.ToString()
                },
                ["models"] = models
            };
        }

        // ------------------------------------------------------ clash tests

        private static Dictionary<string, object> ListTests(Dictionary<string, object> payload)
        {
            var doc = RequireDocument();
            var tests = new List<object>();

            foreach (var saved in doc.GetClash().TestsData.Tests)
            {
                if (!(saved is ClashTest test)) continue;
                tests.Add(new Dictionary<string, object>
                {
                    ["name"] = test.DisplayName ?? string.Empty,
                    ["guid"] = test.Guid.ToString(),
                    ["type"] = test.TestType.ToString(),
                    ["status"] = test.Status.ToString(),
                    ["tolerance"] = test.Tolerance,
                    ["last_run"] = test.LastRun?.ToString("o", CultureInfo.InvariantCulture) ?? string.Empty,
                    ["result_count"] = (double)CountResults(test)
                });
            }

            return new Dictionary<string, object> { ["tests"] = tests };
        }

        // ---------------------------------------------------- clash export

        /// <summary>
        /// The full-fidelity export the analysis engine runs on.
        /// </summary>
        /// <remarks>
        /// This is the part every other Navisworks bridge leaves shallow. A
        /// clash result on its own — id, name, status — cannot be clustered,
        /// scored or traced back to Revit. What makes the downstream analysis
        /// possible is shipping, per clash: the contact point, the signed
        /// distance, both elements' bounding boxes, their parent path (so
        /// multi-layer hosts collapse), and the harvested join keys.
        ///
        /// Results are walked recursively because a test's children may be
        /// groups, and a grouped result must not be silently skipped.
        /// </remarks>
        private static Dictionary<string, object> ExportClashes(Dictionary<string, object> payload)
        {
            var doc = RequireDocument();
            var scale = NavisContext.MetreScale(doc);
            NavisContext.ResetCaches();

            var wanted = new List<string>(NavisContext.BaseProperties);
            wanted.AddRange(Json.StrArr(payload, "properties"));

            var testFilter = new HashSet<string>(Json.StrArr(payload, "tests"), StringComparer.OrdinalIgnoreCase);
            var statusFilter = new HashSet<string>(Json.StrArr(payload, "statuses"), StringComparer.OrdinalIgnoreCase);
            var limit = Json.Int(payload, "limit", 0);

            var clashes = new List<object>();
            var tests = new List<object>();
            var truncated = false;

            foreach (var saved in doc.GetClash().TestsData.Tests)
            {
                if (!(saved is ClashTest test)) continue;
                var testName = test.DisplayName ?? string.Empty;
                if (testFilter.Count > 0 && !testFilter.Contains(testName)) continue;

                var exported = 0;
                foreach (var result in EnumerateResults(test))
                {
                    if (statusFilter.Count > 0 && !statusFilter.Contains(result.Status.ToString())) continue;
                    if (limit > 0 && clashes.Count >= limit)
                    {
                        truncated = true;
                        break;
                    }

                    clashes.Add(DescribeClash(doc, test, result, scale, wanted));
                    exported++;
                }

                tests.Add(new Dictionary<string, object>
                {
                    ["name"] = testName,
                    ["type"] = test.TestType.ToString(),
                    ["tolerance_m"] = test.Tolerance * scale,
                    ["status"] = test.Status.ToString(),
                    ["exported"] = (double)exported,
                    // What the coordinator DECLARED each side to be. This is
                    // the only authoritative statement of intent in the whole
                    // export: a test named "STR-WAL VS ARCH-GB-WALL" is a
                    // person asserting that side A is structure and side B is
                    // architecture. Without it the engine falls back to Revit
                    // categories, where a structural wall and an
                    // architectural wall are both `Walls` — indistinguishable,
                    // and therefore silently collapsed into one discipline.
                    ["selection_a"] = SelectionNames(doc, test.SelectionA),
                    ["selection_b"] = SelectionNames(doc, test.SelectionB)
                });

                if (truncated) break;
            }

            return new Dictionary<string, object>
            {
                ["schema"] = NavisContext.Schema,
                ["document"] = new Dictionary<string, object>
                {
                    ["title"] = doc.Title ?? string.Empty,
                    ["path"] = doc.FileName ?? string.Empty,
                    ["units"] = "m"
                },
                ["models"] = DescribeModels(doc),
                ["tests"] = tests,
                ["clashes"] = clashes,
                ["truncated"] = truncated
            };
        }

        /// <summary>
        /// Names of the saved search/selection sets making up one side of a test.
        /// </summary>
        /// <remarks>
        /// A side is a <c>SelectionSourceCollection</c>, not a set: it can
        /// hold several sets, or a whole folder, or nothing at all when the
        /// test was built against an ad-hoc selection. Every one of those is
        /// legitimate, so an unresolvable side yields an empty list rather
        /// than an error — the engine then falls back to parsing the test
        /// name, and only then to categories.
        ///
        /// Wrapped in try/catch per source because <c>ResolveSelectionSource</c>
        /// throws on a source whose set was deleted after the test was built,
        /// and one stale side must not cost us the other.
        /// </remarks>
        private static List<string> SelectionNames(Document doc, ClashSelection selection)
        {
            var names = new List<string>();
            if (selection == null) return names;

            SelectionSourceCollection sources;
            try { sources = selection.Selection?.SelectionSources; }
            catch { return names; }
            if (sources == null) return names;

            foreach (var source in sources)
            {
                string name = null;
                try { name = doc.SelectionSets.ResolveSelectionSource(source)?.DisplayName; }
                catch { }
                if (!string.IsNullOrWhiteSpace(name) && !names.Contains(name)) names.Add(name);
            }
            return names;
        }

        private static List<object> DescribeModels(Document doc)
        {
            var models = new List<object>();
            for (var i = 0; i < doc.Models.Count; i++)
            {
                var model = doc.Models[i];
                models.Add(new Dictionary<string, object>
                {
                    ["index"] = (double)i,
                    ["source_file"] = System.IO.Path.GetFileName(
                        string.IsNullOrWhiteSpace(model.SourceFileName) ? model.FileName : model.SourceFileName),
                    ["source_path"] = model.SourceFileName ?? string.Empty
                });
            }
            return models;
        }

        private static Dictionary<string, object> DescribeClash(
            Document doc, ClashTest test, ClashResult result, double scale, List<string> wanted)
        {
            // Item1/Item2 are the clashing geometry; CompositeItem falls back
            // to the composite parent when the test merged composites, which
            // is common on Revit exports.
            var a = result.Item1 ?? result.CompositeItem1;
            var b = result.Item2 ?? result.CompositeItem2;

            return new Dictionary<string, object>
            {
                ["guid"] = result.Guid.ToString(),
                ["test"] = test.DisplayName ?? string.Empty,
                ["name"] = result.DisplayName ?? string.Empty,
                ["status"] = result.Status.ToString(),
                // From the owning test, not the result: ClashResult.TestType
                // only exists from API v22 on, and the add-in compiles
                // against 2024 (v21). The test's type is the same value.
                ["test_type"] = test.TestType.ToString(),
                // Navisworks reports interpenetration as a negative distance;
                // the engine relies on that sign, so it is passed through
                // scaled but otherwise untouched.
                ["distance_m"] = result.Distance * scale,
                ["point"] = NavisContext.ToMetres(result.Center, scale),
                ["level"] = LevelOf(a, b),
                ["grid"] = string.Empty,
                ["a"] = NavisContext.Describe(doc, a, scale, wanted),
                ["b"] = NavisContext.Describe(doc, b, scale, wanted)
            };
        }

        /// <summary>
        /// Best-effort level name.
        /// </summary>
        /// <remarks>
        /// ClashResult carries no level of its own — and no grid location
        /// either, despite what the printed Navisworks report shows — so the
        /// level is taken from whichever side actually published one.
        /// </remarks>
        private static string LevelOf(ModelItem a, ModelItem b)
        {
            foreach (var item in new[] { a, b })
            {
                if (item == null) continue;
                var props = NavisContext.Harvest(item, new[] { "Level", "Reference Level", "Nivel" });
                foreach (var key in new[] { "Level", "Reference Level", "Nivel" })
                {
                    if (props.TryGetValue(key, out var value))
                    {
                        var text = Convert.ToString(value, CultureInfo.InvariantCulture);
                        if (!string.IsNullOrWhiteSpace(text)) return text;
                    }
                }
            }
            return string.Empty;
        }

        internal static IEnumerable<ClashResult> EnumerateResults(SavedItem item)
        {
            switch (item)
            {
                case ClashResult result:
                    yield return result;
                    break;
                case ClashTest test:
                    foreach (var child in test.Children)
                    {
                        foreach (var nested in EnumerateResults(child)) yield return nested;
                    }
                    break;
                case ClashResultGroup group:
                    foreach (var child in group.Children)
                    {
                        foreach (var nested in EnumerateResults(child)) yield return nested;
                    }
                    break;
                case GroupItem generic:
                    foreach (var child in generic.Children)
                    {
                        foreach (var nested in EnumerateResults(child)) yield return nested;
                    }
                    break;
            }
        }

        internal static int CountResults(ClashTest test)
        {
            var count = 0;
            foreach (var _ in EnumerateResults(test)) count++;
            return count;
        }
    }
}
