using System;

namespace NavisCoord
{
    /// <summary>
    /// Reading the name a model presents in the tree.
    /// </summary>
    /// <remarks>
    /// This lived on the ribbon's Configure button, which is where it was
    /// first needed and the wrong place for it: the discipline matcher and the
    /// model census both call it, and neither has anything to do with a
    /// button. When the button was removed the compiler found three callers of
    /// a UI class — a good reminder that "where it was first needed" and
    /// "where it belongs" drift apart quietly.
    ///
    /// No Autodesk references, so it is asserted on a runner with no licence.
    /// </remarks>
    internal static class ModelNames
    {
        /// <summary>
        /// Decodes the URL escaping ACC puts in model names.
        /// </summary>
        /// <remarks>
        /// Names from ACC arrive URL-encoded: an Ñ shows up as "%C3%91" in the
        /// tree (observed live on a real ACC federation, 2026-08-12). Decoded
        /// both for display and for discipline-code matching, because a
        /// profile rule written "-SEÑ-" has to match a tree that says
        /// "-SE%C3%91-" or the discipline silently goes unassigned.
        ///
        /// A name that is not encoded comes back untouched, and one that is
        /// malformed comes back as it arrived rather than throwing: a model
        /// with an odd name should be mislabelled at worst, never fatal.
        /// </remarks>
        public static string Decode(string name)
        {
            if (string.IsNullOrEmpty(name) || name.IndexOf('%') < 0) return name ?? string.Empty;
            try
            {
                return Uri.UnescapeDataString(name);
            }
            catch
            {
                return name;
            }
        }
    }
}
