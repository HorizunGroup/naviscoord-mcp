using System;
using System.Collections.Generic;
using System.Linq;

namespace NavisCoord.Tests
{
    /// <summary>
    /// Run All may only count a clash test as verified when it is Complete.
    /// </summary>
    /// <remarks>
    /// The check being replaced was <c>status != "New"</c>, which accepted Old
    /// and Partial. Old is the dangerous one: the test carries a full result
    /// count from a previous version of the model, so the report looks
    /// populated and correct while describing geometry that has since moved.
    ///
    /// None of this needs Navisworks. The verdict is a function of two lists
    /// of strings and ints, which is exactly why it was extracted.
    /// </remarks>
    internal static class RunVerificationTests
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

            EveryTransitionIntoComplete();
            CompleteStaysComplete();
            NotCompleteIsNeverVerified();
            DegradationIsReported();
            MissingAndUnexpected();
            UnexpectedDoesNotOffsetMissing();
            MixedRunIsPartial();
            AllStaleIsFailed();
            CountsDoNotDecideStatus();
            UnchangedResultsAreReported();
            EnumerationOrderDoesNotMatter();
            SameNameDifferentGuid();
            NoTestsAtAll();
            AnInvocationThatThrew();
            OnlyAllCompleteAllowsCompleted();
            TheOldRuleWouldHaveAccepted();
            TheInvariantsHold();

            RenameIsFollowedByIdentity();
            RecreatedWithSameNameIsNotTheSameTest();
            MissingIdentityBeforeIsUnverifiable();
            MissingIdentityAfterIsNotAMatch();
            DuplicateGuidBeforeIsAConflict();
            DuplicateGuidAfterIsAConflict();
            AmbiguityNeverCompletes();
            ReversedOrderWithRepeatedNames();
            IdentityCategoriesPartitionRequested();
            NoProductionPathUsesNameAsIdentity();
        }

        // ------------------------------------------------------------ helpers

        /// <summary>A real, stable GUID from a readable label.</summary>
        /// <remarks>
        /// The fixtures used to pass "t-1" as an identity. That reads well and
        /// is not a GUID, and once the classifier started REQUIRING a parseable
        /// non-empty identity every one of them became unverifiable — which is
        /// the rule working, not the rule being wrong. This keeps the labels
        /// and makes them real: the same label always produces the same GUID,
        /// and two labels never collide.
        /// </remarks>
        private static string Id(string label)
        {
            var bytes = new byte[16];
            var source = System.Text.Encoding.UTF8.GetBytes(label ?? string.Empty);
            for (var i = 0; i < source.Length; i++) bytes[i % 16] ^= source[i];
            bytes[0] |= 0x10;   // never all-zero, so it is never Guid.Empty
            return new Guid(bytes).ToString();
        }

        private static RunVerification.TestState State(
            string label, string name, string status, int results = 0, int groups = 0)
            => new RunVerification.TestState
            {
                Guid = label == null ? string.Empty : Id(label),
                Name = name,
                Status = status,
                ResultCount = results,
                GroupCount = groups
            };

        /// <summary>A test with no usable identity at all.</summary>
        private static RunVerification.TestState Anonymous(
            string name, string status, int results = 0)
            => new RunVerification.TestState
            {
                Guid = string.Empty, Name = name, Status = status, ResultCount = results
            };

        private static RunVerification.TestOutcome Of(
            RunVerification.RunVerdict verdict, string label)
            => verdict.Tests.Single(
                t => string.Equals(t.Identity, Id(label), StringComparison.OrdinalIgnoreCase));

        // -------------------------------------------------------------- cases

        private static void EveryTransitionIntoComplete()
        {
            _section("run: New, Old y Partial que llegan a Complete cuentan como corridos");

            foreach (var from in new[]
                     {
                         RunVerification.New, RunVerification.Old, RunVerification.Partial
                     })
            {
                var verdict = RunVerification.Classify(
                    new[] { State("t-1", "ARQ vs EST", from, results: 3) },
                    new[] { State("t-1", "ARQ vs EST", RunVerification.Complete, results: 12) },
                    invocationStarted: true);

                _eq(1, verdict.CompletedNowCount, from + " -> Complete es completed_now");
                _eq(0, verdict.AlreadyCompleteCount, "y no already_complete");
                _eq(1, verdict.Verified, "cuenta como verificado");
                _eq(RunVerification.StatusCompleted, verdict.Status, "y el run es completed");
                _check(Of(verdict, "t-1").Verified, "el detalle por test también lo dice");
            }
        }

        private static void CompleteStaysComplete()
        {
            _section("run: Complete que sigue Complete es already_complete");

            var verdict = RunVerification.Classify(
                new[] { State("t-1", "ARQ vs EST", RunVerification.Complete, results: 12) },
                new[] { State("t-1", "ARQ vs EST", RunVerification.Complete, results: 12) },
                invocationStarted: true);

            _eq(1, verdict.AlreadyCompleteCount, "already_complete");
            _eq(0, verdict.CompletedNowCount, "no completed_now: no cambió de estado");
            _eq(1, verdict.Verified, "y cuenta como verificado igualmente");
            _eq(RunVerification.StatusCompleted, verdict.Status, "el run es completed");
        }

        private static void NotCompleteIsNeverVerified()
        {
            _section("run: Old, Partial y New nunca cuentan como verificados");

            var cases = new[]
            {
                new[] { RunVerification.Old, RunVerification.Old },
                new[] { RunVerification.Partial, RunVerification.Partial },
                new[] { RunVerification.New, RunVerification.New }
            };

            foreach (var pair in cases)
            {
                var verdict = RunVerification.Classify(
                    new[] { State("t-1", "ARQ vs EST", pair[0], results: 200) },
                    new[] { State("t-1", "ARQ vs EST", pair[1], results: 200) },
                    invocationStarted: true);

                _eq(0, verdict.Verified, pair[1] + " no es verificación");
                _eq(1, verdict.Failed, "y cuenta como fallo");
                _eq(RunVerification.StatusFailed, verdict.Status, "el run entero falla");
                _check(!Of(verdict, "t-1").Verified, "el detalle por test tampoco lo da por bueno");
            }

            // Each flavour is counted separately, so the report can say which.
            var old = RunVerification.Classify(
                new[] { State("t-1", "a", RunVerification.New) },
                new[] { State("t-1", "a", RunVerification.Old) },
                true);
            _eq(1, old.OldCount, "un Old se cuenta como old");
            _eq(0, old.PartialCount, "y no se confunde con partial");
        }

        private static void DegradationIsReported()
        {
            _section("run: Complete que baja a Old o Partial es degradación");

            foreach (var to in new[] { RunVerification.Old, RunVerification.Partial })
            {
                var verdict = RunVerification.Classify(
                    new[] { State("t-1", "ARQ vs EST", RunVerification.Complete, results: 12) },
                    new[] { State("t-1", "ARQ vs EST", to, results: 12) },
                    invocationStarted: true);

                _eq(0, verdict.Verified, "Complete -> " + to + " deja de estar verificado");
                _check(Of(verdict, "t-1").Degraded, "y se marca como degradación");
                _check(Of(verdict, "t-1").Note.IndexOf("estaba Complete", StringComparison.Ordinal) >= 0,
                    "el detalle dice de dónde venía");
            }
        }

        private static void MissingAndUnexpected()
        {
            _section("run: un test que desaparece es missing; uno que aparece es unexpected");

            var verdict = RunVerification.Classify(
                new[] { State("t-1", "ARQ vs EST", RunVerification.Complete, results: 12) },
                new[] { State("t-9", "Otro", RunVerification.Complete, results: 4) },
                invocationStarted: true);

            _eq(1, verdict.MissingCount, "el que estaba y no está es missing");
            _eq(1, verdict.UnexpectedCount, "el que no estaba y está es unexpected");
            _eq(1, verdict.Requested, "requested cuenta lo solicitado, no lo encontrado");
            _eq(0, verdict.Verified, "un missing no está verificado");
            _eq("Otro", verdict.UnexpectedNames.Single(), "y el inesperado se nombra");
            _eq(RunVerification.Missing, Of(verdict, "t-1").Outcome, "clasificado como missing");
        }

        private static void UnexpectedDoesNotOffsetMissing()
        {
            _section("run: un test inesperado no compensa uno perdido");

            var verdict = RunVerification.Classify(
                new[]
                {
                    State("t-1", "ARQ vs EST", RunVerification.Complete, results: 12),
                    State("t-2", "EST vs MEP", RunVerification.New)
                },
                new[]
                {
                    State("t-2", "EST vs MEP", RunVerification.Complete, results: 5),
                    State("t-nuevo", "ARQ vs EST", RunVerification.Complete, results: 12)
                },
                invocationStarted: true);

            // The unexpected test even carries the missing one's NAME and its
            // result count. Matching on either would have called this clean.
            _eq(1, verdict.MissingCount, "sigue faltando uno");
            _eq(1, verdict.UnexpectedCount, "y sobrando otro");
            _eq(1, verdict.Verified, "solo el que sí se re-resolvió cuenta");
            _eq(2, verdict.Requested, "requested no se mueve");
            _eq(1, verdict.Failed, "failed = requested - verified");
            _eq(RunVerification.StatusPartial, verdict.Status, "y el run es partial");
        }

        private static void MixedRunIsPartial()
        {
            _section("run: 2 Complete + 1 Old + 1 Partial es partial, verified=2, failed=2");

            var verdict = RunVerification.Classify(
                new[]
                {
                    State("t-1", "ARQ vs EST", RunVerification.New),
                    State("t-2", "EST vs MEP", RunVerification.Complete, results: 8),
                    State("t-3", "MEP vs RCI", RunVerification.New),
                    State("t-4", "ARQ vs RCI", RunVerification.New)
                },
                new[]
                {
                    State("t-1", "ARQ vs EST", RunVerification.Complete, results: 12),
                    State("t-2", "EST vs MEP", RunVerification.Complete, results: 8),
                    State("t-3", "MEP vs RCI", RunVerification.Old, results: 40),
                    State("t-4", "ARQ vs RCI", RunVerification.Partial, results: 3)
                },
                invocationStarted: true);

            _eq(4, verdict.Requested, "requested=4");
            _eq(1, verdict.CompletedNowCount, "completed_now=1");
            _eq(1, verdict.AlreadyCompleteCount, "already_complete=1");
            _eq(2, verdict.Verified, "verified=2");
            _eq(2, verdict.Failed, "failed=2");
            _eq(1, verdict.OldCount, "old=1");
            _eq(1, verdict.PartialCount, "partial=1");
            _eq(RunVerification.StatusPartial, verdict.Status, "status=partial");
            _check(verdict.Status != RunVerification.StatusCompleted,
                "una corrida mixta NUNCA es completed");
        }

        private static void AllStaleIsFailed()
        {
            _section("run: todos Old o Partial es failed");

            var verdict = RunVerification.Classify(
                new[]
                {
                    State("t-1", "a", RunVerification.Complete, results: 10),
                    State("t-2", "b", RunVerification.New)
                },
                new[]
                {
                    State("t-1", "a", RunVerification.Old, results: 10),
                    State("t-2", "b", RunVerification.Partial, results: 2)
                },
                invocationStarted: true);

            _eq(0, verdict.Verified, "ninguno Complete");
            _eq(2, verdict.Failed, "los dos fallan");
            _eq(RunVerification.StatusFailed, verdict.Status, "status=failed");
        }

        private static void CountsDoNotDecideStatus()
        {
            _section("run: el número de choques no decide nada");

            // Complete with zero results is a correct run: two disciplines
            // that genuinely do not touch.
            var empty = RunVerification.Classify(
                new[] { State("t-1", "ARQ vs RCI", RunVerification.New) },
                new[] { State("t-1", "ARQ vs RCI", RunVerification.Complete, results: 0) },
                invocationStarted: true);
            _eq(1, empty.Verified, "Complete con cero choques está verificado");
            _eq(RunVerification.StatusCompleted, empty.Status, "y el run es completed");

            // Partial with hundreds of results is not.
            var loaded = RunVerification.Classify(
                new[] { State("t-1", "ARQ vs EST", RunVerification.New) },
                new[] { State("t-1", "ARQ vs EST", RunVerification.Partial, results: 847) },
                invocationStarted: true);
            _eq(0, loaded.Verified, "Partial con 847 choques no está verificado");
            _eq(RunVerification.StatusFailed, loaded.Status, "y el run falla");
        }

        private static void UnchangedResultsAreReported()
        {
            _section("run: resultados sin cambios se informan, no deciden");

            var complete = RunVerification.Classify(
                new[] { State("t-1", "a", RunVerification.Complete, results: 12) },
                new[] { State("t-1", "a", RunVerification.Complete, results: 12) },
                invocationStarted: true);
            _eq(1, complete.Verified, "Complete sin cambios sigue siendo válido");
            _check(Of(complete, "t-1").Note.IndexOf("sin cambios", StringComparison.Ordinal) >= 0,
                "pero se informa que no cambió nada");

            var partial = RunVerification.Classify(
                new[] { State("t-1", "a", RunVerification.Partial, results: 12) },
                new[] { State("t-1", "a", RunVerification.Partial, results: 12) },
                invocationStarted: true);
            _eq(0, partial.Verified, "Partial sin cambios sigue siendo inválido");
            _check(Of(partial, "t-1").Note.IndexOf("mismos resultados", StringComparison.Ordinal) >= 0,
                "y se dice que los resultados son los de antes");
        }

        private static void EnumerationOrderDoesNotMatter()
        {
            _section("run: cambiar el orden de enumeración no cambia el veredicto");

            var before = new[]
            {
                State("t-1", "a", RunVerification.New),
                State("t-2", "b", RunVerification.New),
                State("t-3", "c", RunVerification.New)
            };
            var shuffled = new[]
            {
                State("t-3", "c", RunVerification.Complete, results: 1),
                State("t-1", "a", RunVerification.Complete, results: 2),
                State("t-2", "b", RunVerification.Old, results: 3)
            };

            var verdict = RunVerification.Classify(before, shuffled, invocationStarted: true);
            _eq(2, verdict.Verified, "se emparejó por identidad, no por posición");
            _eq(1, verdict.OldCount, "y el Old es el que de verdad quedó Old");
            _eq(RunVerification.StillOld, Of(verdict, "t-2").Outcome, "t-2 es el Old");
            _eq(0, verdict.MissingCount, "ninguno se perdió por reordenar");
        }

        private static void SameNameDifferentGuid()
        {
            _section("run: dos tests con el mismo nombre y GUID distinto no se confunden");

            var verdict = RunVerification.Classify(
                new[]
                {
                    State("t-1", "ARQ vs EST", RunVerification.New),
                    State("t-2", "ARQ vs EST", RunVerification.New)
                },
                new[]
                {
                    State("t-1", "ARQ vs EST", RunVerification.Complete, results: 4),
                    State("t-2", "ARQ vs EST", RunVerification.Old, results: 9)
                },
                invocationStarted: true);

            _eq(2, verdict.Requested, "son dos tests, no uno");
            _eq(1, verdict.Verified, "solo uno llegó a Complete");
            _eq(1, verdict.OldCount, "el otro quedó Old");
            _eq(RunVerification.CompletedNow, Of(verdict, "t-1").Outcome, "y cada uno con su veredicto");
            _eq(RunVerification.StillOld, Of(verdict, "t-2").Outcome, "sin mezclarse por el nombre");
            _eq(0, verdict.MissingCount, "ninguno desapareció");
            _eq(0, verdict.UnexpectedCount, "ni sobró");
        }

        private static void NoTestsAtAll()
        {
            _section("run: cero tests no es una corrida exitosa");

            var verdict = RunVerification.Classify(
                new RunVerification.TestState[0],
                new RunVerification.TestState[0],
                invocationStarted: false);

            _eq(0, verdict.Requested, "no había nada que correr");
            _eq(0, verdict.Verified, "y nada se verificó");
            _eq(0, verdict.Failed, "tampoco falló nada");
            _eq(RunVerification.NothingToRun, verdict.Status,
                "el estado lo dice explícitamente en vez de fingir éxito");
            _check(verdict.Status != RunVerification.StatusCompleted,
                "no se declara completed sobre un documento sin tests");
            _check(!verdict.InvocationStarted, "y se declara que no se invocó nada");
        }

        private static void AnInvocationThatThrew()
        {
            _section("run: si TestsRunAllTests lanza, el veredicto sale de releer");

            // The call threw partway: some tests ran, some did not. The
            // classification does not depend on the exception at all — it
            // depends on what re-reading the document says.
            var verdict = RunVerification.Classify(
                new[]
                {
                    State("t-1", "a", RunVerification.New),
                    State("t-2", "b", RunVerification.New)
                },
                new[]
                {
                    State("t-1", "a", RunVerification.Complete, results: 6),
                    State("t-2", "b", RunVerification.New)
                },
                invocationStarted: true);

            _eq(1, verdict.Verified, "lo que sí quedó Complete se reconoce");
            _eq(1, verdict.NewCount, "y lo que no, también");
            _eq(RunVerification.StatusPartial, verdict.Status, "el run es partial, no failed total");

            // And an invocation that never started leaves everything as it was.
            var never = RunVerification.Classify(
                new[] { State("t-1", "a", RunVerification.Old, results: 5) },
                new[] { State("t-1", "a", RunVerification.Old, results: 5) },
                invocationStarted: false);
            _eq(RunVerification.StatusFailed, never.Status, "sin invocación no hay verificación");
            _check(!never.InvocationStarted, "y se declara que no se llegó a invocar");
        }

        private static void OnlyAllCompleteAllowsCompleted()
        {
            _section("run: solo todos Complete permiten completed");

            var all = RunVerification.Classify(
                new[]
                {
                    State("t-1", "a", RunVerification.New),
                    State("t-2", "b", RunVerification.Old, results: 3)
                },
                new[]
                {
                    State("t-1", "a", RunVerification.Complete, results: 1),
                    State("t-2", "b", RunVerification.Complete, results: 3)
                },
                invocationStarted: true);
            _eq(RunVerification.StatusCompleted, all.Status, "todos Complete");

            // One single non-Complete is enough to stop it.
            foreach (var spoiler in new[]
                     {
                         RunVerification.Old, RunVerification.Partial, RunVerification.New
                     })
            {
                var spoiled = RunVerification.Classify(
                    new[]
                    {
                        State("t-1", "a", RunVerification.New),
                        State("t-2", "b", RunVerification.New)
                    },
                    new[]
                    {
                        State("t-1", "a", RunVerification.Complete, results: 1),
                        State("t-2", "b", spoiler, results: 3)
                    },
                    invocationStarted: true);
                _check(spoiled.Status != RunVerification.StatusCompleted,
                    "un solo " + spoiler + " impide completed");
                _eq(RunVerification.StatusPartial, spoiled.Status, "y lo deja en partial");
            }
        }

        private static void TheOldRuleWouldHaveAccepted()
        {
            _section("run: la regla anterior status != New habría aceptado Old y Partial");

            var before = new[]
            {
                State("t-1", "a", RunVerification.New),
                State("t-2", "b", RunVerification.New)
            };
            var after = new[]
            {
                State("t-1", "a", RunVerification.Old, results: 120),
                State("t-2", "b", RunVerification.Partial, results: 40)
            };

            // The rule being replaced, written out here so the difference is
            // an assertion and not a comment.
            var wouldHavePassed = after.Count(
                t => !string.Equals(t.Status, RunVerification.New, StringComparison.OrdinalIgnoreCase));
            _eq(2, wouldHavePassed, "la regla vieja habría contado los dos como corridos");

            var verdict = RunVerification.Classify(before, after, invocationStarted: true);
            _eq(0, verdict.Verified, "la nueva no cuenta ninguno");
            _eq(RunVerification.StatusFailed, verdict.Status, "y el run falla en vez de dar verde");
        }

        private static void TheInvariantsHold()
        {
            _section("run: las invariantes de conteo se sostienen");

            var cases = new List<RunVerification.RunVerdict>
            {
                RunVerification.Classify(
                    new[]
                    {
                        State("t-1", "a", RunVerification.New),
                        State("t-2", "b", RunVerification.Complete, results: 2),
                        State("t-3", "c", RunVerification.Partial, results: 9),
                        State("t-4", "d", RunVerification.Old)
                    },
                    new[]
                    {
                        State("t-1", "a", RunVerification.Complete, results: 1),
                        State("t-2", "b", RunVerification.Complete, results: 2),
                        State("t-3", "c", RunVerification.Old, results: 9),
                        State("t-5", "e", RunVerification.Complete)
                    },
                    invocationStarted: true),
                RunVerification.Classify(
                    new RunVerification.TestState[0], new RunVerification.TestState[0], false),
                RunVerification.Classify(
                    new[] { State("t-1", "a", RunVerification.New) },
                    new RunVerification.TestState[0],
                    invocationStarted: true)
            };

            foreach (var verdict in cases)
            {
                _eq(verdict.CompletedNowCount + verdict.AlreadyCompleteCount, verdict.Verified,
                    "verified = completed_now + already_complete");
                _eq(verdict.Requested - verdict.Verified, verdict.Failed,
                    "failed = requested - verified");
                _check(verdict.Verified >= 0 && verdict.Verified <= verdict.Requested,
                    "0 <= verified <= requested");
                _check(verdict.Failed >= 0 && verdict.Failed <= verdict.Requested,
                    "0 <= failed <= requested");
                _eq(verdict.Requested, verdict.Tests.Count,
                    "hay exactamente un detalle por test solicitado");
                _eq(verdict.CompletedNowCount + verdict.AlreadyCompleteCount + verdict.OldCount +
                    verdict.PartialCount + verdict.NewCount + verdict.MissingCount,
                    verdict.Requested,
                    "cada test solicitado cae en exactamente una categoría");
            }
        }

        // ------------------------------------------------- identidad estricta

        private static void RenameIsFollowedByIdentity()
        {
            _section("run: si cambia el nombre pero no el GUID, se sigue la identidad");

            var verdict = RunVerification.Classify(
                new[] { State("t-1", "ARQ vs EST", RunVerification.New) },
                new[] { State("t-1", "ARQ vs EST v2", RunVerification.Complete, results: 9) },
                invocationStarted: true);

            _eq(1, verdict.Verified, "el renombrado se relaciona igual: manda el GUID");
            _eq(1, verdict.RenamedCount, "y se cuenta como renombrado");
            _eq(0, verdict.MissingCount, "no se perdió nadie");
            _eq(0, verdict.UnexpectedCount, "ni sobró nadie");

            var row = Of(verdict, "t-1");
            _check(row.Renamed, "la fila lo marca");
            _eq("ARQ vs EST", row.NameBefore, "y conserva el nombre anterior");
            _eq("ARQ vs EST v2", row.NameAfter, "junto al nuevo, para diagnóstico");
        }

        private static void RecreatedWithSameNameIsNotTheSameTest()
        {
            _section("run: mismo nombre y GUID distinto es missing + unexpected");

            var verdict = RunVerification.Classify(
                new[] { State("viejo", "ARQ vs EST", RunVerification.Partial, results: 40) },
                new[] { State("nuevo", "ARQ vs EST", RunVerification.Complete, results: 40) },
                invocationStarted: true);

            _eq(1, verdict.MissingCount, "el solicitado falta");
            _eq(1, verdict.UnexpectedCount, "y el que aparece es inesperado");
            _eq(1, verdict.RecreatedSameNameCount, "se reconoce que reapareció el nombre");
            _eq(0, verdict.Verified, "un homónimo Complete NO cubre al solicitado Partial");
            _check(verdict.Status != RunVerification.StatusCompleted, "y el run no es completed");
            _check(verdict.IdentityConflicts.Any(c =>
                    Convert.ToString(c["reason"]).IndexOf("otra identidad", StringComparison.Ordinal) >= 0),
                "y se explica que la identidad es otra");
        }

        private static void MissingIdentityBeforeIsUnverifiable()
        {
            _section("run: sin GUID antes de correr, identidad no verificable");

            var verdict = RunVerification.Classify(
                new[] { Anonymous("ARQ vs EST", RunVerification.New) },
                new[] { State("t-1", "ARQ vs EST", RunVerification.Complete, results: 9) },
                invocationStarted: true);

            _eq(1, verdict.UnverifiableIdentityCount, "se clasifica como identidad no verificable");
            _eq(0, verdict.Verified, "y no cuenta como verificado");
            _eq(1, verdict.Failed, "failed = requested - verified");
            _eq(RunVerification.StatusFailed, verdict.Status, "el run no puede ser completed");
            _eq(RunVerification.UnverifiableIdentity, verdict.Tests[0].Outcome, "con su propio código");
            _check(verdict.Tests[0].Note.IndexOf("identidad estable", StringComparison.Ordinal) >= 0,
                "y se explica que no se pudo demostrar que fuera el mismo objeto");

            // The homonymous test that DID appear is unexpected, never a match.
            _eq(1, verdict.UnexpectedCount, "el homónimo posterior no lo sustituye");
        }

        private static void MissingIdentityAfterIsNotAMatch()
        {
            _section("run: sin GUID después de correr, no hay relación posible");

            var verdict = RunVerification.Classify(
                new[] { State("t-1", "ARQ vs EST", RunVerification.New) },
                new[] { Anonymous("ARQ vs EST", RunVerification.Complete, results: 9) },
                invocationStarted: true);

            _eq(1, verdict.MissingCount, "el solicitado no se pudo re-resolver");
            _eq(0, verdict.Verified, "aunque haya un Complete con su mismo nombre");
            _eq(1, verdict.UnexpectedCount, "que se reporta como inesperado");
            _eq(RunVerification.StatusFailed, verdict.Status, "y el run falla");
        }

        private static void DuplicateGuidBeforeIsAConflict()
        {
            _section("run: GUID duplicado antes de correr es conflicto de identidad");

            var verdict = RunVerification.Classify(
                new[]
                {
                    State("dup", "ARQ vs EST", RunVerification.New),
                    State("dup", "EST vs MEP", RunVerification.New)
                },
                new[] { State("dup", "ARQ vs EST", RunVerification.Complete, results: 4) },
                invocationStarted: true);

            _eq(2, verdict.IdentityConflictCount, "los dos quedan en conflicto");
            _eq(0, verdict.Verified, "ninguno cuenta como verificado");
            _eq(2, verdict.Failed, "los dos cuentan como fallo");
            _check(verdict.DuplicateGuidCount > 0, "se reporta el GUID duplicado");
            _check(verdict.Status != RunVerification.StatusCompleted, "y el run no es completed");
            _check(verdict.IdentityConflicts.Any(c =>
                    Convert.ToString(c["reason"]).IndexOf("duplicado", StringComparison.Ordinal) >= 0),
                "con el motivo en identity_conflicts");
        }

        private static void DuplicateGuidAfterIsAConflict()
        {
            _section("run: GUID duplicado después de correr también es conflicto");

            var verdict = RunVerification.Classify(
                new[] { State("dup", "ARQ vs EST", RunVerification.New) },
                new[]
                {
                    State("dup", "ARQ vs EST", RunVerification.Complete, results: 4),
                    State("dup", "ARQ vs EST (copia)", RunVerification.Complete, results: 4)
                },
                invocationStarted: true);

            _eq(1, verdict.IdentityConflictCount, "el solicitado queda ambiguo");
            _eq(1, verdict.AmbiguousCandidateCount, "con más de un candidato");
            _eq(0, verdict.Verified, "y no se elige uno al azar para dar por bueno");
            _eq(RunVerification.StatusFailed, verdict.Status, "el run falla");
        }

        private static void AmbiguityNeverCompletes()
        {
            _section("run: con identidad ambigua nunca hay completed");

            // Everything else is perfect: both tests ended Complete.
            var verdict = RunVerification.Classify(
                new[]
                {
                    State("t-1", "a", RunVerification.New),
                    Anonymous("b", RunVerification.New)
                },
                new[]
                {
                    State("t-1", "a", RunVerification.Complete, results: 2),
                    State("t-2", "b", RunVerification.Complete, results: 2)
                },
                invocationStarted: true);

            _eq(1, verdict.Verified, "el que sí tiene identidad se verifica");
            _eq(1, verdict.UnverifiableIdentityCount, "el otro no");
            _eq(RunVerification.StatusPartial, verdict.Status,
                "y el run es partial pese a que los dos terminaron Complete");
        }

        private static void ReversedOrderWithRepeatedNames()
        {
            _section("run: orden invertido y nombres repetidos no se cruzan");

            var verdict = RunVerification.Classify(
                new[]
                {
                    State("a", "ARQ vs EST", RunVerification.New),
                    State("b", "ARQ vs EST", RunVerification.Complete, results: 7)
                },
                new[]
                {
                    State("b", "ARQ vs EST", RunVerification.Old, results: 7),
                    State("a", "ARQ vs EST", RunVerification.Complete, results: 3)
                },
                invocationStarted: true);

            _eq(2, verdict.Requested, "dos tests homónimos siguen siendo dos");
            _eq(1, verdict.Verified, "solo uno quedó Complete");
            _eq(1, verdict.OldCount, "y el otro Old");
            _eq(RunVerification.CompletedNow, Of(verdict, "a").Outcome, "'a' pasó a Complete");
            _eq(RunVerification.StillOld, Of(verdict, "b").Outcome, "'b' se degradó a Old");
            _check(Of(verdict, "b").Degraded, "y se marca la degradación");
            _eq(RunVerification.StatusPartial, verdict.Status, "el run es partial");
        }

        private static void IdentityCategoriesPartitionRequested()
        {
            _section("run: identidad no verificable y conflictos entran en el reparto");

            var verdict = RunVerification.Classify(
                new[]
                {
                    State("ok", "a", RunVerification.New),
                    Anonymous("b", RunVerification.New),
                    State("dup", "c", RunVerification.New),
                    State("dup", "d", RunVerification.New),
                    State("ido", "e", RunVerification.New)
                },
                new[]
                {
                    State("ok", "a", RunVerification.Complete, results: 1),
                    State("dup", "c", RunVerification.Complete, results: 1)
                },
                invocationStarted: true);

            var sum = verdict.CompletedNowCount + verdict.AlreadyCompleteCount +
                      verdict.OldCount + verdict.PartialCount + verdict.NewCount +
                      verdict.MissingCount + verdict.UnverifiableIdentityCount +
                      verdict.IdentityConflictCount;
            _eq(verdict.Requested, sum, "cada test solicitado cae en exactamente una categoría");
            _eq(verdict.Requested - verdict.Verified, verdict.Failed,
                "failed = requested - verified, con identidad incluida");
            _eq(1, verdict.Verified, "solo uno se pudo demostrar");
            _eq(1, verdict.UnverifiableIdentityCount, "uno sin identidad");
            _eq(2, verdict.IdentityConflictCount, "dos en conflicto");
            _eq(1, verdict.MissingCount, "uno desaparecido");
        }

        private static void NoProductionPathUsesNameAsIdentity()
        {
            _section("run: ningún camino usa DisplayName como identidad");

            // The behavioural guarantee is everything above. This is the
            // secondary lock: the shape that made the bug possible must not
            // exist in the source at all, because a fallback added back later
            // would pass every behavioural test that does not happen to cover
            // the case it reintroduces.
            var source = FindAddinSource("RunVerification.cs");
            _check(source != null, "se encontró RunVerification.cs");
            if (source == null) return;

            var text = System.IO.File.ReadAllText(source);
            _check(text.IndexOf("Key =>", StringComparison.Ordinal) < 0,
                "no queda una propiedad Key que pueda caer al nombre");
            _check(text.IndexOf("\"name:\"", StringComparison.Ordinal) < 0,
                "ni un prefijo de nombre usado como identidad");
            _check(text.IndexOf("Identity => HasIdentity ? Guid : string.Empty",
                    StringComparison.Ordinal) > 0,
                "la identidad es el GUID o nada");

            // And no dictionary keyed on the display name of the AFTER list.
            var classify = text.Substring(text.IndexOf("public static RunVerdict Classify",
                StringComparison.Ordinal));
            _check(classify.IndexOf("byName", StringComparison.Ordinal) < 0,
                "no existe un índice posterior por nombre");
            _check(classify.IndexOf("byIdentity", StringComparison.Ordinal) > 0,
                "el único índice posterior es por identidad");
        }

        /// <summary>The repository copy of an add-in source file, or null.</summary>
        private static string FindAddinSource(string name)
        {
            var dir = new System.IO.DirectoryInfo(AppContext.BaseDirectory);
            while (dir != null)
            {
                var candidate = System.IO.Path.Combine(
                    dir.FullName, "addin", "NavisCoord.Addin", name);
                if (System.IO.File.Exists(candidate)) return candidate;
                dir = dir.Parent;
            }
            return null;
        }
    }
}
