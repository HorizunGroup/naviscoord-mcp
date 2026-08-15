using System;

namespace NavisCoord
{
    /// <summary>
    /// Stand-in for the real <c>BridgeHost</c>, which lives in a file that
    /// references the Navisworks API and therefore cannot be linked into a
    /// test runner on a machine with no licence.
    /// </summary>
    /// <remarks>
    /// Only <c>Log</c> is needed: the code under test logs, it never reads
    /// anything back. Lines are captured so a test can assert that a security
    /// warning was actually emitted rather than assuming it was.
    /// </remarks>
    internal static class BridgeHost
    {
        private static readonly System.Collections.Generic.List<string> Lines =
            new System.Collections.Generic.List<string>();

        public static void Log(string message)
        {
            lock (Lines) Lines.Add(message ?? string.Empty);
        }

        public static System.Collections.Generic.List<string> Captured()
        {
            lock (Lines) return new System.Collections.Generic.List<string>(Lines);
        }

        public static void Clear()
        {
            lock (Lines) Lines.Clear();
        }
    }
}
