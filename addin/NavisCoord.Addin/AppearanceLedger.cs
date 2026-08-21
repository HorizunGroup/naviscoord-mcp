using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// What an element looked like before NavisCoord coloured it.
    /// </summary>
    /// <remarks>
    /// ``appearance/reset`` with no arguments called
    /// ``ResetAllPermanentMaterials()``. That is not "undo what I did": it is
    /// "delete every permanent override in this model", including the colour
    /// scheme somebody spent an afternoon building for a client presentation.
    /// The two operations look identical from the outside and only one of them
    /// is recoverable.
    ///
    /// So a colouring run gets an <c>appearance_operation_id</c> and records,
    /// per element, what was there before. A reset then asks for that id — or
    /// for an explicit list — and restores exactly those elements to exactly
    /// what they had, which includes restoring the ABSENCE of an override when
    /// there was none.
    ///
    /// Everything here is strings and numbers so the bookkeeping can be
    /// asserted without a document.
    /// </remarks>
    internal static class AppearanceLedger
    {
        /// <summary>
        /// How many operations are remembered per document.
        /// </summary>
        /// <remarks>
        /// Bounded because a Navisworks session runs for days and each entry
        /// holds one row per coloured element. Past this the oldest operation
        /// stops being undoable, and a reset that names it is told so rather
        /// than being handed a partial restore.
        /// </remarks>
        public const int Capacity = 16;

        /// <summary>Largest single operation kept. A bigger one is not recorded.</summary>
        /// <remarks>
        /// A colouring run over a whole federation would otherwise hold a
        /// million rows for the life of the session. The refusal is explicit:
        /// the operation still runs, and it says its appearance is not
        /// individually recoverable.
        /// </remarks>
        public const int MaxElements = 100000;

        /// <summary>One element's appearance before we touched it.</summary>
        internal sealed class Original
        {
            public string PathId = string.Empty;
            /// <summary>Whether it carried an override at all.</summary>
            /// <remarks>
            /// The distinction the API makes and a naive restore loses: an
            /// element with no override is not the same as an element
            /// overridden to its original colour. Restoring the second where
            /// the first belongs leaves a permanent material behind that the
            /// operator never asked for and cannot see.
            /// </remarks>
            public bool HadOverride;
            public int R;
            public int G;
            public int B;
            public double Transparency;

            public Dictionary<string, object> ToJson()
                => new Dictionary<string, object>
                {
                    ["path_id"] = PathId,
                    ["had_override"] = HadOverride,
                    ["r"] = (double)R,
                    ["g"] = (double)G,
                    ["b"] = (double)B,
                    ["transparency"] = Transparency
                };
        }

        /// <summary>One colouring run, and how to undo it.</summary>
        internal sealed class Operation
        {
            public string OperationId = string.Empty;
            public string DocumentFingerprint = string.Empty;
            public DateTime AppliedUtc = DateTime.UtcNow;
            public bool Recoverable = true;
            public string Limitation = string.Empty;
            public readonly List<Original> Elements = new List<Original>();

            public Dictionary<string, object> Describe()
                => new Dictionary<string, object>
                {
                    ["appearance_operation_id"] = OperationId,
                    ["document_fingerprint"] = DocumentFingerprint,
                    ["applied_at"] = AppliedUtc.ToString("o", CultureInfo.InvariantCulture),
                    ["elements"] = (double)Elements.Count,
                    ["recoverable"] = Recoverable,
                    ["limitation"] = Limitation
                };
        }

        private static readonly object Gate = new object();
        private static readonly Dictionary<string, Operation> Operations =
            new Dictionary<string, Operation>(StringComparer.Ordinal);
        private static readonly Queue<string> Order = new Queue<string>();

        public static string NewOperationId()
            => "app-" + Guid.NewGuid().ToString("N").Substring(0, 12);

        /// <summary>Records what a colouring run is about to overwrite.</summary>
        public static Operation Remember(
            string operationId, string fingerprint, IEnumerable<Original> originals)
        {
            var operation = new Operation
            {
                OperationId = operationId,
                DocumentFingerprint = fingerprint ?? string.Empty
            };
            operation.Elements.AddRange(originals ?? Enumerable.Empty<Original>());

            if (operation.Elements.Count > MaxElements)
            {
                // Recorded as unrecoverable rather than truncated. A half-kept
                // ledger would restore some elements and silently abandon the
                // rest, which is worse than saying so.
                operation.Recoverable = false;
                operation.Limitation =
                    "la operación tocó " + operation.Elements.Count + " elementos y el " +
                    "registro guarda hasta " + MaxElements +
                    ": no se puede deshacer elemento por elemento";
                operation.Elements.Clear();
            }

            lock (Gate)
            {
                if (!Operations.ContainsKey(operationId)) Order.Enqueue(operationId);
                Operations[operationId] = operation;
                while (Order.Count > 0 && Operations.Count > Capacity)
                {
                    var oldest = Order.Dequeue();
                    if (Order.Contains(oldest)) continue;
                    Operations.Remove(oldest);
                }
            }
            return operation;
        }

        /// <summary>The operation to undo, or null with a reason.</summary>
        /// <remarks>
        /// The fingerprint check is the one that matters: an operation
        /// recorded against Torre A names path ids that mean something else in
        /// Torre B, and restoring them would paint arbitrary elements of the
        /// open document with colours from a model that is no longer loaded.
        /// </remarks>
        public static Operation Find(string operationId, string fingerprint, out string refusal)
        {
            refusal = string.Empty;
            if (string.IsNullOrWhiteSpace(operationId))
            {
                refusal = "no se indicó appearance_operation_id";
                return null;
            }

            Operation operation;
            lock (Gate)
            {
                if (!Operations.TryGetValue(operationId, out operation))
                {
                    refusal =
                        "la operación '" + operationId + "' no está en el registro: puede haber " +
                        "expirado (se guardan " + Capacity + ") o pertenecer a otra sesión";
                    return null;
                }
            }

            if (!string.IsNullOrEmpty(operation.DocumentFingerprint) &&
                !string.IsNullOrEmpty(fingerprint) &&
                !string.Equals(operation.DocumentFingerprint, fingerprint,
                    StringComparison.OrdinalIgnoreCase))
            {
                refusal =
                    "la operación se aplicó sobre otro documento (" +
                    operation.DocumentFingerprint + ", ahora " + fingerprint +
                    "): sus path id no significan lo mismo aquí";
                return null;
            }

            if (!operation.Recoverable)
            {
                refusal = operation.Limitation;
                return null;
            }
            return operation;
        }

        /// <summary>Drops everything recorded against a document that is gone.</summary>
        public static int Invalidate(string fingerprint)
        {
            lock (Gate)
            {
                var stale = Operations
                    .Where(kv => !string.Equals(kv.Value.DocumentFingerprint, fingerprint,
                        StringComparison.OrdinalIgnoreCase))
                    .Select(kv => kv.Key)
                    .ToList();
                foreach (var key in stale) Operations.Remove(key);
                return stale.Count;
            }
        }

        /// <summary>Sizes only. Diagnostics and tests.</summary>
        public static Dictionary<string, object> Census()
        {
            lock (Gate)
            {
                return new Dictionary<string, object>
                {
                    ["operations"] = (double)Operations.Count,
                    ["capacity"] = (double)Capacity,
                    ["elements"] = (double)Operations.Values.Sum(o => o.Elements.Count),
                    ["max_elements_per_operation"] = (double)MaxElements
                };
            }
        }

        internal static void ResetForTests()
        {
            lock (Gate)
            {
                Operations.Clear();
                Order.Clear();
            }
        }
    }
}
