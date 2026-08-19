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
                return Ok("build_sets", new Dictionary<string, object>
                {
                    ["dry_run"] = true,
                    ["scanned_items"] = (double)scanned,
                    ["routing"] = routing,
                    ["would_create"] = plan
                });
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

            return Ok("build_sets", new Dictionary<string, object>
            {
                ["dry_run"] = false,
                ["scanned_items"] = (double)scanned,
                ["routing"] = routing,
                ["planned"] = plan,
                ["verified_in_document"] = observed
            });
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

            if (dryRun) return Ok("build_search_sets", result);

            foreach (var group in pending.GroupBy(p => p.Item1))
            {
                var existing = doc.SelectionSets.RootItem.Children
                    .FirstOrDefault(s => string.Equals(s.DisplayName, group.Key, StringComparison.OrdinalIgnoreCase));
                if (existing != null)
                {
                    // Sin replace la carpeta existente se respeta: los clash
                    // tests la referencian por SelectionSource y recrearla
                    // rompería ese enlace y borraría resultados corridos.
                    if (!replace) continue;
                    doc.SelectionSets.Remove(existing);
                }
                var folder = new FolderItem { DisplayName = group.Key };
                foreach (var entry in group)
                {
                    folder.Children.Add(new SelectionSet(entry.Item3) { DisplayName = entry.Item2 });
                }
                doc.SelectionSets.AddCopy(folder);
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
            return Ok("build_search_sets", result);
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

            if (!dryRun)
            {
                // Verification: re-read the document and count the results
                // whose status the triage actually changed. `edited` counts
                // calls that did not throw, which proves nothing — this is
                // what makes the difference visible.
                var verify = new Dictionary<string, object>();
                var verifiedTotal = 0;
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

            return Ok("apply_ignore_rules", response);
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
                return Ok("build_matrix", new Dictionary<string, object>
                {
                    ["dry_run"] = true,
                    ["would_create"] = plan
                });
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

            var verified = clash.TestsData.Tests
                .Select(t => t.DisplayName ?? string.Empty)
                .Where(n => n.StartsWith(prefix, StringComparison.Ordinal))
                .ToList();

            return Ok("build_matrix", new Dictionary<string, object>
            {
                ["dry_run"] = false,
                ["planned"] = plan,
                ["created"] = (double)created,
                ["verified_in_document"] = verified
            });
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

            return Ok("run_tests", new Dictionary<string, object> { ["tests"] = ran });
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
            var doc = Router.RequireDocument();
            var dryRun = Json.Bool(payload, "dry_run", true);
            var groups = Json.Arr(payload, "groups");
            if (groups.Count == 0) throw new ArgumentException("Se requiere 'groups'.");

            var clash = doc.GetClash();
            var index = BuildResultIndex(clash);

            var plan = new List<object>();
            foreach (var raw in groups)
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                var guids = Json.StrArr(spec, "clash_guids");
                var found = guids.Count(g => index.ContainsKey(g));

                // A Navisworks clash group lives under exactly one test, so an
                // issue whose clashes span several becomes several groups. The
                // dry run has to say so: a plan that under-reports what the
                // commit will do is worse than no plan at all.
                var spanned = OwningTests(guids, index);
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
                return Ok("apply_groups", new Dictionary<string, object>
                {
                    ["dry_run"] = true,
                    ["would_create"] = plan
                });
            }

            var created = new List<object>();
            foreach (var raw in groups)
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                var name = Json.Str(spec, "name");
                var guids = Json.StrArr(spec, "clash_guids");
                if (string.IsNullOrWhiteSpace(name) || guids.Count == 0) continue;

                // Every result in a group must live under the same test, so
                // the group is created inside whichever test owns them.
                // Names, not objects: the handles are re-resolved below and
                // anything captured here would be dead after the first move.
                var owners = OwningTests(guids, index);
                if (owners.Count == 0) continue;

                foreach (var testName in owners)
                {
                    // One group per owning test. Suffixed only when the issue
                    // actually spans several, so the common case stays clean.
                    var groupName = owners.Count > 1 ? $"{name} · {testName}" : name;
                    var mine = guids
                        .Where(g => index.ContainsKey(g) &&
                                    string.Equals(index[g].Test.DisplayName, testName,
                                        StringComparison.OrdinalIgnoreCase))
                        .ToList();

                    var host = FindTest(doc, testName);
                    if (host == null) continue;
                    // Reusar el grupo si ya existe (re-agrupar tras cada corrida
                    // duplicaba "Nivel 05" en vez de llenar el existente).
                    var existente = host.Children
                        .OfType<ClashResultGroup>()
                        .Any(g => string.Equals(g.DisplayName, groupName, StringComparison.Ordinal));
                    if (!existente)
                    {
                        clash.TestsData.TestsAddCopy(host, new ClashResultGroup { DisplayName = groupName });
                    }

                    // Every TestsMove rebuilds the children tree and disposes
                    // the handles held from before it, so both the test and
                    // the group are re-resolved on each pass. Indices shift
                    // too, which is why the position is found by GUID.
                    var moved = 0;
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
                        if (position == null) continue;

                        doc.GetClash().TestsData.TestsMove(currentTest, position.i, target, target.Children.Count);
                        moved++;
                    }

                    var verified = FindTest(doc, testName)?.Children
                        .OfType<ClashResultGroup>()
                        .LastOrDefault(g => string.Equals(g.DisplayName, groupName, StringComparison.Ordinal));

                    created.Add(new Dictionary<string, object>
                    {
                        ["name"] = groupName,
                        ["issue"] = name,
                        ["test"] = testName,
                        ["requested"] = (double)mine.Count,
                        ["moved"] = (double)moved,
                        ["verified_children"] = (double)(verified?.Children.Count ?? 0)
                    });
                }
            }

            return Ok("apply_groups", new Dictionary<string, object>
            {
                ["dry_run"] = false,
                ["groups"] = created
            });
        }

        public static Dictionary<string, object> SetStatus(Dictionary<string, object> payload)
        {
            var doc = Router.RequireDocument();
            var dryRun = Json.Bool(payload, "dry_run", true);
            var statusName = Json.Str(payload, "status", "Reviewed");
            if (!Enum.TryParse<ClashResultStatus>(statusName, true, out var status))
            {
                throw new ArgumentException(
                    $"Estado '{statusName}' no válido. Usa: New, Active, Reviewed, Approved, Resolved.");
            }

            var guids = Json.StrArr(payload, "clash_guids");
            var clash = doc.GetClash();
            var index = BuildResultIndex(clash);
            var targets = guids.Where(index.ContainsKey).ToList();

            if (dryRun)
            {
                return Ok("set_status", new Dictionary<string, object>
                {
                    ["dry_run"] = true,
                    ["status"] = status.ToString(),
                    ["would_change"] = (double)targets.Count,
                    ["not_found"] = (double)(guids.Count - targets.Count)
                });
            }

            var changed = 0;
            foreach (var guid in targets)
            {
                var entry = index[guid];
                EditResultStatus(clash.TestsData, entry.Result, status);
                changed++;
            }

            // Verify by re-reading rather than by counting successful calls.
            var reindexed = BuildResultIndex(doc.GetClash());
            var confirmed = targets.Count(g =>
                reindexed.ContainsKey(g) && reindexed[g].Result.Status == status);

            return Ok("set_status", new Dictionary<string, object>
            {
                ["dry_run"] = false,
                ["status"] = status.ToString(),
                ["attempted"] = (double)changed,
                ["verified_in_document"] = (double)confirmed,
                ["mismatch"] = (double)(changed - confirmed)
            });
        }

        public static Dictionary<string, object> SaveViewpoints(Dictionary<string, object> payload)
        {
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
                return Ok("save_viewpoints", new Dictionary<string, object>
                {
                    ["dry_run"] = true,
                    ["would_save"] = (double)resolvable,
                    ["not_found"] = (double)(items.Count - resolvable)
                });
            }

            var before = doc.SavedViewpoints.RootItem.Children.Count;
            var saved = 0;
            foreach (var raw in items)
            {
                if (!(raw is Dictionary<string, object> spec)) continue;
                var guid = Json.Str(spec, "clash_guid");
                if (!index.TryGetValue(guid, out var entry)) continue;

                var viewpoint = clash.TestsData.TestsViewpointForResult(entry.Result);
                if (viewpoint == null) continue;

                var name = Json.Str(spec, "name", $"{folder} - {entry.Result.DisplayName}");
                doc.SavedViewpoints.AddCopy(new SavedViewpoint(viewpoint) { DisplayName = name });
                saved++;
            }

            var after = doc.SavedViewpoints.RootItem.Children.Count;
            return Ok("save_viewpoints", new Dictionary<string, object>
            {
                ["dry_run"] = false,
                ["attempted"] = (double)saved,
                ["viewpoints_before"] = (double)before,
                ["viewpoints_after"] = (double)after,
                ["verified_added"] = (double)(after - before)
            });
        }

        // ------------------------------------------------------ appearance

        public static Dictionary<string, object> ColorElements(Dictionary<string, object> payload)
        {
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

            doc.Models.OverridePermanentColor(items, color);

            var transparency = Json.Num(payload, "transparency", -1.0);
            if (transparency >= 0.0 && transparency <= 1.0)
            {
                doc.Models.OverridePermanentTransparency(items, transparency);
            }

            // Reported against the DISTINCT count. The same element appears in
            // many clashes, so measuring against the raw request makes routine
            // de-duplication look like a resolution failure.
            return Ok("color", new Dictionary<string, object>
            {
                ["requested"] = (double)pathIds.Count,
                ["unique_requested"] = (double)unique,
                ["resolved"] = (double)items.Count,
                ["unresolved"] = (double)Math.Max(0, unique - items.Count)
            });
        }

        public static Dictionary<string, object> ResetAppearance(Dictionary<string, object> payload)
        {
            var doc = Router.RequireDocument();
            var pathIds = Json.StrArr(payload, "path_ids");

            if (pathIds.Count == 0)
            {
                doc.Models.ResetAllPermanentMaterials();
                return Ok("reset_appearance", new Dictionary<string, object> { ["scope"] = "todo el modelo" });
            }

            var items = NavisContext.ResolveMany(doc, pathIds);
            doc.Models.ResetPermanentMaterials(items);
            return Ok("reset_appearance", new Dictionary<string, object>
            {
                ["scope"] = "selección",
                ["resolved"] = (double)items.Count
            });
        }

        public static Dictionary<string, object> SelectItems(Dictionary<string, object> payload)
        {
            var doc = Router.RequireDocument();
            var pathIds = Json.StrArr(payload, "path_ids");
            var unique = pathIds.Distinct(StringComparer.Ordinal).Count();
            var items = NavisContext.ResolveMany(doc, pathIds);

            doc.CurrentSelection.Clear();
            doc.CurrentSelection.CopyFrom(items);

            var selected = doc.CurrentSelection.SelectedItems.Count;
            return Ok("select", new Dictionary<string, object>
            {
                ["requested"] = (double)pathIds.Count,
                ["unique_requested"] = (double)unique,
                ["selected"] = (double)selected,
                ["unresolved"] = (double)Math.Max(0, unique - selected)
            });
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

        /// <summary>Distinct test names owning the given clash results.</summary>
        private static List<string> OwningTests(
            IEnumerable<string> guids, Dictionary<string, ResultEntry> index)
            => guids
                .Where(index.ContainsKey)
                .Select(g => index[g].Test.DisplayName ?? string.Empty)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(n => n, StringComparer.OrdinalIgnoreCase)
                .ToList();

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
