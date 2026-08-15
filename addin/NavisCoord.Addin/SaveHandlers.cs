using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Autodesk.Navisworks.Api;

namespace NavisCoord
{
    /// <summary>
    /// <c>document/save</c> and <c>document/save_as</c>.
    /// </summary>
    /// <remarks>
    /// Everything the workflow produces — search sets, the clash matrix, the
    /// results, the level groups, the renamed clashes — lives only in memory
    /// until the document is written. Closing Navisworks without saving
    /// discards a coordination run that took twenty minutes, and the ribbon
    /// could only ask the operator nicely to press Ctrl+S. That is not a
    /// reminder problem, it is a missing capability.
    ///
    /// It is also the most dangerous route in the add-in, so it is the most
    /// constrained one:
    ///
    /// * the destination goes through <see cref="PathPolicy"/>, so a path
    ///   composed by a model cannot write outside the allowed roots;
    /// * <c>expected_document_fingerprint</c> is mandatory, so a save cannot
    ///   land on a document that was swapped after the caller looked;
    /// * <c>overwrite</c> defaults to false, so an existing file is never
    ///   replaced by accident;
    /// * only .nwf and .nwd are accepted, and a <c>.nwfacc</c> destination is
    ///   refused outright rather than half-working;
    /// * the result is verified by re-reading the filesystem and the document
    ///   — path, existence, size, extension and the modified flag — so a
    ///   <c>TrySaveFile</c> that returned true without writing anything shows
    ///   up as failed rather than as success.
    ///
    /// **A race this cannot close, stated plainly.** The Python exporters
    /// reserve their destination with an exclusive create and publish by
    /// rename, so two writers cannot both win. Save As cannot do that:
    /// <c>Document.TrySaveFile</c> takes a PATH and opens the file itself,
    /// and there is no overload accepting a handle or a stream. Writing to a
    /// temp name and renaming afterwards is not equivalent either - the
    /// document's own <c>FileName</c> would then point at a file that no
    /// longer exists under that name, which is worse than the race.
    ///
    /// So what is left is the check immediately before the call and the
    /// verification immediately after. Between those two instants another
    /// process could create the destination and have it overwritten. The
    /// window is milliseconds rather than the minutes the PDF used to have,
    /// but it is NOT zero, and this is not presented as a guarantee.
    /// </remarks>
    internal static class SaveHandlers
    {
        public static Dictionary<string, object> Save(Dictionary<string, object> payload)
            => Run(payload, saveAs: false, job: null);

        public static Dictionary<string, object> SaveAs(Dictionary<string, object> payload)
            => Run(payload, saveAs: true, job: null);

        public static Dictionary<string, object> Save(Dictionary<string, object> payload, JobManager.Job job)
            => Run(payload, saveAs: false, job: job);

        public static Dictionary<string, object> SaveAs(Dictionary<string, object> payload, JobManager.Job job)
            => Run(payload, saveAs: true, job: job);

        private static Dictionary<string, object> Run(
            Dictionary<string, object> payload, bool saveAs, JobManager.Job job)
        {
            var doc = Router.RequireDocument();
            var operation = saveAs ? "document/save_as" : "document/save";
            var idempotencyKey = Json.Str(payload, "idempotency_key");

            // Consulted, not merely echoed. Saving advertised an
            // idempotency_key and then ignored it, so a retried save_as wrote
            // the file a second time — and with overwrite=false the retry
            // failed with "already exists" against a file the FIRST call had
            // just written, which reads exactly like the save having failed.
            if (IdempotencyLedger.TryGet(idempotencyKey, out var replay)) return replay;

            var result = new MutationResult(operation)
            {
                JobId = job?.Id ?? string.Empty,
                TargetId = Json.Str(payload, "target_id"),
                IdempotencyKey = idempotencyKey,
                FingerprintBefore = DocumentContext.Fingerprint(doc),
                Requested = 1
            };

            var expected = Json.Str(payload, "expected_document_fingerprint");
            if (string.IsNullOrWhiteSpace(expected))
            {
                result.Fail(
                    "Falta 'expected_document_fingerprint'. Guardar es irreversible: el llamador debe " +
                    "declarar sobre qué documento cree que está actuando. Léelo de 'health' o " +
                    "'capabilities' y repite la llamada.");
                result.FingerprintAfter = result.FingerprintBefore;
                return result.ToJson();
            }
            if (!DocumentFingerprint.Matches(expected, result.FingerprintBefore))
            {
                result.Fail(
                    "El documento activo no es el que esperabas (esperado " + expected +
                    ", activo " + result.FingerprintBefore + "). No se guardó nada.");
                result.FingerprintAfter = result.FingerprintBefore;
                return result.ToJson();
            }

            var capability = DocumentContext.SaveCapability(doc);
            var overwrite = Json.Bool(payload, "overwrite", false);
            var dryRun = Json.Bool(payload, "dry_run", false);
            result.DryRun = dryRun;

            string destination;
            if (saveAs)
            {
                var requested = Json.Str(payload, "path");
                if (string.IsNullOrWhiteSpace(requested))
                {
                    result.Fail("document/save_as necesita 'path'.");
                    result.FingerprintAfter = result.FingerprintBefore;
                    return result.ToJson();
                }

                var formatProblem = RejectUnsupportedFormat(requested);
                if (formatProblem != null)
                {
                    result.Fail(formatProblem);
                    result.FingerprintAfter = result.FingerprintBefore;
                    return result.ToJson();
                }

                var policy = DocumentContext.PolicyFor(doc);
                var decision = policy.ResolveFile(requested, overwrite);
                result.Detail["output_policy"] = policy.Describe();
                if (!decision.Allowed)
                {
                    result.Fail(decision.Reason);
                    if (!string.IsNullOrEmpty(decision.Hint)) result.Warn(decision.Hint);
                    result.FingerprintAfter = result.FingerprintBefore;
                    return result.ToJson();
                }
                destination = decision.Path;
                result.Detail["overwrote_existing"] = decision.Existed;
                result.Detail["allowed_root"] = decision.Root ?? string.Empty;
            }
            else
            {
                if (!(capability.TryGetValue("save", out var allowed) && allowed is bool ok && ok))
                {
                    result.Fail(Json.Str(capability, "reason",
                        "Este documento no se puede guardar en su sitio."));
                    result.Detail["capability"] = capability;
                    result.FingerprintAfter = result.FingerprintBefore;
                    return result.ToJson();
                }
                destination = doc.FileName;
            }

            result.Detail["path"] = destination;
            result.Detail["capability"] = capability;
            result.Detail["was_modified"] = DocumentContext.SafeIsModified(doc);

            if (dryRun)
            {
                result.Detail["would_write"] = destination;
                result.FingerprintAfter = result.FingerprintBefore;
                return result.ToJson();
            }

            job?.Phasing("guardando", "Escribiendo " + Path.GetFileName(destination));

            // Re-checked here, immediately before the write. Between resolving
            // the path and reaching this line the job may have sat in a queue
            // behind another mutation, and a directory on the way can become a
            // junction in that window — the classic check-then-use race. The
            // API gives no way to write through an already-validated handle,
            // so the gap cannot be closed entirely; it can be made small and
            // it can be checked again at its far end.
            if (saveAs)
            {
                // The caller's own overwrite choice, not a relaxed one: a file
                // that appeared in the window is exactly what overwrite=false
                // exists to refuse.
                var recheck = DocumentContext.PolicyFor(doc).ResolveFile(destination, overwrite);
                if (!recheck.Allowed)
                {
                    result.Fail("La ruta dejó de ser válida justo antes de escribir: " + recheck.Reason);
                    if (!string.IsNullOrEmpty(recheck.Hint)) result.Warn(recheck.Hint);
                    result.FingerprintAfter = result.FingerprintBefore;
                    return result.ToJson();
                }
            }

            var beforeStamp = SnapshotFile(destination);
            bool written;
            try
            {
                // TrySaveFile rather than SaveFile: the void overload signals
                // failure by throwing, and a bool is a cleaner thing to verify
                // against the filesystem than a caught exception.
                written = doc.TrySaveFile(destination);
            }
            catch (Exception ex)
            {
                result.Fail("Navisworks rechazó el guardado: " + ex.Message);
                result.FingerprintAfter = DocumentContext.Fingerprint(doc);
                return result.ToJson();
            }

            result.Applied = written ? 1 : 0;
            if (!written)
            {
                result.Fail(
                    "Navisworks devolvió falso al guardar en «" + destination + "». " +
                    "Suele ser un archivo abierto por otro proceso o una carpeta de solo lectura.");
            }

            job?.Phasing("verificando", "Releyendo el archivo escrito");
            var verification = Verify(doc, destination, beforeStamp);
            result.Detail["verification"] = verification;
            result.FingerprintAfter = DocumentContext.Fingerprint(doc);

            var confirmed = verification.TryGetValue("ok", out var flag) && flag is bool b && b;
            result.Verified = confirmed ? 1 : 0;
            if (!confirmed)
            {
                foreach (var problem in (List<object>)verification["problems"])
                {
                    result.Fail(Convert.ToString(problem, CultureInfo.InvariantCulture));
                }
            }

            if (result.Verified == 1 && DocumentContext.IsCloudHosted(doc))
            {
                result.Warn(
                    "El documento sigue apuntando a ACC: guardar en local NO publica al proyecto. " +
                    "Sube el archivo desde Desktop Connector o desde la web de ACC si el equipo " +
                    "tiene que verlo.");
            }

            var payloadOut = result.ToJson();
            // Only a verified save is remembered. Replaying a failure would
            // hand the caller a stale "it did not work" for a retry that might
            // now succeed — the retry is exactly what should be allowed.
            if (result.Verified == 1) IdempotencyLedger.Remember(idempotencyKey, payloadOut);
            return payloadOut;
        }

        internal static string RejectUnsupportedFormat(string requested)
            => SaveFormats.Reject(requested);

        private sealed class FileSnapshot
        {
            public bool Existed;
            public long Length;
            public DateTime LastWriteUtc;
        }

        private static FileSnapshot SnapshotFile(string path)
        {
            try
            {
                var info = new FileInfo(path);
                return info.Exists
                    ? new FileSnapshot { Existed = true, Length = info.Length, LastWriteUtc = info.LastWriteTimeUtc }
                    : new FileSnapshot();
            }
            catch
            {
                return new FileSnapshot();
            }
        }

        /// <summary>
        /// Proof that a file was written, gathered from disk and the document.
        /// </summary>
        private static Dictionary<string, object> Verify(
            Document doc, string destination, FileSnapshot before)
        {
            var problems = new List<object>();
            var payload = new Dictionary<string, object>
            {
                ["source"] = "filesystem_and_document_reread",
                ["path"] = destination
            };

            FileInfo info = null;
            try { info = new FileInfo(destination); } catch { /* reported below */ }

            var exists = info != null && info.Exists;
            payload["exists"] = exists;
            if (!exists)
            {
                problems.Add("El archivo no existe en «" + destination + "» después de guardar.");
            }
            else
            {
                payload["bytes"] = (double)info.Length;
                payload["last_write_utc"] = info.LastWriteTimeUtc.ToString("o", CultureInfo.InvariantCulture);
                payload["format"] = info.Extension;

                if (info.Length == 0)
                {
                    problems.Add("El archivo quedó vacío (0 bytes).");
                }
                if (before.Existed && info.LastWriteTimeUtc <= before.LastWriteUtc && info.Length == before.Length)
                {
                    // A save that changed nothing on disk is the signature of
                    // a silent no-op, and it is exactly what "it said it
                    // worked" used to hide.
                    problems.Add(
                        "El archivo no cambió: misma marca de tiempo y mismo tamaño que antes de guardar.");
                }
            }

            string current = string.Empty;
            try { current = doc.FileName ?? string.Empty; } catch { /* reported below */ }
            payload["document_path_after"] = current;
            payload["document_points_at_destination"] =
                string.Equals(Path.GetFullPath(current.Length == 0 ? "." : current),
                    Path.GetFullPath(destination), StringComparison.OrdinalIgnoreCase);

            var modified = DocumentContext.SafeIsModified(doc);
            payload["modified_after"] = modified;
            if (modified)
            {
                problems.Add(
                    "Navisworks sigue marcando el documento como modificado después de guardar: " +
                    "el guardado no cubrió todos los cambios.");
            }

            payload["problems"] = problems;
            payload["ok"] = problems.Count == 0;
            return payload;
        }
    }
}
