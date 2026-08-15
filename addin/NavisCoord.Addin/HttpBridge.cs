using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

namespace NavisCoord
{
    /// <summary>
    /// Loopback HTTP endpoint the MCP server talks to.
    /// </summary>
    /// <remarks>
    /// Every published Navisworks bridge leaves an unauthenticated HTTP
    /// listener on localhost, which means any process on the machine —
    /// including a browser tab hitting the port from a page — can drive the
    /// user's model. This one requires a session token generated at startup
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
    /// Port 8781 rather than the 8765 the other bridges all picked, so the two
    /// can coexist while a team migrates.
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

            _running = true;
            SessionStore.Prune();
            WriteSessionFile();
            AuditRuntimeSecurity();
            SubscribeDocumentEvents();

            _worker = new Thread(Loop)
            {
                IsBackground = true,
                Name = "NavisCoord.HttpBridge"
            };
            _worker.Start();
        }

        public void Stop()
        {
            if (!_running) return;
            UnsubscribeDocumentEvents();
            _running = false;
            try { _listener?.Stop(); } catch { /* already tearing down */ }
            // Only OUR entry. Deleting the shared file was the bug that made
            // closing 2024 break the live 2026 bridge.
            SessionStore.Remove(_pid, _sessionId);
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
                TryRespond(context, 200, LivenessPayload());
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

            // Json.ParseObject is deliberately forgiving and returns an empty
            // object for malformed input rather than throwing, so a bad body
            // reaches the handler as "no arguments" instead of a 500. That is
            // right for a MISSING body and wrong for a malformed one: a
            // truncated payload would be acted on as though the caller had
            // asked for the defaults, and answered 200.
            var payload = Json.ParseObject(body);
            if (!LooksLikeJsonObject(body, payload))
            {
                TryRespond(context, 400, Error(
                    "invalid_json",
                    "El cuerpo no es un objeto JSON válido. Se recibieron " +
                    Encoding.UTF8.GetByteCount(body) + " bytes."));
                return;
            }

            // Answered here, on the listener thread, precisely so they keep
            // answering while a job owns the UI thread.
            if (NonDocumentRoutes.Contains(route))
            {
                TryRespond(context, 200, HandleWithoutDocument(route, payload));
                return;
            }

            if (string.Equals(route, "job/submit", StringComparison.OrdinalIgnoreCase))
            {
                var submission = SubmitJob(payload);
                TryRespond(context, SubmitStatus(submission), submission);
                return;
            }

            if (string.Equals(route, "capabilities", StringComparison.OrdinalIgnoreCase))
            {
                var described = _dispatcher.Invoke(
                    () => _router.DescribeCapabilities(), TimeSpan.FromSeconds(20));
                TryRespond(context, described.Ok ? 200 : 503,
                    described.Ok
                        ? Decorate(AddBridgeRoutes(described.Value), described.WaitedMs)
                        : Error("bridge_busy", described.Rejection ?? described.Error?.Message ?? "sin respuesta"));
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
                var status = outcome.Rejection != null ? 503 : noDocument ? 409 : 500;

                var error = outcome.Rejection != null
                    ? Error("bridge_busy", outcome.Rejection)
                    : noDocument
                        ? Error("no_document", outcome.Error.Message)
                        : Error("command_failed", outcome.Error?.Message ?? "error desconocido");

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

            TryRespond(context, 200, Decorate(outcome.Value, outcome.WaitedMs));
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

            var key = Json.Str(body, "idempotency_key");

            // A finished operation replays from the ledger…
            if (IdempotencyLedger.TryGet(key, out var cached))
            {
                return new Dictionary<string, object>
                {
                    ["job_id"] = string.Empty,
                    ["state"] = JobManager.Completed,
                    ["idempotent_replay"] = true,
                    ["result"] = cached
                };
            }

            // …and one still in flight returns THAT job rather than a second
            // one. The ledger is only written when the work finishes, so
            // without this a client that retried during execution got a
            // duplicate submission — refused as a conflict, which is safe but
            // tells the caller the wrong story about what happened.
            var inFlight = JobManager.FindByIdempotencyKey(key);
            if (inFlight != null)
            {
                return new Dictionary<string, object>
                {
                    ["accepted"] = true,
                    ["job_id"] = inFlight.Id,
                    ["route"] = inFlight.Operation,
                    ["state"] = inFlight.State,
                    ["idempotent_replay"] = true,
                    ["session_id"] = _sessionId,
                    ["detail"] = "Ya había un trabajo con esta idempotency_key; se devuelve ese, " +
                                 "no se envió uno nuevo.",
                    ["poll"] = "job/status con {\"job_id\": \"" + inFlight.Id + "\"}"
                };
            }

            // Collision BEFORE touching the document: asking Navisworks for a
            // fingerprint while a job owns the UI thread blocks for the whole
            // dispatcher timeout, so a submit during a long run took twenty
            // seconds to answer "busy" — from the route whose entire purpose
            // is to answer immediately.
            if (JobManager.WouldCollide(null, out var busy))
            {
                return Error("job_conflict", busy);
            }

            var fingerprint = _dispatcher
                .Invoke(() => DocumentContext.Fingerprint(Autodesk.Navisworks.Api.Application.ActiveDocument),
                    TimeSpan.FromSeconds(20));
            var currentFingerprint = fingerprint.Ok ? fingerprint.Value : string.Empty;

            if (JobManager.WouldCollide(currentFingerprint, out var collision))
            {
                return Error("job_conflict", collision);
            }

            var job = JobManager.Submit(
                route,
                j =>
                {
                    // The work still runs on the UI thread — the API leaves no
                    // choice — but the HTTP caller is no longer waiting on it.
                    var outcome = _dispatcher.Invoke(() => handler(body, j), TimeSpan.FromMinutes(30));
                    if (outcome.Ok) return outcome.Value;
                    throw outcome.Error ?? new InvalidOperationException(
                        outcome.Rejection ?? "El puente rechazó el trabajo.");
                },
                targetId: Json.Str(payload, "target_id"),
                sessionId: _sessionId,
                fingerprint: currentFingerprint,
                idempotencyKey: key,
                // Frozen here, before the job can start: the profile in force
                // at submit is the one it will be judged by, and ProfileStore
                // refuses to swap it while the job runs.
                profileChecksum: ProfileStore.ActiveChecksum(),
                cancellable: Router.IsCancellable(route));

            return new Dictionary<string, object>
            {
                ["accepted"] = true,
                ["job_id"] = job.Id,
                ["route"] = route,
                ["state"] = job.State,
                ["session_id"] = _sessionId,
                ["document_fingerprint"] = currentFingerprint,
                ["poll"] = "job/status con {\"job_id\": \"" + job.Id + "\"}"
            };
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
        private static int SubmitStatus(Dictionary<string, object> submission)
        {
            if (submission == null) return 500;
            if (!submission.TryGetValue("error", out var raw)) return 202;

            var code = Convert.ToString(raw, CultureInfo.InvariantCulture) ?? string.Empty;
            switch (code)
            {
                case "job_conflict":
                    return 409;
                case "unsupported_job_route":
                    return 400;
                default:
                    return 400;
            }
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

        /// <summary>
        /// Whether a body that carried content actually parsed into one.
        /// </summary>
        /// <remarks>
        /// An absent body is legitimate — most routes take no arguments — so
        /// emptiness alone is not an error. Content that produced nothing is:
        /// that is a payload the caller believes it sent.
        /// </remarks>
        private static bool LooksLikeJsonObject(string body, Dictionary<string, object> parsed)
        {
            // An absent body is legitimate — most routes take no arguments —
            // so emptiness alone is not an error. Content that is not one
            // complete object is: that is a payload the caller believes it
            // sent in full.
            if (string.IsNullOrWhiteSpace(body)) return true;
            return Json.IsWellFormedObject(body);
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
            Stop();
            try { _listener?.Close(); } catch { /* nothing left to do */ }
        }
    }
}
