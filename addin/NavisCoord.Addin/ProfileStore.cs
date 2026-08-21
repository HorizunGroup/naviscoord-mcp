using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace NavisCoord
{
    /// <summary>
    /// The one profile this instance is working to, and where it came from.
    /// </summary>
    /// <remarks>
    /// There used to be no such thing. <c>navis_load_profile</c> loaded a
    /// profile into the MCP server's memory, and every step that runs in here
    /// — Configurar, the search sets, the clash matrix, <c>workflow/rules</c>
    /// — went and read <c>naviscoord-profile.json</c> off the disk instead.
    /// Two profiles, both called "the profile", and the only hint was a
    /// <c>profile_path</c> field in the response and a paragraph of docstring
    /// asking the reader to notice. Loading a profile and then running the
    /// rules applied criteria the caller had just replaced, silently, and the
    /// result carried no record of which document had actually been used.
    ///
    /// So the server now pushes the profile here and this is the only place
    /// any of it is read from. The rules:
    ///
    /// * the content arrives as canonical JSON over the authenticated port,
    ///   never as a path for this end to open — a path would make
    ///   <c>profile/load</c> an arbitrary-file-read primitive inside
    ///   Navisworks, with the user's token already checked;
    /// * it is validated here as well as at the sender, because "the other
    ///   end checked it" is how both ends end up checking nothing;
    /// * the checksum is recomputed from the received bytes and compared with
    ///   the declared one, so a canonicaliser that drifts is caught at the
    ///   first push rather than by a coordinator wondering why two runs of
    ///   the same profile disagree;
    /// * it is held in memory and NEVER written to disk. Two Navisworks
    ///   instances are two processes with two of these, which is the whole
    ///   isolation story: writing it to the session root would let one
    ///   instance silently re-tune the other;
    /// * with nothing pushed, the on-disk profile is still used — the ribbon
    ///   buttons have no MCP server to push anything — but it is reported as
    ///   a default rather than presented as the caller's choice.
    ///
    /// No Navisworks reference: unit-testable off a licensed machine.
    /// </remarks>
    internal static class ProfileStore
    {
        /// <summary>Where the active profile came from.</summary>
        public const string SourceExplicit = "explicit_mcp";
        public const string SourcePluginDefault = "plugin_default";
        public const string SourcePackagedDefault = "packaged_default";

        /// <summary>
        /// Largest profile accepted over the wire, in bytes.
        /// </summary>
        /// <remarks>
        /// Much smaller than the general body cap: a profile is a settings
        /// file a coordinator edits by hand, and a megabyte is already
        /// generous. The general cap has to allow tens of thousands of GUIDs;
        /// letting this route inherit it would mean 32 MB of JSON parsed into
        /// a dictionary on the listener thread for no legitimate reason.
        /// </remarks>
        public const int MaxProfileBytes = 1024 * 1024;

        private static readonly object Gate = new object();
        private static ActiveProfile _explicitProfile;
        private static ActiveProfile _defaultCache;
        private static string _defaultCacheKey;

        /// <summary>Identifies this instance in what the store reports.</summary>
        public static string SessionId { get; set; } = string.Empty;

        /// <summary>
        /// Where to record a profile change, set by the bridge at startup.
        /// </summary>
        /// <remarks>
        /// A delegate rather than a direct call to <c>BridgeHost.Log</c> so
        /// this file carries no Navisworks reference and the resolution rules
        /// can be exercised by the test runner.
        /// </remarks>
        public static Action<string> Logger { get; set; }

        internal sealed class ActiveProfile
        {
            public Dictionary<string, object> Content;
            public string Canonical = string.Empty;
            public string Checksum = string.Empty;
            public string Name = string.Empty;
            public string Schema = string.Empty;
            public string Source = string.Empty;
            /// <summary>Empty for a pushed profile: there is no file.</summary>
            public string Path = string.Empty;
            public DateTime LoadedUtc;
            public List<string> Warnings = new List<string>();

            public Dictionary<string, object> Describe()
            {
                var payload = new Dictionary<string, object>
                {
                    ["schema"] = Schema,
                    ["checksum"] = Checksum,
                    ["source"] = Source,
                    ["profile_name"] = Name,
                    ["loaded_at"] = LoadedUtc.ToString("o", CultureInfo.InvariantCulture),
                    ["session_id"] = SessionId ?? string.Empty,
                    ["sections"] = Content == null
                        ? new List<object>()
                        : Content.Keys.Where(k => !k.StartsWith("_", StringComparison.Ordinal))
                            .OrderBy(k => k, StringComparer.Ordinal).Cast<object>().ToList(),
                    ["warnings"] = Warnings.Cast<object>().ToList()
                };
                // A pushed profile has no path, and saying so is the point:
                // it is what tells the caller their profile is the one in use.
                if (!string.IsNullOrEmpty(Path)) payload["path"] = Path;
                return payload;
            }
        }

        // ------------------------------------------------------------- load

        /// <summary>
        /// Installs a profile pushed by the MCP server as this session's own.
        /// </summary>
        public static Dictionary<string, object> Load(Dictionary<string, object> payload)
        {
            var canonical = Json.Str(payload, "canonical");
            var declared = Json.Str(payload, "checksum");

            if (string.IsNullOrWhiteSpace(canonical))
            {
                return Error("profile_missing",
                    "La petición no trae el contenido del perfil en \"canonical\".");
            }

            // Measured in BYTES, and before parsing: a limit checked on
            // character count lets a multibyte payload occupy several times
            // what the limit announced.
            var size = Encoding.UTF8.GetByteCount(canonical);
            if (size > MaxProfileBytes)
            {
                return Error("profile_too_large",
                    "El perfil ocupa " + size.ToString(CultureInfo.InvariantCulture) +
                    " bytes y el máximo es " + MaxProfileBytes.ToString(CultureInfo.InvariantCulture) + ".");
            }

            // A mutation already running was planned against the profile in
            // force when it was submitted, and it will verify its work when it
            // finishes. Swapping the criteria underneath it would produce a
            // job that applied one set of rules and checked another.
            // Pending counts, not only running. Swapping the profile while a
            // job sat queued was allowed, and that job would then have been
            // judged against criteria it never agreed to.
            var busy = JobManager.ActiveOrPending();
            if (busy != null)
            {
                return Error("profile_locked",
                    "El trabajo " + busy.Id + " (" + busy.Operation + ") está mutando el documento " +
                    "con el perfil vigente. Espera a que termine o cancélalo antes de cambiarlo.");
            }

            // A profile pushed by the MCP server must declare what it thinks
            // it sent. Accepting a blank checksum means accepting "trust me",
            // which is the one thing a checksum exists to replace — and it is
            // also how a body corrupted in transit installs cleanly.
            if (string.IsNullOrWhiteSpace(declared))
            {
                return Error("profile_checksum_mismatch",
                    "Un perfil enviado por MCP debe declarar su checksum. No se instaló nada.");
            }

            // Strict, and no repair. The permissive reader would take a
            // profile whose last brace was lost in transit and hand back a
            // complete-looking dictionary; its number scanner would take
            // `1..2` as a tolerance. Neither is something to install.
            if (!JsonStrict.TryParseObject(canonical, out var content, out var badJson))
            {
                // The syntax family keeps the contract's own name, which the
                // Python client already knows; depth and size keep their codes
                // because the status mapper answers them differently (413).
                var syntax = badJson.Code == JsonStrict.InvalidJson ||
                             badJson.Code == JsonStrict.NotAnObject ||
                             badJson.Code == JsonStrict.TrailingContent ||
                             badJson.Code == JsonStrict.DuplicateKey;
                var unreadable = Error(syntax ? "profile_unreadable" : badJson.Code,
                    "El perfil no es JSON estricto: " + badJson.Detail +
                    " (posición " + badJson.Position + "). Sigue vigente el anterior.");
                unreadable["json_error"] = badJson.Code;
                unreadable["active"] = Describe();
                return unreadable;
            }
            if (content.Count == 0)
            {
                return Error("profile_unreadable",
                    "El perfil llegó vacío o no es un objeto JSON.");
            }

            // Semantics before the schema reports success. A profile can
            // satisfy every shape rule and still be impossible:
            // `cluster_size_saturation: 1` is the right type in the right
            // range and makes the engine divide by log(1).
            var semantic = ProfileRules.Validate(content);
            if (semantic.Count > 0)
            {
                var impossible = Error("profile_invalid",
                    "El perfil es JSON válido pero matemáticamente imposible; sigue " +
                    "vigente el anterior.");
                impossible["problems"] = semantic.Select(p => (object)p.ToString()).ToList();
                impossible["semantic_problems"] = semantic.Select(p => (object)p.ToJson()).ToList();
                impossible["active"] = Describe();
                return impossible;
            }

            var validation = ProfileSchema.Validate(content);
            if (!validation.Ok)
            {
                // Not installed. Swapping in a profile that does not validate
                // and reporting the problems separately is how a run ends up
                // scored by criteria nobody chose.
                var refusal = Error("profile_invalid",
                    "El perfil no pasó la validación del complemento; sigue vigente el anterior.");
                refusal["problems"] = validation.Errors.Cast<object>().ToList();
                refusal["warnings"] = validation.Warnings.Cast<object>().ToList();
                refusal["active"] = Describe();
                return refusal;
            }

            // Recomputed from what ARRIVED, not from a re-serialisation of the
            // parse: if the two canonicalisers ever drift, this is where it
            // surfaces, instead of in a report six weeks later.
            var actual = ProfileSchema.ChecksumOf(canonical);
            if (!string.IsNullOrWhiteSpace(declared) &&
                !string.Equals(declared, actual, StringComparison.OrdinalIgnoreCase))
            {
                return Error("profile_checksum_mismatch",
                    "El servidor declaró el checksum " + declared + " y el contenido recibido da " +
                    actual + ". El perfil NO se instaló.");
            }

            // Deep-copied before publishing, and never handed out again by
            // reference. Configure used to write `dry_run` straight into the
            // profile's own `sets` dictionary, so after one run the active
            // profile no longer matched the checksum it was published under —
            // the very number that says which criteria produced a result.
            var frozen = DeepCopy(content);
            var installed = new ActiveProfile
            {
                Content = frozen,
                Canonical = canonical,
                Checksum = actual,
                Name = validation.Name,
                Schema = validation.Version,
                Source = SourceExplicit,
                LoadedUtc = DateTime.UtcNow,
                Warnings = validation.Warnings.ToList()
            };

            lock (Gate)
            {
                _explicitProfile = installed;
            }

            // Only the identity is logged. The body can carry a project's
            // naming conventions and folder structure, and this log is read
            // over someone's shoulder.
            Logger?.Invoke("Perfil activo: " + installed.Name + " (" + installed.Checksum + ", " +
                           installed.Schema + ", " + size.ToString(CultureInfo.InvariantCulture) + " bytes)");

            var result = new Dictionary<string, object>
            {
                ["ok"] = true,
                ["installed"] = true,
                ["bytes"] = (double)size
            };
            foreach (var pair in installed.Describe()) result[pair.Key] = pair.Value;
            return result;
        }

        /// <summary>A structural deep copy: no shared mutable state.</summary>
        /// <remarks>
        /// The published profile has to still describe itself later. A shallow
        /// copy shares every nested dictionary, so a handler that adds one
        /// derived flag to `profile["sets"]` edits the published object — and
        /// then `checksum(active.Content)` no longer equals `active.Checksum`,
        /// which is the number a result cites to say which criteria produced
        /// it.
        /// </remarks>
        internal static Dictionary<string, object> DeepCopy(Dictionary<string, object> source)
        {
            var copy = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (var pair in source ?? new Dictionary<string, object>())
            {
                copy[pair.Key] = CopyValue(pair.Value);
            }
            return copy;
        }

        private static object CopyValue(object value)
        {
            if (value is Dictionary<string, object> map) return DeepCopy(map);
            if (value is List<object> list) return list.Select(CopyValue).ToList();
            // Strings, doubles and bools are immutable already.
            return value;
        }

        /// <summary>
        /// Whether the active profile still describes itself.
        /// </summary>
        /// <remarks>
        /// The invariant a profile checksum exists for: whatever is in
        /// `Content` must canonicalise back to `Checksum`. Held before a
        /// workflow, after it, and after an exception — a handler that writes
        /// into the profile it was handed breaks it, and the breakage is
        /// otherwise invisible until somebody compares two reports.
        /// </remarks>
        public static bool VerifyActiveIntegrity(out string detail)
        {
            var active = Active();
            if (active?.Content == null)
            {
                detail = "no hay perfil activo";
                return true;
            }
            var recomputed = ProfileSchema.ChecksumOf(ProfileSchema.Canonical(active.Content));
            if (string.Equals(recomputed, active.Checksum, StringComparison.OrdinalIgnoreCase))
            {
                detail = string.Empty;
                return true;
            }
            detail = "el perfil activo declara " + active.Checksum + " y su contenido da " +
                     recomputed + ": algo escribió dentro del perfil publicado";
            return false;
        }

        /// <summary>Drops the pushed profile; the on-disk default applies again.</summary>
        public static Dictionary<string, object> Reset()
        {
            // Pending counts, not only running. Swapping the profile while a
            // job sat queued was allowed, and that job would then have been
            // judged against criteria it never agreed to.
            var busy = JobManager.ActiveOrPending();
            if (busy != null)
            {
                return Error("profile_locked",
                    "El trabajo " + busy.Id + " (" + busy.Operation + ") está mutando el documento " +
                    "con el perfil vigente. Espera a que termine o cancélalo antes de cambiarlo.");
            }

            bool had;
            lock (Gate)
            {
                had = _explicitProfile != null;
                _explicitProfile = null;
                // Also drop the cached default, so a file edited meanwhile is
                // re-read rather than served from before the reset.
                _defaultCache = null;
                _defaultCacheKey = null;
            }

            var result = new Dictionary<string, object>
            {
                ["ok"] = true,
                ["had_explicit_profile"] = had,
                ["active"] = Describe()
            };
            return result;
        }

        // ---------------------------------------------------------- resolve

        /// <summary>
        /// The profile every step must use, or null when there is none.
        /// </summary>
        public static ActiveProfile Active()
        {
            lock (Gate)
            {
                if (_explicitProfile != null) return _explicitProfile;
            }
            return LoadDefault();
        }

        /// <summary>True when the caller pushed a profile for this session.</summary>
        public static bool HasExplicit
        {
            get { lock (Gate) { return _explicitProfile != null; } }
        }

        private static ActiveProfile LoadDefault()
        {
            var path = ProfileLocator.Find();
            if (path == null) return null;

            string key;
            try
            {
                key = path + "|" + File.GetLastWriteTimeUtc(path).Ticks.ToString(CultureInfo.InvariantCulture);
            }
            catch
            {
                key = path;
            }

            lock (Gate)
            {
                if (_defaultCache != null && string.Equals(_defaultCacheKey, key, StringComparison.Ordinal))
                {
                    return _defaultCache;
                }
            }

            string text;
            try { text = File.ReadAllText(path); }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }

            if (!JsonStrict.TryParseObject(text, out var content, out var badJson))
            {
                // Named, not silently skipped: an operator who edited the
                // default and broke it needs to know that is why nothing runs.
                Logger?.Invoke("El perfil por defecto de " + Path.GetFileName(path) +
                               " no es JSON estricto (" + badJson.Code + ", posición " +
                               badJson.Position + "). No se activó.");
                return null;
            }
            if (content.Count == 0) return null;

            // The on-disk default gets the same semantic pass. A default that
            // divides by zero is not a safer default for having come from
            // disk.
            var defaultSemantics = ProfileRules.Validate(content);
            if (defaultSemantics.Count > 0)
            {
                Logger?.Invoke("El perfil por defecto de " + Path.GetFileName(path) +
                               " es matemáticamente imposible (" + defaultSemantics.Count +
                               " problema(s), p. ej. " + defaultSemantics[0] + "). No se activó.");
                return null;
            }

            var validation = ProfileSchema.Validate(content);
            if (!validation.Ok)
            {
                // The validation used to be computed and then ignored, so a
                // default that failed its own schema became the active profile
                // and every workflow ran against criteria the add-in had
                // already judged invalid. Only the identity and the problem
                // count are logged; the body carries a project's naming
                // conventions.
                Logger?.Invoke("El perfil por defecto de " + Path.GetFileName(path) +
                               " no pasó la validación (" + validation.Errors.Count +
                               " problema(s)). No se activó.");
                return null;
            }

            var resolved = new ActiveProfile
            {
                Content = DeepCopy(content),
                Canonical = ProfileSchema.Canonical(content),
                Checksum = validation.Checksum,
                Name = validation.Name,
                Schema = validation.Version,
                Source = IsPackaged(path) ? SourcePackagedDefault : SourcePluginDefault,
                Path = path,
                LoadedUtc = DateTime.UtcNow,
                Warnings = validation.Warnings.ToList()
            };

            lock (Gate)
            {
                _defaultCache = resolved;
                _defaultCacheKey = key;
            }
            return resolved;
        }

        private static bool IsPackaged(string path)
        {
            try
            {
                var beside = Path.GetDirectoryName(typeof(ProfileStore).Assembly.Location);
                var folder = Path.GetDirectoryName(Path.GetFullPath(path));
                return !string.IsNullOrEmpty(beside) &&
                       string.Equals(beside, folder, StringComparison.OrdinalIgnoreCase);
            }
            catch
            {
                return false;
            }
        }

        // ------------------------------------------------------------ report

        /// <summary>What is in force, for <c>profile/info</c> and job results.</summary>
        public static Dictionary<string, object> Describe()
        {
            var active = Active();
            if (active == null)
            {
                return new Dictionary<string, object>
                {
                    ["ok"] = false,
                    ["error"] = "profile_not_found",
                    ["source"] = "none",
                    ["session_id"] = SessionId ?? string.Empty,
                    ["detail"] = ProfileLocator.NotFoundMessage().Replace("\n", " "),
                    ["hint"] = "Carga uno con navis_load_profile: se envía por el puerto y no " +
                               "necesita existir en el disco de esta máquina."
                };
            }

            var payload = active.Describe();
            payload["ok"] = true;
            payload["accepted_schemas"] = ProfileSchemaVersion.Accepted.Cast<object>().ToList();
            payload["folders"] = ProfileSchema.FolderNames(active.Content).Cast<object>().ToList();
            return payload;
        }

        /// <summary>The checksum a job should record, or "" when none is active.</summary>
        public static string ActiveChecksum()
        {
            var active = Active();
            return active == null ? string.Empty : active.Checksum;
        }

        private static Dictionary<string, object> Error(string code, string detail)
            => new Dictionary<string, object> { ["ok"] = false, ["error"] = code, ["detail"] = detail };
    }
}
