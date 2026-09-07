<# Downloads the pinned public runtime and verifies its release SHA-256 before extraction. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$cache = Join-Path $env:LOCALAPPDATA 'NavisCoord\portable'
$destination = Join-Path $cache $Version
$mutex = New-Object Threading.Mutex($false, ('Local\NavisCoord-Portable-' + $Version))
$held = $false
$stage = $null
function Assert-InCache([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath($cache) + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe runtime path.' }
    return $resolved
}
function Test-Runtime([string]$Directory) {
    $runtimeRoot = Join-Path $Directory 'naviscoord-mcp'
    $index = Join-Path $runtimeRoot 'runtime-files.json'
    if (-not (Test-Path -LiteralPath $index)) { return $false }
    try {
        $files = Get-Content -LiteralPath $index -Raw | ConvertFrom-Json
        if ($files.version -ne $Version -or -not $files.files.PSObject.Properties['naviscoord-mcp.exe']) { return $false }
        foreach ($property in $files.files.PSObject.Properties) {
            $file = [IO.Path]::GetFullPath((Join-Path $runtimeRoot $property.Name))
            if (-not $file.StartsWith($runtimeRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { return $false }
            if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $false }
            if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $property.Value) { return $false }
        }
        return $true
    } catch { return $false }
}
try {
    try { $held = $mutex.WaitOne(120000) } catch [Threading.AbandonedMutexException] { $held = $true }
    if (-not $held) { throw 'Another runtime installation is still running. Retry after it finishes.' }
    if (Test-Runtime $destination) { return }
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
    $stage = Join-Path $cache ('.staging-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    $asset = "naviscoord-runtime-$Version-win-x64.zip"
    $base = "https://github.com/HorizunGroup/naviscoord-mcp/releases/download/v$Version"
    $archive = Join-Path $stage $asset
    $checksums = Join-Path $stage 'SHA256SUMS'
    [Console]::Error.WriteLine("Installing NavisCoord $Version from its public release...")
    Invoke-WebRequest -UseBasicParsing -Uri "$base/SHA256SUMS.txt" -OutFile $checksums
    $lines = @(Get-Content -LiteralPath $checksums | Where-Object { $_ -match ('^[a-fA-F0-9]{64}\s+\*?' + [regex]::Escape($asset) + '$') })
    if ($lines.Count -ne 1) { throw 'Release checksum is missing or ambiguous.' }
    $expected = $lines[0].Substring(0,64)
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$asset" -OutFile $archive
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) { throw 'Runtime checksum mismatch.' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName.Contains(':')) { throw 'Unsafe archive stream path.' }
            $resolved = [IO.Path]::GetFullPath((Join-Path $stage $entry.FullName))
            if (-not $resolved.StartsWith($stage + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe archive path.' }
        }
    } finally { $zip.Dispose() }
    $unpacked = Join-Path $stage 'payload'
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $unpacked)
    if (-not (Test-Runtime $unpacked)) { throw 'Release runtime is incomplete or corrupt.' }
    # The directory becomes visible only when every file has been verified and extracted.
    $destination = Assert-InCache $destination
    $unpacked = Assert-InCache $unpacked
    $backup = Assert-InCache ($destination + '.repair-' + [guid]::NewGuid().ToString('N'))
    if (Test-Path -LiteralPath $destination) { Move-Item -LiteralPath $destination -Destination $backup }
    try { Move-Item -LiteralPath $unpacked -Destination $destination }
    catch {
        if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $destination }
        throw
    }
} finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        $bounded = [IO.Path]::GetFullPath($stage)
        if ($bounded.StartsWith([IO.Path]::GetFullPath($cache) + '\', [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $bounded -Recurse -Force
        }
    }
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
