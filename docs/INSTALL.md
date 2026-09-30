# Install NavisCoord in your desktop AI client

The live product has two components: the native Navisworks add-in and the MCP runtime. The release runtime includes Python and its dependencies. It needs no API key. Windows and a licensed Navisworks **Manage** 2024, 2025 or 2026 are required.

## 1. Install the Navisworks add-in

Close Navisworks, download `Install-NavisCoord.ps1` from the repository and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-NavisCoord.ps1
```

The installer checks the release SHA-256 manifest, selects the installed Navisworks versions and backs up managed files. It preserves unrelated add-ins. Open Navisworks after installation.

## 2. ChatGPT Desktop — Work, and Codex

Download the **desktop plugin ZIP** from the release, verify it against `SHA256SUMS.txt`, and extract it. In the extracted directory run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-DesktopPlugin.ps1
```

Restart the desktop app. Open **Plugins → Personal → NavisCoord → Install**, then start a new Work conversation. The package runs locally and uses the same Navisworks bridge as the other clients. No OpenAI Platform account, tunnel or API key is needed for this local plugin.

The installer keeps existing personal marketplace entries and backs up changed files. Its plugin lives in `%USERPROFILE%\plugins\naviscoord-mcp`; the personal catalog is `%USERPROFILE%\.agents\plugins\marketplace.json`.

For Codex CLI, the installed personal plugin can also be enabled with:

```text
codex plugin add naviscoord-mcp@personal
```

The marketplace name is reported by the installer if your existing personal catalog uses another name.

## 3. Claude Desktop

Download the release **`.mcpb`** and verify its SHA-256. Double-click it, or choose **Settings → Extensions → Advanced settings → Install Extension**. The bundle includes the executable and its runtime; Claude Code and a separate Python installation are unnecessary.

Restart Claude Desktop and request `navis_health`. A local extension's directory verification status is separate from its artifact checksums. The release record reports actual external approval status.

The optional `-installer.mcpb` is a smaller distribution of the same approved launcher, used by [Smithery](https://smithery.ai/servers/pabloalejandrozg/naviscoord-mcp). Verify it against its adjacent `.sha256` file. Its first launch downloads the complete versioned runtime from GitHub and checks the release SHA256; later launches verify and reuse the local cache. Internet access is needed for the first download or repair. All 53 tools remain available. The full `.mcpb` above already includes that runtime.

## 4. Claude Code

Install the repository marketplace:

```text
/plugin marketplace add HorizunGroup/naviscoord-mcp
/plugin install naviscoord-mcp@horizun-navis
```

The plugin starts the pinned standalone runtime. Alternatively, register an extracted runtime directly:

```powershell
claude mcp add --transport stdio --scope user horizun-navis-mcp -- 'C:\path\to\naviscoord-mcp.exe'
```

For direct registration in Codex:

```powershell
codex mcp add horizun-navis-mcp -- 'C:\path\to\naviscoord-mcp.exe'
```

Use your actual extracted executable path. `scripts/Configure-Clients.ps1` can register the same executable in Claude Desktop, Claude Code and Codex while preserving unrelated settings.

### One registration per client

The same server must not be registered twice in one client: each session could then talk to a different instance, such as an old plugin version. `Configure-Clients.ps1` therefore also turns off the duplicate, backs up every file it changes with a dated copy, prints what it changed, and never touches other servers:

| Client | When you register the executable directly | When you use the plugin |
|---|---|---|
| Claude Code | The `naviscoord-mcp@<marketplace>` plugin is disabled (`enabledPlugins` = `false` in `~/.claude/settings.json`); its cache is kept. | The direct user-scope entry is removed with `claude mcp remove <name> --scope user`. |
| Codex | Navisworks server sections with another name are removed from `config.toml`, and the plugin is disabled. | Navisworks server sections are removed from `config.toml`. |
| Claude Desktop | Entries for this server under an older name are removed from `claude_desktop_config.json`. | Not applicable. |

A registration is only removed when the one you keep is really in place. If you install the Claude Code plugin from the marketplace, remove a direct entry left from before with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Resolve-DuplicateRegistrations.ps1 -Keep Plugin -Client ClaudeCode
```

Add `-Check` to preview without changing anything. Restart the client afterwards. An extension installed from the `.mcpb` bundle in Claude Desktop is managed by Claude Desktop itself; if you also register the executable there, remove one of the two in **Settings → Extensions**.

## First verification

Ask: **“Call navis_health, identify the active document, then analyze its clashes without modifying or saving the model.”** Use `navis_sessions` and `navis_target` when several Navisworks instances are open.

For an enforced document-read-only connection, set `NAVISCOORD_READ_ONLY=1` in the MCP server's environment. Reports may still be generated in the authorized output directory.

## Updates and recovery

Close Navisworks before replacing the add-in. Install the new client package, restart the client, then call `navis_health`. The plugin installer retains prior plugin directories and marketplace backups; the add-in installer retains its managed-file backups. Keep them until the new installation has been verified.

The executable's protocol can be tested without an AI subscription using `scripts/verify_stdio.py` from a development environment. The [release record](RELEASE-1.0-VERIFICATION.md) lists the checks performed on the distributed artifacts.

For source-based Python development, see [development installation](INSTALL-DEVELOPMENT.md). Those Python prerequisites apply to development, not to the standalone desktop package.
