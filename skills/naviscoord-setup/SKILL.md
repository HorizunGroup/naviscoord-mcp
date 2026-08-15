---
name: naviscoord-setup
description: Instala, actualiza, repara y verifica NavisCoord — el complemento de Autodesk Navisworks y su runtime local — cuando se usa como plugin de Claude o Codex. Úsala si las herramientas navis_* no aparecen, si solo aparece navis_install_status, si el puente no responde, si Navisworks no muestra NavisCoord en la pestaña Add-Ins, o si el usuario pide instalar, actualizar o diagnosticar el plugin.
---

# Instalar y verificar NavisCoord

NavisCoord tiene **dos piezas** y casi todos los problemas vienen de confundirlas:

| Pieza | Qué es | Quién la instala |
|---|---|---|
| Runtime del servidor MCP | Python: `mcp`, `reportlab`, `pillow` | El launcher, solo, al arrancar |
| Complemento de Navisworks | Un DLL .NET dentro de Navisworks | **Requiere Navisworks cerrado** — nadie puede hacerlo automáticamente |

El launcher resuelve la primera. La segunda es la que hay que atender aquí.

## Diagnóstico: empieza siempre por aquí

Llama `navis_health`.

- **Responde con la versión de Navisworks** → todo bien, no hay nada que instalar.
- **Solo existe `navis_install_status`** → falló el runtime de Python. Llámala: dice el intérprete, el destino y el comando exacto de `pip` para arreglarlo a mano.
- **`No hay respuesta del complemento en Navisworks`** → el runtime está bien, falta el complemento o Navisworks está cerrado. Sigue abajo.
- **`No encuentro una sesión activa`** → Navisworks está cerrado, o el servidor MCP corre como otro usuario. En ese caso apunta `NAVISCOORD_SESSION` al archivo de esa instancia dentro de `%LOCALAPPDATA%\NavisCoord\sessions\`.

## Instalar el complemento de Navisworks

Requiere **Navisworks cerrado**. El script lo comprueba y se niega si está abierto: con el proceso vivo el DLL queda bloqueado y la copia falla a medias, que es peor que no copiar.

Desde la raíz del repo o del plugin:

```powershell
.\install.ps1
```

Detecta cada Navisworks Manage 2024–2026 instalado, compila el complemento contra el API de **cada uno** — cada versión trae su propio ensamblado, un DLL no sirve para todas — e instala por versión verificando cada copia.

- `.\install.ps1 -Version 2025` limita a una versión.
- `.\install.ps1 -Uninstall` desinstala de todas.

**Sin herramientas de compilación**: descarga `NavisCoord-addin-NW<versión>.zip` del release en GitHub y descomprime en
`%APPDATA%\Autodesk\Navisworks Manage <versión>\Plugins\NavisCoord\`.

Después abre Navisworks: el puente arranca solo. Si no, pestaña **Add-Ins → NavisCoord**.

## Verificar

1. `navis_health` → debe devolver versión de Navisworks y documento.
2. Con un modelo abierto: `navis_discover` → cómo está estructurado el modelo.
3. `navis_analyze` → el análisis completo.

## Problemas frecuentes

**Cambié de versión de Navisworks y falla la primera llamada.** No debería: el cliente relee la sesión y reintenta una vez. Si persiste, `navis_health` de nuevo; si sigue, el complemento no está instalado en esa versión.

**Dos versiones abiertas a la vez.** Conviven en puertos distintos y cada una publica su propia sesión. Llama `navis_sessions` y elige la instancia con `navis_target`; una mutación ambigua se rechaza en vez de adivinar.

**Cambié código Python del servidor y no se refleja.** El servidor MCP es un subproceso que cargó el código al arrancar. `navis_health` lo reporta en `server.code_newer_on_disk`; reinicia el cliente para cargarlo.

**El complemento no aparece en Navisworks.** Comprueba que el DLL esté en
`%APPDATA%\Autodesk\Navisworks Manage <versión>\Plugins\NavisCoord\NavisCoord.dll`
y revisa `%LOCALAPPDATA%\NavisCoord\bridge.log`: el complemento nunca abre diálogos, todo lo escribe ahí.
