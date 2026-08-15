# Pruebas

## Qué corre sin Navisworks

Casi todo, y es deliberado: la lógica que tuvo errores se escribió sin
referencias a Autodesk precisamente para poder probarla en un runner sin
licencia.

```bash
cd server && python -m pytest tests -q       # motor, seguridad, targeting, empaquetado
dotnet run --project addin/NavisCoord.Tests  # lógica pura del add-in
python scripts/build_artifacts.py            # wheel + sdist, verificados leyéndolos
claude plugin validate .                     # manifiestos, contra el validador real
```

### Python

| Archivo | Cubre |
|---|---|
| `test_engine.py` | Motor: filtro, clustering, severidad, causas raíz |
| `test_levels.py` | Normalización de niveles |
| `test_discovery.py` | Detección de estructura del modelo |
| `test_interop.py` | Handoff, worklist, tablas de Power BI |
| `test_safety.py` | Escapado de markup, inyección CSV, política de rutas |
| `test_regressions.py` | Los dos bugs funcionales, con el fallo antiguo asertado |
| `test_targeting.py` | Sesiones, targeting, estado por documento, capacidades |
| `test_packaging.py` | Launcher degradado, runtime, manifiestos, versiones |
| `test_mcp_surface.py` | Inventario de herramientas y sus esquemas |

### C#

`NavisCoord.Tests` es un runner de consola, no un framework: todo lo probable
fuera del hilo de UI no tiene dependencias NuGet, y añadir xunit sería un
grafo de paquetes para aserciones que caben en dos archivos. `dotnet run` es
el comando y un exit distinto de cero es la señal.

Cubre precedencia de reglas, operadores de búsqueda, nombres de nivel, series
repetidas, pureza de vistas, envelope de mutación, idempotencia, máquina de
estados de trabajos, política de rutas, formatos de guardado, esquema de
perfil, registro de sesiones y capacidades.

### Lo que se asserta y suele olvidarse

- Que el escapado funciona **según ReportLab**: se parsea el resultado y se
  comprueba que el texto renderizado es igual al payload — y en paralelo, que
  **sin** escapar ReportLab sí interpreta la etiqueta, para que el guardia no
  se pruebe a sí mismo.
- Que una coordenada negativa **no** se neutraliza en el CSV.
- Que el bug del color existía: el test calcula la inferencia antigua y
  comprueba que daba el lado contrario.
- Que cerrar una sesión no borra la otra.
- Que un `Dictionary` reordenado no cambia el veredicto de disciplina.

## Lo que NO puede correr aquí

Requiere Navisworks Manage abierto con un federado real. **Ninguna de estas
está automatizada, y ninguna se ha ejecutado en esta sesión.**

### Guardado

- [ ] `navis_save_as` a un `.nwf` local: escribe, y cerrar/reabrir conserva
      sets, tests, resultados y grupos.
- [ ] La verificación post-guardado detecta un no-op: apuntar a un archivo de
      solo lectura y comprobar que reporta `failed`, no `completed`.
- [ ] Con un `.nwfacc` de ACC abierto: `navis_save` se rechaza con la razón
      correcta y `navis_capabilities` lo declara **antes**.
- [ ] `navis_save_as` desde un documento de ACC emite el aviso de que subir a
      la nube es un paso manual.

### Multiinstancia

- [ ] Dos Navisworks abiertas: dos archivos de sesión, dos puertos.
- [ ] Cerrar una: la otra sigue funcionando y el puntero de compatibilidad se
      repunta a la viva.
- [ ] Una mutación sin `navis_target` se rechaza con ambas listadas.

### Trabajos

- [ ] `navis_run(run_async=True)` sobre un federado grande, y comprobar que
      `navis_health` y `navis_job_status` **responden durante** la corrida.
- [ ] Cancelar un `workflow/group_levels` a mitad: reporta `partial` con lo
      verificado.
- [ ] Cancelar un `workflow/run`: dice que no puede y por qué.

### Paridad cinta/MCP

- [ ] Correr el flujo 0-1-2-3 desde la cinta y como herramientas MCP sobre el
      mismo modelo, y comparar los conteos.
- [ ] Comprobar que los diálogos de la cinta siguen leyéndose bien tras el
      refactor a `WorkflowText`.

### Permisos

- [ ] Confirmar la DACL real de `%LOCALAPPDATA%\NavisCoord\sessions` con
      `icacls` tras un arranque limpio.
- [ ] Aflojar los permisos a mano y comprobar que el aviso aparece en
      `bridge.log`.

### Corrección del color

- [x] Comprobado dentro de Navisworks 2026 sobre cruces reales (2026-08-14).
      Ver «Ejecutado dentro de Navisworks» más abajo.
- [ ] Confirmación **visual** de que lo rojo es lo que debe moverse. Sigue
      abierta y no se puede automatizar: la API 2026 no expone ningún getter
      de apariencia, así que el píxel solo lo verifica un ojo humano.

## Ejecutado dentro de Navisworks (2026-08-14)

Escenario temporal levantado con copias de samples de Autodesk y un perfil
desechable, sobre Navisworks Manage 2026. Nada de esto corre en CI; queda
aquí como registro de lo que se comprobó de verdad y con qué evidencia.

| Qué | Evidencia |
|---|---|
| Cruces reales | 5 resultados en un test creado por `clash/matrix`, tras `build_sets` verificado releyendo (13 799 / 43 402 / 13 935 elementos) |
| `source_file` en el export | `north.nwd` y `west.nwd` en los 10 elementos; 0 % sin clasificar tras el arreglo (antes: 100 %) |
| Color por responsable | Los elementos pintados son **exactamente** los de la disciplina responsable, contrastados contra `elements_by_discipline`; intersección 0 con el lado inamovible. La regla anterior pintaba el lado equivocado en **4 de 4** |
| `group_levels` | 1.ª pasada: 5 sueltos → 1 grupo, `moved 5`, `verified 5`, `failed 0`. 2.ª y 3.ª: `requested 0`, sin grupos duplicados. `result_count` constante en 5 |
| Resultados sin nivel | Agrupados bajo `SIN NIVEL`, la clasificación explícita — no se pierde ninguno |
| `clash/apply_rules` | Match positivo 5/5; token de modelo distinto → 0; categoría inexistente → 0; aplicación real `verified 5 / mismatch 0` releyendo; `only_new` no reincide; reversible a `New` |
| `RefreshSession` | El archivo de sesión lleva la huella real del documento al abrirlo, por evento y sin sondeo |
| `GET /health` anónimo | Exactamente `ok`, `version`, `pid`, `busy`; ningún campo sensible |
| Sesión muerta | Un proceso terminado a la fuerza queda como obsoleto y no se ofrece como destino |

**No se pudo probar con estos samples** (geometría CAD sin muros, tubos ni
manguitos): detección de pasos sin definir. Se cubre con un test de
integración que entra por `ClashExport.from_json` con un payload copiado de
una respuesta real del bridge —
`TestPenetrationEvidenceSurvivesFiltering::test_whole_pipeline_from_a_bridge_payload`—
que prueba el contrato de ingesta, **no** que el detector acierte en un
edificio concreto.

## Las cuatro tools de paridad, dentro de Navisworks (2026-08-14)

Mismo montaje temporal, con el MCP corriendo **desde el wheel instalado en un
venv limpio** —no desde el árbol— para que lo probado sea lo que se publica.
`tools/list` desde ese paquete: **46 herramientas**, las cuatro presentes.

El perfil usado es el desechable. En esa corrida el paso de reglas todavía
leía el perfil del complemento desde disco en vez del cargado por MCP, así
que se redirigió `LOCALAPPDATA` del proceso de Navisworks a una carpeta
temporal: **el perfil corporativo no interviene en ninguna de estas pruebas.**

Ese comportamiento cambió después de esta corrida: `navis_load_profile` envía
ahora el perfil al complemento y es el que se aplica (ver «Un solo perfil, de
verdad» en el CHANGELOG). La redirección de `LOCALAPPDATA` sigue siendo la
forma correcta de aislar la prueba, pero ya no es lo único que separa el
perfil corporativo de una corrida de verificación.

| Qué | Evidencia |
|---|---|
| `navis_build_search_sets` en seco | `status=planned`, `requested=2`, `applied=0`, `verification_source=not_applicable` |
| …real | `status=completed`, `verified=2`, `failed=0`, `created=[TMP-ALFA, TMP-ZONA]`, `document_reread` |
| Precedencia del alcance | el mismo criterio bajo `west`/`north`/`east` resuelve `west.nwd`/`north.nwd`/`east.nwd` respectivamente |
| Alcance que no casa | `failed=1`, `requested=0`, con el motivo explícito en `errors` |
| Idempotencia de sets | repetir con `replace_existing=False` → `created=[]`, `unchanged=[TMP-ALFA, TMP-ZONA]`, sin carpetas duplicadas |
| `navis_list_search_sets` | relee lo escrito; `kind` distingue carpeta / explícito / búsqueda; solo una llamada y ninguna escritura |
| `navis_apply_clash_rules` positivo | 5 de 5 en seco, `changed=0` |
| …negativo | otro token de modelo → 0 de 5 |
| …real | `matched=5`, `changed=5`, `verified=5`, `mismatch=0`, `document_reread` |
| …idempotencia | repetir con `only_new=True` → `matched=0`, `changed=0` |
| …reversión | vuelta a `New`, 5 de 5, dentro del documento temporal |
| `navis_run_rules_workflow` | job `queued → completed`; `requested=5`, `applied=5`, `verified=5`, `failed=0`, `document_reread`; pares tomados del perfil (`SET-ZONA VS SET-ALFA`) |
| …idempotencia | repetir → `requested=0`, `applied=0` |
| Autenticación | las cuatro rutas nuevas responden **401** sin token |
| Regresión de `/health` | sigue devolviendo exactamente `ok`, `version`, `pid`, `busy` |
| Regresión de `RefreshSession` | tras `save_as` la huella cambia y el archivo de sesión la sigue |

## Los cuatro arreglos de endurecimiento, dentro de Navisworks (2026-08-14)

Navisworks Manage 2026, add-in público 2026 instalado temporalmente sobre una
copia de `bathcity` en `%TEMP%`, con `NAVISCOORD_OUTPUT_ROOTS` apuntando a una
carpeta temporal. Se habla HTTP directamente con el complemento, no a través
del servidor MCP, para que lo ejercitado sea el binario público recién
compilado. **47 comprobaciones, 0 fallos.**

| Qué | Evidencia |
|---|---|
| Perfil: mismo checksum en ambos lados | Python calculó `e0bca9adfbc48aeb` para el perfil A y el complemento devolvió `e0bca9adfbc48aeb` sobre el contenido recibido |
| El perfil corporativo NO se usa | Sin perfil enviado, `profile/info` responde `profile_not_found`; el perfil de empresa que había junto al DLL nunca aparece, porque no se llama `naviscoord-profile.json` y el complemento público no lo busca |
| `workflow/rules` usa el perfil ENVIADO | Sella el resultado con el checksum de A, y con el de B tras cargar B; `profile_source: explicit_mcp` |
| Cambiar de perfil cambia lo aplicado | Con B, Configurar crea la carpeta `BETA` y el test `BETA VS BETA`; con A no aparece ninguna |
| Checksum mentiroso | `profile_checksum_mismatch`, y sigue vigente el perfil anterior |
| Perfil congelado durante una mutación | `profile/load` y `profile/reset` responden `profile_locked` mientras corre un job; el job recuerda su `profile_checksum` |
| Límite de cuerpo en bytes | 700 000 caracteres `ñ` = 1 400 016 bytes → **413**; el listener sigue respondiendo `health` después |
| Cuerpo truncado | `{"canonical": "sin cerrar` → **400**. Antes daba 200: el lector permisivo lo reparaba y el handler actuaba sobre medio payload. **Este defecto lo encontró esta prueba, no la suite** |
| Junction que escapa | `…\nvc-smoke-out\escapa\robado.nwf` (junction a `…\nvc-smoke-outside`) rechazado nombrando el enlace y el destino real; `applied 0` |
| Ruta normal | Aceptada, `status: planned`, sin errores |
| Cancelación honesta | Un `workflow/configure` en curso responde `cannot_cancel_running`, **sin** marcar `cancel_requested`; repetirlo no inventa otro veredicto |
| `GET /health` anónimo | Exactamente `ok`, `version`, `pid`, `busy` |
| Sin token | 401 en toda ruta autenticada |

Restauración verificada: **29/29 archivos con hash idéntico**, los demás
plugins de terceros de la máquina intactos, ninguna sesión fantasma, y los
temporales de la prueba eliminados por ruta literal.

## Cobertura

No se persigue un porcentaje. La regla es más estrecha y más útil: **toda
corrección de un bug llega con una prueba que falla con el código anterior**.
Si no se puede escribir, se dice en el PR y se explica por qué.
