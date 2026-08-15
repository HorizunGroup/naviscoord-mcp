using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Autodesk.Navisworks.Api;

namespace NavisCoord
{
    /// <summary>
    /// Facts about the live document that every other handler needs: which
    /// document it is, whether it can be saved, and where it came from.
    /// </summary>
    internal static class DocumentContext
    {
        /// <summary>Formats <c>SaveFile</c> can actually write.</summary>
        /// <remarks>
        /// The table itself lives in <see cref="SaveFormats"/>, which carries
        /// no Navisworks reference and therefore has tests.
        /// </remarks>
        public static readonly string[] SaveableExtensions = SaveFormats.Writable;

        /// <summary>
        /// The extension a cloud-hosted coordination file carries.
        /// </summary>
        /// <remarks>
        /// A <c>.nwfacc</c> is NOT an .nwf with a different name: it is the
        /// local face of a document whose master lives in ACC, and treating
        /// it as a local federation file is how a team loses a day. Writing
        /// back to it does not publish to ACC, and the next Desktop Connector
        /// sync can replace it. The honest workflow is Save As to a local
        /// .nwf, which is what the save handler enforces rather than hopes.
        /// </remarks>
        public const string CloudExtension = SaveFormats.Cloud;

        /// <summary>
        /// Stable identity of the open document + its attached models.
        /// </summary>
        /// <remarks>
        /// Composed from the file path, the title and every model's source
        /// path, because those together are what make a path id mean
        /// something. Appending or detaching a model renumbers the model
        /// indices, so a plan computed before it addresses different geometry
        /// afterwards — which is exactly the case this must catch.
        /// </remarks>
        public static string Fingerprint(Document doc)
        {
            if (doc == null || doc.IsClear) return DocumentFingerprint.None;

            var parts = new List<string>
            {
                doc.FileName ?? string.Empty,
                doc.Title ?? string.Empty,
                doc.Models.Count.ToString(CultureInfo.InvariantCulture)
            };

            for (var i = 0; i < doc.Models.Count; i++)
            {
                try
                {
                    var model = doc.Models[i];
                    parts.Add(i.ToString(CultureInfo.InvariantCulture));
                    parts.Add(model.SourceFileName ?? model.FileName ?? string.Empty);
                }
                catch
                {
                    parts.Add("?" + i.ToString(CultureInfo.InvariantCulture));
                }
            }
            return DocumentFingerprint.Compute(parts);
        }

        /// <summary>
        /// Everything a caller needs to identify what it is about to mutate.
        /// </summary>
        public static Dictionary<string, object> Describe(Document doc, bool includePath)
        {
            if (doc == null || doc.IsClear)
            {
                return new Dictionary<string, object> { ["open"] = false };
            }

            var payload = new Dictionary<string, object>
            {
                ["open"] = true,
                ["title"] = doc.Title ?? string.Empty,
                ["fingerprint"] = Fingerprint(doc),
                ["models"] = (double)doc.Models.Count,
                ["modified"] = SafeIsModified(doc),
                ["cloud_hosted"] = IsCloudHosted(doc)
            };

            if (includePath)
            {
                // Only ever behind authentication: the path leaks the project
                // name, the client and the machine's directory layout.
                payload["path"] = doc.FileName ?? string.Empty;
                payload["units"] = doc.Units.ToString();
                payload["metre_scale"] = NavisContext.MetreScale(doc);
            }
            return payload;
        }

        public static bool SafeIsModified(Document doc)
        {
            try { return doc.IsModified; }
            catch { return false; }
        }

        public static bool IsCloudHosted(Document doc)
        {
            var path = doc?.FileName ?? string.Empty;
            return path.EndsWith(CloudExtension, StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>
        /// Whether save and save-as are available right now, and if not, why.
        /// </summary>
        /// <remarks>
        /// Reported as data rather than discovered by failing: the MCP server
        /// asks <c>capabilities</c> first and can tell the operator "this file
        /// came from ACC, use save_as to a local .nwf" before anything is
        /// attempted, instead of after a confusing exception.
        /// </remarks>
        public static Dictionary<string, object> SaveCapability(Document doc)
        {
            if (doc == null || doc.IsClear)
            {
                return new Dictionary<string, object>
                {
                    ["save"] = false,
                    ["save_as"] = false,
                    ["reason"] = "No hay documento abierto."
                };
            }

            var path = doc.FileName ?? string.Empty;
            var cloud = IsCloudHosted(doc);
            var hasPath = !string.IsNullOrWhiteSpace(path);
            var extension = hasPath ? Path.GetExtension(path) : string.Empty;
            var localFormat = SaveableExtensions.Contains(extension, StringComparer.OrdinalIgnoreCase);

            var capability = new Dictionary<string, object>
            {
                ["save"] = hasPath && localFormat && !cloud,
                ["save_as"] = true,
                ["formats"] = SaveableExtensions.Cast<object>().ToList(),
                ["current_extension"] = extension,
                ["cloud_hosted"] = cloud,
                ["modified"] = SafeIsModified(doc)
            };

            if (cloud)
            {
                capability["reason"] =
                    "El documento abierto es un " + CloudExtension + " de ACC. Guardar sobre él no " +
                    "publica nada en la nube y el siguiente sync de Desktop Connector puede " +
                    "reemplazarlo. Usa document/save_as hacia un .nwf local.";
            }
            else if (!hasPath)
            {
                capability["reason"] =
                    "El documento nunca se ha guardado, así que no tiene ruta. Usa document/save_as.";
            }
            else if (!localFormat)
            {
                capability["reason"] =
                    "La extensión actual («" + extension + "») no es un formato que SaveFile pueda " +
                    "escribir. Usa document/save_as hacia .nwf o .nwd.";
            }

            return capability;
        }

        /// <summary>The output policy in force for this document.</summary>
        public static PathPolicy PolicyFor(Document doc)
        {
            // A cloud-hosted document contributes no root: its "folder" is a
            // Desktop Connector cache, and an .nwf written there is a file the
            // team cannot find and the connector may overwrite.
            if (doc == null || doc.IsClear || IsCloudHosted(doc)) return PathPolicy.Default();
            return PathPolicy.ForDocument(doc.FileName);
        }
    }
}
