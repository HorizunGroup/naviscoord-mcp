# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
Este proyecto sigue [SemVer](https://semver.org/lang/es/).

## [Unreleased]

## [0.4.1] — 2026-08-20

**Los conteos cambian.** Estos arreglos del motor se escribieron antes de
0.4.0 pero no entraron en esa release: el 0.4.0 publicado es el de la línea
de seguridad e instalación. Salen aquí. Un informe archivado antes de esta
versión sobrestima lo limpio que estaba el modelo, y conviene volver a
correrlo.

Menor, no parche: los conteos cambian. Sobre el modelo con el que se encontró
esto, los mismos 1.911 cruces crudos pasaron de 397 problemas y 18 críticos a
1.060 y 36. Ninguna herramienta cambió de forma; lo que cambió es cuánto del
modelo llega a mirarse. Un informe archivado antes de esta versión sobrestima
lo limpio que estaba el modelo, y conviene volver a correrlo.

Los tres fallos son el mismo error de fondo: **el motor no distinguía «no
encontré nada» de «no miré»**, y en los tres casos el resultado se leía como
buena noticia.

### Fixed

- **Un test entero de estructura contra arquitectura se descartaba como
  «arquitectura consigo misma».** La disciplina salía de la categoría de
  Revit, donde un muro estructural y uno arquitectónico son ambos `Walls`.
  Los dos lados caían en ARQ — que es justo la condición que
  `architectural_self_overlap` usa como salvaguarda— así que la regla se
  disparaba sobre el test que existía para separarlos y se comía sus 477
  cruces. El par seguía reportándose limpio. El nombre del test y los search
  sets de cada lado, que dicen explícitamente qué es cada mitad, se parseaban
  y no los usaba ningún módulo: ahora mandan sobre la categoría
  (`analysis/declared.py`). En el modelo de referencia el tagging pasó de 415
  a 1.808 elementos en estructura y los elementos sin clasificar del 16% a 0%.
- **`Generic Models` se leía como prueba de un pasamuros modelado.** Es el
  cajón de sastre de Revit: cabe un pasamuros y cabe un panel de fachada. Los
  255 «pasos» que el motor decía encontrar eran fachada —246 se llamaban
  `Grey RAL 9006`, un color RAL— y hacían dos daños a la vez: vaciaban un test
  de 700 cruces como `designed_pass_through`, y le daban a la causa raíz más
  grande su evidencia de que el proyecto sí modela pasos, invirtiendo lo que
  esa conclusión le decía al lector. Una categoría específica (`Shafts`,
  `Sleeves`, `Openings`) sigue bastando sola; una genérica tiene que
  corroborarse por el nombre.
- **El veredicto no sabía cuánto de la matriz se había corrido.** `apto: no
  hay interferencias que frenen obra` se emitía con `critical == 0` sin
  preguntar nada más, así que un modelo cuyos tests nunca se ejecutaron daba
  el mismo cero que uno limpio. Sin cobertura completa el veredicto es ahora
  `sin evidencia suficiente` y nombra el vacío.

- **El encuadre apuntaba a una caja de choque que no era el choque.**
  `ClashVolume` confiaba en `ClashResult.BoundingBox`, que mide otra cosa: la
  extensión del resultado entero, no la del solape. Sobre un cruce real entre
  un tubo de cobre de 54 mm y una losa reportó **8,5 × 11 m**. De ahí en
  adelante todo funcionó correctamente sobre un dato falso — el recorte al
  vecindario encuadró fielmente un choque de once metros, la cámara retrocedió
  a 35 m y el tubo salió al 0,02% de la imagen, que el verificador rechazó con
  razón. La intersección de los dos elementos ES la interferencia, y ahora se
  calcula; lo reportado solo se consulta en tests de holgura, donde no hay
  intersección, y solo si podría ser un volumen de choque (una interferencia
  no puede ser mayor que el menor de los dos elementos).
- **El mínimo de encuadre se aplicaba dos veces.** `minExtent` era el suelo de
  la caja del choque y otra vez del alcance del vecindario, así que un cruce de
  54 mm se ensanchaba a 0,8 m y luego recibía 0,8 m de aire por cada lado: 2,4 m
  antes de márgenes. Ahora el alcance es proporcional al vano y nada más.
- **Una foto podía rechazarse por tener pocos colores.** `blank` contaba
  cubetas de color, y aislar geometría existe justamente para dejar dos
  elementos pintados contra el cielo — así que cuanto más limpia la toma, menos
  colores tiene. La vista desde abajo de un tubo colgando bajo una losa, que es
  la única que muestra ese cruce, volvía con tres cubetas y se descartaba como
  fotografía de nada. Encontrar la pintura del sujeto ahora zanja la cuestión;
  cuánto se ve lo deciden las comprobaciones por lado, que ya existían.

- **El 45% del informe eran persianas de ventana.** 605 elementos
  `WIN_BLIND_7'8"_BLACKOUT/SCREEN` archivados bajo `Generic Models` rozaban su
  propio muro de concreto 6 mm de mediana y sobrevivían a todas las reglas:
  `designed_adjacency` decide por categoría de Revit y una persiana no está en
  ninguna. Eran el 69% del trabajo asignado a arquitectura. Una categoría
  cajón de sastre puede ahora identificarse por su nombre, con la misma red
  que el resto de la regla: dos anfitriones, especialidades distintas, menos
  de 25 mm. Problemas 847→466, arquitectura 571→190, cola baja 644→267;
  **los 27 críticos no se mueven**.
- **«Cota sistemática» medía el diámetro del tubo, no la cota.** El detector
  toma el solape en Z de las dos cajas y lee una dispersión apretada como
  prueba de que un recorrido está a la altura equivocada. La misma aritmética
  da una dispersión apretada en dos geometrías que no son errores de cota, y
  en el modelo de referencia eran **las once**: un ramal horizontal cruzando
  un muro reporta su propio diámetro —idéntico en 67 de 67 cruces, publicado
  al 88% y 95% de confianza— y un montante vertical atravesando una losa
  reporta el espesor de la losa, 177,8 mm con desviación exactamente cero. La
  acción sugerida, bajar el recorrido 50 mm, no podía funcionar: 40 de los 54
  anfitriones eran muros y columnas verticales, donde bajar solo mueve el
  agujero. Ahora la causa exige que el recorrido asome por fuera de lo que
  cruza, medido contra ambas cajas. Los cruces siguen reportados; lo que se va
  es la explicación falsa.
- **Encontrar cero pasamuros subía la confianza a 0,90.** Un paso solo llega
  al export si choca dentro de alguno de los tests configurados, y una matriz
  de estructura contra instalaciones no tiene ningún conjunto que pueda
  contener uno — así que el cero es lo que devuelve el export tanto si el
  proyecto modela pasos impecablemente como si no modela ninguno. La frase «el
  modelo no tiene un solo paso definido» era una afirmación sobre los tests.
- **`"complete": true` sobre 3 de las 16 parejas que el perfil exige.** La
  cobertura contaba los tests que existen, que responde «¿corrió todo?» y no
  «¿se miró todo?». Los 13 tests llevaban estructura en el lado A sin
  excepción: ni arquitectura contra instalaciones, ni instalación contra
  instalación, ni una sola de eléctrica. Y el veredicto solo consultaba sus
  puntos ciegos cuando los críticos eran cero, como si encontrar algo probara
  que la búsqueda fue exhaustiva; ahora los consulta siempre.
- **`total_elements` contaba lados de cruce, no elementos.** Un elemento
  aparece en tantos cruces como colisiones tenga, así que un modelo de 2.486
  elementos declaraba 3.822 — y todas las proporciones construidas sobre ese
  total venían infladas, incluida la que decide si un archivo «mezcla
  disciplinas». `resolved_by` se cuenta ahora en la misma escala.
- **Un mapa de pisos adivinado se leía como un mapa de pisos.** Cuando los
  cruces no traen nivel, cada uno se empareja con el piso más cercano por
  cota — y esas cotas son, a su vez, la mediana de la altura de los cruces que
  sí traían nivel: una estimación medida contra otra estimación. Con el 57%
  inferido el aviso ahora dice que el mapa es una inferencia y que los
  conteos por nivel, las zonas calientes y «repetido en N niveles» heredan el
  error. `Layer`, que es de donde sale el piso, pasa a ser propiedad base del
  add-in para que ningún llamador la pierda por no saber pedirla.
- **Una etiqueta que abarcaba seis plantas se fusionaba con un piso.**
  `ARC_2ND` llevaba 133 cruces repartidos entre 4,7 y 24,6 m —no es un piso,
  es una agrupación de arquitectura—, pero resumida por su mediana caía a
  15,5 m, a centímetros de «5 FLOOR». Las dos se fusionaban y 133 cruces
  desperdigados por la torre se reportaban en el quinto piso. Una etiqueta se
  acepta como piso solo si sus elementos caben en una altura de planta, medida
  entre el percentil 10 y el 90 —un cruce suelto 13 m por debajo no puede
  descalificar una planta real— y contra el ritmo de entrepiso del propio
  edificio, no contra una constante: un piso abarca legítimamente una planta
  entera, porque un cruce puede estar en la losa o contra el cielo. Los cruces
  de una etiqueta transversal se ubican uno a uno por su propia cota.
- La narrativa describía como «contactos por debajo de la tolerancia útil» a
  los 510 tabiques que mueren contra un muro, porque `designed_adjacency` no
  estaba en su lista de descartes por diseño.
- El aviso por test saltaba solo al 100%: un test que perdía el 93,7% pasaba
  en silencio. Ahora salta desde el 90% y dice cuántos cruces quedan.

- **Una interferencia profunda y solitaria caía a la banda baja.** Dos de los
  seis componentes de severidad premian la repetición y la compañía, así que
  un cruce que ocurre una sola vez tiene que ganar con los otros cuatro y no
  puede: quedaban 26 problemas en la banda más baja con más de 150 mm dentro
  de estructura, uno con 194. Son los mismos cruces que hubo que rescatar del
  filtro de ruido, y rescatarlos para que la puntuación los enterrara no es
  rescatarlos. A partir de 150 mm contra un lado que no se mueve, la banda no
  puede bajar de ALTO. Contra algo reruteable la profundidad es una molestia;
  dentro de estructura es una decisión antes de vaciar.
- El barrido de propiedades subía solo cuatro ancestros, y un árbol de Revit
  pone los nodos que describen un elemento cinco o seis saltos por encima: seis
  lados llegaban sin ninguna propiedad y 453 perdían su `Mark`. Es también por
  qué la misma persiana llegaba como `Type: Solid` desde un documento y como
  `Type: WIN_BLIND_…` desde otro. Las reglas de ruido leen ahora todos los
  nombres del elemento —tipo, familia, nombre y el de su padre— en vez de uno
  solo, que funcionaba en un export y dejaba de funcionar en el siguiente.

### Added

- **Fixture de un modelo real en la suite.** Los nueve fallos de esta versión
  aparecieron en datos reales y todas las pruebas eran sintéticas, escritas a
  partir de la forma del fallo cuando alguien ya lo había visto. Ese orden es
  el problema. `tests/fixtures/torre-demo-export.json` son 90 cruces recortados
  de un modelo de coordinación real, anonimizados, con un ejemplo trabajado de
  cada trampa: muros de dos especialidades que comparten categoría, persianas
  bajo `Generic Models`, un ramal cuyo solape es su diámetro, un montante cuyo
  solape es el espesor de la losa, una etiqueta que abarca seis plantas, e
  interferencias profundas que tienen que sobrevivir a todo lo anterior. Las
  aserciones son de comportamiento, no de conteos exactos.
- `distinct_families` viaja en la evidencia de las causas de tipología
  repetida. El agrupamiento es posicional —par de disciplinas y celda de medio
  metro en planta— pero la frase que publica, «es un detalle tipo», es una
  afirmación sobre los elementos, y nada la comprobaba. Contra el modelo real
  las 28 causas dieron entre una y tres familias, así que el detector tenía
  razón; queda como guarda para que la afirmación sea comprobable y no creída.
- Modo de cámara **`underside`**: la misma toma desde el otro lado del
  obstáculo. Todos los demás modos miran hacia abajo, lo cual es correcto casi
  siempre y desesperado en el caso más común de todos —una instalación bajando
  por una losa—, donde el obstáculo es un plano horizontal entre la lente y el
  tubo. Abrir el plano no sirve (una vista más amplia del dorso de una losa
  sigue siendo el dorso de una losa) y aislar tampoco, porque quien tapa es la
  otra mitad del choque. La escalera de reintentos lo usa tras aislar y abrir,
  y salta `plan` en esa ruta a propósito: mirar en picado es la única vista
  garantizada a fallar cuando lo que se busca cuelga por debajo.
- Advertencia **por test** cuando el filtro de ruido lo vacía por completo.
  La única que había medía una razón contra el total de la corrida, y un test
  de trece borrado entero es un 8% que nunca llega al umbral. Es la que
  encontró sola el segundo fallo de esta lista. `emptied_tests` va también en
  el payload de `navis_analyze`.
- Detección de **declaraciones contradictorias**: cuando dos tests ponen el
  mismo elemento en especialidades distintas se reporta el conflicto en vez
  de resolverlo por orden de lectura. Es el precio de dejar que la matriz
  mande sobre la categoría, y se declara en lugar de asumirse.
- El add-in exporta `selection_a` / `selection_b` por test: los nombres de los
  search sets de cada lado, que son la señal exacta y no dependen de cómo se
  tituló el test. El servidor cae al nombre del test cuando habla con un
  add-in anterior, así que la corrección funciona sin actualizar el complemento.

### Added

- **Anotaciones en las 50 tools**: cada una declara su `title` y su hint de
  solo lectura o destructivo. No es metadato decorativo — el cliente decide
  con ellos qué ejecuta sin preguntar y qué exige confirmación del usuario,
  así que un hint optimista es una escritura que nadie confirmó. Dos pruebas
  lo sostienen: una exige la anotación, y la otra falla si una tool que acepta
  `expected_document_fingerprint` o `dry_run` se declara de solo lectura.
- **Política de privacidad** (`docs/PRIVACY.md`, con su sección en el README),
  escrita leyendo el código y no la intención. Por eso dice dos cosas que el
  README no decía: los resúmenes que devuelven las tools viajan al proveedor
  del modelo a través del cliente MCP, y el launcher abre una conexión a PyPI
  cuando aprovisiona su entorno.
- **`server.json`** para el registro oficial de MCP, y el marcador `mcp-name`
  dentro del README, que es donde ese registro verifica la propiedad del
  paquete — viaja en la descripción que PyPI publica.
- **Empaquetado como bundle de escritorio** (`scripts/build_mcpb.py`), única
  puerta de un servidor local al directorio de conectores, con la lista de
  tools leída del servidor vivo en vez de escrita a mano. El icono se genera
  con código (`scripts/make_icon.py`) sobre el `brandColor` ya declarado.
- **Validación de argumentos por parámetro**: el rechazo ocurre antes de
  llamar a nada, así que un argumento inválido no toca el puente ni el estado
  en memoria, y el error dice cuál falló. Los perfiles se rechazan al leerlos
  —truncado, NaN, clave duplicada, cuerpo enorme— en vez de convertirse en un
  `Profile` del que ya nadie puede fiarse.
- **Contrato y sincronización de perfil** entre servidor y complemento, para
  que no discrepen en silencio sobre cuál está vigente.
- **Aprovisionamiento del runtime del plugin** con lock e identidad, que es lo
  que permite arrancar en una máquina que no preparó nadie.
- **Add-in**: admisión de trabajos, contratos de ruta, verificación de la
  corrida y del guardado, ledger de apariencia, planificación de configure y
  de clash, JSON estricto y seguridad de la sesión, cada uno con sus pruebas.

### Changed

- La ficha que publican los directorios pasa a inglés, que es quien la lee;
  las tools se siguen describiendo en español, que es el idioma de quien
  trabaja con ellas.
- `docs/PUBLISHING.md` documenta las tres puertas de publicación y lo que
  exige cada una.


## [0.4.0] — 2026-08-20

Esta versión combina la superficie visual y operativa de 0.3.1 con una
revisión profunda de seguridad, consistencia del análisis, instalación y
presentación pública. Cambian varias firmas mutantes al añadir `dry_run`,
fingerprint e idempotencia; por eso es una versión menor y no un parche.

### Added

- Instalador de releases para principiantes con detección de Navisworks
  2024–2026, verificación de `SHA256SUMS.txt`, validación estricta del ZIP,
  publicación atómica y rollback.
- README bilingüe, quick starts, benchmark público, plan de lanzamiento,
  `llms.txt`, imagen social y metadatos de distribución.
- Bundles de handoff publicados bajo lock, manifest de ejecución, staging y
  rollback del conjunto completo.
- Pruebas estructurales para handles de Clash frescos y publicación semántica
  de Selection Sets sin ventana `Remove` + `AddCopy`.

### Fixed

- Las causas raíz y los paquetes usan identidades estables después de ordenar
  las incidencias; una causa ya no recibe decisiones de otro cluster.
- Los paquetes pueden solaparse como contexto, pero cada decisión tiene un
  único dueño determinista y no se cuenta dos veces.
- El límite espacial de clustering se cumple incluso cuando DBSCAN devuelve
  una sola partición sobredimensionada.
- Perfiles con `NaN`, infinitos, tipos incompatibles o parámetros que provocan
  divisiones por cero se rechazan antes del motor.
- Las mutaciones heredadas, selección, apariencia y guardado exigen identidad
  explícita, empiezan en `dry_run` y retornan el envelope común.
- La admisión de trabajos reserva documento e idempotencia atómicamente y
  congela payload y perfil antes de entrar en cola.
- El parser HTTP aplica JSON estricto y profundidad acotada; errores de dominio
  reciben códigos HTTP coherentes.
- Los wrappers de `ClashResult` y `ClashTest` nunca sobreviven a una mutación
  del árbol; cada edición re-resuelve el GUID en la generación vigente.
- Instalación, release, checks de artefactos y launcher endurecidos contra
  copias parciales, runtimes incompatibles, carreras y rutas no autorizadas.

### Changed

- El posicionamiento público pasa de “control MCP” a inteligencia de
  coordinación: clashes → incidencias → causas raíz → paquetes verificables.
- CI cubre Python 3.10–3.14, mínimos declarados, tags de release, dependencias
  hash-locked y validaciones de packaging en Windows y Linux.


## [0.3.1] — 2026-08-16

Parche: ninguna herramienta ni ruta cambió de forma, y los argumentos que se
añaden son opcionales. Sí cambia el comportamiento en un caso concreto y a
propósito: con dos instancias de Navisworks abiertas, cuatro herramientas que
antes escribían en la que Navisworks hubiera tocado último ahora se rechazan
si nadie eligió documento. Una llamada que dependía de esa elección implícita
falla en vez de acertar por casualidad, que es el punto.

### Fixed

- **Cuatro herramientas escribían sin comprobar en qué documento.**
  `navis_build_sets`, `navis_build_clash_matrix`, `navis_run_tests` y
  `navis_reset_appearance` no llamaban `require_mutable()`, así que la sesión
  se resolvía por la vía de lectura — y esa vía, con dos instancias abiertas,
  **elige la más reciente y no lo dice** (`sessions.py`). No había error: los
  tests de clash aparecían en el modelo que Navisworks hubiera tocado último.
  Las cuatro aceptan ahora `expected_document_fingerprint` y devuelven
  `document_fingerprint_before`, como el resto de las mutaciones. El ensayo
  también se rechaza: un `dry_run` que cuenta elementos en Torre B es el
  número que una persona lee antes de aprobar la escritura en Torre A.
- `install.ps1` comprueba **todo** lo que va a copiar antes de copiar nada: el
  DLL se instalaba primero y la cinta se verificaba después, así que un build
  sin XAML dejaba el complemento a medias —DLL cargado, pestaña ausente, cero
  errores— que es justo lo que el script existe para no producir.
- `install.ps1 -Uninstall` funciona aunque ya no quede Navisworks instalado:
  el complemento vive en `%APPDATA%` y sigue ahí, pero el script exigía una
  instalación del producto antes de dejar borrarlo. Además, desinstalar UNA
  versión ya no borra el registro de sesiones, que es común a todas.
- `Verify-SessionAcl.ps1`: la comprobación de herencia estaba escrita
  `-not $x -eq $false`, que PowerShell evalúa como `(-not $x) -eq $false` —o
  sea, `$x`, la misma comprobación de la línea siguiente. Ahora una mira el
  flag y la otra que no haya quedado ninguna regla heredada. El criterio de
  identidad de confianza era una alternancia de subcadenas, así que un grupo
  llamado `NotAdministrators` pasaba; ahora el nombre tiene que ser el
  componente completo.
- **Un paso que fallaba podía decir que no sabía por qué.** El veredicto que
  cierra el texto de la cinta solo miraba `errors`, así que una corrida que
  falla con la causa en `warnings` terminaba en «✖ Falló: sin detalle» dos
  líneas debajo de la frase que daba el detalle. Pasa de verdad: un test de
  clash con las selecciones vacías no llega a correr, Navisworks lo deja en
  «New» y `workflow/run` falla —bien— en vez de reportar cero resultados en
  verde. Ahora el cierre nombra la causa, y las cadenas en blanco dejan de
  contar como detalle. Salió validando este mismo binario en vivo.
- `WorkflowText` era presentación pura pero compartía dependencia con la API
  de Autodesk por un `Truncate` alojado en `CoordinationWorkflow`, así que no
  se podía compilar en un runner sin licencia y la prosa que lee un
  coordinador —lo que decide si se fía de una corrida— no tenía ni una
  prueba. El recorte de cadenas se mudó a `TextLimits`, el texto entró al
  runner de C# y sus comprobaciones pasaron de 622 a 630.
- La tabla de versiones soportadas de `SECURITY.md` decía 0.2.2, tres
  releases atrás, y el pie de enlaces del changelog seguía en `v0.2.2`: los
  encabezados `## [0.3.0]` y `## [0.2.3]` se publicaban como corchetes
  literales y el diff de `[Unreleased]` escondía dos releases enteros.
  Ambos entran ahora en `TestVersionCoherence`, que solo leía los archivos con
  los que se *construye*.

### Changed

- **La lista de mutaciones dejó de escribirse a mano.** El test que debía
  atrapar lo anterior enumeraba nueve herramientas y omitía justo esas
  cuatro, así que pasaba en verde con el fallo dentro. Ahora el conjunto se
  deriva de la superficie de escritura que declara el propio complemento —la
  clase que atiende cada ruta en `Router.cs` y si el paso pasa por
  `WorkflowHandlers.Mutate`— y lo que no muta se exime por nombre y con el
  motivo escrito. Una ruta nueva cuenta como mutación hasta que alguien
  explique por qué no lo es: olvidarse ahora rompe el build en vez de
  aprobarlo. Se añadió además la comprobación de comportamiento que faltaba:
  con dos instancias vivas, cada mutación se ejecuta de verdad contra un
  puente espía y se verifica que **no llegó a llamarlo**.
- La documentación se puso al día con lo que 0.3.0 dejó hecho y describía al
  revés: la pestaña propia y «Estado del puente» —que informa antes de actuar,
  con «No» por defecto— en `ARCHITECTURE.md`, `TESTING.md` y la skill de
  instalación; el paso de release que mandaba correr el flujo 0-1-2-3 «desde
  la cinta», imposible desde que esos botones se quitaron; y la nota que decía
  que el pin del marketplace se verificaba a mano porque en CI la prueba se
  saltaba, cuando el job `manifests` ya trae los tags y exige que la fase
  detectada corresponda al evento. El CHANGELOG de 0.3.0 se contradecía a sí
  mismo sobre `hide_unrelated_geometry`: una entrada daba la causa por
  encontrada y confirmada en vivo y otra la declaraba abierta.
- CI compila los `.py` de `scripts/` y ejecuta `profile_checksums.py`. Solo se
  parseaban los `.ps1`, así que el generador del checksum que el runner de C#
  compara podía romperse sin que nada lo dijera hasta el siguiente release.

## [0.3.0] — 2026-08-16

Menor, no parche: `navis_clash_image` es una herramienta nueva. Ninguna
herramienta ni ruta existente cambió de forma — los argumentos visuales de
`navis_pdf_report` son todos opcionales y una llamada anterior sigue valiendo.

### Added

- **Imágenes de interferencia verificadas.** La cámara de cada cruce se calcula
  ajustando una caja —el volumen de interferencia más la parte de cada elemento
  que lo rodea— en vez de multiplicar un número por un factor de zoom, y toda
  esa aritmética vive en `ClashFraming.cs`, sin referencias a Autodesk, con
  pruebas en un runner sin licencia. Tres modos: `closeup`, `context` y `plan`.
- Control de calidad por imagen en dos vías que fallan distinto: el complemento
  proyecta las ocho esquinas de cada elemento por la cámara **que aplicó** y
  dice si quedaron en cuadro; el servidor cuenta los píxeles de los colores que
  pintó, que es lo único que detecta un muro delante del cruce. La que no pasa
  se repite más abierta o aislando, y si aun así no sirve se descarta contándolo.
- `navis_clash_image`: una sola interferencia con sus métricas completas, para
  afinar el encuadre antes de gastar veinticinco renders en un PDF.
- Opciones visuales en `navis_pdf_report` y `navis_clash_image`: `camera_mode`,
  `margin_percent`, `min_distance`, `max_distance`, `image_width`,
  `image_height`, `background_color`, `hide_unrelated_geometry`,
  `colorize_by_discipline`, `show_clash_marker`, `show_level`, `show_grid`,
  `render_quality`. Todas opcionales; sin ninguna, el informe sale como antes
  pero encuadrado. Cada imagen devuelve los parámetros **efectivos**.
- El informe distingue imágenes pedidas, generadas, fallidas y **descartadas
  por calidad**, con el motivo de cada descarte; antes las dos últimas
  compartían un número y mandaban a reiniciar Navisworks por un muro.
- `VisualScope` captura y restaura cámara, selección, materiales, visibilidad y
  rejilla alrededor de cada captura, y la respuesta trae el flag de documento
  modificado antes y después. La visibilidad se restaura **exacta**: solo se
  ocultan elementos que estaban visibles y se muestran exactamente esos.
- Nivel y eje resueltos desde la rejilla del modelo con el mismo formateador que
  usa la barra de estado, porque un `ClashResult` no lleva ninguno de los dos.
- [docs/IMAGENES.md](docs/IMAGENES.md), incluidos los límites reales del API de
  Navisworks 2026 comprobados por reflexión: el fondo se escribe y no se lee, y
  el marcador de choque no se puede provocar sin selección en Clash Detective.

### Changed

- NavisCoord tiene **pestaña propia** en la cinta, con un panel «Puente» y un
  solo botón, «Estado del puente». El botón informa primero —versión, y
  CORRIENDO con su puerto o DETENIDO— y solo entonces pregunta si arrancarlo o
  pararlo, con **«No» por defecto**: antes alternaba el puente al pulsarlo, así
  que consultarlo lo mataba. Los otros cinco botones —Configurar coordinación y
  los cuatro pasos numerados— hacían exactamente lo que ya hacen
  `navis_configure`,
  `navis_audit_models`, `navis_run` y `navis_group_levels`, y llenaban la
  pestaña «Tool add-ins» de entradas que empezaban todas por la misma palabra.
  `CoordinationWorkflow` no cambia: sigue siendo el servicio que llaman las
  rutas. `ConfigurePlugin.DecodeName` no era UI y se movió a `ModelNames`,
  donde el compilador ya no la deja esconderse en un archivo de botones.

### Fixed

- El modo `plan` mira hacia abajo en **perspectiva**, no en ortográfica, y no
  respeta `min_distance`. El volumen ortográfico es una caja: sobre un plenum
  metía el forjado y todo lo de arriba delante del cruce, con el velo del
  contexto multiplicándose capa a capa hasta dejar los dos elementos en **0,0 %
  del cuadro a cualquier altura de cámara** sobre un federado real. En
  perspectiva lo que queda detrás de la lente no se dibuja.
- **`hide_unrelated_geometry`: causa encontrada y atacada.** No ocultaba nada
  en ningún federado, y la causa no era ninguna de las dos que parecían obvias:
  `ModelItem.IsHidden` **lanza excepción** sobre la mayoría de lo que devuelve
  `ancestor.Children` —140 de 154 hermanos en la medición—, así que el `catch`
  los saltaba en silencio y el aislamiento informaba, con toda razón y sin
  utilidad, de que no había encontrado nada que ocultar. Ahora se pregunta al
  documento (`Models.IsHidden`), que sí los resuelve, con la propiedad como
  respaldo. Lo que lo cerró fue añadir un contador por cada motivo de descarte:
  un `catch` que continúa sin contar convierte un fallo en un silencio.

  Confirmado en vivo sobre el mismo cruce: `unreadable` pasó de **140 a 0** y
  los elementos ocultados de **0 a 136**, con los 154 hermanos repartidos sin
  residuo entre supervivientes, duplicados y ocultados. Ver `docs/TESTING.md`.
- La escalera de reintentos ya no gasta un render de Navisworks en aislar
  cuando el complemento reporta `hidden_items: 0`: ese peldaño devolvía la
  misma imagen por el mismo precio, así que se descarta y se abre el encuadre.
  Se escribió cuando el aislamiento **nunca** ocultaba nada —20 ancestros y
  3.474 hermanos para nada— y el contador por motivo que se añadió aquí es lo
  que permitió encontrar la causa, arreglada en la entrada anterior. La
  optimización sigue valiendo con el aislamiento ya funcionando: ahora el
  peldaño solo se salta cuando de verdad no hay nada que ocultar.
- `visual` y `notes` —lo que el complemento reporta haber hecho y qué le salió
  mal— dejaron de perderse en la capa Python. Llegaban al servidor y no al
  llamante, así que una investigación en vivo leía `null` mientras la respuesta
  del complemento traía la explicación.
- El informe **avisa** cuando deja el documento marcado como modificado. Medido
  sobre un federado real abierto limpio: `clash/export` no lo ensucia, pero una
  imagen con color, aislamiento, rejilla y fondo desactivados —de modo que
  mover la cámara es la única mutación— sí. Navisworks renderiza la vista
  ACTUAL y no expone cámara fuera de pantalla, así que producir la imagen exige
  moverla y no hay forma de evitarlo. No se guarda nada, la huella no cambia y
  la cámara vuelve a su sitio; el aviso lo dice para que nadie se sorprenda con
  el diálogo de guardar al cerrar.
- La foto de un problema es la del cruce **representativo** —el de mayor
  penetración—, que es el mismo del que salen `discipline_a`/`discipline_b`.
  Antes se fotografiaba `clash_ids[0]` y se etiquetaba con las disciplinas de
  otro cruce del mismo clúster: la leyenda quedaba mal en cualquier clúster
  cuyos miembros vinieran en otro orden.

## [0.2.3] — 2026-08-15

### Added

- Cierre desatendido de documento y aplicación con política explícita de
  guardado, huella, ensayo previo y verificación del PID.

### Fixed

- El handshake MCP anuncia la versión de NavisCoord, no la de la dependencia
  `mcp`.
- Un `session.json` legado con PID muerto ya no se considera una sesión viva;
  también se detecta la reutilización de PID por hora de creación.
- Las claves de idempotencia quedan acotadas por operación y documento, y el
  conteo `failed` refleja unidades afectadas sin inflarse por cada mensaje.
- README público reducido a instalación, uso, compatibilidad y seguridad; la
  bitácora de desarrollo dejó de ocupar la portada.
- Documentación de Codex y multiinstancia alineada con los manifiestos y el
  registro por proceso actuales.
- Las acciones de CI quedan fijadas por SHA y Dependabot vigila las
  dependencias Python y GitHub Actions.
- El manifiesto de Codex elimina un campo no admitido y su marketplace declara
  políticas válidas de instalación y autenticación.
- `navis_exit` usa la ventana principal que expone Navisworks antes de caer en
  `Process.CloseMainWindow`: las instancias creadas por Automation tienen GUI
  visible pero `Process.MainWindowHandle=0` y antes no llegaban a cerrarse.
- CI usa las versiones basadas en Node.js 24 de `actions/checkout` y
  `actions/setup-python`; el release no conserva avisos de la plataforma por
  acciones obsoletas.

## [0.2.2] — 2026-08-15

Versión de proceso. El motor y el complemento no cambian de comportamiento:
sólo se mueven la versión declarada y la procedencia de los artefactos.

### CI

- **El workflow se ejecuta también al publicar un tag `v*`.** Antes sólo
  corría en `pull_request` y en `push` a `main`, así que la validación que
  decide si un tag publica el ref correcto nunca llegaba a ejecutarse sobre el
  propio tag: el momento en que esa pregunta importa era justo el único que no
  se comprobaba. Se añade además `workflow_dispatch` para poder diagnosticar
  una referencia concreta sin empujar nada.
- **Todos los jobs que ejecutan la suite hacen checkout con la lista de tags.**
  La regla del pin es una pregunta *sobre* tags y `actions/checkout` no trae
  ninguno por defecto, de modo que `git tag` volvía vacío y la comprobación
  concluía que el tag no existía cuando sí existía.
- **La fase `main` deja de ser un caso implícito.** Entre el merge y el tag,
  `main` declara una versión cuyo tag todavía no se ha publicado; eso es una
  ventana legítima y acotada del proceso de release, y se reporta como tal en
  vez de fallar como si fuera un ref inventado. Sigue siendo un fallo que
  `main` apunte a un ref que ni existe ni es la versión declarada.
- **En fase `tag` se comprueba que el tag apunte al commit del checkout.** Que
  el tag exista y que sea el que se está construyendo son dos afirmaciones
  distintas, y sólo se verificaba la primera.
- **Ninguna fase puede saltarse.** Un prerequisito ausente —tags ilegibles,
  `HEAD` desacoplado, tag inexistente en fase tag— es un fallo con el arreglo
  en el mensaje, nunca un skip: una comprobación que se salta reporta éxito.

### Artefactos

- Reconstruidos como 0.2.2. Mismo contenido funcional que 0.2.1, con la
  versión y la procedencia actualizadas.

## [0.2.1] — 2026-08-15

Primera versión pública de NavisCoord.

### Qué es

Un puente entre Autodesk Navisworks y un cliente MCP. Convierte una corrida de
clash detection en decisiones priorizadas y explicadas: filtra lo que no es
interferencia, agrupa los cruces redundantes, detecta causas raíz sistémicas,
arma la matriz de coordinación y un plan por paquetes, y genera el informe y
el PDF.

### Puente

- **Multiinstancia.** Un registro de sesiones por proceso, con DACL explícito,
  token por sesión y búsqueda de puerto libre: dos versiones de Navisworks
  abiertas a la vez no se pisan ni se enmascaran.
- **Targeting.** El servidor elige a qué instancia habla y lo declara. El
  estado en memoria lleva su procedencia —documento, huella, perfil— y se
  descarta cuando cualquiera de los tres cambia.
- **Trabajos.** Las mutaciones largas se envían como job y se consultan por
  `job/status`, contestado en el hilo del listener, de modo que `health` sigue
  respondiendo mientras el trabajo posee el hilo de UI. La cancelación se
  declara por ruta y su veredicto es explícito: una ruta atómica no promete
  una parada que nadie va a atender.
- **Save / Save As verificados.** Se relee del sistema de archivos, con
  política de rutas de salida, `overwrite` explícito y huella del documento
  declarada obligatoria: guardar es irreversible, así que el llamador tiene
  que decir sobre qué documento cree que está actuando.

### Perfil

- **Un solo perfil vigente por instancia.** `navis_load_profile` lo instala en
  los dos extremos: el servidor valida y canoniza, envía el **contenido**
  —nunca una ruta— por el puerto autenticado, y el complemento lo revalida y
  lo adopta en memoria para esa sesión.
- Los dos extremos calculan el **mismo** checksum sobre el mismo perfil, con
  una forma canónica escrita a mano en ambos lados y fijada por un vector
  compartido.
- Un job congela el checksum con el que se envió, y `profile/load` se rechaza
  mientras corre.
- El perfil que se publica es **neutral y configurable**: categorías estándar,
  disciplinas genéricas y ningún parámetro propio de una organización. Lo que
  antes serían valores por defecto de alguien es aquí una capacidad que se
  declara en el perfil, empezando por `budget_code_keys`, vacío hasta que se
  configure.

### Superficie

- **47 herramientas MCP.** Ninguna ruta servida por el complemento se queda
  sin tool, y ninguna capacidad anunciada sin ruta que la sirva; hay una
  prueba que lo comprueba leyendo las dos mitades.
- Los cuatro pasos de la cinta son también rutas HTTP y llaman al mismo
  servicio, así que un botón y una tool no pueden discrepar.

### Seguridad

- Token por sesión en cabecera, comparación en tiempo fijo. La única ruta
  anónima es `GET /health`, que responde cuatro hechos y nada del modelo.
- Límite de cuerpo **contado en bytes**, no en caracteres. `Content-Length`
  solo sirve para rechazar temprano y nunca dimensiona un buffer.
- Un cuerpo truncado o malformado se responde **400**, en vez de repararlo y
  actuar sobre la mitad que llegó.
- Las rutas de salida se resuelven de verdad: una junction dentro de una raíz
  autorizada que apunte fuera se rechaza nombrando el destino real, y la
  comprobación se repite justo antes de escribir.
- Los CSV se escriben neutralizando fórmulas; el PDF se genera sin plantillas
  externas.

### Empaquetado

- El artefacto publicado **no nombra la máquina que lo compiló**: el build de
  Release no emite símbolos y `PathMap` normaliza las rutas de origen. Un
  guard lo comprueba sobre cada binario antes de empaquetarlo, en ASCII y en
  UTF-16.
- **Build reproducible**: el mismo commit produce los mismos bytes desde
  cualquier carpeta. Los ZIP se escriben con orden fijo y fecha derivada del
  commit; el sdist se normaliza igual.
- Wheel y sdist llevan LICENSE, NOTICE, README y el perfil dentro.

### Compatibilidad

| | |
|---|---|
| Navisworks Manage | 2024, 2025 y 2026 (un ZIP por versión) |
| .NET | Framework 4.8 |
| Python | 3.10 – 3.13 |
| MCP | 1.9 en adelante, y la rama 2.x |

### Limitaciones conocidas

- El color aplicado no se puede releer: la API no expone getter de apariencia
  en `ModelItem`. La verificación llega hasta que cada ruta pedida resuelve a
  un elemento vivo.
- `save_as` tiene un TOCTOU impuesto por la API: la ventana se hace pequeña y
  se vuelve a comprobar en su extremo.
- Navisworks no corre en CI; todo lo que toca un `Document` vivo se prueba a
  mano y queda registrado en `docs/TESTING.md`.
- Los caminos de nube se ejercitan con documentos locales.
- La cancelación cooperativa existe en `workflow/audit_models` y
  `workflow/group_levels`; el resto es atómico y lo declara.

[Unreleased]: https://github.com/HorizunGroup/naviscoord-mcp/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/HorizunGroup/naviscoord-mcp/releases/tag/v0.4.1
[0.4.0]: https://github.com/HorizunGroup/naviscoord-mcp/releases/tag/v0.4.0
[0.3.1]: https://github.com/HorizunGroup/naviscoord-mcp/releases/tag/v0.3.1
[0.3.0]: https://github.com/HorizunGroup/naviscoord-mcp/releases/tag/v0.3.0
[0.2.3]: https://github.com/HorizunGroup/naviscoord-mcp/releases/tag/v0.2.3
[0.2.2]: https://github.com/HorizunGroup/naviscoord-mcp/releases/tag/v0.2.2
[0.2.1]: https://github.com/HorizunGroup/naviscoord-mcp/releases/tag/v0.2.1
