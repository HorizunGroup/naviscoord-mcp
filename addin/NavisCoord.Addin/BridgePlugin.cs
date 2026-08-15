using System;
using System.Globalization;
using System.IO;
using System.Reflection;
using Autodesk.Navisworks.Api.Plugins;

namespace NavisCoord
{
    /// <summary>
    /// Owns the bridge lifetime. Single instance for the whole Navisworks
    /// process.
    /// </summary>
    internal static class BridgeHost
    {
        private static readonly object Gate = new object();
        private static UiDispatcher _dispatcher;
        private static HttpBridge _bridge;

        public static bool IsRunning => _bridge != null && _bridge.IsRunning;
        public static int Port => _bridge?.Port ?? HttpBridge.DefaultPort;

        /// <summary>
        /// La versión tal como la lee una persona ("0.2.3").
        /// </summary>
        /// <remarks>
        /// Se saca del ensamblado y no de una constante a propósito: lo que
        /// hay que poder responder es "qué DLL tiene Navisworks cargado en
        /// este momento", que no siempre es el que está en el repo ni el que
        /// se acaba de compilar. Una constante se copia y miente; el
        /// ensamblado no.
        /// </remarks>
        public static string Version
        {
            get
            {
                var asm = typeof(BridgeHost).Assembly;
                var info = (AssemblyInformationalVersionAttribute)Attribute.GetCustomAttribute(
                    asm, typeof(AssemblyInformationalVersionAttribute));
                var raw = info?.InformationalVersion ?? asm.GetName().Version.ToString();

                // Release estampa "0.2.3+<commit>". El hash sirve en un log, no
                // en un cuadro de diálogo.
                var plus = raw.IndexOf('+');
                return plus > 0 ? raw.Substring(0, plus) : raw;
            }
        }

        /// <summary>Una línea con lo que un humano necesita: si está arriba y dónde.</summary>
        public static string StatusLine()
            => IsRunning
                ? $"CORRIENDO — escuchando en 127.0.0.1:{Port}"
                : "DETENIDO — el servidor MCP no puede conectarse";

        /// <summary>
        /// Alterna el puente y devuelve el parte completo: versión, estado
        /// resultante y el detalle de lo que acaba de pasar.
        /// </summary>
        /// <remarks>
        /// Existe porque el botón antes mandaba el mensaje de Start/Stop a
        /// <see cref="Log"/> — o sea, a un archivo — y en pantalla no salía
        /// absolutamente nada: no había forma de saber si el puente estaba
        /// vivo, ni qué versión estaba cargada, salvo abriendo el log.
        /// </remarks>
        public static string ToggleReport()
        {
            var detail = IsRunning ? Stop() : Start();
            return $"NavisCoord {Version}\n\nEstado: {StatusLine()}\n\n{detail}";
        }

        /// <summary>
        /// Starts the listener. Must be called from the Navisworks UI thread:
        /// the dispatcher binds its marshalling handle to the calling thread,
        /// and binding it to anything else makes every later request deadlock.
        /// </summary>
        public static string Start()
        {
            lock (Gate)
            {
                if (IsRunning) return $"NavisCoord ya está escuchando en el puerto {_bridge.Port}.";

                _dispatcher = _dispatcher ?? new UiDispatcher();
                _bridge = new HttpBridge(_dispatcher, new Router());
                _bridge.Start();

                return $"NavisCoord escuchando en 127.0.0.1:{_bridge.Port}.\n\n" +
                       $"Sesión {_bridge.SessionId} registrada en:\n{_bridge.SessionRecordPath()}";
            }
        }

        public static string Stop()
        {
            lock (Gate)
            {
                if (!IsRunning) return "NavisCoord no estaba activo.";
                _bridge.Dispose();
                _bridge = null;
                return "NavisCoord detenido y token de sesión eliminado.";
            }
        }

        public static void Shutdown()
        {
            lock (Gate)
            {
                try { _bridge?.Dispose(); } catch { /* process is closing */ }
                try { _dispatcher?.Dispose(); } catch { /* process is closing */ }
                _bridge = null;
                _dispatcher = null;
            }
        }

        /// <summary>
        /// Appends a line to the bridge log.
        /// </summary>
        /// <remarks>
        /// Deliberately never a MessageBox. A modal dialog raised on the
        /// Navisworks UI thread blocks that thread, and since every bridge
        /// request is marshalled onto it, one unnoticed popup behind the main
        /// window freezes the entire tool until somebody clicks OK. A log
        /// file says the same thing without being able to stop anything.
        /// </remarks>
        public static void Log(string message)
        {
            try
            {
                var path = LogPath();
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.AppendAllText(
                    path,
                    DateTime.Now.ToString("s", CultureInfo.InvariantCulture) + "  " + message + Environment.NewLine);
            }
            catch
            {
                // Logging must never be the reason something fails.
            }
            System.Diagnostics.Trace.WriteLine("NavisCoord: " + message);
        }

        // Same resolution as SessionStore.Root() so the log sits beside the
        // session registry even on a redirected profile.
        public static string LogPath() => Path.Combine(SessionStore.Root(), "bridge.log");
    }

    /// <summary>
    /// Auto-start hook. Navisworks loads EventWatcherPlugin implementations
    /// during startup without any user interaction, which is what lets the
    /// MCP server find a live bridge without asking someone to click a ribbon
    /// button first.
    /// </summary>
    [Plugin("NavisCoord.AutoStart", "HRZN",
        DisplayName = "NavisCoord AutoStart",
        ToolTip = "Arranca el puente de coordinación NavisCoord al abrir Navisworks")]
    public sealed class AutoStartPlugin : EventWatcherPlugin
    {
        public override void OnLoaded()
        {
            try
            {
                BridgeHost.Log(BridgeHost.Start());
            }
            catch (Exception ex)
            {
                // A failure here must never stop Navisworks from opening, and
                // must never raise a dialog: the user may not be at the
                // machine, and a modal box would block the UI thread forever.
                BridgeHost.Log("Fallo al arrancar: " + ex.Message);
            }
        }

        public override void OnUnloading()
        {
            BridgeHost.Shutdown();
        }
    }

}
