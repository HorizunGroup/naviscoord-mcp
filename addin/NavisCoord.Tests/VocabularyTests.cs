using System;
using System.Collections.Generic;

namespace NavisCoord.Tests
{
    /// <summary>
    /// The discipline vocabulary: codes read from the profile, never compiled
    /// in.
    /// </summary>
    /// <remarks>
    /// This replaces a hard-coded list of one organisation's BEP codes, so the
    /// cases that matter are the ones that prove nothing is baked in any more:
    /// an empty profile recognises nothing, a profile's own codes are the ones
    /// matched, and both aliasing and the "far away on purpose" exemption come
    /// from the file rather than from this assembly.
    /// </remarks>
    internal static class VocabularyTests
    {
        public static void Run(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check)
        {
            section("vocabulario: sin perfil no se reconoce ninguna disciplina");

            eq(DisciplineVocabulary.Unknown,
                DisciplineVocabulary.Empty.TokenIn("TORRE-ARQ-NIVEL3"),
                "sin perfil un nombre no se asigna a ninguna disciplina — no se adivina");
            check(DisciplineVocabulary.Empty.IsEmpty, "el vocabulario vacío se declara vacío");
            check(!DisciplineVocabulary.Empty.IsFreestanding("SITE"),
                "sin perfil nada está exento del audit de co-ubicación");

            section("vocabulario: los códigos salen de las carpetas del perfil");

            var profile = Parse(@"{
              ""sets"": { ""folders"": [
                 { ""folder"": ""ARC"", ""sets"": [] },
                 { ""folder"": ""STR"", ""sets"": [] },
                 { ""folder"": ""MEC"", ""sets"": [] },
                 { ""folder"": ""SITE"", ""sets"": [] } ] },
              ""discipline_aliases"": { ""HVA"": ""MEC"" },
              ""freestanding_disciplines"": [""SITE""]
            }");
            var vocabulary = DisciplineVocabulary.FromProfile(profile);

            eq("ARC", vocabulary.TokenIn("PROY-ARC-TORRE1.rvt"), "reconoce un código del perfil");
            eq("MEC", vocabulary.TokenIn("PROY-MEC-TORRE1.rvt"), "…y otro");
            eq(DisciplineVocabulary.Unknown, vocabulary.TokenIn("PROY-XYZ-TORRE1.rvt"),
                "un código que el perfil no declara no se inventa");
            eq(DisciplineVocabulary.Unknown, vocabulary.TokenIn("TORRE-NORTE-1.rvt"),
                "el código se busca como -CODE-, así que no se encuentra dentro de otra palabra");

            section("vocabulario: alias y disciplinas exentas vienen del archivo");

            eq("MEC", vocabulary.Canonical("HVA"), "un alias se resuelve a su forma canónica");
            eq("ARC", vocabulary.Canonical("ARC"), "un código sin alias es su propia forma canónica");
            eq("HVA", vocabulary.TokenIn("PROY-HVA-TORRE1.rvt"),
                "un alias se reconoce en el nombre tal como está escrito…");
            eq("MEC", vocabulary.Canonical(vocabulary.TokenIn("PROY-HVA-TORRE1.rvt")),
                "…y es Canonical quien lo lleva a la disciplina que manda");
            check(vocabulary.IsFreestanding("SITE"),
                "una disciplina declarada como exenta puede estar lejos sin ser un fallo");
            check(!vocabulary.IsFreestanding("ARC"),
                "…y una que no lo está sigue siendo un desplazamiento real");

            section("vocabulario: un perfil ilegible degrada a vacío, no revienta");

            eq(DisciplineVocabulary.Unknown,
                DisciplineVocabulary.FromProfilePath(@"C:\no\existe\perfil.json").TokenIn("A-ARC-B"),
                "un perfil inexistente da el vocabulario vacío en vez de una excepción");
            check(DisciplineVocabulary.FromProfile(null).IsEmpty,
                "un perfil nulo también");

            ShippedExampleProfile(section, eq, check);
            ShippedProfileChecksums(section, eq, check);
        }

        /// <summary>
        /// Both PUBLISHED profiles, hashed here and pinned to the value Python
        /// computes for the same file.
        /// </summary>
        /// <remarks>
        /// The canonical-form vector already proves the two implementations
        /// agree on a synthetic fixture. It does not prove they agree on the
        /// files that actually ship, and those are the ones whose checksum a
        /// report is traced back to: `default.json` travels inside the wheel,
        /// `example-profile.json` inside every add-in ZIP. Editing either one
        /// changes its identity, so the literal below has to be regenerated
        /// deliberately — from Python, never adjusted until it matches.
        ///
        ///     python scripts/profile_checksums.py
        ///
        /// Both files declare `naviscoord.profile/v1`. `default.json` carried
        /// the pre-versioning tag: accepted, but it would mean the
        /// profile the product ships was the one example of the format it was
        /// asking everyone else to stop using.
        /// </remarks>
        private static void ShippedProfileChecksums(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check)
        {
            section("perfiles publicados: mismo checksum en C# que en Python");

            var pinned = new[]
            {
                new { Rel = System.IO.Path.Combine("server", "naviscoord", "profiles", "default.json"),
                      Sum = "fa674d0e2271b755" },
                new { Rel = System.IO.Path.Combine("profiles", "example-profile.json"),
                      Sum = "9006367f436753bc" },
            };

            foreach (var item in pinned)
            {
                var path = FindRepoFile(item.Rel);
                if (path == null)
                {
                    check(false, "no se encontró " + item.Rel);
                    continue;
                }

                var profile = Json.ParseObject(System.IO.File.ReadAllText(path));
                var report = ProfileSchema.Validate(profile);

                eq(ProfileSchemaVersion.Current, report.Version,
                    item.Rel + " declara el esquema vigente");
                check(report.Ok, report.Ok
                    ? item.Rel + " valida sin errores"
                    : item.Rel + " NO valida: " + string.Join(" | ", report.Errors));
                eq(0, report.Warnings.Count, report.Warnings.Count == 0
                    ? "…y sin advertencias"
                    : "…pero deja advertencias: " + string.Join(" | ", report.Warnings));
                eq(item.Sum, ProfileSchema.Checksum(profile),
                    item.Rel + ": el checksum coincide con el que calcula Python");
            }
        }

        /// <summary>
        /// The example profile in <c>profiles/</c> must actually validate.
        /// </summary>
        /// <remarks>
        /// It is the first thing a new user copies, and a shipped example that
        /// the product's own validator rejects is worse than no example: the
        /// first thing they see is an error against a file they did not write.
        /// Checked against <see cref="ProfileSchema"/> itself, not against a
        /// second opinion about what the schema says.
        /// </remarks>
        private static void ShippedExampleProfile(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check)
        {
            section("perfil de ejemplo: el que se publica valida contra el esquema");

            var path = FindRepoFile(System.IO.Path.Combine("profiles", "example-profile.json"));
            if (path == null)
            {
                check(false, "no se encontró profiles/example-profile.json desde " +
                             AppDomain.CurrentDomain.BaseDirectory);
                return;
            }

            var profile = Json.ParseObject(System.IO.File.ReadAllText(path));
            check(profile.Count > 0, "el perfil de ejemplo es JSON legible");

            var report = ProfileSchema.Validate(profile);
            check(report.Ok, report.Ok
                ? "el perfil de ejemplo valida sin errores"
                : "el perfil de ejemplo NO valida: " + string.Join(" | ", report.Errors));
            eq(0, report.Warnings.Count, report.Warnings.Count == 0
                ? "…y sin advertencias"
                : "…pero deja advertencias: " + string.Join(" | ", report.Warnings));

            var vocabulary = DisciplineVocabulary.FromProfile(profile);
            check(!vocabulary.IsEmpty, "el perfil de ejemplo define un vocabulario de disciplinas");
            check(vocabulary.Tokens.Count >= 4,
                "…con varias disciplinas (" + vocabulary.Tokens.Count + ")");
        }

        /// <summary>Walks up from the test binary to the repository root.</summary>
        private static string FindRepoFile(string relative)
        {
            var dir = new System.IO.DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            for (var hops = 0; dir != null && hops < 10; hops++, dir = dir.Parent)
            {
                var candidate = System.IO.Path.Combine(dir.FullName, relative);
                if (System.IO.File.Exists(candidate)) return candidate;
            }
            return null;
        }

        private static Dictionary<string, object> Parse(string json)
            => Json.ParseObject(json);
    }
}
