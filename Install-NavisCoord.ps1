<#
.SYNOPSIS
    Installs the latest published NavisCoord add-in without build tools.

.DESCRIPTION
    Beginner-facing installer for Autodesk Navisworks Manage 2024-2026.
    It discovers installed versions, downloads the matching ZIPs from the
    official GitHub release, verifies SHA256SUMS.txt, validates the archive
    shape and publishes only NavisCoord-owned files with rollback.

    It never installs Python, changes an MCP client, opens Navisworks, or
    touches a model. Navisworks must be closed.

.EXAMPLE
    .\Install-NavisCoord.ps1
    .\Install-NavisCoord.ps1 -Version 2026
    .\Install-NavisCoord.ps1 -ReleaseTag v0.3.1
    .\Install-NavisCoord.ps1 -SelfTest
#>
[CmdletBinding(SupportsShouldProcess = $true, DefaultParameterSetName = 'Install')]
param(
    [Parameter(ParameterSetName = 'Install')]
    [ValidateSet('2024', '2025', '2026', 'all')]
    [string]$Version = 'all',

    [Parameter(ParameterSetName = 'Install')]
    [ValidatePattern('^(latest|v\d+\.\d+\.\d+)$')]
    [string]$ReleaseTag = 'latest',

    [Parameter(ParameterSetName = 'Install')]
    [string]$DestinationRoot,

    [Parameter(ParameterSetName = 'SelfTest', Mandatory = $true)]
    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Repository = 'HorizunGroup/naviscoord-mcp'
$ManagedFiles = @(
    'NavisCoord.dll',
    'NavisCoordRibbon.xaml',
    'en-US/NavisCoordRibbon.xaml',
    'nc_16.png',
    'nc_32.png',
    'LICENSE',
    'NOTICE',
    'example-profile.json'
)

function Assert-Windows {
    if ($env:OS -ne 'Windows_NT') {
        throw 'El complemento de NavisCoord solo se instala en Windows.'
    }
}

function Assert-NavisworksClosed {
    $running = @(Get-Process -Name 'roamer' -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
        $pids = ($running | ForEach-Object Id) -join ', '
        throw "Navisworks está abierto (PID $pids). Ciérralo por completo y vuelve a ejecutar el instalador."
    }
}

function Get-InstalledVersions {
    param([string]$Wanted)

    $candidates = if ($Wanted -eq 'all') { @('2024', '2025', '2026') } else { @($Wanted) }
    $found = @()
    foreach ($v in $candidates) {
        $paths = @()
        if ($env:ProgramFiles) {
            $paths += Join-Path $env:ProgramFiles "Autodesk\Navisworks Manage $v\Autodesk.Navisworks.Api.dll"
        }
        if (${env:ProgramW6432}) {
            $paths += Join-Path ${env:ProgramW6432} "Autodesk\Navisworks Manage $v\Autodesk.Navisworks.Api.dll"
        }
        if (@($paths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }).Count -gt 0) {
            $found += $v
        }
    }

    if ($found.Count -eq 0) {
        $which = if ($Wanted -eq 'all') { '2024, 2025 o 2026' } else { $Wanted }
        throw "No encontré Autodesk Navisworks Manage $which. Freedom no carga este complemento."
    }
    return $found
}

function Get-Release {
    param([string]$Tag)
    $endpoint = if ($Tag -eq 'latest') {
        "https://api.github.com/repos/$Repository/releases/latest"
    } else {
        "https://api.github.com/repos/$Repository/releases/tags/$Tag"
    }
    try {
        return Invoke-RestMethod -Uri $endpoint -Headers @{
            'User-Agent' = 'NavisCoord-release-installer'
            'Accept' = 'application/vnd.github+json'
        }
    } catch {
        throw "No pude consultar el release '$Tag' en GitHub: $($_.Exception.Message)"
    }
}

function Find-Asset {
    param($Release, [string]$Pattern, [string]$Purpose)
    $matches = @($Release.assets | Where-Object { $_.name -match $Pattern })
    if ($matches.Count -ne 1) {
        throw "El release '$($Release.tag_name)' debe tener exactamente un asset para $Purpose; encontré $($matches.Count)."
    }
    return $matches[0]
}

function Read-ExpectedHash {
    param([string]$Text, [string]$FileName)
    $pattern = '(?im)^([0-9a-f]{64})\s+\*?' + [regex]::Escape($FileName) + '\s*$'
    $match = [regex]::Match($Text, $pattern)
    if (-not $match.Success) {
        throw "SHA256SUMS.txt no contiene una entrada válida para '$FileName'."
    }
    return $match.Groups[1].Value.ToUpperInvariant()
}

function Assert-ArchiveShape {
    param([string]$ZipPath)
    Add-Type -AssemblyName System.IO.Compression -ErrorAction SilentlyContinue
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue

    $stream = [IO.File]::OpenRead($ZipPath)
    try {
        $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Read)
        try {
            $names = @($zip.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
            foreach ($name in $names) {
                if ([string]::IsNullOrWhiteSpace($name) -or $name.StartsWith('/') -or
                    $name.Contains('../') -or $name.Contains(':') -or $name.Contains('//')) {
                    throw "El ZIP contiene una ruta insegura: '$name'."
                }
            }
            $unexpected = @($names | Where-Object { $_ -notin $ManagedFiles })
            $missing = @($ManagedFiles | Where-Object { $_ -notin $names })
            $duplicates = @($names | Group-Object | Where-Object Count -ne 1)
            if ($unexpected.Count -or $missing.Count -or $duplicates.Count) {
                throw "Contenido inesperado en el ZIP. Faltan: $($missing -join ', '); sobran: $($unexpected -join ', ')."
            }
        } finally {
            $zip.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Expand-ManagedArchive {
    param([string]$ZipPath, [string]$Stage)
    [IO.Directory]::CreateDirectory($Stage) | Out-Null
    $stream = [IO.File]::OpenRead($ZipPath)
    try {
        $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Read)
        try {
            foreach ($name in $ManagedFiles) {
                $entry = $zip.GetEntry($name)
                if ($null -eq $entry) { throw "Falta '$name' después de validar el ZIP." }
                $destination = Join-Path $Stage $name
                [IO.Directory]::CreateDirectory((Split-Path $destination -Parent)) | Out-Null
                $source = $entry.Open()
                $target = [IO.File]::Open($destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
                try { $source.CopyTo($target) }
                finally { $target.Dispose(); $source.Dispose() }
            }
        } finally { $zip.Dispose() }
    } finally { $stream.Dispose() }
}

function Publish-ManagedFiles {
    param([string]$Stage, [string]$Destination)
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    $journal = @()
    try {
        foreach ($name in $ManagedFiles) {
            $source = Join-Path $Stage $name
            $target = Join-Path $Destination $name
            if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Falta el archivo preparado '$name'." }

            [IO.Directory]::CreateDirectory((Split-Path $target -Parent)) | Out-Null
            $incoming = "$target.new-$([guid]::NewGuid().ToString('N'))"
            [IO.File]::Copy($source, $incoming, $false)
            $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
            $incomingHash = (Get-FileHash -LiteralPath $incoming -Algorithm SHA256).Hash
            if ($sourceHash -ne $incomingHash) { throw "La copia preparada de '$name' no coincide en SHA-256." }

            $existed = Test-Path -LiteralPath $target -PathType Leaf
            $backup = if ($existed) { "$target.bak-$([guid]::NewGuid().ToString('N'))" } else { $null }
            $journal += [pscustomobject]@{
                Name = $name; Target = $target; Incoming = $incoming
                Existed = $existed; Backup = $backup
            }
            if ($existed) {
                [IO.File]::Replace($incoming, $target, $backup, $true)
            } else {
                [IO.File]::Move($incoming, $target)
            }
            if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $sourceHash) {
                throw "La instalación de '$name' no coincide en SHA-256."
            }
        }
    } catch {
        $reverseJournal = @($journal)
        [array]::Reverse($reverseJournal)
        foreach ($row in $reverseJournal) {
            if ($row.Existed -and $row.Backup -and (Test-Path -LiteralPath $row.Backup)) {
                if (Test-Path -LiteralPath $row.Target) { [IO.File]::Delete($row.Target) }
                [IO.File]::Move($row.Backup, $row.Target)
            } elseif (-not $row.Existed -and (Test-Path -LiteralPath $row.Target)) {
                [IO.File]::Delete($row.Target)
            }
        }
        throw
    } finally {
        foreach ($row in $journal) {
            if ($row.Backup -and (Test-Path -LiteralPath $row.Backup)) { [IO.File]::Delete($row.Backup) }
            if ($row.Incoming -and (Test-Path -LiteralPath $row.Incoming)) {
                [IO.File]::Delete($row.Incoming)
            }
        }
    }
}

function New-TestZip {
    param([string]$Path, [switch]$Unsafe)
    Add-Type -AssemblyName System.IO.Compression -ErrorAction SilentlyContinue
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew)
    try {
        $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create, $true)
        try {
            foreach ($name in $ManagedFiles) {
                $entry = $zip.CreateEntry($name)
                $writer = [IO.StreamWriter]::new($entry.Open())
                try { $writer.Write("test-$name") } finally { $writer.Dispose() }
            }
            if ($Unsafe) {
                $entry = $zip.CreateEntry('../escape.dll')
                $writer = [IO.StreamWriter]::new($entry.Open())
                try { $writer.Write('escape') } finally { $writer.Dispose() }
            }
        } finally { $zip.Dispose() }
    } finally { $stream.Dispose() }
}

function Invoke-SelfTest {
    $state = [pscustomobject]@{ Checks = 0 }
    function Check([bool]$Ok, [string]$Message) {
        $state.Checks++
        if (-not $Ok) { throw "FALLA: $Message" }
        Write-Host "ok  $Message"
    }

    $root = Join-Path ([IO.Path]::GetTempPath()) ('naviscoord-installer-test-' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($root) | Out-Null
    try {
        $hash = 'a' * 64
        Check ((Read-ExpectedHash "$hash *demo.zip`n" 'demo.zip') -eq $hash.ToUpperInvariant()) 'lee SHA256SUMS en formato GNU'

        $release = [pscustomobject]@{ tag_name = 'v9.9.9'; assets = @(
            [pscustomobject]@{ name = 'NavisCoord-9.9.9-addin-NW2026.zip' },
            [pscustomobject]@{ name = 'SHA256SUMS.txt' }
        ) }
        Check ((Find-Asset $release '^NavisCoord-.+-addin-NW2026\.zip$' 'NW2026').name -match 'NW2026') 'elige el asset de la versión exacta'

        $good = Join-Path $root 'good.zip'
        New-TestZip $good
        Assert-ArchiveShape $good
        Check $true "acepta el ZIP con exactamente $($ManagedFiles.Count) archivos permitidos"

        $bad = Join-Path $root 'bad.zip'
        New-TestZip $bad -Unsafe
        $rejected = $false
        try { Assert-ArchiveShape $bad } catch { $rejected = $true }
        Check $rejected 'rechaza traversal y contenido adicional'

        $stage = Join-Path $root 'stage'
        Expand-ManagedArchive $good $stage
        Check (@(Get-ChildItem -LiteralPath $stage -Recurse -File).Count -eq $ManagedFiles.Count) 'extrae solo los archivos administrados'

        $destination = Join-Path $root 'plugin'
        [IO.Directory]::CreateDirectory($destination) | Out-Null
        [IO.File]::WriteAllText((Join-Path $destination 'perfil-del-usuario.json'), 'no tocar')
        Publish-ManagedFiles $stage $destination
        Check ((Get-Content -LiteralPath (Join-Path $destination 'perfil-del-usuario.json') -Raw) -eq 'no tocar') 'conserva archivos ajenos y perfiles del usuario'
        Check (@($ManagedFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $destination $_)) }).Count -eq 0) 'publica el DLL, la cinta, los iconos y los metadatos del release'

        [IO.File]::WriteAllText((Join-Path $destination 'NavisCoord.dll'), 'viejo')
        Publish-ManagedFiles $stage $destination
        Check ((Get-FileHash (Join-Path $destination 'NavisCoord.dll')).Hash -eq (Get-FileHash (Join-Path $stage 'NavisCoord.dll')).Hash) 'actualiza un DLL existente y verifica el hash'

        Write-Host "Install-NavisCoord self-test: $($state.Checks) comprobaciones, 0 fallos"
    } finally {
        $full = [IO.Path]::GetFullPath($root)
        $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($full.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -and
            [IO.Path]::GetFileName($full).StartsWith('naviscoord-installer-test-', [StringComparison]::Ordinal)) {
            Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($SelfTest) {
    Invoke-SelfTest
    exit 0
}

Assert-Windows
Assert-NavisworksClosed
$versions = @(Get-InstalledVersions $Version)
$release = Get-Release $ReleaseTag
$checksumAsset = Find-Asset $release '^SHA256SUMS\.txt$' 'checksums'

$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$work = [IO.Path]::GetFullPath((Join-Path $tempBase ('naviscoord-install-' + [guid]::NewGuid().ToString('N'))))
if (-not $work.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -or
    -not [IO.Path]::GetFileName($work).StartsWith('naviscoord-install-', [StringComparison]::Ordinal)) {
    throw "Ruta temporal insegura: $work"
}
[IO.Directory]::CreateDirectory($work) | Out-Null

try {
    $sumsPath = Join-Path $work 'SHA256SUMS.txt'
    Invoke-WebRequest -Uri $checksumAsset.browser_download_url -OutFile $sumsPath -Headers @{
        'User-Agent' = 'NavisCoord-release-installer'
    }
    $sums = Get-Content -LiteralPath $sumsPath -Raw

    Write-Host "NavisCoord $($release.tag_name)"
    Write-Host "Navisworks detectado: $($versions -join ', ')"
    foreach ($v in $versions) {
        $asset = Find-Asset $release "^NavisCoord-.+-addin-NW$v\.zip$" "Navisworks $v"
        $zipPath = Join-Path $work $asset.name
        Write-Host "[$v] descargando $($asset.name) ..."
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -Headers @{
            'User-Agent' = 'NavisCoord-release-installer'
        }

        $expected = Read-ExpectedHash $sums $asset.name
        $actual = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
        if ($actual -ne $expected) { throw "[$v] SHA-256 incorrecto. Esperado $expected; recibido $actual." }
        Assert-ArchiveShape $zipPath

        $stage = Join-Path $work "stage-$v"
        Expand-ManagedArchive $zipPath $stage
        $base = if ($DestinationRoot) { $DestinationRoot } else { $env:APPDATA }
        if ([string]::IsNullOrWhiteSpace($base)) { throw 'APPDATA no está definido.' }
        $destination = if ($DestinationRoot) {
            Join-Path $base $v
        } else {
            Join-Path $base "Autodesk\Navisworks Manage $v\Plugins\NavisCoord"
        }

        if ($PSCmdlet.ShouldProcess($destination, "instalar NavisCoord $($release.tag_name) para Navisworks $v")) {
            Publish-ManagedFiles $stage $destination
            Write-Host "[$v] instalado y verificado: $destination" -ForegroundColor Green
        }
    }

    Write-Host ''
    Write-Host 'Complemento listo. Abre Navisworks y continúa con el paso 2 de docs\QUICKSTART.md.' -ForegroundColor Green
} finally {
    if (Test-Path -LiteralPath $work) {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}
