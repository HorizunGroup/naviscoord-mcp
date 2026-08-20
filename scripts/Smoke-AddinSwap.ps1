<#
.SYNOPSIS
    Backup, instalacion temporal y restauracion verificada del add-in, para
    el smoke test.

.DESCRIPTION
    Cambiar el add-in que el usuario tiene instalado es la accion menos
    reversible de toda esta validacion, asi que se hace en cuatro modos
    separados y cada uno se puede comprobar solo:

      Backup   copia el arbol de Plugins a %TEMP% con manifiesto SHA-256
      Verify   confirma que el backup coincide con lo que hay instalado
      Install  sustituye UNICAMENTE el DLL de NavisCoord
      Restore  devuelve los archivos del backup y vuelve a comparar hashes

    Solo se toca ese DLL. El perfil del usuario y cualquier OTRO plugin de
    terceros se quedan donde estan: un smoke test no tiene por que reescribir
    configuracion que no produjo.

    El binario sale de dist\addin\<version>\, que es lo que deja
    scripts\Build-Release.ps1.

.EXAMPLE
    .\Smoke-AddinSwap.ps1 -Mode Backup  -Versions 2026
    .\Smoke-AddinSwap.ps1 -Mode Verify  -BackupRoot <ruta>
    .\Smoke-AddinSwap.ps1 -Mode Install -Versions 2026
    .\Smoke-AddinSwap.ps1 -Mode Restore -BackupRoot <ruta>
#>
[CmdletBinding(DefaultParameterSetName = 'Run')]
param(
    [Parameter(ParameterSetName = 'Run', Mandatory = $true)]
    [ValidateSet('Backup', 'Verify', 'Install', 'Restore')]
    [string]$Mode,
    [Parameter(ParameterSetName = 'SelfTest', Mandatory = $true)]
    [switch]$SelfTest,
    [string[]]$Versions = @('2026'),
    [string]$BackupRoot
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
# Only this is ever replaced. Everything else in Plugins is left alone.
$managed = @(
    @{ Folder = 'NavisCoord'; File = 'NavisCoord.dll' }
)

function Get-PluginsDir([string]$v) {
    Join-Path $env:APPDATA "Autodesk\Navisworks Manage $v\Plugins"
}

function Assert-NoNavisworks {
    $running = Get-Process | Where-Object { $_.ProcessName -match '^Roamer$|Navisworks' }
    if ($running) {
        $ids = ($running | ForEach-Object { "$($_.ProcessName) PID $($_.Id)" }) -join ', '
        throw "Navisworks esta abierto ($ids). No se sustituye nada con el proceso vivo: el DLL queda bloqueado y la copia falla a medias."
    }
}

function Resolve-ContainedPath([string]$Root, [string]$Relative) {
    if ([string]::IsNullOrWhiteSpace($Relative) -or
        [IO.Path]::IsPathRooted($Relative) -or
        $Relative.StartsWith('\\') -or
        $Relative.StartsWith('/') -or
        $Relative.Contains(':')) {
        throw "ruta relativa hostil en manifiesto: '$Relative'"
    }
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    $full = [IO.Path]::GetFullPath((Join-Path $rootFull $Relative))
    if (-not $full.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "la ruta '$Relative' intenta salir de '$Root'"
    }
    return $full
}

function Resolve-OwnedBackup([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'falta -BackupRoot' }
    $full = [IO.Path]::GetFullPath($Path)
    $temp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    if (-not ($full + [IO.Path]::DirectorySeparatorChar).StartsWith($temp, [StringComparison]::OrdinalIgnoreCase)) {
        throw "BackupRoot debe estar dentro de TEMP; se rechazo '$full'"
    }
    $marker = Join-Path $full '.naviscoord-smoke-backup.json'
    if (-not (Test-Path -LiteralPath $marker)) {
        throw "'$full' no lleva el marcador de un backup NavisCoord"
    }
    return $full
}

function Publish-FileAtomic([string]$Source, [string]$Target) {
    $parent = Split-Path $Target -Parent
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temp = Join-Path $parent ('.' + [IO.Path]::GetFileName($Target) + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
    $backup = Join-Path $parent ('.' + [IO.Path]::GetFileName($Target) + '.' + [guid]::NewGuid().ToString('N') + '.bak')
    try {
        Copy-Item -LiteralPath $Source -Destination $temp
        $expected = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
        if ((Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash -ne $expected) {
            throw 'hash distinto durante staging'
        }
        if (Test-Path -LiteralPath $Target) { [IO.File]::Replace($temp, $Target, $backup, $true) }
        else { [IO.File]::Move($temp, $Target) }
        if ((Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash -ne $expected) {
            throw 'hash distinto tras publicar'
        }
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
    } catch {
        if (Test-Path -LiteralPath $backup) {
            if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Force }
            [IO.File]::Move($backup, $Target)
        }
        throw
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
    }
}

function Read-Manifest([string]$Root) {
    $path = Join-Path $Root 'manifest.csv'
    if (-not (Test-Path -LiteralPath $path)) { throw "falta $path" }
    $rows = @(Import-Csv -LiteralPath $path)
    if ($rows.Count -eq 0) { throw "el backup en $Root esta VACIO" }
    foreach ($row in $rows) {
        if ($row.Version -notin @('2024','2025','2026')) { throw "version hostil '$($row.Version)'" }
        [void](Resolve-ContainedPath (Join-Path $Root $row.Version) $row.Relative)
        [void](Resolve-ContainedPath (Get-PluginsDir $row.Version) $row.Relative)
        if ($row.Existed -notin @('True','False')) { throw "estado Existed invalido para $($row.Relative)" }
        if ($row.Existed -eq 'False') {
            $managedRelative = Join-Path $managed[0].Folder $managed[0].File
            $declared = $row.Relative -replace '[\\/]+', '/'
            $expected = $managedRelative -replace '[\\/]+', '/'
            if ($declared -ne $expected) {
                throw "solo se permite declarar ausencia del archivo administrado; no '$($row.Relative)'"
            }
        }
    }
    return $rows
}

function Invoke-VerifyBackup([string]$Root) {
    $Root = Resolve-OwnedBackup $Root
    $manifest = @(Read-Manifest $Root)
    $ok = 0; $bad = 0; $missing = 0
    foreach ($row in $manifest) {
        $copy = Resolve-ContainedPath (Join-Path $Root $row.Version) $row.Relative
        if ($row.Existed -eq 'False') {
            if (Test-Path -LiteralPath $copy) { $bad++ } else { $ok++ }
            continue
        }
        if (-not (Test-Path -LiteralPath $copy)) { $missing++; continue }
        if ((Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash -eq $row.Sha256) { $ok++ }
        else { $bad++ }
    }
    if ($bad -or $missing) { throw "backup no restaurable: $bad distintos, $missing ausentes" }
    return [pscustomobject]@{ Correct = $ok; Bad = $bad; Missing = $missing }
}

function Invoke-RestoreBackup([string]$Root) {
    $Root = Resolve-OwnedBackup $Root
    $manifest = @(Read-Manifest $Root)
    foreach ($row in $manifest) {
        if ($row.Existed -eq 'False') { continue }
        $copy = Resolve-ContainedPath (Join-Path $Root $row.Version) $row.Relative
        if (-not (Test-Path -LiteralPath $copy)) { throw "falta en backup: $($row.Relative)" }
        if ((Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash -ne $row.Sha256) {
            throw "hash corrupto en backup: $($row.Relative)"
        }
    }
    foreach ($row in $manifest) {
        $target = Resolve-ContainedPath (Get-PluginsDir $row.Version) $row.Relative
        if ($row.Existed -eq 'False') {
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
        } else {
            $copy = Resolve-ContainedPath (Join-Path $Root $row.Version) $row.Relative
            Publish-FileAtomic $copy $target
        }
    }
    $mismatched = 0
    foreach ($row in $manifest) {
        $target = Resolve-ContainedPath (Get-PluginsDir $row.Version) $row.Relative
        if ($row.Existed -eq 'False') {
            if (Test-Path -LiteralPath $target) { $mismatched++ }
        } elseif (-not (Test-Path -LiteralPath $target) -or
                  (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $row.Sha256) {
            $mismatched++
        }
    }
    if ($mismatched) { throw "la restauracion dejo $mismatched discrepancias" }
    return $manifest.Count
}

if ($SelfTest) {
    $testRoot = Join-Path ([IO.Path]::GetTempPath()) ('naviscoord-swap-selftest-' + [guid]::NewGuid().ToString('N'))
    $oldAppData = $env:APPDATA
    $oldTemp = $env:TEMP
    $checks = 0
    try {
        $env:APPDATA = Join-Path $testRoot 'appdata'
        $env:TEMP = [IO.Path]::GetTempPath()
        $backup = Join-Path $testRoot 'backup'
        $versionBackup = Join-Path $backup '2026'
        New-Item -ItemType Directory -Path $versionBackup -Force | Out-Null
        @{ schema = 'naviscoord.smoke-backup/1' } | ConvertTo-Json |
            Set-Content -LiteralPath (Join-Path $backup '.naviscoord-smoke-backup.json') -Encoding UTF8
        @([pscustomobject]@{
            Version = '2026'; Relative = 'NavisCoord\NavisCoord.dll';
            Existed = $false; Bytes = 0; Sha256 = ''
        }) | Export-Csv -LiteralPath (Join-Path $backup 'manifest.csv') -NoTypeInformation -Encoding UTF8

        $target = Join-Path (Get-PluginsDir '2026') 'NavisCoord\NavisCoord.dll'
        $thirdParty = Join-Path (Get-PluginsDir '2026') 'OtroPlugin\tercero.dll'
        New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
        New-Item -ItemType Directory -Path (Split-Path $thirdParty -Parent) -Force | Out-Null
        [IO.File]::WriteAllText($target, 'dll temporal')
        [IO.File]::WriteAllText($thirdParty, 'tercero intacto')
        [void](Invoke-VerifyBackup $backup); $checks++
        [void](Invoke-RestoreBackup $backup)
        if (Test-Path -LiteralPath $target) { throw 'Restore no retiro un DLL originalmente ausente' }
        $checks++
        if ([IO.File]::ReadAllText($thirdParty) -ne 'tercero intacto') { throw 'se altero un plugin de terceros' }
        $checks++

        foreach ($hostile in @('..\..\victima.dll', 'C:\Windows\x.dll', '\\servidor\share\x.dll', 'NavisCoord\x.dll:oculto')) {
            @([pscustomobject]@{
                Version = '2026'; Relative = $hostile;
                Existed = $true; Bytes = 1; Sha256 = '00'
            }) | Export-Csv -LiteralPath (Join-Path $backup 'manifest.csv') -NoTypeInformation -Encoding UTF8
            $rejected = $false
            try { [void](Read-Manifest $backup) } catch { $rejected = $true }
            if (-not $rejected) { throw "no rechazo ruta hostil: $hostile" }
            $checks++
        }

        Set-Content -LiteralPath (Join-Path $backup 'manifest.csv') -Value ''
        $rejected = $false
        try { [void](Read-Manifest $backup) } catch { $rejected = $true }
        if (-not $rejected) { throw 'un manifest vacio dio verde' }
        $checks++

        Write-Host "Smoke-AddinSwap self-test: $checks comprobaciones, 0 fallos"
        exit 0
    } finally {
        $env:APPDATA = $oldAppData
        $env:TEMP = $oldTemp
        $full = [IO.Path]::GetFullPath($testRoot)
        $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
        if (($full + [IO.Path]::DirectorySeparatorChar).StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -and
            (Test-Path -LiteralPath $full)) {
            Remove-Item -LiteralPath $full -Recurse -Force
        }
    }
}

switch ($Mode) {

# ------------------------------------------------------------------ Backup
'Backup' {
    Assert-NoNavisworks
    $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
    $root  = Join-Path $env:TEMP "NavisCoord-smoke-backup-$stamp"
    New-Item -ItemType Directory -Path $root | Out-Null
    @{ schema = 'naviscoord.smoke-backup/1'; created = (Get-Date).ToUniversalTime().ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root '.naviscoord-smoke-backup.json') -Encoding UTF8

    $manifest = @()
    # Split on commas as well: `powershell -File script.ps1 -Versions 2026,2024`
    # hands the whole thing over as ONE string, and the script would then look
    # for "Navisworks Manage 2026,2024" and cheerfully report nothing to back
    # up — right before something replaced a real installation.
    $Versions = @($Versions | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    foreach ($v in $Versions) {
        $src = Get-PluginsDir $v
        if (-not (Test-Path $src)) {
            throw "NW${v}: no existe $src. Se aborta en vez de respaldar nada y dejar creer que hay backup."
        }
        $dest = Join-Path $root $v
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        # The WHOLE Plugins tree, not only what will be replaced: a restore
        # that can only put back what it expected to change is not a restore.
        $children = @(Get-ChildItem -LiteralPath $src -Force)
        if ($children.Count) { $children | Copy-Item -Destination $dest -Recurse -Force }
        foreach ($f in Get-ChildItem $src -Recurse -File) {
            $manifest += [pscustomobject]@{
                Version  = $v
                Relative = $f.FullName.Substring($src.Length).TrimStart('\')
                Existed  = $true
                Bytes    = $f.Length
                Sha256   = (Get-FileHash $f.FullName -Algorithm SHA256).Hash
            }
        }
        foreach ($m in $managed) {
            $relative = Join-Path $m.Folder $m.File
            if (-not ($manifest | Where-Object { $_.Version -eq $v -and $_.Relative -eq $relative })) {
                $manifest += [pscustomobject]@{
                    Version = $v; Relative = $relative; Existed = $false; Bytes = 0; Sha256 = ''
                }
            }
        }
        Write-Host "  NW${v}: respaldado en $dest"
    }

    $manifest | Export-Csv (Join-Path $root 'manifest.csv') -NoTypeInformation -Encoding UTF8
    @"
Restauracion manual si el script no estuviera disponible:

  powershell -File scripts\Smoke-AddinSwap.ps1 -Mode Restore -BackupRoot "$root"

O a mano, por version:
$(($Versions | ForEach-Object { "  copy /Y `"$root\$_\*`" `"$(Get-PluginsDir $_)`" /s" }) -join "`n")

manifest.csv lleva ruta original, tamano y SHA-256 de cada archivo.
"@ | Set-Content (Join-Path $root 'RESTAURAR.txt') -Encoding UTF8

    Write-Host ""
    Write-Host "BACKUP: $root"
    Write-Host "  $($manifest.Count) archivos con SHA-256 en manifest.csv"
    Write-Host $root   # last line = the path, for capture
}

# ------------------------------------------------------------------ Verify
'Verify' {
    if (-not $BackupRoot) { throw 'Verify necesita -BackupRoot' }
    $report = Invoke-VerifyBackup $BackupRoot
    Write-Host "Backup verificable: $($report.Correct) correctos, 0 distintos, 0 ausentes"
}

# ----------------------------------------------------------------- Install
'Install' {
    Assert-NoNavisworks
    $Versions = @($Versions | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    foreach ($v in $Versions) {
        foreach ($m in $managed) {
            $staged = Join-Path $repoRoot ('dist\addin\{0}\{1}' -f $v, $m.File)
            if (-not (Test-Path $staged)) {
                throw "no existe el binario compilado: $staged. Corre antes scripts\Build-Release.ps1 -Version $v."
            }
            $targetDir = Join-Path (Get-PluginsDir $v) $m.Folder
            if (-not (Test-Path $targetDir)) { New-Item -ItemType Directory -Force -Path $targetDir | Out-Null }
            $target = Join-Path $targetDir $m.File
            Publish-FileAtomic $staged $target
            $h = (Get-FileHash $target -Algorithm SHA256).Hash
            Write-Host ("  NW{0} {1,-16} instalado  {2}  {3} bytes" -f $v, $m.File, $h.Substring(0,16), (Get-Item $target).Length)
        }
    }
}

# ----------------------------------------------------------------- Restore
'Restore' {
    Assert-NoNavisworks
    if (-not $BackupRoot) { throw 'Restore necesita -BackupRoot' }
    $restored = Invoke-RestoreBackup $BackupRoot
    Write-Host "Restaurados o retirados según estado original: $restored; discrepancias: 0"
}

}
