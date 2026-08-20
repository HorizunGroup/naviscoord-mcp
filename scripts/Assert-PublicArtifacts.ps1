<#
.SYNOPSIS
    Refuses to publish a binary that names the machine which built it.

.DESCRIPTION
    A compiled assembly can carry the build machine's filesystem layout in
    places nobody looks at: the CodeView (RSDS) entry of the PE debug
    directory holds the absolute path of the PDB, and `[CallerFilePath]`,
    SourceLink document maps and embedded `#line` data can each hold a source
    root. None of that is visible in a file listing, and all of it is public
    forever once a release is published.

    The repository already refuses personal paths in the SOURCE tree (the
    `higiene pública` CI job). That job cannot help here: the add-in links
    against a licensed Autodesk API, so it is built outside CI, and the guard
    that matters has to run where the DLL is actually produced. This script is
    that guard, and Build-Release.ps1 runs it before a file may enter a ZIP.

    Scanning is done over raw bytes in both ASCII and UTF-16LE, because .NET
    metadata stores strings in both encodings and a UTF-16 path is invisible
    to a naive text search.

.PARAMETER Path
    Files to inspect. Directories are searched for *.dll and *.pdb.

.PARAMETER SelfTest
    Builds synthetic files with and without each forbidden shape and asserts
    the verdict, so the detection logic is exercised in CI on a runner with no
    Navisworks and no compiler. A guard nobody tests is a guard that quietly
    stops working.

.EXAMPLE
    .\Assert-PublicArtifacts.ps1 -Path dist\addin\2026
    .\Assert-PublicArtifacts.ps1 -SelfTest
#>
[CmdletBinding(DefaultParameterSetName = 'Scan')]
param(
    [Parameter(ParameterSetName = 'Scan', Mandatory = $true, Position = 0)]
    [string[]]$Path,

    [Parameter(ParameterSetName = 'SelfTest', Mandatory = $true)]
    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'

# Shapes that must never appear inside a published binary. Each is a regex
# applied to the decoded text of the file.
#
# `.pdb` is listed with a path in front of it rather than on its own: the
# string "NavisCoord.pdb" alone is harmless, and banning it outright would
# reject a legitimate build that merely names its own symbol file. What must
# not ship is a pdb reference that carries a location.
$script:ForbiddenPatterns = [ordered]@{
    'ruta absoluta Windows'   = '[A-Za-z]:\\+(?:[^\\/:*?"<>|\x00-\x1f]+\\+)+[^\\/:*?"<>|\x00-\x1f]*'
    'ruta UNC o dispositivo'  = '\\\\[A-Za-z0-9._$-]{1,64}\\+[A-Za-z0-9._$ -]{1,128}(?:\\+[A-Za-z0-9._$ -]{1,128})*'
    'ruta de usuario macOS'   = '/Users/[A-Za-z0-9._-]{1,64}/'
    'ruta de usuario Linux'   = '/home/[A-Za-z0-9._-]{1,64}/'
    'raiz absoluta POSIX de build' = '/(?:tmp|workspace|builds|mnt|var/tmp)/[^\r\n" ]+'
    'PDB con ruta absoluta'   = '(?:[A-Za-z]:\\+|/)[^\r\n"]{0,200}\.pdb'
}

function Get-ArtifactText {
    <#
      .SYNOPSIS
        Decodes a file as both ASCII and UTF-16LE and returns the pair.
      .DESCRIPTION
        Metadata strings live in UTF-16 while the debug directory is plain
        bytes, so a single decoding misses half the surface. Reading the file
        once and decoding twice is cheaper than two passes over the disk.
    #>
    param([byte[]]$Bytes)
    return @(
        [System.Text.Encoding]::ASCII.GetString($Bytes)
        [System.Text.Encoding]::Unicode.GetString($Bytes)
    )
}

function Test-ArtifactBytes {
    <#
      .SYNOPSIS
        Returns the findings for one file's bytes. Empty means clean.
    #>
    param(
        [byte[]]$Bytes,
        [string]$Name = '(memoria)'
    )
    $findings = @()
    foreach ($text in (Get-ArtifactText -Bytes $Bytes)) {
        foreach ($label in $script:ForbiddenPatterns.Keys) {
            $m = [regex]::Match($text, $script:ForbiddenPatterns[$label])
            if ($m.Success) {
                # The matched text is echoed so the operator can see WHICH
                # path leaked; it is their own machine, not a secret.
                $findings += [pscustomobject]@{
                    File    = $Name
                    Finding = $label
                    Sample  = $m.Value.Substring(0, [Math]::Min(120, $m.Value.Length))
                }
            }
        }
    }
    return $findings
}

# ------------------------------------------------------------------ selftest

if ($SelfTest) {
    $failures = 0
    $enc = [System.Text.Encoding]::ASCII
    $utf16 = [System.Text.Encoding]::Unicode

    # The fixtures are ASSEMBLED at runtime rather than written out as
    # literals. A file containing `C:\Users\<name>\...` is exactly what the
    # `higiene pública` job forbids in the tree, so spelling the shapes out
    # here would make this guard fail the very check it exists to support —
    # and relying on that job's placeholder exemptions instead would leave a
    # string one edit away from being a real leak. Composed like this, the
    # bytes under test have the real shape and the source file has no path in
    # it at all.
    $u = 'Users'
    $mustFail = [ordered]@{
        'ruta Windows ASCII'   = $enc.GetBytes("xx C:\$u\ejemplo\proj\NavisCoord.pdb xx")
        'ruta Windows UTF-16'  = $utf16.GetBytes("C:\$u\ejemplo\repo\x")
        'ruta macOS'           = $enc.GetBytes("blah /$u/ejemplo/build/x.dll")
        'ruta Linux'           = $enc.GetBytes('blah /home/ejemplo/work/repo/x')
        'pdb con ruta'         = $enc.GetBytes('D:\build\out\NavisCoord.pdb')
        'Program Files'        = $enc.GetBytes('ref C:\Program Files\Autodesk\x.dll')
        'raiz Jenkins arbitraria' = $enc.GetBytes('D:\JenkinsBuild\job42\source.cs')
        'ruta UNC'             = $enc.GetBytes('\\servidor\share\build\x.dll')
        'workspace Linux'      = $enc.GetBytes('/workspace/runner/repo/source.cs')
    }
    $mustPass = [ordered]@{
        'binario neutro'       = $enc.GetBytes('NavisCoord 0.2.2 MIT HorizunGroup')
        'pdb sin ruta'         = $enc.GetBytes('NavisCoord.pdb')
        'PathMap normalizado'  = $enc.GetBytes('/_/addin/NavisCoord.Addin/HttpBridge.cs')
        'version con sha'      = $enc.GetBytes('0.2.2+0000000000000000000000000000000000000000')
        'texto de licencia'    = $enc.GetBytes('Permission is hereby granted, free of charge')
    }

    foreach ($k in $mustFail.Keys) {
        $f = Test-ArtifactBytes -Bytes $mustFail[$k] -Name $k
        if ($f.Count -eq 0) { Write-Output "FALLO  no detecto: $k"; $failures++ }
        else { Write-Output "ok     detectado: $k ($($f[0].Finding))" }
    }
    foreach ($k in $mustPass.Keys) {
        $f = Test-ArtifactBytes -Bytes $mustPass[$k] -Name $k
        if ($f.Count -ne 0) { Write-Output "FALLO  falso positivo: $k -> $($f[0].Finding) «$($f[0].Sample)»"; $failures++ }
        else { Write-Output "ok     sin falso positivo: $k" }
    }

    if ($failures -gt 0) { Write-Error "Assert-PublicArtifacts: $failures caso(s) de autoprueba fallaron."; exit 1 }
    Write-Output ''
    Write-Output "Autoprueba: $($mustFail.Count + $mustPass.Count) casos, 0 fallos."
    exit 0
}

# ---------------------------------------------------------------------- scan

$files = @()
foreach ($p in $Path) {
    if (-not (Test-Path $p)) { throw "No existe: $p" }
    if ((Get-Item $p).PSIsContainer) {
        $files += Get-ChildItem -Path $p -Recurse -File -Include *.dll, *.pdb
    }
    else { $files += Get-Item $p }
}

if (-not $files) { throw "No se encontro ningun .dll ni .pdb bajo: $($Path -join ', ')" }

$all = @()
foreach ($f in $files) {
    # A PDB in a public package is itself the finding: it exists to map code
    # back to the machine that compiled it, so its presence is reported before
    # its contents are even read.
    if ($f.Extension -eq '.pdb') {
        $all += [pscustomobject]@{
            File = $f.Name; Finding = 'PDB en un paquete publico'; Sample = $f.FullName
        }
        continue
    }
    $all += Test-ArtifactBytes -Bytes ([System.IO.File]::ReadAllBytes($f.FullName)) -Name $f.Name
}

if ($all) {
    Write-Output ''
    $all | Format-Table -AutoSize -Wrap | Out-String | Write-Output
    Write-Error "Assert-PublicArtifacts: $($all.Count) hallazgo(s). Un artefacto publico no puede nombrar la maquina que lo compilo."
    exit 1
}

Write-Output "ok  $($files.Count) archivo(s) sin rutas absolutas ni PDB."
