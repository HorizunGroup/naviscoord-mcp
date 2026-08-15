<#
.SYNOPSIS
    Compila e instala el complemento NavisCoord en Navisworks Manage.

.DESCRIPTION
    Soporta Navisworks Manage 2024 a 2026. Cada versión de Navisworks trae su
    propia versión del API (.NET), así que el complemento se compila UNA VEZ
    POR VERSIÓN, contra los DLL de esa instalación, y se instala en la carpeta
    de plugins de esa versión. Sin argumentos detecta todas las instalaciones
    y las cubre; -Version limita a una.

    El código usa solo miembros del API presentes desde la v21 (2024) — los
    dos miembros que solo existen desde v22 se evitaron a propósito — de modo
    que la misma fuente compila contra 2024, 2025 y 2026.

    Navisworks debe estar CERRADO: con el proceso abierto el DLL está
    bloqueado y la copia falla a medias, que es peor que no copiar.

.EXAMPLE
    .\install.ps1              # todas las versiones instaladas (2024-2026)
    .\install.ps1 -Version 2025
    .\install.ps1 -Uninstall   # desinstala de todas
#>
[CmdletBinding()]
param(
    [ValidateSet('2024','2025','2026','all')]
    [string]$Version = 'all',
    [switch]$Uninstall,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$supported = if ($Version -eq 'all') { @('2024','2025','2026') } else { @($Version) }
$projectDir = Join-Path $PSScriptRoot "addin\NavisCoord.Addin"

if (Get-Process -Name "Roamer" -ErrorAction SilentlyContinue) {
    throw "Navisworks está abierto. Ciérralo antes de instalar: el DLL queda bloqueado y la copia fallaría a medias."
}

$found = @()
foreach ($v in $supported) {
    $productDir = "C:\Program Files\Autodesk\Navisworks Manage $v"
    if (Test-Path (Join-Path $productDir 'Autodesk.Navisworks.Api.dll')) {
        $found += [pscustomobject]@{ Version = $v; ProductDir = $productDir }
    } elseif ($Version -ne 'all') {
        throw "No encuentro Navisworks Manage $v en $productDir."
    }
}
if (-not $found) {
    throw "No encuentro ninguna instalación de Navisworks Manage 2024-2026 en C:\Program Files\Autodesk."
}

if ($Uninstall) {
    foreach ($f in $found) {
        $pluginDir = Join-Path $env:APPDATA "Autodesk\Navisworks Manage $($f.Version)\Plugins\NavisCoord"
        if (Test-Path $pluginDir) {
            Remove-Item $pluginDir -Recurse -Force
            Write-Host "[$($f.Version)] complemento eliminado"
        } else {
            Write-Host "[$($f.Version)] no había nada instalado"
        }
    }
    $session = Join-Path $env:LOCALAPPDATA "NavisCoord\session.json"
    if (Test-Path $session) { Remove-Item $session -Force }
    $sessions = Join-Path $env:LOCALAPPDATA "NavisCoord\sessions"
    if (Test-Path $sessions) { Remove-Item $sessions -Recurse -Force }
    return
}

foreach ($f in $found) {
    $v = $f.Version
    # Un directorio de salida por versión: el DLL compilado contra el API v21
    # y el compilado contra v23 son binarios distintos, y compartir bin\ haría
    # que el último build pisara al anterior justo antes de copiarlo.
    $outDir = Join-Path $projectDir "bin\Release\NW$v"

    if (-not $SkipBuild) {
        Write-Host "[$v] compilando contra $($f.ProductDir) ..."
        # Por variable de entorno y no por -p:. La ruta contiene espacios
        # ("Program Files") y en la línea de comandos MSBuild la parte en dos.
        $env:NavisworksDir = $f.ProductDir
        & dotnet build $projectDir -c Release -v quiet --nologo -o $outDir
        if ($LASTEXITCODE -ne 0) { throw "[$v] la compilación falló." }
    }

    $built = Join-Path $outDir "NavisCoord.dll"
    if (-not (Test-Path $built)) { throw "[$v] no se generó $built" }

    $pluginDir = Join-Path $env:APPDATA "Autodesk\Navisworks Manage $v\Plugins\NavisCoord"
    New-Item -ItemType Directory -Force -Path $pluginDir | Out-Null
    Copy-Item $built -Destination $pluginDir -Force
    $pdb = [IO.Path]::ChangeExtension($built, ".pdb")
    if (Test-Path $pdb) { Copy-Item $pdb -Destination $pluginDir -Force }

    # El layout de la cinta, a la raíz y a la carpeta del idioma. Sin él el DLL
    # carga igual y la pestaña simplemente no sale, que es el fallo más caro de
    # diagnosticar de todo este complemento: no hay error en ninguna parte.
    $layout = Join-Path $outDir "NavisCoordRibbon.xaml"
    if (-not (Test-Path $layout)) { throw "[$v] el build no dejó NavisCoordRibbon.xaml en $outDir" }
    New-Item -ItemType Directory -Force -Path (Join-Path $pluginDir "en-US") | Out-Null
    Copy-Item $layout -Destination $pluginDir -Force
    Copy-Item $layout -Destination (Join-Path $pluginDir "en-US") -Force

    # Los iconos de los botones. Si faltan, la pestaña sale igual pero con los
    # botones en blanco, así que también se verifican.
    #
    # Se BORRAN los que hubiera antes de copiar: copiar sin limpiar deja los
    # iconos de botones que ya no existen acumulándose en la carpeta del
    # complemento para siempre, y nadie los echa de menos porque no rompen
    # nada — solo confunden a quien mire ahí buscando qué se instaló.
    Get-ChildItem $pluginDir -Filter "nc*.png" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force
    $icons = Get-ChildItem $outDir -Filter "nc*.png" -File
    if (-not $icons) { throw "[$v] el build no dejó los iconos (nc*.png) en $outDir" }
    Copy-Item $icons.FullName -Destination $pluginDir -Force

    # Verificación: comprobar lo copiado en disco, no asumir que Copy-Item
    # funcionó.
    $installed = Join-Path $pluginDir "NavisCoord.dll"
    if (-not (Test-Path $installed)) { throw "[$v] la copia no dejó el DLL en $installed" }
    if ((Get-Item $built).Length -ne (Get-Item $installed).Length) {
        throw "[$v] el DLL instalado no coincide en tamaño con el compilado; la copia quedó incompleta."
    }
    foreach ($x in @((Join-Path $pluginDir "NavisCoordRibbon.xaml"),
                     (Join-Path $pluginDir "en-US\NavisCoordRibbon.xaml"))) {
        if (-not (Test-Path $x)) { throw "[$v] falta el layout de la cinta en $x" }
    }
    Write-Host "[$v] instalado: $installed (+ cinta)"
}

Write-Host ""
Write-Host ("Listo para: " + (($found | ForEach-Object { $_.Version }) -join ', '))
Write-Host "Abre Navisworks: verás la pestaña NavisCoord. El puente arranca solo;"
Write-Host "'Estado del puente' dice la versión cargada y si está corriendo."
Write-Host "Si abres varias instancias, cada puente publica su propia sesión; elige una con navis_target."
