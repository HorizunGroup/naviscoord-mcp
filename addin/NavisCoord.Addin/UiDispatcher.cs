using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Threading;
using System.Windows.Forms;

namespace NavisCoord
{
    /// <summary>
    /// Marshals work onto the Navisworks UI thread.
    /// </summary>
    /// <remarks>
    /// The Navisworks API is single-threaded: touching a Document from the
    /// HTTP listener's thread produces corruption or a hard crash, not an
    /// exception. Every request therefore queues here and runs on the thread
    /// that owns the document.
    ///
    /// The queue is bounded on purpose. An unbounded queue turns a client
    /// that retries too eagerly into a frozen Navisworks with a thousand
    /// pending operations; refusing work with a clear message is kinder than
    /// accepting it and hanging. Waits are measured and reported back so a
    /// slow response can be attributed to queueing rather than guessed at.
    /// </remarks>
    internal sealed class UiDispatcher : IDisposable
    {
        private const int DefaultCapacity = 32;

        private readonly Control _marshal;
        private readonly SemaphoreSlim _slots;
        private readonly int _capacity;
        private int _queued;

        public UiDispatcher(int capacity = DefaultCapacity)
        {
            _capacity = capacity;
            _slots = new SemaphoreSlim(capacity, capacity);

            // Creating the handle here binds it to the calling thread, which
            // must be the UI thread. The plugin guarantees that by
            // constructing the dispatcher during Navisworks startup.
            _marshal = new Control();
            _marshal.CreateControl();
            var _ = _marshal.Handle;
        }

        public int QueueDepth => Volatile.Read(ref _queued);
        public int Capacity => _capacity;

        // Lifecycle of one marshalled work item. The whole point is to know,
        // when a timeout fires, whether the work had already started.
        private const int Pending = 0;
        private const int Started = 1;
        private const int Abandoned = 2;

        /// <summary>
        /// Runs <paramref name="work"/> on the UI thread, bounded by
        /// <paramref name="timeout"/>, and reports how long it waited.
        /// </summary>
        /// <remarks>
        /// The timeout used to bound only admission to the queue: after
        /// acquiring a slot the call went into a blocking
        /// <c>Control.Invoke</c> with no deadline at all. With a Run All
        /// holding the UI thread for twenty minutes that made the parameter a
        /// lie — a request passed 120 seconds blocked for the whole run — and
        /// worse, when the HTTP client eventually gave up, the work item was
        /// still queued on the UI thread and executed later. An abandoned
        /// request that mutates the document minutes after the caller stopped
        /// listening is the worst possible outcome: nobody sees the result and
        /// the document changes anyway.
        ///
        /// So the work is posted with <c>BeginInvoke</c> and waited on with
        /// the real deadline. If the deadline fires first, the item is marked
        /// abandoned and the delegate returns without calling
        /// <paramref name="work"/> — unless it had ALREADY started, in which
        /// case the answer says so rather than claiming nothing happened.
        ///
        /// **Who returns the slot.** Once <c>BeginInvoke</c> has accepted the
        /// item the delegate owns the release, in every one of its exits, and
        /// the caller never touches it again. Splitting that ownership by
        /// outcome is what leaked: on the "already started" timeout the caller
        /// correctly declined to release — the UI thread is still busy — but
        /// the delegate only released on the abandoned path, so nobody
        /// released at all. Each such timeout burned one of the
        /// <see cref="Capacity"/> slots for the life of the process, and a
        /// synchronous route whose work outruns its deadline is an ordinary
        /// event on a federated model, so the bridge eventually answered
        /// everything with "está saturado" until Navisworks was restarted.
        /// </remarks>
        public DispatchOutcome<T> Invoke<T>(Func<T> work, TimeSpan timeout)
        {
            var stopwatch = Stopwatch.StartNew();

            if (!_slots.Wait(timeout))
            {
                return DispatchOutcome<T>.Rejected(
                    $"El puente está saturado ({_capacity} operaciones en cola). " +
                    "Navisworks ejecuta una sola a la vez; reintenta en unos segundos.",
                    stopwatch.ElapsedMilliseconds);
            }

            Interlocked.Increment(ref _queued);
            var delegateOwnsSlot = false;
            try
            {
                if (_marshal.IsDisposed || !_marshal.IsHandleCreated)
                {
                    return DispatchOutcome<T>.Rejected(
                        "El puente perdió su vínculo con el hilo de UI de Navisworks. Reinicia el complemento.",
                        stopwatch.ElapsedMilliseconds);
                }

                T result = default;
                Exception failure = null;
                var state = Pending;

                var async = _marshal.BeginInvoke((MethodInvoker)(() =>
                {
                    try
                    {
                        // Claim the item before touching Navisworks. Losing
                        // this race means the caller already gave up, and the
                        // correct behaviour is to do nothing at all.
                        if (Interlocked.CompareExchange(ref state, Started, Pending) != Pending) return;
                        try
                        {
                            result = work();
                        }
                        catch (Exception ex)
                        {
                            failure = ex;
                        }
                    }
                    finally
                    {
                        // Every exit: abandoned before starting, completed, or
                        // completed long after the caller stopped waiting.
                        Interlocked.Decrement(ref _queued);
                        _slots.Release();
                    }
                }));

                // From here the item is queued on the UI thread and will run,
                // so the release above is the only one that may happen.
                delegateOwnsSlot = true;

                var remaining = timeout - stopwatch.Elapsed;
                if (remaining < TimeSpan.Zero) remaining = TimeSpan.Zero;

                if (!async.AsyncWaitHandle.WaitOne(remaining))
                {
                    if (Interlocked.CompareExchange(ref state, Abandoned, Pending) == Pending)
                    {
                        // Never started. The delegate will see Abandoned and
                        // return without touching the document.
                        return DispatchOutcome<T>.Rejected(
                            "Navisworks sigue ocupado y la operación no llegó a empezar, así que se " +
                            "descartó sin tocar el documento. Si hay un trabajo en curso, consúltalo " +
                            "con job/status y reintenta cuando termine.",
                            stopwatch.ElapsedMilliseconds);
                    }

                    // Already running. Saying "nothing happened" here would be
                    // a lie, so the caller is told the truth and pointed at
                    // the only honest way to find out how it ended.
                    return DispatchOutcome<T>.Rejected(
                        "La operación ya había empezado sobre el documento cuando venció la espera, " +
                        "así que NO se canceló: sigue ejecutándose en Navisworks. Vuelve a consultar " +
                        "el estado antes de reintentar, para no aplicarla dos veces.",
                        stopwatch.ElapsedMilliseconds);
                }

                _marshal.EndInvoke(async);
                var waited = stopwatch.ElapsedMilliseconds;
                return failure != null
                    ? DispatchOutcome<T>.Failed(failure, waited)
                    : DispatchOutcome<T>.Succeeded(result, waited);
            }
            catch (Exception ex)
            {
                return DispatchOutcome<T>.Failed(ex, stopwatch.ElapsedMilliseconds);
            }
            finally
            {
                if (!delegateOwnsSlot)
                {
                    Interlocked.Decrement(ref _queued);
                    _slots.Release();
                }
            }
        }

        public void Dispose()
        {
            try { _marshal?.Dispose(); } catch { /* shutting down anyway */ }
            _slots?.Dispose();
        }
    }

    internal readonly struct DispatchOutcome<T>
    {
        public T Value { get; }
        public Exception Error { get; }
        public string Rejection { get; }
        public long WaitedMs { get; }

        private DispatchOutcome(T value, Exception error, string rejection, long waitedMs)
        {
            Value = value;
            Error = error;
            Rejection = rejection;
            WaitedMs = waitedMs;
        }

        public bool Ok => Error == null && Rejection == null;

        public static DispatchOutcome<T> Succeeded(T value, long waitedMs)
            => new DispatchOutcome<T>(value, null, null, waitedMs);

        public static DispatchOutcome<T> Failed(Exception error, long waitedMs)
            => new DispatchOutcome<T>(default, error, null, waitedMs);

        public static DispatchOutcome<T> Rejected(string reason, long waitedMs)
            => new DispatchOutcome<T>(default, null, reason, waitedMs);
    }
}
