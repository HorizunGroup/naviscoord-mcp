using System;
using System.Collections.Generic;
using System.IO;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Plugins;

namespace NavisCoord
{
    /// <summary>
    /// "Configurar coordinación" (Tool add-ins): build the search sets and the
    /// clash matrix, then apply any residual rules.
    /// </summary>
    /// <remarks>
    /// The plugin owns three things and nothing else: finding the profile,
    /// calling <see cref="CoordinationWorkflow"/>, and showing what came back.
    /// The work itself lives in the workflow service so the HTTP routes run
    /// exactly the same code — a button and a tool call cannot disagree about
    /// what a step does if there is only one of it.
    ///
    /// Policy lives in a JSON profile the coordinator edits, not in this
    /// assembly: discipline codes, search sets, clash pairs and ignore rules
    /// are all read from it, so adapting NavisCoord to a project is editing
    /// one file rather than recompiling. See <c>docs/PROFILES.md</c>.
    ///
    /// Unlike the bridge, this code DOES raise dialogs: it only ever runs
    /// because somebody just clicked, so there is a human present to read them
    /// and no headless flow a modal box could hang.
    /// </remarks>
    [Plugin("NavisCoord.Configure", "NVCD",
        DisplayName = "Configurar coordinación",
        ToolTip = "Crea los search sets por disciplina y la matriz de clash definidos en el perfil")]
    public sealed class ConfigurePlugin : AddInPlugin
    {
        public override int Execute(params string[] parameters)
        {
            try
            {
                var doc = Autodesk.Navisworks.Api.Application.ActiveDocument;
                if (doc == null || doc.IsClear)
                {
                    return Fail("Abre primero el archivo de coordinación con los modelos anexados.");
                }

                // Through the same store the MCP tools use, so the button and
                // navis_load_profile cannot end up applying different criteria.
                var profile = ProfileStore.Active();
                if (profile == null) return Fail(ProfileLocator.NotFoundMessage());

                var configured = CoordinationWorkflow.Configure(doc, profile);
                var rules = CoordinationWorkflow.ApplyRules(doc, profile);

                var message = "Configuración aplicada desde:\n" + Origin(profile) +
                              "\n\n" + WorkflowText.Configure(configured) +
                              "\nReglas: " + WorkflowText.Rules(rules);
                BridgeHost.Log(message.Replace("\n", " | "));
                Inform(message);
                return 0;
            }
            catch (Exception ex)
            {
                return Fail("Configurar coordinación falló: " + ex.Message);
            }
        }

        /// <summary>
        /// Where the profile came from, in one line a coordinator can read.
        /// </summary>
        /// <remarks>
        /// A pushed profile has no path, and the dialog has to say so rather
        /// than print an empty line: "which file did this come from" is the
        /// first thing asked when the result looks wrong.
        /// </remarks>
        internal static string Origin(ProfileStore.ActiveProfile profile)
        {
            if (profile == null) return "(sin perfil)";
            var identity = profile.Name + " · " + profile.Checksum;
            return string.IsNullOrEmpty(profile.Path)
                ? identity + " (enviado por MCP, no está en disco)"
                : identity + "\n" + profile.Path;
        }

        // ----------------------------------------------------- shared bits

        /// <summary>
        /// Model names from ACC arrive URL-encoded: an Ñ shows up as "%C3%91"
        /// in the tree (observed live on a real ACC federation, 2026-08-12).
        /// Decoded both for display and for discipline-code matching.
        /// </summary>
        internal static string DecodeName(string name)
        {
            if (string.IsNullOrEmpty(name) || name.IndexOf('%') < 0) return name ?? string.Empty;
            try { return Uri.UnescapeDataString(name); }
            catch { return name; }
        }

        internal static int Fail(string message)
        {
            BridgeHost.Log(message.Replace("\n", " "));
            ResultsDialog.Show("Algo no salió bien", message, isError: true);
            return 1;
        }

        internal static void Inform(string message)
        {
            // The first line of the message is the dialog heading.
            var parts = message.Replace("\r\n", "\n").Split(new[] { '\n' }, 2);
            var heading = parts[0].Trim();
            var body = parts.Length > 1 ? parts[1].TrimStart('\n') : string.Empty;
            ResultsDialog.Show(heading, body, isError: false);
        }
    }

}
