using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// Editing a clash tree without holding a handle across a mutation.
    /// </summary>
    /// <remarks>
    /// Every wrapper the Navisworks API returns is a pointer into a tree it
    /// rebuilds when edited. A <c>ClashResult</c> kept across a status change,
    /// or a <c>ClashTest</c> kept across a move, is a dangling native pointer,
    /// and dereferencing one does not raise a managed exception — it ends the
    /// process. No test can reproduce that without a licence and a model, so
    /// what is asserted here is the property that prevents it: the object an
    /// edit receives is always the one resolved on that same iteration.
    /// </remarks>
    internal static class PlanningTests
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

            ResolvesBeforeEveryEdit();
            SurvivesAVanishingResult();
            NeverReusesTheLastHandle();
            SplitsAnIssueByOwningTest();
            ReportsTheOutcomeHonestly();
        }

        /// <summary>A stand-in for a wrapper: identity is what matters.</summary>
        private sealed class Handle
        {
            public string Guid;
            public int Generation;
        }

        /// <summary>
        /// A tree that invalidates every handle whenever it is edited, which
        /// is the behaviour the real one has and the reason for all of this.
        /// </summary>
        private sealed class FakeTree
        {
            private readonly HashSet<string> _present;
            public int Generation;
            public int Resolutions;
            public readonly List<string> Edited = new List<string>();

            public FakeTree(IEnumerable<string> guids)
                => _present = new HashSet<string>(guids, StringComparer.OrdinalIgnoreCase);

            public void Remove(string guid) => _present.Remove(guid);

            public Handle Resolve(string guid)
            {
                Resolutions++;
                return _present.Contains(guid)
                    ? new Handle { Guid = guid, Generation = Generation }
                    : null;
            }

            /// <summary>Edits, then rebuilds — so every earlier handle is stale.</summary>
            public void Edit(Handle handle)
            {
                if (handle.Generation != Generation)
                {
                    throw new InvalidOperationException(
                        $"handle de la generación {handle.Generation} usado en la {Generation}: "
                        + "eso es el puntero colgante que tumba Navisworks");
                }
                Edited.Add(handle.Guid);
                Generation++;
            }
        }

        private static void ResolvesBeforeEveryEdit()
        {
            _section("clash: se resuelve antes de cada mutación");

            var guids = new[] { "g-1", "g-2", "g-3" };
            var tree = new FakeTree(guids);

            var report = ClashPlanning.ApplyEach(guids, tree.Resolve, (id, h) => tree.Edit(h));

            _eq(3, report.Applied, "las tres se editan");
            _eq(3, tree.Resolutions, "y cada una se resolvió de nuevo antes de tocarla");
            _eq(3, tree.Generation, "cada edición reconstruyó el árbol");
            _check(report.Vanished.Count == 0, "ninguna desapareció");
        }

        private static void SurvivesAVanishingResult()
        {
            _section("clash: un resultado que desaparece a mitad");

            var guids = new[] { "g-1", "g-2", "g-3" };
            var tree = new FakeTree(guids);
            // The first edit takes the second result with it, which is what a
            // real rebuild does when a result is regrouped or dropped.
            var wrapped = ClashPlanning.ApplyEach(
                guids,
                id =>
                {
                    if (tree.Generation == 1) tree.Remove("g-2");
                    return tree.Resolve(id);
                },
                (id, h) => tree.Edit(h));

            _eq(2, wrapped.Applied, "se aplican las que siguen existiendo");
            _eq("g-2", string.Join(",", wrapped.Vanished), "y la que no, se declara");
            _check(!tree.Edited.Contains("g-2"),
                "no se editó con el handle viejo de la que ya no está");
            _eq("partial", ClashPlanning.Outcome(3, 2), "el lote es parcial, ni completo ni fallido");
        }

        private static void NeverReusesTheLastHandle()
        {
            _section("clash: nunca se reutiliza el handle anterior");

            var guids = new[] { "g-1", "g-2", "g-3", "g-4" };
            var tree = new FakeTree(guids);
            var seen = new List<Handle>();

            ClashPlanning.ApplyEach(
                guids,
                tree.Resolve,
                (id, h) =>
                {
                    // The fake throws if a stale generation reaches it, so
                    // reaching the end at all is the assertion. Identity is
                    // checked too, because a driver that cached would hand
                    // back the same object without changing generation.
                    _check(seen.All(previous => !ReferenceEquals(previous, h)),
                        $"{id} recibió un objeto nuevo, no el de la vuelta anterior");
                    seen.Add(h);
                    tree.Edit(h);
                });

            _eq(4, seen.Count, "cuatro ediciones, cuatro handles distintos");
            _eq(4, seen.Select(h => h.Generation).Distinct().Count(),
                "y cada uno de la generación vigente en su momento");
        }

        private static void SplitsAnIssueByOwningTest()
        {
            _section("clash: un problema que cruza varios tests");

            var owners = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                ["g-1"] = "EST vs HVAC",
                ["g-2"] = "EST vs HVAC",
                ["g-3"] = "EST vs HID",
            };

            var plans = ClashPlanning.PlanIssue("ISS-0007", owners.Keys, owners);
            _eq(2, plans.Count, "un grupo por test propietario");
            _eq("ISS-0007 · EST vs HID", plans[0].GroupName, "el nombre se sufija al partirse");
            _eq(2, plans.Single(p => p.TestName == "EST vs HVAC").Guids.Count,
                "cada grupo se lleva solo los suyos");

            var single = ClashPlanning.PlanIssue(
                "ISS-0008", new[] { "g-1", "g-2" }, owners);
            _eq(1, single.Count, "un solo test, un solo grupo");
            _eq("ISS-0008", single[0].GroupName, "y sin sufijo, que es el caso común");

            _eq(0, ClashPlanning.PlanIssue("ISS-0009", new[] { "desconocido" }, owners).Count,
                "un cruce que no está en el documento no crea grupo");
        }

        private static void ReportsTheOutcomeHonestly()
        {
            _section("clash: el veredicto del lote");

            _eq("completed", ClashPlanning.Outcome(3, 3), "todo verificado");
            _eq("partial", ClashPlanning.Outcome(3, 1), "algo se hizo y algo no");
            _eq("failed", ClashPlanning.Outcome(3, 0), "nada se pudo verificar");
            _eq("completed", ClashPlanning.Outcome(0, 0), "no pedir nada no es fallar");
        }
    }
}
