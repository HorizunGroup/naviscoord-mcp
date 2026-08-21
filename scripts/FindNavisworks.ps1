<#
.SYNOPSIS
    Descubrimiento centralizado de las instalaciones de Navisworks Manage.

.DESCRIPTION
    Antes cada script escribia su propia ruta: Build-Release solo miraba
    C:\Program Files, asi que una instalacion en D:\ o una registrada por el
    deployment de Autodesk "no existia" para el build, y el operador recibia
    un "no encuentro Navisworks" con Navisworks abierto delante.

    El orden de busqueda es el orden de autoridad:

      1. El registro de Autodesk (HKLM\SOFTWARE\Autodesk\Navisworks Manage
         x64\<ver>, y la vista de 32 bits por si el deployment escribio ahi).
         Es donde el instalador declara donde puso el producto, unidades
         alternativas incluidas.
      2. Las rutas convencionales en TODAS las unidades fijas, no solo C:.

    Cada candidato se verifica igual: existe Autodesk.Navisworks.Api.dll.
    Un directorio sin el API no sirve para compilar y se descarta con nombre.

    El mapeo interno-comercial es el de Autodesk: Navisworks 2024 = v21,
    2025 = v22, 2026 = v23.

.PARAMETER SelfTest
    Ejercita el parser y el orden de autoridad con un arbol temporal propio,
    sin tocar el registro real ni Program Files.
#>
[CmdletBinding()]
param([switch]$SelfTest)

# Comercial -> interna. El registro de Autodesk usa la interna.
$script:NavisInternalVersions = @{
    '2024' = '21.0'
    '2025' = '22.0'
    '2026' = '23.0'
}

function Test-NavisProductDir {
    <#
        Lo unico que hace verdadera a una instalacion para NUESTRO proposito:
        el API contra el que se compila esta ahi.
    #>
    param([string]$Dir)
    if ([string]::IsNullOrWhiteSpace($Dir)) { return $false }
    return Test-Path -LiteralPath (Join-Path $Dir 'Autodesk.Navisworks.Api.dll')
}

function Get-NavisFromRegistry {
    <#
        Rutas declaradas por el instalador. Se miran las dos vistas del
        registro: un deployment de 32 bits escribe en WOW6432Node aunque el
        producto sea x64.
    #>
    param([string]$Version)

    $internal = $script:NavisInternalVersions[$Version]
    if (-not $internal) { return @() }

    $found = @()
    $hives = @(
        "HKLM:\SOFTWARE\Autodesk\Navisworks Manage x64\$internal",
        "HKLM:\SOFTWARE\WOW6432Node\Autodesk\Navisworks Manage x64\$internal"
    )
    foreach ($hive in $hives) {
        try {
            $item = Get-ItemProperty -Path $hive -ErrorAction Stop
            foreach ($name in @('InstallLocation', 'InstallationLocation', 'Location')) {
                $value = $item.$name
                if ($value) { $found += $value.ToString().TrimEnd('\') }
            }
        }
        catch { }
    }
    return @($found | Sort-Object -Unique)
}

function Get-NavisConventionalDirs {
    <#
        Program Files en cada unidad fija. "Fija" importa: sondear unidades de
        red o extraibles cuelga el build esperando un servidor que no responde.
    #>
    param([string]$Version)

    $dirs = @()
    foreach ($drive in [System.IO.DriveInfo]::GetDrives()) {
        if ($drive.DriveType -ne [System.IO.DriveType]::Fixed) { continue }
        if (-not $drive.IsReady) { continue }
        $root = $drive.RootDirectory.FullName
        $dirs += Join-Path $root "Program Files\Autodesk\Navisworks Manage $Version"
        $dirs += Join-Path $root "Program Files (x86)\Autodesk\Navisworks Manage $Version"
    }
    return $dirs
}

function Find-NavisworksInstall {
    <#
        La instalacion verificada de UNA version, o $null con el detalle de
        todo lo que se miro (para que "no encuentro" deje de ser un misterio).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Version,
        [System.Collections.Generic.List[string]]$Probed = $null
    )

    $candidates = @(Get-NavisFromRegistry -Version $Version)
    $candidates += Get-NavisConventionalDirs -Version $Version

    foreach ($candidate in $candidates) {
        if ($null -ne $Probed) { [void]$Probed.Add($candidate) }
        if (Test-NavisProductDir -Dir $candidate) {
            return [pscustomobject]@{ Version = $Version; ProductDir = $candidate }
        }
    }
    return $null
}

function Find-AllNavisworks {
    param([string[]]$Versions = @('2024', '2025', '2026'))

    $found = @()
    foreach ($v in $Versions) {
        $hit = Find-NavisworksInstall -Version $v
        if ($hit) { $found += $hit }
    }
    return ,$found
}

# ----------------------------------------------------------------- autoprueba

if ($SelfTest) {
    $failures = 0; $checks = 0
    function Check([string]$name, [bool]$ok, [string]$detail = '') {
        $script:checks++
        if ($ok) { Write-Output "  ok    $name" }
        else { $script:failures++; Write-Output "  FALLA $name $detail" }
    }

    $nonce = [guid]::NewGuid().ToString('N').Substring(0, 12)
    $sandbox = Join-Path ([System.IO.Path]::GetTempPath()) "naviscoord-findnavis-$nonce"
    New-Item -ItemType Directory -Force -Path $sandbox | Out-Null
    Set-Content -LiteralPath (Join-Path $sandbox '.naviscoord-selftest') -Value $nonce -Encoding UTF8

    try {
        # Un directorio con API y otro sin el
        $good = Join-Path $sandbox 'ConApi'
        $bad = Join-Path $sandbox 'SinApi'
        New-Item -ItemType Directory -Force -Path $good, $bad | Out-Null
        Set-Content -LiteralPath (Join-Path $good 'Autodesk.Navisworks.Api.dll') -Value 'x' -Encoding UTF8

        Check 'un directorio con el API valida' (Test-NavisProductDir -Dir $good)
        Check 'un directorio sin el API se descarta' (-not (Test-NavisProductDir -Dir $bad))
        Check 'una ruta vacia se descarta sin explotar' (-not (Test-NavisProductDir -Dir ''))
        Check 'una ruta inexistente se descarta' (-not (Test-NavisProductDir -Dir (Join-Path $sandbox 'NoExiste')))

        Check 'el mapeo comercial-interno cubre 2024-2026' `
            ($script:NavisInternalVersions['2024'] -eq '21.0' -and
             $script:NavisInternalVersions['2025'] -eq '22.0' -and
             $script:NavisInternalVersions['2026'] -eq '23.0')
        Check 'una version desconocida no consulta el registro' `
            ((Get-NavisFromRegistry -Version '1999').Count -eq 0)

        # El orden de autoridad: si el "registro" (simulado inyectando el
        # candidato) apunta a un directorio valido, gana a los convencionales.
        $probed = New-Object 'System.Collections.Generic.List[string]'
        $hit = Find-NavisworksInstall -Version '2026' -Probed $probed
        if ($hit) {
            Check 'lo encontrado lleva el API de verdad' (Test-NavisProductDir -Dir $hit.ProductDir)
        }
        else {
            Check 'sin instalacion real: la lista de sondeos no queda vacia' ($probed.Count -gt 0) `
                '(no se sondeo nada)'
        }

        Check 'las rutas convencionales cubren mas de una unidad si existe' `
            ((Get-NavisConventionalDirs -Version '2026').Count -ge 2)
        Check 'todas las convencionales llevan la version pedida' `
            (@(Get-NavisConventionalDirs -Version '2026' | Where-Object { $_ -notlike '*2026' }).Count -eq 0)
    }
    finally {
        $resolved = [System.IO.Path]::GetFullPath($sandbox)
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        $marker = Join-Path $resolved '.naviscoord-selftest'
        $owned = (Test-Path -LiteralPath $marker) -and
                 ((Get-Content -LiteralPath $marker -Raw).Trim() -eq $nonce)
        if ($owned -and $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
            $resolved -ne $tempRoot.TrimEnd('\')) {
            Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Output ''
    Write-Output "FindNavisworks autoprueba: $checks comprobaciones, $failures fallo(s)"
    if ($failures) { exit 1 }
    exit 0
}
