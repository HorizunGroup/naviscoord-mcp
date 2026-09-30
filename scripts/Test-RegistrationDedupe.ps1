<# Verifies that registration leaves one NavisCoord instance per client, in a temporary home.
   It never reads or writes the real client configuration of the machine it runs on. #>
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('naviscoord-dedupe-test-' + [guid]::NewGuid().ToString('N'))
$resolver = Join-Path $PSScriptRoot 'Resolve-DuplicateRegistrations.ps1'
$utf8 = New-Object Text.UTF8Encoding($false)
$savedProfile = $env:USERPROFILE; $savedHome = $env:HOME; $savedCodex = $env:CODEX_HOME
function Check([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Write-File([string]$path, [string]$text) {
    New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
    [IO.File]::WriteAllText($path, $text, $utf8)
}
function New-Home([string]$name) { $h = Join-Path $root $name; New-Item -ItemType Directory -Path $h -Force | Out-Null; return $h }
function Run([string[]]$arguments) {
    $splat = @{}
    for ($i = 0; $i -lt $arguments.Count; $i++) {
        $key = $arguments[$i].TrimStart('-')
        if ($key -eq 'Check') { $splat[$key] = $true } else { $splat[$key] = $arguments[++$i] }
    }
    return @(& $resolver @splat | ConvertFrom-Json)
}
function Actions($results) { return (@($results | ForEach-Object { $_.client + ':' + $_.action + ':' + $_.item }) -join '|') }
try {
    Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue

    # ---- Claude Code, keep the direct server: the old plugin is disabled, nothing else changes.
    $h = New-Home 'claude-direct'
    $settings = "{`r`n  `"theme`": `"dark`",`r`n  `"enabledPlugins`": {`r`n    `"naviscoord-mcp@horizun-navis`": true,`r`n    `"other-plugin@market`": true`r`n  }`r`n}`r`n"
    Write-File "$h\.claude\settings.json" $settings
    Write-File "$h\.claude\plugins\cache\horizun-navis\naviscoord-mcp\0.3.1\keep.txt" 'cache'
    Write-File "$h\.claude.json" '{"mcpServers":{"horizun-navis-mcp":{"command":"C:\\x\\naviscoord-mcp.exe","args":[]},"horizun-revit":{"command":"C:\\x\\revit.exe","args":[]}}}'
    $r = Run @('-Keep','Direct','-Client','ClaudeCode','-HomeDirectory',$h,'-Check')
    Check ((Actions $r) -eq 'Claude Code:would-disable:naviscoord-mcp@horizun-navis') "check mode: $(Actions $r)"
    Check ([IO.File]::ReadAllText("$h\.claude\settings.json") -eq $settings) 'check mode wrote a file'
    $r = Run @('-Keep','Direct','-Client','ClaudeCode','-HomeDirectory',$h)
    Check ((Actions $r) -eq 'Claude Code:disabled:naviscoord-mcp@horizun-navis') "direct: $(Actions $r)"
    Check ([IO.File]::ReadAllText("$h\.claude\settings.json") -eq $settings.Replace('@horizun-navis": true','@horizun-navis": false')) 'settings changed beyond the flag'
    Check (@(Get-ChildItem "$h\.claude" -Filter 'settings.json.backup-*').Count -eq 1) 'settings backup missing'
    Check (Test-Path "$h\.claude\plugins\cache\horizun-navis\naviscoord-mcp\0.3.1\keep.txt") 'plugin cache was touched'
    $r = Run @('-Keep','Direct','-Client','ClaudeCode','-HomeDirectory',$h)
    Check ((Actions $r) -eq 'Claude Code:none:naviscoord-mcp@horizun-navis') "not idempotent: $(Actions $r)"
    Check (@(Get-ChildItem "$h\.claude" -Filter 'settings.json.backup-*').Count -eq 1) 'idempotent run made another backup'

    # ---- Claude Code, keep the direct server, but it is not registered: nothing is disabled.
    $h = New-Home 'claude-nodirect'
    Write-File "$h\.claude\settings.json" $settings
    Write-File "$h\.claude.json" '{"mcpServers":{}}'
    $r = Run @('-Keep','Direct','-Client','ClaudeCode','-HomeDirectory',$h)
    Check ((Actions $r) -eq 'Claude Code:skipped:horizun-navis-mcp') "no direct: $(Actions $r)"
    Check ([IO.File]::ReadAllText("$h\.claude\settings.json") -eq $settings) 'plugin disabled with no direct server'

    # ---- Claude Code, keep the plugin: the direct entry goes through the CLI; other servers stay.
    $stub = Join-Path $root 'fake-claude.cmd'
    Write-File "$root\fake-claude.ps1" @'
param([Parameter(ValueFromRemainingArguments=$true)]$a)
Add-Content -LiteralPath (Join-Path $env:USERPROFILE 'cli.log') -Value ($a -join ' ')
$path = Join-Path $env:USERPROFILE '.claude.json'
$json = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
$json.mcpServers.PSObject.Properties.Remove($a[2])
$json | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $path
'@
    Write-File $stub "@powershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0fake-claude.ps1`" %*`r`n"
    $h = New-Home 'claude-plugin'
    Write-File "$h\.claude\settings.json" $settings.Replace('@horizun-navis": true','@horizun-navis": false')
    Write-File "$h\.claude.json" '{"mcpServers":{"horizun-navis-mcp":{"command":"C:\\x\\naviscoord-mcp.exe","args":[]},"naviscoord":{"command":"python","args":["-m","naviscoord"]},"horizun-revit":{"command":"C:\\x\\revit.exe","args":[]}}}'
    $r = Run @('-Keep','Plugin','-Client','ClaudeCode','-HomeDirectory',$h,'-ClaudeCli',$stub)
    Check ((Actions $r) -like 'Claude Code:skipped:*') "disabled plugin must keep the direct server: $(Actions $r)"
    Write-File "$h\.claude\settings.json" $settings
    $r = Run @('-Keep','Plugin','-Client','ClaudeCode','-HomeDirectory',$h,'-ClaudeCli',$stub)
    Check (@($r | Where-Object action -eq 'removed').Count -eq 2) "plugin: $(Actions $r)"
    $left = (Get-Content "$h\.claude.json" -Raw | ConvertFrom-Json).mcpServers
    Check ((@($left.PSObject.Properties.Name) -join ',') -eq 'horizun-revit') 'another product was touched'
    Check ((Get-Content "$h\cli.log") -contains 'mcp remove horizun-navis-mcp --scope user') 'claude mcp remove was not used'
    $r = Run @('-Keep','Plugin','-Client','ClaudeCode','-HomeDirectory',$h,'-ClaudeCli',$stub)
    Check ((Actions $r) -eq 'Claude Code:none:direct server') "plugin not idempotent: $(Actions $r)"

    # ---- Codex: sections with another name and the plugin flag; unrelated servers stay byte for byte.
    $toml = @'
# user settings
model = "x"

[plugins."naviscoord-mcp@personal"]
enabled = true

[plugins."other@market"]
enabled = true

[mcp_servers.node_repl]
command = "node"
notes = """
[mcp_servers.horizun-navis-mcp]
this line is text, not a table
"""

[mcp_servers.node_repl.env]
A = "1"

# legacy launcher
[mcp_servers.naviscoord]
command = "python"
args = ["-m", "naviscoord"]

[mcp_servers.naviscoord.env]
NAVISCOORD_READ_ONLY = "0"

# current
[mcp_servers.horizun-navis-mcp]
command = "C:\\x\\naviscoord-mcp.exe"
args = []

[mcp_servers.horizun-revit]
command = "C:\\x\\revit.exe"

[projects.'C:\some path']
trust_level = "trusted"
'@.Replace("`r`n", "`n").Replace("`n", "`r`n")
    $h = New-Home 'codex'
    Write-File "$h\.codex\config.toml" $toml
    $r = Run @('-Keep','Direct','-Client','Codex','-HomeDirectory',$h,'-Check')
    Check ([IO.File]::ReadAllText("$h\.codex\config.toml") -eq $toml) 'check mode wrote the file'
    $r = Run @('-Keep','Direct','-Client','Codex','-HomeDirectory',$h)
    Check ((Actions $r) -eq 'Codex:removed:naviscoord|Codex:disabled:naviscoord-mcp@personal') "codex direct: $(Actions $r)"
    $written = [IO.File]::ReadAllText("$h\.codex\config.toml")
    $legacy = "# legacy launcher`r`n[mcp_servers.naviscoord]`r`ncommand = `"python`"`r`nargs = [`"-m`", `"naviscoord`"]`r`n`r`n[mcp_servers.naviscoord.env]`r`nNAVISCOORD_READ_ONLY = `"0`"`r`n`r`n"
    Check ($toml.Contains($legacy)) 'test fixture is inconsistent'
    $expected = $toml.Replace($legacy, '').Replace('@personal"]' + "`r`nenabled = true", '@personal"]' + "`r`nenabled = false")
    Check ($written -eq $expected) 'codex file differs from the expected edit'
    Check ($written.Contains('this line is text, not a table')) 'multi-line string was damaged'
    Check (@(Get-ChildItem "$h\.codex" -Filter 'config.toml.backup-*').Count -eq 1) 'codex backup missing'
    $python = Get-Command python -ErrorAction SilentlyContinue
    $parsed = $false
    if ($python) {
        & $python.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 7)"
        if ($LASTEXITCODE -eq 0) {
            $check = "import tomllib,sys; d=tomllib.load(open(sys.argv[1],'rb')); s=d['mcp_servers']; assert sorted(s)==['horizun-navis-mcp','horizun-revit','node_repl'], sorted(s); assert d['plugins']['naviscoord-mcp@personal']['enabled'] is False; assert d['plugins']['other@market']['enabled'] is True"
            & $python.Source -c $check "$h\.codex\config.toml"
            Check ($LASTEXITCODE -eq 0) 'result is not valid TOML with the expected content'
            $parsed = $true
        }
    }
    if (-not $parsed) { Write-Output 'NOTE: python >= 3.11 not found; the independent TOML parse check was skipped.' }
    $r = Run @('-Keep','Direct','-Client','Codex','-HomeDirectory',$h)
    Check ((Actions $r) -eq 'Codex:none:config.toml') "codex not idempotent: $(Actions $r)"

    # Keep the plugin: every Navisworks server section goes, only while the plugin is enabled.
    $h = New-Home 'codex-plugin'
    Write-File "$h\.codex\config.toml" $toml
    $r = Run @('-Keep','Plugin','-Client','Codex','-HomeDirectory',$h)
    Check ((Actions $r) -eq 'Codex:removed:horizun-navis-mcp|Codex:removed:naviscoord') "codex plugin: $(Actions $r)"
    $written = [IO.File]::ReadAllText("$h\.codex\config.toml")
    Check ((-not $written.Contains('[mcp_servers.naviscoord')) -and $written.Contains('[mcp_servers.horizun-revit]') -and $written.Contains('enabled = true') -and $written.Contains('[mcp_servers.node_repl.env]')) 'codex plugin result is wrong'
    Check ($written.Contains('[mcp_servers.horizun-navis-mcp]') -eq $true) 'text inside a multi-line string must survive (only the real table goes)'
    $h = New-Home 'codex-plugin-off'
    Write-File "$h\.codex\config.toml" $toml.Replace('@personal"]' + "`r`nenabled = true", '@personal"]' + "`r`nenabled = false")
    $r = Run @('-Keep','Plugin','-Client','Codex','-HomeDirectory',$h)
    Check ((Actions $r) -like 'Codex:skipped:*') "disabled plugin must keep the direct server: $(Actions $r)"

    # The Codex plugin installer drops a direct entry when the plugin is already enabled.
    $h = New-Home 'codex-installer'
    Write-File "$h\.codex\config.toml" $toml
    $fixtureRuntime = Join-Path $h 'fixture-runtime'
    Write-File (Join-Path $fixtureRuntime 'naviscoord-mcp.exe') 'fixture executable'
    $installed = & (Join-Path $PSScriptRoot 'Install-DesktopPlugin.ps1') -HomeDirectory $h -RuntimeDirectory $fixtureRuntime | ConvertFrom-Json
    Check (@($installed.duplicates | Where-Object action -eq 'removed').Count -eq 2) 'plugin installer did not remove the direct entries'
    Check (-not [IO.File]::ReadAllText("$h\.codex\config.toml").Contains('[mcp_servers.naviscoord')) 'plugin installer left a direct entry'

    # An unreadable file is never written.
    $h = New-Home 'codex-bad'
    $bad = "[mcp_servers.horizun-navis-mcp]`r`ncommand = `"a`"`r`ntext = `"`"`"`r`nunterminated`r`n"
    Write-File "$h\.codex\config.toml" $bad
    $r = Run @('-Keep','Direct','-Client','Codex','-HomeDirectory',$h)
    Check (((Actions $r) -like 'Codex:skipped:*') -and ([IO.File]::ReadAllText("$h\.codex\config.toml") -eq $bad)) 'unreadable TOML was modified'

    # ---- Claude Desktop through Configure-Clients: an older-named Navisworks entry is replaced, others stay.
    $h = New-Home 'desktop'
    $exe = Join-Path $h 'naviscoord-mcp.exe'
    [IO.File]::WriteAllText($exe, 'fixture')
    $desktop = Join-Path $h 'claude_desktop_config.json'
    Write-File $desktop '{"mcpServers":{"naviscoord":{"command":"python","args":["-m","naviscoord"]},"horizun-revit":{"command":"C:\\x\\revit.exe"}},"other":1}'
    & (Join-Path $PSScriptRoot 'Configure-Clients.ps1') -Executable $exe -Client ClaudeDesktop -DesktopConfig $desktop | Out-Null
    $after = Get-Content $desktop -Raw | ConvertFrom-Json
    Check (((@($after.mcpServers.PSObject.Properties.Name) | Sort-Object) -join ',') -eq 'horizun-navis-mcp,horizun-revit') 'desktop: expected exactly the current entry and the unrelated one'
    Check ($after.other -eq 1) 'desktop: unrelated setting lost'

    # ---- Claude Code through Configure-Clients with the real CLI, when one is installed.
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if ($claude) {
        $h = New-Home 'real-claude'
        $env:USERPROFILE = $h; $env:HOME = $h
        Write-File "$h\.claude\settings.json" $settings
        $raw = (& (Join-Path $PSScriptRoot 'Configure-Clients.ps1') -Executable $exe -Client ClaudeCode | Out-String)
        $out = @($raw.Substring([regex]::Match($raw, '(?m)^[\[{]').Index) | ConvertFrom-Json)   # the CLI prints its own lines first
        $env:USERPROFILE = $savedProfile; $env:HOME = $savedHome
        Check (@($out[0].duplicates | Where-Object action -eq 'disabled').Count -eq 1) 'real CLI: plugin not disabled'
        Check ((Get-Content "$h\.claude.json" -Raw) -match 'horizun-navis-mcp') 'real CLI: direct entry missing'
        Check ((Get-Content "$h\.claude\settings.json" -Raw) -match '"naviscoord-mcp@horizun-navis": false') 'real CLI: settings not updated'
    } else { Write-Output 'NOTE: claude CLI not found; the real-CLI registration check was skipped.' }

    Write-Output 'PASS: one NavisCoord registration per client (Claude Code, Codex, Claude Desktop), idempotent, backed up, nothing else touched.'
} finally {
    $env:USERPROFILE = $savedProfile; $env:HOME = $savedHome
    if ($savedCodex) { $env:CODEX_HOME = $savedCodex }
    $resolved = [IO.Path]::GetFullPath($root)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolved.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolved).StartsWith('naviscoord-dedupe-test-') -and (Test-Path -LiteralPath $resolved)) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
