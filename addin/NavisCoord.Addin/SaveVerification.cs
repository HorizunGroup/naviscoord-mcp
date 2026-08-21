using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Whether a save actually saved, and which kind of save it was.
    /// </summary>
    /// <remarks>
    /// Two defects lived in the old inline check, pulling in opposite
    /// directions.
    ///
    /// It computed <c>document_points_at_destination</c> and then never
    /// consulted it. So a Save As that wrote the file but left the document
    /// pointing at the ORIGINAL path reported success: the operator believed
    /// they were now working on the new file, kept editing, and every later
    /// save went to the old one.
    ///
    /// And it treated an unchanged timestamp-and-size as proof of a silent
    /// no-op. On a document that was already clean, a save is *legitimately* a
    /// no-op — there is nothing to write — so a correct outcome was reported
    /// as a failure and the operator was sent looking for a problem that did
    /// not exist. Two files can also share a size and land inside one
    /// filesystem timestamp tick.
    ///
    /// The distinction the old code could not make, and this one is built
    /// around: <c>applied</c> and <c>verified</c> are different questions.
    /// A clean document verifies without applying anything.
    ///
    /// Pure: no Document, no FileInfo. The caller gathers the observations and
    /// this decides, so every branch is assertable without Navisworks.
    /// </remarks>
    internal static class SaveVerification
    {
        // What kind of save this turned out to be.
        public const string KindSave = "save";
        public const string KindSaveAs = "save_as";
        /// <summary>Nothing was dirty, so nothing was written. Not a failure.</summary>
        public const string KindAlreadySaved = "already_saved";

        public const string StatusCompleted = "completed";
        public const string StatusPartial = "partial";
        public const string StatusFailed = "failed";

        /// <summary>Everything observed about the document and the file.</summary>
        /// <remarks>
        /// Both sides of each pair are carried — before and after — because
        /// almost every question here is a comparison, and a single reading
        /// cannot answer "did this change?".
        /// </remarks>
        internal sealed class Observation
        {
            public bool IsSaveAs;

            // ---- the document, before
            public string PathBefore = string.Empty;
            public bool ModifiedBefore;
            public string FingerprintBefore = string.Empty;

            // ---- the document, after
            public string PathAfter = string.Empty;
            public bool ModifiedAfter;
            public string FingerprintAfter = string.Empty;

            // ---- where it was meant to go
            public string Destination = string.Empty;

            // ---- the file, before
            public bool ExistedBefore;
            public long LengthBefore;
            public DateTime LastWriteBefore;

            // ---- the file, after
            public bool ExistsAfter;
            public long LengthAfter;
            public DateTime LastWriteAfter;

            /// <summary>Whether the API call itself reported success.</summary>
            public bool CallSucceeded = true;

            /// <summary>Set when something threw after the write began.</summary>
            public string Interrupted = string.Empty;
        }

        internal sealed class Verdict
        {
            public string Kind = KindSave;
            public string Status = StatusFailed;
            public int Requested = 1;
            public int Applied;
            public int Verified;
            public bool PointsAtDestination;
            public bool FileChanged;
            public readonly List<string> Problems = new List<string>();
            public readonly List<string> Notes = new List<string>();

            public Dictionary<string, object> ToJson()
                => new Dictionary<string, object>
                {
                    ["save_kind"] = Kind,
                    ["status"] = Status,
                    ["requested"] = (double)Requested,
                    ["applied"] = (double)Applied,
                    ["verified"] = (double)Verified,
                    ["document_points_at_destination"] = PointsAtDestination,
                    ["file_changed"] = FileChanged,
                    ["problems"] = Problems.Cast<object>().ToList(),
                    ["notes"] = Notes.Cast<object>().ToList()
                };
        }

        /// <summary>
        /// Whether two paths name the same file, as Windows would decide.
        /// </summary>
        /// <remarks>
        /// Never a raw string comparison. Windows is case-insensitive, accepts
        /// either separator, ignores a trailing one, and a document reopened
        /// from a shortcut comes back spelled differently from the string the
        /// caller sent. Any of those would read as "the document points
        /// somewhere else" and turn a correct Save As into a refusal.
        ///
        /// What is NOT normalised away: a genuinely different directory or
        /// name. That is the case this exists to catch.
        /// </remarks>
        public static bool SamePath(string left, string right)
        {
            var a = Normalise(left);
            var b = Normalise(right);
            return a.Length > 0 && b.Length > 0 &&
                   string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
        }

        private static string Normalise(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return string.Empty;
            var text = path.Trim().Trim('"');
            text = text.Replace('/', '\\');
            while (text.Contains("\\\\") && !text.StartsWith("\\\\", StringComparison.Ordinal))
            {
                text = text.Replace("\\\\", "\\");
            }
            // A trailing separator matters for a directory and never for a
            // file, and these are always files.
            text = text.TrimEnd('\\');
            // The long-path prefix is a spelling, not a location.
            if (text.StartsWith("\\\\?\\UNC\\", StringComparison.OrdinalIgnoreCase))
            {
                text = "\\\\" + text.Substring(8);
            }
            else if (text.StartsWith("\\\\?\\", StringComparison.Ordinal))
            {
                text = text.Substring(4);
            }
            return text.Normalize(System.Text.NormalizationForm.FormC);
        }

        /// <summary>The verdict, from the observations alone.</summary>
        public static Verdict Judge(Observation seen)
        {
            var verdict = new Verdict
            {
                Kind = seen.IsSaveAs ? KindSaveAs : KindSave
            };
            if (seen == null) return verdict;

            verdict.PointsAtDestination = SamePath(seen.PathAfter, seen.Destination);
            verdict.FileChanged =
                !seen.ExistedBefore ||
                seen.LengthAfter != seen.LengthBefore ||
                seen.LastWriteAfter > seen.LastWriteBefore;

            if (!string.IsNullOrEmpty(seen.Interrupted))
            {
                // Something threw after the write started. The file may be
                // complete, half-written or untouched, and none of those is
                // distinguishable from here — so the answer is that it is not
                // known, which is never `completed`.
                verdict.Problems.Add(
                    "La operación se interrumpió después de empezar a escribir (" +
                    seen.Interrupted + "): el estado del archivo es indeterminado.");
                verdict.Status = StatusFailed;
                return verdict;
            }

            if (!seen.CallSucceeded)
            {
                verdict.Problems.Add("Navisworks devolvió false al guardar: no se escribió nada.");
                verdict.Status = StatusFailed;
                return verdict;
            }

            // ------------------------------------------------------ the file
            if (!seen.ExistsAfter)
            {
                verdict.Problems.Add(
                    "El archivo no existe en «" + seen.Destination + "» después de guardar.");
            }
            else if (seen.LengthAfter == 0)
            {
                verdict.Problems.Add("El archivo quedó vacío (0 bytes).");
            }

            // -------------------------------------------------- the document
            if (seen.ModifiedAfter)
            {
                verdict.Problems.Add(
                    "Navisworks sigue marcando el documento como modificado después de " +
                    "guardar: el guardado no cubrió todos los cambios.");
            }

            // The check that was computed and thrown away. A Save As whose
            // file lands but whose document keeps pointing at the original is
            // the worst outcome available: the operator believes they moved,
            // keeps working, and every later save goes to the old file.
            if (seen.IsSaveAs && !verdict.PointsAtDestination)
            {
                verdict.Problems.Add(
                    "El archivo existe, pero el documento sigue apuntando a «" +
                    (string.IsNullOrEmpty(seen.PathAfter) ? "(sin ruta)" : seen.PathAfter) +
                    "» y no a «" + seen.Destination + "». Lo que edites a partir de ahora " +
                    "se guardaría en el archivo anterior.");
            }

            // A plain Save must not have moved the document either.
            if (!seen.IsSaveAs && seen.ExistedBefore &&
                !string.IsNullOrEmpty(seen.PathBefore) &&
                !SamePath(seen.PathAfter, seen.PathBefore))
            {
                verdict.Problems.Add(
                    "Un guardado normal cambió la ruta del documento, de «" + seen.PathBefore +
                    "» a «" + seen.PathAfter + "».");
            }

            if (verdict.Problems.Count > 0)
            {
                verdict.Status = StatusFailed;
                return verdict;
            }

            // ------------------------------------------------ what happened
            //
            // Everything checks out. The remaining question is whether
            // anything was WRITTEN, and the answer is not a failure either
            // way — it decides `applied` and the kind, not the status.
            if (!seen.IsSaveAs && !seen.ModifiedBefore && seen.ExistedBefore && !verdict.FileChanged)
            {
                // Nothing was dirty and nothing changed on disk: the save was
                // a legitimate no-op. Reporting this as a failure — which the
                // old timestamp-and-size rule did — sent operators looking for
                // a problem that did not exist.
                verdict.Kind = KindAlreadySaved;
                verdict.Applied = 0;
                verdict.Verified = 1;
                verdict.Notes.Add(
                    "El documento ya estaba guardado: no había cambios que escribir. " +
                    "El archivo en disco es válido y sigue correspondiendo al documento.");
                verdict.Status = StatusCompleted;
                return verdict;
            }

            verdict.Applied = 1;
            verdict.Verified = 1;
            if (!verdict.FileChanged)
            {
                // The document WAS dirty and the file looks untouched. Not
                // conclusive on its own: a filesystem with coarse timestamp
                // granularity, an edit that happens to produce the same byte
                // count, or an antivirus holding the metadata all look like
                // this. Said out loud rather than turned into a verdict.
                verdict.Notes.Add(
                    "El archivo conserva tamaño y marca de tiempo. Puede ser granularidad " +
                    "del sistema de archivos o un cambio del mismo tamaño; el documento " +
                    "quedó limpio y apuntando al destino, que es lo que se verifica.");
            }
            verdict.Status = StatusCompleted;
            return verdict;
        }
    }
}
