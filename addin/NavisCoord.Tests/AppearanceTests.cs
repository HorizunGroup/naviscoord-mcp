using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// Only what NavisCoord changed gets put back, and nothing else gets wiped.
    /// </summary>
    /// <remarks>
    /// ``appearance/reset`` with no arguments called
    /// ``ResetAllPermanentMaterials()`` — not "undo what I did" but "delete
    /// every permanent override in this model", including a colour scheme
    /// somebody built for a client presentation. The two are indistinguishable
    /// from outside and only one of them is recoverable.
    ///
    /// The other half is the distinction the API makes and a naive restore
    /// loses: an element with NO override is not an element overridden to its
    /// original colour. Restoring the second where the first belongs leaves a
    /// permanent material the operator never set and cannot see.
    /// </remarks>
    internal static class AppearanceTests
    {
        private static Action<string> _section;
        private static Action<object, object, string> _eq;
        private static Action<bool, string> _check;

        public static void Run(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check)
        {
            _section = section;
            _eq = eq;
            _check = check;

            AnOperationRemembersWhatItCovered();
            AbsenceOfAnOverrideIsRestorable();
            AnotherDocumentInvalidatesTheLedger();
            AnUnknownOperationIsRefused();
            TheLedgerIsBounded();
            AnEnormousOperationSaysItIsNotUndoable();
            StackedOperationsKeepTheirOwnBefore();
            TheCensusExposesNoContent();
        }

        private static AppearanceLedger.Original Original(
            string pathId, bool had, int r = 0, int g = 0, int b = 0, double alpha = 0.0)
            => new AppearanceLedger.Original
            {
                PathId = pathId, HadOverride = had, R = r, G = g, B = b, Transparency = alpha
            };

        // -------------------------------------------------------------- cases

        private static void AnOperationRemembersWhatItCovered()
        {
            _section("apariencia: una operación recuerda qué tapó");

            AppearanceLedger.ResetForTests();
            var id = AppearanceLedger.NewOperationId();
            var entry = AppearanceLedger.Remember(id, "fp-1", new[]
            {
                Original("1/2/3", had: true, r: 255, g: 0, b: 0, alpha: 0.25),
                Original("4/5/6", had: false)
            });

            _eq(2, entry.Elements.Count, "guarda una fila por elemento");
            _check(entry.Recoverable, "y se declara recuperable");

            var found = AppearanceLedger.Find(id, "fp-1", out var refusal);
            _check(found != null, "se recupera por su id");
            _eq(string.Empty, refusal, "sin motivo de rechazo");
            _eq(255.0, found.Elements[0].ToJson()["r"], "con el color original");
            _eq(0.25, found.Elements[0].Transparency, "y la transparencia original");
        }

        private static void AbsenceOfAnOverrideIsRestorable()
        {
            _section("apariencia: 'no tenía override' es un estado que se restaura");

            AppearanceLedger.ResetForTests();
            var id = AppearanceLedger.NewOperationId();
            AppearanceLedger.Remember(id, "fp-1", new[]
            {
                Original("sin/override", had: false),
                Original("con/override", had: true, r: 12, g: 34, b: 56)
            });

            var found = AppearanceLedger.Find(id, "fp-1", out _);
            var without = found.Elements.Single(e => e.PathId == "sin/override");
            var with = found.Elements.Single(e => e.PathId == "con/override");

            _check(!without.HadOverride,
                "el que no tenía override se distingue del que sí");
            _check(with.HadOverride, "y viceversa");
            // The whole point: these two must not be restored the same way.
            _check(without.HadOverride != with.HadOverride,
                "restaurar los dos igual dejaría un material permanente que nadie puso");
        }

        private static void AnotherDocumentInvalidatesTheLedger()
        {
            _section("apariencia: una operación de otro documento no se aplica aquí");

            AppearanceLedger.ResetForTests();
            var id = AppearanceLedger.NewOperationId();
            AppearanceLedger.Remember(id, "torre-a", new[] { Original("1/2/3", had: false) });

            var found = AppearanceLedger.Find(id, "torre-b", out var refusal);
            _check(found == null, "no se devuelve");
            _check(refusal.IndexOf("otro documento", StringComparison.Ordinal) >= 0,
                "y se explica por qué: los path id no significan lo mismo");

            // Same document, same answer as before.
            _check(AppearanceLedger.Find(id, "torre-a", out _) != null,
                "sobre su propio documento sí");

            _eq(1, AppearanceLedger.Invalidate("torre-b"),
                "cambiar de documento descarta lo registrado del anterior");
            _check(AppearanceLedger.Find(id, "torre-a", out _) == null,
                "y ya no se puede aplicar");
        }

        private static void AnUnknownOperationIsRefused()
        {
            _section("apariencia: un id desconocido se rechaza con motivo");

            AppearanceLedger.ResetForTests();
            _check(AppearanceLedger.Find("no-existe", "fp-1", out var refusal) == null,
                "un id inventado no devuelve nada");
            _check(refusal.Length > 0, "con un motivo legible");

            _check(AppearanceLedger.Find("", "fp-1", out var empty) == null,
                "y un id vacío tampoco");
            _check(empty.IndexOf("appearance_operation_id", StringComparison.Ordinal) >= 0,
                "diciendo qué argumento falta");
        }

        private static void TheLedgerIsBounded()
        {
            _section("apariencia: el registro está acotado");

            AppearanceLedger.ResetForTests();
            var ids = new List<string>();
            for (var i = 0; i < AppearanceLedger.Capacity + 10; i++)
            {
                var id = AppearanceLedger.NewOperationId();
                ids.Add(id);
                AppearanceLedger.Remember(id, "fp-1", new[] { Original("1/2/" + i, had: false) });
            }

            var census = AppearanceLedger.Census();
            _check((double)census["operations"] <= AppearanceLedger.Capacity,
                "nunca supera su capacidad (" + AppearanceLedger.Capacity + ")");

            // The newest survive; the oldest expired, and a reset naming one is
            // told so rather than handed a partial restore.
            _check(AppearanceLedger.Find(ids[ids.Count - 1], "fp-1", out _) != null,
                "la más reciente sigue disponible");
            _check(AppearanceLedger.Find(ids[0], "fp-1", out var expired) == null,
                "la más antigua expiró");
            _check(expired.IndexOf("expirado", StringComparison.Ordinal) >= 0,
                "y se dice que expiró, no que nunca existió");
        }

        private static void AnEnormousOperationSaysItIsNotUndoable()
        {
            _section("apariencia: una operación gigante declara que no se puede deshacer");

            AppearanceLedger.ResetForTests();
            var id = AppearanceLedger.NewOperationId();
            var many = Enumerable.Range(0, AppearanceLedger.MaxElements + 1)
                .Select(i => Original("1/2/" + i, had: false))
                .ToList();

            var entry = AppearanceLedger.Remember(id, "fp-1", many);
            _check(!entry.Recoverable, "se marca como no recuperable");
            _eq(0, entry.Elements.Count,
                "y no guarda un registro a medias, que restauraría unos y abandonaría otros");
            _check(entry.Limitation.Length > 0, "declarando la limitación");

            _check(AppearanceLedger.Find(id, "fp-1", out var refusal) == null,
                "un reset que la nombre se rechaza");
            _eq(entry.Limitation, refusal, "con esa misma limitación como motivo");
        }

        private static void StackedOperationsKeepTheirOwnBefore()
        {
            _section("apariencia: dos operaciones sobre el mismo elemento se apilan");

            AppearanceLedger.ResetForTests();
            var first = AppearanceLedger.NewOperationId();
            AppearanceLedger.Remember(first, "fp-1", new[]
            {
                Original("1/2/3", had: false)   // nothing before the first run
            });

            var second = AppearanceLedger.NewOperationId();
            AppearanceLedger.Remember(second, "fp-1", new[]
            {
                Original("1/2/3", had: true, r: 255, g: 0, b: 0)   // the first run's colour
            });

            // Each records what IT covered, so undoing them in reverse order
            // walks back through the stack. Undoing the first directly would
            // discard the second's colour — documented, not prevented, because
            // preventing it would mean refusing a legitimate "undo everything".
            var a = AppearanceLedger.Find(first, "fp-1", out _);
            var b = AppearanceLedger.Find(second, "fp-1", out _);
            _check(!a.Elements[0].HadOverride, "la primera recuerda que no había nada");
            _check(b.Elements[0].HadOverride, "la segunda recuerda el rojo de la primera");
            _eq(255.0, b.Elements[0].ToJson()["r"], "con su color exacto");
        }

        private static void TheCensusExposesNoContent()
        {
            _section("apariencia: el censo publica tamaños, no contenido");

            AppearanceLedger.ResetForTests();
            var id = AppearanceLedger.NewOperationId();
            AppearanceLedger.Remember(id, "fp-secreta", new[]
            {
                Original("ruta/muy/privada", had: true, r: 1, g: 2, b: 3)
            });

            var text = Json.Write(AppearanceLedger.Census());
            _check(text.IndexOf("ruta/muy/privada", StringComparison.Ordinal) < 0,
                "el censo no lleva path id");
            _check(text.IndexOf("fp-secreta", StringComparison.Ordinal) < 0,
                "ni la huella del documento");
            _check(text.IndexOf("capacity", StringComparison.Ordinal) >= 0,
                "solo tamaños y límites");
        }
    }
}
