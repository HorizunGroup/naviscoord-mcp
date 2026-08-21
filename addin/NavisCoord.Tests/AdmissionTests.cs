using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;

namespace NavisCoord.Tests
{
    /// <summary>
    /// Admission is one indivisible step, and a mutation's reply has to be an
    /// envelope that survives inspection.
    /// </summary>
    /// <remarks>
    /// Two defects, one subsystem. The bridge used to look up the ledger, look
    /// up the in-flight key, ask whether it would collide and then submit —
    /// four calls with three gaps, and two POSTs arriving together could walk
    /// through all of them before either reserved anything. And the job
    /// manager read an unrecognised reply as success as long as it carried no
    /// <c>error</c> key, so every route that had never adopted the envelope
    /// reported completed by staying silent.
    ///
    /// The concurrency here is deterministic: barriers, not sleeps. A test
    /// that races by timing passes on a fast machine and tells you nothing.
    /// </remarks>
    internal static class AdmissionTests
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

            CanonicalHashIgnoresKeyOrder();
            ConcurrentSubmissionsProduceOneJob();
            TheRaceRepeats();
            SameKeyDifferentIntentIsConflict();
            EmptyKeyDoesNotDeduplicate();
            PendingBlocksASecondMutation();
            RunningBlocksASecondMutation();
            CancelReleasesPendingButNotRunning();
            AFailedHandlerReleasesTheDocument();
            NoPhantomReservations();
            ARetryAfterCompletionReplays();
            FingerprintIsRequired();
            DocumentChangeBlocksExecution();
            StopCancelsPendingHonestly();

            PendingAndRunningLiveOnlyInFlight();
            EveryTerminalStateReachesTheLedger();
            CancelledPendingLeavesNoReservation();
            TheLedgerEvictsInOrder();
            TenThousandJobsStayBounded();
            IntentCoversProfileAndFlags();
            TheLedgerDoesNotKeepTheWholeResult();
            ConcurrentTerminalsDoNotCorruptTheLedger();
            DiagnosticsExposeNoPayload();
            StopCancelsEveryPending();
            ACancelledPendingNeverExecutes();
            ProfileIntegrityIsUnchangedByARun();

            EnvelopeRejectsSilence();
            EnvelopeRejectsMissingVerification();
            EnvelopeGradesPartial();
            EnvelopeRejectsUnknownStatus();
            DryRunIsPlannedOnly();
            ReadOnlyNeedsNoEnvelope();
            JobNeverUpgradesPartial();
            NegativeAndIncoherentCountsAreRejected();
            CancelledPendingReportsNothingApplied();
            EveryMutatingRouteHasAContract();
            CapabilitiesMatchTheContracts();
        }

        // ------------------------------------------------------------ helpers

        private static Dictionary<string, object> Envelope(
            int requested = 1, int applied = 1, int verified = 1,
            string status = null, string source = "document_reread",
            bool dryRun = false, int blocked = 0)
        {
            var body = new Dictionary<string, object>
            {
                ["dry_run"] = dryRun,
                ["requested"] = (double)requested,
                ["applied"] = (double)applied,
                ["verified"] = (double)verified,
                ["failed"] = (double)Math.Max(0, applied - verified),
                ["blocked"] = (double)blocked,
                ["verification_source"] = source,
                ["document_fingerprint_before"] = "fp-1",
                ["document_fingerprint_after"] = "fp-1"
            };
            body["status"] = status ?? (dryRun
                ? "planned"
                : verified >= requested && blocked == 0 ? "completed" : "partial");
            return body;
        }

        private static JobRequest Request(
            string route = "workflow/rules",
            string key = "",
            string fingerprint = "fp-1",
            Dictionary<string, object> payload = null,
            string profileChecksum = "chk-a",
            string jobId = null)
            => new JobRequest(
                jobId: jobId ?? "job-" + Guid.NewGuid().ToString("N").Substring(0, 10),
                route: route,
                payload: payload ?? new Dictionary<string, object> { ["a"] = 1.0 },
                expectedFingerprint: fingerprint,
                idempotencyKey: key,
                profileChecksum: profileChecksum);

        private static void WaitUntil(Func<bool> condition, int millis)
        {
            var until = DateTime.UtcNow.AddMilliseconds(millis);
            while (DateTime.UtcNow < until)
            {
                if (condition()) return;
                Thread.Sleep(5);
            }
        }

        // -------------------------------------------------------- block B

        private static void CanonicalHashIgnoresKeyOrder()
        {
            _section("admisión: el hash del payload no depende del orden de las claves");

            var one = new Dictionary<string, object>
            {
                ["zeta"] = 1.0,
                ["alpha"] = new Dictionary<string, object> { ["y"] = true, ["x"] = "s" },
                ["lista"] = new List<object> { 1.0, 2.0 }
            };
            var two = new Dictionary<string, object>
            {
                ["lista"] = new List<object> { 1.0, 2.0 },
                ["alpha"] = new Dictionary<string, object> { ["x"] = "s", ["y"] = true },
                ["zeta"] = 1.0
            };
            _eq(CanonicalPayload.Hash(one), CanonicalPayload.Hash(two),
                "dos payloads iguales con las claves en otro orden hashean igual");

            // Order inside a LIST is meaning, not presentation.
            var reordered = new Dictionary<string, object>
            {
                ["zeta"] = 1.0,
                ["alpha"] = new Dictionary<string, object> { ["x"] = "s", ["y"] = true },
                ["lista"] = new List<object> { 2.0, 1.0 }
            };
            _check(CanonicalPayload.Hash(one) != CanonicalPayload.Hash(reordered),
                "pero cambiar el orden de una lista sí cambia el hash");

            var different = new Dictionary<string, object> { ["zeta"] = 2.0 };
            _check(CanonicalPayload.Hash(one) != CanonicalPayload.Hash(different),
                "y un valor distinto también");
        }

        private static void ConcurrentSubmissionsProduceOneJob()
        {
            _section("admisión: dos envíos simultáneos con la misma clave dan un solo trabajo");

            JobManager.ResetForTests();
            var release = new ManualResetEventSlim(false);
            var payload = new Dictionary<string, object> { ["x"] = 1.0 };

            var start = new Barrier(2);
            var results = new AdmissionResult[2];
            var threads = Enumerable.Range(0, 2).Select(i => new Thread(() =>
            {
                var request = Request(key: "k-1", payload: payload);
                start.SignalAndWait();
                results[i] = JobManager.TrySubmit(request, _ =>
                {
                    release.Wait(3000);
                    return Envelope();
                });
            })).ToList();

            foreach (var t in threads) t.Start();
            foreach (var t in threads) t.Join(5000);

            var accepted = results.Count(r => r.Outcome == Admission.NewSubmission);
            var deduped = results.Count(r => r.Outcome == Admission.DeduplicatedInFlight);
            _eq(1, accepted, "exactamente uno se acepta");
            _eq(1, deduped, "y el otro recibe el mismo trabajo");
            _eq(results[0].JobId, results[1].JobId, "los dos devuelven el MISMO job_id");
            _eq(1, JobManager.All().Count, "y solo existe un trabajo");

            release.Set();
            WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 4000);
        }

        private static void TheRaceRepeats()
        {
            _section("admisión: cien carreras y nunca dos trabajos");

            var duplicates = 0;
            for (var round = 0; round < 100; round++)
            {
                JobManager.ResetForTests();
                var payload = new Dictionary<string, object> { ["round"] = (double)round };
                var start = new Barrier(2);
                var ids = new string[2];

                var threads = Enumerable.Range(0, 2).Select(i => new Thread(() =>
                {
                    var request = Request(key: "race-" + round, payload: payload);
                    start.SignalAndWait();
                    ids[i] = JobManager.TrySubmit(request, _ => Envelope()).JobId;
                })).ToList();

                foreach (var t in threads) t.Start();
                foreach (var t in threads) t.Join(5000);

                if (JobManager.All().Count != 1 || ids[0] != ids[1]) duplicates++;
                WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 2000);
            }
            _eq(0, duplicates, "cero rondas produjeron dos trabajos para la misma operación");
        }

        private static void SameKeyDifferentIntentIsConflict()
        {
            _section("admisión: misma clave con otra intención es conflicto");

            foreach (var variant in new[] { "payload", "route", "fingerprint" })
            {
                JobManager.ResetForTests();
                var hold = new ManualResetEventSlim(false);
                var first = JobManager.TrySubmit(
                    Request(key: "k-dup"), _ => { hold.Wait(2000); return Envelope(); });
                _eq(Admission.NewSubmission, first.Outcome, "el primero entra (" + variant + ")");

                var second = JobManager.TrySubmit(
                    variant == "payload"
                        ? Request(key: "k-dup", payload: new Dictionary<string, object> { ["a"] = 2.0 })
                        : variant == "route"
                            ? Request(route: "workflow/group_levels", key: "k-dup")
                            : Request(key: "k-dup", fingerprint: "fp-otro"),
                    _ => Envelope());

                _eq(Admission.IdempotencyConflict, second.Outcome,
                    "cambiar el " + variant + " con la misma clave es conflicto");
                _eq(1, JobManager.All().Count, "y no se creó un segundo trabajo");
                hold.Set();
                WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 3000);
            }
        }

        private static void EmptyKeyDoesNotDeduplicate()
        {
            _section("admisión: la clave vacía no deduplica pero sí respeta el documento");

            JobManager.ResetForTests();
            var hold = new ManualResetEventSlim(false);
            var first = JobManager.TrySubmit(Request(key: ""), _ => { hold.Wait(2000); return Envelope(); });
            _eq(Admission.NewSubmission, first.Outcome, "el primero entra");

            var second = JobManager.TrySubmit(Request(key: ""), _ => Envelope());
            _eq(Admission.DocumentBusy, second.Outcome,
                "sin clave no hay deduplicación, pero el documento sigue ocupado");
            _check(second.Outcome != Admission.IdempotencyConflict,
                "y no se reporta como conflicto de clave: no había clave");

            hold.Set();
            WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 3000);
        }

        private static void PendingBlocksASecondMutation()
        {
            _section("admisión: un trabajo pendiente también bloquea el segundo");

            JobManager.ResetForTests();
            var hold = new ManualResetEventSlim(false);

            // The first occupies the worker; the second is queued; the third
            // must be refused. Under the old check only the RUNNING job was
            // consulted, so the third was accepted and queued behind two
            // mutations over one document.
            JobManager.TrySubmit(Request(key: "p-1"), _ => { hold.Wait(3000); return Envelope(); });
            WaitUntil(() => JobManager.Active() != null, 2000);

            var second = JobManager.TrySubmit(Request(key: "p-2"), _ => Envelope());
            _eq(Admission.DocumentBusy, second.Outcome, "el segundo se rechaza");
            _check(second.Detail.IndexOf("mutación aceptada", StringComparison.Ordinal) >= 0,
                "y se dice que ya hay una aceptada, no solo una corriendo");

            hold.Set();
            WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 4000);

            var third = JobManager.TrySubmit(Request(key: "p-3"), _ => Envelope());
            _eq(Admission.NewSubmission, third.Outcome, "cuando termina, el documento vuelve a estar libre");
            WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 3000);
        }

        private static void RunningBlocksASecondMutation()
        {
            _section("admisión: dos claves distintas sobre el mismo documento no coexisten");

            JobManager.ResetForTests();
            var hold = new ManualResetEventSlim(false);
            var a = JobManager.TrySubmit(Request(key: "a"), _ => { hold.Wait(3000); return Envelope(); });
            WaitUntil(() => JobManager.Active() != null, 2000);

            var b = JobManager.TrySubmit(Request(route: "workflow/run", key: "b"), _ => Envelope());
            _eq(Admission.NewSubmission, a.Outcome, "uno aceptado");
            _eq(Admission.DocumentBusy, b.Outcome, "el otro ocupado, aunque la ruta y la clave difieran");

            hold.Set();
            WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 4000);
        }

        private static void CancelReleasesPendingButNotRunning()
        {
            _section("admisión: cancelar en cola libera; cancelar en curso no");

            JobManager.ResetForTests();
            var hold = new ManualResetEventSlim(false);
            var running = JobManager.TrySubmit(
                Request(key: "r-1"), _ => { hold.Wait(3000); return Envelope(); });
            WaitUntil(() => JobManager.Active() != null, 2000);

            // Cancelling the RUNNING job must not hand the document to anyone:
            // the code that owns it can still be inside an API call.
            JobManager.Cancel(running.JobId);
            _check(JobManager.HeldDocumentReservations().Contains(running.JobId),
                "un trabajo en curso conserva la reserva aunque se pida cancelarlo");

            hold.Set();
            WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 4000);
            _eq(0, JobManager.HeldDocumentReservations().Count,
                "y la suelta al terminar de verdad");

            // A job cancelled while queued never touched anything, so it gives
            // the document back immediately.
            JobManager.ResetForTests();
            var gate = new ManualResetEventSlim(false);
            var first = JobManager.TrySubmit(
                Request(key: "q-1"), _ => { gate.Wait(3000); return Envelope(); });
            WaitUntil(() => JobManager.Active() != null, 2000);
            var queued = JobManager.Get(first.JobId);
            gate.Set();
            WaitUntil(() => queued.FinishedUtc.HasValue, 3000);
            _eq(0, JobManager.HeldDocumentReservations().Count, "sin reservas colgando");
        }

        private static void AFailedHandlerReleasesTheDocument()
        {
            _section("admisión: una excepción del handler libera la reserva");

            JobManager.ResetForTests();
            var boom = JobManager.TrySubmit(
                Request(key: "x-1"),
                _ => throw new InvalidOperationException("revienta"));
            WaitUntil(() => JobManager.Get(boom.JobId)?.FinishedUtc.HasValue == true, 3000);

            _eq(JobManager.Failed, JobManager.Get(boom.JobId).State, "el trabajo queda failed");
            _eq(0, JobManager.HeldDocumentReservations().Count,
                "y el documento vuelve a estar libre pese a la excepción");

            var next = JobManager.TrySubmit(Request(key: "x-2"), _ => Envelope());
            _eq(Admission.NewSubmission, next.Outcome, "así que el siguiente entra");
            WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 3000);
        }

        private static void NoPhantomReservations()
        {
            _section("admisión: la tabla interna no conserva reservas fantasma");

            JobManager.ResetForTests();
            for (var i = 0; i < 20; i++)
            {
                var outcome = JobManager.TrySubmit(
                    Request(key: "n-" + i, payload: new Dictionary<string, object> { ["i"] = (double)i }),
                    _ => i % 3 == 0 ? throw new InvalidOperationException("falla " + i) : Envelope());
                _eq(Admission.NewSubmission, outcome.Outcome, "el envío " + i + " entra");
                WaitUntil(() => JobManager.Get(outcome.JobId)?.FinishedUtc.HasValue == true, 3000);
            }
            _eq(0, JobManager.HeldDocumentReservations().Count,
                "después de 20 trabajos, con fallos incluidos, no queda ninguna reserva");
        }

        private static void ARetryAfterCompletionReplays()
        {
            _section("admisión: reintentar tras completar devuelve el resultado guardado");

            JobManager.ResetForTests();
            var first = JobManager.TrySubmit(Request(key: "replay-1"), _ => Envelope(verified: 1));
            WaitUntil(() => JobManager.Get(first.JobId)?.FinishedUtc.HasValue == true, 3000);

            var again = JobManager.TrySubmit(Request(key: "replay-1"), _ => Envelope());
            _eq(Admission.ReplayedFromLedger, again.Outcome, "el reintento no crea otro trabajo");
            _eq(first.JobId, again.JobId, "y apunta al mismo trabajo");
            _check(again.Replay != null, "con el resultado que ya se produjo");
            _eq(1, JobManager.All().Count, "sigue habiendo un solo trabajo");
        }

        private static void FingerprintIsRequired()
        {
            _section("admisión: una mutación sin fingerprint no se acepta");

            JobManager.ResetForTests();
            var outcome = JobManager.TrySubmit(Request(key: "f-1", fingerprint: ""), _ => Envelope());
            _eq(Admission.FingerprintRequired, outcome.Outcome, "se rechaza");
            _eq(0, JobManager.All().Count, "y no queda ningún trabajo creado");
        }

        private static void DocumentChangeBlocksExecution()
        {
            _section("admisión: si el documento cambia mientras espera, no se ejecuta");

            var request = Request(key: "d-1", fingerprint: "fp-1");

            var blocker = JobPreflight.Blocker(request, "fp-2", "chk-a");
            _check(blocker != null, "el preflight bloquea");
            _eq("failed", blocker["status"], "status=failed");
            _eq(Admission.DocumentChangedBeforeExecution, blocker["error"], "con el código correcto");
            _eq(0.0, blocker["applied"], "applied=0");
            _eq(0.0, blocker["verified"], "verified=0");
            _check(EnvelopeContract.IsValid(request.Route, blocker),
                "y el bloqueo es un envelope válido, no una respuesta suelta");

            _check(JobPreflight.Blocker(request, "fp-1", "chk-a") == null,
                "con el mismo documento no bloquea nada");

            // And the handler is genuinely never reached.
            JobManager.ResetForTests();
            var invoked = false;
            var job = JobManager.TrySubmit(request, j =>
            {
                var stop = JobPreflight.Blocker(request, "fp-2", "chk-a");
                if (stop != null) return stop;
                invoked = true;
                return Envelope();
            });
            WaitUntil(() => JobManager.Get(job.JobId)?.FinishedUtc.HasValue == true, 3000);
            _check(!invoked, "el handler nunca se invocó");
            _eq(JobManager.Failed, JobManager.Get(job.JobId).State, "y el trabajo quedó failed");
        }

        private static void StopCancelsPendingHonestly()
        {
            _section("admisión: detener el puente cancela lo pendiente y no miente sobre lo que corre");

            JobManager.ResetForTests();
            var hold = new ManualResetEventSlim(false);
            var running = JobManager.TrySubmit(
                Request(key: "s-1"), _ => { hold.Wait(3000); return Envelope(); });
            WaitUntil(() => JobManager.Active() != null, 2000);

            var report = JobManager.StopAndDrain();
            _eq(running.JobId, report["still_running"], "se nombra el que sigue corriendo");
            _eq(false, report["running_stopped"], "y NO se afirma que se detuvo");

            hold.Set();
            WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 4000);
        }

        // -------------------------------------------------------- block C

        private static void EnvelopeRejectsSilence()
        {
            _section("envelope: una mutación sin status es un fallo de protocolo");

            var silent = new Dictionary<string, object> { ["ok"] = true, ["action"] = "build_sets" };
            var problems = EnvelopeContract.Problems("sets/build", silent);
            _check(problems.Count > 0, "callarse no es aprobar");
            _check(problems[0].IndexOf("status", StringComparison.Ordinal) >= 0, "y se dice qué falta");

            JobManager.ResetForTests();
            var job = JobManager.TrySubmit(Request(route: "sets/build", key: "e-1"), _ => silent);
            WaitUntil(() => JobManager.Get(job.JobId)?.FinishedUtc.HasValue == true, 3000);
            var finished = JobManager.Get(job.JobId);
            _eq(JobManager.Failed, finished.State, "el trabajo queda failed, no completed");
            _eq(EnvelopeContract.InvalidMutationResult, finished.Error["type"], "con el código del contrato");
            _check(finished.Error.ContainsKey("raw_result"),
                "la respuesta cruda se conserva solo para diagnóstico");
        }

        private static void EnvelopeRejectsMissingVerification()
        {
            _section("envelope: sin verified o sin verification_source no hay completed");

            var noVerified = Envelope();
            noVerified.Remove("verified");
            _check(!EnvelopeContract.IsValid("clash/matrix", noVerified), "falta 'verified'");

            var noSource = Envelope();
            noSource["verification_source"] = "";
            var problems = EnvelopeContract.Problems("clash/matrix", noSource);
            _check(problems.Count > 0, "verification_source vacío impide completed");
            _check(problems.Any(p => p.IndexOf("verification_source", StringComparison.Ordinal) >= 0),
                "y se dice por qué");
        }

        private static void EnvelopeGradesPartial()
        {
            _section("envelope: verified < applied y applied < requested son partial");

            var less = Envelope(requested: 10, applied: 10, verified: 4, status: "partial");
            _check(EnvelopeContract.IsValid("clash/group", less), "verified<applied como partial es válido");

            var lying = Envelope(requested: 10, applied: 10, verified: 4, status: "completed");
            _check(!EnvelopeContract.IsValid("clash/group", lying),
                "…pero declararlo completed no lo es");

            var short_ = Envelope(requested: 10, applied: 4, verified: 4, status: "completed");
            _check(!EnvelopeContract.IsValid("clash/group", short_),
                "applied < requested tampoco puede ser completed");

            var blocked = Envelope(requested: 3, applied: 3, verified: 3, status: "completed", blocked: 1);
            _check(!EnvelopeContract.IsValid("sets/build_search", blocked),
                "unidades bloqueadas impiden completed");
            _check(EnvelopeContract.IsValid(
                    "sets/build_search",
                    Envelope(requested: 3, applied: 3, verified: 3, status: "partial", blocked: 1)),
                "declararlo partial sí es válido");
        }

        private static void EnvelopeRejectsUnknownStatus()
        {
            _section("envelope: un status desconocido falla explícitamente");

            var weird = Envelope();
            weird["status"] = "casi";
            var problems = EnvelopeContract.Problems("clash/run", weird);
            _check(problems.Count > 0, "no se acepta");
            _check(problems[0].IndexOf("desconocido", StringComparison.Ordinal) >= 0,
                "y se dice que no se reconoce, en vez de asumir éxito");
        }

        private static void DryRunIsPlannedOnly()
        {
            _section("envelope: dry_run es planned, y planned es solo dry_run");

            _check(EnvelopeContract.IsValid(
                    "sets/build_search",
                    Envelope(requested: 4, applied: 0, verified: 0,
                        status: "planned", source: "not_applicable", dryRun: true)),
                "un ensayo declara planned");

            var lying = Envelope(requested: 4, applied: 0, verified: 0,
                status: "completed", dryRun: true);
            _check(!EnvelopeContract.IsValid("sets/build_search", lying),
                "un dry_run que dice completed se rechaza");

            var realPlanned = Envelope(status: "planned");
            _check(!EnvelopeContract.IsValid("sets/build_search", realPlanned),
                "y una corrida real no puede declararse planned");
        }

        private static void ReadOnlyNeedsNoEnvelope()
        {
            _section("envelope: una ruta de solo lectura sigue respondiendo normal");

            var plain = new Dictionary<string, object> { ["tests"] = new List<object>() };
            _check(EnvelopeContract.IsValid("clash/tests", plain),
                "clash/tests no debe un envelope");
            _check(EnvelopeContract.IsValid("census", plain), "census tampoco");
            _check(!EnvelopeContract.IsValid("clash/status", plain),
                "pero clash/status sí");
        }

        private static void JobNeverUpgradesPartial()
        {
            _section("envelope: el job manager no convierte partial en completed");

            JobManager.ResetForTests();
            var job = JobManager.TrySubmit(
                Request(route: "workflow/run", key: "p-keep"),
                _ => Envelope(requested: 4, applied: 4, verified: 2, status: "partial",
                    source: VerificationSources.ClashTestStatusReread));
            WaitUntil(() => JobManager.Get(job.JobId)?.FinishedUtc.HasValue == true, 3000);

            var finished = JobManager.Get(job.JobId);
            _eq(JobManager.Partial, finished.State, "partial sigue siendo partial por vía job");
            _eq(4, finished.Requested, "y los conteos son los del envelope");
            _eq(2, finished.Verified, "verified=2");

            // Which is exactly the Run All shape the brief describes: 2
            // Complete, 1 Old, 1 Partial.
            var verdict = RunVerification.Classify(
                new[]
                {
                    new RunVerification.TestState { Guid = "00000000-0000-0000-0000-000000000001", Name = "a", Status = "New" },
                    new RunVerification.TestState { Guid = "00000000-0000-0000-0000-000000000002", Name = "b", Status = "Complete" },
                    new RunVerification.TestState { Guid = "00000000-0000-0000-0000-000000000003", Name = "c", Status = "New" },
                    new RunVerification.TestState { Guid = "00000000-0000-0000-0000-000000000004", Name = "d", Status = "New" }
                },
                new[]
                {
                    new RunVerification.TestState { Guid = "00000000-0000-0000-0000-000000000001", Name = "a", Status = "Complete" },
                    new RunVerification.TestState { Guid = "00000000-0000-0000-0000-000000000002", Name = "b", Status = "Complete" },
                    new RunVerification.TestState { Guid = "00000000-0000-0000-0000-000000000003", Name = "c", Status = "Old" },
                    new RunVerification.TestState { Guid = "00000000-0000-0000-0000-000000000004", Name = "d", Status = "Partial" }
                },
                invocationStarted: true);
            _eq(2, verdict.Verified, "Run All mixto: verified=2");
            _eq(2, verdict.Failed, "failed=2");
            _eq("partial", verdict.Status, "status=partial");
        }

        private static void NegativeAndIncoherentCountsAreRejected()
        {
            _section("envelope: conteos negativos o incoherentes se rechazan");

            // preserved and blocked are part of the envelope, not decoration:
            // a run that kept four sources and refused two is not complete,
            // and it is not a failure either.
            var kept = new MutationResult("sets/build_search")
            {
                Requested = 6, Applied = 6, Verified = 6,
                Preserved = 4, Blocked = 2,
                VerificationSource = VerificationSources.SavedItemReread,
                FingerprintBefore = "fp-1", FingerprintAfter = "fp-1"
            };
            var keptJson = kept.ToJson();
            _eq(4.0, keptJson["preserved"], "el envelope lleva lo conservado");
            _eq(2.0, keptJson["blocked"], "y lo bloqueado");
            _eq("partial", keptJson["status"], "con bloqueos no hay completed");
            _check(EnvelopeContract.IsValid("sets/build_search", keptJson),
                "y así declarado, el envelope es válido");

            var negative = Envelope();
            negative["verified"] = -1.0;
            _check(!EnvelopeContract.IsValid("clash/status", negative), "verified negativo se rechaza");

            var tooMuchApplied = Envelope(requested: 2, applied: 5, verified: 2, status: "partial");
            _check(!EnvelopeContract.IsValid("clash/status", tooMuchApplied),
                "applied > requested se rechaza");

            var tooMuchVerified = Envelope(requested: 5, applied: 2, verified: 4, status: "partial");
            _check(!EnvelopeContract.IsValid("clash/status", tooMuchVerified),
                "verified > applied se rechaza");

            var zero = Envelope(requested: 0, applied: 0, verified: 0, status: "completed");
            _check(EnvelopeContract.IsValid("clash/status", zero),
                "requested=0 con todo a cero es coherente y se representa tal cual");
        }

        private static void CancelledPendingReportsNothingApplied()
        {
            _section("envelope: un pendiente cancelado queda en applied=verified=0");

            JobManager.ResetForTests();
            var hold = new ManualResetEventSlim(false);
            var first = JobManager.TrySubmit(
                Request(key: "c-1"), _ => { hold.Wait(3000); return Envelope(); });
            WaitUntil(() => JobManager.Active() != null, 2000);

            // Queue a second by hand: the document is busy, so this uses the
            // legacy path purely to reach the Queued state under test.
            var queued = JobManager.Submit("workflow/rules", _ => Envelope());
            var cancelled = JobManager.Cancel(queued.Id);
            _eq(true, cancelled["cancelled"], "un pendiente sí se cancela");
            _eq(JobManager.Cancelled, queued.State, "queda cancelled");
            _eq(0, queued.Applied, "applied=0");
            _eq(0, queued.Verified, "verified=0");
            _check(!JobManager.HeldDocumentReservations().Contains(queued.Id),
                "y no retiene el documento");

            hold.Set();
            WaitUntil(() => JobManager.Get(first.JobId)?.FinishedUtc.HasValue == true, 4000);
        }

        private static void EveryMutatingRouteHasAContract()
        {
            _section("contratos: toda ruta mutante está clasificada");

            // The router's own table, read through the same names the bridge
            // dispatches on. A route added there without a contract fails here
            // rather than reaching production unclassified.
            var routed = new[]
            {
                "health", "census", "model/schema", "clash/tests", "clash/export", "clash/image",
                "sets/build", "sets/build_search", "sets/list", "clash/matrix", "clash/run",
                "clash/group", "clash/status", "clash/apply_rules", "viewpoints/save",
                "appearance/color", "appearance/reset", "selection/set",
                "workflow/audit_models", "workflow/configure", "workflow/run",
                "workflow/group_levels", "workflow/rules",
                "profile/info", "profile/load", "profile/reset",
                "document/save", "document/save_as", "document/close", "application/exit"
            };

            foreach (var route in routed)
            {
                _check(RouteContracts.For(route) != null, "la ruta '" + route + "' tiene contrato");
            }

            foreach (var contract in RouteContracts.All.Where(c => c.IsMutation))
            {
                _check(contract.RequiresEnvelope || contract.Name == "application/exit",
                    "'" + contract.Name + "' es mutante y exige envelope");
                _check(!string.IsNullOrEmpty(contract.VerificationSource),
                    "'" + contract.Name + "' declara cómo verifica");
                _check(contract.Reads == ReadClass.NotARead,
                    "'" + contract.Name + "' no se clasifica como lectura");
            }

            foreach (var contract in RouteContracts.All.Where(c => !c.IsMutation))
            {
                _check(!contract.RequiresEnvelope,
                    "'" + contract.Name + "' es lectura y no exige envelope");
                _check(contract.Reads != ReadClass.NotARead,
                    "'" + contract.Name + "' declara cómo se comporta durante una mutación");
            }

            // Appearance is the one that used to escape by being "only visual".
            _check(RouteContracts.IsMutation("appearance/color"), "colorear es una mutación");
            _check(RouteContracts.IsMutation("appearance/reset"), "resetear apariencia también");
            _check(RouteContracts.For("appearance/color").Persistent,
                "y es persistente: sobrevive a la sesión");

            // An unclassified route is refused, not treated leniently.
            _check(RouteContracts.For("ruta/inventada") == null,
                "una ruta sin clasificar no recibe un contrato permisivo por defecto");
            _check(EnvelopeContract.Problems("ruta/inventada", Envelope()).Count > 0,
                "y su respuesta no se puede validar");
        }

        private static void CapabilitiesMatchTheContracts()
        {
            _section("contratos: capabilities se deriva de la misma tabla");

            var manifest = Capabilities.Describe(
                "0.4.0", "Navisworks Manage 2026",
                RouteContracts.Names, true, null, RouteContracts.Cancellable);

            var mutations = ((List<object>)manifest["mutations"]).Cast<string>().ToList();
            _eq(RouteContracts.Mutations.Count(), mutations.Count,
                "las mutaciones publicadas son las de la tabla");
            foreach (var name in RouteContracts.Mutations)
            {
                _check(mutations.Contains(name), "'" + name + "' se publica como mutación");
            }

            var jobs = ((List<object>)manifest["job_routes"]).Cast<string>().ToList();
            _eq(RouteContracts.JobRoutes.Count(), jobs.Count, "y las rutas job también");

            var envelopes = ((List<object>)manifest["envelope_required"]).Cast<string>().ToList();
            foreach (var contract in RouteContracts.All.Where(c => c.RequiresEnvelope))
            {
                _check(envelopes.Contains(contract.Name),
                    "'" + contract.Name + "' se publica como que exige envelope");
            }

            var jobsBlock = manifest["jobs"] as Dictionary<string, object>;
            _check(jobsBlock != null, "el manifiesto trae el bloque de trabajos");
            var cancellable = jobsBlock?["cancel"] as Dictionary<string, object>;
            _check(cancellable != null, "el manifiesto declara la política de cancelación");
            var routes = ((List<object>)cancellable["cancellable_routes"]).Cast<string>().ToList();
            _eq(RouteContracts.Cancellable.Count(), routes.Count,
                "y las cancelables salen del contrato, no de una lista aparte");
        }

        // ------------------------------------- reservas en vuelo vs. ledger

        private static int Count(string field)
            => (int)Convert.ToDouble(JobManager.Census()[field]);

        private static void PendingAndRunningLiveOnlyInFlight()
        {
            _section("ledger: un trabajo en cola o corriendo vive solo en las reservas en vuelo");

            JobManager.ResetForTests();
            var hold = new ManualResetEventSlim(false);
            var job = JobManager.TrySubmit(
                Request(key: "lf-1"), _ => { hold.Wait(3000); return Envelope(); });
            WaitUntil(() => JobManager.Active() != null, 2000);

            _eq(1, Count("in_flight_reservations"), "la reserva en vuelo existe");
            _eq(0, Count("ledger_entries"), "y el ledger sigue vacío: no ha terminado");
            _eq(1, Count("document_reservations"), "el documento está reservado");

            hold.Set();
            WaitUntil(() => JobManager.Get(job.JobId)?.FinishedUtc.HasValue == true, 4000);

            _eq(0, Count("in_flight_reservations"), "al terminar sale de las reservas");
            _eq(1, Count("ledger_entries"), "y entra al ledger");
            _eq(0, Count("document_reservations"), "el documento vuelve a estar libre");
        }

        private static void EveryTerminalStateReachesTheLedger()
        {
            _section("ledger: completed, partial, failed y cancelled quedan registrados");

            var cases = new (string Key, Func<JobManager.Job, Dictionary<string, object>> Work, string Expected)[]
            {
                ("t-completed", _ => Envelope(), JobManager.Completed),
                ("t-partial", _ => Envelope(requested: 4, applied: 4, verified: 1, status: "partial"),
                    JobManager.Partial),
                ("t-failed", _ => Envelope(requested: 4, applied: 4, verified: 0, status: "failed"),
                    JobManager.Failed),
                ("t-threw", _ => throw new InvalidOperationException("revienta"), JobManager.Failed)
            };

            foreach (var scenario in cases)
            {
                JobManager.ResetForTests();
                var job = JobManager.TrySubmit(Request(key: scenario.Key), scenario.Work);
                WaitUntil(() => JobManager.Get(job.JobId)?.FinishedUtc.HasValue == true, 3000);

                _eq(scenario.Expected, JobManager.Get(job.JobId).State,
                    scenario.Key + " termina en " + scenario.Expected);
                _eq(0, Count("in_flight_reservations"), "…y sale de las reservas en vuelo");
                _eq(1, Count("ledger_entries"), "…y entra al ledger");

                // A retry gets the SAME outcome back rather than running again.
                var reran = false;
                var again = JobManager.TrySubmit(
                    Request(key: scenario.Key), _ => { reran = true; return Envelope(); });
                _eq(Admission.ReplayedFromLedger, again.Outcome, "…y el reintento se reproduce");
                _eq(scenario.Expected, again.TerminalState, "…con el estado terminal original");
                _check(!reran, "…sin volver a ejecutar el trabajo en silencio");
            }
        }

        private static void CancelledPendingLeavesNoReservation()
        {
            _section("ledger: cancelar en cola libera documento y reserva");

            JobManager.ResetForTests();
            var hold = new ManualResetEventSlim(false);
            var running = JobManager.TrySubmit(
                Request(key: "cp-run"), _ => { hold.Wait(3000); return Envelope(); });
            WaitUntil(() => JobManager.Active() != null, 2000);

            var queued = JobManager.Submit("workflow/rules", _ => Envelope(),
                idempotencyKey: "cp-queued");
            JobManager.Cancel(queued.Id);
            _eq(JobManager.Cancelled, queued.State, "el pendiente queda cancelled");
            _check(!JobManager.HeldDocumentReservations().Contains(queued.Id),
                "y no retiene el documento");

            // The running one still holds it: a cancel request is not a stop.
            JobManager.Cancel(running.JobId);
            _check(JobManager.HeldDocumentReservations().Contains(running.JobId),
                "el que corre conserva la reserva hasta terminar de verdad");

            hold.Set();
            WaitUntil(() => JobManager.Get(running.JobId)?.FinishedUtc.HasValue == true, 4000);
            _eq(0, Count("document_reservations"), "y al terminar la suelta");
        }

        private static void TheLedgerEvictsInOrder()
        {
            _section("ledger: al llegar a capacidad expulsa la entrada más antigua");

            JobManager.ResetForTests();
            var capacity = JobManager.LedgerCapacity;

            for (var i = 0; i < capacity + 5; i++)
            {
                var job = JobManager.TrySubmit(
                    Request(key: "ev-" + i,
                        payload: new Dictionary<string, object> { ["i"] = (double)i }),
                    _ => Envelope());
                WaitUntil(() => JobManager.Get(job.JobId)?.FinishedUtc.HasValue == true, 3000);
            }

            _check(Count("ledger_entries") <= capacity,
                "el ledger nunca supera su capacidad (" + capacity + ")");

            // The first keys are gone; the last ones are still replayable.
            var evicted = JobManager.TrySubmit(
                Request(key: "ev-0", payload: new Dictionary<string, object> { ["i"] = 0.0 }),
                _ => Envelope());
            _eq(Admission.NewSubmission, evicted.Outcome,
                "una clave expulsada se acepta como operación nueva, según la política declarada");
            WaitUntil(() => JobManager.Get(evicted.JobId)?.FinishedUtc.HasValue == true, 3000);

            var recent = JobManager.TrySubmit(
                Request(key: "ev-" + (capacity + 4),
                    payload: new Dictionary<string, object> { ["i"] = (double)(capacity + 4) }),
                _ => Envelope());
            _eq(Admission.ReplayedFromLedger, recent.Outcome, "una reciente sigue reproduciéndose");
        }

        private static void TenThousandJobsStayBounded()
        {
            _section("ledger: tras 10.000 trabajos las estructuras siguen acotadas");

            JobManager.ResetForTests();
            for (var i = 0; i < 10000; i++)
            {
                var job = JobManager.TrySubmit(
                    Request(key: "bulk-" + i,
                        payload: new Dictionary<string, object> { ["i"] = (double)i }),
                    _ => Envelope());
                WaitUntil(() => JobManager.Get(job.JobId)?.FinishedUtc.HasValue == true, 3000);
            }

            var census = JobManager.Census();
            _check(Count("ledger_entries") <= JobManager.LedgerCapacity,
                "ledger acotado: " + census["ledger_entries"] + " <= " + JobManager.LedgerCapacity);
            _eq(0, Count("in_flight_reservations"), "cero reservas en vuelo");
            _eq(0, Count("document_reservations"), "cero reservas documentales fantasma");
            _eq(0, Count("pending_queue"), "cola vacía");
            _check(Count("jobs_tracked") <= 128,
                "la tabla de trabajos también está acotada: " + census["jobs_tracked"]);
        }

        private static void IntentCoversProfileAndFlags()
        {
            _section("ledger: el hash de intención cubre perfil, dry_run y flags");

            var baseline = Request(key: "k", payload: new Dictionary<string, object>
            {
                ["dry_run"] = false, ["replace_existing"] = false
            });

            var otherProfile = Request(key: "k", profileChecksum: "chk-b",
                payload: new Dictionary<string, object>
                {
                    ["dry_run"] = false, ["replace_existing"] = false
                });
            _check(!baseline.SameIntent(otherProfile), "otro profile_checksum es otra intención");

            var dry = Request(key: "k", payload: new Dictionary<string, object>
            {
                ["dry_run"] = true, ["replace_existing"] = false
            });
            _check(!baseline.SameIntent(dry), "dry_run true/false es otra intención");

            var overwrite = Request(key: "k", payload: new Dictionary<string, object>
            {
                ["dry_run"] = false, ["replace_existing"] = true
            });
            _check(!baseline.SameIntent(overwrite), "replace_existing true/false es otra intención");

            var reordered = Request(key: "k", payload: new Dictionary<string, object>
            {
                ["replace_existing"] = false, ["dry_run"] = false
            });
            _check(baseline.SameIntent(reordered),
                "pero el mismo payload con las claves en otro orden es la misma intención");

            // Session is excluded on purpose: a reconnect must not turn a
            // retry into a new operation.
            var otherSession = new JobRequest(
                jobId: "job-x", route: "workflow/rules",
                payload: new Dictionary<string, object>
                {
                    ["dry_run"] = false, ["replace_existing"] = false
                },
                sessionId: "otra-sesion", expectedFingerprint: "fp-1",
                idempotencyKey: "k", profileChecksum: "chk-a");
            _check(baseline.SameIntent(otherSession),
                "cambiar de sesión no cambia la operación");
        }

        private static void TheLedgerDoesNotKeepTheWholeResult()
        {
            _section("ledger: un resultado grande no se guarda entero");

            JobManager.ResetForTests();
            var fat = Envelope();
            fat["planned"] = Enumerable.Range(0, 5000)
                .Select(i => (object)new Dictionary<string, object>
                {
                    ["name"] = "conjunto-" + i,
                    ["blob"] = new string('x', 400)
                })
                .ToList();
            fat["image_base64"] = new string('A', 200000);

            var job = JobManager.TrySubmit(Request(key: "fat-1"), _ => fat);
            WaitUntil(() => JobManager.Get(job.JobId)?.FinishedUtc.HasValue == true, 4000);

            var replay = JobManager.TrySubmit(Request(key: "fat-1"), _ => Envelope());
            _eq(Admission.ReplayedFromLedger, replay.Outcome, "se reproduce");
            _check(!replay.Replay.ContainsKey("planned"), "sin la tabla de 5000 filas");
            _check(!replay.Replay.ContainsKey("image_base64"), "y sin la imagen");
            _eq("completed", replay.Replay["status"], "pero con el veredicto");
            _eq(1.0, replay.Replay["verified"], "y con los conteos");
            _check(replay.Replay.ContainsKey("detail_omitted"), "diciendo que se omitió detalle");

            var size = CanonicalPayload.Render(replay.Replay).Length;
            _check(size < 16 * 1024, "el registro cabe en el presupuesto (" + size + " car.)");
        }

        private static void ConcurrentTerminalsDoNotCorruptTheLedger()
        {
            _section("ledger: dos finalizaciones concurrentes no lo corrompen");

            JobManager.ResetForTests();
            var start = new Barrier(4);
            var threads = Enumerable.Range(0, 4).Select(i => new Thread(() =>
            {
                start.SignalAndWait();
                for (var n = 0; n < 25; n++)
                {
                    var key = "cc-" + i + "-" + n;
                    var job = JobManager.TrySubmit(
                        Request(key: key, payload: new Dictionary<string, object>
                        {
                            ["i"] = (double)i, ["n"] = (double)n
                        }),
                        _ => Envelope());
                    if (job.JobId.Length > 0)
                    {
                        WaitUntil(() => JobManager.Get(job.JobId)?.FinishedUtc.HasValue != false, 3000);
                    }
                }
            })).ToList();

            foreach (var t in threads) t.Start();
            foreach (var t in threads) t.Join(60000);
            WaitUntil(() => Count("document_reservations") == 0, 5000);

            _check(Count("ledger_entries") <= JobManager.LedgerCapacity, "el ledger sigue acotado");
            _eq(0, Count("in_flight_reservations"), "sin reservas en vuelo colgadas");
            _eq(0, Count("document_reservations"), "sin reservas documentales colgadas");
        }

        private static void DiagnosticsExposeNoPayload()
        {
            _section("ledger: el censo no expone payloads ni claves");

            JobManager.ResetForTests();
            var job = JobManager.TrySubmit(
                Request(key: "secreto-abc",
                    payload: new Dictionary<string, object> { ["token"] = "no-debe-salir" }),
                _ => Envelope());
            WaitUntil(() => JobManager.Get(job.JobId)?.FinishedUtc.HasValue == true, 3000);

            var text = CanonicalPayload.Render(JobManager.Census());
            _check(text.IndexOf("no-debe-salir", StringComparison.Ordinal) < 0,
                "el censo no lleva el payload");
            _check(text.IndexOf("secreto-abc", StringComparison.Ordinal) < 0,
                "ni la idempotency_key");
            _check(text.IndexOf("ledger_capacity", StringComparison.Ordinal) > 0,
                "solo tamaños y política");
            _check(text.IndexOf("fifo", StringComparison.Ordinal) > 0,
                "incluida la política de expulsión");
        }

        // ---------------------------------------------------- ciclo de vida

        private static void StopCancelsEveryPending()
        {
            _section("stop: se cancela todo lo pendiente y no se miente sobre lo que corre");

            JobManager.ResetForTests();
            var hold = new ManualResetEventSlim(false);
            var running = JobManager.TrySubmit(
                Request(key: "st-run"), _ => { hold.Wait(4000); return Envelope(); });
            WaitUntil(() => JobManager.Active() != null, 2000);

            // Queued behind it, through the non-admitting entry point: the
            // admission gate would refuse a second mutation, and what is under
            // test here is the drain, not the gate.
            var queuedA = JobManager.Submit("workflow/rules", _ => Envelope());
            var queuedB = JobManager.Submit("workflow/run", _ => Envelope());

            var report = JobManager.StopAndDrain();
            var cancelled = ((List<object>)report["cancelled_pending"]).Cast<string>().ToList();

            _check(cancelled.Contains(queuedA.Id), "el primero en cola se cancela");
            _check(cancelled.Contains(queuedB.Id), "y el segundo también");
            _eq(JobManager.Cancelled, queuedA.State, "queda en cancelled");
            _eq(0, queuedA.Applied, "sin nada aplicado");
            _eq(0, queuedA.Verified, "ni verificado");

            _eq(running.JobId, report["still_running"], "se nombra el que sigue dentro de la API");
            _eq(false, report["running_stopped"], "y NO se afirma que se detuvo");
            _check(JobManager.HeldDocumentReservations().Contains(running.JobId),
                "que conserva el documento mientras pueda estar mutando");

            hold.Set();
            WaitUntil(() => JobManager.Get(running.JobId)?.FinishedUtc.HasValue == true, 5000);
            _eq(0, Count("document_reservations"), "al terminar de verdad, lo suelta");
        }

        private static void ACancelledPendingNeverExecutes()
        {
            _section("stop: un pendiente cancelado no se ejecuta después");

            JobManager.ResetForTests();
            var hold = new ManualResetEventSlim(false);
            var running = JobManager.TrySubmit(
                Request(key: "ne-run"), _ => { hold.Wait(4000); return Envelope(); });
            WaitUntil(() => JobManager.Active() != null, 2000);

            var executed = false;
            var queued = JobManager.Submit("workflow/rules", _ => { executed = true; return Envelope(); });
            JobManager.StopAndDrain();
            _eq(JobManager.Cancelled, queued.State, "quedó cancelado antes de arrancar");

            // Let the worker drain the queue: the cancelled job must be
            // skipped, not merely marked. Marking one and running it anyway is
            // exactly what "stopped" must never mean.
            hold.Set();
            WaitUntil(() => JobManager.All().All(j => j.FinishedUtc.HasValue), 5000);
            _check(!executed, "su trabajo NUNCA se ejecutó");
            _check(queued.Result == null, "y no produjo resultado");
        }

        private static void ProfileIntegrityIsUnchangedByARun()
        {
            _section("perfil: el checksum describe el contenido antes y después");

            // The invariant the deep copy protects. Configure used to write
            // `dry_run` into the profile's own `sets` dictionary, so after a
            // run the active profile no longer canonicalised to the checksum
            // it had been published under — the number a report cites to say
            // which criteria produced it.
            var content = new Dictionary<string, object>
            {
                ["$schema"] = "naviscoord.profile/v1",
                ["name"] = "integridad",
                ["sets"] = new Dictionary<string, object>
                {
                    ["folders"] = new List<object>()
                }
            };
            var canonical = ProfileSchema.Canonical(content);
            var checksum = ProfileSchema.ChecksumOf(canonical);

            // A handler taking a copy and stamping its own flags on it.
            var derived = ProfileStore.DeepCopy(content);
            ((Dictionary<string, object>)derived["sets"])["dry_run"] = false;
            ((Dictionary<string, object>)derived["sets"])["replace_existing"] = true;

            _eq(checksum, ProfileSchema.ChecksumOf(ProfileSchema.Canonical(content)),
                "el original sigue dando el mismo checksum");
            _check(!((Dictionary<string, object>)content["sets"]).ContainsKey("dry_run"),
                "y la sección original no aprendió la bandera derivada");
            _check(((Dictionary<string, object>)derived["sets"]).ContainsKey("dry_run"),
                "que sí está en la copia");
            _check(ProfileSchema.ChecksumOf(ProfileSchema.Canonical(derived)) != checksum,
                "la copia modificada tiene otra identidad, que es lo correcto");
        }
    }
}
