using System;
using System.Collections.Generic;
using System.Threading;
using System.Windows.Forms;

namespace NavisCoord.Tests
{
    /// <summary>
    /// The real <see cref="UiDispatcher"/>, against a real message pump.
    /// </summary>
    /// <remarks>
    /// Not a copy and not a mock: the production source is linked into this
    /// project, and the only thing simulated is the Navisworks UI thread —
    /// simulated by being an actual WinForms message loop, which is what the
    /// Navisworks UI thread is.
    ///
    /// These cover the defect found in review: the timeout bounded only
    /// admission to the queue, so a request that got a slot then blocked
    /// forever inside Control.Invoke. During a twenty-minute Run All that made
    /// the deadline meaningless, and when the HTTP client finally gave up the
    /// work item was still queued and mutated the document later — with nobody
    /// listening for the answer.
    /// </remarks>
    internal static class DispatcherTests
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

            using (var pump = new MessagePump())
            {
                var dispatcher = pump.CreateDispatcher();

                HappyPath(dispatcher);
                ExceptionsTravel(dispatcher);
                TimeoutBoundsExecution(pump, dispatcher);
                AbandonedWorkDoesNotRun(pump, dispatcher);
                StartedWorkIsNotClaimedCancelled(pump, dispatcher);
                StartedTimeoutReturnsItsSlot(pump);
                SlotsAreReturned(pump, dispatcher);
            }
        }

        // ------------------------------------------------------------- cases

        private static void HappyPath(UiDispatcher dispatcher)
        {
            _section("dispatcher: caso normal");

            var ranOn = 0;
            var outcome = dispatcher.Invoke(
                () => { ranOn = Thread.CurrentThread.ManagedThreadId; return 42; },
                TimeSpan.FromSeconds(5));

            _check(outcome.Ok, "una operación corta se completa");
            _eq(42, outcome.Value, "…y devuelve su resultado");
            _check(ranOn != Thread.CurrentThread.ManagedThreadId,
                "el trabajo NO corrió en el hilo que llamó: se marshalló al de UI");
        }

        private static void ExceptionsTravel(UiDispatcher dispatcher)
        {
            _section("dispatcher: excepciones del hilo de UI");

            var outcome = dispatcher.Invoke<int>(
                () => throw new InvalidOperationException("revienta en UI"),
                TimeSpan.FromSeconds(5));

            _check(!outcome.Ok, "una excepción en el hilo de UI no se reporta como éxito");
            _check(outcome.Error is InvalidOperationException,
                "el tipo de la excepción sobrevive al marshalling");
            _check(outcome.Error.Message.Contains("revienta en UI"),
                "…y el mensaje también");
            _check(outcome.Rejection == null, "una excepción no es un rechazo de cola");
        }

        private static void TimeoutBoundsExecution(MessagePump pump, UiDispatcher dispatcher)
        {
            _section("dispatcher: el timeout acota la EJECUCIÓN, no solo la cola");

            // Occupy the UI thread the way a long clash run does.
            var release = new ManualResetEventSlim(false);
            var hogStarted = new ManualResetEventSlim(false);
            var hog = new Thread(() => dispatcher.Invoke(
                () => { hogStarted.Set(); release.Wait(10000); return 0; },
                TimeSpan.FromSeconds(30)))
            { IsBackground = true };
            hog.Start();
            _check(hogStarted.Wait(5000), "la operación larga tomó el hilo de UI");

            var clock = System.Diagnostics.Stopwatch.StartNew();
            var outcome = dispatcher.Invoke(() => 7, TimeSpan.FromMilliseconds(400));
            clock.Stop();

            _check(!outcome.Ok, "la segunda operación no se completa mientras el hilo está ocupado");
            _check(clock.ElapsedMilliseconds < 3000,
                $"…y vuelve en su plazo ({clock.ElapsedMilliseconds} ms), no cuando el hilo se libere");
            _check(outcome.Rejection != null, "vuelve como rechazo con motivo legible");

            release.Set();
            hog.Join(5000);
        }

        private static void AbandonedWorkDoesNotRun(MessagePump pump, UiDispatcher dispatcher)
        {
            _section("dispatcher: lo abandonado NO muta después");

            var release = new ManualResetEventSlim(false);
            var hogStarted = new ManualResetEventSlim(false);
            var hog = new Thread(() => dispatcher.Invoke(
                () => { hogStarted.Set(); release.Wait(10000); return 0; },
                TimeSpan.FromSeconds(30)))
            { IsBackground = true };
            hog.Start();
            hogStarted.Wait(5000);

            // This one gives up before the UI thread is free.
            var mutated = 0;
            var outcome = dispatcher.Invoke(
                () => { Interlocked.Increment(ref mutated); return 1; },
                TimeSpan.FromMilliseconds(300));
            _check(!outcome.Ok, "la operación que venció no reporta éxito");

            // Free the UI thread and give the pump ample time to drain.
            release.Set();
            hog.Join(5000);
            pump.Drain(1500);

            _eq(0, Volatile.Read(ref mutated),
                "el trabajo abandonado NUNCA llegó a ejecutarse — este era el defecto: " +
                "se ejecutaba minutos después, sin nadie escuchando la respuesta");
        }

        private static void StartedWorkIsNotClaimedCancelled(MessagePump pump, UiDispatcher dispatcher)
        {
            _section("dispatcher: lo ya empezado se admite, no se finge cancelado");

            var started = new ManualResetEventSlim(false);
            var finished = 0;

            // Times out WHILE its own work is running, not before it starts.
            var outcome = dispatcher.Invoke(
                () =>
                {
                    started.Set();
                    Thread.Sleep(700);
                    Interlocked.Increment(ref finished);
                    return 5;
                },
                TimeSpan.FromMilliseconds(250));

            _check(started.Wait(5000), "el trabajo sí había empezado");
            _check(!outcome.Ok, "vencido el plazo, no se reporta éxito");
            _check(outcome.Rejection != null && outcome.Rejection.Contains("NO se canceló"),
                "…y se dice explícitamente que sigue ejecutándose, en vez de fingir que no pasó nada");

            pump.Drain(2000);
            _eq(1, Volatile.Read(ref finished),
                "el trabajo ya iniciado terminó de verdad: por eso NO se anuncia como cancelado");
        }

        /// <summary>
        /// The slot is returned even when the deadline fires AFTER the work
        /// started — the one path where the caller cannot release it itself.
        /// </summary>
        /// <remarks>
        /// Found in review, not by the suite above: <see cref="SlotsAreReturned"/>
        /// compares the queue depth against whatever it happened to be when it
        /// started, and the preceding case had already leaked one slot, so the
        /// delta agreed with itself and the absolute number was never checked.
        ///
        /// The defect: on the "already started" timeout the caller returned
        /// without releasing (correctly — the UI thread is still busy) and the
        /// delegate released only on the abandoned path, so nobody ever
        /// released at all. Each such timeout permanently consumed one of the
        /// 32 slots and one unit of the advertised queue depth. After enough of
        /// them — a synchronous route whose work outruns its 120 s deadline is
        /// an ordinary event on a federated model — the bridge answered every
        /// request with "el puente está saturado" until Navisworks restarted.
        ///
        /// Capacity 3 rather than the default 32 so exhaustion is provable in
        /// seconds instead of minutes.
        /// </remarks>
        private static void StartedTimeoutReturnsItsSlot(MessagePump pump)
        {
            _section("dispatcher: el timeout con trabajo YA EMPEZADO devuelve su slot");

            const int capacity = 3;
            var small = pump.CreateDispatcher(capacity);

            for (var i = 0; i < capacity; i++)
            {
                var outcome = small.Invoke(
                    () => { Thread.Sleep(500); return 0; },
                    TimeSpan.FromMilliseconds(150));
                _check(outcome.Rejection != null && outcome.Rejection.Contains("NO se canceló"),
                    "la operación " + (i + 1) + " venció cuando ya se estaba ejecutando");
                pump.Drain(2000);
            }
            pump.Drain(2000);

            _eq(0, small.QueueDepth,
                "la profundidad de cola vuelve a CERO: lo ya empezado también devuelve su slot al terminar");
            _check(small.Invoke(() => 1, TimeSpan.FromSeconds(5)).Ok,
                "el dispatcher sigue aceptando trabajo tras " + capacity + " timeouts de trabajo ya " +
                "iniciado — con la fuga el semáforo quedaba agotado hasta reiniciar Navisworks");

            pump.Drain(1000);
        }

        private static void SlotsAreReturned(MessagePump pump, UiDispatcher dispatcher)
        {
            _section("dispatcher: la cola no se agota tras timeouts");

            pump.Drain(1000);
            var before = dispatcher.QueueDepth;

            for (var i = 0; i < 5; i++)
            {
                dispatcher.Invoke(() => { Thread.Sleep(5); return i; }, TimeSpan.FromSeconds(5));
            }
            pump.Drain(1000);

            _eq(before, dispatcher.QueueDepth,
                "tras varias operaciones la profundidad de cola vuelve a su valor inicial");
            _check(dispatcher.Invoke(() => 1, TimeSpan.FromSeconds(5)).Ok,
                "el dispatcher sigue aceptando trabajo (no se filtraron slots del semáforo)");
        }

        // -------------------------------------------------------- message pump

        /// <summary>
        /// A real WinForms message loop on its own STA thread, standing in for
        /// the Navisworks UI thread.
        /// </summary>
        private sealed class MessagePump : IDisposable
        {
            private readonly Thread _thread;
            private readonly ManualResetEventSlim _ready = new ManualResetEventSlim(false);
            private Form _host;
            private UiDispatcher _dispatcher;

            public MessagePump()
            {
                _thread = new Thread(() =>
                {
                    _host = new Form { WindowState = FormWindowState.Minimized, ShowInTaskbar = false };
                    _host.Load += (_, __) =>
                    {
                        // The dispatcher binds its marshalling handle to the
                        // calling thread, so it must be constructed here.
                        _dispatcher = new UiDispatcher();
                        _ready.Set();
                    };
                    Application.Run(_host);
                })
                { IsBackground = true };
                _thread.SetApartmentState(ApartmentState.STA);
                _thread.Start();
                if (!_ready.Wait(10000)) throw new TimeoutException("el bucle de mensajes no arrancó");
            }

            public UiDispatcher CreateDispatcher() => _dispatcher;

            /// <summary>
            /// A second dispatcher with its own capacity, bound to this same
            /// pump thread — constructed there, because the dispatcher takes
            /// its marshalling handle from whichever thread builds it.
            /// </summary>
            public UiDispatcher CreateDispatcher(int capacity)
            {
                UiDispatcher made = null;
                var ready = new ManualResetEventSlim(false);
                _host.BeginInvoke((MethodInvoker)(() =>
                {
                    made = new UiDispatcher(capacity);
                    ready.Set();
                }));
                if (!ready.Wait(10000)) throw new TimeoutException("no se pudo crear el dispatcher");
                return made;
            }

            /// <summary>Lets the pump process everything already posted.</summary>
            public void Drain(int millis)
            {
                var deadline = DateTime.UtcNow.AddMilliseconds(millis);
                while (DateTime.UtcNow < deadline)
                {
                    var done = new ManualResetEventSlim(false);
                    try { _host.BeginInvoke((MethodInvoker)(() => done.Set())); }
                    catch { return; }
                    if (!done.Wait(millis)) return;
                    Thread.Sleep(20);
                }
            }

            public void Dispose()
            {
                try
                {
                    _dispatcher?.Dispose();
                    _host?.BeginInvoke((MethodInvoker)(() => Application.ExitThread()));
                    _thread.Join(3000);
                }
                catch
                {
                    // Tearing down a test pump must never fail the run.
                }
            }
        }
    }
}
