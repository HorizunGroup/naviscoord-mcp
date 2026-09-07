<#
.SYNOPSIS
    Nucleo transaccional de Backup / Verify / Install / Restore.

.DESCRIPTION
    Aqui vive lo que HACE el intercambio del add-in. No sabe donde esta
    instalado Navisworks, no lee $env:APPDATA y no consulta procesos: recibe
    las raices como parametro y opera sobre ellas.

    Esa separacion es lo que permite probar el ciclo completo -incluida la
    sustitucion del DLL- sin desactivar ninguna proteccion. La version
    anterior lo intento al reves: un marcador `.naviscoord-sandbox` en disco
    apagaba `Assert-NoNavisworks`, y eso convertia "cualquier cosa capaz de
    escribir en el directorio de plugins" en "cualquier cosa capaz de apagar
    la unica proteccion que impide sustituir un DLL en uso".

    La diferencia no es cosmetica:

      * El nucleo no tiene ningun parametro que relaje una comprobacion.
        Recibe rutas, y opera sobre las rutas que recibe. Un test le pasa un
        temporal porque ES un temporal, no porque haya convencido a nadie de
        que lo trate como tal.
      * El entrypoint de produccion -Smoke-AddinSwap.ps1- llama SIEMPRE a
        Assert-NoNavisworks antes de resolver las raices reales. No hay
        argumento, variable ni archivo capaz de saltarselo, y hay un guard
        estructural que lo comprueba.

    Alcanzar el nucleo requiere escribir codigo que lo importe. Un atacante
    que ya puede ejecutar codigo arbitrario no necesitaba este rodeo.

    Las funciones devuelven objetos de resultado en vez de lanzar. El
    entrypoint traduce a excepcion y codigo de salida; las pruebas inspeccionan
    el estado.
#>

. (Join-Path $PSScriptRoot 'RestoreSafety.ps1')

function New-SwapResult {
    param([string]$Outcome, [string]$Detail = '')
    # Los desenlaces son explicitos. "failed_rollback_incomplete" existe porque
    # un rollback a medias NO es lo mismo que un fallo limpio, y colapsar los
    # dos en "failed" deja al operador sin saber si su instalacion sigue en pie.
    return [pscustomobject]@{
        Outcome  = $Outcome      # completed | failed | failed_rolled_back |
                                 # failed_rollback_incomplete | indeterminate
        Detail   = $Detail
        Items    = @()
        Restored = 0
        Removed  = 0
        Rejected = 0
        Mismatched = 0
    }
}

function Resolve-BackupCopy {
    # El lado del backup es igual de manipulable que el destino: una fila con
    # Version = "..\..\Windows" leeria desde donde quisiera.
    param([string]$BackupRoot, [string]$Version, [string]$Relative)
    $why = Test-RelativeSegment -Relative $Version
    if ($why) {
        return [pscustomobject]@{ Ok = $false; Path = ''; Reason = "columna Version invalida: $why"; Mismatch = '' }
    }
    return Resolve-RestoreTarget -Root $BackupRoot -Relative (Join-Path $Version $Relative)
}

function Invoke-BackupCore {
    <#
        Respalda el arbol COMPLETO de cada raiz, no solo lo que se va a
        sustituir: una restauracion que solo puede devolver lo que esperaba
        cambiar no es una restauracion.

        -Roots es una hashtable version -> ruta absoluta.
    #>
    param(
        [Parameter(Mandatory = $true)][hashtable]$Roots,
        [Parameter(Mandatory = $true)][string]$BackupRoot
    )

    $result = New-SwapResult -Outcome 'completed'
    $manifest = @()
    foreach ($version in ($Roots.Keys | Sort-Object)) {
        $source = $Roots[$version]
        if (-not (Test-Path -LiteralPath $source)) {
            return New-SwapResult -Outcome 'failed' `
                -Detail "NW${version}: no existe $source. Se aborta en vez de respaldar nada y dejar creer que hay backup."
        }
        $dest = Join-Path $BackupRoot $version
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        Copy-Item -Path (Join-Path $source '*') -Destination $dest -Recurse -Force
        $base = (Get-Item -LiteralPath $source -Force).FullName.TrimEnd('\')
        foreach ($file in (Get-ChildItem -LiteralPath $source -Recurse -File -Force)) {
            $manifest += [pscustomobject]@{
                Version  = $version
                Relative = $file.FullName.Substring($base.Length).TrimStart('\')
                Original = $file.FullName
                Bytes    = $file.Length
                Sha256   = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            }
        }
    }
    $manifest | Export-Csv (Join-Path $BackupRoot 'manifest.csv') -NoTypeInformation -Encoding UTF8
    $result.Items = $manifest
    return $result
}

function Invoke-VerifyCore {
    <#
        Un backup vacio "verificaba" limpiamente, que es la peor respuesta
        posible: daba luz verde para sustituir una instalacion de la que no
        habia nada que restaurar.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$BackupRoot,
        [Parameter(Mandatory = $true)][hashtable]$Roots
    )

    $manifest = @(Import-Csv (Join-Path $BackupRoot 'manifest.csv'))
    if ($manifest.Count -eq 0) {
        return New-SwapResult -Outcome 'failed' `
            -Detail "el backup en $BackupRoot esta VACIO: no hay nada que restaurar"
    }

    $result = New-SwapResult -Outcome 'completed'
    $ok = 0; $bad = 0; $missing = 0; $rejected = 0
    foreach ($row in $manifest) {
        $root = $Roots[$row.Version]
        if (-not $root) {
            $rejected++
            $result.Items += "version desconocida en el manifiesto: $($row.Version)"
            continue
        }
        $target = Resolve-RestoreTarget -Root $root -Relative $row.Relative -Recorded $row.Original
        if (-not $target.Ok) {
            $rejected++; $result.Items += "MANIFIESTO RECHAZADO: $($row.Relative) -> $($target.Reason)"; continue
        }
        $source = Resolve-BackupCopy -BackupRoot $BackupRoot -Version $row.Version -Relative $row.Relative
        if (-not $source.Ok) {
            $rejected++; $result.Items += "origen invalido: $($row.Relative) -> $($source.Reason)"; continue
        }
        if (-not (Test-Path -LiteralPath $source.Path)) {
            $missing++; $result.Items += "FALTA en backup: $($row.Relative)"; continue
        }
        if ((Get-FileHash -LiteralPath $source.Path -Algorithm SHA256).Hash -eq $row.Sha256) { $ok++ }
        else { $bad++; $result.Items += "HASH DISTINTO: $($row.Relative)" }
    }
    $result.Restored = $ok; $result.Mismatched = $bad; $result.Rejected = $rejected + $missing
    if ($rejected) {
        $result.Outcome = 'failed'
        $result.Detail = 'el manifiesto contiene rutas que no se pueden restaurar con seguridad'
    }
    elseif ($bad -or $missing) {
        $result.Outcome = 'failed'
        $result.Detail = 'el backup NO es restaurable con fidelidad'
    }
    return $result
}

function Invoke-InstallCore {
    <#
        Sustituye SOLO los archivos gestionados. Copia a un temporal junto al
        destino y publica con un rename, para que un fallo a mitad no deje un
        DLL truncado donde antes habia uno bueno.
    #>
    param(
        [Parameter(Mandatory = $true)][hashtable]$Roots,
        [Parameter(Mandatory = $true)][string]$StagedDir,
        [Parameter(Mandatory = $true)][object[]]$Managed
    )

    $result = New-SwapResult -Outcome 'completed'
    $written = @()
    foreach ($version in ($Roots.Keys | Sort-Object)) {
        foreach ($m in $Managed) {
            $staged = Join-Path (Join-Path $StagedDir $version) $m.File
            if (-not (Test-Path -LiteralPath $staged)) {
                return New-SwapResult -Outcome 'failed' `
                    -Detail "no existe el binario compilado: $staged. Corre antes scripts\Build-Release.ps1 -Version $version."
            }
            $targetDir = Join-Path $Roots[$version] $m.Folder
            if (-not (Test-Path -LiteralPath $targetDir)) {
                New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
            }
            $target = Join-Path $targetDir $m.File
            $temp = "$target.new-$([guid]::NewGuid().ToString('N').Substring(0,8))"
            Copy-Item -LiteralPath $staged -Destination $temp -Force
            $expected = (Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash
            $actual = (Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash
            if ($expected -ne $actual) {
                Remove-Item -LiteralPath $temp -Force
                return New-SwapResult -Outcome 'failed' `
                    -Detail "la copia de $($m.File) no reprodujo el binario; no se publica nada"
            }
            # File.Move(source, destination, overwrite) is absent in Windows
            # PowerShell 5.1/.NET Framework. Both operations below publish a
            # complete file atomically on the destination volume.
            try {
                if ([System.IO.File]::Exists($target)) {
                    $previous = "$temp.previous"
                    [System.IO.File]::Replace($temp, $target, $previous)
                    Remove-Item -LiteralPath $previous -Force
                } else {
                    [System.IO.File]::Move($temp, $target)
                }
            } catch {
                if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
                return New-SwapResult -Outcome 'failed' -Detail "no se pudo publicar $($m.File): $($_.Exception.Message)"
            }
            $written += [pscustomobject]@{ Version = $version; Path = $target; Sha256 = $actual }
        }
    }
    $result.Items = $written
    return $result
}

function Invoke-RestoreCore {
    <#
        Restaurar es devolver el arbol a su estado, y eso incluye volver a la
        AUSENCIA: si el add-in no estaba instalado, el backup no lo contiene, y
        copiar solo lo respaldado dejaba el DLL puesto para siempre mientras el
        script anunciaba "restaurado".

        Todo se resuelve ANTES de escribir: una fila que no se puede restaurar
        con seguridad aborta la operacion entera, porque a mitad de camino ya
        no hay decision buena.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$BackupRoot,
        [Parameter(Mandatory = $true)][hashtable]$Roots,
        [Parameter(Mandatory = $true)][object[]]$Managed
    )

    $manifest = @(Import-Csv (Join-Path $BackupRoot 'manifest.csv'))
    if ($manifest.Count -eq 0) {
        return New-SwapResult -Outcome 'failed' -Detail "el backup en $BackupRoot esta VACIO"
    }

    $plan = @()
    foreach ($row in $manifest) {
        $root = $Roots[$row.Version]
        if (-not $root) {
            return New-SwapResult -Outcome 'failed' `
                -Detail "el manifiesto nombra una version que no se esta restaurando: $($row.Version)"
        }
        $target = Resolve-RestoreTarget -Root $root -Relative $row.Relative -Recorded $row.Original
        if (-not $target.Ok) {
            return New-SwapResult -Outcome 'failed' `
                -Detail "fila de manifiesto no restaurable ($($row.Relative)): $($target.Reason)"
        }
        $source = Resolve-BackupCopy -BackupRoot $BackupRoot -Version $row.Version -Relative $row.Relative
        if (-not $source.Ok) {
            return New-SwapResult -Outcome 'failed' `
                -Detail "origen de backup no valido ($($row.Relative)): $($source.Reason)"
        }
        $plan += [pscustomobject]@{
            Version = $row.Version; Relative = $row.Relative; Sha256 = $row.Sha256
            Source = $source.Path; Target = $target.Path; Mismatch = $target.Mismatch
        }
    }

    $result = New-SwapResult -Outcome 'completed'
    foreach ($item in $plan) {
        if (-not (Test-Path -LiteralPath $item.Source)) { continue }
        $targetDir = Split-Path -Parent $item.Target
        if (-not (Test-Path -LiteralPath $targetDir)) {
            New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
        }
        Copy-Item -LiteralPath $item.Source -Destination $item.Target -Force
        if ((Get-FileHash -LiteralPath $item.Target -Algorithm SHA256).Hash -eq $item.Sha256) {
            $result.Restored++
        }
        else {
            $result.Mismatched++
            $result.Items += "NO COINCIDE tras restaurar: $($item.Relative)"
        }
    }

    $folders = @($Managed | ForEach-Object { $_.Folder })
    foreach ($version in ($plan | ForEach-Object { $_.Version } | Sort-Object -Unique)) {
        $root = $Roots[$version]
        $backed = @($plan | Where-Object { $_.Version -eq $version } | ForEach-Object { $_.Relative })
        foreach ($stray in (Get-RestoreExtraneous -Root $root -Backed $backed -Managed $folders)) {
            $base = (Get-Item -LiteralPath $root -Force).FullName.TrimEnd('\')
            $relative = $stray.Substring($base.Length).TrimStart('\')
            # Se revalida la ruta concreta antes de borrar: el listado viene
            # del disco, pero el borrado no se apoya en eso.
            $check = Resolve-RestoreTarget -Root $root -Relative $relative
            if (-not $check.Ok) {
                $result.Items += "NO se retira $stray -> $($check.Reason)"; continue
            }
            Remove-Item -LiteralPath $check.Path -Force
            $result.Removed++
        }
    }

    if ($result.Mismatched) {
        $result.Outcome = 'failed'
        $result.Detail = 'la restauracion no reprodujo el estado original'
    }
    return $result
}
