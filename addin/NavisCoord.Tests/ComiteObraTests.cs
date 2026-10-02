using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// Regressions from the real «Comité de obra» run of 2026-10-01
    /// (PRUEBA.md, section F2): the add-in half of each defect.
    /// </summary>
    internal static class ComiteObraTests
    {
        public static void Run(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check)
        {
            PenetrationRulesDecideBeforeReading(section, eq, check);
            ExportRunsAsCancellableJob(section, eq, check);
            EmptySetsCarryTheirCause(section, eq, check);
            EmptyTestsDoNotPinOldSets(section, eq, check);
            VocabularyReadsScopeTokens(section, eq, check);
            PurityWorksOnCategoryNames(section, eq, check);
            OverlapIsNotCoordinates(section, eq, check);
            FingerprintFollowsContent(section, eq, check);
            MissingFolderIsNamed(section, eq, check);
            TextDoesNotClaimWhatWasNotWritten(section, eq, check);
            PropertyCacheReadsEachNodeOnce(section, eq, check);
            TopDownWalkMatchesTheUpwardRules(section, eq, check);
        }

        private static void TopDownWalkMatchesTheUpwardRules(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 1+: recorrido de arriba abajo con los ancestros a mano");

            var root = new FakeNode(null, "Source File", "MIR-EST.nwc");
            var level = new FakeNode(root, "Level", "02");
            var category = new FakeNode(level, "Category", "Generic Models");
            var instance = new FakeNode(category, "Type", "Pasamuro 4in", "Family", "Sleeve");
            var geometry = new FakeNode(instance, "Type", "");
            var sibling = new FakeNode(level, "Category", "Walls");
            var children = new Dictionary<FakeNode, List<FakeNode>>
            {
                [root] = new List<FakeNode> { level },
                [level] = new List<FakeNode> { category, sibling },
                [category] = new List<FakeNode> { instance },
                [instance] = new List<FakeNode> { geometry }
            };
            var reads = 0;
            Dictionary<string, string> Read(FakeNode node)
            {
                reads++;
                var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                foreach (var p in node.Props)
                    if (!map.ContainsKey(p.Key) && !string.IsNullOrWhiteSpace(p.Value)) map[p.Key] = p.Value;
                return map;
            }

            var frames = TreeWalk.PreOrder(root,
                n => children.TryGetValue(n, out var k) ? k : new List<FakeNode>(), Read).ToList();
            eq("root level category instance geometry sibling",
                string.Join(" ", frames.Select(f =>
                    f.Node == root ? "root" : f.Node == level ? "level" : f.Node == category ? "category" :
                    f.Node == instance ? "instance" : f.Node == geometry ? "geometry" : "sibling")),
                "pre-orden: el mismo orden que DescendantsAndSelf");
            eq(0, frames.Single(f => f.Node == geometry).ChildCount, "la hoja sabe que es hoja");
            eq(2, frames.Single(f => f.Node == level).ChildCount, "y el nivel, cuántos hijos tiene");
            eq(4, frames.Single(f => f.Node == geometry).Depth, "profundidad desde la raíz");

            var leaf = frames.Single(f => f.Node == geometry);
            eq("Generic Models", leaf.ValuesUp("Category", 5).First(), "la categoría llega desde dos niveles arriba");
            eq(0, leaf.ValuesUp("Category", 2).Count(), "con el mismo tope de profundidad");
            var identity = leaf.Harvest(n => n == "Type" || n == "Family", 8);
            eq("Pasamuro 4in", identity["Type"], "un Type vacío en la hoja no tapa el de la instancia");
            eq("Sleeve", identity["Family"], "la familia se hereda");
            eq(5, reads, "solo se leen los nodos por los que alguien preguntó, y cada uno una vez");
            leaf.ValuesUp("Category", 5).ToList();
            eq(5, reads, "volver a preguntar no relee");
        }

        private sealed class FakeNode
        {
            public FakeNode Parent;
            public List<KeyValuePair<string, string>> Props = new List<KeyValuePair<string, string>>();
            public bool Broken;
            public FakeNode(FakeNode parent, params string[] pairs)
            {
                Parent = parent;
                for (var i = 0; i + 1 < pairs.Length; i += 2)
                    Props.Add(new KeyValuePair<string, string>(pairs[i], pairs[i + 1]));
            }
        }

        private static void PropertyCacheReadsEachNodeOnce(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 1+: cada nodo se lee una vez por corrida, con las mismas reglas");

            var file = new FakeNode(null, "Source File", "MIR-EST.nwc");
            var category = new FakeNode(file, "Category", "Structural Framing", "Level", "02");
            var family = new FakeNode(category, "Family", "M_Concrete-Rectangular Beam", "Category", "");
            var instance = new FakeNode(family, "Type", "400x800", "Mark", "V-12", "MARK", "otra", "Ignorada", "x");
            var geometryA = new FakeNode(instance, "Type", "", "Material", "Concrete");
            var geometryB = new FakeNode(instance, "Material", "Steel");

            var reads = new Dictionary<FakeNode, int>();
            IEnumerable<KeyValuePair<string, string>> Read(FakeNode node, Func<string, bool> keep)
            {
                reads[node] = reads.TryGetValue(node, out var n) ? n + 1 : 1;
                if (node.Broken) throw new InvalidOperationException("pestaña ilegible");
                return node.Props.Where(p => keep(p.Key)).ToList();
            }
            var interest = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
                { "Category", "Level", "Type", "Family", "Mark", "Material" };
            var cache = new NodePropertyCache<FakeNode>(Read, n => n.Parent, interest.Contains);

            var harvestA = cache.Harvest(geometryA, interest.Contains, 8);
            eq("400x800", harvestA["Type"], "un valor vacío en la hoja no tapa el del ancestro");
            eq("V-12", harvestA["mark"], "nombres sin distinguir mayúsculas; dentro de un nodo gana el primero");
            eq("02", harvestA["Level"], "el nivel sale de un ancestro lejano");
            check(!harvestA.ContainsKey("Ignorada"), "lo que no interesa ni siquiera se guarda");
            eq("Structural Framing", cache.ValuesUp(geometryA, "Category", 5).First(),
                "la categoría vacía de la familia se salta y gana la publicada más arriba");
            eq(0, cache.ValuesUp(geometryA, "Category", 2).Count(),
                "el tope de profundidad se respeta igual que sin caché");

            cache.Harvest(geometryB, interest.Contains, 8);
            cache.ValuesUp(geometryB, "Category", 5).ToList();
            check(reads.Values.All(n => n == 1),
                "dos hojas hermanas y varias preguntas: cada nodo se leyó UNA vez");
            eq(6, cache.NodesRead, "seis nodos distintos en caché");

            var broken = new FakeNode(category, "Type", "x") { Broken = true };
            check(cache.Harvest(broken, interest.Contains, 8)["Level"].Equals("02"),
                "una pestaña ilegible no tumba la lectura del resto de la rama");

            var onlyType = cache.Harvest(geometryA, n => n == "Type", 8);
            eq(1, onlyType.Count, "el filtro de la llamada se aplica sobre la caché compartida");
        }

        private static void TextDoesNotClaimWhatWasNotWritten(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 3 y 6: el texto no afirma lo que el documento no tiene");

            var configure = WorkflowText.Configure(Json.ParseObject(@"{
              ""profile"": { ""errors"": [] },
              ""sets"": { ""present"": true, ""folders"": 3, ""sets"": 16, ""matches"": 0,
                          ""planned_matches"": 3940,
                          ""stale_sets"": [""ARQ/ARQ - Muros"", ""EST/EST - Vigas""],
                          ""blocked_folders"": [""ARQ"", ""EST""] },
              ""clash"": { ""present"": true, ""created"": 0, ""kept"": 3, ""tests_in_document"": 3 }
            }"));
            check(!configure.Contains("3,940 elementos capturados"),
                "los conteos planeados ya no se presentan como capturados");
            check(configure.Contains("NO se actualizaron") && configure.Contains("3,940"),
                "se dice qué sets no se actualizaron y cuánto capturarían");
            check(configure.Contains("ARQ, EST"), "y qué carpetas se conservaron");

            var audit = WorkflowText.AuditModels(Json.ParseObject(@"{
              ""models"": [
                { ""name"": ""MIR-EST-Mirador.nwc"", ""discipline"": ""EST"", ""elements"": 10,
                  ""status"": ""misma zona (coordenadas sin verificar)"" },
                { ""name"": ""MIR-MEP-Mirador.nwc"", ""discipline"": ""MEP"", ""elements"": 10,
                  ""status"": ""misma zona (coordenadas sin verificar)"" } ],
              ""misplaced"": [], ""reference_mismatch"": [], ""coordinates_verified"": false
            }"));
            check(audit.Contains("SIN verificar"), "sin puntos publicados se dice que no se verificó");

            var purity = WorkflowText.AuditModels(Json.ParseObject(@"{
              ""models"": [ { ""name"": ""MIR-EST-Mirador.nwc"", ""discipline"": ""EST"", ""elements"": 1,
                              ""status"": ""único modelo"" } ],
              ""misplaced"": [], ""coordinates_verified"": false,
              ""view_purity"": { ""evaluated"": true, ""dirty_views"": 0, ""findings"": [
                { ""discipline"": ""EST"", ""evaluated"": true, ""intruder_elements"": 0,
                  ""unclassified"": [
                    { ""category"": ""Structural Rebar"", ""elements"": 1153, ""category_id"": ""Structural Rebar"" },
                    { ""category"": ""Levels"", ""elements"": 5, ""category_id"": ""-2000240"" } ] } ] }
            }"));
            check(!purity.Contains("(id Structural Rebar)"), "sin CategoryId no se repite el nombre como «id»");
            check(purity.Contains("(id -2000240)"), "un id real sí se muestra");
            check(!audit.Contains("comparten volumen"), "ya no se afirma una ubicación común sin matices");
        }

        // -------------------------------------------------- defecto 7

        private static void FingerprintFollowsContent(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 7: la huella cambia con el contenido, no solo con la ruta");

            string Print(string creator, int items, double maxX) => DocumentFingerprint.Compute(new[]
            {
                @"C:\obra\Mirador-comite.nwf", "Mirador-comite.nwf", "1", "0", @"C:\obra\MIR-MEP.nwc",
                DocumentFingerprint.ModelContent(creator, items, new[] { 0.0, 0.0, 0.0 }, new[] { maxX, 40.0, 12.0 })
            });

            var original = Print("Autodesk Revit 2026", 412, 62.5);
            eq(original, Print("Autodesk Revit 2026", 412, 62.5), "mismo contenido, misma huella");
            check(original != Print("Autodesk Revit 2026", 413, 62.5),
                "otro documento en la misma ruta (otro número de elementos) cambia la huella");
            check(original != Print("Autodesk Revit 2026", 412, 72.5),
                "una geometría desplazada o distinta cambia la huella");
            check(original != Print("IFC", 412, 62.5), "otra herramienta de origen cambia la huella");
            eq(original, Print("Autodesk Revit 2026", 412, 62.5000001),
                "el ruido de coma flotante no la mueve");
            check(DocumentFingerprint.ModelContent(null, -1, null, null).Contains("min=-"),
                "un modelo sin geometría legible se describe sin inventar una caja");
        }

        // -------------------------------------------------- defecto 8

        private static void MissingFolderIsNamed(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 8: guardar en una carpeta que no existe dice eso");

            var missing = SaveFormats.MissingFolderProblem(
                @"C:\Users\<tu-usuario>\AppData\Local\NavisCoord\exports\ComiteObra\Mirador-comite.nwf", _ => false);
            check(missing != null && missing.Contains(@"exports\ComiteObra") && missing.Contains("no existe"),
                "el mensaje nombra la carpeta que falta");
            check(!missing.Contains("devolvió false"), "y no es el «Navisworks devolvió false» de antes");
            check(SaveFormats.MissingFolderProblem(@"C:\obra\a.nwf", _ => true) == null,
                "con la carpeta presente no hay problema");
            check(SaveFormats.MissingFolderProblem("", _ => false) == null, "sin ruta no se inventa un problema");
        }

        private static Dictionary<string, object> Cond(string property, string value, string tab = "Element",
            string test = "equals")
            => new Dictionary<string, object>
            {
                ["tab"] = tab, ["property"] = property, ["test"] = test, ["value"] = value
            };

        // -------------------------------------------------- defecto 2

        private static void EmptySetsCarryTheirCause(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 2: un set vacío se avisa con su causa");

            check(SetDiagnostics.Diagnose(new[] { Cond("Category", "Walls") }, 146, false) == null,
                "un set con elementos no lleva diagnóstico");

            var categoryId = SetDiagnostics.Diagnose(
                new[] { Cond("CategoryId", "OST_Walls", "Properties") }, 0, false);
            eq(SetDiagnostics.CategoryIdAbsent, Json.Str(categoryId, "code"),
                "CategoryId sobre un NWC local que no lo publica");
            check(Json.Str(categoryId, "hint").Contains("Element") && Json.Str(categoryId, "hint").Contains("Category"),
                "la pista propone Element > Category");

            var numeric = SetDiagnostics.Diagnose(
                new[] { Cond("CategoryId", "-2000011", "Properties") }, 0, null);
            eq(SetDiagnostics.CategoryIdAbsent, Json.Str(numeric, "code"),
                "el id numérico tampoco existe si nadie vio la propiedad");

            var and = SetDiagnostics.Diagnose(
                new[] { Cond("Category", "Doors"), Cond("Category", "Windows") }, 0, true);
            eq(SetDiagnostics.ConditionsAreAnd, Json.Str(and, "code"),
                "dos 'equals' sobre la misma propiedad: Navisworks los une con Y");

            var plain = SetDiagnostics.Diagnose(new[] { Cond("Category", "Roofs") }, 0, true);
            eq(SetDiagnostics.NoMatch, Json.Str(plain, "code"), "sin causa reconocible, se dice que no casó");
        }

        // -------------------------------------------------- defecto 3

        private static void EmptyTestsDoNotPinOldSets(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 3: tests vacíos que el perfil rehace no congelan los sets viejos");

            var empty = new ConfigurePlanning.TestSnapshot
            {
                Guid = "t1", Name = "EST VS MEP", ResultCount = 0,
                SourceGuidsA = new List<string> { "s-est" }, SourceGuidsB = new List<string> { "s-mep" }
            };
            var withResults = new ConfigurePlanning.TestSnapshot
            {
                Guid = "t2", Name = "EST VS MEP", ResultCount = 440,
                SourceGuidsA = new List<string> { "s-est" }, SourceGuidsB = new List<string> { "s-mep" }
            };
            var existing = new[]
            {
                new ConfigurePlanning.SetSnapshot { Guid = "s-est", Folder = "EST", Name = "EST - Vigas",
                    Definition = "EST/EST - Vigas#anterior" }
            };
            var desired = new[]
            {
                new ConfigurePlanning.DesiredSet { Folder = "EST", Name = "EST - Vigas", Definition = "EST/EST - Vigas" }
            };
            var rebuild = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "EST VS MEP" };

            var plan = ConfigurePlanning.Plan(existing, desired, new[] { empty },
                ConfigurePlanning.MigrationCapability.Unverified, rebuild);
            check(plan.Any(s => s.Target == "EST/EST - Vigas" && s.Action == ConfigurePlanning.Create),
                "el set cambiado se reconstruye en vez de quedar bloqueado");
            check(plan.Any(s => s.Target == "EST VS MEP" && s.Action == ConfigurePlanning.RebuildTest),
                "y el test vacío se declara para rehacer");
            var gate = ConfigurePlanning.MutationGate.ForFolders(true, plan, new[] { "EST" });
            var replaced = true;
            try { gate.ReplaceUnreferenced("EST"); } catch (InvalidOperationException) { replaced = false; }
            check(replaced, "la compuerta permite reemplazar la carpeta");

            var guarded = ConfigurePlanning.Plan(existing, desired, new[] { withResults },
                ConfigurePlanning.MigrationCapability.Unverified, rebuild);
            check(guarded.Any(s => s.Action == ConfigurePlanning.Blocked),
                "un test con resultados sigue protegiendo su set");

            var outside = ConfigurePlanning.Plan(existing, desired, new[] { empty },
                ConfigurePlanning.MigrationCapability.Unverified,
                new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "OTRO TEST" });
            check(outside.Any(s => s.Action == ConfigurePlanning.Blocked),
                "un test vacío que el perfil NO rehace también protege su set");

            var standalone = ConfigurePlanning.Plan(existing, desired, new[] { empty },
                ConfigurePlanning.MigrationCapability.Unverified);
            check(standalone.Any(s => s.Action == ConfigurePlanning.Blocked),
                "sin lista de tests a rehacer (navis_build_search_sets suelto) se conserva como antes");

            var names = ConfigurePlanning.MatrixTestNames(new Dictionary<string, object>
            {
                ["clash"] = new Dictionary<string, object>
                {
                    ["name_format"] = "{0} VS {1}",
                    ["pairs"] = new List<object>
                    {
                        new Dictionary<string, object> { ["a"] = "EST", ["b"] = "MEP" },
                        new Dictionary<string, object> { ["a"] = "ARQ", ["b"] = "EST", ["name"] = "ARQ VS EST 10mm" }
                    }
                }
            });
            eq("EST VS MEP|ARQ VS EST 10mm", string.Join("|", names),
                "los nombres de la matriz salen del formato o del nombre explícito");
        }

        // -------------------------------------------------- defecto 6

        private static void VocabularyReadsScopeTokens(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 6: la carpeta «Arquitectura» con alcance -ARQ- reconoce MIR-ARQ-Mirador");

            var vocabulary = DisciplineVocabulary.FromProfile(Json.ParseObject(@"{
              ""sets"": { ""folders"": [
                 { ""folder"": ""Arquitectura"", ""scope_model_contains"": ""-ARQ-"", ""sets"": [] },
                 { ""folder"": ""Estructura"", ""scope_model_contains"": ""-EST-"", ""sets"": [] },
                 { ""folder"": ""Redes"", ""scope_model_contains"": ""-MEP-|-HVA-"", ""sets"": [] } ] }
            }"));
            eq("ARQ", vocabulary.TokenIn("MIR-ARQ-Mirador.nwc"), "el token del alcance se reconoce en el nombre");
            eq("Arquitectura", vocabulary.Canonical("ARQ"), "…y se lleva a su carpeta");
            eq("Redes", vocabulary.Canonical(vocabulary.TokenIn("MIR-HVA-Mirador.nwc")),
                "cada variante del alcance apunta a la misma carpeta");
            eq(DisciplineVocabulary.Unknown, vocabulary.TokenIn("MIR-XYZ-Mirador.nwc"),
                "lo que el perfil no declara sigue sin disciplina");
        }

        private static void PurityWorksOnCategoryNames(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 6: pureza de vistas con Element > Category (sin CategoryId)");

            var whitelists = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase)
            {
                ["Arquitectura"] = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "Walls", "Floors", "Doors" },
                ["Estructura"] = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
                    { "Structural Columns", "Structural Framing", "Floors", "Walls" }
            };
            var tally = new Dictionary<string, int>
            {
                ["Walls"] = 146, ["Doors"] = 100, ["Structural Columns"] = 176, ["Structural Framing"] = 38
            };
            var finding = ViewPurity.Evaluate("ARQ", tally, whitelists, new Dictionary<string, string>(),
                code => code == "ARQ" ? "Arquitectura" : code);
            check(finding.Evaluated, "la vista de arquitectura se evalúa por nombre de categoría");
            eq(214, finding.IntruderElements, "176 columnas + 38 vigas estructurales son intrusas");
            check(finding.Dirty, "…y la vista se declara sucia");

            var byId = ViewPurity.Evaluate("Arquitectura",
                new Dictionary<string, int> { ["-2001330"] = 12 },
                whitelists,
                new Dictionary<string, string> { ["-2001330"] = "Structural Columns" });
            eq(12, byId.IntruderElements, "con CategoryId se compara también su nombre visible");
        }

        private static void OverlapIsNotCoordinates(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 6: cajas solapadas no son coordenadas compartidas");

            var none = ColocationAudit.Compare(new List<Dictionary<string, string>>
            {
                new Dictionary<string, string>(), new Dictionary<string, string>()
            });
            eq(ColocationAudit.SameArea, ColocationAudit.ForOverlap(0, none, out var verified),
                "sin puntos publicados: misma zona, coordenadas sin verificar");
            check(!verified, "…y no se declara verificado");

            var survey = "Revit/Survey Point E/W";
            var mismatch = ColocationAudit.Compare(new List<Dictionary<string, string>>
            {
                new Dictionary<string, string> { [survey] = "-9.202" },
                new Dictionary<string, string> { [survey] = "-9.202" },
                new Dictionary<string, string> { [survey] = "-12.282" }
            });
            check(mismatch.Comparable, "un punto publicado por varios modelos se compara");
            eq(ColocationAudit.ReferenceMismatch, ColocationAudit.ForOverlap(2, mismatch, out _),
                "el modelo con punto distinto se marca aunque su caja se solape");
            eq(ColocationAudit.Colocated, ColocationAudit.ForOverlap(0, mismatch, out var ok),
                "los que coinciden con la mayoría sí quedan verificados");
            check(ok, "…con coordinates_verified");

            var tie = ColocationAudit.Compare(new List<Dictionary<string, string>>
            {
                new Dictionary<string, string> { [survey] = "1" },
                new Dictionary<string, string> { [survey] = "2" }
            });
            eq(2, tie.Mismatched.Count, "dos modelos y dos valores: ninguno es «el bueno»");
            check(ColocationAudit.IsReferenceProperty("Punto de reconocimiento"), "reconoce el nombre en español");
            check(!ColocationAudit.IsReferenceProperty("Category"), "y no cualquier propiedad");
        }

        // -------------------------------------------------- defecto 1

        private static void PenetrationRulesDecideBeforeReading(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 1: sin reglas de pasos no se recorre el modelo");

            var none = PenetrationRules.FromPayload(new Dictionary<string, object>
            {
                ["penetration_categories"] = new List<object>(),
                ["penetration_keywords"] = new List<object>()
            });
            check(none.Configured, "las listas llegaron, aunque vacías");
            check(!none.HasRules, "listas vacías: ninguna regla puede casar");
            check(none.SkipReason() != null,
                "sin reglas el inventario se omite en vez de leer cada nodo para descartarlo");
            check(none.SkipReason().Contains("pass_through"),
                "el motivo nombra la sección del perfil que falta");

            var missing = PenetrationRules.FromPayload(new Dictionary<string, object>());
            check(!missing.Configured && missing.SkipReason() != null,
                "sin listas en la petición también se omite, con el motivo de siempre");

            section("comité 1: la categoría se prueba antes de leer propiedades");

            var byCategory = PenetrationRules.FromPayload(new Dictionary<string, object>
            {
                ["penetration_categories"] = new List<object> { "Generic Models", " " },
                ["penetration_keywords"] = new List<object>()
            });
            check(byCategory.HasRules && byCategory.SkipReason() == null, "con una categoría sí se recorre");
            check(!byCategory.NeedsIdentity,
                "sin palabras clave no hace falta cosechar Type/Family de cada nodo");
            check(byCategory.MatchesCategory("generic models"), "la categoría casa sin mayúsculas");
            check(!byCategory.MatchesCategory(""), "una categoría vacía no casa");
            eq(1, byCategory.Categories.Count, "las entradas en blanco no cuentan como regla");

            var byKeyword = PenetrationRules.FromPayload(new Dictionary<string, object>
            {
                ["penetration_categories"] = new List<object>(),
                ["penetration_keywords"] = new List<object> { "pasamuro", "Sleeve", "sleeve" }
            });
            check(byKeyword.NeedsIdentity, "una palabra clave sí exige leer la identidad");
            check(byKeyword.MatchesText("Tubo PASAMURO 4in"), "la palabra clave casa sin mayúsculas");
            check(!byKeyword.MatchesText("Basic Wall"), "y no casa donde no aparece");
            eq(2, byKeyword.Keywords.Count, "las palabras repetidas se cuentan una vez");
        }

        private static void ExportRunsAsCancellableJob(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("comité 1: clash/export se puede enviar como trabajo y cancelar");

            var contract = RouteContracts.For("clash/export");
            check(contract != null, "clash/export tiene contrato");
            check(!contract.IsMutation, "sigue siendo una lectura");
            check(contract.SupportsJob, "admite job/submit: un minuto en el hilo de UI es el límite del cliente");
            check(contract.Cancellable, "se detiene entre cruces y entre elementos del inventario");
            check(RouteContracts.JobRoutes.Contains("clash/export"), "aparece entre las rutas de trabajo");
            eq(0, EnvelopeContract.Problems("clash/export",
                    new Dictionary<string, object> { ["status"] = "completed" }).Count,
                "una lectura no debe envelope de mutación");
        }
    }
}
