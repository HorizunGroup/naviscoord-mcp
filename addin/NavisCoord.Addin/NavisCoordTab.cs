using System.Windows.Forms;
using Autodesk.Navisworks.Api.Plugins;

namespace NavisCoord
{
    /// <summary>
    /// La pestaña NavisCoord: todos los botones del producto en un solo sitio.
    /// </summary>
    /// <remarks>
    /// Antes cada acción era un <c>AddInPlugin</c> suelto, y Navisworks los
    /// amontona en "Tool add-ins" mezclados con los add-ins de terceros —
    /// cinco botones de NavisCoord repartidos entre exportadores ajenos, sin
    /// una pestaña que los reuniera.
    ///
    /// La razón por la que se hizo así está registrada: un tab por
    /// <c>RibbonLayout</c> depende de un esquema XAML no documentado y, cuando
    /// falla, falla en silencio. Eso dejó de ser una apuesta el 2026-08-12, al
    /// medirse el esquema exacto que sí renderiza en 2024, 2025 y 2026 (ver la
    /// cabecera de NavisCoordRibbon.xaml). Si algún día la pestaña no aparece,
    /// el sospechoso es ese archivo, no este.
    ///
    /// Aquí no vive NADA de lo que un paso hace: cada comando llama al mismo
    /// <see cref="WorkflowSteps"/> que atienden las rutas HTTP, para que un
    /// botón y una herramienta MCP no puedan divergir.
    /// </remarks>
    [Plugin("NavisCoordTab", "NVCD",
        DisplayName = "NavisCoord",
        ToolTip = "Coordinación NavisCoord")]
    [RibbonLayout("NavisCoordRibbon.xaml")]
    [RibbonTab("ID_TabNavisCoord")]
    [Command("ID_NavisCoordEstado",
        Icon = "nc_16.png", LargeIcon = "nc_32.png",
        DisplayName = "Estado del puente",
        ToolTip = "Ver la versión y el estado del puente; iniciarlo o detenerlo si hace falta",
        ExtendedToolTip = "Muestra qué versión de NavisCoord tiene cargada Navisworks en este momento y si el puente está CORRIENDO (con su puerto) o DETENIDO.\n\nConsultar es seguro: primero informa y después pregunta si quieres cambiar el estado, con \"No\" por defecto. El puente arranca solo al abrir Navisworks, así que en condiciones normales basta con mirar y cerrar.")]
    [Command("ID_NavisCoordConfigCoord",
        Icon = "ncC_16.png", LargeIcon = "ncC_32.png",
        DisplayName = "Configurar coordinación",
        ToolTip = "Crear los search sets y la matriz de clash, y aplicar las reglas del perfil",
        ExtendedToolTip = "Hace lo mismo que \"1. Configurar\" y además aplica las reglas de exclusión del perfil.\n\nSe conserva como botón aparte porque es lo que hacía el botón de este nombre antes de que NavisCoord tuviera pestaña propia; quien ya lo usaba encuentra el mismo comportamiento.")]
    [Command("ID_NavisCoordAuditar",
        Icon = "nc0_16.png", LargeIcon = "nc0_32.png",
        DisplayName = "0. Auditar modelos",
        ToolTip = "Paso 0 — Auditar los modelos anexados",
        ExtendedToolTip = "Valida ANTES de coordinar: que cada modelo esté co-ubicado con los demás (un modelo publicado sin coordenadas compartidas queda a kilómetros y sus tests dan 0 choques FALSOS), que el nombre traiga la disciplina, y cuántos elementos aporta.\n\nSi reporta un modelo desplazado, el responsable debe corregir coordenadas compartidas y republicar.")]
    [Command("ID_NavisCoordConfigurar",
        Icon = "nc1_16.png", LargeIcon = "nc1_32.png",
        DisplayName = "1. Configurar",
        ToolTip = "Paso 1 — Crear los search sets y la matriz de clash del perfil",
        ExtendedToolTip = "Crea las carpetas de search sets por disciplina y los clash tests definidos en el perfil, enlazados a esas carpetas.\n\nNo corre nada y respeta lo que ya exista. Úsalo al abrir una coordinación nueva o cuando el perfil cambie.")]
    [Command("ID_NavisCoordCorrer",
        Icon = "nc2_16.png", LargeIcon = "nc2_32.png",
        DisplayName = "2. Correr tests",
        ToolTip = "Paso 2 — Correr todos los clash tests",
        ExtendedToolTip = "Ejecuta todos los tests de la matriz (igual que Update All de Clash Detective).\n\nNavisworks queda ocupado mientras calcula: según el tamaño del federado puede tardar varios minutos.")]
    [Command("ID_NavisCoordAgrupar",
        Icon = "nc3_16.png", LargeIcon = "nc3_32.png",
        DisplayName = "3. Agrupar por nivel",
        ToolTip = "Paso 3 — Agrupar los choques de cada test por nivel",
        ExtendedToolTip = "Organiza los resultados de cada test en grupos por nivel, leyendo el nivel de los elementos que chocan — la misma organización con la que se preparan las incidencias.\n\nSolo agrupa lo que esté suelto: lo ya agrupado se respeta, así que se puede repetir tras cada corrida.")]
    public class NavisCoordTab : CommandHandlerPlugin
    {
        public override int ExecuteCommand(string name, params string[] parameters)
        {
            switch (name)
            {
                // El único comando que no es un paso del flujo: no toca el
                // modelo, solo cuenta qué hay cargado y cómo está.
                //
                // Informa PRIMERO y pregunta después. Antes este botón
                // alternaba el puente de una vez, así que oprimirlo para ver
                // si estaba vivo lo mataba y el servidor MCP perdía la
                // conexión. El "No" es el botón por defecto: un Enter
                // distraído no debe tumbar el puente.
                //
                // Con su propio try/catch porque no pasa por WorkflowSteps,
                // que es quien atrapa por los demás: un fallo al abrir el
                // puerto (otra instancia lo tiene tomado) subiría hasta
                // Navisworks en vez de explicarse.
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

                case "ID_NavisCoordConfigCoord": return ConfigurePlugin.Execute();

                case "ID_NavisCoordAuditar": return WorkflowSteps.Execute(WorkflowSteps.Audit);
                case "ID_NavisCoordConfigurar": return WorkflowSteps.Execute(WorkflowSteps.Configure);
                case "ID_NavisCoordCorrer": return WorkflowSteps.Execute(WorkflowSteps.Run);
                case "ID_NavisCoordAgrupar": return WorkflowSteps.Execute(WorkflowSteps.Group);

                // Un Id del XAML que no exista aquí llega como un clic que no
                // hace nada; devolver 1 lo deja registrado en vez de fingir
                // que se ejecutó.
                default: return 1;
            }
        }

        public override CommandState CanExecuteCommand(string commandId)
        {
            // Siempre habilitados: cada paso valida por su cuenta que haya un
            // documento abierto y un perfil cargado, y explica qué falta. Un
            // botón gris no dice por qué está gris.
            return new CommandState(true);
        }
    }
}
