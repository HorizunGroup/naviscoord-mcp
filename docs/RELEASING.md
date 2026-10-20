# Guía de release

**Una aprobación por versión: conecta SimplySign Desktop → Approve en `firma-certum` → listo.**

Esta guía describe el procedimiento para la siguiente versión; la versión
vigente y el estado histórico viven en `CHANGELOG.md` y en los releases de
GitHub, no se duplican aquí.

## 1. Decidir la versión

SemVer. Para este proyecto, en la práctica:

| Cambio | Bump |
|---|---|
| Se elimina o cambia de forma una herramienta MCP o una ruta HTTP | **major** |
| Se añade una herramienta, una ruta o una capacidad | **minor** |
| Corrección sin cambio de superficie | **patch** |
| Cambia el contrato de sesión o el esquema de perfil | **minor** como mínimo, y se sube la versión del contrato |

## 2. Actualizar las declaraciones

La versión se declara en siete sitios y **todos tienen que coincidir**; hay una
prueba que lo comprueba (`test_packaging.py::TestVersionCoherence`).

- `server/pyproject.toml` → `version`
- `server/naviscoord/__init__.py` → `__version__`
- `.claude-plugin/plugin.json` → `version`
- `.codex-plugin/plugin.json` → `version`
- `addin/NavisCoord.Addin/NavisCoord.Addin.csproj` → `<Version>`
- `SECURITY.md` → la primera fila de la tabla de versiones soportadas
- `CHANGELOG.md` → el encabezado `## [x.y.z]` más reciente

Los dos últimos entraron tarde a la prueba, y se nota en el historial: la
tabla de `SECURITY.md` se quedó en 0.2.2 durante tres releases porque la
comprobación solo leía los archivos con los que se *construye*. Una versión
que un lector puede ver es una versión que tiene que estar bien.

El pie de enlaces del changelog también se comprueba: cada encabezado
`## [x.y.z]` necesita su definición `[x.y.z]: …` al final del archivo, y
`[Unreleased]` tiene que comparar contra el tag más reciente. Sin eso el
encabezado se publica como corchetes literales y el diff de `Unreleased`
esconde un release entero.

### El `ref` de los marketplaces va en el commit de release, ANTES del tag

Un tag es un puntero a un commit, así que el `ref` que quede escrito en ese
commit es el que el tag publica para siempre. Etiquetar primero y actualizar
el `ref` después deja `vX.Y.Z` conteniendo el ref anterior: quien instale
desde el tag nuevo se lleva el código viejo, y el error no se ve hasta que
alguien compara versiones. **El cambio de `ref` es parte del commit de
release, no un paso posterior.**

Durante el desarrollo pasa lo contrario: el árbol lleva la versión siguiente
y el `ref` tiene que seguir apuntando al último tag que existe de verdad,
porque apuntar a uno inexistente rompe toda instalación de inmediato.

Son **tres** invariantes y `test_packaging.py::TestMarketplacePins` las
distingue por el estado del repositorio:

| Estado | Cómo se detecta | Invariante |
|---|---|---|
| Desarrollo | no existe el tag de la versión declarada, y la rama no es la candidata | el `ref` es un tag que **existe** |
| Candidata de release | la rama es `release/naviscoord-<versión>` | `ref == "v" + versión`, aunque ese tag todavía no exista |
| Release | el tag de la versión declarada ya existe | `ref == "v" + versión` |

> Esto **ya se comprueba en CI**, y la nota anterior —que decía que la prueba
> se saltaba y que el pin había que verificarlo a mano— describía justamente
> el defecto que `marketplace_pin.py` vino a cerrar. Hoy el job `manifests`
> hace `fetch-tags: true` con `fetch-depth: 0`, de modo que `git tag` responde
> de verdad; la fase se lee del evento (`GITHUB_REF`, `GITHUB_EVENT_NAME`,
> `GITHUB_HEAD_REF`) en vez de `git rev-parse`, así que «candidata» **sí** es
> alcanzable en un pull request desde `release/naviscoord-<versión>`; y el run
> falla si la fase detectada no es la que corresponde al evento, o si en fase
> `tag` la lista de tags vuelve vacía. Verificarlo a mano antes de etiquetar
> (paso 7.4) sigue siendo buena costumbre, pero ya no es lo único que lo
> sostiene.

Los archivos a cambiar en el paso 2 son:

- `.claude-plugin/marketplace.json`
- `.agents/plugins/marketplace.json`

`Install-NavisCoord.ps1` es el instalador ligero distribuido con el código; los
artefactos binarios siguen siendo un ZIP por versión de Navisworks.

## 3. Actualizar el CHANGELOG

Mover lo de «Sin publicar» a la versión nueva con su fecha.

## 4. Verificar en local

```bash
cd server && python -m pytest tests -q
dotnet run --project addin/NavisCoord.Tests
python scripts/build_artifacts.py
claude plugin validate .
```

Y con Navisworks instalado, para cada versión soportada:

```powershell
powershell -File scripts\Build-Release.ps1
```

### Qué necesita la máquina que compila el add-in

- **.NET Framework 4.8 Developer Pack** (targeting pack). El add-in apunta a
  `net48` porque es el runtime que carga Navisworks; el SDK de .NET 8 solo
  puede compilarlo si el targeting pack está instalado. Sin él, el error es
  `NETSDK1045`/«reference assemblies for .NETFramework,Version=v4.8 were not
  found» — se instala desde
  <https://dotnet.microsoft.com/download/dotnet-framework/net48> y no
  requiere reiniciar.
- **SDK de .NET 8** (`dotnet --version` ≥ 8): compila el proyecto y corre la
  suite de pruebas, que apunta al mismo `net48`.
- **Navisworks Manage instalado** por cada versión a compilar. La detección
  ya no asume `C:\Program Files`: `scripts\FindNavisworks.ps1` consulta el
  registro de Autodesk (las dos vistas, 32 y 64 bits) y después las rutas
  convencionales en **todas** las unidades fijas, y verifica que
  `Autodesk.Navisworks.Api.dll` esté de verdad en el candidato. Si no
  encuentra una versión, el error lista cada ruta que sondeó.
- Los DLL de Autodesk **nunca** se empaquetan: se compila contra ellos con
  `Private=false` y el ZIP lleva solo `NavisCoord.dll`, la cinta
  (`NavisCoordRibbon.xaml` en la raíz y en `en-US/`, `nc_16.png`, `nc_32.png`),
  licencias y el perfil
  de ejemplo.

Compila una vez por versión detectada, arma `dist/addin/<versión>/`, copia
`LICENSE`, `NOTICE` y el perfil de ejemplo dentro de cada carpeta, empaqueta
el ZIP y verifica en disco lo que dejó. `-WhatIf` enseña qué haría sin tocar
nada; `-Version 2026` limita a una.

Nunca borra un `naviscoord-profile.json` que ya estuviera en el destino: un
perfil afinado es trabajo de un coordinador y el script no sabe reconstruirlo.

## 5. Prueba de instalación desde el wheel

No basta con `pip install -e .`: un editable oculta todo problema de
empaquetado.

```bash
python -m venv /tmp/v && /tmp/v/bin/python -m pip install server/dist/naviscoord-X.Y.Z-py3-none-any.whl
/tmp/v/bin/python -c "from naviscoord.profile import Profile; print(Profile.load().name)"
```

Ese `Profile.load()` es la prueba concreta: el perfil vive **dentro** del
paquete precisamente porque estar un nivel más arriba solo funcionaba en
instalaciones editables.

## 6. Las pruebas que necesitan Navisworks

Ninguna de estas corre en CI. Ver [TESTING.md](TESTING.md) para la lista
completa; el mínimo antes de publicar:

- [ ] Abrir un federado real y correr el flujo 0-1-2-3 con las herramientas
      MCP (`navis_audit_models`, `navis_configure`, `navis_run`,
      `navis_group_levels`). Desde la cinta ya no se puede: esos cinco botones
      se quitaron en 0.3.0 por duplicar las rutas, y `CoordinationWorkflow`
      —el servicio que ambos llamaban— es el mismo.
- [ ] La pestaña **NavisCoord** aparece con su icono y «Estado del puente»
      informa sin alterar nada si se responde «No».
- [ ] `navis_save_as` a un `.nwf` local; cerrar, reabrir y confirmar que
      sets, tests, resultados y grupos siguen ahí.
- [ ] Con un `.nwfacc` de ACC abierto: comprobar que `navis_save` se rechaza
      con la razón correcta.
- [ ] Dos Navisworks abiertas: `navis_sessions` lista ambas; cerrar una no
      rompe la otra; y **toda** mutación sin `navis_target` se rechaza — no
      solo las evidentes. Comprobar en concreto `navis_build_sets`,
      `navis_build_clash_matrix`, `navis_run_tests` y `navis_reset_appearance`,
      que son las cuatro que se publicaron sin ese guardia: no daban error,
      escribían en el modelo que Navisworks hubiera tocado último.
- [ ] `navis_run(run_async=True)` y comprobar que `navis_health` y
      `navis_job_status` **siguen respondiendo** durante la corrida.
- [ ] Con cambios sin guardar, comprobar los tres modos de
      `navis_close_document`: `require_clean` rechaza, `save` persiste y
      `discard` solo descarta cuando se pidió expresamente.
- [ ] `navis_exit(dry_run=false)` responde antes de cerrar y solo queda en
      `completed` cuando el PID de Navisworks desaparece.

Para hacer todo eso **con el binario del release** en vez de con el compilado
del árbol, hay que sustituir el complemento que ya tienes instalado, que es la
acción menos reversible de esta validación. Para eso está
`scripts/Smoke-AddinSwap.ps1`, en cuatro modos separados y comprobables por
separado — toca **solo** el DLL de NavisCoord y deja en paz el perfil y
cualquier otro plugin:

```powershell
.\scripts\Smoke-AddinSwap.ps1 -Mode Backup  -Versions 2026   # copia con manifiesto SHA-256
.\scripts\Smoke-AddinSwap.ps1 -Mode Install -Versions 2026   # instala el de dist\addin\<versión>\
# … las pruebas de arriba …
.\scripts\Smoke-AddinSwap.ps1 -Mode Restore -BackupRoot <ruta>  # y vuelve a comparar hashes
```

## 7. El commit de release, y el tag encima

El orden importa y no es intercambiable. Todo lo que el tag debe contener
tiene que estar ya escrito **antes** de crearlo.

```bash
VERSION=$(python -c "import sys; sys.path.insert(0, 'server'); import naviscoord; print(naviscoord.__version__)")
TAG="v${VERSION}"

# 1. Un solo commit con el bump de versión, el CHANGELOG fechado
#    y los refs de marketplace ya en la versión nueva.
git add -A && git commit -m "Release ${VERSION}"

# 2. Con el commit hecho, las dos invariantes se comprueban juntas.
cd server && python -m pytest tests/test_packaging.py -q

# 3. El tag apunta a ESE commit.
git tag -a "$TAG" -m "$TAG"

# 4. Confirmar que el tag lleva dentro el ref correcto, no el anterior.
git show "$TAG":.claude-plugin/marketplace.json | grep '"ref"'
git show "$TAG":.agents/plugins/marketplace.json | grep '"ref"'

# 5. Y sólo entonces publicarlo.
git push origin main "$TAG"
```

El paso 4 no es ceremonia: es la comprobación que faltaba, y su ausencia es
lo que hace plausible publicar un tag que instala la versión anterior.

### La ventana entre el merge y el tag

Entre el paso 1 y el paso 5, `main` declara una versión cuyo tag todavía no
existe. Es una ventana legítima del proceso y la comprobación del pin la
nombra —fase `main`, «el tag todavía no se ha publicado»— en vez de fallar.
Lo que sigue siendo un fallo en `main` es un ref que ni existe como tag ni
coincide con la versión declarada.

### El CI del tag

Empujar el tag dispara el workflow sobre el propio tag, y ahí la comprobación
corre en fase `tag`: exige que el tag exista, que apunte al commit que se está
construyendo y que los manifiestos lo nombren. **Ese run tiene que quedar
verde antes de publicar el release**; es la única ejecución que demuestra que
lo que el tag contiene es lo que se va a instalar.

```bash
gh run list --commit "$(git rev-list -n1 "$TAG")" --limit 5
```

## 8. Artefactos, construidos desde el tag

Construir desde el working tree puede meter en el ZIP cambios que el tag no
contiene. **No es hipotético**: comprobado en esta máquina, el mismo commit
daba un DLL de 246.272 B construido desde el ref y 348.160 B construido desde
el árbol con cambios sin commitear.

Para el add-in, `Build-Release.ps1` lo hace solo:

```powershell
powershell -File scripts\Build-Release.ps1 -Ref v0.4.0
```

Resuelve el ref a **un** SHA una sola vez —resolverlo dos veces es la ventana
en la que un `git tag -f` ajeno cambia lo que se publica—, clona ese commit
desprendido en un temporal único, compila ahí con el script *de ese commit*, y
copia los ZIP a `dist\addin\` junto a un `FROM-REF.txt` con ref y SHA. El
temporal se borra solo, tras comprobar ruta absoluta, contención en `%TEMP%`,
marcador de propiedad de esa corrida y ausencia de reparse points.

Para el paquete de Python, una copia limpia del tag:

```bash
RELEASE_TREE="/tmp/release-${VERSION}"
git worktree add "$RELEASE_TREE" "$TAG"
cd "$RELEASE_TREE"
python scripts/build_artifacts.py
powershell -File scripts/Build-Release.ps1 -Version all -VerifyReproducible
```

`build_artifacts.py` **no escribe en `server/`**. Copia el paquete a un
workspace temporal único, mete ahí LICENSE, NOTICE y README, y construye en la
copia. La versión anterior los dejaba caer dentro de `server/` y los borraba en
un `finally` —que no corre si matan el proceso—, así que un build interrumpido
dejaba tres archivos sin rastrear que parecen una segunda fuente de verdad, dos
builds simultáneos se peleaban por ellos, y compilar desde un tag ensuciaba el
worktree cuya limpieza la release tenía que demostrar.

### Las dos comprobaciones que solo se responden construyendo

```bash
python scripts/verify_sdist.py --strict        # ¿se basta el sdist a sí mismo?
python scripts/build_artifacts.py --reproducible  # ¿dos builds, los mismos bytes?
```

- **`verify_sdist.py`** extrae el sdist en un temporal y reconstruye el wheel
  *desde ahí*. Un sdist sin `pyproject.toml`, sin el perfil por defecto o sin
  el README que `project.readme` nombra se descarga bien y falla al construir,
  meses después y en la máquina de otro. Informa por separado de dos cosas:
  mismos **miembros** (autosuficiente) y mismos **bytes** (reproducible desde
  la fuente publicada). Nunca llama reproducible a algo que no comparó.
- **`--reproducible`** construye dos veces desde cero y compara. Un rebuild
  incremental no demuestra nada: demuestra que no se recompiló. Esta es la
  única comprobación que dice si `normalize_sdist` sigue funcionando —
  setuptools honra `SOURCE_DATE_EPOCH` en el wheel pero **no** en el sdist,
  donde las marcas de tiempo son las del disco.

Medido en esta máquina sobre `0.4.0`: wheel y sdist idénticos byte a byte entre
dos construcciones, y el wheel reconstruido desde el sdist idéntico al
original (`sha256 6522bb21…`).

Ambas corren también en el job `packaging` del CI.

Adjuntar al release de GitHub, **como borrador** (sección 9), con los nombres
que producen los scripts —no otros—. Son trece assets:

- `NavisCoord-X.Y.Z-addin-NW2024.zip`, `-NW2025`, `-NW2026` — los tres, o
  corregir la lista de versiones soportadas en el README. Prometer un ZIP que
  no existe es peor que no prometerlo. Cada uno lleva `NavisCoord.dll`.
- `naviscoord-X.Y.Z-py3-none-any.whl` y `naviscoord-X.Y.Z.tar.gz`
- `naviscoord-X.Y.Z-win-x64.mcpb` (runtime completo),
  `naviscoord-runtime-X.Y.Z-win-x64.zip` y `naviscoord-desktop-X.Y.Z-win-x64.zip`
- `naviscoord-X.Y.Z-win-x64-installer.mcpb` y su `.mcpb.sha256` (lanzador
  pequeño que descarga el runtime de esta misma release)
- `provenance.json` y `sbom.cdx.json`
- `SHA256SUMS.txt`

Quién produce cada uno, todo **desde el tag** (entorno limpio de Python 3.14
con el lock; ver «Desktop runtime release gate» más abajo):

| Asset | Origen |
|---|---|
| 3 ZIP del complemento | `scripts\Build-Release.ps1 -Ref vX.Y.Z` (Navisworks 2024/2025/2026 instalados) |
| runtime ZIP, desktop ZIP y `SHA256SUMS.txt` parcial | `build_portable.py` -> `verify_stdio.py` -> `build_mcpb.py` -> `mcpb pack` -> `package_desktop.py` |
| `win-x64.mcpb` | `npm exec --yes --package=@anthropic-ai/mcpb -- mcpb pack dist/mcpb dist/naviscoord-X.Y.Z-win-x64.mcpb` |
| `installer.mcpb` y `.sha256` | `build_bootstrap_mcpb.py dist/naviscoord-X.Y.Z-win-x64.mcpb` y luego `mcpb pack dist/mcpb-bootstrap dist/naviscoord-X.Y.Z-win-x64-installer.mcpb` (necesita que el tag exista: toma los lanzadores del commit del tag) |
| wheel, sdist, `sbom.cdx.json`, `provenance.json` | artifact `release-evidence` del run de CI **del tag** (`gh run download <run> -n release-evidence`) |

El `SHA256SUMS.txt` de la release une las sumas de `package_desktop.py` (los
seis paquetes de Windows) con las de la evidencia (wheel, sdist, SBOM y
provenance); el `.sha256` del instalador va aparte. `provenance.json` y el SBOM
**no contienen hashes de los binarios** (solo tag, commit, run y componentes de
Python), así que la firma no los invalida: se suben tal cual.

Cada ZIP del complemento lleva `LICENSE` y `NOTICE` dentro, y sus entradas son
exactamente las `$ManagedFiles` de `Install-NavisCoord.ps1`:
`test_addin_package_parity.py` compara las dos listas. Antes de 1.1.1 no
coincidían y el instalador oficial rechazaba los ZIP publicados.

Los checksums se generan sobre los artefactos ya construidos, en modo binario
y con nombres relativos, para que `sha256sum -c` funcione desde la carpeta de
descarga:

```bash
cd dist && sha256sum -b NavisCoord-*.zip naviscoord-*.whl naviscoord-*.tar.gz > SHA256SUMS.txt
sha256sum -c SHA256SUMS.txt
```

El build de Release desactiva símbolos, normaliza rutas con `PathMap` y es
determinista. `Build-Release.ps1` ejecuta `Assert-PublicArtifacts.ps1` antes de
empaquetar: cualquier ruta de usuario, PDB o raíz absoluta bloquea el ZIP. Aun
así, los checksums publicados deben salir del build definitivo hecho desde el
tag, nunca de un ensayo anterior. `-VerifyReproducible` construye en rutas
distintas y exige hashes idénticos antes de aceptar los ZIP.

## 9. Firma Certum: borrador -> aprobación -> publicada firmada

Todo binario propio publicado va firmado con el certificado de HORIZUN GROUP
S.A.S. (Certum). Las releases publicadas son **inmutables** en GitHub (no se
pueden reemplazar assets), así que la firma ocurre **antes** de publicar y
nunca se publica una release sin firmar.

Qué se firma: `NavisCoord.dll` de cada ZIP del complemento y
`naviscoord-mcp.exe` del runtime ZIP, del desktop ZIP y del `win-x64.mcpb`
(cualquier `.exe`, `.dll` o `.msi` propio, también dentro de ZIP, MCPB y NuGet
anidados). Las DLL de Autodesk y de terceros no se tocan.

El ciclo:

1. Terminar la sección 7 (merge del PR de release y tag anotado `vX.Y.Z`) y
   esperar el CI del tag en verde. `ci.yml` **no crea releases**.
2. Construir los trece assets de la sección 8 desde el tag y crear la release
   como **borrador**:

   ```bash
   gh release create vX.Y.Z --draft --verify-tag --title "NavisCoord X.Y.Z" \
     --notes-file <notas> <los trece assets>
   ```

3. Lanzar el workflow de firma (la CI deja el borrador; nada se publica aún):

   ```bash
   gh workflow run firmar-release.yml -R HorizunGroup/naviscoord-mcp -f tag=vX.Y.Z
   ```

4. El job `firmar-y-publicar` (runner `self-hosted, windows, horizun-sign`,
   environment `firma-certum`) queda **esperando aprobación** de Pablo
   (revisor obligatorio). GitHub avisa.
5. Pablo conecta **SimplySign Desktop** (la clave Certum vive ahí) y aprueba
   el environment en la pestaña **Actions** del run («Review deployments»).
6. El job ejecuta `scripts/firmar-release.ps1`: comprueba que puede firmar
   (archivo de prueba) antes de tocar nada, descarga el borrador, firma,
   regenera `SHA256SUMS.txt`, el `.sha256` del instalador y los
   `runtime-files.json` internos, verifica cada firma (`signtool verify /pa`,
   estado `Valid`, emisor Certum y sello de tiempo Certum), sube con
   `--clobber`, vuelve a descargar lo subido y compara, y **solo entonces
   publica** el borrador. Si cualquier paso falla, la release sigue en
   borrador y no se publica nada.

### Hashes que cuidar (nada fijado puede contradecir los bytes firmados)

| Dónde vive el hash | Qué pasa con la firma |
|---|---|
| `SHA256SUMS.txt` y `*.mcpb.sha256` (assets) | los regenera el script contra los archivos ya firmados |
| `runtime-files.json` dentro del runtime ZIP, el desktop ZIP y el `win-x64.mcpb` | el script lo reescribe tras firmar el `.exe`; `Install-Runtime.ps1` y el lanzador lo usan para verificar |
| `Install-Runtime.ps1` e `Install-NavisCoord.ps1` | no fijan nada: leen el hash del `SHA256SUMS.txt` **de la release** al instalar, así que aceptan los bytes firmados |
| `provenance.json`, `sbom.cdx.json` | no contienen hashes de binarios: no cambian |
| `server.json` -> `packages[0].fileSha256` (**en el repo**) | es el único pin en el árbol y apunta al `win-x64.mcpb`. El registro MCP verifica ese hash contra el asset publicado, así que tiene que ser el del MCPB **firmado**. No puede ir en el commit del tag (el MCPB firmado aún no existe): se actualiza **después** de publicar, en un PR posterior (como «Registrar X.Y.Z en el registro MCP oficial»), leyendo el hash del asset ya publicado |
| `docs/PUBLISHING.md` | los hashes que cita son históricos de cada release; al registrar una nueva se escribe el del asset firmado y publicado |

Tras publicar: hash del asset publicado -> PR que sube `server.json` (versión,
URL y `fileSha256`) -> publicar en el registro MCP -> actualizar README y
`docs/PUBLISHING.md`.

### El runner de firma

Hay un runner self-hosted con la etiqueta `horizun-sign` por repositorio, en
`%USERPROFILE%\.horizun\sign-runners\naviscoord-mcp`. Tiene que correr en la
sesión interactiva de Pablo para ver `Cert:\CurrentUser\My` (SimplySign
Desktop). Lo arranca la tarea programada de usuario **Horizun Sign Runners**
con `%USERPROFILE%\.horizun\sign-runners\iniciar-runners.ps1`, que lanza los
que no estén en marcha. Para revisarlo: Settings -> Actions -> Runners del
repo (debe verse *Idle* con las etiquetas `self-hosted`, `Windows` y
`horizun-sign`), o a mano:

```powershell
Get-CimInstance Win32_Process -Filter "Name='Runner.Listener.exe'" | Select-Object ExecutablePath
& "$env:USERPROFILE\.horizun\sign-runners\iniciar-runners.ps1"   # arranca los que falten
```

### Si el job falla por SimplySign desconectado

El primer paso del script firma un archivo de prueba y, si no puede, aborta
con «No se puede firmar (SimplySign desconectado o sesión caducada)» sin haber
tocado la release. Reconectar SimplySign Desktop y pulsar **Re-run failed
jobs** en el run; el environment pide aprobar otra vez. Si falla después de
subir (la comparación remota) la release sigue en borrador y el script es
idempotente: reintentar es seguro.

### Ensayar el script sin firmar de verdad

```powershell
pwsh scripts/firmar-release.ps1 -Directorio <carpeta con los assets> -Salida <destino> `
  -Prueba -Thumbprint <huella de un certificado de prueba autofirmado>
```

`-Prueba` acepta un certificado autofirmado (sin sello ni emisor Certum) y no
sube nada: solo sirve para comprobar que ninguna referencia de hash queda
obsoleta. Borrar el certificado de prueba al terminar.

Si una release publicada resulta mal firmada o sin firmar, no se puede
corregir: se sube una versión patch nueva.

## 10. Después

- Confirmar que `/plugin install naviscoord-mcp` trae el tag nuevo.
- Confirmar que `docs/INSTALL.md` no promete nada que no esté publicado.

## Configuración externa de GitHub

Mantener activos estos controles:

- reporte privado de vulnerabilidades;
- protección de `main` con PR, una aprobación, `ci-ok`, historial actualizado
  y aplicación también a administradores;
- ruleset que impide actualizar o borrar tags `v*`;
- alertas y correcciones de seguridad de Dependabot;
- secret scanning y push protection.

Discussions permanece habilitado para preguntas, ejemplos y soporte comunitario;
los defectos reproducibles siguen entrando por los formularios de Issue.
`CODEOWNERS` queda como decisión del equipo; la revisión obligatoria ya existe
sin asignar propietarios por ruta.

### Por qué el check requerido es `ci-ok` y no cada job de la matriz

Los nombres de varios jobs llevan la matriz dentro (`motor (py3.10,
mcp<2)` y compañía). Registrarlos como requeridos ata la configuración del
repositorio a la forma de la matriz: el día que entre Python 3.14 o cambie un
rango de `mcp`, el check requerido deja de existir, nadie lo reporta nunca y
**todo pull request queda bloqueado esperando un estado que no puede llegar**.

`ci-ok` depende de todos ellos con `needs:` y corre con `if: always()`, así que
falla si cualquiera falla, se cancela, se salta o desaparece. Es un solo nombre
estable y la matriz queda libre de cambiar sin tocar Settings.

## Desktop runtime release gate (1.0 and later)

Build the Windows runtime in a clean Python 3.14 virtual environment. Export requirements with `runtime_lock_spec.requirements_text(load())`, install them with `pip --require-hashes`, then install PyInstaller 6.22.2 and run `pip check`. Do not regenerate the runtime lock during packaging.

```powershell
python scripts/build_portable.py
python scripts/verify_stdio.py dist/portable/naviscoord-mcp/naviscoord-mcp.exe
python scripts/build_mcpb.py
npm exec --yes --package=@anthropic-ai/mcpb -- mcpb pack dist/mcpb dist/naviscoord-1.0.0-win-x64.mcpb
python scripts/package_desktop.py
powershell -File scripts/Test-PortableInstall.ps1
```

Use the declared release version for the MCPB filename. `package_desktop.py` reads that version and requires all three add-in ZIPs. It creates the standalone runtime ZIP, the local desktop plugin ZIP, and `dist/SHA256SUMS.txt`. Add the Python wheel/sdist and their hashes when preparing the complete release upload.

Run `scripts/live_acceptance.py --pid <dedicated sample PID> --allow-sample-writes --evidence <local evidence path>` against an unmodified Autodesk gatehouse sample session. It verifies nested search sets, invalid matrix refusal, a real clash run, snapshots, group write-back, revision invalidation and a PDF with images. Close the QA document with discard afterwards. Do not publish raw local evidence containing user paths; record sanitized outcomes in `RELEASE-1.0-VERIFICATION.md`.

The `portable-runtime` CI job independently installs the lock, builds the executable and exercises the real MCP stdio protocol plus interrupted-install repair. It is required by `ci-ok`.
