using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// A save is verified by what it left behind, and "nothing to do" is not a
    /// failure.
    /// </summary>
    /// <remarks>
    /// Two reproductions, pulling in opposite directions.
    ///
    /// The old check computed <c>document_points_at_destination</c> and never
    /// looked at it. A Save As that wrote the file but left the document
    /// pointing at the ORIGINAL path reported success — so the operator
    /// believed they had moved, kept editing, and every later save went to the
    /// old file. That is the shape of losing an afternoon's work while being
    /// told everything is fine.
    ///
    /// And it treated an unchanged timestamp-and-size as proof of a silent
    /// no-op. On an already-clean document a save has nothing to write, so a
    /// correct outcome was reported as a failure.
    /// </remarks>
    internal static class SaveVerificationTests
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

            TheSaveAsReproduction();
            TheCleanSaveReproduction();
            AModifiedSaveSucceeds();
            AFileThatIsNotThere();
            AnEmptyFile();
            TheCallReturnedFalse();
            StillModifiedAfterwards();
            AnInterruptedSaveIsIndeterminate();
            PathsAreComparedNormalised();
            SizeAndTimestampDoNotOverrideIdentity();
            APlainSaveMustNotMoveTheDocument();
            TheEnvelopeCarriesTheEvidence();
        }

        private static readonly DateTime T0 = new DateTime(2026, 8, 19, 10, 0, 0, DateTimeKind.Utc);

        /// <summary>A healthy Save As of a modified document.</summary>
        private static SaveVerification.Observation SaveAs(
            string destination = @"C:\obra\torre-v2.nwd",
            string pathAfter = @"C:\obra\torre-v2.nwd")
            => new SaveVerification.Observation
            {
                IsSaveAs = true,
                PathBefore = @"C:\obra\torre.nwd",
                ModifiedBefore = true,
                FingerprintBefore = "fp-1",
                PathAfter = pathAfter,
                ModifiedAfter = false,
                FingerprintAfter = "fp-2",
                Destination = destination,
                ExistedBefore = false,
                ExistsAfter = true,
                LengthAfter = 4096,
                LastWriteAfter = T0.AddSeconds(5),
                CallSucceeded = true
            };

        /// <summary>A plain Save of a document that is already clean.</summary>
        private static SaveVerification.Observation CleanSave()
            => new SaveVerification.Observation
            {
                IsSaveAs = false,
                PathBefore = @"C:\obra\torre.nwd",
                ModifiedBefore = false,
                FingerprintBefore = "fp-1",
                PathAfter = @"C:\obra\torre.nwd",
                ModifiedAfter = false,
                FingerprintAfter = "fp-1",
                Destination = @"C:\obra\torre.nwd",
                ExistedBefore = true,
                LengthBefore = 4096,
                LastWriteBefore = T0,
                ExistsAfter = true,
                LengthAfter = 4096,      // unchanged, because nothing was written
                LastWriteAfter = T0,     // and the clock did not move either
                CallSucceeded = true
            };

        // -------------------------------------------------- reproductions

        private static void TheSaveAsReproduction()
        {
            _section("save: el archivo aparece pero el documento apunta a otro sitio");

            // The exact case: Save As wrote C:\obra\torre-v2.nwd, and the
            // document is still C:\obra\torre.nwd.
            var seen = SaveAs(pathAfter: @"C:\obra\torre.nwd");
            var verdict = SaveVerification.Judge(seen);

            _check(!verdict.PointsAtDestination, "el documento NO apunta al destino");
            _eq(SaveVerification.StatusFailed, verdict.Status,
                "y eso basta para que no sea completed, aunque el archivo exista");
            _eq(0, verdict.Verified, "nada se da por verificado");
            _check(verdict.Problems.Any(p =>
                    p.IndexOf("se guardaría en el archivo anterior", StringComparison.Ordinal) >= 0),
                "y se explica la consecuencia: lo que edites iría al archivo viejo");
        }

        private static void TheCleanSaveReproduction()
        {
            _section("save: un documento ya limpio no es un fallo");

            var verdict = SaveVerification.Judge(CleanSave());

            _eq(SaveVerification.StatusCompleted, verdict.Status, "es un éxito");
            _eq(SaveVerification.KindAlreadySaved, verdict.Kind, "de tipo already_saved");
            _eq(0, verdict.Applied, "no se aplicó nada, porque no había nada que escribir");
            _eq(1, verdict.Verified, "pero el archivo se verificó");
            _check(!verdict.FileChanged, "el archivo no cambió, que es lo correcto aquí");
            _check(verdict.Problems.Count == 0,
                "y no se inventa un problema con la marca de tiempo");
            _check(verdict.Notes.Any(n => n.IndexOf("ya estaba guardado", StringComparison.Ordinal) >= 0),
                "se dice por qué no se escribió nada");
        }

        // --------------------------------------------------- healthy paths

        private static void AModifiedSaveSucceeds()
        {
            _section("save: un documento modificado que se guarda de verdad");

            var seen = CleanSave();
            seen.ModifiedBefore = true;
            seen.LengthAfter = 5120;
            seen.LastWriteAfter = T0.AddSeconds(10);

            var verdict = SaveVerification.Judge(seen);
            _eq(SaveVerification.StatusCompleted, verdict.Status, "completed");
            _eq(SaveVerification.KindSave, verdict.Kind, "de tipo save, no already_saved");
            _eq(1, verdict.Applied, "aplicado");
            _eq(1, verdict.Verified, "y verificado");

            var asVerdict = SaveVerification.Judge(SaveAs());
            _eq(SaveVerification.StatusCompleted, asVerdict.Status, "un Save As sano también");
            _eq(SaveVerification.KindSaveAs, asVerdict.Kind, "de tipo save_as");
            _check(asVerdict.PointsAtDestination, "con el documento apuntando al destino");
        }

        // ------------------------------------------------------- failures

        private static void AFileThatIsNotThere()
        {
            _section("save: si el archivo no está, no hay guardado");

            var seen = SaveAs();
            seen.ExistsAfter = false;
            var verdict = SaveVerification.Judge(seen);
            _eq(SaveVerification.StatusFailed, verdict.Status, "failed");
            _check(verdict.Problems.Any(p => p.IndexOf("no existe", StringComparison.Ordinal) >= 0),
                "y se dice que no existe");
        }

        private static void AnEmptyFile()
        {
            _section("save: un archivo de cero bytes no es un guardado");

            var seen = SaveAs();
            seen.LengthAfter = 0;
            var verdict = SaveVerification.Judge(seen);
            _eq(SaveVerification.StatusFailed, verdict.Status, "failed");
            _check(verdict.Problems.Any(p => p.IndexOf("0 bytes", StringComparison.Ordinal) >= 0),
                "nombrando el tamaño");
        }

        private static void TheCallReturnedFalse()
        {
            _section("save: si la API devuelve false, no se verifica nada más");

            var seen = SaveAs();
            seen.CallSucceeded = false;
            var verdict = SaveVerification.Judge(seen);
            _eq(SaveVerification.StatusFailed, verdict.Status, "failed");
            _eq(0, verdict.Applied, "sin aplicar");
            _eq(0, verdict.Verified, "sin verificar");
        }

        private static void StillModifiedAfterwards()
        {
            _section("save: si el documento sigue sucio, el guardado no cubrió todo");

            var seen = SaveAs();
            seen.ModifiedAfter = true;
            var verdict = SaveVerification.Judge(seen);
            _eq(SaveVerification.StatusFailed, verdict.Status, "failed");
            _check(verdict.Problems.Any(p =>
                    p.IndexOf("sigue marcando el documento como modificado",
                        StringComparison.Ordinal) >= 0),
                "y se dice cuál es la señal");
        }

        private static void AnInterruptedSaveIsIndeterminate()
        {
            _section("save: una excepción tras empezar a escribir es indeterminado");

            var verdict = SaveVerification.Judge(new SaveVerification.Observation
            {
                IsSaveAs = true,
                Destination = @"C:\obra\torre-v2.nwd",
                Interrupted = "el disco se llenó"
            });

            _eq(SaveVerification.StatusFailed, verdict.Status, "nunca completed");
            _check(verdict.Problems.Any(p =>
                    p.IndexOf("indeterminado", StringComparison.Ordinal) >= 0),
                "se declara indeterminado en vez de elegir un desenlace");
            _eq(0, verdict.Verified, "y no se verifica nada");
        }

        // ------------------------------------------------ path normalisation

        private static void PathsAreComparedNormalised()
        {
            _section("save: las rutas se comparan normalizadas, no como texto crudo");

            _check(SaveVerification.SamePath(@"C:\Obra\Torre.nwd", @"c:\obra\torre.nwd"),
                "Windows no distingue mayúsculas");
            _check(SaveVerification.SamePath(@"C:\obra/torre.nwd", @"C:\obra\torre.nwd"),
                "ni el separador");
            _check(SaveVerification.SamePath(@"C:\obra\torre.nwd\", @"C:\obra\torre.nwd"),
                "ni un separador final");
            _check(SaveVerification.SamePath(@"\\?\C:\obra\torre.nwd", @"C:\obra\torre.nwd"),
                "el prefijo de ruta larga es ortografía, no ubicación");
            _check(SaveVerification.SamePath(@"C:\obra\diseño ñandú.nwd", @"C:\obra\diseño ñandú.nwd"),
                "y el Unicode se compara normalizado");

            // What must NOT be normalised away.
            _check(!SaveVerification.SamePath(@"C:\obra\torre.nwd", @"C:\obra\torre-v2.nwd"),
                "un nombre distinto sigue siendo otro archivo");
            _check(!SaveVerification.SamePath(@"C:\obra\torre.nwd", @"D:\obra\torre.nwd"),
                "y otra unidad también");
            _check(!SaveVerification.SamePath("", @"C:\obra\torre.nwd"),
                "un documento sin ruta no apunta a ningún destino");

            // A Save As into a differently-cased path is a success, not a
            // refusal: comparing raw strings would have failed it.
            var verdict = SaveVerification.Judge(
                SaveAs(destination: @"C:\Obra\Torre-V2.nwd", pathAfter: @"c:\obra\torre-v2.nwd"));
            _eq(SaveVerification.StatusCompleted, verdict.Status,
                "distinta caja no invalida un Save As correcto");
        }

        private static void SizeAndTimestampDoNotOverrideIdentity()
        {
            _section("save: un cambio de tamaño no compensa un FileName incorrecto");

            var seen = SaveAs(pathAfter: @"C:\obra\torre.nwd");
            seen.ExistedBefore = true;
            seen.LengthBefore = 1024;
            seen.LengthAfter = 999999;          // el archivo claramente cambió
            seen.LastWriteAfter = T0.AddHours(1);

            var verdict = SaveVerification.Judge(seen);
            _check(verdict.FileChanged, "el archivo cambió, sin duda");
            _eq(SaveVerification.StatusFailed, verdict.Status,
                "y aun así falla: el documento apunta a otro sitio");

            // The converse: same size and timestamp on a genuinely dirty
            // document is a NOTE, not a verdict.
            var quiet = CleanSave();
            quiet.ModifiedBefore = true;
            var quietVerdict = SaveVerification.Judge(quiet);
            _eq(SaveVerification.StatusCompleted, quietVerdict.Status,
                "tamaño y marca iguales no bastan para declarar fallo");
            _check(quietVerdict.Notes.Any(n =>
                    n.IndexOf("granularidad", StringComparison.Ordinal) >= 0),
                "pero se informa, porque puede ser granularidad del sistema de archivos");
        }

        private static void APlainSaveMustNotMoveTheDocument()
        {
            _section("save: un guardado normal no puede cambiar la ruta del documento");

            var seen = CleanSave();
            seen.ModifiedBefore = true;
            seen.PathAfter = @"C:\otra\parte.nwd";
            var verdict = SaveVerification.Judge(seen);

            _eq(SaveVerification.StatusFailed, verdict.Status, "failed");
            _check(verdict.Problems.Any(p =>
                    p.IndexOf("cambió la ruta del documento", StringComparison.Ordinal) >= 0),
                "y se nombra el movimiento");
        }

        private static void TheEnvelopeCarriesTheEvidence()
        {
            _section("save: el envelope lleva la evidencia, no solo el veredicto");

            var json = SaveVerification.Judge(CleanSave()).ToJson();
            foreach (var key in new[]
                     {
                         "save_kind", "status", "requested", "applied", "verified",
                         "document_points_at_destination", "file_changed", "problems", "notes"
                     })
            {
                _check(json.ContainsKey(key), "el envelope incluye «" + key + "»");
            }
            _eq(SaveVerification.KindAlreadySaved, json["save_kind"], "con el tipo de guardado");
            _eq(0.0, json["applied"], "applied=0");
            _eq(1.0, json["verified"], "verified=1");

            // And the counts obey the mutation contract: verified <= applied
            // is the general rule, and `already_saved` is the documented
            // exception — nothing was applied because nothing needed to be,
            // and the file was still checked.
            _check((double)json["verified"] >= (double)json["applied"],
                "already_saved es la excepción documentada a verified <= applied");
        }
    }
}
