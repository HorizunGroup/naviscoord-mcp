<# Exercises download verification, interrupted-install repair and repeat installation in isolation. #>
$ErrorActionPreference = 'Stop'
$sandbox = Join-Path ([IO.Path]::GetTempPath()) ('naviscoord-portable-test-' + [guid]::NewGuid().ToString('N'))
$originalLocalAppData = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $sandbox | Out-Null
try {
    $env:LOCALAPPDATA = $sandbox
    $payload = Join-Path $sandbox 'fixture\naviscoord-mcp'
    New-Item -ItemType Directory -Path $payload -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $payload 'naviscoord-mcp.exe'), 'test executable fixture')
    $hash = (Get-FileHash -LiteralPath (Join-Path $payload 'naviscoord-mcp.exe') -Algorithm SHA256).Hash
    @{version='1.0.0';files=@{'naviscoord-mcp.exe'=$hash}} | ConvertTo-Json | Set-Content (Join-Path $payload 'runtime-files.json')
    $fixtureZip = Join-Path $sandbox 'fixture.zip'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory((Split-Path $payload -Parent), $fixtureZip)
    $archiveHash = (Get-FileHash -LiteralPath $fixtureZip -Algorithm SHA256).Hash
    function Invoke-WebRequest {
        param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
        if ($Uri.EndsWith('/SHA256SUMS.txt')) {
            [IO.File]::WriteAllText($OutFile, "$archiveHash  naviscoord-runtime-1.0.0-win-x64.zip`n")
        } else { Copy-Item -LiteralPath $fixtureZip -Destination $OutFile }
    }
    $installer = Join-Path $PSScriptRoot 'Install-Runtime.ps1'
    & $installer -Version 1.0.0
    $installedExe = Join-Path $sandbox 'NavisCoord\portable\1.0.0\naviscoord-mcp\naviscoord-mcp.exe'
    if ((Get-FileHash -LiteralPath $installedExe).Hash -ne $hash) { throw 'Fresh installation failed.' }
    [IO.File]::WriteAllText($installedExe, 'interrupted update')
    & $installer -Version 1.0.0
    if ((Get-FileHash -LiteralPath $installedExe).Hash -ne $hash) { throw 'Repair failed.' }
    function Invoke-WebRequest { throw 'A valid installation must not download again.' }
    & $installer -Version 1.0.0
    Write-Output 'PASS: checksum-verified install, corrupt-install repair, offline repeat.'
} finally {
    $env:LOCALAPPDATA = $originalLocalAppData
    $resolved = [IO.Path]::GetFullPath($sandbox)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolved.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolved).StartsWith('naviscoord-portable-test-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
