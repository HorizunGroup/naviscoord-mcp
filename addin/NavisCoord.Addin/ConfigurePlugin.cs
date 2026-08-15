using System;
using System.Collections.Generic;
using System.IO;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Plugins;

namespace NavisCoord
{
    /// <summary>
    /// Diálogos y localización del perfil, compartidos por lo que necesite
    /// hablarle a la persona que está delante.
    /// </summary>
    /// <remarks>
    /// Fue el botón "Configurar coordinación" hasta que la cinta se quedó solo
    /// con el estado del puente: el paso duplicaba <c>navis_configure</c>, que
    /// hace lo mismo desde el cliente MCP. Lo que ejecutaba vive intacto en
    /// <see cref="CoordinationWorkflow"/>, al que llaman las rutas HTTP; de
    /// esta clase sobreviven los helpers, que es lo que de verdad usaban los
    /// demás.
    ///
    /// La política vive en un perfil JSON que edita el coordinador, no en este
    /// ensamblado: códigos de disciplina, search sets, pares de clash y reglas
    /// de exclusión se leen de ahí, así que adaptar NavisCoord a un proyecto es
    /// editar un archivo y no recompilar. Ver <c>docs/PROFILES.md</c>.
    ///
    /// A diferencia del puente, este código SÍ levanta diálogos: solo corre
    /// porque alguien acaba de hacer clic, así que hay un humano presente para
    /// leerlos y no hay flujo desatendido que un modal pueda colgar.
    /// </remarks>
    public sealed class ConfigurePlugin
    {
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
