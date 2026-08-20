using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// Pure planning and fresh-handle driver for mutations of the clash tree.
    /// </summary>
    /// <remarks>
    /// Every Navisworks clash edit rebuilds the native result tree.  Therefore
    /// a handle obtained before an edit must never cross the edit boundary.
    /// This driver makes resolution part of every iteration and only hands the
    /// just-resolved value to the corresponding edit delegate.
    /// </remarks>
    internal static class ClashPlanning
    {
        internal sealed class ApplyResult
        {
            public readonly List<string> Applied = new List<string>();
            public readonly List<string> Vanished = new List<string>();
            public readonly List<string> Failed = new List<string>();
        }

        public static ApplyResult ApplyEach<T>(
            IEnumerable<string> identifiers,
            Func<string, T> resolve,
            Action<string, T> edit)
            where T : class
        {
            if (resolve == null) throw new ArgumentNullException(nameof(resolve));
            if (edit == null) throw new ArgumentNullException(nameof(edit));

            var outcome = new ApplyResult();
            foreach (var identifier in (identifiers ?? Enumerable.Empty<string>())
                         .Where(value => !string.IsNullOrWhiteSpace(value))
                         .Distinct(StringComparer.OrdinalIgnoreCase))
            {
                T fresh;
                try
                {
                    fresh = resolve(identifier);
                }
                catch
                {
                    outcome.Failed.Add(identifier);
                    continue;
                }

                if (fresh == null)
                {
                    outcome.Vanished.Add(identifier);
                    continue;
                }

                try
                {
                    edit(identifier, fresh);
                    outcome.Applied.Add(identifier);
                }
                catch
                {
                    outcome.Failed.Add(identifier);
                }
            }
            return outcome;
        }

        public static Dictionary<string, string> SnapshotOwners(
            IEnumerable<KeyValuePair<string, string>> entries)
        {
            var owners = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var entry in entries ?? Enumerable.Empty<KeyValuePair<string, string>>())
            {
                if (string.IsNullOrWhiteSpace(entry.Key) || string.IsNullOrWhiteSpace(entry.Value)) continue;
                owners[entry.Key] = entry.Value;
            }
            return owners;
        }

        public static List<string> OwningTests(
            IEnumerable<string> identifiers, IReadOnlyDictionary<string, string> owners)
            => (identifiers ?? Enumerable.Empty<string>())
                .Where(id => id != null && owners.ContainsKey(id))
                .Select(id => owners[id])
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(name => name, StringComparer.OrdinalIgnoreCase)
                .ToList();

        public static List<string> ForOwner(
            IEnumerable<string> identifiers, IReadOnlyDictionary<string, string> owners, string owner)
            => (identifiers ?? Enumerable.Empty<string>())
                .Where(id => id != null && owners.TryGetValue(id, out var found) &&
                             string.Equals(found, owner, StringComparison.OrdinalIgnoreCase))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
    }
}
