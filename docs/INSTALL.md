# Instalación

NavisCoord tiene **dos piezas**, y casi todos los problemas vienen de
confundirlas.

| Pieza | Qué es | Quién la instala |
|---|---|---|
| Servidor MCP | Python: `mcp`, `reportlab`, `pillow` | El launcher, solo, al arrancar |
| Complemento de Navisworks | Un DLL .NET dentro de Navisworks | **Requiere Navisworks cerrado** |

## Requisitos

- **Python 3.10 o superior.** Es el suelo real: el código usa uniones PEP 604
  (`X | None`) en anotaciones evaluadas en runtime, y 3.9 lanza.
- **Autodesk Navisworks Manage 2024, 2025 o 2026**, con licencia. El
  complemento compila contra el API de la instalación local; no se
  redistribuye ningún ensamblado de Autodesk.
- **.NET SDK 8 o superior** solo si vas a compilar el complemento tú. Los
  proyectos apuntan a .NET Framework 4.8 y restauran
  `Microsoft.NETFramework.ReferenceAssemblies`; no dependen de que Visual
  Studio haya instalado casualmente el Developer Pack. Sí necesitan el API de
  la versión local de Navisworks.

### Qué NO hace el launcher

**No instala un Python.** Crea un entorno virtual a partir del intérprete con
el que el cliente lo arrancó, en
`%LOCALAPPDATA%\NavisCoord\runtime\py3XX-<identidad>\venv`, e instala ahí las tres
dependencias. Si ese intérprete es demasiado antiguo, lo dice y se detiene —
no hay nada más que honestamente pueda hacer.

La identidad incluye ejecutable, arquitectura, ABI, versión del plugin y
contrato de dependencias. El entorno se construye en una carpeta hermana bajo
lock y solo se publica cuando imports **y versiones** son válidos; dos hosts no
ejecutan `venv`/`pip` simultáneamente sobre la misma carpeta.

Si el runtime no se puede provisionar, el servidor **no muere**: arranca en
modo degradado con una sola herramienta, `navis_install_status`, que dice qué
falló, con qué intérprete, y el comando exacto para arreglarlo a mano.

## Instalar el complemento

Requiere **Navisworks cerrado**. Para una instalación normal no necesitas
Visual Studio, Git ni el SDK de .NET. Descarga
[`Install-NavisCoord.ps1`](../Install-NavisCoord.ps1) y ejecútalo desde la
carpeta donde quedó guardado:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-NavisCoord.ps1
```

El instalador detecta Navisworks Manage 2024–2026, descarga el ZIP de la
[última versión publicada](https://github.com/HorizunGroup/naviscoord-mcp/releases/latest),
verifica su SHA-256, rechaza rutas inesperadas y publica los archivos con
rollback. Se niega a continuar si Navisworks está abierto.

- `-Version 2025` limita la instalación a una versión.
- `-ReleaseTag v0.3.1` fija una versión publicada concreta.
- `-WhatIf` muestra qué haría sin copiar nada.
- `-SelfTest` prueba el instalador sin descargar ni instalar el add-in.

### Instalar compilando desde el código fuente

Esta vía es para desarrolladores. Requiere Git, .NET SDK 8+ y el API de cada
Navisworks local. Desde la raíz del repositorio:

```powershell
.\install.ps1
```

Detecta cada Navisworks Manage 2024–2026 instalado, compila el complemento
contra el API de **cada uno** —cada versión trae su propio ensamblado, un DLL
no sirve para todas— e instala por versión verificando cada copia.

- `.\install.ps1 -Version 2025` limita la compilación a una versión.
- `.\install.ps1 -Uninstall` desinstala de todas las versiones detectadas.

### Sin herramientas de compilación

Descomprime el paquete del complemento de tu versión en:

```
%APPDATA%\Autodesk\Navisworks Manage <versión>\Plugins\NavisCoord\
```

> Cada [release](https://github.com/HorizunGroup/naviscoord-mcp/releases) trae
> un ZIP por versión de Navisworks. Descarga el tuyo, compruébalo contra
> `SHA256SUMS.txt` y descomprímelo en esa carpeta. `install.ps1` compila desde
> el código en vez de descargar, y para eso necesita el SDK de .NET y una
> instalación local de Navisworks.

## Registrar el servidor MCP

### Como plugin (Claude Code)

```
/plugin marketplace add HorizunGroup/naviscoord-mcp
/plugin install naviscoord-mcp@horizun-navis
```

El manifiesto usa `${CLAUDE_PLUGIN_ROOT}` para localizar el launcher.

### Como plugin (Codex)

Agrega este repositorio como marketplace y habilita el plugin
`naviscoord-mcp@horizun-navis`. Codex instala el paquete en su caché y sustituye
`${CLAUDE_PLUGIN_ROOT}` por la raíz real del plugin; usar una ruta relativa
sería incorrecto porque el directorio de trabajo del proceso no está
garantizado. Este flujo se verificó contra una instalación real de Codex.

### A mano

Copia `.mcp.json.example` a `.mcp.json` y ajusta las rutas:

```json
{
  "mcpServers": {
    "horizun-navis-mcp": {
      "command": "python",
      "args": ["-m", "naviscoord.mcp_server"],
      "env": {
        "PYTHONPATH": "RUTA/AL/REPO/server",
        "PYTHONIOENCODING": "utf-8",
        "NAVISCOORD_OUTPUT_ROOTS": "D:\\Coordinacion\\salidas"
      }
    }
  }
}
```

Con `pip install -e .` desde `server/`, el `PYTHONPATH` sobra.

En Windows, si `python` abre la Microsoft Store en vez de ejecutar, usa la
ruta absoluta del intérprete real:
`python -c "import sys; print(sys.executable)"`.

## Variables de entorno

| Variable | Para qué |
|---|---|
| `NAVISCOORD_OUTPUT_ROOTS` | Carpetas donde se autoriza escribir (separadas por `;` en Windows). Ver [SECURITY-MODEL.md](SECURITY-MODEL.md) |
| `NAVISCOORD_SESSION` | Apunta al archivo o carpeta de sesión. Ver [SESSIONS.md](SESSIONS.md) |
| `LOCALAPPDATA` | Raíz del runtime, sesiones y log. La leen ambos lados |

## Verificar

1. `navis_health` → versión de Navisworks y documento.
2. `navis_capabilities` → rutas y operaciones que ofrece el complemento.
3. `navis_sessions` → instancias activas.
4. Con un modelo abierto: `navis_discover`, luego `navis_analyze`.

## Problemas frecuentes

**Solo aparece `navis_install_status`.** Falló el runtime de Python. Llámala:
dice el intérprete, la versión, el destino y el comando de `pip`.

**«No encuentro ninguna sesión activa».** Navisworks está cerrado, o el
servidor MCP corre como otro usuario. En ese caso apunta `NAVISCOORD_SESSION`
al `session.json` real.

**«El complemento no expone esta ruta».** El add-in instalado es anterior al
servidor. Cierra Navisworks y ejecuta `install.ps1`. `navis_capabilities`
lista lo que sí ofrece.

**Dos versiones abiertas a la vez.** Conviven, cada una con su puerto y su
sesión. Elige con `navis_target`; una mutación sin elegir se rechaza.

**Cambié código Python y no se refleja.** El servidor MCP es un subproceso
que cargó el código al arrancar. `navis_health` lo reporta en
`server.code_newer_on_disk`; reinicia el cliente.

**El complemento no aparece en Navisworks.** Comprueba que el DLL esté en
`%APPDATA%\Autodesk\Navisworks Manage <versión>\Plugins\NavisCoord\NavisCoord.dll`
y revisa `%LOCALAPPDATA%\NavisCoord\bridge.log`: el complemento nunca abre
diálogos desde el puente, todo lo escribe ahí.
