<#
Leaves exactly one NavisCoord server per client program.

NavisCoord can be registered twice in the same client: as a plugin and as a
direct MCP server entry (or under an older server name). Each session may then
talk to a different instance, including an old one. This script keeps the
registration you choose and turns the other one off, touching only NavisCoord.

  -Keep Direct  the server entry `horizun-navis-mcp` is the one to keep.
                Claude Code: the `naviscoord-mcp@<marketplace>` plugin is disabled
                (enabledPlugins.<key> = false in ~/.claude/settings.json).
                Codex: the plugin is disabled (enabled = false) and Navisworks
                server sections with another name are removed from config.toml.
  -Keep Plugin  the plugin is the one to keep. Claude Code: the direct user-scope
                entry is removed with `claude mcp remove --scope user`.
                Codex: Navisworks server sections are removed from config.toml.

It never edits ~/.claude.json by hand (Claude Code rewrites it while it runs),
never deletes the plugin cache, never touches servers of other products, backs
up every file it changes with a dated copy, and is idempotent. A registration
is only removed or disabled when the one to keep is really in place, so it can
never leave a client with none. The result is a JSON list of what was done.
#>
[CmdletBinding()]
param(
    [ValidateSet('Direct','Plugin')][string]$Keep = 'Direct',
    [ValidateSet('ClaudeCode','Codex','All')][string]$Client = 'All',
    [string]$HomeDirectory = $env:USERPROFILE,
    [string]$CodexHome = '',
    [string]$ClaudeCli = '',
    [string]$CurrentName = 'horizun-navis-mcp',
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
$homeRoot = [IO.Path]::GetFullPath($HomeDirectory)
if (-not $CodexHome) {
    $CodexHome = if ($env:CODEX_HOME -and $homeRoot -eq [IO.Path]::GetFullPath($env:USERPROFILE)) { $env:CODEX_HOME } else { Join-Path $homeRoot '.codex' }
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$results = New-Object System.Collections.ArrayList
$utf8 = New-Object Text.UTF8Encoding($false)

function Add-Result([string]$client, [string]$action, [string]$item, [string]$detail) {
    [void]$results.Add([pscustomobject]@{ client = $client; action = $action; item = $item; detail = $detail })
}

# Only the Navisworks product: by server name, or by what the entry launches.
function Test-NavisEntry([string]$name, [string]$launch) {
    return (($name -match 'navis') -or ($launch -match 'naviscoord'))
}

function Read-TextFile([string]$path) {
    $bytes = [IO.File]::ReadAllBytes($path)
    $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    return [pscustomobject]@{ text = $utf8.GetString($bytes, $(if ($bom) { 3 } else { 0 }), $bytes.Length - $(if ($bom) { 3 } else { 0 })); bom = $bom }
}

function Write-TextFileSafely([string]$path, [string]$text, [bool]$bom) {
    Copy-Item -LiteralPath $path -Destination ($path + '.backup-' + $stamp) -Force
    $payload = $utf8.GetBytes($text)
    if ($bom) { $payload = [byte[]](@(0xEF, 0xBB, 0xBF) + $payload) }
    $temporary = $path + '.new-' + [guid]::NewGuid().ToString('N')
    [IO.File]::WriteAllBytes($temporary, $payload)
    [IO.File]::Replace($temporary, $path, [NullString]::Value)
}

# ---------------------------------------------------------------- Claude Code
function Get-ClaudePluginKeys([object]$settings, [bool]$onlyEnabled) {
    $keys = @()
    if ($settings -and $settings.PSObject.Properties['enabledPlugins'] -and $settings.enabledPlugins) {
        foreach ($property in $settings.enabledPlugins.PSObject.Properties) {
            if ($property.Name -match '^naviscoord-mcp@' -and ((-not $onlyEnabled) -or $property.Value -eq $true)) { $keys += $property.Name }
        }
    }
    return $keys
}

function Get-ClaudeUserServers {
    $config = Join-Path $homeRoot '.claude.json'
    if (-not (Test-Path -LiteralPath $config)) { return , @() }
    try { $json = (Read-TextFile $config).text | ConvertFrom-Json } catch { return $null }
    $found = @()
    if ($json.PSObject.Properties['mcpServers'] -and $json.mcpServers) {
        foreach ($server in $json.mcpServers.PSObject.Properties) {
            $launch = [string]$server.Value.command + ' ' + (@($server.Value.args) -join ' ')
            if (Test-NavisEntry $server.Name $launch) { $found += $server.Name }
        }
    }
    return , @($found)
}

function Resolve-Claude {
    $settingsPath = Join-Path $homeRoot '.claude\settings.json'
    $settings = $null; $file = $null
    if (Test-Path -LiteralPath $settingsPath) {
        $file = Read-TextFile $settingsPath
        try { $settings = $file.text | ConvertFrom-Json } catch {
            Add-Result 'Claude Code' 'skipped' $settingsPath 'settings.json is not valid JSON; left untouched.'; return
        }
    }
    $direct = Get-ClaudeUserServers
    if ($null -eq $direct) { Add-Result 'Claude Code' 'skipped' '.claude.json' 'Could not read the user configuration; left untouched.'; return }

    if ($Keep -eq 'Direct') {
        if ($direct -notcontains $CurrentName) {
            Add-Result 'Claude Code' 'skipped' $CurrentName 'The direct server is not registered; the plugin was left as it is.'; return
        }
        $keys = Get-ClaudePluginKeys $settings $false
        if ($keys.Count -eq 0) { Add-Result 'Claude Code' 'none' 'plugin' 'No NavisCoord plugin is listed in settings.json.'; return }
        $text = $file.text; $changed = $false; $done = @()
        foreach ($key in $keys) {
            if ($settings.enabledPlugins.$key -ne $true) { Add-Result 'Claude Code' 'none' $key 'Plugin is already disabled.'; continue }
            $pattern = '("' + [regex]::Escape($key) + '"\s*:\s*)true\b'
            if ([regex]::Matches($text, $pattern).Count -ne 1) {
                Add-Result 'Claude Code' 'skipped' $key 'The plugin key could not be located unambiguously; disable it with /plugin.'; continue
            }
            if ($Check) { Add-Result 'Claude Code' 'would-disable' $key 'Plugin would be disabled (enabledPlugins = false).'; continue }
            $text = [regex]::Replace($text, $pattern, '${1}false')
            $changed = $true
            $done += $key
        }
        if ($changed) {
            $after = $text | ConvertFrom-Json   # must still parse
            if (@($after.PSObject.Properties).Count -ne @($settings.PSObject.Properties).Count -or
                @($after.enabledPlugins.PSObject.Properties).Count -ne @($settings.enabledPlugins.PSObject.Properties).Count) { throw 'Unexpected settings result.' }
            foreach ($property in $settings.enabledPlugins.PSObject.Properties) {
                $expected = if ($keys -contains $property.Name) { $false } else { $property.Value }
                if ($after.enabledPlugins.($property.Name) -ne $expected) { throw 'Unexpected settings result.' }
            }
            Write-TextFileSafely $settingsPath $text $file.bom
        }
        foreach ($key in $done) { Add-Result 'Claude Code' 'disabled' $key 'Plugin disabled (enabledPlugins = false); its cache was kept. Restart Claude Code.' }
        return
    }

    # Keep Plugin: drop the direct entry, but only while the plugin is enabled.
    if ($direct.Count -eq 0) { Add-Result 'Claude Code' 'none' 'direct server' 'No direct NavisCoord server is registered.'; return }
    if ((Get-ClaudePluginKeys $settings $true).Count -eq 0) {
        Add-Result 'Claude Code' 'skipped' ($direct -join ', ') 'The NavisCoord plugin is not enabled; the direct server was kept.'; return
    }
    $cli = $ClaudeCli
    if (-not $cli) { $command = Get-Command claude -ErrorAction SilentlyContinue; if ($command) { $cli = $command.Source } }
    if (-not $cli) { Add-Result 'Claude Code' 'skipped' ($direct -join ', ') 'Claude Code CLI not found; run: claude mcp remove <name> --scope user'; return }
    foreach ($name in $direct) {
        if ($Check) { Add-Result 'Claude Code' 'would-remove' $name 'Direct user-scope server would be removed.'; continue }
        $savedProfile = $env:USERPROFILE; $savedHome = $env:HOME
        try {
            $env:USERPROFILE = $homeRoot; $env:HOME = $homeRoot
            $output = & $cli mcp remove $name --scope user 2>&1
            $code = $LASTEXITCODE
        } finally { $env:USERPROFILE = $savedProfile; $env:HOME = $savedHome }
        if ($code -ne 0) { Add-Result 'Claude Code' 'failed' $name ('claude mcp remove failed: ' + (($output | Out-String).Trim())); continue }
        if ((Get-ClaudeUserServers) -contains $name) { Add-Result 'Claude Code' 'failed' $name 'The entry is still present after removal.'; continue }
        Add-Result 'Claude Code' 'removed' $name 'Direct user-scope server removed with claude mcp remove; the plugin remains. Restart Claude Code.'
    }
}

# ---------------------------------------------------------------- Codex
# Splits a TOML dotted key such as mcp_servers."a.b".env into its parts.
function Split-TomlKey([string]$key) {
    $parts = New-Object System.Collections.ArrayList
    $i = 0
    while ($i -lt $key.Length) {
        while ($i -lt $key.Length -and ($key[$i] -eq ' ' -or $key[$i] -eq "`t")) { $i++ }
        if ($i -ge $key.Length) { break }
        $c = $key[$i]
        if ($c -eq '"' -or $c -eq "'") {
            $end = $key.IndexOf($c, $i + 1)
            if ($end -lt 0) { return $null }
            [void]$parts.Add($key.Substring($i + 1, $end - $i - 1)); $i = $end + 1
        } else {
            $start = $i
            while ($i -lt $key.Length -and $key[$i] -ne '.') { $i++ }
            [void]$parts.Add($key.Substring($start, $i - $start).Trim())
        }
        while ($i -lt $key.Length -and ($key[$i] -eq ' ' -or $key[$i] -eq "`t")) { $i++ }
        if ($i -lt $key.Length) { if ($key[$i] -ne '.') { return $null }; $i++ }
    }
    return , @($parts)
}

# Returns the table sections of a config.toml as line ranges, or $null if the
# structure cannot be read with certainty (an unterminated multi-line string).
function Get-TomlSections([string[]]$lines) {
    $sections = New-Object System.Collections.ArrayList
    $inString = $null
    for ($n = 0; $n -lt $lines.Count; $n++) {
        $line = $lines[$n]
        if ($inString) {
            if ($line.Contains($inString)) { $inString = $null }
            continue
        }
        if ($line -match '^\s*\[\[?\s*(.+?)\s*\]\]?\s*(#.*)?\s*$' -and $line.TrimStart().StartsWith('[')) {
            $parts = Split-TomlKey $Matches[1]
            if ($null -eq $parts) { return $null }
            [void]$sections.Add([pscustomobject]@{ start = $n; end = $lines.Count; parts = $parts; header = $line.Trim() })
            continue
        }
        foreach ($quote in @('"""', "'''")) {
            if (([regex]::Matches($line, [regex]::Escape($quote))).Count % 2 -eq 1) { $inString = $quote }
        }
    }
    if ($inString) { return $null }
    for ($s = 0; $s -lt $sections.Count - 1; $s++) { $sections[$s].end = $sections[$s + 1].start }
    # Comments and blank lines just above the next header belong to that header.
    foreach ($section in $sections) {
        while ($section.end - 1 -gt $section.start -and ($lines[$section.end - 1].Trim() -eq '' -or $lines[$section.end - 1].TrimStart().StartsWith('#'))) { $section.end-- }
    }
    return , $sections
}

function Resolve-Codex {
    $path = Join-Path $CodexHome 'config.toml'
    if (-not (Test-Path -LiteralPath $path)) { Add-Result 'Codex' 'none' 'config.toml' 'No Codex configuration found.'; return }
    $file = Read-TextFile $path
    $lines = [regex]::Split($file.text, '(?<=\n)') | Where-Object { $_ -ne '' }
    $lines = @($lines)
    $sections = Get-TomlSections $lines
    if ($null -eq $sections) { Add-Result 'Codex' 'skipped' 'config.toml' 'The file could not be read with certainty; left untouched. Edit it by hand.'; return }

    # Server groups: every section under mcp_servers.<name>.
    $servers = @{}
    foreach ($section in $sections) {
        if ($section.parts.Count -ge 2 -and $section.parts[0] -eq 'mcp_servers') {
            $name = $section.parts[1]
            if (-not $servers.ContainsKey($name)) { $servers[$name] = New-Object System.Collections.ArrayList }
            [void]$servers[$name].Add($section)
        }
    }
    $navisServers = @()
    foreach ($name in $servers.Keys) {
        $body = ($servers[$name] | ForEach-Object { $lines[$_.start..($_.end - 1)] }) -join "`n"
        if (Test-NavisEntry $name $body) { $navisServers += $name }
    }
    $navisServers = @($navisServers | Sort-Object)
    $pluginSections = @($sections | Where-Object { $_.parts.Count -eq 2 -and $_.parts[0] -eq 'plugins' -and $_.parts[1] -match '^naviscoord-mcp@' })
    $pluginEnabled = @($pluginSections | Where-Object { ($lines[$_.start..($_.end - 1)] -join "`n") -match '(?m)^\s*enabled\s*=\s*true\b' })

    $removeNames = @(); $disableSections = @()
    if ($Keep -eq 'Direct') {
        if ($navisServers -notcontains $CurrentName) {
            Add-Result 'Codex' 'skipped' $CurrentName 'The direct server is not registered; nothing was changed.'; return
        }
        $removeNames = @($navisServers | Where-Object { $_ -ne $CurrentName })
        $disableSections = $pluginEnabled
    } else {
        if ($pluginEnabled.Count -eq 0) {
            if ($navisServers.Count -gt 0) { Add-Result 'Codex' 'skipped' ($navisServers -join ', ') 'The NavisCoord plugin is not enabled; the direct server was kept.' } else { Add-Result 'Codex' 'none' 'config.toml' 'Nothing to change.' }
            return
        }
        $removeNames = $navisServers
    }
    if ($removeNames.Count -eq 0 -and $disableSections.Count -eq 0) { Add-Result 'Codex' 'none' 'config.toml' 'Already a single NavisCoord registration.'; return }

    $dropLine = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($name in $removeNames) {
        foreach ($section in $servers[$name]) {
            # A removed table takes with it the comment lines right above it and the blank lines right after it.
            $first = $section.start
            while ($first -gt 0 -and $lines[$first - 1].TrimStart().StartsWith('#')) { $first-- }
            $last = $section.end
            while ($last -lt $lines.Count -and $lines[$last].Trim() -eq '') { $last++ }
            for ($n = $first; $n -lt $last; $n++) { [void]$dropLine.Add($n) }
        }
    }
    $new = New-Object System.Text.StringBuilder
    $flip = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($section in $disableSections) { for ($n = $section.start; $n -lt $section.end; $n++) { [void]$flip.Add($n) } }
    for ($n = 0; $n -lt $lines.Count; $n++) {
        if ($dropLine.Contains($n)) { continue }
        $line = $lines[$n]
        if ($flip.Contains($n)) { $line = [regex]::Replace($line, '^(\s*enabled\s*=\s*)true\b', '${1}false') }
        [void]$new.Append($line)
    }
    $newText = $new.ToString()

    # No TOML parser exists in Windows PowerShell, so prove the result
    # structurally: it must parse into sections, hold nothing that was not
    # there, and differ only by the removed sections and the flipped flags.
    $newLines = @([regex]::Split($newText, '(?<=\n)') | Where-Object { $_ -ne '' })
    $newSections = Get-TomlSections $newLines
    if ($null -eq $newSections) { Add-Result 'Codex' 'skipped' 'config.toml' 'The edit did not produce a readable file; nothing was written.'; return }
    $before = @($sections | ForEach-Object { $_.header })
    $after = @($newSections | ForEach-Object { $_.header })
    $expected = @($sections | Where-Object { -not ($_.parts.Count -ge 2 -and $_.parts[0] -eq 'mcp_servers' -and $removeNames -contains $_.parts[1]) } | ForEach-Object { $_.header })
    if (($after -join "`n") -ne ($expected -join "`n") -or @($after | Where-Object { $before -notcontains $_ }).Count -gt 0) {
        Add-Result 'Codex' 'skipped' 'config.toml' 'The edit did not match the expected result; nothing was written.'; return
    }
    if (-not $Check) { Write-TextFileSafely $path $newText $file.bom }
    foreach ($name in $removeNames) { $verb = if ($Check) { 'would-remove' } else { 'removed' }; Add-Result 'Codex' $verb $name 'Navisworks server section removed from config.toml (other servers untouched).' }
    foreach ($section in $disableSections) { $verb = if ($Check) { 'would-disable' } else { 'disabled' }; Add-Result 'Codex' $verb $section.parts[1] 'Plugin disabled (enabled = false); its files were kept.' }
}

if ($Client -in @('All', 'ClaudeCode')) { try { Resolve-Claude } catch { Add-Result 'Claude Code' 'failed' '' $_.Exception.Message } }
if ($Client -in @('All', 'Codex')) { try { Resolve-Codex } catch { Add-Result 'Codex' 'failed' '' $_.Exception.Message } }
ConvertTo-Json -InputObject @($results) -Depth 4
