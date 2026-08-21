<#
.SYNOPSIS
    Decide si es seguro sustituir archivos de plugins, y por que.

.DESCRIPTION
    Con Navisworks vivo, el DLL esta bloqueado y la copia falla a medias. La
    comprobacion es una compuerta de produccion, y por eso este archivo existe:

    La version anterior de `Assert-NoNavisworks` acepto un marcador de sandbox
    -un archivo `.naviscoord-sandbox` dentro de la raiz- que la desactivaba.
    Se introdujo para que una prueba de extremo a extremo pudiera correr con
    Navisworks abierto. Esta mal, y no por teoria: cualquier cosa capaz de
    crear un archivo en el directorio de plugins podia apagar la unica
    proteccion que impide sustituir un DLL en uso. Una compuerta de pruebas no
    puede desactivar seguridad en produccion.

    El arreglo no es "quitar el marcador y aguantarse": es separar la DECISION
    de la OBSERVACION.

      * `Test-NavisworksSafety` es pura. Recibe una lista de procesos ya
        normalizada y devuelve un veredicto con su evidencia. Las pruebas la
        llaman con listas inventadas, y por eso no necesitan que haya -ni que
        no haya- un Navisworks abierto en la maquina de nadie.
      * `Get-NavisworksProcesses` es la unica que mira el sistema. Distingue
        "no hay ninguno" de "no pude averiguarlo", que no es lo mismo.
      * `Assert-NoNavisworks` une las dos y lanza. No admite ningun parametro
        capaz de relajarla: ni -Force, ni -Skip, ni marcador, ni variable de
        entorno, ni un nombre especial de carpeta.

    Ante la duda se bloquea. Un fallo enumerando procesos es "no lo se", y "no
    lo se" no autoriza escribir encima de un DLL que puede estar en uso.

.PARAMETER SelfTest
    Ejecuta las comprobaciones de este archivo y sale. No mira el sistema.
#>
[CmdletBinding()]
param([switch]$SelfTest)

# Nombres de proceso que significan "Navisworks esta vivo". `Roamer` es el
# ejecutable real de Navisworks Manage; el resto cubre variantes y lanzadores.
$script:NavisworksProcessNames = @('roamer', 'navisworks', 'navisworksmanage', 'navisworksimulate')

function Test-IsNavisworksName {
    <#
        Comparacion en minusculas y sin extension. Un proceso llamado
        `ROAMER` o `Roamer.exe` es el mismo proceso: dejar que la caja o la
        extension decidan convierte la compuerta en una adivinanza.
    #>
    param([AllowEmptyString()][AllowNull()][string]$Name)

    if ([string]::IsNullOrWhiteSpace($Name)) { return $false }
    $bare = [System.IO.Path]::GetFileNameWithoutExtension($Name.Trim()).ToLowerInvariant()
    if (-not $bare) { $bare = $Name.Trim().ToLowerInvariant() }
    return $script:NavisworksProcessNames -contains $bare
}

function Test-NavisworksSafety {
    <#
        La decision, sin tocar el sistema.

        -Snapshot es la lista observada de procesos: objetos con Name y,
        opcionalmente, Id. -Enumerated dice si esa observacion se pudo
        completar; $false significa "no lo se", no "no habia ninguno".

        Devuelve Safe (bool), Verdict y Evidence. Tres veredictos:

          clear          ningun proceso Navisworks vivo -> se puede operar
          blocked        hay al menos uno -> no se opera
          indeterminate  no se pudo saber -> no se opera (fail-closed)
    #>
    param(
        [AllowNull()][object[]]$Snapshot = @(),
        [bool]$Enumerated = $true
    )

    if (-not $Enumerated) {
        return [pscustomobject]@{
            Safe = $false; Verdict = 'indeterminate'
            Evidence = 'no se pudo enumerar los procesos del sistema; ante la duda no se sustituye nada'
        }
    }

    $hits = @()
    foreach ($process in @($Snapshot)) {
        if ($null -eq $process) { continue }
        $name = $null
        # Acepta objetos de Get-Process, hashtables de prueba o cadenas
        # sueltas. Lo que NO acepta es un objeto del que no pueda leer nombre:
        # eso se cuenta como observacion incompleta, no como ausencia.
        if ($process -is [string]) { $name = $process }
        elseif ($process -is [hashtable]) { $name = [string]$process['Name'] }
        elseif ($process.PSObject.Properties.Name -contains 'ProcessName') { $name = [string]$process.ProcessName }
        elseif ($process.PSObject.Properties.Name -contains 'Name') { $name = [string]$process.Name }
        else {
            return [pscustomobject]@{
                Safe = $false; Verdict = 'indeterminate'
                Evidence = 'la lista de procesos trae una entrada sin nombre legible'
            }
        }
        if (Test-IsNavisworksName -Name $name) {
            $id = if ($process -is [string]) { '' }
                  elseif ($process -is [hashtable]) { [string]$process['Id'] }
                  elseif ($process.PSObject.Properties.Name -contains 'Id') { [string]$process.Id }
                  else { '' }
            $hits += if ($id) { "$name PID $id" } else { $name }
        }
    }

    if ($hits.Count) {
        return [pscustomobject]@{
            Safe = $false; Verdict = 'blocked'
            Evidence = "Navisworks esta abierto ($($hits -join ', '))"
        }
    }
    return [pscustomobject]@{
        Safe = $true; Verdict = 'clear'; Evidence = 'ningun proceso de Navisworks vivo'
    }
}

function Get-NavisworksProcesses {
    <#
        La unica funcion que mira el sistema. Devuelve la lista y si la
        enumeracion se completo; un fallo aqui no se convierte en lista vacia.
    #>
    try {
        $all = @(Get-Process -ErrorAction Stop)
        return [pscustomobject]@{ Snapshot = $all; Enumerated = $true }
    }
    catch {
        return [pscustomobject]@{ Snapshot = @(); Enumerated = $false }
    }
}

function Assert-NoNavisworks {
    <#
        La compuerta de produccion. Sin parametros: no hay nada que un caller
        pueda pasar para relajarla, y esa ausencia es la garantia.
    #>
    $observed = Get-NavisworksProcesses
    $verdict = Test-NavisworksSafety -Snapshot $observed.Snapshot -Enumerated $observed.Enumerated
    if ($verdict.Safe) { return }
    throw ("$($verdict.Evidence). No se sustituye nada con el proceso vivo: " +
           "el DLL queda bloqueado y la copia falla a medias. " +
           "Cierra Navisworks y repite. (veredicto: $($verdict.Verdict))")
}

# ----------------------------------------------------------------- autoprueba

if ($SelfTest) {
    $checks = 0; $failures = 0
    function Check([string]$name, [bool]$ok, [string]$detail = '') {
        $script:checks++
        if ($ok) { Write-Output "  ok    $name" }
        else { $script:failures++; Write-Output "  FALLA $name $detail" }
    }

    # Ninguna de estas comprobaciones mira el sistema: la decision es pura, y
    # por eso no dependen de que haya -o no- un Navisworks abierto aqui.
    $none = Test-NavisworksSafety -Snapshot @() -Enumerated $true
    Check 'sin procesos Navisworks: permitido' ($none.Safe -and $none.Verdict -eq 'clear')

    $others = Test-NavisworksSafety -Snapshot @(
        @{ Name = 'chrome'; Id = 1 }, @{ Name = 'devenv'; Id = 2 }) -Enumerated $true
    Check 'otros procesos no bloquean' $others.Safe

    foreach ($variant in @('Roamer', 'roamer', 'ROAMER', 'Roamer.exe', 'roamer.EXE')) {
        $v = Test-NavisworksSafety -Snapshot @(@{ Name = $variant; Id = 42 }) -Enumerated $true
        Check "'$variant' bloquea" (-not $v.Safe -and $v.Verdict -eq 'blocked')
    }

    $named = Test-NavisworksSafety -Snapshot @(@{ Name = 'Roamer'; Id = 7 }) -Enumerated $true
    Check 'el veredicto nombra el proceso y su PID' ($named.Evidence -match 'Roamer' -and $named.Evidence -match '7')

    $failed = Test-NavisworksSafety -Snapshot @() -Enumerated $false
    Check 'no poder enumerar es indeterminado, no "no hay"' `
        (-not $failed.Safe -and $failed.Verdict -eq 'indeterminate')

    $opaque = Test-NavisworksSafety -Snapshot @([pscustomobject]@{ Otra = 'cosa' }) -Enumerated $true
    Check 'una entrada sin nombre legible es indeterminada' `
        (-not $opaque.Safe -and $opaque.Verdict -eq 'indeterminate')

    # El punto de todo esto: nada externo puede cambiar la decision.
    $env:NAVISCOORD_SKIP_NAVISWORKS_CHECK = '1'
    $env:NAVISCOORD_FORCE = 'true'
    try {
        $hostile = Test-NavisworksSafety -Snapshot @(@{ Name = 'Roamer'; Id = 1 }) -Enumerated $true
        Check 'una variable de entorno hostil no cambia el veredicto' (-not $hostile.Safe)
    }
    finally {
        Remove-Item Env:\NAVISCOORD_SKIP_NAVISWORKS_CHECK -ErrorAction SilentlyContinue
        Remove-Item Env:\NAVISCOORD_FORCE -ErrorAction SilentlyContinue
    }

    # Un marcador de sandbox en disco no participa en la decision, porque la
    # decision no lee el disco. Se comprueba estructuralmente.
    $pure = (Get-Command Test-NavisworksSafety).ScriptBlock.ToString()
    foreach ($forbidden in @('Test-Path', 'Get-Content', 'Get-Item', 'sandbox', 'Env:')) {
        Check "la decision no consulta '$forbidden'" (-not $pure.Contains($forbidden))
    }

    # Y la compuerta de produccion no admite parametros.
    $gate = (Get-Command Assert-NoNavisworks).Parameters.Keys |
        Where-Object { $_ -notin [System.Management.Automation.PSCmdlet]::CommonParameters }
    Check 'Assert-NoNavisworks no expone ningun parametro' (@($gate).Count -eq 0) `
        "(expone: $($gate -join ', '))"

    Write-Output ''
    Write-Output "NavisworksSafety autoprueba: $checks comprobaciones, $failures fallo(s)"
    if ($failures) { exit 1 }
    exit 0
}
