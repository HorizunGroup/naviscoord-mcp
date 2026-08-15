using System;
using System.Collections.Generic;
using Autodesk.Navisworks.Api;

namespace NavisCoord
{
    /// <summary>Safe document close and application-exit preparation.</summary>
    /// <remarks>
    /// Navisworks exposes no managed CloseDocument method.  The supported API
    /// operation equivalent to File &gt; New is MainDocument.Clear(), which
    /// returns the application to an untitled empty document.  It is called
    /// only after the caller names what to do with unsaved changes and the
    /// expected document fingerprint has matched.
    ///
    /// Exiting is split across layers: this handler first clears or saves the
    /// document and returns <c>exit_requested</c>; HttpBridge sends that reply
    /// and only then posts the close request to the main window.  The Python
    /// client verifies process liveness afterwards.  This ordering prevents
    /// the process from disappearing before the caller receives the result.
    /// </remarks>
    internal static class CloseHandlers
    {
        public static Dictionary<string, object> CloseDocument(Dictionary<string, object> payload)
        {
            var doc = Router.RequireDocument();
            var result = new MutationResult("document/close")
            {
                TargetId = Json.Str(payload, "target_id"),
                FingerprintBefore = DocumentContext.Fingerprint(doc),
                Requested = 1,
                DryRun = Json.Bool(payload, "dry_run", true)
            };

            var expected = Json.Str(payload, "expected_document_fingerprint");
            if (string.IsNullOrWhiteSpace(expected))
            {
                result.Fail("Falta 'expected_document_fingerprint'; cerrar o descartar no puede actuar a ciegas.");
                result.FingerprintAfter = result.FingerprintBefore;
                return result.ToJson();
            }
            if (!DocumentFingerprint.Matches(expected, result.FingerprintBefore))
            {
                result.Fail("El documento activo no es el que esperabas. No se cerró ni se guardó nada.");
                result.FingerprintAfter = result.FingerprintBefore;
                return result.ToJson();
            }

            var disposition = ClosePolicy.Normalize(Json.Str(payload, "disposition"));
            var modified = DocumentContext.SafeIsModified(doc);
            var problem = ClosePolicy.Problem(disposition, modified);
            result.Detail["disposition"] = disposition;
            result.Detail["was_modified"] = modified;
            if (!string.IsNullOrEmpty(problem))
            {
                result.Fail(problem);
                result.FingerprintAfter = result.FingerprintBefore;
                return result.ToJson();
            }

            if (result.DryRun)
            {
                result.Detail["would_close_document"] = true;
                result.Detail["would_save_first"] = modified && disposition == ClosePolicy.Save;
                result.Detail["would_discard_changes"] = modified && disposition == ClosePolicy.Discard;
                result.FingerprintAfter = result.FingerprintBefore;
                return result.ToJson();
            }

            if (modified && disposition == ClosePolicy.Save)
            {
                var savePayload = new Dictionary<string, object>(payload)
                {
                    ["dry_run"] = false,
                    ["idempotency_key"] = ClosePolicy.SaveIdempotencyKey(
                        Json.Str(payload, "idempotency_key"))
                };
                var save = SaveHandlers.Save(savePayload);
                result.Detail["save"] = save;
                if (!string.Equals(Json.Str(save, "status"), "completed", StringComparison.OrdinalIgnoreCase) ||
                    Json.Int(save, "verified") < 1)
                {
                    result.Fail("El guardado previo no quedó verificado; el documento permanece abierto.");
                    result.FingerprintAfter = DocumentContext.Fingerprint(doc);
                    return result.ToJson();
                }
            }
            else if (modified && disposition == ClosePolicy.Discard)
            {
                result.Warn("Se descartaron cambios sin guardar porque el llamador eligió disposition=discard.");
            }

            try
            {
                Autodesk.Navisworks.Api.Application.MainDocument.Clear();
                result.Applied = 1;
            }
            catch (Exception ex)
            {
                result.Fail("Navisworks no pudo cerrar el documento: " + ex.Message);
                result.FingerprintAfter = DocumentContext.Fingerprint(doc);
                return result.ToJson();
            }

            var active = Autodesk.Navisworks.Api.Application.ActiveDocument;
            var closed = active == null || active.IsClear;
            result.Verified = closed ? 1 : 0;
            result.FingerprintAfter = DocumentContext.Fingerprint(active);
            result.Detail["document_open_after"] = !closed;
            result.Detail["verification"] = new Dictionary<string, object>
            {
                ["source"] = "active_document_reread",
                ["is_clear"] = closed
            };
            if (!closed) result.Fail("Navisworks sigue mostrando un documento cargado después de Clear().");
            return result.ToJson();
        }

        public static Dictionary<string, object> ExitApplication(Dictionary<string, object> payload)
        {
            var dryRun = Json.Bool(payload, "dry_run", true);
            var doc = Autodesk.Navisworks.Api.Application.ActiveDocument;
            var open = doc != null && !doc.IsClear;
            Dictionary<string, object> close = null;

            if (open)
            {
                close = CloseDocument(payload);
                var closeStatus = Json.Str(close, "status");
                if (dryRun || !string.Equals(closeStatus, "completed", StringComparison.OrdinalIgnoreCase))
                {
                    close["operation"] = "application/exit";
                    close["exit_requested"] = false;
                    return close;
                }
            }
            else
            {
                var problem = ClosePolicy.Problem(Json.Str(payload, "disposition"), false);
                if (!string.IsNullOrEmpty(problem))
                {
                    return new Dictionary<string, object>
                    {
                        ["operation"] = "application/exit",
                        ["status"] = "failed",
                        ["exit_requested"] = false,
                        ["errors"] = new List<object> { problem }
                    };
                }
                if (dryRun)
                {
                    return new Dictionary<string, object>
                    {
                        ["operation"] = "application/exit",
                        ["status"] = "planned",
                        ["dry_run"] = true,
                        ["exit_requested"] = false,
                        ["would_exit_application"] = true
                    };
                }
            }

            return new Dictionary<string, object>
            {
                ["operation"] = "application/exit",
                ["status"] = "accepted",
                ["dry_run"] = false,
                ["exit_requested"] = true,
                ["application_exit_verified"] = false,
                ["verification_source"] = "pending_process_exit",
                ["document_close"] = close ?? (object)new Dictionary<string, object>
                {
                    ["status"] = "not_needed",
                    ["document_open_before"] = false
                }
            };
        }
    }
}
