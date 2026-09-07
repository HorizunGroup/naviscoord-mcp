<# Starts the versioned Windows runtime. No Python or administrator account required. #>
[CmdletBinding()]
param([string]$RuntimeDirectory = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$manifest = Get-Content -LiteralPath (Join-Path $root '.codex-plugin/plugin.json') -Raw | ConvertFrom-Json
$release = ([string]$manifest.version).Split('+')[0]
if ($release -notmatch '^\d+\.\d+\.\d+$') { throw 'A stable release version is required.' }
if ($RuntimeDirectory) {
    $runtime = [IO.Path]::GetFullPath($RuntimeDirectory)
} elseif (Test-Path -LiteralPath (Join-Path $root 'runtime\naviscoord-mcp.exe')) {
    $runtime = Join-Path $root 'runtime'
} else {
    $runtime = Join-Path $env:LOCALAPPDATA "NavisCoord\portable\$release\naviscoord-mcp"
    $exe = Join-Path $runtime 'naviscoord-mcp.exe'
    & (Join-Path $PSScriptRoot 'Install-Runtime.ps1') -Version $release
}
$exe = Join-Path $runtime 'naviscoord-mcp.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Runtime executable missing: $exe" }
& $exe
exit $LASTEXITCODE
