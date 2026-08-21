<#
.SYNOPSIS
    Resolucion segura de rutas para la restauracion del smoke test.

.DESCRIPTION
    El manifiesto de un backup es un CSV en %TEMP%. Cualquier cosa que pueda
    escribir ahi decide, si se le cree, en que ruta absoluta escribe la
    restauracion. La version anterior hacia exactamente eso:

        $targetDir = Split-Path $row.Original -Parent
        Copy-Item $copy -Destination $row.Original -Force

    Una fila con Original = C:\Windows\System32\... se copiaba ahi sin que
    nada lo mirara. Ese CSV es un dato, no una autoridad.

    Asi que la ruta de destino ya no se lee: se DERIVA. La autoridad es la
    version (columna Version) pasada por Get-PluginsDir, que es codigo de este
    repositorio, mas la ruta RELATIVA, que se valida antes de tocar el disco.
    Original queda solo como comprobacion cruzada: si no coincide con lo
    derivado se reporta, pero nunca manda.

    Las funciones son puras respecto del arbol real: reciben la raiz como
    parametro, de modo que la autoprueba trabaja en un directorio temporal
    unico y jamas cerca de una instalacion de Navisworks.

.PARAMETER SelfTest
    Ejecuta las comprobaciones de este archivo contra un arbol temporal
    propio y sale. No lee ni escribe fuera de el.
#>
[CmdletBinding()]
param([switch]$SelfTest)

# --------------------------------------------------------------- primitivas

function Test-RelativeSegment {
    <#
        Motivos por los que una ruta relativa de manifiesto se rechaza. Se
        comprueban sobre el TEXTO, antes de combinar con nada: una vez
        combinada y normalizada, "..\..\Windows" ya no se distingue de una
        ruta legitima escrita por alguien.
    #>
    param([AllowEmptyString()][string]$Relative)

    if ([string]::IsNullOrWhiteSpace($Relative)) { return 'ruta relativa vacia' }
    # Bloquea C:\... y tambien flujos alternativos NTFS (archivo.dll:oculto),
    # que se escriben con el mismo separador y no aparecen en un listado.
    if ($Relative.Contains(':')) { return 'la ruta relativa contiene ":" (unidad o flujo alternativo NTFS)' }
    if ($Relative.StartsWith('\\') -or $Relative.StartsWith('//')) { return 'ruta UNC' }
    if ($Relative.StartsWith('\') -or $Relative.StartsWith('/')) { return 'ruta enraizada' }
    if ([System.IO.Path]::IsPathRooted($Relative)) { return 'ruta enraizada' }
    foreach ($segment in ($Relative -split '[\\/]+')) {
        if ($segment -eq '..') { return 'la ruta relativa sube de nivel ("..")' }
    }
    # Un caracter invalido convertiria GetFullPath en excepcion mas abajo; se
    # nombra aqui para que el diagnostico diga cual es el problema.
    foreach ($bad in [System.IO.Path]::GetInvalidPathChars()) {
        if ($Relative.Contains($bad)) { return 'la ruta relativa tiene caracteres invalidos' }
    }
    return ''
}

function Test-InsideRoot {
    <#
        Contencion con separador final: sin el, "...\Plugins-otro" pasaria por
        estar dentro de "...\Plugins" por simple prefijo de texto.
    #>
    param([string]$Root, [string]$Candidate)

    $normalRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $normalCandidate = [System.IO.Path]::GetFullPath($Candidate)
    return $normalCandidate.StartsWith($normalRoot, [StringComparison]::OrdinalIgnoreCase)
}

function Test-ReparsePath {
    <#
        Cualquier componente EXISTENTE del destino que sea junction o symlink.
        Basta uno: si "...\Plugins\NavisCoord" es un junction a otra unidad,
        escribir en el destino escribe fuera de la raiz aprobada, y la
        comprobacion de contencion sobre el texto no lo ve.
    #>
    param([string]$Path)

    $current = $Path
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return $current }
        }
        $parent = Split-Path -Parent $current
        if ($parent -eq $current -or [string]::IsNullOrEmpty($parent)) { break }
        $current = $parent
    }
    return ''
}

# ------------------------------------------------------------------ resolver

function Resolve-RestoreTarget {
    <#
        Devuelve Ok/Path/Reason/Mismatch para UNA fila de manifiesto.

        Root es la raiz aprobada, calculada por el llamador a partir de la
        version. Recorded es la columna Original: se compara y se reporta la
        discrepancia, pero no participa en construir la ruta.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Relative,
        [AllowEmptyString()][string]$Recorded = ''
    )

    $result = [pscustomobject]@{ Ok = $false; Path = ''; Reason = ''; Mismatch = '' }

    $why = Test-RelativeSegment -Relative $Relative
    if ($why) { $result.Reason = $why; return $result }

    try { $candidate = [System.IO.Path]::GetFullPath((Join-Path $Root $Relative)) }
    catch { $result.Reason = "no se pudo normalizar la ruta: $($_.Exception.Message)"; return $result }

    if (-not (Test-InsideRoot -Root $Root -Candidate $candidate)) {
        $result.Reason = "el destino queda fuera de la raiz aprobada ($Root)"
        return $result
    }

    $reparse = Test-ReparsePath -Path $candidate
    if ($reparse) {
        $result.Reason = "hay un punto de reanalisis (junction/symlink) en el camino: $reparse"
        return $result
    }

    # Comprobacion cruzada, no autoridad: se informa y se sigue.
    if ($Recorded) {
        try {
            $normalRecorded = [System.IO.Path]::GetFullPath($Recorded)
            if ($normalRecorded -ne $candidate) {
                $result.Mismatch = "el manifiesto decia '$Recorded' y la ruta derivada es '$candidate'"
            }
        }
        catch { $result.Mismatch = "el manifiesto traia una ruta Original ilegible: $Recorded" }
    }

    $result.Ok = $true
    $result.Path = $candidate
    return $result
}

function Get-RestoreExtraneous {
    <#
        Archivos que EXISTEN ahora bajo la raiz y que el backup no contiene.

        Es la mitad olvidada de una restauracion. Si el add-in no estaba
        instalado antes, el backup no lo lleva; devolver solo lo que el backup
        contiene dejaba el DLL instalado para siempre y el smoke test terminaba
        anunciando "restaurado". Restaurar es tambien volver a la ausencia.

        Se limita a las carpetas que Install pudo tocar (-Managed): borrar
        cualquier archivo no listado convertiria esta funcion en una limpieza
        del directorio de plugins del usuario.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [string[]]$Backed = @(),
        [string[]]$Managed = @()
    )

    if (-not (Test-Path -LiteralPath $Root)) { return ,([string[]]@()) }
    $known = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($rel in $Backed) { [void]$known.Add(($rel -replace '/', '\').TrimStart('\')) }

    $base = (Get-Item -LiteralPath $Root -Force).FullName.TrimEnd('\')
    $extra = @()
    foreach ($file in (Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue)) {
        $rel = $file.FullName.Substring($base.Length).TrimStart('\')
        if ($known.Contains($rel)) { continue }
        $top = ($rel -split '\\')[0]
        if ($Managed.Count -and ($Managed -notcontains $top)) { continue }
        $extra += $file.FullName
    }
    # La coma importa: sin ella PowerShell desenvuelve un array de un solo
    # elemento y el llamador termina indexando caracteres de un string.
    return ,([string[]]$extra)
}

# ----------------------------------------------------------------- autoprueba

if ($SelfTest) {
    $failures = 0
    $checks = 0
    function Check([string]$name, [bool]$ok, [string]$detail = '') {
        $script:checks++
        if ($ok) { Write-Output "  ok    $name" }
        else { $script:failures++; Write-Output "  FALLA $name $detail" }
    }

    # Raiz temporal UNICA por ejecucion, con marcador de propiedad. Se borra
    # solo despues de confirmar las dos cosas.
    $nonce = [guid]::NewGuid().ToString('N').Substring(0, 12)
    $sandbox = Join-Path ([System.IO.Path]::GetTempPath()) "naviscoord-restoretest-$nonce"
    New-Item -ItemType Directory -Force -Path $sandbox | Out-Null
    Set-Content -LiteralPath (Join-Path $sandbox '.naviscoord-selftest') -Value $nonce -Encoding UTF8

    try {
        $root = Join-Path $sandbox 'Plugins'
        New-Item -ItemType Directory -Force -Path (Join-Path $root 'NavisCoord') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $root 'OtroPlugin') | Out-Null
        Set-Content -LiteralPath (Join-Path $root 'NavisCoord\NavisCoord.dll') -Value 'x' -Encoding UTF8
        Set-Content -LiteralPath (Join-Path $root 'OtroPlugin\Ajeno.dll') -Value 'y' -Encoding UTF8

        $good = Resolve-RestoreTarget -Root $root -Relative 'NavisCoord\NavisCoord.dll'
        Check 'una ruta relativa normal se acepta' $good.Ok $good.Reason
        Check 'y resuelve dentro de la raiz' ($good.Path -eq (Join-Path $root 'NavisCoord\NavisCoord.dll'))

        foreach ($evil in @(
            'C:\Windows\System32\drivers\etc\hosts',
            '..\..\..\Windows\System32\x.dll',
            'NavisCoord\..\..\fuera.dll',
            '\\servidor\share\x.dll',
            '\Windows\x.dll',
            'NavisCoord\NavisCoord.dll:oculto',
            ''
        )) {
            $verdict = Resolve-RestoreTarget -Root $root -Relative $evil
            Check "se rechaza '$evil'" (-not $verdict.Ok) '(fue aceptada)'
        }

        # Original manipulado: la ruta derivada manda y la discrepancia se ve.
        $crossed = Resolve-RestoreTarget -Root $root -Relative 'NavisCoord\NavisCoord.dll' `
            -Recorded 'C:\Windows\System32\NavisCoord.dll'
        Check 'un Original manipulado no cambia el destino' `
            ($crossed.Ok -and $crossed.Path -eq (Join-Path $root 'NavisCoord\NavisCoord.dll'))
        Check 'y la discrepancia queda reportada' ([bool]$crossed.Mismatch)

        # Ausencia previa: el DLL de un plugin que el backup no contenia sale
        # como sobrante, y lo ajeno NO.
        $extra = Get-RestoreExtraneous -Root $root -Backed @('OtroPlugin\Ajeno.dll') -Managed @('NavisCoord')
        Check 'un archivo instalado que el backup no tenia se reporta sobrante' `
            ($extra.Count -eq 1 -and $extra[0].EndsWith('NavisCoord.dll'))

        $extraAjeno = Get-RestoreExtraneous -Root $root -Backed @() -Managed @('NavisCoord')
        Check 'un plugin de terceros nunca se reporta sobrante' `
            (@($extraAjeno | Where-Object { $_ -like '*Ajeno.dll' }).Count -eq 0)

        $nothing = Get-RestoreExtraneous -Root $root `
            -Backed @('NavisCoord\NavisCoord.dll', 'OtroPlugin\Ajeno.dll') -Managed @('NavisCoord')
        Check 'un arbol identico al backup no deja sobrantes' ($nothing.Count -eq 0)

        # Junction: el destino resuelve dentro de la raiz por texto y aun asi
        # apunta fuera. Solo se comprueba si el entorno permite crearlo.
        $outside = Join-Path $sandbox 'Fuera'
        New-Item -ItemType Directory -Force -Path $outside | Out-Null
        $link = Join-Path $root 'Enlazado'
        $made = $false
        try {
            New-Item -ItemType Junction -Path $link -Target $outside -ErrorAction Stop | Out-Null
            $made = $true
        }
        catch { Write-Output '  nota  el entorno no permite crear junctions: caso no ejercitado' }
        if ($made) {
            $viaLink = Resolve-RestoreTarget -Root $root -Relative 'Enlazado\x.dll'
            Check 'un junction en el camino se rechaza' (-not $viaLink.Ok) '(fue aceptada)'
        }
    }
    finally {
        # Borrado recursivo solo tras confirmar ruta absoluta, contencion en
        # el temporal del sistema y el marcador que escribio ESTA ejecucion.
        $resolved = [System.IO.Path]::GetFullPath($sandbox)
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        $marker = Join-Path $resolved '.naviscoord-selftest'
        $owned = (Test-Path -LiteralPath $marker) -and
                 ((Get-Content -LiteralPath $marker -Raw).Trim() -eq $nonce)
        $inTemp = $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)
        $notLink = -not (Test-ReparsePath -Path $resolved)
        if ($owned -and $inTemp -and $notLink -and $resolved -ne $tempRoot.TrimEnd('\')) {
            Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
        }
        else {
            Write-Output "  aviso no se borro $resolved (no paso las comprobaciones de propiedad)"
        }
    }

    Write-Output ''
    Write-Output "RestoreSafety autoprueba: $checks comprobaciones, $failures fallo(s)"
    if ($failures) { exit 1 }
    exit 0
}
