using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord
{
    /// <summary>
    /// A pre-order walk that carries each node's ancestors with it.
    /// </summary>
    /// <remarks>
    /// The opening inventory asks every node of the federation about itself
    /// AND its ancestors: the category may sit five levels up, the type and
    /// family eight, the composite parent's name one. Asking upward from each
    /// node meant a native <c>Parent</c> call per hop, a native hash and
    /// equality per cache lookup, and a full path id per node just to read
    /// its parent's name. Walking down instead, each node is read once when
    /// it is entered and its descendants find their ancestors on the frame
    /// chain — plain object references.
    ///
    /// The order is the one <c>DescendantsAndSelf</c> yields (pre-order,
    /// children in their own order), so the item count, the progress and the
    /// cancellation points are unchanged. Generic so the walk and the
    /// nearest-ancestor rules are tested without Navisworks.
    /// </remarks>
    internal static class TreeWalk
    {
        internal sealed class Frame<T>
        {
            public T Node;
            public Frame<T> Parent;
            public int Depth;
            /// <summary>How many children the node has; known when it is yielded.</summary>
            public int ChildCount;
            private readonly Func<T, Dictionary<string, string>> _read;
            private Dictionary<string, string> _own;

            public Frame(T node, Frame<T> parent, Func<T, Dictionary<string, string>> read)
            {
                Node = node;
                Parent = parent;
                Depth = parent == null ? 0 : parent.Depth + 1;
                _read = read;
            }

            /// <summary>The node's own properties, read on first use only.</summary>
            public Dictionary<string, string> Own
                => _own ?? (_own = _read(Node) ?? new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase));

            /// <summary>This frame and up to <paramref name="maxDepth"/>-1 ancestors.</summary>
            public IEnumerable<Frame<T>> Chain(int maxDepth)
            {
                var current = this;
                for (var depth = 0; current != null && depth < maxDepth; depth++)
                {
                    yield return current;
                    current = current.Parent;
                }
            }

            public IEnumerable<string> ValuesUp(string name, int maxDepth)
            {
                foreach (var frame in Chain(maxDepth))
                {
                    if (frame.Own.TryGetValue(name, out var value)) yield return value;
                }
            }

            /// <summary>Nearest-wins merge, as <c>NavisContext.Harvest</c> does.</summary>
            public Dictionary<string, object> Harvest(Func<string, bool> wanted, int maxDepth)
            {
                var result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
                foreach (var frame in Chain(maxDepth))
                {
                    foreach (var pair in frame.Own)
                    {
                        if (result.ContainsKey(pair.Key)) continue;
                        if (wanted != null && !wanted(pair.Key)) continue;
                        result[pair.Key] = pair.Value;
                    }
                }
                return result;
            }
        }

        /// <summary>Every node under <paramref name="root"/>, root first, in pre-order.</summary>
        public static IEnumerable<Frame<T>> PreOrder<T>(
            T root, Func<T, IEnumerable<T>> children, Func<T, Dictionary<string, string>> read)
        {
            if (root == null) yield break;
            var stack = new Stack<Frame<T>>();
            stack.Push(new Frame<T>(root, null, read));
            while (stack.Count > 0)
            {
                var frame = stack.Pop();
                List<T> kids;
                try { kids = (children(frame.Node) ?? Enumerable.Empty<T>()).ToList(); }
                catch { kids = new List<T>(); }
                frame.ChildCount = kids.Count;
                for (var i = kids.Count - 1; i >= 0; i--)
                {
                    stack.Push(new Frame<T>(kids[i], frame, read));
                }
                // Yielded before any child is popped: still pre-order.
                yield return frame;
            }
        }
    }
}
