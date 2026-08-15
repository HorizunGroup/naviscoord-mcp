# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
Este proyecto sigue [SemVer](https://semver.org/lang/es/).

## [0.2.0] — sin publicar

Endurecimiento y paridad sobre 0.1.2, **validado dentro de Navisworks 2026 y
2024**. Minor y no patch porque la política de rutas de salida es un cambio
de comportamiento que rompe llamadas existentes: `navis_pdf_report(path=…)` y
`navis_handoff(directory=…)` ahora rechazan destinos fuera de las raíces
autorizadas hasta configurar `NAVISCOORD_OUTPUT_ROOTS`. Los contratos suben a
`naviscoord.api/2` y `naviscoord.session/2`.

**No se ha creado tag ni release.** Los marketplaces ya apuntan a `v0.2.0`,
que es lo correcto en una rama candidata: el tag se creará sobre este commit y
publicará para siempre el `ref` que este commit contenga, así que el cambio va
antes del tag y no después (ver `docs/RELEASING.md`).

### El artefacto ya no nombra la máquina que lo compiló

- **El DLL publicado llevaba dentro la ruta absoluta de quien lo construyó.**
  El build de Release emitía un PDB, y la entrada CodeView (RSDS) del *debug
  directory* del PE guarda su ruta completa: cada ZIP viajaba con
  `C:\Users\<quien fuera>\…\NavisCoord.pdb` incrustado. El job `higiene
  pública` prohíbe exactamente eso en el árbol, pero solo mira código, y el
  complemento se compila fuera de CI porque necesita el API de Autodesk.

  Release ya no emite símbolos (`DebugType=none`), y `PathMap` normaliza
  cualquier ruta de origen que el compilador quisiera incrustar. Debug queda
  igual. `scripts/Assert-PublicArtifacts.ps1` lo comprueba sobre cada binario
  antes de que entre en un ZIP, y su detección se autoprueba en CI.

- **El mismo commit ahora da los mismos bytes desde cualquier carpeta.** Esa
  ruta incrustada era también lo que hacía irreproducible el build: dos clones
  del mismo commit daban DLL de idéntico tamaño y distinto SHA-256, porque un
  build determinista estampa un hash del contenido en el encabezado PE y el
  contenido incluía la ruta. Además el ZIP se armaba con `Compress-Archive`,
  que graba la hora de modificación de cada archivo —la del checkout— así que
  cambiaba en cada clon. Ahora el ZIP se escribe con orden ordinal, fecha
  derivada del commit (`SOURCE_DATE_EPOCH`), compresión y atributos fijos.
  `Build-Release.ps1 -VerifyReproducible` construye dos veces en rutas
  distintas y compara.

### El perfil publicado usa el esquema vigente

- `naviscoord/profiles/default.json` declaraba `naviscoord.profile/1`, la
  etiqueta anterior a versionar el formato. Se aceptaba sin aviso, pero el
  perfil que el producto reparte era el único ejemplo del formato que pide
  dejar de usar. Pasa a `naviscoord.profile/v1`, y **su checksum cambia** —
  admisible porque `v0.2.0` todavía no existe públicamente. La lectura del
  esquema legacy se mantiene para los perfiles 0.1.x que ya están en campo.
- Los checksums de los dos perfiles que se publican quedan fijados en el lado
  C# contra el valor que calcula Python (`scripts/profile_checksums.py`), así
  que la paridad se prueba sobre los archivos que viajan y no solo sobre un
  vector sintético.

### Un solo perfil, de verdad

- **`navis_load_profile` no llegaba al complemento.** Cargaba el perfil en el
  servidor MCP y ahí se quedaba: `workflow/rules`, Configurar, los search sets
  y la matriz seguían leyendo `naviscoord-profile.json` del disco del
  complemento. Cargar un perfil y correr las reglas aplicaba criterios que el
  llamador acababa de reemplazar, y el resultado no dejaba constancia de cuál
  se había usado. La ronda anterior documentó la divergencia en un docstring
  —«el perfil que usa este paso es el del complemento»— lo que la hizo
  intencional sin hacerla menos peligrosa.

  Ahora el perfil se instala en los dos lados: el servidor valida, canoniza y
  envía el **contenido** (nunca una ruta) por el puerto autenticado, el
  complemento lo revalida con `ProfileSchema` y lo adopta como perfil de esa
  sesión, en memoria y sin escribirlo en disco. Rutas nuevas `profile/load` y
  `profile/reset`; `profile/info` pasa a describir lo que está vigente en vez
  de abrir el archivo que le nombren.

- **Los dos checksums de perfil no coincidían con ninguno.** `state.py`
  hasheaba la salida de `json.dumps`; `ProfileSchema.Checksum` hasheaba un
  recorrido propio con forma `{k:v;}`. Cada archivo llevaba un comentario
  afirmando que reflejaba al otro y ninguno lo hacía para ningún perfil: cada
  lado solo comparaba su salida consigo misma, así que ninguna prueba podía
  notarlo. Ambos emiten ahora la misma forma canónica —claves ordenadas,
  comentarios fuera, cadenas escapadas a ASCII, enteros exactos sin parte
  fraccionaria porque el parser del complemento solo tiene `double`— fijada
  por un vector compartido entre `test_profile_sync.py` y
  `LogicTests.CanonicalFormTests`.

- **Aislamiento por instancia.** El perfil vive en memoria del proceso, así
  que dos Navisworks abiertos a la vez pueden trabajar con perfiles distintos
  sin pisarse. Escribirlo en `%LOCALAPPDATA%` habría hecho justo lo contrario.

- **El perfil queda congelado mientras hay una mutación.** Un job captura el
  checksum al enviarse y lo reporta en su resultado; `profile/load` y
  `profile/reset` responden `profile_locked` mientras corre. Un trabajo que
  empieza con un perfil y verifica con otro no es un resultado.

- **Los botones de la cinta pasan por el mismo almacén**, de modo que un
  perfil cargado por MCP es también el que aplica el botón.

- `navis_reset_profile` (tool nueva) devuelve el complemento a su perfil de
  disco. Total de herramientas MCP: **47**.

### Corregido: seguridad del puente

- **El límite de cuerpo HTTP se contaba en caracteres, no en bytes.**
  `ReadBody` sumaba lo que devolvía `StreamReader`, así que un cuerpo
  multibyte ocupaba varias veces el máximo anunciado antes de que saltara la
  comprobación: un tope de 32 MB que admitía ~96 MB de texto acentuado y hasta
  128 MB con caracteres de cuatro bytes. El listener corre **dentro** de la
  sesión de modelado del usuario, así que esa memoria es la de Navisworks.
  Ahora se lee el stream crudo, se cuentan bytes reales, se decodifica UTF-8
  en modo estricto **después** de acotar, y `profile/load` tiene su propio
  tope de 1 MB. `Content-Length` solo sirve para rechazar temprano: nunca
  dimensiona un buffer. 413 para tamaño, 400 para UTF-8 inválido, cuerpo
  truncado o JSON malformado —que antes llegaba al handler como «sin
  argumentos» y se respondía 200.

- **`PathPolicy` comprobaba prefijos de texto, no contención real.** Crear una
  junction no requiere elevación: cualquiera que pueda escribir dentro de una
  raíz autorizada podía apuntar una carpeta de ahí a `C:\Windows` y todas las
  comprobaciones seguían pasando, mientras `paths.py` —que resuelve de verdad—
  rechazaba la misma ruta. Dos extremos de una misma política discrepando
  sobre qué está permitido. Ahora se resuelve el ancestro existente más
  cercano con `GetFinalPathNameByHandle` (Win32, compatible con .NET Framework
  4.8) y se compara la forma resuelta contra la raíz **también resuelta**, de
  modo que una raíz que sea ella misma un enlace —Documentos redirigido,
  OneDrive— sigue funcionando. La comprobación se repite justo antes de
  escribir, y `overwrite=false` se mantiene en esa segunda pasada.

### Corregido: semántica de cancelación

- **`cancelled` se reportaba con mutaciones ya aplicadas.** `StateFromResult`
  devolvía `cancelled` en cuanto había un cancel pendiente y las unidades no
  llegaban al total, así que un trabajo que había aplicado y **verificado**
  cuarenta de cien renombrados reportaba `cancelled` —que todo llamador lee
  como «no se tocó nada»— con los cuarenta cambios dentro del documento.
  Parar a medias habiendo mutado es `partial`; solo parar sin haber cambiado
  nada es `cancelled`.

- **Se prometía cancelar lo que no se puede cancelar.** `Cancel()` marcaba el
  flag en cualquier trabajo en curso, incluidos los atómicos donde nada lo
  lee, y respondía `cancel_requested`. Ahora la capacidad se declara por ruta
  al enviarse —no se deduce del contador de unidades, que vale 0 en un job
  cooperativo que aún no ha reportado progreso— y el veredicto es explícito:
  `cancelled_before_start`, `cancel_requested`, `cannot_cancel_running` o
  `already_finished`. `capabilities` nombra las rutas realmente cancelables en
  lugar de prometer «los trabajos con unidades».

### Encontrado en Navisworks real (lo que ningún unit test vio)

- **El complemento enviaba `source_file` vacío en todo el export.**
  `ModelItem.Model` devuelve null para muchos nodos reales —lo hizo para los
  diez elementos de los cruces de un federado de cinco modelos— mientras el
  censo, que recorre `doc.Models`, nombraba los mismos archivos sin fallar. La
  consecuencia era total y silenciosa de forma: sin archivo de origen ninguna
  regla de disciplina puede casar, así que el **100 %** de los elementos caía
  en «sin clasificar» y toda la priorización se calculaba contra un único
  cubo. Ahora el nombre se resuelve por índice de modelo, que el `path_id` ya
  lleva en su primer segmento. Lo único que delató el fallo fue el aviso de
  cobertura del etiquetador, que ahora tiene prueba propia.
- **`navis_color_by_priority` no coloreaba nada y no lo decía.** La paleta
  solo cubre crítico, alto y medio; con todos los problemas en «bajo»
  devolvía `applied: []` y ni una palabra, que se lee como un fallo cuando es
  lo contrario. Ahora informa cuántos quedaron en cada banda y por qué.
- **`job/submit` nunca se anunciaba en `capabilities`**, porque lo atiende
  `HttpBridge` y la lista se construía solo desde el `Router`. Efecto total e
  invisible: `require("job/submit")` fallaba siempre, así que **todas** las
  ejecuciones asíncronas se rechazaban con «actualiza el complemento» contra
  un complemento que las soportaba. Los tests no lo vieron porque simulaban
  la lista de capacidades y solo coincidían consigo mismos.
- **El perfil corporativo desplegado no tiene `$schema`** —es anterior a que
  el formato se versionara— y la validación nueva lo **rechazaba**, rompiendo
  «Configurar» en toda instalación existente. Una versión *ausente* ahora
  migra con aviso; una *desconocida* sigue fallando, que es el caso que nadie
  puede migrar.
- **Forma de error inconsistente**: `TargetError` envuelto en `BridgeError`
  se aplanaba y perdía `candidates`, así que la misma condición llegaba al
  llamador de dos formas según dónde se lanzara.
- Escritura exclusiva real (`O_CREAT|O_EXCL` + publicación por renombrado)
  para PDF, CSV, JSON y handoff, con prueba de dos escritores concurrentes.
  Para Save As la API solo acepta una ruta: se documenta que la ventana no es
  cero en vez de fingir una garantía.
- `_is_number` ceñido a un decimal estricto (`float()` aceptaba `inf`, `nan`,
  `1_0` y les saltaba la neutralización CSV).
- `installer/Build-Stage.ps1` y `scripts/Smoke-AddinSwap.ps1` corregidos: un
  backup vacío se aprobaba como válido, y `-Versions 2026,2024` llegaba como
  una sola cadena.

### Corregido en la revisión de integración

Defectos encontrados revisando la propia implementación, no reportados desde
fuera:

- **El timeout del dispatcher no acotaba la ejecución.** Solo limitaba la
  admisión a la cola; después, `Control.Invoke` bloqueaba sin plazo. Con un Run
  All ocupando el hilo de UI veinte minutos, una petición con 120 s de plazo
  esperaba los veinte — y cuando el cliente HTTP se rendía, el trabajo seguía
  encolado y **mutaba el documento después**, sin nadie escuchando. Ahora se
  publica con `BeginInvoke`, se espera con el plazo real, y lo abandonado se
  marca y no llega a ejecutarse. Lo que ya había empezado se declara como tal
  en vez de fingirse cancelado.
- **`job/submit` bloqueaba 20 s durante un trabajo**: pedía la huella al
  documento antes de comprobar la colisión, justo desde la ruta cuyo propósito
  es responder de inmediato. La colisión se comprueba primero.
- **`document/save_as` anunciaba `idempotency_key` y no la consultaba.** Un
  reintento reescribía el archivo, y con `overwrite=false` fallaba con «ya
  existe» contra el archivo que la primera llamada acababa de crear — que se
  lee exactamente como un guardado fallido.
- **Una `idempotency_key` en vuelo creaba un segundo trabajo.** El ledger solo
  registra resultados terminados; ahora un reintento durante la ejecución
  devuelve **el mismo** trabajo.
- **Cuerpo HTTP sin límite.** `ReadToEnd` sobre un stream sin cota permite
  agotar la memoria del proceso de Navisworks. Tope de 32 MB, con 413 y 400
  legibles en vez de una excepción sin manejar en el hilo del listener.
- **Porcentaje sin acotar.** `ToJson` y `Progress` corren en hilos distintos;
  una lectura mezclada podía reportar 130 %.
- **Byte de control 0x1F crudo** en `MutationContract.cs` y **0x01** en
  `SchemaHandlers.cs` (este último ya commiteado). Hacían que git clasificara
  el archivo como binario, lo que lo exime en silencio de la normalización a
  LF de `.gitattributes` y de todo diff textual. Ambos son ahora constantes
  documentadas con escape visible.
- **La huella de documento se construía con `string.Join("")`**, de modo que
  título «AB» con un modelo y título «A» con un modelo «B» producían la misma
  huella. Ahora lleva separador.
- **La suite C# escribía en el `%LOCALAPPDATA%\NavisCoord` real** del
  desarrollador, incluido `session.json`. La causa estaba corregida; ahora la
  prueba **aborta** si detecta que el aislamiento no está activo, en vez de
  comprobarlo y continuar.
- **TOCTOU en la escritura del PDF**: entre autorizar el destino y escribirlo
  transcurren minutos pidiendo imágenes a Navisworks. La decisión de
  sobrescritura se reconfirma justo antes de escribir.
- **`_is_number` usaba `float()`** para decidir qué valores saltan la
  neutralización CSV, y aceptaba `inf`, `nan` y `1_0`. Ceñido a un decimal.
- **El manifiesto de Codex se había cambiado a ruta relativa** por analogía
  con el nombre de la variable. La instalación local de Codex demuestra lo
  contrario: `${CLAUDE_PLUGIN_ROOT}` es el marcador de raíz también en Codex.
  Revertido, con la evidencia en las pruebas.
- **`installer/build-installer.sh` no existía** pese a estar referenciado.
  Sustituido por `scripts/Build-Release.ps1`, que además copia LICENSE,
  NOTICE y el perfil de ejemplo dentro de cada paquete del add-in y
  **preserva** cualquier `naviscoord-profile.json` ya presente en el destino.
- Código muerto (`entropy_group`) e imports sin uso eliminados.

### Seguridad

- **Markup no confiable en el PDF.** Todo texto procedente del modelo o del
  perfil se escapa antes de entrar en un `Paragraph` de ReportLab. Un tipo
  llamado `<img src="file:///C:/Windows/win.ini">` era una petición de embeber
  un archivo local; ahora es texto. El formato interno se genera con helpers
  seguros en vez de concatenar valores externos dentro de markup.
- **Confinamiento de rutas de salida.** Política central en Python y C#: PDF,
  handoff, CSV, JSON, export y `save_as` solo escriben bajo raíces
  autorizadas. Se rechazan traversal, UNC, rutas de dispositivo, nombres
  reservados de DOS, flujos alternos NTFS y bytes nulos. `overwrite=false` por
  defecto. Se configura con `NAVISCOORD_OUTPUT_ROOTS`, nunca por argumento.
- **Inyección de fórmulas en CSV.** Los valores que empiezan por `=`, `+`,
  `-`, `@`, tabulador o retorno de carro se neutralizan en todo CSV exportado,
  cabecera incluida. Los números —incluidos los negativos— quedan intactos.
- **Protección del archivo de sesión.** DACL explícita (usuario actual, SYSTEM,
  Administrators), herencia desactivada, escritura atómica con la DACL aplicada
  antes del contenido, auditoría de permisos al arrancar. 0700/0600 en POSIX.
- **`GET /health` sin autenticar es mínimo**: ok, versión, PID, session_id,
  puerto y si hay documento. Ya no expone ruta ni título del documento. El
  detalle completo vive en `POST /health`, con token.
- El listado de sesiones **redacta el token** de las entradas ajenas.

### Corregido

- **Color por responsable pintaba el lado equivocado.** `discipline_pair` está
  ordenado alfabéticamente, y el lado a colorear se infería comparando el
  responsable contra su último elemento — acertaba solo cuando la disciplina
  responsable ordenaba última. Ahora se resuelve por la disciplina del propio
  elemento (`Issue.elements_by_discipline`), robusto incluso cuando dentro de
  un clúster el orden A/B de Navisworks varía entre choques.
- **`sleeves_found=0` con pasamuros presentes.** El filtro de ruido descarta
  correctamente el cruce entre un pasamuro y el tubo que lo atraviesa, y el
  detector de causa raíz solo veía los choques supervivientes — así que
  reportaba «el modelo no tiene un solo paso definido» al 0,9 de confianza
  sobre modelos llenos de ellos. Los elementos de penetración se indexan
  aparte y viajan al detector.
- **Precedencia de reglas de search sets.** Regla por archivo > regla por
  categoría > fallback, con el orden de declaración del perfil como desempate.
  Antes era un OR dentro de un `foreach` sobre un `Dictionary`: la disciplina
  ganadora dependía del orden de iteración del hash.
- **Estado global sobrevivía al cambio de documento.** El análisis, la clave de
  agrupación y los roles persistían al cerrar un modelo y abrir otro, y el
  siguiente `navis_apply_groups` escribía un plan calculado para un edificio
  dentro de otro. El estado va atado a `(target_id, huella, checksum de
  perfil)` y lo derivado se descarta cuando cualquiera cambia.
- **`discover` sin recomendación dejaba la clave anterior.** Ahora se limpia:
  una clave heredada hace que todos los sets vuelvan vacíos y la corrida
  reporte un proyecto limpio.
- **El modo degradado del launcher lanzaba `NameError`.** Llamaba a un
  `lib_dir()` inexistente, así que el único camino cuyo trabajo es explicar un
  fallo se rompía en vez de explicarlo.
- **Detección de runtime incompleta.** `import mcp` bastaba; una instalación
  con `mcp` pero sin `reportlab` pasaba el chequeo y fallaba en el primer PDF.
  Ahora se comprueban todas las dependencias y la versión mínima de Python.
- **Un operador de búsqueda con typo caía en `default`** y se convertía en un
  `equals` silencioso: un set que debía excluir algo lo incluía todo. Ahora se
  rechaza nombrando los válidos.
- **`clash/apply_rules` reportaba `edited` sin verificar.** Ahora relee el
  documento y reporta `verified_edited`.

### Añadido

- **Registro de sesiones multiinstancia.** Un archivo por PID en
  `%LOCALAPPDATA%\NavisCoord\sessions\`. Cada instancia gestiona solo el suyo;
  cerrar una ya no borra la sesión de otra. Limpieza por liveness de PID con
  detección de PID reciclado. `session.json` se conserva como puntero de
  compatibilidad.
- **Targeting**: `navis_sessions`, `navis_target`, `navis_current_target`. Con
  varias instancias y sin target elegido, **una mutación se rechaza**; una
  lectura toma la más reciente y lo dice.
- **Trabajos asíncronos**: `job/submit|status|list|cancel` y
  `navis_job_status`, `navis_jobs`, `navis_cancel_job`. El listener responde
  concurrentemente, así que `health` y `job/status` siguen vivos mientras un
  trabajo ocupa el hilo de UI. Progreso por unidades cuando las hay e
  `indeterminate` cuando la API es atómica. Cancelación solo donde es segura.
- **Contrato uniforme de mutación**: `operation_id`, `job_id`, `target_id`,
  huellas antes/después, `requested/applied/verified/failed`, `status`,
  `verification_source`, `warnings`, `errors`. El estado se **deriva**: un
  handler que no verificó reporta `failed`, nunca `completed`.
- **Idempotencia**: `idempotency_key` en las mutaciones; un reintento devuelve
  el resultado original sin volver a tocar el documento.
- **Paridad add-in/MCP**: los cuatro pasos operativos como herramientas —
  `navis_audit_models`, `navis_configure`, `navis_run`, `navis_group_levels` —
  ejecutando los **mismos servicios** (`CoordinationWorkflow`) que los botones
  de la cinta. La cinta ahora solo renderiza a prosa lo que devuelven.
- **`navis_capabilities`**: versión de contrato, rutas, operaciones, versión de
  Navisworks, capacidades de save/save_as, soporte de trabajos y versión de
  esquema de perfil. Una ruta ausente produce «actualiza el complemento a esta
  versión» en vez de `unknown_route`.
- **Guardado seguro**: `document/save`, `document/save_as`, `navis_save`,
  `navis_save_as`. Huella obligatoria, política de rutas, `overwrite=false`,
  solo `.nwf`/`.nwd`, y verificación releyendo (existencia, bytes, marca de
  tiempo, ruta del documento, flag de modificado). El caso `.nwfacc` de ACC se
  rechaza con la razón y la alternativa.
- **Esquema único de perfil** `naviscoord.profile/v1`, con validación
  estructural que reporta todos los problemas, checksum de identidad en estado
  y resultados, e invalidación de lo derivado al cambiar de perfil.
  `navis_profile_info` valida sin aplicar.
- **`navis_output_policy`**: qué raíces están autorizadas y cómo añadir otra.
- `NOTICE` con los avisos de Autodesk y de terceros; `LICENSE` queda como MIT
  canónica.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `SUPPORT.md`,
  `CHANGELOG.md`, guía de release, plantilla de PR y formularios de issue.
- `docs/`: arquitectura, modelo de seguridad, sesiones, trabajos, guardado,
  perfiles, instalación y pruebas.
- `scripts/build_artifacts.py`: construye wheel y sdist y **verifica leyendo
  los archivos** que llevan LICENSE, NOTICE, el perfil y el servidor dentro.

### Cambiado

- Versiones unificadas a `0.1.2` en `pyproject.toml`, `__init__.py`, ambos
  `plugin.json` y ambos `.csproj`.
- Los marketplaces apuntan al tag `v0.1.2` en vez de a `main`: instalar desde
  una rama significa que dos personas obtienen código distinto el mismo día.
- El manifiesto de Codex ya no usa `${CLAUDE_PLUGIN_ROOT}`, que solo define
  Claude Code.
- La raíz de runtime se resuelve leyendo `%LOCALAPPDATA%` en **ambos** lados;
  antes C# usaba `SpecialFolder` y Python la variable, y divergían justo en
  perfiles redirigidos, Citrix y cuentas de servicio.
- CI: pruebas C#, wheel/sdist, instalación desde wheel, validación de
  manifiestos, coherencia de versiones, comprobación de LICENSE/NOTICE en los
  artefactos y build del complemento para 2024/2025/2026.

### Paridad add-in/MCP cerrada

Cuatro rutas que el complemento servía y anunciaba llevaban todo el ciclo de
0.2.0 sin herramienta MCP delante: la cinta ofrecía el paso de reglas y la
lista de capacidades decía `clash_rules`, pero quien manejaba el servidor no
podía ejecutarlo. Ahora cada una tiene su tool tipada:

| Tool | Ruta |
|---|---|
| `navis_build_search_sets` | `sets/build_search` |
| `navis_list_search_sets` | `sets/list` |
| `navis_apply_clash_rules` | `clash/apply_rules` |
| `navis_run_rules_workflow` | `workflow/rules` |

`sets/build` y `sets/build_search` **no** son duplicados y ambas siguen
expuestas: la primera congela qué elementos se probaron, para que dos corridas
de la matriz sigan siendo comparables; la segunda guarda la regla, para que la
plantilla sobreviva a una actualización del federado. Tampoco lo son
`clash/apply_rules` y `workflow/rules`: los pares vienen del llamador en una y
del perfil en la otra.

Las tres mutantes exigen destino inequívoco y `expected_document_fingerprint`,
negocian capacidades antes de llamar, aceptan `idempotency_key`, pasan por el
sistema de trabajos y devuelven el envelope uniforme. Como `sets/build_search`
y `clash/apply_rules` responden con su propio payload en vez del
`MutationResult` del complemento, el envelope se arma en Python a partir de una
**relectura del documento** —nunca de que la llamada no lanzara— y
`test_the_status_table_matches_the_addin` comprueba que la tabla de estados sea
la misma de `MutationContract.cs`.

`navis_list_search_sets` es solo lectura y no acepta huella ni destino. Para un
search set devuelve `item_count: null` en vez de cero: el conjunto guarda la
regla, no la lista, y un cero se leería como «vacío».

La prueba que fijaba el hueco (`test_the_mcp_gap_is_exactly_the_documented_one`)
se eliminó —consagraba el defecto como resultado esperado— y la sustituye
`test_every_route_the_addin_serves_is_reachable_from_mcp`, que falla si aparece
cualquier ruta servida sin tool.

Total de herramientas MCP: **47** (eran 42): 46 de paridad más
`navis_reset_profile`.

### Corregido en la secuencia de release

`docs/RELEASING.md` decía «crear el tag y después actualizar los `ref` de
marketplace». Como un tag es un puntero a un commit, eso produce un `v0.2.0`
que contiene `"ref": "v0.1.2"`: quien instala desde el tag nuevo se lleva el
código viejo, y nada falla hasta que alguien compara versiones a mano. El
cambio de `ref` pasa a formar parte del commit de release, antes del tag, y se
añade un paso que lee el `ref` **desde dentro del tag** para comprobarlo.
`TestMarketplacePins` distingue ahora las dos invariantes: en desarrollo el
`ref` debe ser un tag que exista; en release, exactamente `"v" + versión`.

### Conocido y sin cerrar

- **El color aplicado no se puede releer**: la API de Navisworks 2026 expone
  `OverridePermanentColor` y `Reset*` pero **ningún** getter de apariencia en
  `ModelItem` (comprobado por reflexión sobre el ensamblado). La verificación
  llega hasta que cada ruta pedida resuelve a un elemento vivo; que el píxel
  cambie no es comprobable por API y no se declara como verificado.
- El renombrado de choques con nombre por defecto no actúa cuando ambos lados
  carecen de nombre visible: renombraría a «001 · vs ». Se reporta como
  `attempted: 0` con `still_default: N`, no como éxito.

## [0.1.2] — 2026-08-04

- Soporte de `mcp` 2.x junto a 1.x, y pruebas contra ambos.

## [0.1.1] — 2026-08

- Correcciones de empaquetado.

## [0.1.0] — 2026-08

- Primera versión: motor de análisis, add-in de Navisworks, servidor MCP.
