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

    Este archivo es SOLO el entrypoint de produccion. Su trabajo es, en este
    orden y sin excepciones:

      1. Assert-NoNavisworks       (compuerta, sin parametros que la relajen)
      2. resolver las raices REALES desde $env:APPDATA
      3. delegar en el nucleo transaccional de SwapCore.ps1

    El nucleo recibe las raices como parametro y no lee $env:APPDATA ni
    consulta procesos. Esa separacion es lo que permite probar el ciclo
    completo contra un directorio temporal sin desactivar nada: un test llama
    al nucleo con un temporal porque ES un temporal, no porque haya convencido
    al entrypoint de que lo trate como tal.

    Que el orden se respeta lo comprueba `-SelfTest`, no un comentario.

    El binario sale de dist\addin\<version>\, que es lo que deja
    scripts\Build-Release.ps1.

.EXAMPLE
    .\Smoke-AddinSwap.ps1 -Mode Backup  -Versions 2026
    .\Smoke-AddinSwap.ps1 -Mode Verify  -BackupRoot <ruta>
    .\Smoke-AddinSwap.ps1 -Mode Install -Versions 2026
    .\Smoke-AddinSwap.ps1 -Mode Restore -BackupRoot <ruta>
    .\Smoke-AddinSwap.ps1 -SelfTest
#>
[CmdletBinding(DefaultParameterSetName = 'Run')]
param(
    [Parameter(ParameterSetName = 'Run', Mandatory = $true)]
    [ValidateSet('Backup', 'Verify', 'Install', 'Restore')]
    [string]$Mode,
    [Parameter(ParameterSetName = 'Run')]
    [string[]]$Versions = @('2026'),
    [Parameter(ParameterSetName = 'Run')]
    [string]$BackupRoot,
    [Parameter(ParameterSetName = 'SelfTest', Mandatory = $true)]
    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

# Se guarda ANTES de cargar nada. Los scripts que se cargan con punto declaran
# su propio `param([switch]$SelfTest)`, y el dot-sourcing corre en ESTE ambito:
# ese param vuelve a ligar $SelfTest a su valor por defecto, $false. El sintoma
# fue una autoprueba que salia con codigo 0 sin ejecutar una sola comprobacion
# -verde perfecto, cero evidencia-.
$wantSelfTest = [bool]$SelfTest

# Solo esto se sustituye. Todo lo demas en Plugins se queda donde esta.
$managed = @(
    @{ Folder = 'NavisCoord'; File = 'NavisCoord.dll' }
)

. (Join-Path $PSScriptRoot 'NavisworksSafety.ps1')
. (Join-Path $PSScriptRoot 'SwapCore.ps1')

function Get-PluginsDir([string]$v) {
    Join-Path $env:APPDATA "Autodesk\Navisworks Manage $v\Plugins"
}

function Expand-Versions([string[]]$Raw) {
    # `powershell -File script.ps1 -Versions 2026,2024` entrega TODO como UNA
    # cadena, y el script buscaria "Navisworks Manage 2026,2024", no
    # encontraria nada y anunciaria alegremente que no habia que respaldar
    # -justo antes de que algo sustituyera una instalacion real-.
    return @($Raw | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Get-RealRoots([string[]]$Raw) {
    $roots = @{}
    foreach ($v in (Expand-Versions $Raw)) { $roots[$v] = Get-PluginsDir $v }
    return $roots
}

function Assert-Result($result) {
    foreach ($line in $result.Items) { if ($line -is [string]) { Write-Host "  $line" } }
    if ($result.Outcome -ne 'completed') {
        throw "$($result.Outcome): $($result.Detail)"
    }
}

# ----------------------------------------------------------------- autoprueba

if ($wantSelfTest) {
    # Guard ESTRUCTURAL del orden. No comprueba que exista una llamada -eso lo
    # haria un grep- sino que en cada modo la compuerta aparece ANTES de
    # cualquier entrada al nucleo. El defecto que previene es real: basta mover
    # una linea para que Install resuelva raices y copie antes de mirar si
    # Navisworks esta vivo, y nada mas en el sistema lo notaria.
    $failures = 0; $checks = 0
    function Check([string]$name, [bool]$ok, [string]$detail = '') {
        $script:checks++
        if ($ok) { Write-Output "  ok    $name" }
        else { $script:failures++; Write-Output "  FALLA $name $detail" }
    }

    $text = Get-Content -LiteralPath $PSCommandPath -Raw
    $body = $text.Substring($text.IndexOf('switch ($Mode)'))
    $coreEntry = 'Invoke-BackupCore', 'Invoke-VerifyCore', 'Invoke-InstallCore', 'Invoke-RestoreCore'

    foreach ($mode in @('Backup', 'Verify', 'Install', 'Restore')) {
        $start = $body.IndexOf("'$mode' {")
        Check "el modo $mode existe" ($start -ge 0)
        if ($start -lt 0) { continue }
        $next = ($coreEntry | ForEach-Object { $body.IndexOf($_, $start) } |
                 Where-Object { $_ -gt 0 } | Sort-Object | Select-Object -First 1)
        $gate = $body.IndexOf('Assert-NoNavisworks', $start)
        Check "$mode entra al nucleo" ($null -ne $next)
        Check "$mode llama a la compuerta" ($gate -ge 0)
        Check "$mode la llama ANTES del nucleo" ($gate -ge 0 -and $null -ne $next -and $gate -lt $next) `
            "(compuerta en $gate, nucleo en $next)"
    }

    # El nucleo no puede mirar el entorno ni los procesos por su cuenta: si lo
    # hiciera, un test con raices temporales estaria ejercitando otra cosa.
    # Se analiza CODIGO, no texto: los comentarios y los bloques de ayuda
    # nombran estas cosas precisamente para explicar por que el nucleo no las
    # usa, y contarlas como uso convierte el guard en un grep con opinion.
    $coreRaw = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'SwapCore.ps1') -Raw
    $tokens = $null; $errs = $null
    [System.Management.Automation.Language.Parser]::ParseInput($coreRaw, [ref]$tokens, [ref]$errs) | Out-Null
    $coreCode = ($tokens |
        Where-Object { $_.Kind -ne 'Comment' } |
        ForEach-Object { $_.Text }) -join ' '
    foreach ($forbidden in @('env:APPDATA', 'Get-Process', 'Get-PluginsDir')) {
        Check "el nucleo no usa '$forbidden'" (-not $coreCode.Contains($forbidden))
    }
    # Y el guard tiene que saber fallar: si no distinguiera codigo de texto,
    # esta linea inventada lo demostraria.
    Check 'el guard detecta un uso real, no solo su ausencia' `
        (($coreCode + ' $env:APPDATA').Contains('env:APPDATA'))
    # Y la compuerta sigue sin admitir nada que la relaje.
    $gateParams = (Get-Command Assert-NoNavisworks).Parameters.Keys |
        Where-Object { $_ -notin [System.Management.Automation.PSCmdlet]::CommonParameters }
    Check 'Assert-NoNavisworks no admite parametros' (@($gateParams).Count -eq 0)

    Write-Output ''
    Write-Output "Smoke-AddinSwap orden de preflight: $checks comprobaciones, $failures fallo(s)"
    if ($failures) { exit 1 }
    exit 0
}

switch ($Mode) {

# ------------------------------------------------------------------ Backup
'Backup' {
    Assert-NoNavisworks
    $roots = Get-RealRoots $Versions
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $root = Join-Path $env:TEMP "NavisCoord-smoke-backup-$stamp"
    New-Item -ItemType Directory -Force -Path $root | Out-Null

    $result = Invoke-BackupCore -Roots $roots -BackupRoot $root
    Assert-Result $result

    @"
Restauracion manual si el script no estuviera disponible:

  powershell -File scripts\Smoke-AddinSwap.ps1 -Mode Restore -BackupRoot "$root"

manifest.csv lleva ruta original, tamano y SHA-256 de cada archivo.
"@ | Set-Content (Join-Path $root 'RESTAURAR.txt') -Encoding UTF8

    Write-Host ""
    Write-Host "BACKUP: $root"
    Write-Host "  $($result.Items.Count) archivos con SHA-256 en manifest.csv"
    Write-Host $root   # ultima linea = la ruta, para capturarla
}

# ------------------------------------------------------------------ Verify
'Verify' {
    Assert-NoNavisworks
    if (-not $BackupRoot) { throw 'Verify necesita -BackupRoot' }
    $manifest = @(Import-Csv (Join-Path $BackupRoot 'manifest.csv'))
    $roots = Get-RealRoots (@($manifest | ForEach-Object { $_.Version } | Sort-Object -Unique))
    $result = Invoke-VerifyCore -BackupRoot $BackupRoot -Roots $roots
    Write-Host "Backup verificable: $($result.Restored) correctos, $($result.Mismatched) distintos, $($result.Rejected) rechazados/ausentes"
    Assert-Result $result
}

# ----------------------------------------------------------------- Install
'Install' {
    Assert-NoNavisworks
    $roots = Get-RealRoots $Versions
    $result = Invoke-InstallCore -Roots $roots -StagedDir (Join-Path $repoRoot 'dist\addin') -Managed $managed
    Assert-Result $result
    foreach ($item in $result.Items) {
        Write-Host ("  NW{0} {1,-24} instalado  {2}" -f $item.Version, (Split-Path -Leaf $item.Path), $item.Sha256.Substring(0, 16))
    }
}

# ----------------------------------------------------------------- Restore
'Restore' {
    Assert-NoNavisworks
    if (-not $BackupRoot) { throw 'Restore necesita -BackupRoot' }
    $manifest = @(Import-Csv (Join-Path $BackupRoot 'manifest.csv'))
    $roots = Get-RealRoots (@($manifest | ForEach-Object { $_.Version } | Sort-Object -Unique))
    $result = Invoke-RestoreCore -BackupRoot $BackupRoot -Roots $roots -Managed $managed
    Write-Host "Restaurados con hash identico: $($result.Restored); discrepancias: $($result.Mismatched); retirados: $($result.Removed)"
    Assert-Result $result
}

}
