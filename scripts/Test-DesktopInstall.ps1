<# Verify complete updates and preservation when a client holds runtime files. #>
$ErrorActionPreference = 'Stop'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('naviscoord-desktop-test-' + [guid]::NewGuid().ToString('N'))
$heldFile = $null
try {
    $runtime = Join-Path $testRoot 'fixture'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $runtime 'naviscoord-mcp.exe'), 'fixture executable')
    [IO.File]::WriteAllText((Join-Path $runtime 'locked.dat'), 'fixture data')
    $catalogPath = Join-Path $testRoot '.agents/plugins/marketplace.json'
    New-Item -ItemType Directory -Path (Split-Path $catalogPath -Parent) -Force | Out-Null
    @{name='personal';plugins=@(@{name='unrelated';source=@{source='local';path='./plugins/unrelated'}})} |
        ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $catalogPath
    $installer = Join-Path $PSScriptRoot 'Install-DesktopPlugin.ps1'
    & $installer -HomeDirectory $testRoot -RuntimeDirectory $runtime | Out-Null
    & $installer -HomeDirectory $testRoot -RuntimeDirectory $runtime | Out-Null
    $installed = Join-Path $testRoot 'plugins/naviscoord-mcp'
    $before = @{}
    foreach ($file in Get-ChildItem -LiteralPath $installed -File -Recurse -Force) {
        $before[$file.FullName.Substring($installed.Length)] = (Get-FileHash -LiteralPath $file.FullName).Hash
    }
    $heldFile = [IO.File]::Open((Join-Path $installed 'runtime/locked.dat'), 'Open', 'Read', 'Read')
    [IO.File]::WriteAllText((Join-Path $runtime 'naviscoord-mcp.exe'), 'updated executable')
    $updated = & $installer -HomeDirectory $testRoot -RuntimeDirectory $runtime | ConvertFrom-Json
    foreach ($relative in $before.Keys) {
        $target = $installed + $relative
        if (-not (Test-Path -LiteralPath $target) -or (Get-FileHash -LiteralPath $target).Hash -ne $before[$relative]) {
            throw "An in-use update left an incomplete installation: $relative"
        }
    }
    $catalog = Get-Content -LiteralPath $catalogPath -Raw | ConvertFrom-Json
    $registered = @($catalog.plugins | Where-Object name -eq 'naviscoord-mcp')[0]
    $active = Join-Path $testRoot $registered.source.path
    if ((Get-Content -LiteralPath (Join-Path $active 'runtime/naviscoord-mcp.exe') -Raw) -ne 'updated executable' -or
        $updated.directory -ne [IO.Path]::GetFullPath($active)) { throw 'Catalog does not point at the completed update.' }
    if (@($catalog.plugins | Where-Object name -eq 'unrelated').Count -ne 1 -or
        @($catalog.plugins | Where-Object name -eq 'naviscoord-mcp').Count -ne 1) {
        throw 'The update changed unrelated registrations or duplicated NavisCoord.'
    }
    Write-Output 'PASS: fresh install, repeat update, side-by-side in-use update, unrelated catalog entry preserved.'
} finally {
    if ($heldFile) { $heldFile.Dispose() }
    $resolved = [IO.Path]::GetFullPath($testRoot)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolved.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolved).StartsWith('naviscoord-desktop-test-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
