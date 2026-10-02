using System;
using System.Collections.Generic;

namespace NavisCoord
{
    /// <summary>
    /// Each node's properties read once per run, however many descendants
    /// ask for them.
    /// </summary>
    /// <remarks>
    /// The export and the opening inventory ask the same question of every
    /// node — "what is your category, your type, your level?" — and both
    /// answer it by walking up the tree: five ancestors for the category,
    /// eight for harvested properties. Every node of a Revit tree has dozens
    /// of descendants, so the same ancestor's property tabs were read again
    /// for each of them. Measured live on the «Comité de obra» federation
    /// (58,244 nodes): the inventory took 39 s with one category rule and
    /// 104 s with one keyword, almost all of it re-reading the same tabs.
    ///
    /// The cache keeps, per node, only the properties the run is interested
    /// in, with the rule the uncached code used: within one node the first
    /// non-empty value of a name wins, names compare case-insensitively.
    /// Walking up then reads dictionaries instead of native property tabs.
    ///
    /// Generic over the node type so the semantics are tested with plain
    /// objects; <see cref="NavisContext"/> instantiates it for ModelItem.
    /// A run creates one and drops it: nothing here outlives the call, so a
    /// model edited between two exports is never answered from stale data.
    /// </remarks>
    internal sealed class NodePropertyCache<T> where T : class
    {
        private readonly Func<T, Func<string, bool>, IEnumerable<KeyValuePair<string, string>>> _readOwn;
        private readonly Func<T, T> _parent;
        private readonly Func<string, bool> _interested;
        private readonly Dictionary<T, Dictionary<string, string>> _own;

        /// <param name="readOwn">Yields a node's (property name, value text)
        /// pairs in tab order, converting only the names the filter accepts.</param>
        /// <param name="parent">The node above, or null at the root.</param>
        /// <param name="interested">Which property names to keep.</param>
        /// <param name="comparer">Node identity (ModelItem needs Equals, not
        /// reference identity).</param>
        public NodePropertyCache(
            Func<T, Func<string, bool>, IEnumerable<KeyValuePair<string, string>>> readOwn,
            Func<T, T> parent,
            Func<string, bool> interested,
            IEqualityComparer<T> comparer = null)
        {
            _readOwn = readOwn ?? throw new ArgumentNullException(nameof(readOwn));
            _parent = parent ?? throw new ArgumentNullException(nameof(parent));
            _interested = interested ?? (_ => true);
            _own = new Dictionary<T, Dictionary<string, string>>(comparer ?? EqualityComparer<T>.Default);
        }

        /// <summary>How many nodes have been read. Diagnostics and tests.</summary>
        public int NodesRead => _own.Count;

        /// <summary>The node's own properties of interest; read once.</summary>
        public Dictionary<string, string> Own(T node)
        {
            if (node == null) return Empty;
            if (_own.TryGetValue(node, out var cached)) return cached;

            var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            try
            {
                foreach (var pair in _readOwn(node, _interested))
                {
                    if (string.IsNullOrEmpty(pair.Key) || map.ContainsKey(pair.Key)) continue;
                    if (string.IsNullOrWhiteSpace(pair.Value)) continue;
                    if (!_interested(pair.Key)) continue;
                    map[pair.Key] = pair.Value;
                }
            }
            catch
            {
                // An unreadable tab is not a reason to fail; what was read stays.
            }
            _own[node] = map;
            return map;
        }

        /// <summary>
        /// The values of one property from the node up, nearest first, at
        /// most <paramref name="maxDepth"/> nodes.
        /// </summary>
        public IEnumerable<string> ValuesUp(T node, string name, int maxDepth)
        {
            var current = node;
            for (var depth = 0; current != null && depth < maxDepth; depth++)
            {
                if (Own(current).TryGetValue(name, out var value)) yield return value;
                current = _parent(current);
            }
        }

        /// <summary>
        /// Wanted properties merged from the node up, nearest node winning —
        /// what <c>NavisContext.Harvest</c> returns, read from the cache.
        /// </summary>
        public Dictionary<string, object> Harvest(T node, Func<string, bool> wanted, int maxDepth)
        {
            var result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            var current = node;
            for (var depth = 0; current != null && depth < maxDepth; depth++)
            {
                foreach (var pair in Own(current))
                {
                    if (result.ContainsKey(pair.Key)) continue;
                    if (wanted != null && !wanted(pair.Key)) continue;
                    result[pair.Key] = pair.Value;
                }
                current = _parent(current);
            }
            return result;
        }

        private static readonly Dictionary<string, string> Empty =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    }
}
