using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace NavisCoord
{
    /// <summary>
    /// Loopback HTTP endpoint the MCP server talks to.
    /// </summary>
    /// <remarks>
    /// An unauthenticated localhost listener would let any process on the
    /// machine — including a browser tab hitting the port from a page — drive
    /// the user's model. This listener requires a session token generated at startup
    /// and written to a per-user file with an explicit DACL, so reaching the
    /// bridge requires the ability to read that user's own AppData.
    ///
    /// Three properties are load-bearing:
    ///
    /// * **Everything that reads model data or mutates the document requires
    ///   the token.** The single exception is <c>GET /health</c>, which answers
    ///   four facts — ok, version, PID, busy — and nothing else. Discovery
    ///   needs to know a bridge is alive and whether to wait; it does not need
    ///   to know what is open, on which port, under which session id, or even
    ///   whether somebody is working right now.
    /// * **Requests are handled concurrently.** The old loop called
    ///   <c>GetContext</c>, handled the request, and only then accepted the
    ///   next one — so while a Run All held the UI thread, nothing else was
    ///   even read off the socket and the bridge looked dead exactly when it
    ///   was busiest. Each context now goes to the thread pool; document work
    ///   still serialises behind the UI dispatcher, but <c>health</c>,
    ///   <c>job/status</c> and <c>sessions</c> answer immediately because they
    ///   never touch the document.
    /// * **One session file per instance.** See <see cref="SessionStore"/>.
    ///
    /// The default port is 8781 and the bridge searches a bounded range so
    /// several Navisworks instances can coexist.
    /// </remarks>
    internal sealed class HttpBridge : IDisposable
    {
        public const int DefaultPort = 8781;
        private const int PortRange = 8;
        private const string TokenHeader = "X-NavisCoord-Token";

        /// <summary>
        /// Routes answered on the listener thread, without marshalling to the
        /// Navisworks UI thread — which is what keeps them alive during a job.
        /// </summary>
        private static readonly HashSet<string> NonDocumentRoutes = new HashSet<string>(
            StringComparer.OrdinalIgnoreCase)
        {
            "job/status", "job/list", "job/cancel", "sessions", "output_policy",
            // The profile routes touch no Document, and answering them off
            // the UI thread is what lets profile/load refuse honestly while a
            // job holds it: dispatched, the refusal would arrive as a
            // timeout instead of "there is a mutation running".
            "profile/info", "profile/load", "profile/reset"
        };

        private HttpListener _listener;
        private readonly UiDispatcher _dispatcher;
        private readonly Router _router;
        private readonly string _token;
        private readonly string _sessionId;
        private readonly int _pid;
        private readonly int _basePort;
        private int _port;
        private Thread _worker;
        private volatile bool _running;
        private volatile string _state = BridgeState.Starting;
        private string _securityFailure = string.Empty;

        public HttpBridge(UiDispatcher dispatcher, Router router, int port = DefaultPort)
        {
            _dispatcher = dispatcher;
            _router = router;
            _basePort = port;
            _port = port;
            _token = NewToken();
            _sessionId = SessionStore.NewSessionId();
            _pid = System.Diagnostics.Process.GetCurrentProcess().Id;
            // So profile/info can say which instance the active profile
            // belongs to; the store itself is per-process, which is what
            // keeps two open Navisworks instances from sharing one.
            ProfileStore.SessionId = _sessionId;
            ProfileStore.Logger = BridgeHost.Log;
        }

        public int Port => _port;
        public string Token => _token;
        public string SessionId => _sessionId;
        public bool IsRunning => _running;

        public void Start()
        {
            if (_running) return;

            // Two Navisworks versions open at once — 2024 with the archived
            // model, 2026 with the live one — both host this add-in and both
            // would fight over one port. The second walks up the range instead
            // of dying, and each instance records ITS OWN port in ITS OWN
            // session file, so neither can mask the other.
            HttpListenerException lastFailure = null;
            for (var offset = 0; offset < PortRange; offset++)
            {
                var candidate = _basePort + offset;
                var listener = new HttpListener();
                listener.Prefixes.Add($"http://127.0.0.1:{candidate}/");
                try
                {
                    listener.Start();
                    _listener = listener;
                    _port = candidate;
                    lastFailure = null;
                    break;
                }
                catch (HttpListenerException ex)
                {
                    lastFailure = ex;
                    try { listener.Close(); } catch { /* not ours */ }
                }
            }

            if (_listener == null)
            {
                throw new InvalidOperationException(
                    $"Ningún puerto libre entre {_basePort} y {_basePort + PortRange - 1}.",
                    lastFailure);
            }

            // The port is reserved above and NOT yet being accepted: the loop
            // that calls GetContext starts at the bottom of this method. So the
            // order is reserve, publish the credential securely, then accept —
            // no authenticated request can be served before the token exists
            // where the client can find it, and none is served at all if it
            // could not be protected.
            SessionStore.Prune();
            try
            {
                WriteSessionFile();
            }
            catch (SecurityNotEnforceable secure)
            {
                // Fail closed. Nothing listens, nothing is published, and the
                // reason is actionable rather than a warning in a log nobody
                // reads.
                _state = BridgeState.FailedSecurity;
                _securityFailure = secure.Message;
                try { _listener?.Close(); } catch { /* never accepted */ }
                _listener = null;
                _running = false;
                SessionStore.Remove(_pid, _sessionId);
                BridgeHost.Log("El puente NO se inició: " + secure.Message);
                throw;
            }

            _running = true;
            _state = BridgeState.Ready;
            AuditRuntimeSecurity();
            SubscribeDocumentEvents();

            _worker = new Thread(Loop)
            {
                IsBackground = true,
                Name = "NavisCoord.HttpBridge"
            };
            _worker.Start();
        }

        /// <summary>What the bridge is doing, as one of the named states.</summary>
        public string State => _state;

        /// <summary>Why the bridge refused to start, when it did.</summary>
        public string SecurityFailure => _securityFailure;

        /// <summary>
        /// Stop accepting, drop what has not started, and stay answerable
        /// until nothing can still be mutating.
        /// </summary>
        /// <remarks>
        /// The old Stop closed the listener and deleted the session file. Both
        /// of those are what "stopped" looks like from outside, and neither was
        /// true: a job already accepted could still be inside a Navisworks call
        /// rewriting clash tests, and with the session file gone there was no
        /// way left to ask about it. The operator read "detenido" and concluded
        /// nothing further would happen to their model.
        ///
        /// So Stop has two exits. If nothing survives the drain it finishes and
        /// the session goes away. If a job is running inside an atomic API call
        /// — which has no supported interruption — the bridge stays in
        /// `draining`: the listener no longer accepts new mutations, the
        /// session record stays so job/status still answers, and Stop reports
        /// which job is holding it open.
        /// </remarks>
        public Dictionary<string, object> Stop()
        {
            if (!_running)
            {
                return new Dictionary<string, object>
                {
                    ["state"] = _state,
                    ["stopped"] = _state == BridgeState.Stopped ||
                                  _state == BridgeState.FailedSecurity,
                    ["detail"] = "El puente no estaba escuchando."
                };
            }

            // Refuse new work first, so nothing joins the queue while it drains.
            _state = BridgeState.Stopping;
            UnsubscribeDocumentEvents();

            var drain = JobManager.StopAndDrain();
            var stillRunning = Json.Str(drain, "still_running");

            if (!string.IsNullOrEmpty(stillRunning))
            {
                // Draining, not stopped. The session record stays exactly so
                // this job remains queryable.
                _state = BridgeState.Draining;
                var pendingJob = JobManager.Get(stillRunning);
                return new Dictionary<string, object>
                {
                    ["state"] = _state,
                    ["stopped"] = false,
                    ["cancelled_pending"] = drain["cancelled_pending"],
                    ["running_job"] = stillRunning,
                    ["running_operation"] = pendingJob?.Operation ?? string.Empty,
                    ["running_stopped"] = false,
                    ["detail"] = "El trabajo " + stillRunning + " (" +
                                 (pendingJob?.Operation ?? "?") + ") sigue dentro de una llamada " +
                                 "de Navisworks y no se puede interrumpir. El puente queda en " +
                                 "'draining': no acepta mutaciones nuevas y sigue respondiendo " +
                                 "job/status hasta que ese trabajo termine.",
                    ["poll"] = "job/status con {\"job_id\": \"" + stillRunning + "\"}"
                };
            }

            return FinishStop(drain);
        }

        /// <summary>Completes a stop once nothing can still mutate.</summary>
        /// <remarks>
        /// Only here does the listener close and the session record disappear.
        /// Separated from <see cref="Stop"/> so the draining path reaches the
        /// same ending rather than a second copy of it.
        /// </remarks>
        public Dictionary<string, object> FinishStop(Dictionary<string, object> drain = null)
        {
            _running = false;
            _state = BridgeState.Stopped;
            try { _listener?.Stop(); } catch { /* already tearing down */ }
            // Only OUR entry. Deleting the shared file was the bug that made
            // closing 2024 break the live 2026 bridge.
            SessionStore.Remove(_pid, _sessionId);

            var result = new Dictionary<string, object>
            {
                ["state"] = _state,
                ["stopped"] = true,
                ["detail"] = "El puente se detuvo y la sesión se retiró."
            };
            if (drain != null && drain.TryGetValue("cancelled_pending", out var cancelled))
            {
                result["cancelled_pending"] = cancelled;
            }
            return result;
        }

        /// <summary>
        /// Process teardown: best effort, and honest about what that means.
        /// </summary>
        /// <remarks>
        /// Navisworks closing is not the same event as the operator pressing
        /// Stop. There is no waiting to be done — the process is destroying the
        /// APIs a running job is inside — so this records what was in flight
        /// and removes the session so nothing else can call in, without
        /// pretending anything was cancelled.
        /// </remarks>
        public Dictionary<string, object> Dispose(string reason)
        {
            var running = JobManager.Active();
            if (running != null)
            {
                BridgeHost.Log("El proceso termina (" + reason + ") con el trabajo " + running.Id +
                               " (" + running.Operation + ") en ejecución. No se puede cancelar; " +
                               "el estado final de ese trabajo no quedará registrado.");
            }
            _running = false;
            _state = BridgeState.Stopped;
            try { _listener?.Abort(); } catch { /* the process is going */ }
            SessionStore.Remove(_pid, _sessionId);
            return new Dictionary<string, object>
            {
                ["state"] = _state,
                ["reason"] = reason ?? string.Empty,
                ["was_running"] = running?.Id ?? string.Empty,
                ["running_stopped"] = false
            };
        }

        private void Loop()
        {
            while (_running)
            {
                HttpListenerContext context;
                try
                {
                    context = _listener.GetContext();
                }
                catch (HttpListenerException)
                {
                    break; // Stop() closed the listener.
                }
                catch (ObjectDisposedException)
                {
                    break;
                }

                // Accept the next request immediately. Handling inline is what
                // made a long mutation look like a hung bridge.
                ThreadPool.QueueUserWorkItem(_ =>
                {
                    try
                    {
                        Handle(context);
                    }
                    catch (Exception ex)
                    {
                        TryRespond(context, 500, new Dictionary<string, object>
                        {
                            ["error"] = "bridge_failure",
                            ["detail"] = ex.Message
                        });
                    }
                });
            }
        }

        private void Handle(HttpListenerContext context)
        {
            var request = context.Request;

            // Loopback binding already restricts this, but a remote endpoint
            // arriving here at all means something is misconfigured.
            if (!IPAddress.IsLoopback(request.RemoteEndPoint.Address))
            {
                TryRespond(context, 403, Error("forbidden", "Solo se aceptan conexiones locales."));
                return;
            }

            var route = request.Url.AbsolutePath.Trim('/');

            // The ONE unauthenticated answer, and the only one that can be:
            // liveness for discovery. No document path, no title, no token,
            // nothing about the model.
            if (IsAnonymousLiveness(request, route))
            {
                var liveness = LivenessPayload();
                liveness["bridge_state"] = _state;
                if (_state == BridgeState.Draining)
                {
                    liveness["draining_job"] = JobManager.Active()?.Id ?? string.Empty;
                }
                TryRespond(context, 200, liveness);
                return;
            }

            var supplied = request.Headers[TokenHeader];
            if (!FixedTimeEquals(supplied, _token))
            {
                TryRespond(context, 401, Error(
                    "unauthorized",
                    "Token de sesión inválido o ausente. El servidor MCP lo lee de " +
                    SessionStore.SessionsDir() + "."));
                return;
            }

            string body;
            try
            {
                body = ReadBody(request, LimitFor(route));
            }
            catch (BodyReader.TooLargeException ex)
            {
                TryRespond(context, 413, Error("body_too_large", ex.Message));
                return;
            }
            catch (Exception ex)
            {
                // A truncated, aborted or mis-encoded upload must answer, not
                // kill the listener thread pool worker with an unhandled
                // exception — the port has to stay live for the next request.
                TryRespond(context, 400, Error("body_unreadable", ex.Message));
                return;
            }

            // One strict parse, not a permissive parse checked afterwards by a
            // second scanner. Two readers over one string is two grammars, and
            // the one that decides what the handler sees was the lenient one:
            // a body cut off in transit came through as a complete-looking
            // dictionary and was answered 200.
            Dictionary<string, object> payload;
            if (string.IsNullOrWhiteSpace(body))
            {
                // An absent body is legitimate: most routes take no arguments.
                payload = new Dictionary<string, object>();
            }
            else if (!JsonStrict.TryParseObject(body, out payload, out var badJson))
            {
                var refusal = badJson.ToJson();
                refusal["detail"] = badJson.Detail + " (posición " + badJson.Position + ", " +
                                    Encoding.UTF8.GetByteCount(body) + " bytes recibidos)";
                TryRespond(context, HttpStatus.For(badJson.Code), refusal);
                return;
            }

            // Answered here, on the listener thread, precisely so they keep
            // answering while a job owns the UI thread.
            if (NonDocumentRoutes.Contains(route))
            {
                var answered = HandleWithoutDocument(route, payload);
                TryRespond(context, HttpStatus.For(answered), answered);
                return;
            }

            // A bridge that is draining still answers questions and still
            // refuses work. Accepting a mutation now would queue it behind the
            // job that is keeping the drain open, which is the opposite of
            // what pressing Stop asked for.
            if (!BridgeState.AcceptsMutations(_state) && RouteContracts.IsMutation(route))
            {
                TryRespond(context, HttpStatus.For("bridge_stopping"), new Dictionary<string, object>
                {
                    ["error"] = "bridge_stopping",
                    ["state"] = _state,
                    ["detail"] = "El puente está " + _state + ": no acepta mutaciones nuevas. " +
                                 "Las consultas siguen respondiendo."
                });
                return;
            }

            if (string.Equals(route, "job/submit", StringComparison.OrdinalIgnoreCase))
            {
                if (!BridgeState.AcceptsMutations(_state))
                {
                    TryRespond(context, HttpStatus.For("bridge_stopping"), new Dictionary<string, object>
                    {
                        ["error"] = "bridge_stopping",
                        ["state"] = _state,
                        ["detail"] = "El puente está " + _state + " y no admite trabajos nuevos."
                    });
                    return;
                }
                var submission = SubmitJob(payload);
                TryRespond(context, SubmitStatus(submission), submission);
                return;
            }

            if (string.Equals(route, "capabilities", StringComparison.OrdinalIgnoreCase))
            {
                var described = _dispatcher.Invoke(
                    () => _router.DescribeCapabilities(), TimeSpan.FromSeconds(20));
                if (!described.Ok)
                {
                    TryRespond(context, HttpStatus.Unavailable,
                        Error("dispatcher_busy",
                            described.Rejection ?? described.Error?.Message ?? "sin respuesta"));
                    return;
                }
                var manifest = Decorate(AddBridgeRoutes(described.Value), described.WaitedMs);
                TryRespond(context, HttpStatus.For(manifest), manifest);
                return;
            }

            // RefreshSession rides along INSIDE the dispatched delegate rather
            // than after it. It reads Application.ActiveDocument, walks
            // doc.Models and asks for Units — Navisworks API calls, every one
            // — and Handle runs on a thread-pool worker, so doing it out here
            // was exactly the off-thread document access UiDispatcher exists
            // to prevent: "corruption or a hard crash, not an exception".
            // Marshalled here it costs no extra queue slot, and it still only
            // rewrites the session file when the fingerprint actually moved.
            var outcome = _dispatcher.Invoke(
                () =>
                {
                    var value = _router.Dispatch(route, payload);
                    RefreshSession();
                    return value;
                },
                TimeSpan.FromSeconds(120));

            if (!outcome.Ok)
            {
                // A missing document is an ordinary, recoverable state — a big
                // federation simply has not finished opening — so it answers
                // 409 with its own code instead of masquerading as a crash.
                var noDocument = outcome.Error is Router.NoDocumentException;
                var code = outcome.Rejection != null
                    ? "dispatcher_busy"
                    : noDocument ? "no_document" : "internal_error";
                var status = HttpStatus.For(code);

                var error = outcome.Rejection != null
                    ? Error(code, outcome.Rejection)
                    : noDocument
                        ? Error(code, outcome.Error.Message)
                        : Error(code, outcome.Error?.Message ?? "error desconocido");

                error["route"] = route;
                if (outcome.Error != null && !noDocument)
                {
                    error["exception"] = outcome.Error.GetType().Name;
                }
                error["bridge_queue"] = QueueInfo(outcome.WaitedMs);
                if (_router.CanRunAsJob(route))
                {
                    error["hint"] = "Esta ruta admite ejecución asíncrona: envíala por job/submit " +
                                    "y consulta job/status en vez de esperar en la conexión.";
                }
                TryRespond(context, status, error);
                return;
            }

            var exitAfterResponse = string.Equals(route, "application/exit", StringComparison.OrdinalIgnoreCase) &&
                                    Json.Bool(outcome.Value, "exit_requested", false);
            // Read from the reply rather than assumed from having got here.
            // A handler that answered `status: failed` or carried an `error`
            // used to go out as 200, and the Python client raises on status
            // codes — so a refusal arrived as a successful call.
            var answer = Decorate(outcome.Value, outcome.WaitedMs);
            TryRespond(context, HttpStatus.For(answer), answer);
            if (exitAfterResponse)
            {
                // The response stream is closed synchronously above.  Only
                // now may the process receive WM_CLOSE; doing it in the
                // handler made the bridge disappear before it could answer.
                ThreadPool.QueueUserWorkItem(_ => RequestApplicationExit());
            }
        }

        private static void RequestApplicationExit()
        {
            try
            {
                // Process.MainWindowHandle is zero for Navisworks instances
                // created through the Automation API even while their GUI is
                // visible.  Navisworks itself exposes the authoritative HWND;
                // post WM_CLOSE to that window first, then retain the process
                // API as a fallback for ordinary interactive launches.
                var process = System.Diagnostics.Process.GetCurrentProcess();
                var gui = Autodesk.Navisworks.Api.Application.Gui;
                var window = gui?.MainWindow;
                var handle = ExitWindowPolicy.PreferredHandle(
                    window?.Handle ?? IntPtr.Zero,
                    process.MainWindowHandle);
                if (handle != IntPtr.Zero && NativeMethods.PostMessage(
                        handle, NativeMethods.WmClose, IntPtr.Zero, IntPtr.Zero))
                {
                    return;
                }

                if (!process.CloseMainWindow())
                {
                    BridgeHost.Log("Navisworks no aceptó la solicitud de cierre de su ventana principal.");
                }
            }
            catch (Exception ex)
            {
                BridgeHost.Log("No se pudo solicitar el cierre de Navisworks: " + ex.Message);
            }
        }

        private static class NativeMethods
        {
            internal const uint WmClose = 0x0010;

            [DllImport("user32.dll", SetLastError = true)]
            [return: MarshalAs(UnmanagedType.Bool)]
            internal static extern bool PostMessage(
                IntPtr hWnd, uint message, IntPtr wParam, IntPtr lParam);
        }

        /// <summary>
        /// Adds the routes THIS class answers to the advertised route list.
        /// </summary>
        /// <remarks>
        /// The Router only knows the routes it dispatches, so a capabilities
        /// payload built from it alone omits everything the bridge handles
        /// itself — <c>job/submit</c> above all. The effect was total and
        /// invisible to unit tests: <c>Bridge.require("job/submit")</c> never
        /// found it, so EVERY asynchronous submission was refused with
        /// "update the add-in" by an add-in that supported it perfectly well.
        ///
        /// Found by the first live smoke test; the tests had stubbed the
        /// capability list and therefore agreed with themselves.
        /// </remarks>
        private Dictionary<string, object> AddBridgeRoutes(Dictionary<string, object> capabilities)
        {
            if (capabilities == null) return null;

            var advertised = capabilities.TryGetValue("routes", out var raw) && raw is List<object> list
                ? list
                : new List<object>();

            // Everything answered before the Router ever sees it.
            var mine = new List<string>(NonDocumentRoutes) { "job/submit", "capabilities" };
            foreach (var route in mine)
            {
                if (!advertised.Any(r => string.Equals(
                        Convert.ToString(r, CultureInfo.InvariantCulture), route,
                        StringComparison.OrdinalIgnoreCase)))
                {
                    advertised.Add(route);
                }
            }

            capabilities["routes"] = advertised
                .OrderBy(r => Convert.ToString(r, CultureInfo.InvariantCulture),
                    StringComparer.OrdinalIgnoreCase)
                .ToList();
            capabilities["bridge_routes"] = mine
                .OrderBy(r => r, StringComparer.OrdinalIgnoreCase)
                .Cast<object>()
                .ToList();
            return capabilities;
        }

        private Dictionary<string, object> Decorate(Dictionary<string, object> result, long waitedMs)
        {
            var payload = result ?? new Dictionary<string, object>();
            payload["bridge_queue"] = QueueInfo(waitedMs);
            payload["session_id"] = _sessionId;
            return payload;
        }

        /// <summary>
        /// Whether this request may be answered without a token.
        /// </summary>
        /// <remarks>
        /// GET only, and only <c>health</c>. A POST to the same path is the
        /// full, authenticated report; keeping the two on different verbs is
        /// what stops the minimal answer from quietly becoming the real one.
        /// </remarks>
        private static bool IsAnonymousLiveness(HttpListenerRequest request, string route)
            => string.Equals(request.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase) &&
               string.Equals(route, "health", StringComparison.OrdinalIgnoreCase);

        /// <summary>
        /// The only answer given without a token: four facts and nothing else.
        /// </summary>
        /// <remarks>
        /// This exists so a discovery tool can tell a live bridge from a dead
        /// port. That is all it needs, so that is all it gets.
        ///
        /// Everything previously here has been withdrawn on purpose, because
        /// each item told an unauthenticated caller something it had no need
        /// to know:
        ///
        /// * <c>session_id</c> and <c>port</c> — identifiers of a handshake
        ///   whose whole security model is that you must be able to read the
        ///   user's own AppData to learn them;
        /// * <c>document_open</c> — whether this person is working right now,
        ///   which is presence information about a human;
        /// * <c>api_version</c> and <c>session_contract</c> — a precise
        ///   build fingerprint for anyone choosing which weakness to try.
        ///
        /// <c>busy</c> stays because a discovery tool has to know whether to
        /// wait, and it reveals nothing about WHAT is open. The version stays
        /// because the whole point of asking is to find out what you are
        /// talking to; the detailed contract versions live behind the token,
        /// in <c>capabilities</c>.
        /// </remarks>
        private Dictionary<string, object> LivenessPayload() => new Dictionary<string, object>
        {
            ["ok"] = true,
            ["version"] = typeof(HttpBridge).Assembly.GetName().Version.ToString(),
            ["pid"] = (double)_pid,
            ["busy"] = JobManager.Active() != null
        };

        private Dictionary<string, object> HandleWithoutDocument(
            string route, Dictionary<string, object> payload)
        {
            switch (route.ToLowerInvariant())
            {
                case "job/status":
                {
                    var job = JobManager.Get(Json.Str(payload, "job_id"));
                    return job == null
                        ? Error("unknown_job", "No existe un trabajo con ese id en esta sesión.")
                        : job.ToJson();
                }
                case "job/list":
                    return new Dictionary<string, object>
                    {
                        ["jobs"] = JobManager.All().Select(j => (object)j.ToJson()).ToList(),
                        ["active"] = JobManager.Active()?.Id ?? string.Empty,
                        ["session_id"] = _sessionId
                    };
                case "job/cancel":
                    return JobManager.Cancel(Json.Str(payload, "job_id"));
                case "sessions":
                    return new Dictionary<string, object>
                    {
                        ["sessions"] = SessionStore.List()
                            .Select(s => (object)Redact(s))
                            .ToList(),
                        ["this_session"] = _sessionId,
                        ["pruned"] = (double)SessionStore.Prune()
                    };
                case "output_policy":
                    return PathPolicy.Default().Describe();
                case "profile/info":
                    return ProfileStore.Describe();
                case "profile/load":
                    return ProfileStore.Load(payload);
                case "profile/reset":
                    return ProfileStore.Reset();
                default:
                    return Error("unknown_route", "Ruta '" + route + "' no reconocida.");
            }
        }

        /// <summary>
        /// A session record with the bearer token removed.
        /// </summary>
        /// <remarks>
        /// Listing sessions must not hand every instance's token to a caller
        /// authenticated against one of them. A client that needs another
        /// instance's token reads that instance's own file, which is exactly
        /// the permission boundary the DACL enforces.
        /// </remarks>
        private static Dictionary<string, object> Redact(Dictionary<string, object> session)
        {
            var copy = new Dictionary<string, object>(session, StringComparer.OrdinalIgnoreCase);
            copy.Remove("token");
            copy["token_present"] = session.ContainsKey("token");
            copy["alive"] = SessionStore.IsAlive(session);
            return copy;
        }

        private Dictionary<string, object> SubmitJob(Dictionary<string, object> payload)
        {
            var route = Json.Str(payload, "route");
            var handler = _router.JobHandler(route);
            if (handler == null)
            {
                return Error("unsupported_job_route",
                    "La ruta '" + route + "' no se puede ejecutar como trabajo. " +
                    "Admiten job/submit: " + string.Join(", ",
                        _router.Routes.Where(_router.CanRunAsJob).OrderBy(r => r, StringComparer.OrdinalIgnoreCase)) + ".");
            }

            var body = payload.TryGetValue("payload", out var raw) && raw is Dictionary<string, object> inner
                ? inner
                : new Dictionary<string, object>();

            // The fingerprint, read once, before anything is decided.
            //
            // It has to be taken outside the admission lock — reading it means
            // reaching the Navisworks UI thread, and holding the job lock
            // across that would block every job/status poll for the duration.
            // What matters is that it is only ever an INPUT to the decision:
            // the accept-or-refuse itself happens in one indivisible step
            // below, so a stale reading here can lose the race but cannot open
            // a window in it.
            var fingerprint = _dispatcher
                .Invoke(() => DocumentContext.Fingerprint(Autodesk.Navisworks.Api.Application.ActiveDocument),
                    TimeSpan.FromSeconds(20));
            var currentFingerprint = fingerprint.Ok ? fingerprint.Value : string.Empty;

            var profile = ProfileStore.Active();
            var request = new JobRequest(
                jobId: "job-" + Guid.NewGuid().ToString("N").Substring(0, 10),
                route: route,
                payload: body,
                targetId: Json.Str(payload, "target_id"),
                sessionId: _sessionId,
                expectedFingerprint: currentFingerprint,
                idempotencyKey: Json.Str(body, "idempotency_key"),
                // Frozen here, at admission. The job is judged by the profile
                // it was accepted under, not by whatever is loaded when its
                // turn finally comes.
                profileChecksum: profile?.Checksum ?? string.Empty,
                profileCanonical: profile?.Canonical ?? string.Empty);

            // One call. The bridge no longer assembles ledger lookup, in-flight
            // lookup, collision check and submit into a sequence that two
            // concurrent POSTs could both walk through before either reserved
            // anything.
            var admission = JobManager.TrySubmit(
                request,
                j =>
                {
                    // Re-validated on the UI thread, immediately before the
                    // handler runs. Being right at submit does not authorise a
                    // mutation twenty minutes later on a document the operator
                    // has since swapped.
                    var outcome = _dispatcher.Invoke(() =>
                    {
                        var live = DocumentContext.Fingerprint(
                            Autodesk.Navisworks.Api.Application.ActiveDocument);
                        var blocker = JobPreflight.Blocker(request, live, ProfileStore.ActiveChecksum());
                        if (blocker != null) return blocker;
                        return handler(request.Payload, j);
                    }, TimeSpan.FromMinutes(30));
                    if (outcome.Ok) return outcome.Value;
                    throw outcome.Error ?? new InvalidOperationException(
                        outcome.Rejection ?? "El puente rechazó el trabajo.");
                });

            switch (admission.Outcome)
            {
                case Admission.ReplayedFromLedger:
                    return new Dictionary<string, object>
                    {
                        ["job_id"] = admission.JobId,
                        ["state"] = admission.TerminalState,
                        ["idempotent_replay"] = true,
                        ["outcome"] = admission.Outcome,
                        ["detail"] = admission.Detail,
                        ["result"] = admission.Replay
                    };

                case Admission.DeduplicatedInFlight:
                    return new Dictionary<string, object>
                    {
                        ["accepted"] = true,
                        ["outcome"] = admission.Outcome,
                        ["job_id"] = admission.JobId,
                        ["route"] = route,
                        ["state"] = JobManager.Get(admission.JobId)?.State ?? JobManager.Queued,
                        ["idempotent_replay"] = true,
                        ["session_id"] = _sessionId,
                        ["detail"] = admission.Detail,
                        ["poll"] = "job/status con {\"job_id\": \"" + admission.JobId + "\"}"
                    };

                case Admission.NewSubmission:
                    return new Dictionary<string, object>
                    {
                        ["accepted"] = true,
                        ["outcome"] = admission.Outcome,
                        ["job_id"] = admission.JobId,
                        ["route"] = route,
                        ["state"] = JobManager.Get(admission.JobId)?.State ?? JobManager.Queued,
                        ["session_id"] = _sessionId,
                        ["document_fingerprint"] = currentFingerprint,
                        ["payload_hash"] = request.PayloadHash,
                        ["profile_checksum"] = request.ProfileChecksum,
                        ["poll"] = "job/status con {\"job_id\": \"" + admission.JobId + "\"}"
                    };

                default:
                    // The outcome IS the error code, so the central mapper
                    // turns document_busy into 409 and a bad route into 404
                    // without this switch deciding anything.
                    var refusal = Error(admission.Outcome, admission.Detail);
                    refusal["outcome"] = admission.Outcome;
                    return refusal;
            }
        }


        /// <summary>
        /// The HTTP status for a submission, derived from what it produced.
        /// </summary>
        /// <remarks>
        /// Every answer used to be 202 Accepted, including the refusals. That
        /// is not a cosmetic problem: the Python client raises only on HTTP
        /// error codes, so a body saying <c>job_conflict</c> — "a mutation is
        /// already running on this document" — arrived as a SUCCESSFUL result
        /// and the caller was told its work had been accepted. Nothing had
        /// been queued. In a codebase whose whole rule is never to report work
        /// it did not do, the one route that reports acceptance was the one
        /// that could not be trusted about it.
        ///
        /// 409 for a conflict (retry when the running job finishes), 400 for a
        /// route that cannot be a job at all (retrying will never help), 202
        /// only when something was really queued or replayed.
        /// </remarks>
        /// <summary>The status a submission deserves, from its own outcome.</summary>
        /// <remarks>
        /// It used to answer 400 for everything it did not recognise, which
        /// told a client that a busy document was its own malformed request.
        /// Now the outcome names the case and the central mapper turns it into
        /// a status — the same mapper every other reply goes through.
        /// </remarks>
        private static int SubmitStatus(Dictionary<string, object> submission)
        {
            if (submission == null) return HttpStatus.ServerError;

            var outcome = Json.Str(submission, "outcome");
            if (!string.IsNullOrEmpty(outcome)) return HttpStatus.ForAdmission(outcome);

            if (submission.TryGetValue("error", out var raw))
            {
                return HttpStatus.For(Convert.ToString(raw, CultureInfo.InvariantCulture));
            }
            return HttpStatus.Accepted;
        }

        private Dictionary<string, object> QueueInfo(long waitedMs) => new Dictionary<string, object>
        {
            ["waited_ms"] = (double)waitedMs,
            ["depth"] = (double)_dispatcher.QueueDepth,
            ["capacity"] = (double)_dispatcher.Capacity
        };

        private static Dictionary<string, object> Error(string code, string detail)
            => new Dictionary<string, object> { ["error"] = code, ["detail"] = detail };

        /// <summary>
        /// Largest request body accepted, in bytes.
        /// </summary>
        /// <remarks>
        /// Generous on purpose — a clash/group payload can carry tens of
        /// thousands of GUIDs — but bounded, because <c>ReadToEnd</c> on an
        /// unbounded stream lets anything that reaches the port allocate
        /// until Navisworks dies, and the listener runs INSIDE the user's
        /// modelling session. The token stops a stranger reaching this code,
        /// but a bug in a legitimate client should not take the process down
        /// either.
        /// </remarks>
        private const long MaxBodyBytes = 32L * 1024 * 1024;

        /// <summary>The cap for a route, in bytes.</summary>
        /// <remarks>
        /// A profile is a settings file somebody edits by hand. Letting it
        /// inherit the clash-payload cap would mean 32 MB of JSON parsed into
        /// a dictionary on the listener thread for no legitimate reason.
        /// </remarks>
        private static long LimitFor(string route)
            => string.Equals(route, "profile/load", StringComparison.OrdinalIgnoreCase)
                ? ProfileStore.MaxProfileBytes
                : MaxBodyBytes;

        /// <summary>
        /// Reads the request body, bounded in BYTES.
        /// </summary>
        /// <remarks>
        /// The counting itself lives in <see cref="BodyReader"/>, which has no
        /// Navisworks reference and can therefore be exercised against a real
        /// stream by the test runner. See the note there about why counting
        /// characters was not the same as counting bytes.
        /// </remarks>
        private static string ReadBody(HttpListenerRequest request, long limit)
        {
            if (!request.HasEntityBody) return string.Empty;
            using (var stream = request.InputStream)
            {
                return BodyReader.Read(stream, request.ContentLength64, limit);
            }
        }

        private static void TryRespond(HttpListenerContext context, int status, object payload)
        {
            try
            {
                var bytes = Encoding.UTF8.GetBytes(Json.Write(payload));
                context.Response.StatusCode = status;
                context.Response.ContentType = "application/json; charset=utf-8";
                context.Response.ContentLength64 = bytes.Length;
                context.Response.OutputStream.Write(bytes, 0, bytes.Length);
                context.Response.OutputStream.Close();
            }
            catch
            {
                // The client hung up. Nothing useful left to do.
            }
        }

        // -------------------------------------------------------- session

        private static string NewToken()
        {
            var bytes = new byte[32];
            using (var rng = RandomNumberGenerator.Create())
            {
                rng.GetBytes(bytes);
            }
            return BitConverter.ToString(bytes).Replace("-", string.Empty).ToLowerInvariant();
        }

        /// <summary>Comparison that does not leak length or prefix by timing.</summary>
        private static bool FixedTimeEquals(string supplied, string expected)
        {
            if (supplied == null || expected == null) return false;
            if (supplied.Length != expected.Length) return false;
            var diff = 0;
            for (var i = 0; i < expected.Length; i++) diff |= supplied[i] ^ expected[i];
            return diff == 0;
        }

        /// <summary>Kept for the message that tells a human where to look.</summary>
        public static string SessionFilePath() => SessionStore.LegacyPath();

        public string SessionRecordPath() => SessionStore.PathFor(_pid, _sessionId);

        private void WriteSessionFile()
        {
            try
            {
                var doc = Autodesk.Navisworks.Api.Application.ActiveDocument;
                var process = System.Diagnostics.Process.GetCurrentProcess();

                var payload = new Dictionary<string, object>
                {
                    ["contract"] = SessionStore.ContractVersion,
                    ["api_version"] = Capabilities.ApiVersion,
                    ["session_id"] = _sessionId,
                    ["port"] = (double)_port,
                    ["token"] = _token,
                    ["pid"] = (double)_pid,
                    ["process_started"] = process.StartTime.ToUniversalTime()
                        .ToString("o", CultureInfo.InvariantCulture),
                    ["product"] = Router.SafeProductName(),
                    ["addin_version"] = typeof(HttpBridge).Assembly.GetName().Version.ToString(),
                    ["document"] = DocumentContext.Describe(doc, includePath: true),
                    ["started"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                    ["heartbeat"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture)
                };
                SessionStore.Write(payload, _pid, _sessionId);
            }
            catch (Exception ex)
            {
                // Without the file the MCP server cannot authenticate, so this
                // is worth surfacing rather than swallowing.
                throw new InvalidOperationException(
                    "No se pudo escribir el archivo de sesión: " + ex.Message, ex);
            }
        }

        // ------------------------------------------------ session freshness

        /// <summary>
        /// The document state the session file was last written with.
        /// </summary>
        /// <remarks>
        /// Compared before every rewrite so that a refresh is a no-op when
        /// nothing moved. Navisworks fires its document events more than once
        /// for a single user action — opening a federated file raises
        /// ActiveDocumentChanged repeatedly as models append — and rewriting
        /// the handshake on each one is pointless IO on a file other
        /// processes are reading.
        /// </remarks>
        // A sentinel no real value can equal: the published state is always
        // "0:<hash>" or "1:<hash>", so the first refresh always writes.
        private string _publishedFingerprint = "unset";

        private Autodesk.Navisworks.Api.Document _watched;
        private readonly object _watchGate = new object();

        /// <summary>
        /// Rewrites this instance's session record when, and only when, the
        /// document it describes has actually changed.
        /// </summary>
        /// <remarks>
        /// The token and the session id are instance fields and are NOT
        /// regenerated: a refresh must never invalidate a handshake a client
        /// is already using. Only this instance's file is touched.
        ///
        /// **Must be called on the Navisworks UI thread.** It reads the active
        /// document, walks its models and asks for its units. The document
        /// event handlers already run there; the per-request refresh reaches it
        /// by riding inside the dispatched delegate in <see cref="Handle"/>.
        /// </remarks>
        public void RefreshSession(string reason = "")
        {
            if (!_running) return;
            try
            {
                var doc = Autodesk.Navisworks.Api.Application.ActiveDocument;
                var open = doc != null && !doc.IsClear;
                // The published state is (fingerprint + open), so closing the
                // last document is a change even though both fingerprints are
                // empty.
                var current = (open ? "1" : "0") + ":" + DocumentContext.Fingerprint(doc);
                if (string.Equals(current, _publishedFingerprint, StringComparison.Ordinal)) return;

                WriteSessionFile();
                _publishedFingerprint = current;
                if (!string.IsNullOrEmpty(reason))
                {
                    BridgeHost.Log("Sesión refrescada (" + reason + "): documento " +
                                   (open ? "abierto" : "cerrado") + ".");
                }
            }
            catch (Exception ex)
            {
                // A stale session record degrades to "the client asks health
                // and gets the truth"; failing the request would be worse.
                BridgeHost.Log("No se pudo refrescar la sesión: " + ex.Message);
            }
        }

        /// <summary>
        /// Subscribes to the document lifecycle so the session file follows it.
        /// </summary>
        /// <remarks>
        /// Three events, all present since API v21 (Navisworks 2024):
        ///
        /// * <c>Application.ActiveDocumentChanged</c> — open, close and swap.
        /// * <c>Document.FileSaved</c> — a save can clear the modified flag.
        /// * <c>Document.FileNameChanged</c> — what Save As raises, and the
        ///   one that matters most here: the fingerprint is identity and
        ///   includes the path, so a Save As changes it. Without this,
        ///   `navis_current_target` kept reporting the OLD identity until
        ///   somebody happened to call health.
        ///
        /// The document-level handlers are rebound whenever the active
        /// document changes, because they belong to the document instance and
        /// not to the application.
        /// </remarks>
        private void SubscribeDocumentEvents()
        {
            try
            {
                Autodesk.Navisworks.Api.Application.ActiveDocumentChanged += OnActiveDocumentChanged;
                RebindDocumentWatch();
            }
            catch (Exception ex)
            {
                // Events are an optimisation over the per-request refresh
                // below, so losing them degrades freshness, not correctness.
                BridgeHost.Log("No se pudieron suscribir los eventos de documento: " + ex.Message);
            }
        }

        private void UnsubscribeDocumentEvents()
        {
            try
            {
                Autodesk.Navisworks.Api.Application.ActiveDocumentChanged -= OnActiveDocumentChanged;
            }
            catch { /* shutting down */ }
            lock (_watchGate) { DetachWatched(); }
        }

        private void OnActiveDocumentChanged(object sender, EventArgs e)
        {
            RebindDocumentWatch();
            RefreshSession("documento activo cambió");
        }

        private void OnDocumentSaved(object sender, EventArgs e)
            => RefreshSession("documento guardado");

        private void OnDocumentRenamed(object sender, EventArgs e)
            => RefreshSession("la ruta del documento cambió");

        private void RebindDocumentWatch()
        {
            lock (_watchGate)
            {
                DetachWatched();
                try
                {
                    var doc = Autodesk.Navisworks.Api.Application.ActiveDocument;
                    if (doc == null || doc.IsClear) return;
                    doc.FileSaved += OnDocumentSaved;
                    doc.FileNameChanged += OnDocumentRenamed;
                    _watched = doc;
                }
                catch (Exception ex)
                {
                    BridgeHost.Log("No se pudo observar el documento: " + ex.Message);
                }
            }
        }

        private void DetachWatched()
        {
            if (_watched == null) return;
            try
            {
                _watched.FileSaved -= OnDocumentSaved;
                _watched.FileNameChanged -= OnDocumentRenamed;
            }
            catch { /* the document may already be gone */ }
            _watched = null;
        }

        /// <summary>
        /// Complains loudly if the runtime directory is reachable by others.
        /// </summary>
        private static void AuditRuntimeSecurity()
        {
            var audit = SessionStore.Audit(SessionStore.SessionsDir());
            if (audit.TryGetValue("secure", out var flag) && flag is bool secure && secure) return;

            var findings = audit.TryGetValue("findings", out var raw) && raw is List<object> list
                ? string.Join(" ", list.Select(f => Convert.ToString(f, CultureInfo.InvariantCulture)))
                : "(sin detalle)";
            BridgeHost.Log(
                "AVISO DE SEGURIDAD: " + SessionStore.SessionsDir() +
                " no quedó restringido al usuario actual. " + findings +
                " El token de sesión que da control sobre este Navisworks vive ahí.");
        }

        public void Dispose()
        {
            // The IDisposable path is process teardown, not the operator
            // pressing Stop. It does not wait and does not claim a cancel:
            // `Dispose(reason)` records what was still running and removes the
            // session so nothing else can call in.
            Dispose("plugin_unload");
            try { _listener?.Close(); } catch { /* nothing left to do */ }
        }
    }
}
