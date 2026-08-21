# Inicio rápido de NavisCoord — Windows

[English](QUICKSTART.md) · **Español**

Esta guía es para quien usa Navisworks pero no desarrolla software. Reserva
cinco minutos. El modelo permanece en este equipo.

## Antes de empezar

Necesitas Windows, Autodesk Navisworks **Manage** 2024–2026, Python 3.10+ y
Claude Code o Codex. No necesitas Visual Studio, el SDK .NET ni ejecutar `pip`
si instalas desde un release.

## 1. Instalar el complemento

Cierra todas las ventanas de Navisworks. Descarga
[`Install-NavisCoord.ps1`](../Install-NavisCoord.ps1) y elige **Ejecutar con
PowerShell**, o abre PowerShell en la carpeta de descargas:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-NavisCoord.ps1
```

El instalador detecta tus versiones de Navisworks, descarga los ZIP del último
release, verifica `SHA256SUMS.txt`, revisa que el ZIP solo tenga los cuatro
archivos permitidos y publica con rollback. Conserva perfiles personales y
otros plugins.

## 2. Instalar el plugin MCP

Claude Code:

```text
/plugin marketplace add HorizunGroup/naviscoord-mcp
/plugin install naviscoord-mcp@horizun-navis
```

En Codex agrega `https://github.com/HorizunGroup/naviscoord-mcp` como
marketplace, instala `naviscoord-mcp@horizun-navis` y reinicia Codex.

En el primer arranque se crea un entorno Python privado y versionado. No se
modifica tu instalación normal de Python.

## 3. Verificar

Abre Navisworks Manage y un documento de coordinación. Pega:

> Verifica mi instalación de NavisCoord. No modifiques ni guardes el modelo.

El camino sano llama `navis_health`, `navis_capabilities` y `navis_sessions`.
Debe decir qué versión de Navisworks está abierta y que existe un documento.

## 4. Primer análisis seguro

Pega:

> Analiza el modelo abierto en modo de solo lectura. Descubre su estructura,
> resume las interferencias, identifica causas raíz y arma el plan de
> coordinación. No configures tests, cambies estados, apliques colores ni
> guardes nada.

El flujo esperado es:

```text
health → discover → analyze → root causes → work plan
```

## Qué deberías recibir

- clashes crudos frente a incidencias accionables;
- ruido y duplicados descontados con sus motivos;
- causas raíz, zonas críticas y prioridad explicada;
- paquetes por disciplina/zona en lugar de una lista gigante;
- advertencias de todo lo que no pudo trazarse o verificarse.

## Problemas frecuentes

| Síntoma | Acción |
|---|---|
| Solo aparece `navis_install_status` | Llámala: entrega el intérprete y el comando exacto de reparación |
| No hay sesión activa | Abre Navisworks y un modelo; revisa `%LOCALAPPDATA%\NavisCoord\bridge.log` |
| Hay varias sesiones | Elige una con `navis_target` antes de escribir |
| Add-in más antiguo que el servidor | Cierra Navisworks y vuelve a ejecutar el instalador |
| El complemento no aparece | Confirma que tienes **Manage**, no solo Freedom; consulta [Instalación](INSTALL.md) |

No publiques modelos, tokens, rutas de clientes ni perfiles corporativos en un
issue. Las dudas van a
[Discussions](https://github.com/HorizunGroup/naviscoord-mcp/discussions).
