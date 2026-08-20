using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;

namespace NavisCoord
{
    /// <summary>
    /// One session file per running Navisworks instance, under
    /// <c>%LOCALAPPDATA%\NavisCoord\sessions\{pid}-{session-id}.json</c>.
    /// </summary>
    /// <remarks>
    /// The single <c>session.json</c> this replaces had three defects, all of
    /// them observed rather than theorised:
    ///
    /// * **Last writer wins.** Two Navisworks versions open at once — 2024 with
    ///   the archived model, 2026 with the live one — both wrote the same file,
    ///   so the MCP server could only ever reach whichever opened last, and no
    ///   caller could say which document it was about to mutate.
    /// * **Either closer deletes it.** Closing the instance you were NOT using
    ///   removed the handshake for the one you were, and the next call failed
    ///   with "no active session" against a perfectly live bridge.
    /// * **Inherited permissions.** The file carries a bearer token that drives
    ///   the user's model. Created with whatever LOCALAPPDATA happened to
    ///   inherit, on a machine with a loose profile ACL that token is readable
    ///   by any other account on the box.
    ///
    /// So: one file per PID, written atomically, with an explicit DACL naming
    /// only the current user, SYSTEM and Administrators, and inheritance
    /// switched off so a permissive parent cannot widen it. Each instance
    /// deletes only its own file. Stale entries are pruned by checking whether
    /// the PID is still alive, never by age alone — a long import can outlive
    /// any heartbeat you would care to wait for.
    /// </remarks>
    internal static class SessionStore
    {
        /// <summary>
        /// Bumped when the shape of a session record or the route surface
        /// changes in a way an older MCP server cannot read. The server
        /// compares it and says so, instead of failing on a missing field.
        /// </summary>
        public const string ContractVersion = "naviscoord.session/2";

        // Kept in step with the assembly. Reported in every session record so
        // the server can refuse a capability the running add-in lacks.
        public const string LegacyFileName = "session.json";

        /// <summary>
        /// The runtime directory, resolved the same way the Python client
        /// resolves it.
        /// </summary>
        /// <remarks>
        /// The environment variable wins over the shell folder because that is
        /// what <c>naviscoord/bridge.py</c> reads. Using
        /// <c>SpecialFolder.LocalApplicationData</c> here and <c>%LOCALAPPDATA%</c>
        /// there agrees on an ordinary machine and diverges on exactly the
        /// ones that matter — a redirected profile, a Citrix or FSLogix
        /// session, a service account — where the add-in would publish its
        /// handshake somewhere the MCP server never looks, and the only
        /// symptom is "no active session" against a healthy bridge.
        /// </remarks>
        public static string Root()
        {
            var configured = Environment.GetEnvironmentVariable("LOCALAPPDATA");
            var baseDir = string.IsNullOrWhiteSpace(configured)
                ? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)
                : configured;
            return Path.Combine(baseDir, "NavisCoord");
        }

        public static string SessionsDir() => Path.Combine(Root(), "sessions");

        public static string LegacyPath() => Path.Combine(Root(), LegacyFileName);

        public static string PathFor(int pid, string sessionId)
            => Path.Combine(SessionsDir(), Sanitize(pid + "-" + sessionId) + ".json");

        /// <summary>
        /// A session id is used as a filename component and echoed back to
        /// callers, so it never carries anything but hex.
        /// </summary>
        public static string NewSessionId()
        {
            var bytes = new byte[8];
            using (var rng = System.Security.Cryptography.RandomNumberGenerator.Create())
            {
                rng.GetBytes(bytes);
            }
            return BitConverter.ToString(bytes).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static string Sanitize(string value)
        {
            var sb = new StringBuilder(value.Length);
            foreach (var c in value ?? string.Empty)
            {
                sb.Append(char.IsLetterOrDigit(c) || c == '-' || c == '_' ? c : '_');
            }
            return sb.Length == 0 ? "session" : sb.ToString();
        }

        // ------------------------------------------------------------ write

        /// <summary>
        /// Publishes this instance's handshake. Returns the file it wrote.
        /// </summary>
        public static string Write(Dictionary<string, object> record, int pid, string sessionId)
        {
            EnsureSecureDirectory(Root());
            EnsureSecureDirectory(SessionsDir());

            var path = PathFor(pid, sessionId);
            WriteAtomic(path, Json.Write(record));
            WriteLegacyPointer(record);
            return path;
        }

        /// <summary>
        /// Keeps the pre-registry <c>session.json</c> alive for MCP servers
        /// that predate the registry.
        /// </summary>
        /// <remarks>
        /// It carries the owning PID so that on shutdown an instance can tell
        /// whether the pointer is its own. Deleting it unconditionally is the
        /// exact bug this file exists to fix.
        /// </remarks>
        private static void WriteLegacyPointer(Dictionary<string, object> record)
        {
            try
            {
                WriteAtomic(LegacyPath(), Json.Write(record));
            }
            catch
            {
                // The registry is the source of truth; the pointer is a
                // courtesy to old clients and must never break a startup.
            }
        }

        /// <summary>
        /// Removes only this instance's entry, then repoints the legacy file
        /// at a surviving session if there is one.
        /// </summary>
        public static void Remove(int pid, string sessionId)
        {
            try
            {
                var path = PathFor(pid, sessionId);
                if (File.Exists(path)) File.Delete(path);
            }
            catch
            {
                // A leftover entry fails closed: its port is dead and its
                // token no longer authenticates anything.
            }

            try
            {
                var legacy = LegacyPath();
                if (!File.Exists(legacy)) return;

                var current = Json.ParseObject(File.ReadAllText(legacy));
                var ownerPid = (int)Json.Num(current, "pid", -1);
                var ownerSession = Json.Str(current, "session_id");
                var isMine = ownerPid == pid ||
                             string.Equals(ownerSession, sessionId, StringComparison.OrdinalIgnoreCase);
                if (!isMine) return;

                var survivor = List().FirstOrDefault(s => IsAlive(s));
                if (survivor != null)
                {
                    WriteAtomic(legacy, Json.Write(survivor));
                }
                else
                {
                    File.Delete(legacy);
                }
            }
            catch
            {
                // Same reasoning: a stale pointer cannot authorise anything.
            }
        }

        /// <summary>Atomic replace via a temp file in the same directory.</summary>
        /// <remarks>
        /// A plain WriteAllText leaves a window where the file exists and is
        /// empty or half-written; a client reading it in that window gets
        /// "session file corrupt" for a bridge that is perfectly healthy. The
        /// temp file is created with the restrictive DACL BEFORE any content
        /// is written to it, so the token is never briefly world-readable.
        /// </remarks>
        internal static void WriteAtomic(string path, string content)
        {
            var directory = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(directory)) EnsureSecureDirectory(directory);

            var temp = path + ".tmp-" + Guid.NewGuid().ToString("N").Substring(0, 8);
            try
            {
                using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    ApplyFileAcl(temp);
                    var bytes = new UTF8Encoding(false).GetBytes(content);
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }

                if (File.Exists(path))
                {
                    // Replace preserves the destination's ACL, which is ours
                    // already; it is the only single-syscall swap available.
                    File.Replace(temp, path, null, ignoreMetadataErrors: true);
                }
                else
                {
                    File.Move(temp, path);
                }
            }
            finally
            {
                try { if (File.Exists(temp)) File.Delete(temp); } catch { /* best effort */ }
            }
        }

        // ------------------------------------------------------------- read

        /// <summary>Every session file currently on disk, newest first.</summary>
        public static List<Dictionary<string, object>> List()
        {
            var sessions = new List<Dictionary<string, object>>();
            try
            {
                var dir = SessionsDir();
                if (!Directory.Exists(dir)) return sessions;

                foreach (var file in Directory.GetFiles(dir, "*.json"))
                {
                    try
                    {
                        var record = Json.ParseObject(File.ReadAllText(file));
                        if (record.Count == 0) continue;
                        record["session_file"] = file;
                        sessions.Add(record);
                    }
                    catch
                    {
                        // One unreadable entry must not hide the others.
                    }
                }
            }
            catch
            {
                return sessions;
            }

            sessions.Sort((a, b) => string.CompareOrdinal(
                Json.Str(b, "started"), Json.Str(a, "started")));
            return sessions;
        }

        /// <summary>
        /// Deletes entries whose process is gone.
        /// </summary>
        /// <remarks>
        /// PID liveness, not a timestamp. A heartbeat-only rule evicts a
        /// healthy instance that spent twenty minutes appending a federated
        /// model on the UI thread — precisely the moment you least want the
        /// handshake to vanish. PID reuse is guarded by comparing the process
        /// start time recorded in the entry.
        /// </remarks>
        public static int Prune()
        {
            var removed = 0;
            foreach (var session in List())
            {
                if (IsAlive(session)) continue;
                try
                {
                    var file = Json.Str(session, "session_file");
                    if (!string.IsNullOrEmpty(file) && File.Exists(file))
                    {
                        File.Delete(file);
                        removed++;
                    }
                }
                catch
                {
                    // Locked by a viewer; next prune gets it.
                }
            }
            return removed;
        }

        internal static bool IsAlive(Dictionary<string, object> session)
        {
            var pid = (int)Json.Num(session, "pid", -1);
            if (pid <= 0) return false;
            try
            {
                var process = System.Diagnostics.Process.GetProcessById(pid);
                if (process.HasExited) return false;

                // Windows recycles PIDs. An entry claiming a PID whose process
                // started AFTER the entry was written is a different process
                // wearing the same number.
                var recorded = Json.Str(session, "process_started");
                if (!string.IsNullOrEmpty(recorded) &&
                    DateTime.TryParse(recorded, CultureInfo.InvariantCulture,
                        DateTimeStyles.RoundtripKind, out var started))
                {
                    var actual = process.StartTime.ToUniversalTime();
                    if (Math.Abs((actual - started.ToUniversalTime()).TotalSeconds) > 5) return false;
                }
                return true;
            }
            catch (ArgumentException)
            {
                return false; // no such process
            }
            catch
            {
                // Access denied reading another user's process: it exists.
                return true;
            }
        }

        // -------------------------------------------------------------- ACL

        /// <summary>
        /// Creates the directory if needed and forces a restrictive DACL on it.
        /// </summary>
        public static void EnsureSecureDirectory(string path)
        {
            Directory.CreateDirectory(path);
            try
            {
                var info = new DirectoryInfo(path);
                var security = info.GetAccessControl();
                // preserveInheritance: false — copying inherited entries in is
                // exactly how a permissive parent leaks through.
                security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
                foreach (var rule in TrustedRules(inheritable: true))
                {
                    security.AddAccessRule(rule);
                }
                foreach (FileSystemAccessRule existing in security
                             .GetAccessRules(true, false, typeof(SecurityIdentifier))
                             .Cast<FileSystemAccessRule>()
                             .ToList())
                {
                    if (!IsTrusted(existing.IdentityReference as SecurityIdentifier))
                    {
                        security.RemoveAccessRuleSpecific(existing);
                    }
                }
                info.SetAccessControl(security);
                RequireSecure(path);
            }
            catch (Exception ex)
            {
                // The directory will contain a bearer token that can drive a
                // live model. Publishing it on a filesystem whose DACL could
                // not be set or verified is never an acceptable fallback.
                throw new UnauthorizedAccessException(
                    "No se pudo asegurar el directorio de sesiones '" + path +
                    "'. El puente no publicará ningún token: " + ex.Message, ex);
            }
        }

        private static void ApplyFileAcl(string path)
        {
            try
            {
                var info = new FileInfo(path);
                var security = info.GetAccessControl();
                security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
                foreach (var rule in TrustedRules(inheritable: false))
                {
                    security.AddAccessRule(rule);
                }
                info.SetAccessControl(security);
                RequireSecure(path);
            }
            catch (Exception ex)
            {
                throw new UnauthorizedAccessException(
                    "No se pudo asegurar el archivo que contendría el token '" + path +
                    "'. Se abortó su publicación: " + ex.Message, ex);
            }
        }

        private static void RequireSecure(string path)
        {
            var audit = Audit(path);
            if (audit.TryGetValue("secure", out var raw) && raw is bool secure && secure) return;
            var findings = audit.TryGetValue("findings", out var found) && found is List<object> list
                ? string.Join("; ", list.Select(Convert.ToString))
                : "no se pudo verificar la DACL";
            throw new UnauthorizedAccessException(findings);
        }

        private static IEnumerable<FileSystemAccessRule> TrustedRules(bool inheritable)
        {
            var inheritance = inheritable
                ? InheritanceFlags.ObjectInherit | InheritanceFlags.ContainerInherit
                : InheritanceFlags.None;

            foreach (var sid in TrustedSids())
            {
                yield return new FileSystemAccessRule(
                    sid, FileSystemRights.FullControl, inheritance,
                    PropagationFlags.None, AccessControlType.Allow);
            }
        }

        private static List<SecurityIdentifier> TrustedSids()
        {
            var sids = new List<SecurityIdentifier>();
            try
            {
                var me = WindowsIdentity.GetCurrent();
                if (me?.User != null) sids.Add(me.User);
            }
            catch { /* fall through: the well-known SIDs still apply */ }

            foreach (var wellKnown in new[]
                     {
                         WellKnownSidType.LocalSystemSid,
                         WellKnownSidType.BuiltinAdministratorsSid
                     })
            {
                try { sids.Add(new SecurityIdentifier(wellKnown, null)); }
                catch { /* absent on this SKU */ }
            }
            return sids;
        }

        private static bool IsTrusted(SecurityIdentifier sid)
            => sid != null && TrustedSids().Any(t => t.Equals(sid));

        /// <summary>
        /// Reports whether the runtime directory is reachable by anyone else.
        /// </summary>
        /// <remarks>
        /// Called at startup. The point is not to be clever about remediation
        /// — <see cref="EnsureSecureDirectory"/> already reasserts the DACL —
        /// but to make a machine where that failed say so out loud, instead of
        /// quietly handing a model-driving token to every local account.
        /// </remarks>
        public static Dictionary<string, object> Audit(string path)
        {
            var findings = new List<object>();
            var result = new Dictionary<string, object>
            {
                ["path"] = path,
                ["exists"] = Directory.Exists(path) || File.Exists(path)
            };

            try
            {
                var security = Directory.Exists(path)
                    ? (CommonObjectSecurity)new DirectoryInfo(path).GetAccessControl()
                    : new FileInfo(path).GetAccessControl();

                var owner = security.GetOwner(typeof(SecurityIdentifier)) as SecurityIdentifier;
                result["owner"] = owner?.Value ?? "(desconocido)";
                result["owner_is_current_user"] = owner != null && IsTrusted(owner);
                if (owner != null && !IsTrusted(owner))
                {
                    findings.Add("El propietario no es el usuario actual ni un administrador.");
                }

                foreach (FileSystemAccessRule rule in security
                             .GetAccessRules(true, true, typeof(SecurityIdentifier)))
                {
                    if (rule.AccessControlType != AccessControlType.Allow) continue;
                    if (IsTrusted(rule.IdentityReference as SecurityIdentifier)) continue;

                    var writes = (rule.FileSystemRights &
                                  (FileSystemRights.Write | FileSystemRights.Modify |
                                   FileSystemRights.FullControl | FileSystemRights.WriteData |
                                   FileSystemRights.TakeOwnership | FileSystemRights.ChangePermissions)) != 0;
                    var reads = (rule.FileSystemRights &
                                 (FileSystemRights.Read | FileSystemRights.ReadData)) != 0;
                    if (writes || reads)
                    {
                        findings.Add(
                            (rule.IdentityReference?.Value ?? "(sid desconocido)") +
                            (writes ? " puede ESCRIBIR" : " puede LEER") + " aquí.");
                    }
                }
            }
            catch (Exception ex)
            {
                findings.Add("No se pudieron leer los permisos: " + ex.Message);
            }

            result["secure"] = findings.Count == 0;
            result["findings"] = findings;
            return result;
        }
    }
}
