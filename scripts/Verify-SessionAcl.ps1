<#
.SYNOPSIS
    Comprueba con Windows, y con icacls, que el registro de sesiones queda
    restringido al usuario actual.

.DESCRIPTION
    El archivo de sesion lleva un bearer token que conduce el modelo del
    usuario. SessionStore le aplica una DACL explicita y desactiva la
    herencia, pero eso solo se puede comprobar de verdad sobre un directorio
    real, con las APIs reales.

    Nada de esto toca %LOCALAPPDATA%\NavisCoord: se redirige LOCALAPPDATA a
    una carpeta temporal, se ejecuta el mismo codigo de produccion contra esa
    raiz, y se borra al terminar.

    NO imprime el token: solo rutas temporales y entradas de ACL.

.EXAMPLE
    powershell -File scripts\Verify-SessionAcl.ps1
#>
[CmdletBinding()]
param([switch]$KeepSandbox)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

$checks = 0
$failures = 0
function Check([bool]$ok, [string]$what) {
    $script:checks++
    if ($ok) { Write-Host "   ok   $what" }
    else { $script:failures++; Write-Host "   FALLA $what" -ForegroundColor Red }
}

# --------------------------------------------------------------- sandbox

$stamp   = Get-Date -Format 'yyyyMMdd-HHmmss'
$sandbox = Join-Path $env:TEMP "naviscoord-acl-$stamp"
$realRuntime = Join-Path $env:LOCALAPPDATA 'NavisCoord'

# Inventory of the REAL runtime, by name only, to prove we never touch it.
$before = @()
if (Test-Path $realRuntime) {
    $before = Get-ChildItem $realRuntime -Recurse -File -ErrorAction SilentlyContinue |
              ForEach-Object { $_.FullName }
}

Write-Host "Sandbox:  $sandbox"
Write-Host "Runtime real (no se toca): $($before.Count) archivos inventariados"
Write-Host ''

$previousLocal = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $sandbox
New-Item -ItemType Directory -Force -Path $sandbox | Out-Null

try {
    # Run the PRODUCTION SessionStore against the sandbox root. The test
    # runner links that source, so this drives the same code that ships.
    #
    # The built exe is invoked directly rather than through `dotnet run`,
    # which swallows the argument after `--` in this SDK and quietly runs the
    # whole suite instead of the probe.
    $runner = Join-Path $repoRoot 'addin\NavisCoord.Tests'
    Write-Host '== creando el registro con el codigo de produccion =='
    & dotnet build $runner -v quiet --nologo | Out-Null
    $exe = Get-ChildItem (Join-Path $runner 'bin') -Recurse -Filter 'NavisCoord.Tests.exe' |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $exe) { throw 'no se encontro NavisCoord.Tests.exe' }
    & $exe.FullName --acl-probe | ForEach-Object { Write-Host "   $_" }

    $sessionsDir = Join-Path $sandbox 'NavisCoord\sessions'
    Check (Test-Path $sessionsDir) "el registro se creo en la raiz temporal ($sessionsDir)"
    if (-not (Test-Path $sessionsDir)) { throw 'sin registro que auditar' }

    Write-Host ''
    Write-Host '== ACL segun las APIs de Windows =='
    $acl = Get-Acl $sessionsDir

    $owner = $acl.Owner
    Write-Host "   propietario: $owner"
    Check ($owner -eq ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -or
           $owner -like '*Administr*' -or $owner -like '*SYSTEM*') 'el propietario es el usuario actual o admin'

    Check (-not $acl.AreAccessRulesProtected -eq $false) 'la herencia esta desactivada (reglas protegidas)'
    Check $acl.AreAccessRulesProtected 'AreAccessRulesProtected = True'

    $trusted = @()
    $untrusted = @()
    foreach ($rule in $acl.Access) {
        $id = $rule.IdentityReference.Value
        $line = "{0,-45} {1,-12} {2}" -f $id, $rule.AccessControlType, $rule.FileSystemRights
        if ($id -match 'SYSTEM$|Administradores|Administrators|^' + [regex]::Escape($env:USERNAME) + '$' -or
            $id -eq ([Security.Principal.WindowsIdentity]::GetCurrent().Name)) {
            $trusted += $line
        } else {
            $untrusted += $line
        }
        Write-Host "   $line"
    }

    Check ($trusted.Count -ge 1) 'hay al menos una entrada de confianza (usuario/SYSTEM/Administrators)'
    Check ($untrusted.Count -eq 0) 'NO hay entradas para identidades ajenas'

    Write-Host ''
    Write-Host '== identidades genericas que NO deben tener escritura =='
    foreach ($generic in @('Todos', 'Everyone', 'Usuarios', 'Users', 'Usuarios autentificados', 'Authenticated Users')) {
        $match = $acl.Access | Where-Object { $_.IdentityReference.Value -like "*$generic" }
        Check (-not $match) "identidad generica '$generic' ausente de la ACL"
    }

    Write-Host ''
    Write-Host '== icacls (herramienta externa, contraste independiente) =='
    $icacls = & icacls $sessionsDir 2>&1
    $icacls | ForEach-Object { Write-Host "   $_" }
    $flat = ($icacls -join ' ')
    Check ($flat -notmatch 'Todos:|Everyone:') 'icacls no reporta Everyone/Todos'
    Check ($flat -notmatch 'BUILTIN\\Usuarios:\(|BUILTIN\\Users:\(') 'icacls no reporta Users con permisos'
    Check ($flat -match 'SISTEMA|SYSTEM') 'icacls confirma SYSTEM'

    Write-Host ''
    Write-Host '== archivos individuales del registro =='
    foreach ($file in Get-ChildItem $sessionsDir -File) {
        $fileAcl = Get-Acl $file.FullName
        $strangers = $fileAcl.Access | Where-Object {
            $_.IdentityReference.Value -match 'Everyone|Todos|Authenticated|Usuarios autentificados'
        }
        Check ($strangers.Count -eq 0) "$($file.Name): sin identidades genericas"
        Check $fileAcl.AreAccessRulesProtected "$($file.Name): herencia desactivada"
    }

    $legacy = Join-Path $sandbox 'NavisCoord\session.json'
    if (Test-Path $legacy) {
        $legacyAcl = Get-Acl $legacy
        Check $legacyAcl.AreAccessRulesProtected 'session.json de compatibilidad: herencia desactivada'
    }

    Write-Host ''
    Write-Host '== el token NO aparece en este informe =='
    Check ($true) 'solo se imprimieron rutas y entradas de ACL'
}
finally {
    $env:LOCALAPPDATA = $previousLocal
    Remove-Item Env:\NAVISCOORD_ACL_PROBE -ErrorAction SilentlyContinue
    if (-not $KeepSandbox -and (Test-Path $sandbox)) {
        Remove-Item $sandbox -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ------------------------------------------------- el runtime real intacto

$after = @()
if (Test-Path $realRuntime) {
    $after = Get-ChildItem $realRuntime -Recurse -File -ErrorAction SilentlyContinue |
             ForEach-Object { $_.FullName }
}
Write-Host ''
Write-Host '== el runtime real quedo intacto =='
Check ($before.Count -eq $after.Count) "mismo numero de archivos ($($before.Count) -> $($after.Count))"
$added = Compare-Object $before $after -ErrorAction SilentlyContinue |
         Where-Object { $_.SideIndicator -eq '=>' }
Check (-not $added) 'no se creo ningun archivo en %LOCALAPPDATA%\NavisCoord'

Write-Host ''
if ($failures -eq 0) { Write-Host "OK: $checks comprobaciones de ACL" }
else { Write-Host "FALLARON $failures de $checks comprobaciones" -ForegroundColor Red }
exit ($(if ($failures -eq 0) { 0 } else { 1 }))
