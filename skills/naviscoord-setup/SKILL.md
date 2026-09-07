---
name: naviscoord-setup
description: Instalar, actualizar, reparar y verificar NavisCoord en ChatGPT Work de escritorio, Claude Desktop, Claude Code y Codex cuando faltan las herramientas navis_* o el puente de Navisworks no responde.
---

# Instalar y verificar NavisCoord

NavisCoord conecta un runtime MCP local con un complemento de Navisworks Manage 2024–2026. El runtime distribuido incluye Python y sus dependencias. No requiere instalar Python, una cuenta de OpenAI Platform, una API key ni un túnel para Work de escritorio.

## Diagnóstico

Llama `navis_health` cuando esté disponible. Comprueba las versiones del servidor y del complemento, la sesión y el documento; una conexión viva por sí sola no prueba que el análisis esté actualizado.

Si no aparecen herramientas, revisa la configuración del cliente y el registro de arranque del servidor. `scripts/Start-Mcp.ps1` usa el runtime incluido en el plugin o descarga la versión exacta del release con verificación SHA-256. `scripts/Install-Runtime.ps1` detecta archivos incompletos y los repara conservando una copia del directorio anterior. En instalaciones antiguas basadas en Python puede aparecer `navis_install_status`: su diagnóstico corresponde al launcher anterior.

Si no se encuentra una sesión, comprueba que Navisworks esté abierto con el complemento instalado bajo el mismo usuario. Con varias instancias, usa `navis_sessions` y `navis_target`.

## Instalación del cliente

- ChatGPT Work de escritorio y Codex: extrae el ZIP de desktop del release y ejecuta `scripts/Install-DesktopPlugin.ps1`. Reinicia la app y abre **Plugins → Personal → NavisCoord → Install**. El instalador conserva las entradas ajenas del catálogo personal y comunica su nombre si no es `personal`.
- Claude Desktop: instala el `.mcpb` del release mediante Extensions. Incluye su propio ejecutable.
- Claude Code: añade el marketplace `HorizunGroup/naviscoord-mcp` e instala `naviscoord-mcp@horizun-navis`.
- Registro directo: `scripts/Configure-Clients.ps1 -Executable <ruta real a naviscoord-mcp.exe> -Client <ClaudeDesktop|ClaudeCode|Codex>`.

Verifica los paquetes contra `SHA256SUMS.txt` del mismo release. Documentación completa: https://github.com/HorizunGroup/naviscoord-mcp/blob/main/docs/INSTALL.md.

## Complemento de Navisworks

Con Navisworks cerrado, ejecuta `Install-NavisCoord.ps1` descargado del repositorio oficial. Selecciona los binarios de la versión instalada, verifica sus hashes y respalda los archivos gestionados. No sustituyas un DLL mientras Navisworks lo utiliza. Si hay cambios sin guardar, respeta la decisión de guardado del usuario antes de cerrar.

Abre Navisworks y verifica `navis_health`, luego `navis_discover` con un modelo. Los binarios se instalan en `%APPDATA%\Autodesk\Navisworks Manage <año>\Plugins\NavisCoord\`. El registro está en `%LOCALAPPDATA%\NavisCoord\bridge.log`.

Después de actualizar, reinicia el cliente MCP y vuelve a verificar versiones y documento. Para análisis usa `navis_analyze`; para comprobar vigencia usa `navis_analysis_state`. Repite el análisis cuando cambien el modelo, los conjuntos o los resultados de clash.
