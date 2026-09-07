<# Installs NavisCoord in Plugins > Personal for ChatGPT Desktop and Codex. #>
[CmdletBinding()]
param([string]$HomeDirectory = $env:USERPROFILE, [string]$RuntimeDirectory = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$homeRoot = [IO.Path]::GetFullPath($HomeDirectory)
$pluginRoot = Join-Path $homeRoot 'plugins'
$destination = Join-Path $pluginRoot 'naviscoord-mcp'
$marketplace = Join-Path $homeRoot '.agents\plugins\marketplace.json'
if (-not $RuntimeDirectory) { $RuntimeDirectory = Join-Path $root 'runtime' }
$runtime = (Resolve-Path -LiteralPath $RuntimeDirectory).Path
if (-not (Test-Path -LiteralPath (Join-Path $runtime 'naviscoord-mcp.exe'))) { throw 'The standalone runtime is missing.' }
$manifest = Get-Content -LiteralPath (Join-Path $root '.codex-plugin\plugin.json') -Raw | ConvertFrom-Json
if ($manifest.name -ne 'naviscoord-mcp') { throw 'Unexpected plugin identity.' }
# Validate any existing personal catalog before changing files.
$catalog = if (Test-Path -LiteralPath $marketplace) {
    Get-Content -LiteralPath $marketplace -Raw | ConvertFrom-Json
} else { [pscustomobject]@{ name='personal'; interface=@{displayName='Personal'}; plugins=@() } }
if (-not $catalog.name -or -not $catalog.PSObject.Properties['plugins']) { throw 'Existing personal marketplace is invalid; it was preserved.' }
New-Item -ItemType Directory -Path $pluginRoot -Force | Out-Null
$stage = Join-Path $pluginRoot ('.naviscoord-staging-' + [guid]::NewGuid().ToString('N'))
$backup = $destination + '.backup-' + [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    foreach ($relative in @('.codex-plugin','skills','scripts','assets','LICENSE','NOTICE')) {
        Copy-Item -LiteralPath (Join-Path $root $relative) -Destination (Join-Path $stage $relative) -Recurse
    }
    Copy-Item -LiteralPath $runtime -Destination (Join-Path $stage 'runtime') -Recurse
    if (Test-Path -LiteralPath $destination) {
        if (-not ([IO.Path]::GetFullPath($destination)).StartsWith($pluginRoot + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe plugin path.' }
        # Directory.Move is a single rename. Move-Item can move individual files
        # before failing on a running client's DLL, leaving a partial install.
        try { [IO.Directory]::Move($destination, $backup) }
        catch {
            # Running desktop clients can keep the whole directory locked.
            # Install beside it and point the catalog at the complete new copy.
            # Existing processes keep their original files until they exit.
            if (-not (Test-Path -LiteralPath $destination) -or (Test-Path -LiteralPath $backup)) { throw }
            $destination = Join-Path $pluginRoot ('naviscoord-mcp-' + [guid]::NewGuid().ToString('N'))
        }
    }
    try { [IO.Directory]::Move($stage, $destination) }
    catch {
        if (Test-Path -LiteralPath $backup) { [IO.Directory]::Move($backup, $destination) }
        throw
    }
    $entry = [pscustomobject]@{
        name='naviscoord-mcp'; source=@{source='local';path=('./plugins/' + [IO.Path]::GetFileName($destination))}
        policy=@{installation='AVAILABLE';authentication='ON_INSTALL'};category='Productivity'
    }
    $catalog.plugins = @($catalog.plugins | Where-Object name -ne 'naviscoord-mcp') + @($entry)
    New-Item -ItemType Directory -Path (Split-Path $marketplace -Parent) -Force | Out-Null
    $temp = $marketplace + '.new-' + [guid]::NewGuid().ToString('N')
    [IO.File]::WriteAllText($temp,($catalog | ConvertTo-Json -Depth 100),(New-Object Text.UTF8Encoding($false)))
    if (Test-Path -LiteralPath $marketplace) {
        [IO.File]::Replace($temp,$marketplace,($marketplace+'.backup-'+[guid]::NewGuid().ToString('N')))
    } else { [IO.File]::Move($temp,$marketplace) }
    [pscustomobject]@{status='configured';plugin=$manifest.name;version=$manifest.version;marketplace=$catalog.name;directory=$destination;runtime=(Join-Path $destination 'runtime');next='Restart ChatGPT Desktop. Open Plugins > Personal > NavisCoord > Install.'} | ConvertTo-Json
} finally {
    if (Test-Path -LiteralPath $stage) {
        if (([IO.Path]::GetFullPath($stage)).StartsWith($pluginRoot + '\',[StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $stage -Recurse -Force
        }
    }
}
