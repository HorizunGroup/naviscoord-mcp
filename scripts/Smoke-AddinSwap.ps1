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
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Backup', 'Verify', 'Install', 'Restore')]
    [string]$Mode,
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

switch ($Mode) {

# ------------------------------------------------------------------ Backup
'Backup' {
    Assert-NoNavisworks
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $root  = Join-Path $env:TEMP "NavisCoord-smoke-backup-$stamp"
    New-Item -ItemType Directory -Force -Path $root | Out-Null

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
        Copy-Item -Path (Join-Path $src '*') -Destination $dest -Recurse -Force
        foreach ($f in Get-ChildItem $src -Recurse -File) {
            $manifest += [pscustomobject]@{
                Version  = $v
                Relative = $f.FullName.Substring($src.Length).TrimStart('\')
                Original = $f.FullName
                Bytes    = $f.Length
                Sha256   = (Get-FileHash $f.FullName -Algorithm SHA256).Hash
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
    $manifest = @(Import-Csv (Join-Path $BackupRoot 'manifest.csv'))
    # An empty backup used to "verify" cleanly, which is the worst possible
    # answer: it green-lights replacing an installation there is nothing to
    # restore from.
    if ($manifest.Count -eq 0) {
        throw "el backup en $BackupRoot esta VACIO: no hay nada que restaurar, no se puede continuar"
    }
    $ok = 0; $bad = 0; $missing = 0
    foreach ($row in $manifest) {
        $copy = Join-Path (Join-Path $BackupRoot $row.Version) $row.Relative
        if (-not (Test-Path $copy)) { $missing++; Write-Host "  FALTA en backup: $($row.Relative)"; continue }
        $hash = (Get-FileHash $copy -Algorithm SHA256).Hash
        if ($hash -eq $row.Sha256) { $ok++ } else { $bad++; Write-Host "  HASH DISTINTO: $($row.Relative)" }
    }
    Write-Host "Backup verificable: $ok correctos, $bad distintos, $missing ausentes"
    if ($bad -or $missing) { throw 'el backup NO es restaurable con fidelidad' }
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
            Copy-Item $staged -Destination $target -Force
            $h = (Get-FileHash $target -Algorithm SHA256).Hash
            Write-Host ("  NW{0} {1,-16} instalado  {2}  {3} bytes" -f $v, $m.File, $h.Substring(0,16), (Get-Item $target).Length)
        }
    }
}

# ----------------------------------------------------------------- Restore
'Restore' {
    Assert-NoNavisworks
    if (-not $BackupRoot) { throw 'Restore necesita -BackupRoot' }
    $manifest = Import-Csv (Join-Path $BackupRoot 'manifest.csv')
    $restored = 0; $mismatched = 0
    foreach ($row in $manifest) {
        $copy = Join-Path (Join-Path $BackupRoot $row.Version) $row.Relative
        if (-not (Test-Path $copy)) { continue }
        $targetDir = Split-Path $row.Original -Parent
        if (-not (Test-Path $targetDir)) { New-Item -ItemType Directory -Force -Path $targetDir | Out-Null }
        Copy-Item $copy -Destination $row.Original -Force
        $hash = (Get-FileHash $row.Original -Algorithm SHA256).Hash
        if ($hash -eq $row.Sha256) { $restored++ }
        else { $mismatched++; Write-Host "  NO COINCIDE tras restaurar: $($row.Relative)" }
    }
    Write-Host "Restaurados con hash identico: $restored; discrepancias: $mismatched"
    if ($mismatched) { throw 'la restauracion no reprodujo el estado original' }
}

}
