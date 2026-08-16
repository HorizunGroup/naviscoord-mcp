using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Turns a workflow result into the prose the ribbon dialog shows.
    /// </summary>
    /// <remarks>
    /// Presentation only, and separate on purpose. The step used to build its
    /// sentences while it worked, which meant the MCP server could not use the
    /// step at all without also parsing Spanish out of a string. Now the step
    /// produces data and this renders it, so a route and a button can disagree
    /// about wording without ever disagreeing about what happened.
    /// </remarks>
    internal static class WorkflowText
    {
        public static string AuditModels(Dictionary<string, object> payload)
        {
            var lines = new List<string>();
            var models = Json.Arr(payload, "models").OfType<Dictionary<string, object>>().ToList();
            if (models.Count == 0) return "No hay modelos anexados.";

            lines.Add(models.Count + " modelos anexados:");
            foreach (var model in models)
            {
                var status = Json.Str(model, "status");
                var distance = Json.Num(model, "nearest_model_m", 0);
                var label = status == "desplazado"
                    ? "⚠ DESPLAZADO ~" + distance.ToString("N0", CultureInfo.InvariantCulture) + " m del resto"
                    : status == "separado (puede ser legítimo para " + Json.Str(model, "discipline") + ")"
                        ? "separado ~" + distance.ToString("N0", CultureInfo.InvariantCulture) + " m " +
                          "(puede ser legítimo para " + Json.Str(model, "discipline") + ")"
                        : status;

                lines.Add("[" + Json.Str(model, "discipline") + "] " +
                          TextLimits.Truncate(Json.Str(model, "name"), 55) + "\n      " +
                          Json.Num(model, "elements", 0).ToString("N0", CultureInfo.InvariantCulture) +
                          " elementos — " + label);
            }

            lines.Add(string.Empty);
            var misplaced = Json.Arr(payload, "misplaced");
            if (misplaced.Count > 0)
            {
                lines.Add("⚠ MODELOS MAL UBICADOS — sus clash tests darán 0 FALSOS:");
                foreach (var entry in misplaced)
                {
                    lines.Add("   • " + Convert.ToString(entry, CultureInfo.InvariantCulture));
                }
                lines.Add("El responsable debe corregir las coordenadas compartidas en su Revit y republicar.");
            }
            else
            {
                lines.Add("✔ Ubicación: todos los modelos comparten volumen.");
            }

            var nameless = Json.Arr(payload, "without_discipline");
            if (nameless.Count > 0)
            {
                lines.Add("⚠ Sin disciplina reconocible en el nombre (nomenclatura BEP): " + nameless.Count);
            }

            lines.Add(string.Empty);
            lines.Add(Purity(payload));
            return string.Join("\n", lines);
        }

        private static string Purity(Dictionary<string, object> payload)
        {
            if (!(payload.TryGetValue("view_purity", out var raw) &&
                  raw is Dictionary<string, object> purity))
            {
                return "Pureza de vistas: no evaluada.";
            }
            if (!Json.Bool(purity, "evaluated", false))
            {
                return "Pureza de vistas: no evaluada (" + Json.Str(purity, "reason") + ").";
            }

            var lines = new List<string> { "— Pureza de las vistas de coordinación —" };
            foreach (var finding in Json.Arr(purity, "findings").OfType<Dictionary<string, object>>())
            {
                var discipline = Json.Str(finding, "discipline");
                if (!Json.Bool(finding, "evaluated", true))
                {
                    lines.Add("[" + discipline + "] no evaluada (" + Json.Str(finding, "note") + ")");
                    continue;
                }

                var intruders = Json.Num(finding, "intruder_elements", 0);
                if (Json.Bool(finding, "dirty", false))
                {
                    var detail = string.Join(", ", Json.Arr(finding, "intruders")
                        .OfType<Dictionary<string, object>>()
                        .Take(4)
                        .Select(i => Json.Str(i, "category") + " " +
                                     Json.Num(i, "elements", 0).ToString("N0", CultureInfo.InvariantCulture) +
                                     " [" + Json.Str(i, "owned_by") + "]"));
                    lines.Add("[" + discipline + "] ⚠ VISTA SUCIA: " +
                              intruders.ToString("N0", CultureInfo.InvariantCulture) +
                              " elementos de otras disciplinas (" + detail + ")");
                }
                else if (intruders > 0)
                {
                    lines.Add("[" + discipline + "] casi limpia (" +
                              intruders.ToString("N0", CultureInfo.InvariantCulture) +
                              " elementos ajenos, tolerado)");
                }
                else
                {
                    lines.Add("[" + discipline + "] ✔ limpia");
                }

                var unclassified = Json.Arr(finding, "unclassified").OfType<Dictionary<string, object>>().ToList();
                if (unclassified.Count > 0)
                {
                    lines.Add("      sin clasificar en el perfil: " + string.Join(", ", unclassified
                        .Take(5)
                        .Select(u => Json.Str(u, "category") + " " +
                                     Json.Num(u, "elements", 0).ToString("N0", CultureInfo.InvariantCulture) +
                                     " (id " + Json.Str(u, "category_id") + ")")));
                }
            }

            var dirty = Json.Num(purity, "dirty_views", 0);
            if (dirty > 0)
            {
                lines.Add("⚠ " + dirty.ToString("N0", CultureInfo.InvariantCulture) +
                          " vista(s) de coordinación mal filtradas: corregir la plantilla de la");
                lines.Add("   VISTA DE COORDINACIÓN en el Revit de esa disciplina (solo su disciplina) y republicar.");
            }
            return string.Join("\n", lines);
        }

        public static string Configure(Dictionary<string, object> payload)
        {
            var lines = new List<string>();
            var profile = Section(payload, "profile");
            foreach (var error in Json.Arr(profile, "errors"))
            {
                lines.Add("⚠ Perfil: " + Convert.ToString(error, CultureInfo.InvariantCulture));
            }
            if (lines.Count > 0)
            {
                lines.Insert(0, "El perfil tiene errores y no se aplicó nada:");
                return string.Join("\n", lines);
            }
            foreach (var warning in Json.Arr(payload, "warnings"))
            {
                lines.Add("ℹ " + Convert.ToString(warning, CultureInfo.InvariantCulture));
            }

            var sets = Section(payload, "sets");
            if (Json.Bool(sets, "present", false))
            {
                var summary = "Sets: " + Json.Num(sets, "folders", 0).ToString("N0", CultureInfo.InvariantCulture) +
                              " carpetas, " + Json.Num(sets, "sets", 0).ToString("N0", CultureInfo.InvariantCulture) +
                              " sets, " + Json.Num(sets, "matches", 0).ToString("N0", CultureInfo.InvariantCulture) +
                              " elementos capturados";
                if (Json.Num(sets, "matches", 0) == 0 && Json.Num(sets, "sets", 0) > 0)
                {
                    summary += "\n⚠ NINGÚN set capturó elementos — revisar el perfil (ids/tab) antes de correr.";
                }
                else
                {
                    var empty = Json.Arr(sets, "empty_sets")
                        .Select(e => Convert.ToString(e, CultureInfo.InvariantCulture)).ToList();
                    if (empty.Count > 0)
                    {
                        // A set at zero with its model present may be
                        // legitimate — that category does not exist in this
                        // project's discipline — or a wrong id in the profile.
                        // Shown either way so it cannot pass unnoticed.
                        summary += "\nSets sin elementos (" + empty.Count + "): " +
                                   string.Join(", ", empty.Take(6));
                        if (empty.Count > 6) summary += " y " + (empty.Count - 6) + " más";
                    }
                }
                lines.Add(summary);
            }
            else
            {
                lines.Add("Sets: sin sets en el perfil");
            }

            var clash = Section(payload, "clash");
            if (Json.Bool(clash, "present", false))
            {
                var summary = "Clash: " + Json.Num(clash, "created", 0).ToString("N0", CultureInfo.InvariantCulture) +
                              " tests creados, " + Json.Num(clash, "kept", 0).ToString("N0", CultureInfo.InvariantCulture) +
                              " conservados (" + Json.Num(clash, "tests_in_document", 0).ToString("N0", CultureInfo.InvariantCulture) +
                              " en el documento)";
                var outdated = Json.Arr(clash, "outdated")
                    .Select(o => Convert.ToString(o, CultureInfo.InvariantCulture)).ToList();
                if (outdated.Count > 0)
                {
                    summary += "\n⚠ Con resultados pero definición desactualizada en el perfil (" +
                               outdated.Count + "): " + string.Join(", ", outdated.Take(5)) +
                               "\n   Para actualizarlos: bórralos en Clash Detective y repite el Paso 1.";
                }
                var skipped = Json.Arr(clash, "skipped")
                    .Select(s => Convert.ToString(s, CultureInfo.InvariantCulture)).ToList();
                if (skipped.Count > 0)
                {
                    summary += "\nOmitidos (sin modelo de esa disciplina): " + string.Join(", ", skipped.Take(6));
                    if (skipped.Count > 6) summary += " y " + (skipped.Count - 6) + " más";
                }
                lines.Add(summary);
            }
            else
            {
                lines.Add("Clash: sin matriz en el perfil");
            }

            lines.Add(Verdict(payload));
            return string.Join("\n", lines);
        }

        public static string RunAll(Dictionary<string, object> payload)
        {
            var tests = Json.Arr(payload, "tests").OfType<Dictionary<string, object>>().ToList();
            var total = Json.Num(payload, "results_total", 0);
            var lines = new List<string>
            {
                tests.Count + " tests corridos, " + total.ToString("N0", CultureInfo.InvariantCulture) + " resultados"
            };

            var stillNew = tests
                .Where(t => string.Equals(Json.Str(t, "status"), "New", StringComparison.OrdinalIgnoreCase))
                .Select(t => Json.Str(t, "name"))
                .ToList();
            if (stillNew.Count > 0)
            {
                lines.Add("⚠ Siguen en 'New' (¿selecciones vacías?): " + string.Join(", ", stillNew.Take(6)));
            }
            lines.Add(Verdict(payload));
            return string.Join("\n", lines);
        }

        public static string GroupByLevel(Dictionary<string, object> payload)
        {
            var lines = new List<string>();
            var tests = Json.Arr(payload, "tests").OfType<Dictionary<string, object>>().ToList();
            if (tests.Count == 0)
            {
                lines.Add("Nada nuevo que agrupar (sin resultados sueltos).");
            }
            else
            {
                foreach (var test in tests)
                {
                    lines.Add(Json.Str(test, "test") + ": " +
                              Json.Num(test, "levels", 0).ToString("N0", CultureInfo.InvariantCulture) + " niveles, " +
                              Json.Num(test, "moved", 0).ToString("N0", CultureInfo.InvariantCulture) + " choques agrupados");
                }
                lines.Add(string.Empty);
                lines.Add("Total: " + Json.Num(payload, "groups_created", 0).ToString("N0", CultureInfo.InvariantCulture) +
                          " grupos de nivel, " + Json.Num(payload, "applied", 0).ToString("N0", CultureInfo.InvariantCulture) +
                          " choques movidos, " + Json.Num(payload, "verified", 0).ToString("N0", CultureInfo.InvariantCulture) +
                          " verificados en el documento.");
            }

            var rename = Section(payload, "rename");
            var renamed = Json.Num(rename, "renamed", 0);
            var stillDefault = Json.Num(rename, "still_default", 0);
            if (renamed == 0)
            {
                lines.Add("Nombres de choques: sin cambios (ya personalizados o sin nombres por defecto).");
            }
            else if (stillDefault == 0)
            {
                lines.Add(renamed.ToString("N0", CultureInfo.InvariantCulture) +
                          " choques renombrados a \"NNN · elemento vs elemento\" (verificado releyendo).");
            }
            else
            {
                lines.Add("⚠ Renombrado parcial: " + renamed.ToString("N0", CultureInfo.InvariantCulture) +
                          " aplicados, " + stillDefault.ToString("N0", CultureInfo.InvariantCulture) +
                          " siguen como Clash/Conflicto N.");
            }

            lines.Add(string.Empty);
            lines.Add(Repetition(Section(payload, "repetition")));
            lines.Add(string.Empty);
            lines.Add("💾 GUARDA AHORA. Sin guardar se pierde TODO esto —");
            lines.Add("   sets, tests, resultados, grupos y nombres — al cerrar Navisworks.");
            lines.Add("   Si el archivo viene de ACC (.nwfacc): Archivo → Guardar como → .nwf local.");
            return string.Join("\n", lines);
        }

        private static string Repetition(Dictionary<string, object> repetition)
        {
            var lines = new List<string>();
            var series = Json.Arr(repetition, "top").OfType<Dictionary<string, object>>().ToList();
            var count = (int)Json.Num(repetition, "repeated_series", 0);

            if (count > 0)
            {
                lines.Add("⚠ " + count + " interferencias-TIPO repetidas en 3+ niveles " +
                          "(misma solución en serie → UNA incidencia ACC por serie):");
                foreach (var entry in series.Take(8))
                {
                    lines.Add("   • " + Json.Str(entry, "test") + ": " +
                              TextLimits.Truncate(Json.Str(entry, "element_a"), 30) + " vs " +
                              TextLimits.Truncate(Json.Str(entry, "element_b"), 30) + " — " +
                              Json.Num(entry, "level_count", 0).ToString("N0", CultureInfo.InvariantCulture) + " niveles");
                }
                if (count > 8) lines.Add("   … y " + (count - 8) + " más");
                var csv = Json.Str(repetition, "csv");
                lines.Add("Detalle con niveles y GUIDs: " +
                          (csv.Length > 0 ? csv : "(no se pudo escribir el CSV)"));
            }
            else
            {
                lines.Add("Sin series repetidas entre niveles (ninguna interferencia-tipo en 3+ pisos).");
            }

            var fragments = Json.Num(repetition, "fragments", 0);
            if (fragments > 0)
            {
                lines.Add("ℹ " + fragments.ToString("N0", CultureInfo.InvariantCulture) +
                          " posibles fragmentos (mismo par partido en varias entradas dentro de un nivel).");
            }
            return string.Join("\n", lines);
        }

        public static string Rules(Dictionary<string, object> payload)
        {
            var note = Json.Str(payload, "note");
            if (!string.IsNullOrEmpty(note)) return note;
            var detail = Section(payload, "rules");
            var verified = Json.Num(payload, "verified", 0);
            var requested = Json.Num(payload, "requested", 0);
            var status = Json.Str(payload, "status_applied", "Approved");
            var total = Json.Arr(detail, "tests").OfType<Dictionary<string, object>>()
                .Sum(t => Json.Num(t, "results_total", 0));

            return total == 0
                ? "sin resultados todavía — corre los tests (paso 2) y aplica las reglas después"
                : verified.ToString("N0", CultureInfo.InvariantCulture) + " choques marcados " + status +
                  " y verificados (coincidieron " + requested.ToString("N0", CultureInfo.InvariantCulture) +
                  " de " + total.ToString("N0", CultureInfo.InvariantCulture) + " resultados)";
        }

        /// <summary>
        /// The one line that says whether the step actually did what it said.
        /// </summary>
        private static string Verdict(Dictionary<string, object> payload)
        {
            var status = Json.Str(payload, "status");
            var errors = Json.Arr(payload, "errors")
                .Select(e => Convert.ToString(e, CultureInfo.InvariantCulture)).ToList();
            switch (status)
            {
                case "completed":
                    return "✔ Verificado releyendo el documento.";
                case "partial":
                    return "⚠ Parcial: " + Json.Num(payload, "verified", 0).ToString("N0", CultureInfo.InvariantCulture) +
                           " verificados de " + Json.Num(payload, "applied", 0).ToString("N0", CultureInfo.InvariantCulture) +
                           " aplicados." + (errors.Count > 0 ? " " + errors[0] : string.Empty);
                case "failed":
                    return "✖ Falló: " + (Causes(payload, errors) ?? "sin detalle.");
                case "planned":
                    return "(ensayo: no se tocó el documento)";
                default:
                    return string.Empty;
            }
        }

        /// <summary>
        /// Why the step failed, in the caller's own words, or null if it
        /// really did not say.
        /// </summary>
        /// <remarks>
        /// A step can fail with `errors` empty and the reason sitting in
        /// `warnings`: `workflow/run` reports a clash test that stayed in
        /// «New» —empty selections, so Navisworks never ran it— as a warning
        /// and then fails, correctly, rather than reporting a green run of
        /// zero results. Reading only `errors` printed «Falló: sin detalle»
        /// two lines under the sentence that gave the detail.
        /// </remarks>
        private static string Causes(Dictionary<string, object> payload, List<string> errors)
        {
            var causes = errors.Where(e => !string.IsNullOrWhiteSpace(e)).ToList();
            if (causes.Count == 0)
            {
                causes = Json.Arr(payload, "warnings")
                    .Select(w => Convert.ToString(w, CultureInfo.InvariantCulture))
                    .Where(w => !string.IsNullOrWhiteSpace(w))
                    .ToList();
            }
            return causes.Count > 0 ? string.Join(" ", causes.Take(3)) : null;
        }

        private static Dictionary<string, object> Section(Dictionary<string, object> payload, string key)
            => payload != null && payload.TryGetValue(key, out var raw) && raw is Dictionary<string, object> map
                ? map
                : new Dictionary<string, object>();
    }
}
