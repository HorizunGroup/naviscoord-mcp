using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// The compiled root-key list against the canonical contract file.
    /// </summary>
    /// <remarks>
    /// <c>ProfileRules.KnownRootKeys</c> is compiled into the DLL because the
    /// add-in cannot read repository files at runtime. That makes it a copy,
    /// and a copy drifts. This suite reads the original —
    /// <c>server/naviscoord/profiles/profile_contract.json</c>, the same file
    /// <c>test_profile_contract.py</c> holds Python to — and fails if the two
    /// sets differ by a single key in either direction. Editing one side
    /// without the other cannot survive both suites.
    /// </remarks>
    internal static class ProfileContractTests
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

            TheCompiledListMatchesTheContract();
            UnknownSectionsAreRefused();
            CommentsAndExtensionsAreNot();
        }

        private static string FindRepoFile(string relative)
        {
            var dir = new System.IO.DirectoryInfo(AppContext.BaseDirectory);
            while (dir != null)
            {
                var candidate = System.IO.Path.Combine(dir.FullName, relative);
                if (System.IO.File.Exists(candidate)) return candidate;
                dir = dir.Parent;
            }
            return null;
        }

        private static HashSet<string> ContractRootKeys()
        {
            var path = FindRepoFile(System.IO.Path.Combine(
                "server", "naviscoord", "profiles", "profile_contract.json"));
            if (path == null) return null;

            var raw = Json.ParseObject(System.IO.File.ReadAllText(path));
            if (raw == null) return null;

            var keys = new HashSet<string>(StringComparer.Ordinal);
            if (raw.TryGetValue("metadata_keys", out var meta) && meta is List<object> metaList)
            {
                foreach (var key in metaList.OfType<string>()) keys.Add(key);
            }
            if (raw.TryGetValue("sections", out var sections)
                && sections is Dictionary<string, object> sectionMap)
            {
                foreach (var key in sectionMap.Keys) keys.Add(key);
            }
            return keys;
        }

        // -------------------------------------------------------------- cases

        private static void TheCompiledListMatchesTheContract()
        {
            _section("contrato: KnownRootKeys es idéntico a profile_contract.json");

            var contract = ContractRootKeys();
            _check(contract != null && contract.Count > 0,
                "se leyó server/naviscoord/profiles/profile_contract.json");
            if (contract == null) return;

            var compiled = ProfileRules.KnownRootKeys;

            var missing = contract.Except(compiled).OrderBy(k => k).ToList();
            _eq(0, missing.Count, "ninguna clave del contrato falta en el DLL" +
                (missing.Count == 0 ? "" : " — faltan: " + string.Join(", ", missing)));

            var extra = compiled.Except(contract).OrderBy(k => k).ToList();
            _eq(0, extra.Count, "el DLL no admite claves que el contrato no declara" +
                (extra.Count == 0 ? "" : " — sobran: " + string.Join(", ", extra)));
        }

        private static void UnknownSectionsAreRefused()
        {
            _section("contrato: una sección desconocida en la raíz se rechaza");

            var raw = new Dictionary<string, object>
            {
                ["$schema"] = "naviscoord.profile/v1",
                ["severty"] = new Dictionary<string, object>()
            };
            var problems = ProfileRules.Validate(raw);

            _check(problems.Any(p => p.Code == ProfileRules.UnknownError
                                     && p.Path == "severty"),
                "«severty» produce profile_unknown con la clave como ruta");
            _check(problems.First(p => p.Code == ProfileRules.UnknownError)
                       .Detail.Contains("extensions"),
                "y el mensaje nombra la vía de escape («extensions»)");
        }

        private static void CommentsAndExtensionsAreNot()
        {
            _section("contrato: comentarios y extensions no cuentan como desconocidas");

            var raw = new Dictionary<string, object>
            {
                ["_comment"] = "nota del autor",
                ["extensions"] = new Dictionary<string, object>
                {
                    // Deliberately hostile content: the free area is free.
                    ["umbral_interno"] = -99.0
                }
            };
            var problems = ProfileRules.Validate(raw);
            _eq(0, problems.Count(p => p.Code == ProfileRules.UnknownError),
                "ni «_comment» ni «extensions» disparan profile_unknown");
        }
    }
}
