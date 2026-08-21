using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Raised when a credential cannot be protected. Never swallowed.
    /// </summary>
    /// <remarks>
    /// The bearer token in the session file drives Navisworks: it can rebuild
    /// clash tests, colour a model, save over a file. Failing to protect it and
    /// logging a warning is a decision to hand that capability to every local
    /// account and hope somebody reads the log — so it is an exception, and the
    /// bridge does not start.
    /// </remarks>
    internal sealed class SecurityNotEnforceable : Exception
    {
        public readonly string Code;

        public SecurityNotEnforceable(string code, string message, Exception inner = null)
            : base(message, inner)
        {
            Code = code;
        }
    }

    /// <summary>
    /// The verdict on one file's or directory's access control.
    /// </summary>
    /// <remarks>
    /// Kept as data rather than a bool so an unreadable ACL is its own answer.
    /// "We could not read the permissions" and "the permissions are correct"
    /// are opposite facts, and the old audit reported the first as the second
    /// by returning true whenever nothing threw.
    /// </remarks>
    internal sealed class AclVerdict
    {
        public string Path = string.Empty;
        public bool Readable;
        public bool Protected;
        public bool InheritanceDisabled;
        public List<string> UntrustedIdentities = new List<string>();
        public List<string> Problems = new List<string>();

        /// <summary>
        /// Secure only when every question was answered and answered well.
        /// </summary>
        /// <remarks>
        /// Note the first clause: an ACL that could not be read is NOT secure.
        /// Indeterminate is the honest reading, and for a credential the honest
        /// reading of "I do not know" is "do not publish".
        /// </remarks>
        public bool Secure
            => Readable && Protected && InheritanceDisabled &&
               UntrustedIdentities.Count == 0 && Problems.Count == 0;

        public string State
            => !Readable ? "indeterminado" : Secure ? "seguro" : "inseguro";

        public Dictionary<string, object> ToJson()
            => new Dictionary<string, object>
            {
                ["path"] = Path,
                ["state"] = State,
                ["secure"] = Secure,
                ["readable"] = Readable,
                ["protected"] = Protected,
                ["inheritance_disabled"] = InheritanceDisabled,
                ["untrusted"] = UntrustedIdentities.Cast<object>().ToList(),
                ["problems"] = Problems.Cast<object>().ToList()
            };
    }

    /// <summary>
    /// Which Windows identities may hold the session credential.
    /// </summary>
    /// <remarks>
    /// Split out from the file work so the policy — the part that decides
    /// whether an entry on an ACL is acceptable — can be asserted without
    /// touching a real file or weakening a real ACL to see what happens.
    ///
    /// The model: the account running Navisworks, LocalSystem, and the local
    /// Administrators group. Administrators and SYSTEM already own the process
    /// and can read its memory, so listing them concedes nothing that was not
    /// already conceded. Everything wider is a different user reading a token
    /// that drives somebody else's model.
    /// </remarks>
    internal static class AclPolicy
    {
        /// <summary>Well-known SIDs that must never appear on the file.</summary>
        /// <remarks>
        /// By SID rather than by display name: the names are localised, so a
        /// check against "Users" passes silently on a Spanish or German
        /// Windows where the group is called something else.
        /// </remarks>
        public static readonly Dictionary<string, string> ForbiddenSids =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                ["S-1-1-0"] = "Everyone",
                ["S-1-5-32-545"] = "BUILTIN\\Users",
                ["S-1-5-11"] = "Authenticated Users",
                ["S-1-5-32-546"] = "BUILTIN\\Guests",
                ["S-1-5-7"] = "Anonymous Logon",
                ["S-1-5-32-547"] = "Power Users",
                ["S-1-2-0"] = "Local",
                ["S-1-5-4"] = "Interactive",
                ["S-1-5-32-555"] = "Remote Desktop Users"
            };

        /// <summary>Whether this SID is one the credential may be exposed to.</summary>
        public static bool IsTrusted(string sid, IEnumerable<string> ownSids)
        {
            if (string.IsNullOrWhiteSpace(sid)) return false;
            if (ForbiddenSids.ContainsKey(sid)) return false;
            return (ownSids ?? Enumerable.Empty<string>())
                .Any(s => string.Equals(s, sid, StringComparison.OrdinalIgnoreCase));
        }

        /// <summary>A readable name for a forbidden SID, or the SID itself.</summary>
        public static string Describe(string sid)
            => ForbiddenSids.TryGetValue(sid ?? string.Empty, out var name)
                ? name + " (" + sid + ")"
                : sid ?? string.Empty;

        /// <summary>
        /// Judge an ACL from its parts, with no filesystem involved.
        /// </summary>
        /// <param name="readable">Whether the ACL could be read at all.</param>
        /// <param name="isProtected">Whether inheritance is blocked.</param>
        /// <param name="allowedSids">Every SID with an Allow entry.</param>
        /// <param name="ownSids">The identities this process is entitled to.</param>
        public static AclVerdict Judge(
            string path,
            bool readable,
            bool isProtected,
            IEnumerable<string> allowedSids,
            IEnumerable<string> ownSids)
        {
            var verdict = new AclVerdict
            {
                Path = path ?? string.Empty,
                Readable = readable,
                Protected = isProtected,
                InheritanceDisabled = isProtected
            };

            if (!readable)
            {
                verdict.Problems.Add(
                    "no se pudieron leer los permisos: el estado es indeterminado, " +
                    "que para una credencial equivale a inseguro");
                return verdict;
            }
            if (!isProtected)
            {
                verdict.Problems.Add(
                    "la herencia sigue activa: un permiso del directorio padre alcanza al archivo");
            }

            var own = (ownSids ?? Enumerable.Empty<string>()).ToList();
            foreach (var sid in (allowedSids ?? Enumerable.Empty<string>()).Distinct(
                         StringComparer.OrdinalIgnoreCase))
            {
                if (IsTrusted(sid, own)) continue;
                verdict.UntrustedIdentities.Add(Describe(sid));
            }
            if (verdict.UntrustedIdentities.Count > 0)
            {
                verdict.Problems.Add(
                    "hay identidades ajenas con acceso: " +
                    string.Join(", ", verdict.UntrustedIdentities));
            }
            return verdict;
        }
    }

    /// <summary>The bridge's own lifecycle, named.</summary>
    /// <remarks>
    /// "Stopped" used to mean two different things — the listener closed, and
    /// nothing more will happen — and only the first was true. A job already
    /// accepted could still be inside a Navisworks call, mutating the document,
    /// with its session file deleted so nobody could ask about it.
    /// </remarks>
    internal static class BridgeState
    {
        public const string Starting = "starting";
        public const string Ready = "ready";
        public const string Stopping = "stopping";
        public const string Draining = "draining";
        public const string Stopped = "stopped";
        /// <summary>The credential could not be protected. Nothing is listening.</summary>
        public const string FailedSecurity = "failed_security";

        private static readonly HashSet<string> All = new HashSet<string>(StringComparer.Ordinal)
        {
            Starting, Ready, Stopping, Draining, Stopped, FailedSecurity
        };

        public static bool IsKnown(string state) => All.Contains(state ?? string.Empty);

        /// <summary>Whether a new mutation may be accepted in this state.</summary>
        public static bool AcceptsMutations(string state) => state == Ready;

        /// <summary>Whether the bridge still answers questions about itself.</summary>
        /// <remarks>
        /// Draining answers. That is the whole point of the state: a mutation
        /// that cannot be interrupted stays queryable until it finishes, rather
        /// than continuing invisibly behind a deleted session file.
        /// </remarks>
        public static bool AnswersQueries(string state)
            => state == Ready || state == Stopping || state == Draining;

        public static IEnumerable<string> Known => All.OrderBy(s => s, StringComparer.Ordinal);
    }
}
