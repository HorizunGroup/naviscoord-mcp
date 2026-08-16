namespace NavisCoord
{
    /// <summary>
    /// Cutting text down to a length a dialog can show.
    /// </summary>
    /// <remarks>
    /// It lived on <c>CoordinationWorkflow</c>, next to the code that runs
    /// clash tests against a live Document. Nothing about shortening a string
    /// needs Autodesk, but sharing a file with the Navisworks API meant
    /// <c>WorkflowText</c> — pure presentation, whose only other dependency is
    /// the JSON codec — could not be compiled on a runner without a licence,
    /// and so the prose the ribbon shows went untested until a verdict line
    /// started claiming a failure had no detail.
    ///
    /// Same lesson as <see cref="ModelNames"/>: "where it was first needed"
    /// and "where it belongs" drift apart quietly.
    /// </remarks>
    internal static class TextLimits
    {
        /// <summary>
        /// At most <paramref name="max"/> characters, with an ellipsis when
        /// something was dropped.
        /// </summary>
        internal static string Truncate(string text, int max)
        {
            var value = text ?? string.Empty;
            return value.Length <= max ? value : value.Substring(0, max) + "…";
        }
    }
}
