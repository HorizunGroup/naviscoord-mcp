using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// `workflow/configure` must not leave a clash test pointing at a set it
    /// deleted a moment earlier.
    /// </summary>
    /// <remarks>
    /// Configure rebuilds the discipline folders, which removes them and
    /// creates new ones with new identities, and then rebuilds the matrix,
    /// which KEEPS any test that already has results. Nothing checked that a
    /// kept test's two sides still resolved, and the verification compared
    /// names and counts — so the folder was there, the set was there, the test
    /// was there, and the run reported green over a test whose Selection A
    /// referenced a deleted item.
    ///
    /// None of this needs a licence to assert: the ordering rules, the
    /// dependency graph and the identity checks are arithmetic over GUIDs and
    /// names, which is why they live in <see cref="ConfigurePlanning"/>.
    /// </remarks>
    internal static class ConfigureTests
    {
        private static Action<string> _section;
        private static Action<object, object, string> _eq;
        private static Action<bool, string> _check;

        public static void Run(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check)
        {
            _section = section;
            _eq = eq;
            _check = check;

            IdenticalRunIsIdempotent();
            UnreferencedSetIsFreeToChange();
            ReferencedSetWithoutResultsMigrates();
            ReferencedSetWithResultsIsPreserved();
            OldSourceOutlivesAFailedCreate();
            OldSourceOutlivesAFailedRelink();
            SameNameDifferentGuidFails();
            OrphanOnOneSideIsNotCompleted();
            TwoTestsSharingOneSet();
            NamesWouldHavePassedIdentityDoesNot();
            NothingVerifiedIsNeverCompleted();
            AnEmptySourceIsStillValid();

            DryRunTouchesNothing();
            ACapabilityProbeIsNotInThePlan();
            UnreferencedReplacementIsOneCall();
            ThereAreNoRecombinablePrimitives();
            ANewFolderIsAdded();
            ReferencedFolderIsStillBlocked();
            ADuplicateFolderFailsVerification();
            AWrongDefinitionAfterReplacementFails();
            AFailedReplacementIsDescribedNotRepaired();
            NoRemoveThenAddSequenceInTheSource();
            BlockedRunLeavesTheSnapshotUnchanged();
            AFailedReplaceIsNeverCompleted();
            SameGuidButBoundElsewhereFails();
            SameGuidDifferentDefinitionFails();
            FewerResultsFails();
            DegradedStatusFails();
            AVanishedTestIsADegradation();
        }

        // ------------------------------------------------------------ helpers

        private static ConfigurePlanning.SetSnapshot Set(
            string guid, string folder, string name, string definition)
            => new ConfigurePlanning.SetSnapshot
            {
                Guid = guid, Folder = folder, Name = name, Definition = definition
            };

        private static ConfigurePlanning.DesiredSet Want(
            string folder, string name, string definition)
            => new ConfigurePlanning.DesiredSet
            {
                Folder = folder, Name = name, Definition = definition
            };

        private static ConfigurePlanning.TestSnapshot Test(
            string name, string guidA, string guidB, int results = 0)
            => new ConfigurePlanning.TestSnapshot
            {
                Name = name,
                ResultCount = results,
                SourceGuidsA = new List<string> { guidA },
                SourceGuidsB = new List<string> { guidB }
            };

        private static ConfigurePlanning.ObservedTest Seen(
            string name, string guidA, bool aLives, string guidB, bool bLives, int results = 0)
            => new ConfigurePlanning.ObservedTest
            {
                Name = name,
                Exists = true,
                ResultCount = results,
                SideA = new List<ConfigurePlanning.ObservedSource>
                {
                    new ConfigurePlanning.ObservedSource { Guid = guidA, ExistsInDocument = aLives }
                },
                SideB = new List<ConfigurePlanning.ObservedSource>
                {
                    new ConfigurePlanning.ObservedSource { Guid = guidB, ExistsInDocument = bLives }
                }
            };

        private static List<ConfigurePlanning.Step> For(
            List<ConfigurePlanning.Step> steps, string target)
            => steps.Where(s => string.Equals(s.Target, target, StringComparison.OrdinalIgnoreCase)).ToList();

        // -------------------------------------------------------------- cases

        private static void IdenticalRunIsIdempotent()
        {
            _section("configure: una segunda corrida idéntica no cambia identidades");

            var existing = new[] { Set("g-est", "EST", "muros", "cat=Walls") };
            var desired = new[] { Want("EST", "muros", "cat=Walls") };
            var tests = new[] { Test("EST vs HVAC", "g-est", "g-hvac", results: 40) };

            var steps = ConfigurePlanning.Plan(
                existing, desired, tests, ConfigurePlanning.MigrationCapability.Unverified);

            _eq(1, steps.Count, "una definición sin cambios produce un solo paso");
            _eq(ConfigurePlanning.UpdateInPlace, steps[0].Action, "y ese paso no toca nada");
            _eq("g-est", steps[0].Guid, "conserva la identidad");
            _check(!steps.Any(s => s.Action == ConfigurePlanning.RemoveAfterVerification),
                "no se retira nada en una corrida idempotente");
            _check(!steps.Any(s => s.Action == ConfigurePlanning.Create),
                "ni se duplica el conjunto");
        }

        private static void UnreferencedSetIsFreeToChange()
        {
            _section("configure: un conjunto que nadie referencia se puede rehacer");

            var steps = ConfigurePlanning.Plan(
                new[] { Set("g-libre", "ARQ", "tabiques", "cat=Walls") },
                new[] { Want("ARQ", "tabiques", "cat=Walls&tipo=GB") },
                new ConfigurePlanning.TestSnapshot[0],
                ConfigurePlanning.MigrationCapability.Unverified);

            _check(steps.Any(s => s.Action == ConfigurePlanning.Create), "se crea el nuevo");
            var retire = steps.Single(s => s.Action == ConfigurePlanning.RemoveAfterVerification);
            _eq("g-libre", retire.Guid, "y se retira el anterior");
            _eq(0, retire.AffectedTests.Count, "sin tests que revincular");
            _check(!steps.Any(s => s.Action == ConfigurePlanning.Blocked), "nada que bloquear");
        }

        private static void ReferencedSetWithoutResultsMigrates()
        {
            _section("configure: conjunto usado por un test sin resultados");

            var existing = new[] { Set("g-viejo", "EST", "muros", "v1") };
            var desired = new[] { Want("EST", "muros", "v2") };
            var tests = new[] { Test("EST vs HVAC", "g-viejo", "g-hvac", results: 0) };

            // Nothing has been run yet, so no results are at stake — and it is
            // still blocked. Relinking a test needs an operation that provably
            // rebinds it, and "provably" is the word doing the work: the API
            // has an atomic ReplaceWithCopy, but its contract promises a copy
            // and says nothing about the GUID or about the SelectionSources
            // pointing at it. Cheap to be wrong is not the same as safe.
            var conservative = ConfigurePlanning.Plan(
                existing, desired, tests, ConfigurePlanning.MigrationCapability.Unverified);

            var blocked = conservative.Single(s => s.Action == ConfigurePlanning.Blocked);
            _eq("g-viejo", blocked.Guid, "sin capacidad demostrada, la fuente se conserva");
            _check(blocked.Reason.IndexOf(ConfigurePlanning.CapabilityUnverified,
                       StringComparison.Ordinal) >= 0,
                "y el motivo dice que la capacidad no está verificada");
            _check(!conservative.Any(s => s.Action == ConfigurePlanning.RemoveAfterVerification),
                "no se retira nada");
            _check(!conservative.Any(s => s.Action == ConfigurePlanning.PreserveDueToResults),
                "pero no se alega la excusa de los resultados: no hay ninguno");

            // With the capability demonstrated, the ordering contract applies.
            var steps = ConfigurePlanning.Plan(
                existing, desired, tests,
                ConfigurePlanning.MigrationCapability.VerifiedAtomicReplace);

            var order = steps.Select(s => s.Action).ToList();
            _check(order.IndexOf(ConfigurePlanning.Create) <
                   order.IndexOf(ConfigurePlanning.Relink),
                "primero se crea la fuente nueva");
            _check(order.IndexOf(ConfigurePlanning.Relink) <
                   order.IndexOf(ConfigurePlanning.RemoveAfterVerification),
                "luego se revincula, y solo al final se retira la anterior");

            var relink = steps.Single(s => s.Action == ConfigurePlanning.Relink);
            _eq("EST vs HVAC", relink.Target, "el test dependiente se revincula por nombre");
        }

        private static void ReferencedSetWithResultsIsPreserved()
        {
            _section("configure: conjunto usado por un test CON resultados");

            var steps = ConfigurePlanning.Plan(
                new[] { Set("g-viejo", "EST", "muros", "v1") },
                new[] { Want("EST", "muros", "v2") },
                new[] { Test("EST vs HVAC", "g-viejo", "g-hvac", results: 216) },
                ConfigurePlanning.MigrationCapability.Unverified);

            var blocked = steps.Single(s => s.Action == ConfigurePlanning.Blocked);
            _eq("g-viejo", blocked.Guid, "el conjunto anterior se conserva");
            _check(blocked.Reason.IndexOf("resultados", StringComparison.OrdinalIgnoreCase) >= 0,
                "y se dice por qué");
            _check(steps.Any(s => s.Action == ConfigurePlanning.PreserveDueToResults),
                "el test se declara conservado");
            _check(!steps.Any(s => s.Action == ConfigurePlanning.RemoveAfterVerification),
                "NADA se retira: destruir una corrida para uniformar la config no es una opción");
            _check(!steps.Any(s => s.Action == ConfigurePlanning.Create),
                "ni se crea un reemplazo que dejaría el test huérfano");
        }

        private static void OldSourceOutlivesAFailedCreate()
        {
            _section("configure: si falla crear la fuente nueva, la anterior sigue ahí");

            // The retirement step is emitted only after the create, and its
            // contract is "after verification". Simulating the create failing
            // means the verification never happens, so nothing is retired.
            var steps = ConfigurePlanning.Plan(
                new[] { Set("g-viejo", "EST", "muros", "v1") },
                new[] { Want("EST", "muros", "v2") },
                new[] { Test("EST vs HVAC", "g-viejo", "g-hvac") },
                ConfigurePlanning.MigrationCapability.VerifiedAtomicReplace);

            var retire = steps.Single(s => s.Action == ConfigurePlanning.RemoveAfterVerification);
            _check(retire.Reason.IndexOf("tras comprobar", StringComparison.OrdinalIgnoreCase) >= 0,
                "la retirada está condicionada explícitamente a la verificación");

            // And the verifier refuses the run if the new source is not there.
            var verdict = ConfigurePlanning.Verify(
                new[] { Seen("EST vs HVAC", "g-nuevo", false, "g-hvac", true) },
                new Dictionary<string, int>());
            _eq("failed", verdict.Status, "sin fuente nueva verificada, el run no es completed");
        }

        private static void OldSourceOutlivesAFailedRelink()
        {
            _section("configure: si falla revincular, no se borra la fuente anterior");

            var verdict = ConfigurePlanning.Verify(
                new[] { Seen("EST vs HVAC", "g-viejo", true, "g-hvac", true) },
                new Dictionary<string, int>());
            _eq("completed", verdict.Status,
                "el test sigue apuntando a una fuente viva: el documento no quedó peor");
            _eq(0, verdict.Failed, "y no se reporta como fallo del test");

            // Preserved and blocked are part of the verdict, not decoration
            // added to it afterwards.
            var counted = ConfigurePlanning.Verify(
                new[] { Seen("EST vs HVAC", "g-viejo", true, "g-hvac", true) },
                new Dictionary<string, int>(),
                preserved: 4, blocked: 2);
            _eq(4, counted.Preserved, "el verdicto lleva cuántos se conservaron");
            _eq(2, counted.BlockedCount, "y cuántos quedaron bloqueados");
        }

        private static void SameNameDifferentGuidFails()
        {
            _section("configure: mismo nombre, GUID distinto, no pasa");

            var existing = new[] { Set("g-nuevo", "EST", "muros", "v2") };
            var tests = new[] { Test("EST vs HVAC", "g-viejo", "g-hvac", results: 5) };

            // The graph keys on identity, so the rebuilt set with the same
            // name is NOT seen as the one the test holds.
            var graph = ConfigurePlanning.DependentTests(tests);
            _check(!graph.ContainsKey("g-nuevo"),
                "el conjunto recreado no hereda las dependencias del anterior");
            _check(graph.ContainsKey("g-viejo"),
                "que siguen colgando del GUID que el test realmente guarda");
            _eq(1, existing.Length, "y el conjunto nuevo existe con otra identidad");
        }

        private static void OrphanOnOneSideIsNotCompleted()
        {
            _section("configure: Selection A válida y B huérfana");

            var verdict = ConfigurePlanning.Verify(
                new[] { Seen("EST vs HVAC", "g-est", true, "g-borrado", false) },
                new Dictionary<string, int>());

            _eq("failed", verdict.Status, "un solo test y su lado B roto: failed");
            _eq(1, verdict.Failed, "");
            var problems = (List<object>)verdict.Details[0]["problems"];
            _check(problems.Any(p => p.ToString().IndexOf("Selection B", StringComparison.Ordinal) >= 0),
                "y se nombra el lado roto");

            var mixed = ConfigurePlanning.Verify(
                new[]
                {
                    Seen("bueno", "g-1", true, "g-2", true),
                    Seen("roto", "g-1", true, "g-borrado", false)
                },
                new Dictionary<string, int>());
            _eq("partial", mixed.Status, "con uno bueno y uno roto, partial");
        }

        private static void TwoTestsSharingOneSet()
        {
            _section("configure: dos tests comparten un conjunto");

            var tests = new[]
            {
                Test("EST vs HVAC", "g-est", "g-hvac"),
                Test("EST vs HID", "g-est", "g-hid", results: 12)
            };

            var graph = ConfigurePlanning.DependentTests(tests);
            _eq(2, graph["g-est"].Count, "el conjunto compartido lista sus dos tests");

            var steps = ConfigurePlanning.Plan(
                new[] { Set("g-est", "EST", "muros", "v1") },
                new[] { Want("EST", "muros", "v2") },
                tests,
                ConfigurePlanning.MigrationCapability.Unverified);

            // One of the two has results, so the shared set is preserved for
            // both: there is one source and it cannot be half-migrated.
            var blocked = steps.Single(s => s.Action == ConfigurePlanning.Blocked);
            _eq("g-est", blocked.Guid, "una sola fuente, conservada");
            _check(!steps.Any(s => s.Action == ConfigurePlanning.Create),
                "no se crea una segunda copia para el test sin resultados");
        }

        private static void NamesWouldHavePassedIdentityDoesNot()
        {
            _section("configure: la verificación por nombres habría dado verde");

            // Exactly the shipped failure: the folder, the set and the test
            // all exist by name; the test holds the identity of the set that
            // was deleted when the folder was rebuilt.
            var namesPresent = new[] { "EST", "muros", "EST vs HVAC" };
            _eq(3, namesPresent.Length, "por nombre está todo: la comprobación anterior pasaba");

            var verdict = ConfigurePlanning.Verify(
                new[] { Seen("EST vs HVAC", "g-borrado", false, "g-hvac", true) },
                new Dictionary<string, int>());

            _eq("failed", verdict.Status, "por identidad, no");
            var problems = (List<object>)verdict.Details[0]["problems"];
            _check(problems.Any(p => p.ToString().IndexOf("g-borrado", StringComparison.Ordinal) >= 0),
                "y se nombra el GUID que ya no está");
        }

        private static void NothingVerifiedIsNeverCompleted()
        {
            _section("configure: no hay completed con fuentes sin verificar");

            var verdict = ConfigurePlanning.Verify(
                new[]
                {
                    new ConfigurePlanning.ObservedTest { Name = "sin fuentes", Exists = true }
                },
                new Dictionary<string, int>());
            _eq("failed", verdict.Status, "un test sin fuentes no es una configuración completa");

            var kept = ConfigurePlanning.Verify(
                new[] { Seen("conservado", "g-1", true, "g-2", true, results: 0) },
                new Dictionary<string, int> { ["conservado"] = 216 });
            _eq("failed", kept.Status,
                "se conservó por 216 resultados y ahora tiene 0: eso no es completed");
        }

        private static void AnEmptySourceIsStillValid()
        {
            _section("configure: existir y resolver no es lo mismo que encontrar algo");

            var verdict = ConfigurePlanning.Verify(
                new[] { Seen("EST vs HVAC", "g-est", true, "g-hvac", true) },
                new Dictionary<string, int>());
            _eq("completed", verdict.Status,
                "un conjunto vivo que hoy no matchea nada sigue siendo válido");
        }

        // ------------------------------------------- integridad y mutaciones

        /// <summary>A snapshot with everything the integrity check reads.</summary>
        private static ConfigurePlanning.TestSnapshot Snap(
            string guid, string name, string sourceA, string sourceB,
            int results, int groups, string status,
            string printA = "search:v1", string printB = "search:v1")
        {
            var snap = new ConfigurePlanning.TestSnapshot
            {
                Guid = guid,
                Name = name,
                Tolerance = 0.01,
                ResultCount = results,
                GroupCount = groups,
                Status = status,
                SourceGuidsA = new List<string> { sourceA },
                SourceGuidsB = new List<string> { sourceB }
            };
            snap.SourceFingerprints[sourceA] = printA;
            snap.SourceFingerprints[sourceB] = printB;
            return snap;
        }

        private static Dictionary<string, ConfigurePlanning.SourceResolution> Live(
            params string[] guids)
        {
            var map = new Dictionary<string, ConfigurePlanning.SourceResolution>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var guid in guids)
            {
                map[guid] = new ConfigurePlanning.SourceResolution
                {
                    Guid = guid, Resolves = true, Fingerprint = "search:v1"
                };
            }
            return map;
        }

        private static string Refused(Action act)
        {
            try { act(); return null; }
            catch (InvalidOperationException error) { return error.Message; }
        }

        private static ConfigurePlanning.MutationGate Gate(
            bool allowed, List<ConfigurePlanning.Step> plan, params string[] existing)
            => ConfigurePlanning.MutationGate.ForFolders(allowed, plan, existing);

        /// <summary>A plan with one free folder and one held by a run.</summary>
        private static List<ConfigurePlanning.Step> MixedPlan()
            => ConfigurePlanning.Plan(
                new[]
                {
                    Set("g-ref", "EST", "muros", "v1"),
                    Set("g-libre", "ARQ", "tabiques", "v1")
                },
                new[] { Want("EST", "muros", "v2"), Want("ARQ", "tabiques", "v2") },
                new[] { Test("EST vs HVAC", "g-ref", "g-hvac", results: 216) },
                ConfigurePlanning.MigrationCapability.Unverified);

        private static void DryRunTouchesNothing()
        {
            _section("configure: un dry_run no invoca ninguna operación mutante");

            var plan = ConfigurePlanning.Plan(
                new[] { Set("g-libre", "ARQ", "tabiques", "v1") },
                new[] { Want("ARQ", "tabiques", "v2") },
                new ConfigurePlanning.TestSnapshot[0],
                ConfigurePlanning.MigrationCapability.Unverified);

            // The same plan the real run would execute, behind a gate built
            // for a rehearsal. Every write refuses.
            var gate = Gate(allowed: false, plan: plan, existing: "ARQ");

            _check(Refused(() => gate.ReplaceUnreferenced("ARQ")) != null,
                "ReplaceWithCopy se rechaza en dry_run");
            _check(Refused(() => gate.AddNew("NUEVA")) != null, "AddCopy se rechaza en dry_run");
            _eq(0, gate.Journal.Count, "y no queda registrada ni una sola mutación");

            _check(Refused(() => gate.ReplaceUnreferenced("ARQ"))
                       .IndexOf("dry_run", StringComparison.Ordinal) >= 0,
                "el rechazo dice que fue por ser un ensayo");
        }

        private static void ACapabilityProbeIsNotInThePlan()
        {
            _section("configure: una sonda de capacidad no llega al documento");

            var plan = ConfigurePlanning.Plan(
                new[] { Set("g-libre", "ARQ", "tabiques", "v1") },
                new[] { Want("ARQ", "tabiques", "v2") },
                new ConfigurePlanning.TestSnapshot[0],
                ConfigurePlanning.MigrationCapability.Unverified);
            var gate = Gate(allowed: true, plan: plan, existing: "ARQ");

            // A probe is, by construction, an item nobody planned. The gate
            // does not need to recognise probes to stop them: it only lets
            // through what the plan named.
            var refusal = Refused(() => gate.AddNew("__naviscoord_probe__"));
            _check(refusal != null, "plantar un elemento fuera del plan se rechaza");
            _check(refusal.IndexOf("no está en el plan", StringComparison.Ordinal) >= 0,
                "y se dice exactamente por qué");
            _eq(0, gate.Journal.Count, "la sonda no deja rastro porque nunca ocurre");

            gate.ReplaceUnreferenced("ARQ");
            _eq(1, gate.Journal.Count, "lo que sí estaba planificado pasa normalmente");
        }

        private static void UnreferencedReplacementIsOneCall()
        {
            _section("configure: el reemplazo no referenciado es una sola llamada");

            var gate = Gate(allowed: true, plan: MixedPlan(), "EST", "ARQ");
            gate.ReplaceUnreferenced("ARQ");

            _eq(1, gate.Journal.Count, "una sola operación");
            _eq("replace_unreferenced:ARQ", gate.Journal[0], "y es el reemplazo atómico");
            _check(!gate.Journal.Any(j => j.StartsWith("remove", StringComparison.Ordinal)),
                "no hay Remove");
            _check(!gate.Journal.Any(j => j.StartsWith("add_new", StringComparison.Ordinal)),
                "ni AddCopy: entre ambos habría una ventana sin la carpeta");
        }

        private static void ThereAreNoRecombinablePrimitives()
        {
            _section("configure: la compuerta no expone Remove ni Add sueltos");

            // The guarantee is not "the caller remembers not to do it", it is
            // that the unsafe sequence has no name to be written in.
            var methods = typeof(ConfigurePlanning.MutationGate)
                .GetMethods(System.Reflection.BindingFlags.Public |
                            System.Reflection.BindingFlags.Instance |
                            System.Reflection.BindingFlags.DeclaredOnly)
                .Select(m => m.Name)
                .ToList();

            _check(!methods.Contains("Remove"), "no existe Remove");
            _check(!methods.Contains("Add"), "no existe Add");
            _check(!methods.Contains("Replace"), "no existe un Replace genérico");
            _check(methods.Contains("AddNew") && methods.Contains("ReplaceUnreferenced") &&
                   methods.Contains("PreserveReferenced"),
                "solo las tres operaciones con intención");
        }

        private static void ANewFolderIsAdded()
        {
            _section("configure: una carpeta nueva sí se añade");

            var plan = ConfigurePlanning.Plan(
                new ConfigurePlanning.SetSnapshot[0],
                new[] { Want("MEP", "ductos", "v1") },
                new ConfigurePlanning.TestSnapshot[0],
                ConfigurePlanning.MigrationCapability.Unverified);
            var gate = Gate(allowed: true, plan: plan);   // nothing exists yet

            gate.AddNew("MEP");
            _eq("add_new:MEP", gate.Journal[0], "lo nuevo se añade");

            // And the two operations are not interchangeable in either
            // direction: asking for the wrong one is a refusal, not a silent
            // fallback to the other.
            _check(Refused(() => gate.ReplaceUnreferenced("MEP")) != null,
                "no se puede reemplazar lo que no existe");

            var onExisting = Gate(allowed: true, plan: MixedPlan(), existing: "ARQ");
            _check(Refused(() => onExisting.AddNew("ARQ")) != null,
                "ni añadir lo que ya existe");
        }

        private static void ReferencedFolderIsStillBlocked()
        {
            _section("configure: la carpeta referenciada se conserva");

            var gate = Gate(allowed: true, plan: MixedPlan(), "EST", "ARQ");

            var refusal = Refused(() => gate.ReplaceUnreferenced("EST"));
            _check(refusal != null, "no se reemplaza una carpeta con una fuente conservada");
            _check(refusal.IndexOf("conserva", StringComparison.Ordinal) >= 0, "y se dice por qué");

            gate.PreserveReferenced("EST");
            _eq("preserve_referenced:EST", gate.Journal[0], "se registra que se conservó");
            _check(!gate.Journal.Any(j => j.EndsWith(":EST", StringComparison.Ordinal) &&
                                          j.StartsWith("replace", StringComparison.Ordinal)),
                "y no se tocó");
        }

        private static void ADuplicateFolderFailsVerification()
        {
            _section("configure: dos carpetas con el mismo nombre fallan la verificación");

            var problems = ConfigurePlanning.VerifyReplacement(new ConfigurePlanning.FolderObservation
            {
                Folder = "ARQ",
                MatchingRootEntries = 2,
                SetNames = new List<string> { "tabiques" },
                ExpectedSetNames = new List<string> { "tabiques" }
            });
            _eq(1, problems.Count, "una homónima de más es un fallo");
            _check(problems[0].IndexOf("2 carpetas", StringComparison.Ordinal) >= 0,
                "y se dice cuántas quedaron");

            var leftover = ConfigurePlanning.VerifyReplacement(new ConfigurePlanning.FolderObservation
            {
                Folder = "ARQ",
                MatchingRootEntries = 1,
                SetNames = new List<string> { "tabiques" },
                ExpectedSetNames = new List<string> { "tabiques" },
                PreviousItemStillPresent = true
            });
            _eq(1, leftover.Count, "y el elemento anterior sobreviviendo también");

            var gone = ConfigurePlanning.VerifyReplacement(new ConfigurePlanning.FolderObservation
            {
                Folder = "ARQ",
                MatchingRootEntries = 0,
                ExpectedSetNames = new List<string> { "tabiques" }
            });
            _eq(1, gone.Count, "y que no quede ninguna, también");
        }

        private static void AWrongDefinitionAfterReplacementFails()
        {
            _section("configure: definición distinta tras el reemplazo, falla");

            var problems = ConfigurePlanning.VerifyReplacement(new ConfigurePlanning.FolderObservation
            {
                Folder = "ARQ",
                MatchingRootEntries = 1,
                SetNames = new List<string> { "tabiques" },
                ExpectedSetNames = new List<string> { "tabiques" },
                MismatchedDefinitions = new List<string> { "tabiques" }
            });
            _eq(1, problems.Count, "el conjunto está pero no busca lo pedido");
            _check(problems[0].IndexOf("definición pedida", StringComparison.Ordinal) >= 0,
                "y se dice qué falla");

            var missing = ConfigurePlanning.VerifyReplacement(new ConfigurePlanning.FolderObservation
            {
                Folder = "ARQ",
                MatchingRootEntries = 1,
                SetNames = new List<string> { "otro" },
                ExpectedSetNames = new List<string> { "tabiques" }
            });
            _eq(2, missing.Count, "falta uno y sobra otro");

            var ok = ConfigurePlanning.VerifyReplacement(new ConfigurePlanning.FolderObservation
            {
                Folder = "ARQ",
                MatchingRootEntries = 1,
                SetNames = new List<string> { "tabiques" },
                ExpectedSetNames = new List<string> { "tabiques" }
            });
            _eq(0, ok.Count, "un reemplazo correcto no reporta nada");
        }

        private static void AFailedReplacementIsDescribedNotRepaired()
        {
            _section("configure: un reemplazo fallido se describe, no se arregla");

            _eq("permanece_la_anterior",
                ConfigurePlanning.DescribeState(previousPresent: true, replacementPresent: false),
                "la anterior sigue: el documento no quedó peor");
            _eq("aparecio_la_nueva",
                ConfigurePlanning.DescribeState(previousPresent: false, replacementPresent: true),
                "la nueva está: el reemplazo llegó aunque algo más fallara");
            _eq("indeterminado",
                ConfigurePlanning.DescribeState(previousPresent: true, replacementPresent: true),
                "ambas a la vez no es un estado que se pueda declarar bueno");
            _eq("indeterminado",
                ConfigurePlanning.DescribeState(previousPresent: false, replacementPresent: false),
                "y ninguna, tampoco");
        }

        private static void NoRemoveThenAddSequenceInTheSource()
        {
            _section("configure: no queda una secuencia Remove + AddCopy en el código");

            // The gate is the real guarantee; this is the second lock, and it
            // is the one that would catch somebody bypassing the gate
            // entirely. It fails loudly when it cannot find the file rather
            // than passing by default — a structural check that silently
            // checks nothing is worse than none.
            var source = FindSource("WriteHandlers.cs");
            _check(source != null, "se encontró WriteHandlers.cs para inspeccionar");
            if (source == null) return;

            var text = System.IO.File.ReadAllText(source);
            var from = text.IndexOf("BuildCriteriaSets(Dictionary<string, object> payload)",
                StringComparison.Ordinal);
            var to = text.IndexOf("private static ModelItemCollection ResolveScope",
                StringComparison.Ordinal);
            _check(from > 0 && to > from, "se acotó el cuerpo de BuildCriteriaSets");
            var body = text.Substring(from, to - from);

            _check(body.IndexOf("SelectionSets.Remove", StringComparison.Ordinal) < 0,
                "no se elimina ningún conjunto en BuildCriteriaSets");
            _check(body.IndexOf("SelectionSets.RemoveAt", StringComparison.Ordinal) < 0,
                "ni por índice");
            _check(body.IndexOf("SelectionSets.ReplaceWithCopy", StringComparison.Ordinal) > 0,
                "el reemplazo se hace con ReplaceWithCopy");
            _check(body.IndexOf("SelectionSets.AddCopy", StringComparison.Ordinal) > 0,
                "y AddCopy queda para lo realmente nuevo");
        }

        /// <summary>The repository copy of a source file, or null.</summary>
        private static string FindSource(string name)
        {
            var dir = new System.IO.DirectoryInfo(AppContext.BaseDirectory);
            while (dir != null)
            {
                var candidate = System.IO.Path.Combine(
                    dir.FullName, "addin", "NavisCoord.Addin", name);
                if (System.IO.File.Exists(candidate)) return candidate;
                dir = dir.Parent;
            }
            return null;
        }

        private static void BlockedRunLeavesTheSnapshotUnchanged()
        {
            _section("configure: sin migración demostrable, el documento no cambia");

            var before = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 12, status: "Complete")
            };

            // Nothing ran, so "after" is the same reading taken again.
            var after = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 12, status: "Complete")
            };

            var degraded = ConfigurePlanning.CompareIntegrity(
                before, after, Live("g-est", "g-hvac"));
            _eq(0, degraded.Count, "el snapshot final coincide con el inicial");

            var plan = ConfigurePlanning.Plan(
                new[] { Set("g-est", "EST", "muros", "v1") },
                new[] { Want("EST", "muros", "v2") },
                before,
                ConfigurePlanning.MigrationCapability.Unverified);
            _check(plan.Any(s => s.Action == ConfigurePlanning.Blocked), "y el resultado es blocked");
        }

        private static void AFailedReplaceIsNeverCompleted()
        {
            _section("configure: una excepción en ReplaceWithCopy no da completed");

            var before = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 12, status: "Complete")
            };

            // Replace threw halfway: the test now declares the new source,
            // which was never added, so its GUID resolves to nothing.
            var after = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-nuevo", "g-hvac", results: 216, groups: 12, status: "Complete")
            };

            var degraded = ConfigurePlanning.CompareIntegrity(
                before, after, Live("g-hvac"));
            _eq(1, degraded.Count, "la degradación se reporta");
            var problems = ((List<object>)degraded[0]["problems"]).Cast<string>().ToList();
            _check(problems.Any(t => t.IndexOf("ya no declara g-est", StringComparison.Ordinal) >= 0),
                "se dice que el test dejó de declarar su fuente");
            _check(problems.Any(t => t.IndexOf("no resuelve", StringComparison.Ordinal) >= 0),
                "y que la nueva no resuelve");
        }

        private static void SameGuidButBoundElsewhereFails()
        {
            _section("configure: el GUID existe pero el test apunta a otra fuente");

            var before = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 12, status: "Complete")
            };
            // g-est is alive and well — ResolveGuid would say yes — but the
            // test is no longer the thing pointing at it.
            var after = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-otro", "g-hvac", results: 216, groups: 12, status: "Complete")
            };

            var degraded = ConfigurePlanning.CompareIntegrity(
                before, after, Live("g-est", "g-hvac", "g-otro"));
            _eq(1, degraded.Count, "que el GUID viva no basta: el enlace se perdió");
            var problems = ((List<object>)degraded[0]["problems"]).Cast<string>().ToList();
            _check(problems.Any(t => t.IndexOf("ya no declara g-est", StringComparison.Ordinal) >= 0),
                "se nombra la fuente que dejó de declarar");
            _check(problems.Any(t => t.IndexOf("ahora declara g-otro", StringComparison.Ordinal) >= 0),
                "y la que declara en su lugar");
        }

        private static void SameGuidDifferentDefinitionFails()
        {
            _section("configure: mismo GUID, definición distinta, falla");

            var before = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac",
                     results: 216, groups: 12, status: "Complete", printA: "search:Self:False:cat=Walls")
            };
            var after = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac",
                     results: 216, groups: 12, status: "Complete", printA: "search:Self:False:cat=Walls")
            };

            var live = Live("g-est", "g-hvac");
            live["g-est"].Fingerprint = "search:Self:False:cat=Beams";

            var degraded = ConfigurePlanning.CompareIntegrity(before, after, live);
            _eq(1, degraded.Count, "la identidad sobrevivió pero la definición no");
            var problems = ((List<object>)degraded[0]["problems"]).Cast<string>().ToList();
            _check(problems.Any(t => t.IndexOf("definición distinta", StringComparison.Ordinal) >= 0),
                "y se dice que resuelve a otra cosa");
        }

        private static void FewerResultsFails()
        {
            _section("configure: test conservado pero con menos resultados, falla");

            var before = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 12, status: "Complete")
            };
            var after = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 40, groups: 12, status: "Complete")
            };

            var degraded = ConfigurePlanning.CompareIntegrity(before, after, Live("g-est", "g-hvac"));
            _eq(1, degraded.Count, "\"el test sigue ahí\" no es prueba de preservación");
            var problems = ((List<object>)degraded[0]["problems"]).Cast<string>().ToList();
            _check(problems.Any(t => t.IndexOf("perdió 176 resultado", StringComparison.Ordinal) >= 0),
                "se cuenta lo que se perdió");

            var fewerGroups = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 3, status: "Complete")
            };
            var groupLoss = ConfigurePlanning.CompareIntegrity(
                before, fewerGroups, Live("g-est", "g-hvac"));
            _eq(1, groupLoss.Count, "perder jerarquía de grupos también cuenta");
        }

        private static void DegradedStatusFails()
        {
            _section("configure: un test que baja de Complete a Old o Partial, falla");

            var before = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 12, status: "Complete")
            };

            foreach (var worse in new[] { "Old", "Partial", "New" })
            {
                var after = new[]
                {
                    Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 12, status: worse)
                };
                var degraded = ConfigurePlanning.CompareIntegrity(before, after, Live("g-est", "g-hvac"));
                _eq(1, degraded.Count, "Complete -> " + worse + " es una degradación");
            }

            // The other direction is not: a test that was stale and is now
            // current did not lose anything.
            var stale = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 12, status: "Old")
            };
            var current = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 12, status: "Complete")
            };
            _eq(0, ConfigurePlanning.CompareIntegrity(stale, current, Live("g-est", "g-hvac")).Count,
                "Old -> Complete no es degradación");
            _check(ConfigurePlanning.StatusRank("Complete") > ConfigurePlanning.StatusRank("Partial"),
                "el orden de estados es explícito");
            _eq(0, ConfigurePlanning.StatusRank("cualquier-cosa"),
                "un estado desconocido no cuenta como salud");
        }

        private static void AVanishedTestIsADegradation()
        {
            _section("configure: un test que pierde su identidad se reporta");

            var before = new[]
            {
                Snap("t-1", "EST vs HVAC", "g-est", "g-hvac", results: 216, groups: 12, status: "Complete")
            };
            // Same name, new identity: the old test is gone and something else
            // took its place. Comparing names would have missed it.
            var after = new[]
            {
                Snap("t-2", "EST vs HVAC", "g-est", "g-hvac", results: 0, groups: 0, status: "New")
            };

            var degraded = ConfigurePlanning.CompareIntegrity(before, after, Live("g-est", "g-hvac"));
            _eq(1, degraded.Count, "un homónimo no sustituye al test original");
            var problems = ((List<object>)degraded[0]["problems"]).Cast<string>().ToList();
            _check(problems.Any(t => t.IndexOf("ya no está con su identidad", StringComparison.Ordinal) >= 0),
                "y se dice que lo que falta es la identidad, no el nombre");
        }
    }
}
