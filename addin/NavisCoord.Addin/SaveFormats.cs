using System;
using System.IO;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Which destinations <c>document/save_as</c> will accept, and why not.
    /// </summary>
    /// <remarks>
    /// Split out of the save handler with no Navisworks reference so the
    /// refusals can be asserted on a runner with no licence. The .nwfacc case
    /// is the one that matters in the field: it looks like an .nwf with a
    /// longer extension, it is not one, and treating it as one loses a
    /// coordination session to a Desktop Connector sync.
    /// </remarks>
    internal static class SaveFormats
    {
        /// <summary>Formats <c>Document.SaveFile</c> can actually write.</summary>
        public static readonly string[] Writable = { ".nwf", ".nwd" };

        /// <summary>
        /// Why a destination cannot be written because its folder is missing,
        /// or null when the folder exists.
        /// </summary>
        /// <remarks>
        /// <c>SaveFile</c> does not create folders: pointed at a subfolder that
        /// does not exist it returns false, and the only message the operator
        /// got was «Navisworks devolvió false», which names neither the cause
        /// nor the fix. Checked before the call, on the dry run as well, so the
        /// rehearsal already says what the real run would hit.
        /// </remarks>
        public static string MissingFolderProblem(string destination, Func<string, bool> folderExists)
        {
            if (string.IsNullOrWhiteSpace(destination) || folderExists == null) return null;
            string folder;
            try { folder = Path.GetDirectoryName(destination); }
            catch (ArgumentException) { return null; }
            if (string.IsNullOrWhiteSpace(folder) || folderExists(folder)) return null;
            return "La carpeta «" + folder + "» no existe y Navisworks no crea carpetas al guardar. " +
                   "Créala (dentro de una raíz permitida) o elige otra ruta; no se escribió nada.";
        }

        /// <summary>The extension a document hosted in ACC carries locally.</summary>
        public const string Cloud = ".nwfacc";

        public static bool IsWritable(string extension)
            => Writable.Contains((extension ?? string.Empty).Trim(), StringComparer.OrdinalIgnoreCase);

        public static bool IsCloud(string pathOrExtension)
        {
            var value = (pathOrExtension ?? string.Empty).Trim();
            return value.EndsWith(Cloud, StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>Null when the destination is acceptable, else the reason.</summary>
        public static string Reject(string requestedPath)
        {
            var path = (requestedPath ?? string.Empty).Trim();
            if (path.Length == 0) return "La ruta de destino está vacía.";

            string extension;
            try { extension = Path.GetExtension(path); }
            catch (ArgumentException ex) { return "Ruta de destino inválida: " + ex.Message; }

            if (string.IsNullOrEmpty(extension))
            {
                return "La ruta de destino no tiene extensión. Usa " +
                       string.Join(" o ", Writable) + ".";
            }
            if (IsCloud(path))
            {
                return "No se puede guardar como " + Cloud + ": ese formato es la cara local de un " +
                       "documento alojado en ACC, no un archivo de federación que este complemento " +
                       "pueda escribir. Guarda como .nwf local y publica a ACC desde Desktop Connector.";
            }
            if (!IsWritable(extension))
            {
                return "Formato no soportado («" + extension + "»). SaveFile solo escribe " +
                       string.Join(" y ", Writable) + ".";
            }
            return null;
        }
    }
}
