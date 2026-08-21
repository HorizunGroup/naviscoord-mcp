using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading;

namespace NavisCoord
{
    /// <summary>
    /// Long operations, run off the request thread, with progress you can read
    /// while they are still running.
    /// </summary>
    /// <remarks>
    /// Navisworks executes everything on its UI thread, and Run All on a real
    /// federated model holds that thread for minutes. Under the old
    /// request/response model the HTTP caller simply waited: the socket
    /// timed out, the operator saw nothing, and there was no way to ask
    /// whether the run had started, stalled, or finished. Worse, the listener
    /// loop itself blocked, so even <c>health</c> went unanswered — the tool
    /// looked dead precisely when it was busiest.
    ///
    /// Jobs fix the reporting, not the threading model. One job runs at a
    /// time, because the API cannot do otherwise; what changes is that
    /// submitting returns immediately and <c>job/status</c> is answered from
    /// this in-memory table without touching the document, so it stays live
    /// throughout.
    ///
    /// Progress is honest. An operation that loops over N tests reports units;
    /// one that calls a single atomic API reports phases and says
    /// <c>indeterminate</c>. Nothing here invents a percentage.
    /// </remarks>
    internal static class JobManager
    {
        public const string Queued = "queued";
        public const string Running = "running";
        public const string Verifying = "verifying";
        public const string Completed = "completed";
        public const string Partial = "partial";
        public const string Failed = "failed";
        public const string Cancelled = "cancelled";

        private const int HistoryLimit = 64;

        private static readonly object Gate = new object();
        private static readonly Dictionary<string, Job> Jobs =
            new Dictionary<string, Job>(StringComparer.OrdinalIgnoreCase);
        private static readonly Queue<Job> Pending = new Queue<Job>();
        private static readonly Queue<string> History = new Queue<string>();
        private static Job _running;
        private static Thread _worker;
        private static volatile bool _draining;

        internal sealed class Job
        {
            public string Id = "job-" + Guid.NewGuid().ToString("N").Substring(0, 10);
            public string Operation = string.Empty;
            public string State = Queued;
            public string Phase = "en cola";
            public string Message = string.Empty;
            public string TargetId = string.Empty;
            public string SessionId = string.Empty;
            public string DocumentFingerprint = string.Empty;
            public string IdempotencyKey = string.Empty;
            /// <summary>
            /// The criteria this job was planned against, frozen at submit.
            /// </summary>
            /// <remarks>
            /// Recorded so a result can be traced to the profile that produced
            /// it, and so a job cannot start under one profile and verify its
            /// work under another — <see cref="ProfileStore"/> refuses to swap
            /// the profile while a job is active, and this is the value that
            /// refusal protects.
            /// </remarks>
            public string ProfileChecksum = string.Empty;

            /// <summary>Exactly what was accepted. Never mutated afterwards.</summary>
            /// <remarks>
            /// The job runs against this, not against whatever the stores hold
            /// when its turn comes. Null only for jobs created through the
            /// legacy <see cref="Submit"/> entry point.
            /// </remarks>
            public JobRequest Request;

            public int UnitsDone;
            public int UnitsTotal;          // 0 = indeterminate, by design
            public int Requested;
            public int Applied;
            public int Verified;
            public int FailedCount;
            public DateTime QueuedUtc = DateTime.UtcNow;
            public DateTime? StartedUtc;
            public DateTime? FinishedUtc;
            public Dictionary<string, object> Result;
            public Dictionary<string, object> Error;
            public Func<Job, Dictionary<string, object>> Work;
            /// <summary>Mutations on the same document must not interleave.</summary>
            public bool Exclusive = true;
            /// <summary>
            /// Whether this operation checks <see cref="CancelRequested"/>
            /// between units and can therefore stop once started.
            /// </summary>
            /// <remarks>
            /// Declared per route at submit rather than inferred from
            /// <see cref="UnitsTotal"/>. Inferring it was wrong twice over: a
            /// cooperative job that has not reached its first
            /// <c>Progress</c> call still reports zero units, so a cancel
            /// arriving in that window was told the work could not be stopped
            /// when it could; and an atomic job was handed a flag nothing
            /// would ever read, which reads as "it will stop soon".
            /// </remarks>
            public bool Cancellable;
            public volatile bool CancelRequested;

            public void Progress(string phase, int done, int total, string message = null)
            {
                Phase = phase ?? Phase;
                UnitsDone = done;
                UnitsTotal = total;
                if (message != null) Message = message;
            }

            public void Phasing(string phase, string message = null)
            {
                Phase = phase ?? Phase;
                if (message != null) Message = message;
                // Total stays 0: an atomic API call has no units, and
                // inventing some would be a lie the caller cannot detect.
                UnitsTotal = 0;
                UnitsDone = 0;
            }

            public Dictionary<string, object> ToJson()
            {
                var payload = new Dictionary<string, object>
                {
                    ["job_id"] = Id,
                    ["operation"] = Operation,
                    ["state"] = State,
                    ["phase"] = Phase,
                    ["message"] = Message,
                    ["target_id"] = TargetId,
                    ["session_id"] = SessionId,
                    ["document_fingerprint"] = DocumentFingerprint,
                    ["idempotency_key"] = IdempotencyKey,
                    ["profile_checksum"] = ProfileChecksum,
                    ["cancellable"] = Cancellable,
                    ["requested"] = (double)Requested,
                    ["applied"] = (double)Applied,
                    ["verified"] = (double)Verified,
                    ["failed"] = (double)FailedCount,
                    ["queued_utc"] = Stamp(QueuedUtc),
                    ["started_utc"] = StartedUtc.HasValue ? Stamp(StartedUtc.Value) : string.Empty,
                    ["finished_utc"] = FinishedUtc.HasValue ? Stamp(FinishedUtc.Value) : string.Empty,
                    ["cancel_requested"] = CancelRequested
                };

                // Read once into locals. `Progress` writes done and total from
                // the job thread while this runs on the listener thread, so
                // reading each field twice can mix an old count with a new
                // total and report 130%.
                var done = UnitsDone;
                var total = UnitsTotal;
                if (total > 0)
                {
                    payload["progress"] = new Dictionary<string, object>
                    {
                        ["kind"] = "units",
                        ["done"] = (double)done,
                        ["total"] = (double)total,
                        // Clamped: a percentage above 100 is an invented
                        // number, which is the one thing this must never be.
                        ["percent"] = Math.Round(
                            Math.Max(0.0, Math.Min(100.0, 100.0 * done / total)), 1)
                    };
                }
                else
                {
                    payload["progress"] = new Dictionary<string, object>
                    {
                        ["kind"] = "indeterminate",
                        // Said out loud rather than faked: the caller can show
                        // a spinner instead of a bar that lies.
                        ["note"] = "La API de Navisworks ejecuta este paso de forma atómica: " +
                                   "hay fase, no porcentaje."
                    };
                }

                if (Result != null) payload["result"] = Result;
                if (Error != null) payload["error"] = Error;
                return payload;
            }

            private static string Stamp(DateTime value)
                => value.ToString("o", CultureInfo.InvariantCulture);
        }

        // --------------------------------------------------------- submit

        /// <summary>
        /// A key held by a job that has been accepted and has not finished.
        /// </summary>
        /// <remarks>
        /// One of the two structures, and the short-lived one. It answers "is
        /// this operation happening right now?", nothing else. It never
        /// outlives the job: the terminal transition removes it.
        /// </remarks>
        private sealed class Reservation
        {
            public string JobId = string.Empty;
            public string Route = string.Empty;
            public string IntentHash = string.Empty;
        }

        /// <summary>
        /// What one finished operation produced, kept so a retry can replay it.
        /// </summary>
        /// <remarks>
        /// The other structure, and the long-lived one. It answers "has this
        /// operation already happened, and what came out?" — a different
        /// question with a different lifetime, which is why merging the two
        /// into one table made both of them wrong: releasing on completion
        /// broke the replay, and not releasing left the document reserved
        /// forever.
        ///
        /// The envelope is trimmed and immutable. A finished result can carry
        /// megabytes of per-unit detail, and keeping every one of them for the
        /// life of a Navisworks session is a leak with a retry story attached.
        /// </remarks>
        private sealed class LedgerEntry
        {
            public string JobId = string.Empty;
            public string Route = string.Empty;
            public string IntentHash = string.Empty;
            public string TerminalState = string.Empty;
            public DateTime FinishedUtc;
            public Dictionary<string, object> Envelope;
        }

        /// <summary>
        /// How many finished operations stay replayable.
        /// </summary>
        /// <remarks>
        /// FIFO by completion time: the oldest finished operation is the one a
        /// client is least likely to still be retrying. Bounded because a
        /// Navisworks session runs for days and an unbounded table of results
        /// is a leak that only shows up on the machines that matter.
        ///
        /// Past the bound the guarantee expires, and that is stated rather
        /// than pretended: an evicted key is accepted as a NEW submission, so
        /// idempotency here is "within the last 128 finished operations of
        /// this session", not "forever".
        /// </remarks>
        public const int LedgerCapacity = 128;

        /// <summary>Longest envelope kept verbatim, in canonical characters.</summary>
        private const int LedgerEnvelopeBudget = 16 * 1024;

        private static readonly Dictionary<string, Reservation> InFlightReservations =
            new Dictionary<string, Reservation>(StringComparer.Ordinal);
        private static readonly Dictionary<string, LedgerEntry> CompletedLedger =
            new Dictionary<string, LedgerEntry>(StringComparer.Ordinal);
        private static readonly Queue<string> LedgerOrder = new Queue<string>();

        /// <summary>
        /// Jobs that hold the document: accepted, not yet terminal.
        /// </summary>
        /// <remarks>
        /// By job id rather than by fingerprint. Navisworks has one active
        /// document per process, so two mutations cannot proceed in parallel
        /// whatever their fingerprints say — pretending otherwise would be
        /// simulating a parallelism the host does not have.
        /// </remarks>
        private static readonly HashSet<string> DocumentHolders =
            new HashSet<string>(StringComparer.Ordinal);

        /// <summary>
        /// Accept a job, or say why not — in one indivisible step.
        /// </summary>
        /// <remarks>
        /// The bridge used to do this in four calls: look up the ledger, look
        /// up the in-flight key, ask whether it would collide, then submit.
        /// Every gap between those calls is a window, and two POSTs arriving
        /// together could both pass all three checks before either reserved
        /// anything — so the same idempotency key produced two jobs, and two
        /// mutations were accepted against one document.
        ///
        /// The fix is not a tighter check, it is a single one: every question
        /// is asked and every reservation is taken while the same lock is
        /// held, so there is no observable moment between deciding and
        /// reserving. Callers translate the outcome; they no longer assemble
        /// the decision.
        /// </remarks>
        public static AdmissionResult TrySubmit(
            JobRequest request, Func<Job, Dictionary<string, object>> work)
        {
            if (request == null)
            {
                return AdmissionResult.Reject(Admission.UnknownRoute, "Falta la petición.");
            }

            var contract = RouteContracts.For(request.Route);
            if (contract == null)
            {
                return AdmissionResult.Reject(Admission.UnknownRoute,
                    "La ruta '" + request.Route + "' no tiene contrato declarado.");
            }
            if (!contract.SupportsJob)
            {
                return AdmissionResult.Reject(Admission.RouteNotJobbable,
                    "La ruta '" + request.Route + "' no se puede ejecutar como trabajo.");
            }
            if (contract.RequiresFingerprint && string.IsNullOrEmpty(request.ExpectedFingerprint))
            {
                return AdmissionResult.Reject(Admission.FingerprintRequired,
                    "Esta ruta muta el documento y exige 'expected_document_fingerprint'. " +
                    "Léelo de 'health' y repite.");
            }

            lock (Gate)
            {
                var key = request.IdempotencyKey;
                if (!string.IsNullOrEmpty(key))
                {
                    // ------------------------------- still running or queued
                    if (InFlightReservations.TryGetValue(key, out var reserved))
                    {
                        if (!string.Equals(reserved.IntentHash, request.IntentHash,
                                StringComparison.Ordinal))
                        {
                            return AdmissionResult.Reject(Admission.IdempotencyConflict,
                                "La idempotency_key '" + key + "' está en curso para otra petición " +
                                "(ruta " + reserved.Route + ", trabajo " + reserved.JobId +
                                "). Usa una clave distinta.");
                        }
                        return new AdmissionResult
                        {
                            Outcome = Admission.DeduplicatedInFlight,
                            JobId = reserved.JobId,
                            Detail = "Ya había un trabajo en curso con esta idempotency_key; " +
                                     "se devuelve ese."
                        };
                    }

                    // ------------------------------------------- or finished
                    if (CompletedLedger.TryGetValue(key, out var done))
                    {
                        if (!string.Equals(done.IntentHash, request.IntentHash,
                                StringComparison.Ordinal))
                        {
                            return AdmissionResult.Reject(Admission.IdempotencyConflict,
                                "La idempotency_key '" + key + "' ya se usó para otra petición " +
                                "(ruta " + done.Route + ", terminada en " + done.TerminalState +
                                "). Usa una clave distinta.");
                        }
                        return new AdmissionResult
                        {
                            Outcome = Admission.ReplayedFromLedger,
                            JobId = done.JobId,
                            Replay = done.Envelope,
                            TerminalState = done.TerminalState,
                            Detail = "Esta idempotency_key ya se ejecutó (" + done.TerminalState +
                                     "); se devuelve el resultado guardado."
                        };
                    }
                }

                // ------------------------------------------------ collision
                //
                // Pending counts, not only running. The old check looked at
                // the single active job, so a second mutation submitted while
                // the first was still queued was accepted and sat behind it —
                // two accepted mutations over one document, which is the state
                // the check existed to prevent.
                if (request.IsMutation)
                {
                    var holder = DocumentHolders
                        .Select(id => Jobs.TryGetValue(id, out var j) ? j : null)
                        .FirstOrDefault(j => j != null && !IsTerminal(j.State));
                    if (holder != null)
                    {
                        return AdmissionResult.Reject(Admission.DocumentBusy,
                            "Ya hay una mutación aceptada sobre este documento (" +
                            holder.Operation + ", trabajo " + holder.Id + ", estado " +
                            holder.State + "). Consulta job/status y reintenta cuando termine.");
                    }
                }

                // ---------------------------------------------- reservation
                var job = new Job
                {
                    Id = request.JobId,
                    Operation = request.Route,
                    Work = work,
                    Request = request,
                    TargetId = request.TargetId,
                    SessionId = request.SessionId,
                    DocumentFingerprint = request.ExpectedFingerprint,
                    IdempotencyKey = request.IdempotencyKey,
                    Exclusive = request.IsMutation,
                    ProfileChecksum = request.ProfileChecksum,
                    Cancellable = request.Cancellable
                };

                Jobs[job.Id] = job;
                History.Enqueue(job.Id);
                if (request.IsMutation) DocumentHolders.Add(job.Id);
                if (!string.IsNullOrEmpty(key))
                {
                    InFlightReservations[key] = new Reservation
                    {
                        JobId = job.Id,
                        Route = request.Route,
                        IntentHash = request.IntentHash
                    };
                }
                Pending.Enqueue(job);
                TrimHistoryLocked();
                EnsureWorkerLocked();

                return new AdmissionResult
                {
                    Outcome = Admission.NewSubmission,
                    JobId = job.Id,
                    Detail = "Aceptado."
                };
            }
        }

        /// <summary>Whether a state can no longer execute anything.</summary>
        public static bool IsTerminal(string state)
            => state == Completed || state == Partial || state == Failed || state == Cancelled;

        /// <summary>
        /// The single terminal transition: in-flight out, ledger in, document
        /// back.
        /// </summary>
        /// <remarks>
        /// One function rather than three calls at each exit, because
        /// "released on every path" spelled out per path is a promise that
        /// survives until somebody adds a path. Callers hold the lock.
        ///
        /// The order matters: the reservation goes first, so a throw while
        /// writing the ledger cannot leave the document held. Recording a
        /// result is worth less than not deadlocking the next mutation.
        /// </remarks>
        private static void RetireLocked(Job job)
        {
            if (job == null) return;

            var key = job.IdempotencyKey ?? string.Empty;
            if (!string.IsNullOrEmpty(key) &&
                InFlightReservations.TryGetValue(key, out var reserved) &&
                string.Equals(reserved.JobId, job.Id, StringComparison.Ordinal))
            {
                InFlightReservations.Remove(key);
            }
            DocumentHolders.Remove(job.Id);

            // Failed, partial and cancelled go in too. A retry of a failed
            // operation must get the failure back rather than silently running
            // it again — the caller decides whether to try once more, with a
            // new key.
            if (string.IsNullOrEmpty(key) || job.Request == null) return;
            RememberLocked(key, job);
        }

        private static void RememberLocked(string key, Job job)
        {
            if (!CompletedLedger.ContainsKey(key)) LedgerOrder.Enqueue(key);
            CompletedLedger[key] = new LedgerEntry
            {
                JobId = job.Id,
                Route = job.Operation,
                IntentHash = job.Request.IntentHash,
                TerminalState = job.State,
                FinishedUtc = job.FinishedUtc ?? DateTime.UtcNow,
                Envelope = MutationEnvelopeSummary.Trim(job.Result, LedgerEnvelopeBudget)
            };

            while (LedgerOrder.Count > 0 && CompletedLedger.Count > LedgerCapacity)
            {
                var oldest = LedgerOrder.Dequeue();
                // Skip a key that was re-registered after being queued: its
                // current entry is newer than the position that named it.
                if (LedgerOrder.Contains(oldest)) continue;
                CompletedLedger.Remove(oldest);
            }
        }

        /// <summary>Whether any job is accepted and not yet terminal.</summary>
        /// <remarks>
        /// Used by <see cref="ProfileStore"/> to refuse a profile swap. The
        /// old guard asked for the RUNNING job, so swapping the profile while
        /// a job sat queued was allowed — and that job would have been judged
        /// against criteria it never agreed to.
        /// </remarks>
        public static Job ActiveOrPending()
        {
            lock (Gate)
            {
                if (_running != null) return _running;
                return Jobs.Values.FirstOrDefault(j => !IsTerminal(j.State));
            }
        }

        /// <summary>Document reservations still held. Diagnostics and tests.</summary>
        public static List<string> HeldDocumentReservations()
        {
            lock (Gate) { return DocumentHolders.ToList(); }
        }

        /// <summary>
        /// Sizes of the internal tables. Counts only — never the payloads.
        /// </summary>
        /// <remarks>
        /// Deliberately numbers and route names, with no payload, no envelope
        /// body and no idempotency key: a diagnostics endpoint that echoes
        /// what clients sent is a way to read another client's request.
        /// </remarks>
        public static Dictionary<string, object> Census()
        {
            lock (Gate)
            {
                return new Dictionary<string, object>
                {
                    ["jobs_tracked"] = (double)Jobs.Count,
                    ["in_flight_reservations"] = (double)InFlightReservations.Count,
                    ["document_reservations"] = (double)DocumentHolders.Count,
                    ["ledger_entries"] = (double)CompletedLedger.Count,
                    ["ledger_capacity"] = (double)LedgerCapacity,
                    ["ledger_eviction"] = "fifo_por_finalizacion",
                    ["history"] = (double)History.Count,
                    ["pending_queue"] = (double)Pending.Count
                };
            }
        }

        /// <summary>Clears every table. Test isolation only.</summary>
        internal static void ResetForTests()
        {
            lock (Gate)
            {
                Jobs.Clear();
                Pending.Clear();
                History.Clear();
                InFlightReservations.Clear();
                CompletedLedger.Clear();
                LedgerOrder.Clear();
                DocumentHolders.Clear();
                _running = null;
            }
        }

        public static Job Submit(
            string operation,
            Func<Job, Dictionary<string, object>> work,
            string targetId = "",
            string sessionId = "",
            string fingerprint = "",
            string idempotencyKey = "",
            bool exclusive = true,
            string profileChecksum = "",
            bool cancellable = false)
        {
            var job = new Job
            {
                Operation = operation ?? string.Empty,
                Work = work,
                TargetId = targetId ?? string.Empty,
                SessionId = sessionId ?? string.Empty,
                DocumentFingerprint = fingerprint ?? string.Empty,
                IdempotencyKey = idempotencyKey ?? string.Empty,
                Exclusive = exclusive,
                ProfileChecksum = profileChecksum ?? string.Empty,
                Cancellable = cancellable
            };

            lock (Gate)
            {
                Jobs[job.Id] = job;
                History.Enqueue(job.Id);
                Pending.Enqueue(job);
                TrimHistoryLocked();
                EnsureWorkerLocked();
            }
            return job;
        }

        public static Job Get(string jobId)
        {
            if (string.IsNullOrWhiteSpace(jobId)) return null;
            lock (Gate)
            {
                return Jobs.TryGetValue(jobId.Trim(), out var job) ? job : null;
            }
        }

        public static List<Job> All()
        {
            lock (Gate)
            {
                return Jobs.Values
                    .OrderByDescending(j => j.QueuedUtc)
                    .ToList();
            }
        }

        public static Job Active()
        {
            lock (Gate) { return _running; }
        }

        /// <summary>Machine-readable answers to a cancel request.</summary>
        /// <remarks>
        /// The caller needs to tell "it stopped" from "it will stop" from "it
        /// cannot stop", and the old reply gave only <c>cancelled: true/false</c>
        /// with the difference buried in Spanish prose.
        /// </remarks>
        public const string CancelledBeforeStart = "cancelled_before_start";
        public const string CancelRequested_ = "cancel_requested";
        public const string CannotCancelRunning = "cannot_cancel_running";
        public const string AlreadyFinished = "already_finished";

        /// <summary>
        /// Cancels a job, but only where cancelling is actually possible.
        /// </summary>
        /// <remarks>
        /// A queued job has not touched the document, so dropping it is free
        /// and honest. A running one is inside a Navisworks API call on the UI
        /// thread; there is no supported interruption, and aborting the thread
        /// corrupts the document rather than stopping the work.
        ///
        /// So the answer depends on what the operation actually supports, not
        /// on how far it happens to have got:
        ///
        /// * queued            → <c>cancelled</c>, nothing applied, nothing verified;
        /// * running, atomic   → <c>cannot_cancel_running</c>, and the job is
        ///                       left alone to finish;
        /// * running, co-op    → the request is recorded; the job stops at its
        ///                       next safe boundary and reports <c>cancelled</c>
        ///                       if it changed nothing and <c>partial</c> if it
        ///                       did;
        /// * finished          → the result is not rewritten.
        ///
        /// Setting the flag on an atomic job, which is what used to happen,
        /// promised a stop that nothing in the code path could deliver.
        /// </remarks>
        public static Dictionary<string, object> Cancel(string jobId)
        {
            var job = Get(jobId);
            if (job == null)
            {
                return new Dictionary<string, object>
                {
                    ["error"] = "unknown_job",
                    ["detail"] = "No existe un trabajo con id '" + (jobId ?? string.Empty) + "'."
                };
            }

            lock (Gate)
            {
                if (job.State == Queued)
                {
                    job.State = Cancelled;
                    job.Phase = "cancelado antes de empezar";
                    job.Message = "El trabajo se canceló sin tocar el documento.";
                    job.FinishedUtc = DateTime.UtcNow;
                    // Said explicitly rather than left at whatever the fields
                    // happened to hold: "cancelled" has to mean the document
                    // was not touched, and these are the numbers that say so.
                    job.Applied = 0;
                    job.Verified = 0;
                    job.FailedCount = 0;
                    job.UnitsDone = 0;
                    // Cancelled before starting means the document was never
                    // taken, so it goes back now. A RUNNING job is not
                    // released here: the code that owns it can still be inside
                    // an API call, and handing the document to a second
                    // mutation while the first is mid-write is the collision
                    // this table exists to prevent.
                    RetireLocked(job);
                    return new Dictionary<string, object>
                    {
                        ["cancelled"] = true,
                        ["outcome"] = CancelledBeforeStart,
                        ["detail"] = "El trabajo estaba en cola y se descartó sin tocar el documento.",
                        ["job"] = job.ToJson()
                    };
                }

                if (job.State == Running || job.State == Verifying)
                {
                    if (!job.Cancellable)
                    {
                        return new Dictionary<string, object>
                        {
                            ["cancelled"] = false,
                            ["outcome"] = CannotCancelRunning,
                            ["detail"] = "«" + job.Operation + "» ya empezó y la API de Navisworks lo " +
                                         "ejecuta de forma atómica: no se puede interrumpir sin " +
                                         "corromper el documento. Terminará solo; consulta job/status.",
                            ["job"] = job.ToJson()
                        };
                    }

                    job.CancelRequested = true;
                    return new Dictionary<string, object>
                    {
                        ["cancelled"] = false,
                        ["outcome"] = CancelRequested_,
                        ["cancel_requested"] = true,
                        ["detail"] = "Solicitud registrada: el trabajo se detendrá en el próximo " +
                                     "límite seguro. Reportará 'cancelled' si no alcanzó a cambiar " +
                                     "nada y 'partial' con lo ya verificado si sí.",
                        ["job"] = job.ToJson()
                    };
                }
            }

            return new Dictionary<string, object>
            {
                ["cancelled"] = false,
                ["outcome"] = AlreadyFinished,
                ["detail"] = "El trabajo ya terminó (" + job.State + "); su resultado no cambia.",
                ["job"] = job.ToJson()
            };
        }

        /// <summary>
        /// Shutdown: drop what has not started, and say what is still running.
        /// </summary>
        /// <remarks>
        /// A queued job never touched the document, so cancelling it is
        /// honest. A running one is inside a Navisworks call on the UI thread
        /// and there is no supported way to interrupt it — so this reports it
        /// as still running rather than claiming a stop it cannot deliver.
        /// Aborting that thread would corrupt the document, which is a worse
        /// answer than "it is still going".
        /// </remarks>
        public static Dictionary<string, object> StopAndDrain()
        {
            var cancelled = new List<object>();
            string stillRunning = null;
            lock (Gate)
            {
                foreach (var job in Jobs.Values.Where(j => j.State == Queued).ToList())
                {
                    job.State = Cancelled;
                    job.Phase = "cancelado al detener el puente";
                    job.Message = "El puente se detuvo antes de que este trabajo empezara.";
                    job.FinishedUtc = DateTime.UtcNow;
                    job.Applied = 0;
                    job.Verified = 0;
                    job.FailedCount = 0;
                    RetireLocked(job);
                    cancelled.Add(job.Id);
                }
                stillRunning = _running?.Id;
            }
            return new Dictionary<string, object>
            {
                ["cancelled_pending"] = cancelled,
                ["still_running"] = stillRunning ?? string.Empty,
                ["running_stopped"] = false,
                ["detail"] = stillRunning == null
                    ? "No quedaba nada en ejecución."
                    : "El trabajo " + stillRunning + " sigue dentro de una llamada de Navisworks; " +
                      "no se puede interrumpir y no se afirma que se haya detenido."
            };
        }

        // --------------------------------------------------------- worker

        private static void EnsureWorkerLocked()
        {
            if (_worker != null && _worker.IsAlive) return;
            _worker = new Thread(Drain)
            {
                IsBackground = true,
                Name = "NavisCoord.Jobs"
            };
            _draining = true;
            _worker.Start();
        }

        private static void Drain()
        {
            while (_draining)
            {
                Job job;
                lock (Gate)
                {
                    if (Pending.Count == 0)
                    {
                        _worker = null;
                        _draining = false;
                        return;
                    }
                    job = Pending.Dequeue();
                    if (job.State == Cancelled) continue;
                    _running = job;
                    job.State = Running;
                    job.StartedUtc = DateTime.UtcNow;
                    job.Phase = "ejecutando";
                }

                try
                {
                    var result = job.Work(job);
                    lock (Gate)
                    {
                        job.Result = result;
                        // The job's counters ARE the envelope's counters. A
                        // poller watching job/status and a caller reading the
                        // final result must never see two different stories.
                        job.Requested = (int)Json.Num(result, "requested", job.Requested);
                        job.Applied = (int)Json.Num(result, "applied", job.Applied);
                        job.Verified = (int)Json.Num(result, "verified", job.Verified);
                        job.FailedCount = (int)Json.Num(result, "failed", job.FailedCount);
                        job.State = StateFromResult(job, result);
                        job.Phase = "terminado";
                    }
                }
                catch (Exception ex)
                {
                    lock (Gate)
                    {
                        job.State = Failed;
                        job.Phase = "error";
                        job.Message = ex.Message;
                        job.Error = new Dictionary<string, object>
                        {
                            ["type"] = ex.GetType().Name,
                            ["detail"] = ex.Message
                        };
                    }
                    BridgeHost.Log("Trabajo " + job.Id + " (" + job.Operation + ") falló: " + ex.Message);
                }
                finally
                {
                    lock (Gate)
                    {
                        job.FinishedUtc = DateTime.UtcNow;
                        _running = null;
                        // The one place the document goes back, whatever the
                        // job did on its way out — completed, partial, failed,
                        // cancelled or thrown.
                        RetireLocked(job);
                    }
                }
            }
        }

        /// <summary>
        /// The job's final state comes from the mutation envelope it produced,
        /// so a handler cannot report success the contract does not support.
        /// </summary>
        private static string StateFromResult(Job job, Dictionary<string, object> result)
        {
            if (result == null) return Failed;

            // A mutation must produce an envelope that survives inspection.
            // The rule this replaces treated an unrecognised reply as success
            // as long as it carried no `error` key — so every handler that had
            // not adopted the envelope reported completed by staying silent,
            // and a half-applied one reported completed by not mentioning it.
            var problems = EnvelopeContract.Problems(job.Operation, result);
            if (problems.Count > 0)
            {
                job.Error = new Dictionary<string, object>
                {
                    ["type"] = EnvelopeContract.InvalidMutationResult,
                    ["detail"] = "La ruta '" + job.Operation + "' devolvió una respuesta que no " +
                                 "cumple el contrato de mutación: " + string.Join("; ", problems) + ".",
                    ["problems"] = problems.Cast<object>().ToList(),
                    // Kept for diagnosis only. It is never read back as if it
                    // meant something — that is how it got believed before.
                    ["raw_result"] = result
                };
                job.Phase = "respuesta inválida";
                return Failed;
            }

            string state;
            switch (Json.Str(result, "status"))
            {
                case EnvelopeContract.Completed:
                    state = Completed;
                    break;
                case EnvelopeContract.Planned:
                    // A rehearsal finished, which is a real completion of a
                    // job that promised to change nothing.
                    state = Completed;
                    break;
                case EnvelopeContract.Partial:
                    state = Partial;
                    break;
                case EnvelopeContract.Cancelled:
                    state = Cancelled;
                    break;
                case EnvelopeContract.Failed:
                    state = Failed;
                    break;
                default:
                    // Unreachable: the contract check above rejects anything
                    // not in that set. Failing rather than falling through to
                    // Completed is the point — there is no path from "I do not
                    // recognise this" to "it worked".
                    state = Failed;
                    break;
            }

            // A run that was asked to stop and did stop short is not simply
            // "completed" — but it is only "cancelled" if it changed nothing.
            //
            // This is the inconsistency the contract had to settle: the old
            // rule returned Cancelled whenever a cancel was pending and the
            // unit count had not reached its total, so a job that had already
            // applied and VERIFIED forty of a hundred renames reported
            // `cancelled`, which every caller reads as "nothing happened".
            // The forty renames were still in the document.
            if (state != Failed && job.CancelRequested && StoppedShort(job))
            {
                return job.Applied > 0 || job.Verified > 0 ? Partial : Cancelled;
            }
            return state;
        }

        /// <summary>Whether the work ended before covering every unit.</summary>
        /// <remarks>
        /// Guards the race where the cancel lands just as the last unit
        /// finishes: the work is done, and rewriting a finished job's state
        /// because a request arrived microseconds too late would be its own
        /// kind of lie.
        /// </remarks>
        private static bool StoppedShort(Job job)
            => job.UnitsTotal > 0 && job.UnitsDone < job.UnitsTotal;

        private static void TrimHistoryLocked()
        {
            while (History.Count > HistoryLimit)
            {
                var evicted = History.Dequeue();
                if (Jobs.TryGetValue(evicted, out var job) &&
                    (job.State == Queued || job.State == Running || job.State == Verifying))
                {
                    // Never evict live work just because it is old.
                    History.Enqueue(evicted);
                    return;
                }
                Jobs.Remove(evicted);
            }
        }

        public static void Reset()
        {
            lock (Gate)
            {
                Jobs.Clear();
                Pending.Clear();
                History.Clear();
                _running = null;
            }
        }

        /// <summary>
        /// True when a second exclusive mutation would collide with live work.
        /// </summary>
        /// <remarks>
        /// A null or empty <paramref name="fingerprint"/> asks the broader
        /// question — "is anything exclusive running at all?" — which is what
        /// the submit path needs BEFORE it touches the document, since asking
        /// Navisworks for a fingerprint while a job owns the UI thread is
        /// precisely what it is trying to avoid.
        /// </remarks>
    }
}
