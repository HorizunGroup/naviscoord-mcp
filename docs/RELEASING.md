# Guía de release

> **Nada de esto se ha ejecutado todavía.** No hay release publicado por
> encima de `v0.1.2`. Esta guía es el procedimiento, no un registro.

## 1. Decidir la versión

SemVer. Para este proyecto, en la práctica:

| Cambio | Bump |
|---|---|
| Se elimina o cambia de forma una herramienta MCP o una ruta HTTP | **major** |
| Se añade una herramienta, una ruta o una capacidad | **minor** |
| Corrección sin cambio de superficie | **patch** |
| Cambia el contrato de sesión o el esquema de perfil | **minor** como mínimo, y se sube la versión del contrato |

## 2. Actualizar las declaraciones

La versión se declara en seis sitios y **todos tienen que coincidir**; hay una
prueba que lo comprueba (`test_packaging.py::TestVersionCoherence`).

- `server/pyproject.toml` → `version`
- `server/naviscoord/__init__.py` → `__version__`
- `.claude-plugin/plugin.json` → `version`
- `.codex-plugin/plugin.json` → `version`
- `addin/NavisCoord.Addin/NavisCoord.Addin.csproj` → `<Version>`

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

> El estado «candidata» se detecta por el **nombre de la rama**, así que la
> comprobación solo es concluyente en un clon con la rama activa y con los
> tags presentes. En CI no lo es: `actions/checkout` no trae tags, de modo que
> la prueba se salta; y en el checkout desacoplado que GitHub usa para un pull
> request, `git rev-parse --abbrev-ref HEAD` responde `HEAD` y el estado
> «candidata» es inalcanzable. **Este pin se verifica a mano antes de
> etiquetar** (paso 7.4), no en CI.

Los archivos a cambiar en el paso 2 son:

- `.claude-plugin/marketplace.json`
- `.agents/plugins/marketplace.json`

Este repositorio no publica instalador: el artefacto del complemento es un
ZIP por versión de Navisworks, construido en el paso 4.

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

- [ ] Abrir un federado real y correr el flujo 0-1-2-3 desde la cinta.
- [ ] Correr los mismos cuatro pasos como herramientas MCP y comparar.
- [ ] `navis_save_as` a un `.nwf` local; cerrar, reabrir y confirmar que
      sets, tests, resultados y grupos siguen ahí.
- [ ] Con un `.nwfacc` de ACC abierto: comprobar que `navis_save` se rechaza
      con la razón correcta.
- [ ] Dos Navisworks abiertas: `navis_sessions` lista ambas; una mutación sin
      `navis_target` se rechaza; cerrar una no rompe la otra.
- [ ] `navis_run(run_async=True)` y comprobar que `navis_health` y
      `navis_job_status` **siguen respondiendo** durante la corrida.

## 7. El commit de release, y el tag encima

El orden importa y no es intercambiable. Todo lo que el tag debe contener
tiene que estar ya escrito **antes** de crearlo.

```bash
# 1. Un solo commit con el bump de versión, el CHANGELOG fechado
#    y los refs de marketplace ya en la versión nueva.
git add -A && git commit -m "Release 0.2.1"

# 2. Con el commit hecho, las dos invariantes se comprueban juntas.
cd server && python -m pytest tests/test_packaging.py -q

# 3. El tag apunta a ESE commit.
git tag -a v0.2.1 -m "v0.2.1"

# 4. Confirmar que el tag lleva dentro el ref correcto, no el anterior.
git show v0.2.1:.claude-plugin/marketplace.json | grep '"ref"'
git show v0.2.1:.agents/plugins/marketplace.json | grep '"ref"'

# 5. Y sólo entonces publicarlo.
git push origin main v0.2.1
```

El paso 4 no es ceremonia: es la comprobación que faltaba, y su ausencia es
lo que hace plausible publicar un tag que instala la versión anterior.

## 8. Artefactos, construidos desde el tag

Construir desde el working tree puede meter en el ZIP cambios que el tag no
contiene. Sal a una copia limpia del tag y construye ahí:

```bash
git worktree add /tmp/release-0.2.1 v0.2.1
cd /tmp/release-0.2.1 && python scripts/build_artifacts.py
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

> **El build no es reproducible entre carpetas distintas.** Medido: dos clones
> del mismo commit, compilados en `…\clean\` y `…\clean2\`, producen
> `NavisCoord.dll` de idéntico tamaño y distinto SHA-256. La diferencia no
> está en el código: el DLL lleva embebida la ruta absoluta del PDB de la
> máquina que compiló, y con ella cambia el sello del encabezado PE. Repetir
> el build **en la misma carpeta** sí da un binario idéntico.
>
> Dos consecuencias, y conviene no confundirlas:
>
> 1. Los checksums publicados tienen que ser los del build definitivo desde el
>    tag, nunca los de un ensayo previo hecho en otra ruta.
> 2. El artefacto publicado **divulga la ruta local de quien lo compiló**
>    (`C:\Users\<usuario>\…`), que es justo lo que el job `higiene pública`
>    prohíbe en el árbol —pero ese job solo mira el código, no el binario, que
>    se construye fuera de CI. Para cerrarlo hay que decidir el modo de
>    depuración del build de Release (`<DebugType>`, o `<PathMap>` +
>    `<ContinuousIntegrationBuild>`); mientras no se decida, es una fuga
>    conocida y no un descuido.

## 9. Después

- Confirmar que `/plugin install naviscoord-mcp` trae el tag nuevo.
- Confirmar que `docs/INSTALL.md` no promete nada que no esté publicado.

## Configuración externa pendiente

Esto **no** está aplicado y no puede aplicarse desde el repositorio. Requiere
permisos de administración en GitHub y una decisión humana.

### Bloqueante del release

- [ ] **Reporte privado de vulnerabilidades.** `SECURITY.md` dice que no se
      abra un issue y manda a la pestaña Security. Publicar con ese canal
      apagado deja a quien encuentre algo sin la vía que le acabamos de
      ofrecer, y la alternativa que le queda es el issue público que le
      pedimos no usar. **No publiques el release sin esto.**

```bash
# Habilitar
gh api -X PUT repos/HorizunGroup/naviscoord-mcp/private-vulnerability-reporting
# Verificar: debe responder {"enabled":true}
gh api repos/HorizunGroup/naviscoord-mcp/private-vulnerability-reporting
```

### No bloqueante

- [ ] Protección de rama en `main`: PR obligatorio, `ci-ok` en verde, sin push
      directo, sin force-push. El único check requerido es **`ci-ok`**; ver
      abajo por qué no se registran los quince.
- [ ] Ruleset para tags `v*`: solo mantenedores, sin update ni delete.
- [ ] Discussions. `SUPPORT.md` manda hoy las dudas de uso a Issues
      precisamente porque no está habilitado; si se habilita, hay que cambiar
      esa fila de vuelta.

```bash
# Habilitar Discussions (opcional)
gh api -X PATCH repos/HorizunGroup/naviscoord-mcp -F has_discussions=true
# Verificar ambos estados de una vez
gh api repos/HorizunGroup/naviscoord-mcp --jq '{has_discussions}'
gh api repos/HorizunGroup/naviscoord-mcp/private-vulnerability-reporting
```

- [ ] Decidir si `CODEOWNERS` y revisión obligatoria aplican al equipo.

### Por qué el check requerido es `ci-ok` y no los quince

Trece de los quince nombres de job llevan la matriz dentro (`motor (py3.10,
mcp<2)` y compañía). Registrarlos como requeridos ata la configuración del
repositorio a la forma de la matriz: el día que entre Python 3.14 o cambie un
rango de `mcp`, el check requerido deja de existir, nadie lo reporta nunca y
**todo pull request queda bloqueado esperando un estado que no puede llegar**.

`ci-ok` depende de todos ellos con `needs:` y corre con `if: always()`, así que
falla si cualquiera falla, se cancela, se salta o desaparece. Es un solo nombre
estable y la matriz queda libre de cambiar sin tocar Settings.
