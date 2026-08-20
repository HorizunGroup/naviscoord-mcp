# Guía de release

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
contiene. Sal a una copia limpia del tag y construye ahí:

```bash
RELEASE_TREE="/tmp/release-${VERSION}"
git worktree add "$RELEASE_TREE" "$TAG"
cd "$RELEASE_TREE"
python scripts/build_artifacts.py
powershell -File scripts/Build-Release.ps1 -Version all -VerifyReproducible
```

Adjuntar al release de GitHub, con los nombres que produce el script —no otros:

- `naviscoord-X.Y.Z-py3-none-any.whl` y `naviscoord-X.Y.Z.tar.gz`
- `NavisCoord-X.Y.Z-addin-NW2024.zip`, `-NW2025`, `-NW2026` — los tres, o
  corregir la lista de versiones soportadas en el README. Prometer un ZIP que
  no existe es peor que no prometerlo.
- `SHA256SUMS.txt`

Cada ZIP del complemento lleva `LICENSE` y `NOTICE` dentro.

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

## 9. Después

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
