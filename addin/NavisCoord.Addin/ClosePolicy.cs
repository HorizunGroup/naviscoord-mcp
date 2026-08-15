using System;

namespace NavisCoord
{
    /// <summary>Pure validation for document close and application exit.</summary>
    /// <remarks>
    /// Kept free of Autodesk references so the three destructive choices can
    /// be exercised in CI.  There is deliberately no implicit default: a
    /// caller must say save, discard, or refuse to close while dirty.
    /// </remarks>
    internal static class ClosePolicy
    {
        public const string Save = "save";
        public const string Discard = "discard";
        public const string RequireClean = "require_clean";

        public static string Normalize(string value)
            => (value ?? string.Empty).Trim().ToLowerInvariant();

        public static string SaveIdempotencyKey(string value)
        {
            var key = (value ?? string.Empty).Trim();
            return key.Length == 0 ? string.Empty : key + ":close-save";
        }

        public static string Problem(string value, bool modified)
        {
            var disposition = Normalize(value);
            if (disposition != Save && disposition != Discard && disposition != RequireClean)
            {
                return "'disposition' debe ser 'save', 'discard' o 'require_clean'; " +
                       "no existe una decisión implícita al cerrar.";
            }
            if (modified && disposition == RequireClean)
            {
                return "El documento tiene cambios sin guardar y disposition=require_clean. " +
                       "Usa 'save', 'discard' o guarda primero con navis_save_as.";
            }
            return string.Empty;
        }
    }
}
