using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// clash/rename: which names the add-in may replace, and what it plans.
    /// </summary>
    /// <remarks>
    /// The whole contract of the operation lives in this decision. A rename
    /// that touched a name the coordinator typed would erase their work with
    /// no undo short of reopening the file, so it is decided by a function
    /// that has never seen the API and is exercised here name by name.
    /// </remarks>
    internal static class RenameTests
    {
        public static void Run(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check)
        {
            DefaultNames(section, eq, check);
            OwnNames(section, eq, check);
            PlanRenamesOnlyWhatItMay(section, eq, check);
            PlanGroupsMultiClashIssues(section, eq, check);
            PlanSpansTests(section, eq, check);
            PlanRespectsCoordinatorGroups(section, eq, check);
            PlanReclaimsOwnGroupsOnlyWhenAsked(section, eq, check);
            PlanIsIdempotent(section, eq, check);
            NamesAreTruncated(section, eq, check);
            RouteIsAContractedMutation(section, eq, check);
        }

        private static ClashNaming.ResultSnapshot R(string guid, string test, string name, string parent = "")
            => new ClashNaming.ResultSnapshot { Guid = guid, Test = test, Name = name, Parent = parent };

        private static ClashNaming.IssueSpec I(string name, string[] guids, string[] names)
            => new ClashNaming.IssueSpec { Name = name, Guids = guids.ToList(), Names = names.ToList() };

        private static Dictionary<string, ClashNaming.ResultSnapshot> Doc(params ClashNaming.ResultSnapshot[] results)
            => results.ToDictionary(r => r.Guid, StringComparer.OrdinalIgnoreCase);

        private static void DefaultNames(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("renombrar: nombres por defecto de Navisworks");
            foreach (var name in new[] { "Clash1", "Clash 12", "Conflicto1", "conflicto 7", "CONFLICTO42" })
            {
                check(ClashNaming.IsDefault(name), "«" + name + "» es por defecto");
            }
            foreach (var name in new[] { "", "Conflicto", "Conflicto 1 revisar", "Losa N3 ver con EST",
                                         "001.1", "Clash1b" })
            {
                check(!ClashNaming.IsDefault(name), "«" + name + "» no es por defecto");
            }
        }

        private static void OwnNames(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("renombrar: nombres que escribió NavisCoord");
            foreach (var name in new[] { "001.1", "012.14", "001 · CRÍTICO · EST × MEP", "1335 · BAJO",
                                         "014 · Losa vs Ducto" })
            {
                check(ClashNaming.IsOwnResultName(name), "«" + name + "» es de NavisCoord");
            }
            foreach (var name in new[] { "1.1", "Conflicto1", "001 revisar", "Pasa por el vano", "26.10" })
            {
                check(!ClashNaming.IsOwnResultName(name), "«" + name + "» no es de NavisCoord");
            }

            check(ClashNaming.IsOwnGroupName("001 · CRÍTICO · EST × MEP · Losa vs Ducto"), "grupo numerado");
            check(ClashNaming.IsOwnGroupName("[CRITICAL] ISS-db75bd49ae215134 EST×MEP → MEP"),
                "grupo de navis_apply_groups");
            check(ClashNaming.IsOwnGroupName("[low] ISS-1 ARQ×EST → ARQ"), "en minúsculas también");
            check(!ClashNaming.IsOwnGroupName("Nivel 03"), "un grupo por nivel no se reclama: no se distingue del coordinador");
            check(!ClashNaming.IsOwnGroupName("Shafts torre B"), "grupo del coordinador");
        }

        private static void PlanRenamesOnlyWhatItMay(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("renombrar: solo nombres por defecto, salvo opt-in");
            var doc = Doc(
                R("a", "T1", "Conflicto1"),
                R("b", "T1", "Pasa por el vano, OK"),
                R("c", "T1", "001 · ALTO · viejo"));
            var issues = new[]
            {
                I("001 · CRÍTICO · A", new[] { "a" }, new[] { "001 · CRÍTICO · A" }),
                I("002 · ALTO · B", new[] { "b" }, new[] { "002 · ALTO · B" }),
                I("003 · MEDIO · C", new[] { "c" }, new[] { "003 · MEDIO · C" })
            };

            var plan = ClashNaming.Plan(issues, doc, overwriteOwn: false);
            eq(1, plan.Renames.Count, "solo el por defecto");
            eq("a", plan.Renames[0].Guid, "es «a»");
            eq("001 · CRÍTICO · A", plan.Renames[0].To, "con el nombre pedido");
            check(plan.Skipped.Any(s => s.Guid == "b" && s.Reason == ClashNaming.ReasonCoordinatorName),
                "el nombre del coordinador se respeta y se dice por qué");
            check(plan.Skipped.Any(s => s.Guid == "c" && s.Reason == ClashNaming.ReasonOwnNameProtected),
                "el nombre previo de NavisCoord se protege sin opt-in");

            var overwrite = ClashNaming.Plan(issues, doc, overwriteOwn: true);
            eq(2, overwrite.Renames.Count, "con opt-in también el de NavisCoord");
            check(overwrite.Renames.All(r => r.Guid != "b"), "y NUNCA el del coordinador");
        }

        private static void PlanGroupsMultiClashIssues(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("renombrar: problema de varios choques = grupo + 001.1, 001.2");
            var doc = Doc(R("a", "T1", "Conflicto1"), R("b", "T1", "Conflicto2"), R("c", "T1", "Conflicto3"));
            var plan = ClashNaming.Plan(new[]
            {
                I("001 · CRÍTICO · A", new[] { "b", "a" }, new[] { "001.1", "001.2" }),
                I("002 · BAJO · C", new[] { "c" }, new[] { "002 · BAJO · C" })
            }, doc, false);

            eq(1, plan.Groups.Count, "un solo grupo: el de un choque no lo amerita");
            eq("001 · CRÍTICO · A", plan.Groups[0].Name, "con el nombre del problema");
            eq("T1", plan.Groups[0].Test, "dentro de su test");
            eq("b,a", string.Join(",", plan.Groups[0].Moves), "mueve los dos, en el orden del plan");
            eq("001.1", plan.Renames.First(r => r.Guid == "b").To, "b es .1");
            eq("001.2", plan.Renames.First(r => r.Guid == "a").To, "a es .2");
            eq("002 · BAJO · C", plan.Renames.First(r => r.Guid == "c").To, "el suelto lleva el nombre completo");
            eq(0, plan.Missing, "nada perdido");

            var gone = ClashNaming.Plan(new[] { I("001", new[] { "zz", "a" }, new[] { "001.1", "001.2" }) }, doc, false);
            eq(1, gone.Missing, "un GUID que ya no está se cuenta, no se inventa");
            eq(0, gone.Groups.Count, "y con uno solo encontrado no hay grupo");
        }

        private static void PlanSpansTests(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("renombrar: un problema repartido en dos tests");
            var doc = Doc(R("a", "NC EST vs MEP", "Conflicto1"), R("b", "NC EST vs MEP", "Conflicto2"),
                          R("c", "NC ARQ vs MEP", "Conflicto1"), R("d", "NC ARQ vs MEP", "Conflicto2"));
            var plan = ClashNaming.Plan(new[]
            {
                I("001 · ALTO · X", new[] { "a", "b", "c", "d" }, new[] { "001.1", "001.2", "001.3", "001.4" })
            }, doc, false);
            eq(2, plan.Groups.Count, "un grupo por test: Navisworks no deja un grupo entre tests");
            check(plan.Groups.All(g => g.Name.StartsWith("001 · ALTO · X · ")),
                "cada uno dice de qué test es");
        }

        private static void PlanRespectsCoordinatorGroups(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("renombrar: lo que el coordinador agrupó se queda donde está");
            var doc = Doc(R("a", "T1", "Conflicto1", "Shafts torre B"), R("b", "T1", "Conflicto2"));
            var plan = ClashNaming.Plan(new[]
            {
                I("001 · ALTO · X", new[] { "a", "b" }, new[] { "001.1", "001.2" })
            }, doc, overwriteOwn: true);
            eq("b", string.Join(",", plan.Groups.SelectMany(g => g.Moves)), "solo se mueve el suelto");
            check(plan.Skipped.Any(s => s.Guid == "a" && s.Reason == ClashNaming.ReasonCoordinatorGroup),
                "el del grupo del coordinador queda, y se dice");
            check(plan.Renames.Any(r => r.Guid == "a" && r.To == "001.1"),
                "pero su nombre por defecto sí se numera");
        }

        private static void PlanReclaimsOwnGroupsOnlyWhenAsked(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("renombrar: grupos viejos de NavisCoord");
            var old = "[CRITICAL] ISS-db75bd49ae215134 EST×MEP → MEP";
            var doc = Doc(R("a", "T1", "Conflicto1", old), R("b", "T1", "Conflicto2", old),
                          R("c", "T1", "Conflicto3", old));
            var issues = new[]
            {
                I("001 · CRÍTICO · X", new[] { "a", "b" }, new[] { "001.1", "001.2" }),
                I("002 · ALTO · Y", new[] { "c" }, new[] { "002 · ALTO · Y" })
            };

            var asIs = ClashNaming.Plan(issues, doc, overwriteOwn: false);
            eq(0, asIs.Groups.Sum(g => g.Moves.Count), "sin opt-in no se saca nada del grupo viejo");
            eq(0, asIs.Loosen.Count, "ni se suelta");
            check(asIs.Skipped.Any(s => s.Reason == ClashNaming.ReasonOwnGroupProtected),
                "y se dice que hace falta el opt-in");

            var reclaim = ClashNaming.Plan(issues, doc, overwriteOwn: true);
            eq("a,b", string.Join(",", reclaim.Groups.SelectMany(g => g.Moves)), "con opt-in pasan al grupo nuevo");
            eq("c", string.Join(",", reclaim.Loosen.Select(l => l.Guid)), "y el de un solo choque queda suelto");
        }

        private static void PlanIsIdempotent(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("renombrar: correrlo dos veces no cambia nada");
            var doc = Doc(R("a", "T1", "001.1", "001 · CRÍTICO · X"), R("b", "T1", "001.2", "001 · CRÍTICO · X"),
                          R("c", "T1", "002 · ALTO · Y"));
            var issues = new[]
            {
                I("001 · CRÍTICO · X", new[] { "a", "b" }, new[] { "001.1", "001.2" }),
                I("002 · ALTO · Y", new[] { "c" }, new[] { "002 · ALTO · Y" })
            };
            foreach (var overwrite in new[] { false, true })
            {
                var plan = ClashNaming.Plan(issues, doc, overwrite);
                eq(0, plan.Renames.Count, "sin renombres (overwrite=" + overwrite + ")");
                eq(0, plan.Groups.Sum(g => g.Moves.Count), "sin movimientos (overwrite=" + overwrite + ")");
                eq(3, plan.AlreadyNamed, "los tres ya tienen su nombre (overwrite=" + overwrite + ")");
            }
        }

        private static void NamesAreTruncated(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("renombrar: nombres largos se recortan");
            var longName = "001 · CRÍTICO · " + new string('x', 400);
            var doc = Doc(R("a", "T1", "Conflicto1"), R("b", "T1", "Conflicto2"));
            var plan = ClashNaming.Plan(new[]
            {
                I(longName, new[] { "a", "b" }, new[] { "001.1", "001.2" })
            }, doc, false);
            check(plan.Groups[0].Name.Length <= ClashNaming.MaxLength + 1, "el grupo cabe");
            check(ClashNaming.IsOwnGroupName(plan.Groups[0].Name), "y sigue siendo reconocible como propio");
        }

        private static void RouteIsAContractedMutation(
            Action<string> section, Action<object, object, string> eq, Action<bool, string> check)
        {
            section("renombrar: clash/rename tiene contrato de mutación");
            var contract = RouteContracts.For("clash/rename");
            check(contract != null, "existe");
            check(contract != null && contract.IsMutation, "es mutación");
            check(contract != null && contract.RequiresFingerprint, "exige huella");
            check(contract != null && contract.SupportsDryRun, "tiene ensayo");
            check(contract != null && contract.SupportsJob, "puede ir como trabajo");
            check(contract != null && contract.RequiresIdempotency, "acepta idempotency_key");
            check(Capabilities.Operations.Contains("clash_rename"), "y se anuncia como operación");
        }
    }
}
