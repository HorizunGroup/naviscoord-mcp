using System.Windows.Forms;
using Autodesk.Navisworks.Api.Plugins;

namespace NavisCoord
{
    /// <summary>
    /// La pestaña NavisCoord: el estado del puente, y nada más.
    /// </summary>
    /// <remarks>
    /// Llegó a tener seis botones — auditar, configurar, correr, agrupar —
    /// heredados de cuando cada paso era un <c>AddInPlugin</c> suelto. Salieron
    /// todos: cada uno duplicaba una herramienta MCP que ya hacía lo mismo
    /// (<c>navis_audit_models</c>, <c>navis_configure</c>,
    /// <c>navis_build_sets</c>, <c>navis_run_tests</c>,
    /// <c>navis_apply_groups</c>), y una segunda forma de invocar lo mismo es
    /// una segunda forma de que se desincronicen.
    ///
    /// Lo que el add-in aporta y el cliente MCP no puede darse solo es esto:
    /// saber si el puente está arriba y qué versión está cargada. El resto del
    /// motor sigue completo — <see cref="CoordinationWorkflow"/> lo atienden
    /// las rutas HTTP en WorkflowHandlers.
    /// </remarks>
    [Plugin("NavisCoordTab", "NVCD",
        DisplayName = "NavisCoord",
        ToolTip = "Puente de coordinación NavisCoord")]
    [RibbonLayout("NavisCoordRibbon.xaml")]
    [RibbonTab("ID_TabNavisCoord")]
    [Command("ID_NavisCoordEstado",
        Icon = "nc_16.png", LargeIcon = "nc_32.png",
        DisplayName = "Estado del puente",
        ToolTip = "Ver la versión y el estado del puente; iniciarlo o detenerlo si hace falta",
        ExtendedToolTip = "Muestra qué versión de NavisCoord tiene cargada Navisworks en este momento y si el puente está CORRIENDO (con su puerto) o DETENIDO.\n\nConsultar es seguro: primero informa y después pregunta si quieres cambiar el estado, con \"No\" por defecto. El puente arranca solo al abrir Navisworks, así que en condiciones normales basta con mirar y cerrar.\n\nTodo lo demás se pide desde el cliente MCP, que es donde vive el flujo.")]
    public class NavisCoordTab : CommandHandlerPlugin
    {
        public override int ExecuteCommand(string name, params string[] parameters)
        {
            switch (name)
            {
                // Informa PRIMERO y pregunta después. Este botón alternaba el
                // puente de una vez, así que oprimirlo para ver si estaba vivo
                // lo mataba y el servidor MCP perdía la conexión. El "No" es el
                // botón por defecto: un Enter distraído no debe tumbarlo.
                case "ID_NavisCoordEstado":
                    try
                    {
                        var running = BridgeHost.IsRunning;
                        var answer = MessageBox.Show(
                            BridgeHost.StatusReport() + "\n\n" +
                            (running ? "¿Detener el puente?" : "¿Iniciar el puente?"),
                            "NavisCoord " + BridgeHost.Version,
                            MessageBoxButtons.YesNo,
                            MessageBoxIcon.Information,
                            MessageBoxDefaultButton.Button2);

                        if (answer == DialogResult.Yes)
                        {
                            ConfigurePlugin.Inform(running ? BridgeHost.Stop() : BridgeHost.Start());
                        }
                        return 0;
                    }
                    catch (System.Exception ex)
                    {
                        return ConfigurePlugin.Fail(
                            "No se pudo cambiar el estado del puente: " + ex.Message);
                    }

                // Un Id del XAML que no exista aquí llega como un clic que no
                // hace nada; devolver 1 lo deja registrado en vez de fingir
                // que se ejecutó.
                default: return 1;
            }
        }

        public override CommandState CanExecuteCommand(string commandId)
        {
            return new CommandState(true);
        }
    }
}
