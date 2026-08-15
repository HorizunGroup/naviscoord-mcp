using System;

namespace NavisCoord
{
    /// <summary>Selects the HWND used to request a non-blocking application exit.</summary>
    /// <remarks>
    /// <c>Process.MainWindowHandle</c> is zero for a visible Navisworks
    /// instance created through the Automation API. Navisworks' own GUI
    /// handle is therefore authoritative; the process handle remains a
    /// fallback for ordinary interactive launches.
    /// </remarks>
    internal static class ExitWindowPolicy
    {
        public static IntPtr PreferredHandle(IntPtr navisworksHandle, IntPtr processHandle)
            => navisworksHandle != IntPtr.Zero ? navisworksHandle : processHandle;
    }
}
