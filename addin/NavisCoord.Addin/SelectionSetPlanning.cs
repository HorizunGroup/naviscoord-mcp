using System;
using System.Collections.Generic;

namespace NavisCoord
{
    /// <summary>
    /// Semantic gate for selection-set publication.
    /// </summary>
    /// <remarks>
    /// The gate deliberately exposes no Remove/Add pair.  A caller can only
    /// add a name that is absent, replace an existing unreferenced name in one
    /// operation, or preserve a referenced name.  This makes the unsafe
    /// remove-then-add sequence unrepresentable in the orchestration code and
    /// keeps the state rules testable without loading Navisworks.
    /// </remarks>
    internal sealed class SelectionSetPublicationGate
    {
        private readonly HashSet<string> _existing;
        private readonly HashSet<string> _referenced;

        public SelectionSetPublicationGate(IEnumerable<string> existing, IEnumerable<string> referenced)
        {
            _existing = new HashSet<string>(existing ?? Array.Empty<string>(), StringComparer.OrdinalIgnoreCase);
            _referenced = new HashSet<string>(referenced ?? Array.Empty<string>(), StringComparer.OrdinalIgnoreCase);
        }

        public void AddNew(string name, Action publish)
        {
            RequireName(name);
            if (_existing.Contains(name))
                throw new InvalidOperationException("No se puede añadir como nueva una carpeta que ya existe: " + name);
            if (publish == null) throw new ArgumentNullException(nameof(publish));
            publish();
            _existing.Add(name);
        }

        public void ReplaceUnreferenced(string name, Action replaceAtomically)
        {
            RequireName(name);
            if (!_existing.Contains(name))
                throw new InvalidOperationException("No se puede reemplazar una carpeta inexistente: " + name);
            if (_referenced.Contains(name))
                throw new InvalidOperationException("No se puede reemplazar una carpeta referenciada: " + name);
            if (replaceAtomically == null) throw new ArgumentNullException(nameof(replaceAtomically));
            replaceAtomically();
        }

        public void PreserveReferenced(string name)
        {
            RequireName(name);
            if (!_existing.Contains(name) || !_referenced.Contains(name))
                throw new InvalidOperationException("Solo se puede preservar como referenciada una carpeta existente y referenciada: " + name);
        }

        private static void RequireName(string name)
        {
            if (string.IsNullOrWhiteSpace(name))
                throw new ArgumentException("El nombre de carpeta no puede estar vacío.", nameof(name));
        }
    }
}
