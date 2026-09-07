using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Clash;

namespace NavisCoord
{
    // UI-thread only. The epoch changes on source geometry, transforms, properties,
    // reloads and clash-test edits. Selection/camera changes do not expire analysis.
    internal static class AnalysisRevision
    {
        private static Document _document;
        private static string _epoch = Guid.NewGuid().ToString("N");
        private static long _revision;
        private static readonly List<Tuple<object, EventInfo, Delegate>> Subscriptions =
            new List<Tuple<object, EventInfo, Delegate>>();

        public static string Current(Document document)
        {
            if (document == null || document.IsClear) throw new InvalidOperationException("No document for analysis revision.");
            if (!ReferenceEquals(document, _document))
            {
                foreach (var s in Subscriptions) s.Item2.RemoveEventHandler(s.Item1, s.Item3);
                Subscriptions.Clear();
                _epoch = Guid.NewGuid().ToString("N");
                _revision = 0;
                _document = null;
                try
                {
                    Watch(document.Models, "CollectionChanged", "ModelTransformChanged", "ModelItemPropertiesChanged", "SceneLoaded");
                    Watch(document.GetClash().TestsData, "Changed");
                    Watch(document.SelectionSets, "Changed");
                    Watch(document, "UnitsChanged");
                    _document = document;
                }
                catch
                {
                    foreach (var s in Subscriptions) s.Item2.RemoveEventHandler(s.Item1, s.Item3);
                    Subscriptions.Clear();
                    throw;
                }
            }
            return _epoch + ":" + _revision.ToString(CultureInfo.InvariantCulture) + ":" + DocumentContext.Fingerprint(document);
        }

        private static void Watch(object source, params string[] names)
        {
            foreach (var name in names)
            {
                var evt = source.GetType().GetEvent(name);
                if (evt == null) throw new InvalidOperationException("Cannot monitor analysis revision: " + name);
                var method = typeof(AnalysisRevision).GetMethod(nameof(Changed), BindingFlags.Static | BindingFlags.NonPublic);
                var handler = Delegate.CreateDelegate(evt.EventHandlerType, method);
                evt.AddEventHandler(source, handler);
                Subscriptions.Add(Tuple.Create(source, evt, handler));
            }
        }

        private static void Changed(object sender, EventArgs args) { _revision++; }

        public static Dictionary<string, object> Validate(Dictionary<string, object> payload)
        {
            var expected = Json.Str(payload, "expected_analysis_revision");
            if (string.IsNullOrEmpty(expected)) return null;
            var actual = Current(Autodesk.Navisworks.Api.Application.ActiveDocument);
            if (string.Equals(expected, actual, StringComparison.Ordinal)) return null;
            return new Dictionary<string, object> {
                ["error"] = "stale_analysis", ["status"] = "failed",
                ["detail"] = "Geometry or clash results changed. Analyze this revision before applying a derived plan.",
                ["expected_analysis_revision"] = expected, ["analysis_revision"] = actual
            };
        }
    }
}
