using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace NavisCoord
{
    /// <summary>
    /// Where the add-in is allowed to write. Mirrors <c>naviscoord/paths.py</c>
    /// so both ends of the bridge refuse the same destinations.
    /// </summary>
    /// <remarks>
    /// Every output path reaching this assembly arrives in an HTTP body, and
    /// that body is composed by a model that has just read an untrusted clash
    /// export. Honouring any path makes <c>document/save_as</c> an
    /// arbitrary-file-write primitive running inside Navisworks, with the
    /// user's token already checked. So:
    ///
    /// * writes land under an explicitly configured root, the default export
    ///   directory, or — for save-as only — the folder the currently open
    ///   document already lives in, which the user chose themselves;
    /// * the path is normalised first and authorised second, so <c>..</c>
    ///   cannot climb out;
    /// * UNC shares and device namespaces are refused outright, because
    ///   "\\somewhere\else" is an exfiltration channel, not a destination;
    /// * an existing file is never replaced unless the caller said so.
    ///
    /// Deliberately free of any Navisworks reference so it is unit-testable
    /// off a licensed machine.
    /// </remarks>
    internal sealed class PathPolicy
    {
        public const string RootsEnvVar = "NAVISCOORD_OUTPUT_ROOTS";

        private static readonly HashSet<string> DeviceNames = new HashSet<string>(
            StringComparer.OrdinalIgnoreCase)
        {
            "con", "prn", "aux", "nul",
            "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
            "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"
        };

        private readonly List<string> _roots;

        public PathPolicy(IEnumerable<string> roots)
        {
            _roots = (roots ?? Enumerable.Empty<string>())
                .Where(r => !string.IsNullOrWhiteSpace(r))
                .Select(NormaliseRoot)
                .Where(r => r != null)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
        }

        public IReadOnlyList<string> Roots => _roots;

        /// <summary>The policy with no caller-supplied context.</summary>
        public static PathPolicy Default() => new PathPolicy(DefaultRoots());

        /// <summary>
        /// The policy widened by the folder of the open document.
        /// </summary>
        /// <remarks>
        /// Saving a copy next to the coordination file is the whole point of
        /// Save As, and the user picked that folder by opening the file from
        /// it. A cloud-hosted document contributes nothing: its "folder" is
        /// an ACC cache path, and writing an .nwf into it would produce a file
        /// nobody can find and Navisworks may overwrite.
        /// </remarks>
        public static PathPolicy ForDocument(string documentPath)
        {
            var roots = DefaultRoots();
            var folder = LocalFolderOf(documentPath);
            if (folder != null) roots.Insert(0, folder);
            return new PathPolicy(roots);
        }

        internal static string LocalFolderOf(string documentPath)
        {
            if (string.IsNullOrWhiteSpace(documentPath)) return null;
            if (Rejection(documentPath) != null) return null;
            try
            {
                var folder = Path.GetDirectoryName(Path.GetFullPath(documentPath));
                return string.IsNullOrWhiteSpace(folder) || !Directory.Exists(folder) ? null : folder;
            }
            catch
            {
                return null;
            }
        }

        public static List<string> DefaultRoots()
        {
            var roots = new List<string>();
            var configured = Environment.GetEnvironmentVariable(RootsEnvVar) ?? string.Empty;
            foreach (var chunk in configured.Split(Path.PathSeparator))
            {
                var candidate = chunk.Trim().Trim('"');
                if (candidate.Length > 0) roots.Add(candidate);
            }
            roots.Add(DefaultExportRoot());
            return roots;
        }

        public static string DefaultExportRoot()
            => Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "NavisCoord", "exports");

        // ------------------------------------------------------- resolution

        public sealed class Decision
        {
            public bool Allowed;
            public string Path;
            public string Root;
            public bool Existed;
            public string Reason;
            public string Hint;

            public Dictionary<string, object> ToJson()
            {
                var payload = new Dictionary<string, object>
                {
                    ["allowed"] = Allowed,
                    ["path"] = Path ?? string.Empty
                };
                if (Allowed)
                {
                    payload["allowed_root"] = Root ?? string.Empty;
                    payload["overwrote_existing"] = Existed;
                }
                else
                {
                    payload["error"] = "path_rejected";
                    payload["detail"] = Reason ?? string.Empty;
                    if (!string.IsNullOrEmpty(Hint)) payload["hint"] = Hint;
                }
                return payload;
            }
        }

        public Decision ResolveFile(string requested, bool overwrite)
        {
            if (string.IsNullOrWhiteSpace(requested))
            {
                return Deny(requested, "La ruta de salida está vacía.");
            }

            var hostile = Rejection(requested);
            if (hostile != null) return Deny(requested, hostile);

            string full;
            try
            {
                var candidate = Environment.ExpandEnvironmentVariables(requested.Trim());
                if (!Path.IsPathRooted(candidate))
                {
                    candidate = Path.Combine(_roots.FirstOrDefault() ?? DefaultExportRoot(), candidate);
                }
                full = Path.GetFullPath(candidate);
            }
            catch (Exception ex)
            {
                return Deny(requested, "No se pudo normalizar la ruta: " + ex.Message);
            }

            hostile = Rejection(full);
            if (hostile != null) return Deny(full, hostile);

            var root = _roots.FirstOrDefault(r => IsWithin(full, r));
            if (root == null)
            {
                return new Decision
                {
                    Allowed = false,
                    Path = full,
                    Reason = "«" + full + "» está fuera de las rutas de salida permitidas.",
                    Hint = "Permitidas: " + string.Join("; ", _roots) +
                           ". Para autorizar otra carpeta, define " + RootsEnvVar +
                           " antes de abrir Navisworks."
                };
            }

            // Textual containment is not containment. A junction created
            // inside an allowed root points anywhere the caller likes, and
            // every check above still passes because the STRING starts with
            // the root. Resolve what the path really refers to.
            var escape = ReparseEscape(full, root);
            if (escape != null) return Deny(full, escape.Reason, escape.Hint);

            if (Directory.Exists(full))
            {
                return Deny(full, "«" + full + "» ya existe y es una carpeta, no un archivo.");
            }

            var existed = File.Exists(full);
            if (existed && !overwrite)
            {
                return new Decision
                {
                    Allowed = false,
                    Path = full,
                    Reason = "«" + full + "» ya existe.",
                    Hint = "Repite la llamada con overwrite=true si de verdad quieres reemplazarlo."
                };
            }

            return new Decision { Allowed = true, Path = full, Root = root, Existed = existed };
        }

        private static Decision Deny(string path, string reason, string hint = null)
            => new Decision { Allowed = false, Path = path ?? string.Empty, Reason = reason, Hint = hint };

        // ------------------------------------------------- reparse points

        /// <summary>
        /// Non-null when the path really lands outside the root it appears to
        /// be inside.
        /// </summary>
        /// <remarks>
        /// The textual check above answers "does this string start with the
        /// root", which is not the same question as "does this write land in
        /// the root". Anyone who can create a directory inside an allowed
        /// root can put a junction there — <c>mklink /J</c> needs no elevation
        /// — and every path under it then passes a prefix test while landing
        /// wherever the junction points. The Python end resolves paths for
        /// real, so the two ends disagreed about what was allowed, which is
        /// the worst way for a policy to be wrong.
        ///
        /// Both sides are resolved through a Win32 handle and compared: the
        /// root as well as the target, because an authorised root that is
        /// ITSELF a link — a redirected Documents folder, a OneDrive mount —
        /// is perfectly legitimate and must not be refused. Only a mismatch
        /// between the two resolved forms is an escape.
        ///
        /// The nearest EXISTING ancestor is what gets resolved: the file
        /// being written usually does not exist yet, and neither may the last
        /// folder or two of the path.
        /// </remarks>
        internal static Decision ReparseEscape(string full, string root)
        {
            string effectiveRoot;
            string effectiveTarget;
            try
            {
                effectiveRoot = ResolveFinal(root) ?? root;
                effectiveTarget = ResolveEffective(full);
            }
            catch
            {
                // A path that cannot be resolved has not been shown to be
                // safe. Refusing is the conservative answer, and the caller
                // gets a destination it can actually reach.
                return new Decision
                {
                    Reason = "No se pudo resolver «" + full + "» para comprobar que queda dentro de la raíz.",
                    Hint = "Elige una ruta bajo una carpeta normal, sin enlaces ni unidades desconectadas."
                };
            }

            if (effectiveTarget == null) return null;

            // Resolution can land on a share; the policy refuses those
            // outright and a junction is not a way around that.
            if (effectiveTarget.StartsWith(@"\\", StringComparison.Ordinal) &&
                !effectiveRoot.StartsWith(@"\\", StringComparison.Ordinal))
            {
                return new Decision
                {
                    Reason = "«" + full + "» resuelve a una ruta de red (" + effectiveTarget + ").",
                    Hint = "Guarda en una carpeta local y copia después si hay que publicarla."
                };
            }

            if (IsWithin(effectiveTarget, effectiveRoot)) return null;

            return new Decision
            {
                Reason = "«" + full + "» parece estar dentro de «" + root + "», pero un enlace " +
                         "(junction o symlink) lo lleva a «" + effectiveTarget + "», fuera de la raíz.",
                Hint = "Escribe en una carpeta real dentro de la raíz autorizada. Para autorizar el " +
                       "destino de verdad, añádelo a " + RootsEnvVar + " antes de abrir Navisworks."
            };
        }

        /// <summary>
        /// Where a path will really land: its nearest existing ancestor
        /// resolved, with the not-yet-existing tail appended.
        /// </summary>
        private static string ResolveEffective(string full)
        {
            var ancestor = full;
            var tail = new List<string>();

            while (!string.IsNullOrEmpty(ancestor) && !Exists(ancestor))
            {
                var parent = Path.GetDirectoryName(ancestor);
                if (string.IsNullOrEmpty(parent) || string.Equals(parent, ancestor, StringComparison.Ordinal))
                {
                    // Reached the volume root without finding anything that
                    // exists: nothing on this chain can be a link.
                    return full;
                }
                tail.Insert(0, ancestor.Substring(parent.Length).Trim(
                    Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
                ancestor = parent;
            }

            var resolved = ResolveFinal(ancestor);
            if (resolved == null) return full;
            return tail.Count == 0
                ? resolved
                : Path.Combine(new[] { resolved }.Concat(tail).ToArray());
        }

        private static bool Exists(string path)
        {
            try { return File.Exists(path) || Directory.Exists(path); }
            catch { return false; }
        }

        /// <summary>
        /// The canonical path of something that exists, links followed.
        /// </summary>
        /// <remarks>
        /// <c>GetFinalPathNameByHandle</c> rather than any of the managed
        /// helpers: <c>FileSystemInfo.LinkTarget</c> and
        /// <c>Directory.ResolveLinkTarget</c> arrived in .NET 6 and this
        /// assembly targets .NET Framework 4.8, which is what Navisworks
        /// 2024-2026 hosts. Opening with no access rights is enough to ask
        /// the question and does not disturb a file another process holds.
        /// </remarks>
        private static string ResolveFinal(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return null;
            using (var handle = CreateFileW(
                       path,
                       0,                       // query only: no read, no write
                       FileShareAll,
                       IntPtr.Zero,
                       OpenExisting,
                       FileFlagBackupSemantics, // required to open a directory
                       IntPtr.Zero))
            {
                if (handle.IsInvalid) return null;

                var buffer = new StringBuilder(1024);
                var length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, VolumeNameDos);
                if (length == 0) return null;
                if (length > buffer.Capacity)
                {
                    buffer = new StringBuilder((int)length + 1);
                    length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, VolumeNameDos);
                    if (length == 0) return null;
                }
                return StripPrefix(buffer.ToString());
            }
        }

        /// <summary>Win32 answers in \\?\ form; the policy compares plain paths.</summary>
        private static string StripPrefix(string value)
        {
            if (string.IsNullOrEmpty(value)) return value;
            if (value.StartsWith(@"\\?\UNC\", StringComparison.Ordinal))
            {
                return @"\\" + value.Substring(8);
            }
            return value.StartsWith(@"\\?\", StringComparison.Ordinal) ? value.Substring(4) : value;
        }

        private const uint FileShareAll = 0x00000007;        // READ | WRITE | DELETE
        private const uint OpenExisting = 3;
        private const uint FileFlagBackupSemantics = 0x02000000;
        private const uint VolumeNameDos = 0x0;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "CreateFileW")]
        private static extern SafeFileHandle CreateFileW(
            string lpFileName,
            uint dwDesiredAccess,
            uint dwShareMode,
            IntPtr lpSecurityAttributes,
            uint dwCreationDisposition,
            uint dwFlagsAndAttributes,
            IntPtr hTemplateFile);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true,
            EntryPoint = "GetFinalPathNameByHandleW")]
        private static extern uint GetFinalPathNameByHandleW(
            SafeFileHandle hFile,
            StringBuilder lpszFilePath,
            uint cchFilePath,
            uint dwFlags);

        // ---------------------------------------------------------- shapes

        /// <summary>Null when the shape is acceptable, otherwise the reason.</summary>
        internal static string Rejection(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return "La ruta de salida está vacía.";
            if (raw.IndexOf('\0') >= 0) return "La ruta contiene un byte nulo.";

            var normalised = raw.Replace('/', '\\');
            if (normalised.StartsWith("\\\\", StringComparison.Ordinal))
            {
                return "«" + raw + "» es una ruta de red (UNC) o de dispositivo. " +
                       "Guarda en una carpeta local y copia después si hay que publicarla.";
            }

            // A colon anywhere but the drive-letter position names an NTFS
            // alternate data stream, which is a write nobody can later find.
            var tail = HasDriveLetter(normalised) ? normalised.Substring(2) : normalised;
            if (tail.IndexOf(':') >= 0)
            {
                return "«" + raw + "» nombra un flujo alterno de datos (ADS), no un archivo.";
            }

            foreach (var part in normalised.Split('\\'))
            {
                if (part.Length == 0) continue;
                var stem = part.Split('.')[0].Trim();
                if (DeviceNames.Contains(stem))
                {
                    return "«" + part + "» es un nombre de dispositivo reservado de Windows.";
                }
            }
            return null;
        }

        private static bool HasDriveLetter(string value)
            => value.Length >= 3 && char.IsLetter(value[0]) && value[1] == ':' && value[2] == '\\';

        internal static bool IsWithin(string candidate, string root)
        {
            if (string.IsNullOrEmpty(candidate) || string.IsNullOrEmpty(root)) return false;
            var a = WithTrailingSeparator(candidate);
            var b = WithTrailingSeparator(root);
            return a.StartsWith(b, StringComparison.OrdinalIgnoreCase);
        }

        private static string WithTrailingSeparator(string value)
        {
            var trimmed = value.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return trimmed + Path.DirectorySeparatorChar;
        }

        private static string NormaliseRoot(string raw)
        {
            try
            {
                var expanded = Environment.ExpandEnvironmentVariables(raw.Trim().Trim('"'));
                return Rejection(expanded) != null ? null : Path.GetFullPath(expanded);
            }
            catch
            {
                return null;
            }
        }

        public Dictionary<string, object> Describe() => new Dictionary<string, object>
        {
            ["allowed_roots"] = _roots.Cast<object>().ToList(),
            ["env_var"] = RootsEnvVar
        };
    }
}
