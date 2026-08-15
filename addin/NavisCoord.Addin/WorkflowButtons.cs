using System;
using Autodesk.Navisworks.Api.Plugins;

namespace NavisCoord
{
    /// <summary>
    /// The coordination workflow as classic <c>AddInPlugin</c> buttons (Tool
    /// add-ins), one per step.
    /// </summary>
    /// <remarks>
    /// <c>AddInPlugin</c> rather than a custom ribbon tab on purpose: it always
    /// renders. A <c>RibbonLayout</c> tab depends on an undocumented XAML
    /// schema and a matching plugin assembly, and when it fails it fails
    /// silently — no tab, no error, no clue. These appear under Tool Add-ins on
    /// every supported Navisworks version with no extra assembly at all.
    ///
    /// Each button calls the SAME <see cref="CoordinationWorkflow"/> service
    /// the HTTP routes call, and only renders the result differently. Nothing
    /// about what a step DOES lives in this file, so a button and a tool call
    /// cannot drift apart.
    ///
    /// Anyone wanting a branded tab of their own can add a ribbon assembly that
    /// invokes <see cref="WorkflowSteps.Run"/> — there is no need to fork the
    /// engine to change a logo.
    /// </remarks>
    internal static class WorkflowSteps
    {
        public const string Audit = "auditar";
        public const string Configure = "configurar";
        public const string Run = "correr";
        public const string Group = "agrupar";
        public const string Rules = "reglas";

        internal static int Execute(string step)
        {
            try
            {
                var doc = Autodesk.Navisworks.Api.Application.ActiveDocument;
                if (doc == null || doc.IsClear)
                {
                    return ConfigurePlugin.Fail(
                        "Abre primero el archivo de coordinación con los modelos anexados.");
                }

                // The same store the MCP tools read, so a profile loaded over
                // the port is the one this button applies too.
                var profile = ProfileStore.Active();
                if (profile == null) return ConfigurePlugin.Fail(ProfileLocator.NotFoundMessage());

                string summary;
                switch (step)
                {
                    case Audit:
                        summary = "Paso 0 — Auditoría de modelos\n\n" +
                                  WorkflowText.AuditModels(CoordinationWorkflow.AuditModels(doc, profile)) +
                                  "\n\nSi todo está co-ubicado y limpio, sigue: 1. Configurar.";
                        break;
                    case Configure:
                        summary = "Paso 1 — Configurar\n\n" +
                                  WorkflowText.Configure(CoordinationWorkflow.Configure(doc, profile)) +
                                  "\n\nSigue: 2. Correr tests.";
                        break;
                    case Run:
                        summary = "Paso 2 — Correr tests\n\n" +
                                  WorkflowText.RunAll(CoordinationWorkflow.RunAll(doc)) +
                                  "\n\nSigue: 3. Agrupar por nivel.";
                        break;
                    case Group:
                        summary = "Paso 3 — Agrupar por nivel\n\n" +
                                  WorkflowText.GroupByLevel(CoordinationWorkflow.GroupByLevel(doc)) +
                                  "\n\nListo para exportar o crear incidencias.";
                        break;
                    default:
                        summary = "Aplicar reglas\n\n" +
                                  WorkflowText.Rules(CoordinationWorkflow.ApplyRules(doc, profile));
                        break;
                }

                BridgeHost.Log(summary.Replace("\n", " | "));
                ConfigurePlugin.Inform(summary);
                return 0;
            }
            catch (Exception ex)
            {
                return ConfigurePlugin.Fail("NavisCoord (" + step + ") falló: " + ex.Message);
            }
        }
    }

    [Plugin("NavisCoord.Audit", "NVCD",
        DisplayName = "NavisCoord 0\nAuditar modelos",
        ToolTip = "Valida que los modelos anexados estén co-ubicados (no a kilómetros), con nomenclatura y elementos")]
    public sealed class AuditModelsPlugin : AddInPlugin
    {
        public override int Execute(params string[] parameters)
            => WorkflowSteps.Execute(WorkflowSteps.Audit);
    }

    [Plugin("NavisCoord.Sets", "NVCD",
        DisplayName = "NavisCoord 1\nConfigurar",
        ToolTip = "Crea las carpetas de search sets por disciplina y la matriz de clash definidas en el perfil")]
    public sealed class ConfigureSetsPlugin : AddInPlugin
    {
        public override int Execute(params string[] parameters)
            => WorkflowSteps.Execute(WorkflowSteps.Configure);
    }

    [Plugin("NavisCoord.RunTests", "NVCD",
        DisplayName = "NavisCoord 2\nCorrer tests",
        ToolTip = "Corre todos los clash tests (equivale a Run All); Navisworks queda ocupado durante la corrida")]
    public sealed class RunTestsPlugin : AddInPlugin
    {
        public override int Execute(params string[] parameters)
            => WorkflowSteps.Execute(WorkflowSteps.Run);
    }

    [Plugin("NavisCoord.GroupByLevel", "NVCD",
        DisplayName = "NavisCoord 3\nAgrupar por nivel",
        ToolTip = "Agrupa los choques de cada test por nivel, como se preparan las incidencias")]
    public sealed class GroupByLevelPlugin : AddInPlugin
    {
        public override int Execute(params string[] parameters)
            => WorkflowSteps.Execute(WorkflowSteps.Group);
    }
}
