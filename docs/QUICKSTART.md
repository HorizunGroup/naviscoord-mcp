# NavisCoord quick start — Windows

**English** · [Español](QUICKSTART.es.md)

This guide is for someone who uses Navisworks but does not build software.
Allow about five minutes. Your model stays on this computer.

## Before you start

You need:

- Windows;
- Autodesk Navisworks **Manage** 2024, 2025 or 2026;
- Python 3.10 or newer;
- Claude Code or Codex with local MCP support.

You do **not** need Visual Studio, the .NET SDK or `pip` when installing from a
published release.

## 1. Install the Navisworks add-in

Close every Navisworks window. Download
[`Install-NavisCoord.ps1`](../Install-NavisCoord.ps1), right-click the file and
choose **Run with PowerShell**.

The installer:

1. detects Navisworks Manage 2024–2026;
2. downloads the matching ZIP from the latest GitHub release;
3. verifies its SHA-256 checksum;
4. installs only the NavisCoord files under your Autodesk plugins folder;
5. leaves personal profiles and unrelated plugins untouched.

If Windows blocks the downloaded script, open PowerShell in the download
folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-NavisCoord.ps1
```

## 2. Add the MCP plugin

### Claude Code

Paste these two commands into Claude Code:

```text
/plugin marketplace add HorizunGroup/naviscoord-mcp
/plugin install naviscoord-mcp@horizun-navis
```

Restart Claude Code after installation.

### Codex

Add `https://github.com/HorizunGroup/naviscoord-mcp` as a plugin marketplace,
install `naviscoord-mcp@horizun-navis`, and restart Codex.

The plugin builds an isolated Python environment on first launch. It does not
install packages into your normal Python.

## 3. Confirm the connection

Open Navisworks Manage and a coordination model. Ask your AI client:

> Verify my NavisCoord installation. Do not modify or save the model.

The healthy path calls `navis_health`, `navis_capabilities` and
`navis_sessions`. It should name your Navisworks version and say that a
document is open.

## 4. Run the first safe analysis

Paste:

> Analyze the open Navisworks model in read-only mode. Discover its structure,
> summarize the clashes, identify root causes and give me a coordination work
> plan. Do not configure tests, change statuses, apply appearance overrides or
> save anything.

NavisCoord should move through:

```text
health → discover → analyze → root causes → work plan
```

## What success looks like

You should receive:

- the number of raw clashes and the smaller number of actionable issues;
- noise and duplicate counts, with reasons;
- ranked root causes and hotspots;
- work packages grouped by trade/zone rather than one giant clash list;
- warnings for anything the tool could not trace or verify.

## If it does not work

| What you see | What to do |
|---|---|
| Only `navis_install_status` exists | Call it. It reports the Python interpreter and exact repair command |
| No active Navisworks session | Open Navisworks and a model; check `%LOCALAPPDATA%\NavisCoord\bridge.log` |
| Several sessions are listed | Choose one with `navis_target` before any write |
| “Add-in is older than the server” | Close Navisworks and run `Install-NavisCoord.ps1` again |
| The add-in is absent | Confirm you installed **Manage**, not only Freedom; see [full installation help](INSTALL.md) |

Never send a model, bearer token or client-specific profile in a public issue.
Installation questions belong in
[GitHub Discussions](https://github.com/HorizunGroup/naviscoord-mcp/discussions).
