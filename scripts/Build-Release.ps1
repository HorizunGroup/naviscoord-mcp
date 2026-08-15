<#
.SYNOPSIS
    Compila el add-in NavisCoord por versión de Navisworks y empaqueta un ZIP
    reproducible por versión.

.DESCRIPTION
    Cada versión de Navisworks trae su propio ensamblado del API, así que un
    DLL no sirve para las tres: se compila una vez por versión, contra los
    binarios de esa instalación, y se verifica en disco lo que quedó en vez de
    confiar en que el build lo dejó donde dijo.

    Cada paquete lleva dentro LICENSE, NOTICE y el perfil de ejemplo. Un
    redistribuible sin su licencia es lo único que no puede permitirse, y un
    complemento sin un perfil que copiar deja al primer usuario delante de un
    error contra un archivo que no escribió.

    Dos garantías que este script sí hace cumplir, y que antes no existían:

    * **Ningún artefacto nombra la máquina que lo construyó.** Se comprueba
      con Assert-PublicArtifacts.ps1 antes de que un archivo entre en un ZIP.
    * **El mismo commit produce los mismos bytes desde cualquier carpeta.** El
      ZIP se arma con marcas de tiempo derivadas del commit y en orden fijo,
      no con Compress-Archive, que graba la hora de modificación de cada
      archivo — distinta en cada clon recién hecho.

    El script no publica nada: deja artefactos en dist\ y los describe. Subirlos
    a un release es una decisión humana, y está en docs\RELEASING.md.

.PARAMETER Version
    2024, 2025, 2026 o 'all' (por defecto). Solo se procesan las versiones
    realmente instaladas.

.PARAMETER SkipBuild
    Rearma el empaquetado con lo ya compilado.

.PARAMETER NoZip
    Deja el árbol en dist\addin\<versión>\ sin comprimirlo.

.PARAMETER VerifyReproducible
    Construye DOS veces en carpetas temporales distintas a partir de este mismo
    repositorio y compara los hashes. Es la prueba de que la reproducibilidad
    es una propiedad del build y no del hecho de haberlo corrido dos veces en
    el mismo sitio, que es lo que un rebuild incremental demuestra (nada).

.EXAMPLE
    .\Build-Release.ps1
    .\Build-Release.ps1 -Version 2026
    .\Build-Release.ps1 -VerifyReproducible
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('2024', '2025', '2026', 'all')]
    [string]$Version = 'all',
    [switch]$SkipBuild,
    [switch]$NoZip,
    [switch]$VerifyReproducible
)

$ErrorActionPreference = 'Stop'

$repoRoot  = Split-Path -Parent $PSScriptRoot
$distRoot  = Join-Path $repoRoot 'dist\addin'
$project   = @{
    Name = 'NavisCoord'
    Path = Join-Path $repoRoot 'addin\NavisCoord.Addin\NavisCoord.Addin.csproj'
    Dll  = 'NavisCoord.dll'
}
$legal        = @('LICENSE', 'NOTICE')
$profileSrc   = Join-Path $repoRoot 'profiles\example-profile.json'
$profileName  = 'example-profile.json'
$guard        = Join-Path $PSScriptRoot 'Assert-PublicArtifacts.ps1'

# ------------------------------------------------------- fecha reproducible

function Get-SourceDateEpoch {
    <#
      .SYNOPSIS
        Segundos UNIX que fechan el contenido del paquete.
      .DESCRIPTION
        Del commit, no del reloj: dos clones del mismo commit tienen que
        producir el mismo ZIP, y la hora a la que alguien hizo `git clone` no
        es una propiedad del software. Se respeta SOURCE_DATE_EPOCH si ya
        viene puesto, que es la convención que usan las distribuciones.

        Sin repositorio git (un tarball descargado) cae a una constante fija,
        porque «no sé la fecha» tiene que seguir siendo determinista.
    #>
    if ($env:SOURCE_DATE_EPOCH) { return [long]$env:SOURCE_DATE_EPOCH }
    try {
        $ct = & git -C $repoRoot log -1 --format=%ct 2>$null
        if ($LASTEXITCODE -eq 0 -and $ct) { return [long]$ct }
    } catch { }
    return 315532800L   # 1980-01-01Z, el mínimo que el formato ZIP admite
}

function New-DeterministicZip {
    <#
      .SYNOPSIS
        Escribe un ZIP cuyos bytes dependen solo del contenido, no del disco.
      .DESCRIPTION
        Compress-Archive graba la marca de modificación de cada archivo y los
        recorre en el orden que devuelve el sistema de archivos. Un clon nuevo
        estampa la hora del checkout en todos, así que el mismo commit daba un
        ZIP distinto en cada máquina y hasta en cada clon.

        Aquí se fija todo lo que el formato deja fijar: orden ordinal por
        nombre, fecha única derivada del commit, nivel de compresión, nombres
        relativos con barra normal y atributos externos constantes.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][long]$Epoch
    )

    Add-Type -AssemblyName System.IO.Compression -ErrorAction SilentlyContinue
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue

    if (Test-Path $Destination) { [System.IO.File]::Delete($Destination) }

    # DOS guarda la hora en pasos de 2 s y no admite nada anterior a 1980.
    $stamp = [System.DateTimeOffset]::FromUnixTimeSeconds($Epoch).UtcDateTime
    if ($stamp.Year -lt 1980) { $stamp = [datetime]::new(1980, 1, 1, 0, 0, 0, [System.DateTimeKind]::Utc) }
    $stamp = [datetime]::new($stamp.Year, $stamp.Month, $stamp.Day,
                             $stamp.Hour, $stamp.Minute, [int]([math]::Floor($stamp.Second / 2) * 2),
                             [System.DateTimeKind]::Utc)

    $files = Get-ChildItem -Path $SourceDir -Recurse -File |
        Sort-Object { $_.FullName.Substring($SourceDir.Length).TrimStart('\', '/').Replace('\', '/') } -CaseSensitive

    $stream = [System.IO.File]::Open($Destination, [System.IO.FileMode]::CreateNew)
    try {
        $zip = [System.IO.Compression.ZipArchive]::new(
            $stream, [System.IO.Compression.ZipArchiveMode]::Create, $true)
        try {
            foreach ($f in $files) {
                $name = $f.FullName.Substring($SourceDir.Length).TrimStart('\', '/').Replace('\', '/')
                $entry = $zip.CreateEntry($name, [System.IO.Compression.CompressionLevel]::Optimal)
                $entry.LastWriteTime = [System.DateTimeOffset]::new($stamp, [timespan]::Zero)
                # 0644 en el byte alto (Unix) + FILE_ATTRIBUTE_NORMAL: constante,
                # para que el ZIP no herede los permisos del disco que lo armó.
                $entry.ExternalAttributes = (0x81A4 -shl 16) -bor 0x80
                $dst = $entry.Open()
                try {
                    $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
                    $dst.Write($bytes, 0, $bytes.Length)
                } finally { $dst.Dispose() }
            }
        } finally { $zip.Dispose() }
    } finally { $stream.Dispose() }
}

# ------------------------------------------------ modo verificar reproducible

if ($VerifyReproducible) {
    $head = (& git -C $repoRoot rev-parse HEAD).Trim()
    Write-Host "Verificando reproducibilidad de $head en dos rutas distintas."

    # Dos rutas deliberadamente desiguales en longitud y forma: la fuga que
    # esto persigue era precisamente una ruta absoluta incrustada, y dos
    # carpetas hermanas del mismo largo podrían ocultarla por casualidad.
    $a = Join-Path ([System.IO.Path]::GetTempPath()) 'nvc-repro-a'
    $b = Join-Path ([System.IO.Path]::GetTempPath()) 'nvc-repro-b-una-ruta-bastante-mas-larga\anidada'

    $results = @()
    foreach ($pair in @(@{ Path = $a; Tag = 'A' }, @{ Path = $b; Tag = 'B' })) {
        $dir = $pair.Path
        if (Test-Path $dir) { Remove-Item -Recurse -Force -LiteralPath $dir }
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        & git clone --quiet --no-checkout $repoRoot $dir 2>&1 | Out-Null
        & git -C $dir checkout --quiet --detach $head 2>&1 | Out-Null
        Write-Host "[$($pair.Tag)] construyendo en $dir"
        # El mismo host que corre esto, no «powershell»: en una máquina con
        # PowerShell 7 esa palabra invoca al 5.1 del sistema, y comparar dos
        # builds hechos por intérpretes distintos no demuestra nada sobre el
        # build.
        $host_exe = (Get-Process -Id $PID).Path
        & $host_exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'scripts\Build-Release.ps1') -Version $Version 2>&1 |
            Select-Object -Last 3 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "[$($pair.Tag)] el build falló en $dir" }
        $results += [pscustomobject]@{ Tag = $pair.Tag; Root = $dir }
    }

    Write-Host ''
    $rows = @()
    $rootA = (Join-Path $results[0].Root 'dist\addin')
    $rootB = (Join-Path $results[1].Root 'dist\addin')
    foreach ($item in (Get-ChildItem $rootA -File | Sort-Object Name)) {
        $other = Join-Path $rootB $item.Name
        $ha = (Get-FileHash $item.FullName -Algorithm SHA256).Hash
        $hb = if (Test-Path $other) { (Get-FileHash $other -Algorithm SHA256).Hash } else { '(ausente)' }
        $rows += [pscustomobject]@{ Artefacto = $item.Name; A = $ha.Substring(0, 16); B = $hb.Substring(0, 16); Identico = ($ha -eq $hb) }
    }
    foreach ($v in @('2024', '2025', '2026')) {
        $da = Join-Path $rootA "$v\NavisCoord.dll"; $db = Join-Path $rootB "$v\NavisCoord.dll"
        if ((Test-Path $da) -and (Test-Path $db)) {
            $ha = (Get-FileHash $da -Algorithm SHA256).Hash; $hb = (Get-FileHash $db -Algorithm SHA256).Hash
            $rows += [pscustomobject]@{ Artefacto = "NW$v/NavisCoord.dll"; A = $ha.Substring(0, 16); B = $hb.Substring(0, 16); Identico = ($ha -eq $hb) }
        }
    }
    $rows | Sort-Object Artefacto | Format-Table -AutoSize | Out-String | Write-Host

    $bad = @($rows | Where-Object { -not $_.Identico })
    if ($bad) {
        Write-Error "No reproducible: $($bad.Count) artefacto(s) difieren entre las dos rutas."
        exit 1
    }
    Write-Host "Reproducible: $($rows.Count) artefacto(s) idénticos entre dos clones en rutas distintas."
    exit 0
}

# --------------------------------------------------------------- detección

$wanted = if ($Version -eq 'all') { @('2024', '2025', '2026') } else { @($Version) }
$found = @()
foreach ($v in $wanted) {
    $productDir = "C:\Program Files\Autodesk\Navisworks Manage $v"
    if (Test-Path (Join-Path $productDir 'Autodesk.Navisworks.Api.dll')) {
        $found += [pscustomobject]@{ Version = $v; ProductDir = $productDir }
    }
    elseif ($Version -ne 'all') {
        throw "No encuentro Navisworks Manage $v en $productDir."
    }
}
if (-not $found) {
    throw "No encuentro ninguna instalacion de Navisworks Manage 2024-2026. El add-in se compila contra el API de cada version instalada."
}

foreach ($name in $legal) {
    if (-not (Test-Path (Join-Path $repoRoot $name))) {
        throw "Falta $name en la raiz del repositorio; ningun paquete debe salir sin su licencia."
    }
}
if (-not (Test-Path $profileSrc)) {
    throw "Falta $profileSrc; el paquete debe llevar un perfil de ejemplo que el usuario pueda copiar."
}
if (-not (Test-Path $guard)) {
    throw "Falta $guard; ningun binario se empaqueta sin pasar la comprobacion de rutas."
}

# La version declarada en el csproj es la que va en el nombre del ZIP, para que
# un artefacto suelto siga diciendo de que release salio.
$csproj = [xml](Get-Content $project.Path)
$declaredVersion = ($csproj.Project.PropertyGroup.Version | Where-Object { $_ }) | Select-Object -First 1
if (-not $declaredVersion) { throw "El csproj no declara <Version>." }

$epoch = Get-SourceDateEpoch

Write-Host "NavisCoord $declaredVersion"
Write-Host "Versiones de Navisworks detectadas: $(($found | ForEach-Object { $_.Version }) -join ', ')"
Write-Host "Fecha de empaquetado (SOURCE_DATE_EPOCH): $epoch  [$([System.DateTimeOffset]::FromUnixTimeSeconds($epoch).UtcDateTime.ToString('u'))]"

# -------------------------------------------------------------- empaquetado

$report = @()
foreach ($f in $found) {
    $v       = $f.Version
    $outDir  = Join-Path $repoRoot "addin\_build\NW$v\$($project.Name)"
    $packDir = Join-Path $distRoot $v

    if (-not $SkipBuild) {
        if ($PSCmdlet.ShouldProcess("$($project.Name) NW$v", 'dotnet build')) {
            Write-Host "[$v] compilando contra $($f.ProductDir) ..."
            # Por variable de entorno y no por -p:. Un valor pasado con -p: es
            # una propiedad GLOBAL de MSBuild y el proyecto no puede
            # normalizarla; el csproj deriva $(NavisworksRefDir) precisamente
            # para que ambas vias funcionen, pero la variable es la que la
            # ruta con espacios ("Program Files") atraviesa sin sorpresas.
            $env:NavisworksDir = $f.ProductDir
            & dotnet build $project.Path -c Release -v quiet --nologo -o $outDir
            if ($LASTEXITCODE -ne 0) { throw "[$v] fallo la compilacion." }
        }
    }

    if (-not $PSCmdlet.ShouldProcess("$packDir", 'empaquetado')) { continue }

    $builtDll = Join-Path $outDir $project.Dll
    if (-not (Test-Path $builtDll)) { throw "[$v] no se genero $builtDll" }

    # La comprobacion va ANTES de copiar: un binario con la ruta de la maquina
    # dentro no debe llegar siquiera a la carpeta de empaquetado.
    & $guard -Path $builtDll
    if ($LASTEXITCODE -ne 0) { throw "[$v] el binario compilado no paso la comprobacion de rutas." }

    # Se borra SOLO lo que este script produce, y ARCHIVO A ARCHIVO.
    # [IO.File]::Delete opera sobre un archivo y nada mas: aunque $packDir se
    # calculara mal, no existe forma de que esto arrase un arbol de
    # directorios. Y nunca se toca un naviscoord-profile.json que el usuario
    # haya dejado aqui: un perfil afinado es trabajo de un coordinador y este
    # script no sabe reconstruirlo.
    New-Item -ItemType Directory -Force -Path $packDir | Out-Null
    $owned = @($project.Dll, '*.pdb', $profileName) + $legal
    foreach ($pattern in $owned) {
        Get-ChildItem -Path $packDir -Filter $pattern -File -ErrorAction SilentlyContinue |
            ForEach-Object { [System.IO.File]::Delete($_.FullName) }
    }

    Copy-Item $builtDll -Destination $packDir -Force
    foreach ($name in $legal) {
        Copy-Item (Join-Path $repoRoot $name) -Destination $packDir -Force
    }
    Copy-Item $profileSrc -Destination $packDir -Force

    # Verificacion: comprobar lo que quedo en disco, no asumir que las copias
    # funcionaron.
    $packedDll = Join-Path $packDir $project.Dll
    if (-not (Test-Path $packedDll)) { throw "[$v] la copia no dejo $($project.Dll) en $packDir" }
    if ((Get-Item $builtDll).Length -ne (Get-Item $packedDll).Length) {
        throw "[$v] el DLL empaquetado no coincide en tamano con el compilado; la copia quedo incompleta."
    }
    foreach ($name in ($legal + $profileName)) {
        if (-not (Test-Path (Join-Path $packDir $name))) { throw "[$v] falta $name en $packDir" }
    }

    # Nada privado puede viajar en el paquete. Un .pdb ya NO es tolerado: el
    # build de Release no emite simbolos, asi que uno aqui solo puede venir de
    # una copia manual o de un build de Debug mal dirigido.
    $allowed = @($project.Dll, $profileName) + $legal
    $stray = Get-ChildItem $packDir -Recurse -File | Where-Object { $_.Name -notin $allowed }
    if ($stray) {
        throw "[$v] el paquete lleva archivos inesperados: $(($stray | ForEach-Object { $_.Name }) -join ', ')"
    }

    # Y la comprobacion de rutas se repite sobre la carpeta ya armada, que es
    # exactamente lo que se va a comprimir.
    & $guard -Path $packDir
    if ($LASTEXITCODE -ne 0) { throw "[$v] el paquete no paso la comprobacion de rutas." }

    $zipPath = Join-Path $distRoot "NavisCoord-$declaredVersion-addin-NW$v.zip"
    if (-not $NoZip) {
        New-DeterministicZip -SourceDir $packDir -Destination $zipPath -Epoch $epoch
        if (-not (Test-Path $zipPath)) { throw "[$v] no se genero $zipPath" }
    }

    $report += [pscustomobject]@{
        NW      = $v
        Dll     = '{0:N0} B' -f (Get-Item $packedDll).Length
        Files   = (Get-ChildItem $packDir -Recurse -File).Count
        Zip     = if ($NoZip) { '(omitido)' } else { Split-Path -Leaf $zipPath }
        Sha256  = if ($NoZip) { '' } else { (Get-FileHash $zipPath -Algorithm SHA256).Hash.Substring(0, 16) + '...' }
    }
}

if ($report) {
    Write-Host ''
    $report | Format-Table -AutoSize
    Write-Host "Artefactos en $distRoot"
    Write-Host 'Este script NO publica nada. Ver docs\RELEASING.md para el resto.'
}
