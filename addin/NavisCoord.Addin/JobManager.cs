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

        /// <summary>
        /// A job with this idempotency key that has NOT finished yet, if any.
        /// </summary>
        /// <remarks>
        /// The ledger only records a result once the work completes, so it
        /// cannot answer "is this retry already running?". Without that
        /// question answered, a client retrying mid-execution submitted a
        /// second job for the same operation. Finished jobs are excluded on
        /// purpose: their answer lives in the ledger, which carries the actual
        /// result rather than just an id.
        /// </remarks>
        public static Job FindByIdempotencyKey(string key)
        {
            if (string.IsNullOrWhiteSpace(key)) return null;
            lock (Gate)
            {
                return Jobs.Values.FirstOrDefault(j =>
                    string.Equals(j.IdempotencyKey, key, StringComparison.Ordinal) &&
                    (j.State == Queued || j.State == Running || j.State == Verifying));
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

            string state;
            switch (Json.Str(result, "status"))
            {
                case "completed":
                case "planned":
                    state = Completed;
                    break;
                case "partial":
                    state = Partial;
                    break;
                case "failed":
                    state = Failed;
                    break;
                default:
                    state = result.ContainsKey("error") ? Failed : Completed;
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
        public static bool WouldCollide(string fingerprint, out string detail)
        {
            lock (Gate)
            {
                var busy = _running;
                if (busy == null || !busy.Exclusive)
                {
                    detail = null;
                    return false;
                }
                if (!string.IsNullOrEmpty(fingerprint) &&
                    !string.IsNullOrEmpty(busy.DocumentFingerprint) &&
                    !string.Equals(busy.DocumentFingerprint, fingerprint, StringComparison.OrdinalIgnoreCase))
                {
                    detail = null;
                    return false;
                }
                detail = "Ya hay una mutación en curso sobre este documento (" +
                         busy.Operation + ", trabajo " + busy.Id + "). " +
                         "Consulta job/status y reintenta cuando termine.";
                return true;
            }
        }
    }
}
