<# Registers the same standalone server in Claude Desktop, Claude Code and Codex. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Executable,
    [ValidateSet('ClaudeDesktop','ClaudeCode','Codex','All')][string]$Client = 'All',
    [string]$DesktopConfig = '',
    [switch]$ReadOnly
)
$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path -LiteralPath $Executable -ErrorAction Stop).Path
if ([IO.Path]::GetExtension($exe) -ne '.exe') { throw 'Select the packaged naviscoord-mcp.exe.' }
$name = 'horizun-navis-mcp'
$mode = if ($ReadOnly) { '1' } else { '0' }
$outcomes = @()
if ($Client -in @('All','ClaudeDesktop')) {
    if (-not $DesktopConfig) {
        $DesktopConfig = Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'
        # MSIX redirects Roaming AppData into its package's LocalCache.
        $appxCommand = Get-Command Get-AppxPackage -ErrorAction SilentlyContinue
        if ($appxCommand) {
            $packages = @(Get-AppxPackage -Name Claude -ErrorAction SilentlyContinue)
            if ($packages.Count -eq 1) {
                $packagedConfig = Join-Path $env:LOCALAPPDATA ('Packages\' + $packages[0].PackageFamilyName + '\LocalCache\Roaming\Claude\claude_desktop_config.json')
                if ((Test-Path -LiteralPath $packagedConfig) -or -not (Test-Path -LiteralPath $DesktopConfig)) {
                    $DesktopConfig = $packagedConfig
                }
            }
        }
    }
    $DesktopConfig = [IO.Path]::GetFullPath($DesktopConfig)
    $config = if (Test-Path -LiteralPath $DesktopConfig) {
        Get-Content -LiteralPath $DesktopConfig -Raw | ConvertFrom-Json
    } else { [pscustomobject]@{} }
    if (-not $config.PSObject.Properties['mcpServers']) {
        $config | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
    }
    $entry = [pscustomobject]@{ command=$exe; args=@(); env=@{ NAVISCOORD_READ_ONLY=$mode } }
    $config.mcpServers | Add-Member -NotePropertyName $name -NotePropertyValue $entry -Force
    $directory = Split-Path $DesktopConfig -Parent
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = $DesktopConfig + '.new-' + [guid]::NewGuid().ToString('N')
    $serialized = $config | ConvertTo-Json -Depth 100
    [IO.File]::WriteAllText($temporary, $serialized, (New-Object Text.UTF8Encoding($false)))
    # Parse before publication, and keep the previous config for recovery.
    Get-Content -LiteralPath $temporary -Raw | ConvertFrom-Json | Out-Null
    if (Test-Path -LiteralPath $DesktopConfig) {
        [IO.File]::Replace($temporary,$DesktopConfig,($DesktopConfig+'.backup-'+[guid]::NewGuid().ToString('N')))
    } else { [IO.File]::Move($temporary,$DesktopConfig) }
    $outcomes += [pscustomobject]@{client='Claude Desktop';status='configured';next='Restart Claude Desktop and verify navis_health'}
}
if ($Client -in @('All','ClaudeCode')) {
    $cli = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $cli) { throw 'Claude Code CLI is not installed.' }
    $userConfig = Join-Path $env:USERPROFILE '.claude.json'
    $previousConfig = $null
    if (Test-Path -LiteralPath $userConfig) {
        $settings = Get-Content -LiteralPath $userConfig -Raw | ConvertFrom-Json
        if ($settings.mcpServers -and $settings.mcpServers.PSObject.Properties[$name]) {
            $previousConfig = $userConfig + '.naviscoord-backup-' + [guid]::NewGuid().ToString('N')
            Copy-Item -LiteralPath $userConfig -Destination $previousConfig
            & $cli.Source mcp remove --scope user $name
            if ($LASTEXITCODE -ne 0) { throw 'Could not update the existing user-scoped server; configuration backup retained.' }
        }
    }
    & $cli.Source mcp add --transport stdio --scope user $name --env "NAVISCOORD_READ_ONLY=$mode" -- $exe
    if ($LASTEXITCODE -ne 0 -and $previousConfig) {
        # Only restore the removed MCP entry through the client, preserving unrelated concurrent edits.
        $entryJson = $settings.mcpServers.$name | ConvertTo-Json -Depth 100 -Compress
        & $cli.Source mcp add-json --scope user $name $entryJson
        throw 'Claude Code update failed; attempted to restore the previous entry. Backup retained.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Claude Code registration failed; existing configuration was preserved.' }
    $outcomes += [pscustomobject]@{client='Claude Code';status='configured';next='claude mcp get horizun-navis-mcp'}
}
if ($Client -in @('All','Codex')) {
    $cli = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $cli) { throw 'Codex CLI is not installed.' }
    & $cli.Source mcp add $name --env "NAVISCOORD_READ_ONLY=$mode" -- $exe
    if ($LASTEXITCODE -ne 0) { throw 'Codex registration failed.' }
    $outcomes += [pscustomobject]@{client='Codex';status='configured';next='Restart the client and verify navis_health'}
}
$outcomes | ConvertTo-Json -Depth 5
