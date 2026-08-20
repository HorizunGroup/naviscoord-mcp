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

function Get-PluginDir([string]$v) {
    Join-Path $env:APPDATA "Autodesk\Navisworks Manage $v\Plugins\NavisCoord"
}

function Get-NavisworksProductDir([string]$v) {
    $candidates = @()
    foreach ($base in @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($base) { $candidates += Join-Path $base "Autodesk\Navisworks Manage $v" }
    }
    foreach ($hive in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )) {
        Get-ItemProperty $hive -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -like "Autodesk Navisworks Manage $v*" } |
            ForEach-Object { if ($_.InstallLocation) { $candidates += $_.InstallLocation } }
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Path (Join-Path $candidate 'Autodesk.Navisworks.Api.dll')) {
            return (Resolve-Path $candidate).Path
        }
    }
    return $null
}

function Install-FileAtomic([string]$Source, [string]$Destination) {
    $parent = Split-Path $Destination -Parent
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temp = Join-Path $parent ('.' + [IO.Path]::GetFileName($Destination) + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
    $backup = Join-Path $parent ('.' + [IO.Path]::GetFileName($Destination) + '.' + [guid]::NewGuid().ToString('N') + '.bak')
    try {
        Copy-Item -LiteralPath $Source -Destination $temp
        $expected = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
        $staged = (Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash
        if ($expected -ne $staged) { throw "el archivo temporal no coincide con el origen" }

        if (Test-Path -LiteralPath $Destination) {
            [IO.File]::Replace($temp, $Destination, $backup, $true)
        } else {
            [IO.File]::Move($temp, $Destination)
        }
        $installed = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
        if ($installed -ne $expected) { throw "el archivo publicado no coincide con el origen" }
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
    } catch {
        if (Test-Path -LiteralPath $backup) {
            if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force }
            [IO.File]::Move($backup, $Destination)
        }
        throw
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
    }
}

if (Get-Process -Name "Roamer" -ErrorAction SilentlyContinue) {
    throw "Navisworks está abierto. Ciérralo antes de instalar: el DLL queda bloqueado y la copia fallaría a medias."
}

# Desinstalar depende de dónde vive el plugin, no de que el producto siga en
# C:\Program Files. Esto también limpia instalaciones huérfanas después de
# mover o desinstalar Navisworks.
if ($Uninstall) {
    foreach ($v in $supported) {
        $pluginDir = Get-PluginDir $v
        if (Test-Path -LiteralPath $pluginDir) {
            Remove-Item -LiteralPath $pluginDir -Recurse -Force
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
    $productDir = Get-NavisworksProductDir $v
    if ($productDir) {
        $found += [pscustomobject]@{ Version = $v; ProductDir = $productDir }
    } elseif ($Version -ne 'all') {
        throw "No encuentro Navisworks Manage $v ni por registro ni bajo Program Files."
    }
}
if (-not $found) {
    throw "No encuentro ninguna instalación de Navisworks Manage 2024-2026."
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

    $pluginDir = Get-PluginDir $v
    $installed = Join-Path $pluginDir "NavisCoord.dll"
    Install-FileAtomic $built $installed

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
    if (-not (Test-Path $installed)) { throw "[$v] la copia no dejó el DLL en $installed" }
    if ((Get-FileHash $built -Algorithm SHA256).Hash -ne (Get-FileHash $installed -Algorithm SHA256).Hash) {
        throw "[$v] el DLL instalado no coincide en SHA-256 con el compilado."
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
