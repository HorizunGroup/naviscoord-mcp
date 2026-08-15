using System;
using Autodesk.Navisworks.Api.Plugins;

namespace NavisCoord
{
    /// <summary>
    /// The coordination workflow, one entry point per step.
    /// </summary>
    /// <remarks>
    /// These used to be classic <c>AddInPlugin</c> buttons, chosen because they
    /// always render: a <c>RibbonLayout</c> tab depends on an undocumented XAML
    /// schema and, when it fails, fails silently — no tab, no error, no clue.
    /// The cost was that Navisworks piles every <c>AddInPlugin</c> into "Tool
    /// add-ins" next to third-party exporters, so the product had five buttons
    /// and no tab of its own.
    ///
    /// They now hang off <see cref="NavisCoordTab"/>, which is safe to do
    /// because the XAML schema stopped being a guess: the exact incantation
    /// that renders on 2024-2026 was measured and is documented in the header
    /// of NavisCoordRibbon.xaml.
    ///
    /// Each step calls the SAME <see cref="CoordinationWorkflow"/> service the
    /// HTTP routes call, and only renders the result differently. Nothing about
    /// what a step DOES lives in this file, so a button and a tool call cannot
    /// drift apart.
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

}
