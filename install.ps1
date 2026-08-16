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

# Desinstalar va ANTES de buscar Navisworks, y recorre las versiones pedidas
# en vez de las encontradas: el complemento vive en %APPDATA%, no dentro del
# producto, así que sigue ahí cuando alguien ya desinstaló Navisworks — y ese
# es precisamente el momento en que querría limpiarlo. Exigir una instalación
# para poder borrar dejaba la carpeta huérfana sin forma de quitarla.
if ($Uninstall) {
    foreach ($v in $supported) {
        $pluginDir = Join-Path $env:APPDATA "Autodesk\Navisworks Manage $v\Plugins\NavisCoord"
        if (Test-Path $pluginDir) {
            Remove-Item $pluginDir -Recurse -Force
            Write-Host "[$v] complemento eliminado"
        } else {
            Write-Host "[$v] no había nada instalado"
        }
    }

    # El registro de sesiones es de TODO el producto, no de una versión. Con
    # -Version 2024 quedan complementos instalados en 2025/2026, y borrarlo
    # les quita el handshake a instancias que siguen siendo válidas; solo se
    # limpia cuando se desinstala todo.
    if ($Version -eq 'all') {
        $session = Join-Path $env:LOCALAPPDATA "NavisCoord\session.json"
        if (Test-Path $session) { Remove-Item $session -Force }
        $sessions = Join-Path $env:LOCALAPPDATA "NavisCoord\sessions"
        if (Test-Path $sessions) { Remove-Item $sessions -Recurse -Force }
    } else {
        Write-Host "El registro de sesiones se conserva: hay otras versiones que pueden usarlo."
        Write-Host "Para borrarlo todo: .\install.ps1 -Uninstall"
    }
    return
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
        #
        # Se restaura al salir: con -Version all esto se reasigna una vez por
        # versión, y dejarlo puesto al terminar hace que el siguiente build
        # que alguien lance a mano en la misma consola compile contra la
        # última versión instalada sin haberlo pedido.
        $previousNavisworksDir = $env:NavisworksDir
        try {
            $env:NavisworksDir = $f.ProductDir
            & dotnet build $projectDir -c Release -v quiet --nologo -o $outDir
            if ($LASTEXITCODE -ne 0) { throw "[$v] la compilación falló." }
        } finally {
            $env:NavisworksDir = $previousNavisworksDir
        }
    }

    # TODO lo que hay que copiar se comprueba ANTES de copiar nada.
    #
    # Antes el DLL se copiaba primero y la cinta se verificaba después, así que
    # un build sin XAML dejaba el complemento a medias: DLL cargado, pestaña
    # ausente y ningún error visible dentro de Navisworks. Es justo el estado
    # que este script existe para no producir, y el más caro de diagnosticar
    # de todo el complemento, porque no hay error en ninguna parte.
    $built  = Join-Path $outDir "NavisCoord.dll"
    $layout = Join-Path $outDir "NavisCoordRibbon.xaml"
    if (-not (Test-Path $built))  { throw "[$v] no se generó $built" }
    if (-not (Test-Path $layout)) { throw "[$v] el build no dejó NavisCoordRibbon.xaml en $outDir" }
    # Los iconos de los botones. Si faltan, la pestaña sale igual pero con los
    # botones en blanco.
    $icons = Get-ChildItem $outDir -Filter "nc*.png" -File
    if (-not $icons) { throw "[$v] el build no dejó los iconos (nc*.png) en $outDir" }

    $pluginDir = Join-Path $env:APPDATA "Autodesk\Navisworks Manage $v\Plugins\NavisCoord"
    New-Item -ItemType Directory -Force -Path $pluginDir | Out-Null
    Copy-Item $built -Destination $pluginDir -Force
    $pdb = [IO.Path]::ChangeExtension($built, ".pdb")
    if (Test-Path $pdb) { Copy-Item $pdb -Destination $pluginDir -Force }

    # El layout de la cinta, a la raíz y a la carpeta del idioma: el cargador
    # busca primero en la subcarpeta del idioma.
    New-Item -ItemType Directory -Force -Path (Join-Path $pluginDir "en-US") | Out-Null
    Copy-Item $layout -Destination $pluginDir -Force
    Copy-Item $layout -Destination (Join-Path $pluginDir "en-US") -Force

    # Se BORRAN los iconos que hubiera antes de copiar: copiar sin limpiar deja
    # los de botones que ya no existen acumulándose en la carpeta del
    # complemento para siempre, y nadie los echa de menos porque no rompen
    # nada — solo confunden a quien mire ahí buscando qué se instaló.
    Get-ChildItem $pluginDir -Filter "nc*.png" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force
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
