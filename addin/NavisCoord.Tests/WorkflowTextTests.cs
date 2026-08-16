using System;
using System.Collections.Generic;

namespace NavisCoord.Tests
{
    /// <summary>
    /// The verdict line: whether a step that failed says why.
    /// </summary>
    /// <remarks>
    /// Found in the live validation of 0.3.1 (2026-08-16). A clash test whose
    /// selections are empty never runs: Navisworks leaves it in «New», and
    /// `workflow/run` fails instead of reporting a green run of zero results
    /// — which is the behaviour we want. But the reason travelled in
    /// `warnings`, the verdict read only `errors`, and the dialog closed with
    /// «Falló: sin detalle» directly under the sentence that gave the detail.
    ///
    /// The rendering is what a coordinator reads before deciding whether to
    /// trust a run, so a verdict that shrugs is a verdict that gets ignored.
    /// </remarks>
    internal static class WorkflowTextTests
    {
        public static void Run(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check)
        {
            section("veredicto: un fallo nombra su causa");

            var soloAdvertencia = Payload(
                status: "failed",
                errors: new object[0],
                warnings: new object[]
                {
                    "1 test(s) siguen en estado New tras la corrida: revisa que sus selecciones no estén vacías."
                });

            var texto = WorkflowText.RunAll(soloAdvertencia);
            check(!texto.Contains("sin detalle"),
                "un fallo con la causa en warnings ya no se cierra con «sin detalle»");
            check(texto.Contains("siguen en estado New"),
                "el veredicto repite la causa que traía la advertencia");

            section("veredicto: errors manda sobre warnings");

            var ambos = Payload(
                status: "failed",
                errors: new object[] { "El documento tiene cambios sin guardar." },
                warnings: new object[] { "una advertencia menos importante" });
            var textoAmbos = WorkflowText.RunAll(ambos);
            check(textoAmbos.Contains("cambios sin guardar"),
                "cuando hay errors, son ellos los que se muestran");
            check(!textoAmbos.Contains("menos importante"),
                "la advertencia no desplaza al error");

            section("veredicto: cuando de verdad no se dijo nada");

            var mudo = Payload(status: "failed", errors: new object[0], warnings: new object[0]);
            check(WorkflowText.RunAll(mudo).Contains("sin detalle"),
                "sin errors ni warnings se sigue admitiendo que no hay detalle");

            var vacios = Payload(
                status: "failed",
                errors: new object[] { "", "   " },
                warnings: new object[] { "   " });
            check(WorkflowText.RunAll(vacios).Contains("sin detalle"),
                "cadenas en blanco no cuentan como detalle");

            section("veredicto: los demás estados no cambian");

            var completado = Payload(status: "completed", errors: new object[0], warnings: new object[]
            {
                "una advertencia que no convierte el éxito en fallo"
            });
            check(WorkflowText.RunAll(completado).Contains("✔ Verificado releyendo el documento."),
                "un paso completado se sigue anunciando verificado, con advertencias o sin ellas");
            check(!WorkflowText.RunAll(completado).Contains("Falló"),
                "una advertencia sobre un paso completado no lo declara fallido");
        }

        /// <summary>
        /// The shape `workflow/run` returns, reduced to what the verdict reads.
        /// </summary>
        private static Dictionary<string, object> Payload(
            string status, object[] errors, object[] warnings)
            => new Dictionary<string, object>
            {
                ["status"] = status,
                ["errors"] = new List<object>(errors),
                ["warnings"] = new List<object>(warnings),
                ["results_total"] = 0d,
                ["tests"] = new List<object>
                {
                    new Dictionary<string, object>
                    {
                        ["name"] = "Test 1",
                        ["status"] = "New",
                        ["results_before"] = 0d,
                        ["results_after"] = 0d,
                    },
                },
            };
    }
}
