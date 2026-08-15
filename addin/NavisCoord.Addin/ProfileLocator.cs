using System;
using System.IO;

namespace NavisCoord
{
    /// <summary>
    /// Where the on-disk default profile lives, and how to say it is missing.
    /// </summary>
    /// <remarks>
    /// Three places, most specific first:
    ///
    /// 1. <c>%NAVISCOORD_PROFILE%</c> — an explicit file, for a machine that
    ///    coordinates several projects with different criteria;
    /// 2. <c>%LOCALAPPDATA%\NavisCoord\naviscoord-profile.json</c> — the
    ///    per-user copy;
    /// 3. beside the plugin DLL — the copy an installer deploys for everyone.
    ///
    /// This is the DEFAULT only. A profile pushed by the MCP server never
    /// touches the disk and never comes through here; see
    /// <see cref="ProfileStore"/> for which of the two wins.
    ///
    /// In its own file, free of any Navisworks reference, so the resolution
    /// order is unit-testable off a licensed machine — it used to sit beside
    /// a ribbon button and could only be exercised by clicking it.
    /// </remarks>
    internal static class ProfileLocator
    {
        public const string FileName = "naviscoord-profile.json";
        public const string EnvVar = "NAVISCOORD_PROFILE";

        public static string Find()
        {
            var configured = Environment.GetEnvironmentVariable(EnvVar);
            if (!string.IsNullOrWhiteSpace(configured) && File.Exists(configured.Trim('"')))
            {
                return configured.Trim('"');
            }

            var local = Path.Combine(SessionStore.Root(), FileName);
            if (File.Exists(local)) return local;

            var beside = Path.Combine(
                Path.GetDirectoryName(typeof(ProfileLocator).Assembly.Location) ?? string.Empty,
                FileName);
            return File.Exists(beside) ? beside : null;
        }

        public static string NotFoundMessage()
            => "No encontré " + FileName + ".\n" +
               "Cárgalo por MCP con navis_load_profile, cópialo a " + SessionStore.Root() +
               "\\, ponlo junto al DLL del complemento, o apunta " + EnvVar +
               " a un perfil concreto.\n" +
               "Hay un ejemplo neutral en profiles/example-profile.json del repositorio.";
    }
}
