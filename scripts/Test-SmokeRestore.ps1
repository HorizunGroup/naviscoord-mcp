<#
.SYNOPSIS
    Ciclo completo Backup / Verify / Install / Restore sobre raices temporales,
    mas inyeccion de fallos.

.DESCRIPTION
    Llama al NUCLEO transaccional de SwapCore.ps1, que recibe las raices como
    parametro. Por eso el ciclo entero -incluida la sustitucion del DLL- corre
    aunque Navisworks este abierto, y sin desactivar ninguna proteccion.

    Que eso no sea un bypass es la parte importante. La version anterior de
    esta prueba escribia un marcador `.naviscoord-sandbox` que apagaba
    `Assert-NoNavisworks`: cualquier cosa capaz de crear un archivo en el
    directorio de plugins podia apagar la unica proteccion que impide
    sustituir un DLL en uso. Ahora:

      * el nucleo no tiene ningun parametro que relaje una comprobacion;
        recibe rutas y opera sobre las rutas que recibe;
      * el entrypoint de produccion llama SIEMPRE a la compuerta antes de
        resolver las raices reales, y `Smoke-AddinSwap.ps1 -SelfTest` lo
        comprueba estructuralmente en los cuatro modos.

    Llegar al nucleo requiere escribir codigo que lo importe. Quien ya puede
    ejecutar codigo arbitrario no necesitaba este rodeo.

.EXAMPLE
    pwsh -File scripts\Test-SmokeRestore.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'SwapCore.ps1')

$checks = 0; $failures = 0
function Check([string]$name, [bool]$ok, [string]$detail = '') {
    $script:checks++
    if ($ok) { Write-Host "  ok    $name" }
    else { $script:failures++; Write-Host "  FALLA $name $detail" }
}

$managed = @(@{ Folder = 'NavisCoord'; File = 'NavisCoord.dll' })
$version = '9999'
$nonce = [guid]::NewGuid().ToString('N').Substring(0, 12)
$sandbox = Join-Path ([System.IO.Path]::GetTempPath()) "naviscoord-smoketest-$nonce"

New-Item -ItemType Directory -Force -Path $sandbox | Out-Null
Set-Content -LiteralPath (Join-Path $sandbox '.naviscoord-selftest') -Value $nonce -Encoding UTF8

function Get-TreeSnapshot([string]$Root) {
    # Snapshot COMPLETO: no basta con "el DLL principal existe". Un rollback se
    # juzga por el arbol entero, incluidos los archivos que nadie esperaba.
    $out = @{}
    if (-not (Test-Path -LiteralPath $Root)) { return $out }
    $base = (Get-Item -LiteralPath $Root -Force).FullName.TrimEnd('\')
    foreach ($f in (Get-ChildItem -LiteralPath $Root -Recurse -File -Force)) {
        $out[$f.FullName.Substring($base.Length).TrimStart('\')] =
            (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash
    }
    return $out
}
function Compare-Snapshot($a, $b) {
    if ($a.Count -ne $b.Count) { return $false }
    foreach ($k in $a.Keys) { if ($b[$k] -ne $a[$k]) { return $false } }
    return $true
}

try {
    $plugins = Join-Path $sandbox "Plugins"
    New-Item -ItemType Directory -Force -Path (Join-Path $plugins 'OtroPlugin') | Out-Null
    $ajeno = Join-Path $plugins 'OtroPlugin\Ajeno.dll'
    Set-Content -LiteralPath $ajeno -Value 'plugin de un tercero' -Encoding UTF8
    $roots = @{ $version = $plugins }

    $staged = Join-Path $sandbox 'staged'
    New-Item -ItemType Directory -Force -Path (Join-Path $staged $version) | Out-Null
    Set-Content -LiteralPath (Join-Path $staged "$version\NavisCoord.dll") `
        -Value "dll de prueba $nonce" -Encoding UTF8

    $before = Get-TreeSnapshot $plugins

    # ---------------------------------------------------------------- Backup
    $backupRoot = Join-Path $sandbox 'backup'
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $r = Invoke-BackupCore -Roots $roots -BackupRoot $backupRoot
    Check 'Backup completa' ($r.Outcome -eq 'completed') $r.Detail
    Check 'respalda el plugin ajeno' (@($r.Items | Where-Object { $_.Relative -eq 'OtroPlugin\Ajeno.dll' }).Count -eq 1)
    Check 'y NO contiene el add-in, porque no estaba instalado' `
        (@($r.Items | Where-Object { $_.Relative -like '*NavisCoord.dll' }).Count -eq 0)

    # ---------------------------------------------------------------- Verify
    $r = Invoke-VerifyCore -BackupRoot $backupRoot -Roots $roots
    Check 'Verify acepta un backup legitimo' ($r.Outcome -eq 'completed') $r.Detail

    # --------------------------------------------------------------- Install
    $r = Invoke-InstallCore -Roots $roots -StagedDir $staged -Managed $managed
    Check 'Install completa' ($r.Outcome -eq 'completed') $r.Detail
    $installed = Join-Path $plugins 'NavisCoord\NavisCoord.dll'
    Check 'el add-in queda en su sitio' (Test-Path -LiteralPath $installed)
    Check 'y el plugin ajeno no cambio' `
        ((Get-FileHash -LiteralPath $ajeno -Algorithm SHA256).Hash -eq $before['OtroPlugin\Ajeno.dll'])

    # --------------------------------------------------------------- Restore
    $r = Invoke-RestoreCore -BackupRoot $backupRoot -Roots $roots -Managed $managed
    Check 'Restore completa' ($r.Outcome -eq 'completed') $r.Detail
    Check 'y retira lo que no existia antes' ($r.Removed -eq 1) "(retirados: $($r.Removed))"
    $after = Get-TreeSnapshot $plugins
    Check 'el arbol vuelve EXACTAMENTE al snapshot inicial' (Compare-Snapshot $before $after) `
        "(antes $($before.Count) archivos, despues $($after.Count))"

    # ------------------------------------------- inyeccion de fallo: staging
    $vacio = Join-Path $sandbox 'staged-vacio'
    New-Item -ItemType Directory -Force -Path (Join-Path $vacio $version) | Out-Null
    $r = Invoke-InstallCore -Roots $roots -StagedDir $vacio -Managed $managed
    Check 'sin binario compilado, Install falla' ($r.Outcome -eq 'failed')
    Check 'y no deja nada escrito' (Compare-Snapshot $before (Get-TreeSnapshot $plugins))

    # ------------------------------------------- inyeccion: backup vacio
    $vacioBk = Join-Path $sandbox 'backup-vacio'
    New-Item -ItemType Directory -Force -Path $vacioBk | Out-Null
    @() | Export-Csv (Join-Path $vacioBk 'manifest.csv') -NoTypeInformation -Encoding UTF8
    $r = Invoke-VerifyCore -BackupRoot $vacioBk -Roots $roots
    Check 'un backup VACIO no verifica limpiamente' ($r.Outcome -eq 'failed')

    # ------------------------------------------- manifiesto manipulado
    foreach ($hostil in @('..\..\..\..\VICTIMA.dll', 'C:\Windows\System32\x.dll',
                          '\\servidor\share\x.dll', 'NavisCoord\x.dll:oculto')) {
        $evil = Join-Path $sandbox ("evil-" + [guid]::NewGuid().ToString('N').Substring(0,6))
        New-Item -ItemType Directory -Force -Path (Join-Path $evil $version) | Out-Null
        @([pscustomobject]@{
            Version = $version; Relative = $hostil
            Original = (Join-Path $sandbox 'VICTIMA.dll'); Bytes = 1
            Sha256 = '0' * 64
        }) | Export-Csv (Join-Path $evil 'manifest.csv') -NoTypeInformation -Encoding UTF8

        $r = Invoke-RestoreCore -BackupRoot $evil -Roots $roots -Managed $managed
        Check "Restore rechaza '$hostil'" ($r.Outcome -eq 'failed') "(detalle: $($r.Detail))"
        Check "  y no escribio nada" (Compare-Snapshot $before (Get-TreeSnapshot $plugins))
    }
    Check 'ningun destino hostil llego a existir' `
        (-not (Test-Path -LiteralPath (Join-Path $sandbox 'VICTIMA.dll')))
}
finally {
    $resolved = [System.IO.Path]::GetFullPath($sandbox)
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $marker = Join-Path $resolved '.naviscoord-selftest'
    $owned = (Test-Path -LiteralPath $marker) -and
             ((Get-Content -LiteralPath $marker -Raw).Trim() -eq $nonce)
    if ($owned -and $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        -not (Test-ReparsePath -Path $resolved) -and $resolved -ne $tempRoot.TrimEnd('\')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
        if (Test-Path -LiteralPath $resolved) { Write-Host "  aviso el sandbox no se borro del todo" }
    }
    else { Write-Host "  aviso no se borro $resolved" }
}

Write-Host ''
Write-Host "Smoke ciclo completo + fault injection: $checks comprobaciones, $failures fallo(s)"
if ($failures) { exit 1 }
exit 0
