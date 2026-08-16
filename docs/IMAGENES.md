# Imágenes de interferencia

Un informe de coordinación vale lo que valen sus imágenes. Y una imagen mala
no avisa: Navisworks devuelve un PNG perfectamente válido tanto si la cámara
encuadró el tubo atravesando la viga como si fotografió el edificio entero o
el interior del propio conducto. El render tiene éxito en los tres casos, así
que un informe lleno de imágenes inútiles parece igual de terminado — y así es
como llega a un cliente.

De ahí las dos reglas de este subsistema:

1. **La cámara se calcula, no se empuja.** Se ajusta una caja, no un número.
2. **Ninguna imagen se publica sin comprobarla.** Dos veces, por dos vías que
   fallan de forma distinta.

## Cómo se encuadra

```
caja de interferencia  ──┐
caja del elemento A  ────┼──►  vecindad  ──►  caja a encuadrar  ──►  cámara
caja del elemento B  ────┘     (recorte)      (+ margen)             (extensiones
                                                                      + distancia)
```

- **Vecindad**: la caja del cruce expandida una fracción de su propio tamaño
  (35 % en primer plano, 150 % en contexto). Solo la parte de cada elemento
  que cae dentro puede agrandar el encuadre: sin ese recorte, un conducto de
  60 m fija las extensiones y la foto sale aérea.
- **Objetivo**: el centro del **volumen de interferencia**, nunca el de la
  caja ajustada. Difieren en cuanto un elemento es más largo que el otro, y
  apuntar al centro de la caja desplaza el cruce hacia una esquina.
- **Orientación**: una dirección de tres cuartos, proyectada **perpendicular a
  la recta que une los dos elementos**. Con una dirección fija, dos elementos
  alineados con la vista se tapan y la imagen muestra un solo objeto.
- **La planta mira hacia abajo en perspectiva, no en ortográfica.** El volumen
  de vista ortográfico es una caja: todo lo que está encima del cruce entra en
  la imagen sea cual sea la altura de la cámara, porque en esa proyección la
  posición no excluye nada. Sobre un plenum eso mete el forjado y todas las
  instalaciones de arriba delante del cruce, cada uno con el velo del contexto,
  y los velos se multiplican hasta blanquear los dos elementos —medido en un
  federado real: **0,0 % del cuadro para ambos lados a cualquier altura**,
  mientras la vista de tres cuartos del mismo cruce salía bien. En perspectiva
  lo que queda detrás de la lente no se dibuja. Por eso la planta además
  **ignora `min_distance`**: en los demás modos la distancia solo aplana la
  perspectiva, pero mirando hacia abajo decide qué se mete en medio.
- **La contención se comprueba en perspectiva.** Las extensiones se miden con
  productos escalares —afín, exacto en el plano focal—, pero el render proyecta
  en perspectiva: una esquina de la caja del cruce más cercana que la distancia
  focal se proyecta MÁS GRANDE y se sale de un cuadro que la aritmética acababa
  de declarar suficiente. La corrección es exacta y no itera, y se aplica solo
  a la caja del **cruce**: forzar dentro también las esquinas cercanas de un
  conducto de 30 m duplica el encuadre y deshace todo lo anterior.
- **Extensiones y distancia se fijan por separado**
  (`SetExtentsAtFocalDistance`). Por eso `min_distance` y `max_distance` son
  opciones honestas: mueven la cámara sin cambiar lo que entra en el cuadro.
  Cuando la geometría es más profunda que `max_distance`, gana la geometría y
  la respuesta lo dice (`max_distance_below_geometry_floor`); obedecer a
  ciegas mete la lente dentro del cruce y devuelve gris.

Toda esa aritmética vive en `addin/NavisCoord.Addin/ClashFraming.cs`, **sin una
sola referencia a Autodesk**, y se comprueba en `NavisCoord.Tests` sin licencia.
No es purismo: un encuadre malo no lanza excepción, así que el único sitio
donde el fallo es visible son las coordenadas.

## Cómo se comprueba

| Comprobación | Dónde | Qué detecta |
|---|---|---|
| Proyección de las 8 esquinas de cada caja por la cámara aplicada | complemento | elemento fuera de cuadro, cruce cortado, elemento demasiado pequeño para leerse |
| Recuento de píxeles de cada color aplicado | servidor | **oclusión**: un muro delante del cruce |
| Colores dominantes y color mayoritario | servidor | imagen vacía (cielo) o cámara dentro de la geometría |

Ninguna de las dos basta sola. La geometría no ve el muro que hay delante; los
píxeles no distinguen una imagen oscura bien encuadrada de una mal encuadrada.

La proyección **no** usa `View.ProjectPoint`: ese proyecta por el viewport
interactivo, cuya relación de aspecto es la de la ventana de Navisworks,
mientras que el render sale al tamaño que pidió el llamante. Comprobar uno y
entregar el otro es como una comprobación pasa sobre una imagen equivocada.

### Por qué el control de píxeles mide el MATIZ

Tres formas de clasificar un píxel, y dos fallan sobre un render real:

| Método | Falla con | Cómo se vio |
|---|---|---|
| Color RGB más cercano | el **sombreado**: Navisworks escala la superficie hacia el negro, así que un tubo rojo abarca de (214,40,40) a (30,6,6) dentro de una misma imagen | encuentra la cara iluminada y pierde la sombreada |
| Cromaticidad (proporción de canales) | el **velado**: con el contexto atenuado, los dos elementos se ven **a través** de una capa translúcida y cada píxel es una mezcla hacia el fondo | un tubo rojo perfectamente visible se midió en 0,01 % del cuadro y seis imágenes buenas se descartaron |
| Matiz a secas | el **recorte** por sobreexposición: una superficie de frente a la luz satura sus canales fuertes en 255, y con dos de los tres clavados ahí la proporción entre ellos se pierde y el matiz se desliza | un conducto (245,166,35), matiz 37°, salió de canto y muy iluminado como **(255,255,140)**, matiz **60°**: 7.704 píxeles contados como fondo |
| **Matiz + modelo de exposición** | nada de lo anterior | se recorre la luz hacia arriba sobre cada color y se recogen los matices que produce; el píxel casa si cae en ese arco |

Sombrear mueve el valor y velar mueve la saturación —el matiz aguanta las dos—
pero recortar destruye información: `(255,255,140)` tiene muchos orígenes y no
se puede invertir. Modelarlo **hacia adelante** sí: el render es
`clamp(color × luz)`, así que basta subir la luz sobre el color conocido y
quedarse con los matices resultantes. El arco se corta donde el color se lava
por debajo del piso de saturación, porque a partir de ahí es indistinguible del
blanco de todas formas.

El piso de saturación (28 %) está **medido, no supuesto**: sobre un federado
real los dos elementos pintados dieron 0,49 y 0,58 de saturación mediana,
mientras el cielo y el contexto atenuado daban 0,15 y 0,09. Importa porque el
fondo por defecto de Navisworks es **azul pálido**: con un piso más bajo, un
elemento pintado de azul «aparecía» en todas las imágenes estuviera o no, que
es peor que el rechazo falso que esto reemplazó.

### La escalera de reintentos

Repetir el mismo render produce el mismo fallo y cuesta otro frame de
Navisworks, así que cada escalón cambia lo único que podría ser responsable:

| Síntoma | Siguiente intento |
|---|---|
| fuera de cuadro / demasiado pequeño | abrir el modo: `closeup` → `context` → `plan` |
| pintado pero ausente en los píxeles | `hide_unrelated_geometry`: es la única cura de la oclusión |
| ambos | abrir **y** aislar |
| PNG ilegible | ninguno; se reporta |

Si al final no sirve, la imagen se **descarta contándolo** y la página dice por
qué en castellano («otra geometría tapaba uno de los elementos»), en vez de
dejar un hueco. Con `keep_rejected=True` se publica el mejor intento igualmente.

## Esquema de opciones

Todas son opcionales. Sin ninguna, el informe sale como siempre pero con el
encuadre nuevo.

| Opción | Por defecto | Qué hace |
|---|---|---|
| `camera_mode` | `closeup` | `closeup`, `context`, `plan` (acepta `contexto`, `planta`) |
| `margin_percent` | `25` | aire alrededor de la caja ajustada |
| `min_distance` / `max_distance` | `1.5` / `60` m | acotan la cámara sin reencuadrar |
| `image_width` / `image_height` | `1200` / `800` | píxeles del render |
| `background_color` | *(sin tocar)* | fondo plano; **exige** color de restauración (ver límites) |
| `hide_unrelated_geometry` | `false` | aísla los dos elementos ocultando lo demás |
| `colorize_by_discipline` | `true` | color estable por disciplina; con `false`, rojo = quien mueve, azul = quien no se toca |
| `show_clash_marker` | `true` | recuadro sobre el volumen de interferencia |
| `show_level` | `true` | quema en la imagen el ID, el nivel y la leyenda de colores |
| `show_grid` | *(sin tocar)* | enciende o apaga la rejilla del modelo |
| `render_quality` | `standard` | `draft` (acotado en tiempo), `standard`, `high` |
| `min_screen_fraction` | `0.004` | umbral geométrico de tamaño mínimo |
| `min_pixel_fraction` | `0.0006` | umbral de píxeles de cada elemento |
| `max_attempts` | `3` | intentos por imagen, incluido el primero |
| `keep_rejected` | `false` | publicar el mejor intento aunque no pase |

`standard` usa sombreado plano **a propósito**, no por velocidad: la
comprobación de píxeles clasifica por matiz, y las luces de escena con
texturas convierten un tubo rojo en cincuenta tonos que ningún umbral separa.

## Qué devuelve cada imagen

`navis_clash_image` y el bloque `images.per_issue` de `navis_pdf_report`
traen, por imagen: la cámara aplicada (posición, objetivo, distancia,
extensiones), las cajas en metros, la fracción de pantalla y de píxeles de cada
lado, los intentos con su motivo, los **parámetros efectivos**, y el flag de
documento modificado antes y después.

El recuento del informe distingue cuatro cosas que antes eran un solo número:

```
requested / generated / failed / rejected_for_quality  (+ rejection_reasons)
```

Un render **fallido** es un problema de puente o de licencia; uno
**descartado** es de modelado o de encuadre. Reportar ambos como
`images_failed` mandaba a la gente a reiniciar Navisworks por un muro delante
de un tubo.

## Qué toca del documento, y cómo lo devuelve

`VisualScope` captura el estado antes de la foto y lo restaura después, dentro
de un `using`: un render que lanza excepción restaura igual que uno que
funciona.

| Estado | Restauración | Cómo |
|---|---|---|
| Cámara | completa | `CurrentViewpoint.ToViewpoint()` / `CopyViewpointFrom(..., JumpCut)` |
| Selección | completa | `CurrentSelection.CreateCopy()` / `CopyFrom` |
| Materiales temporales | completa | `ResetAllTemporaryMaterials`; no se guardan nunca |
| Visibilidad | **exacta** | solo se ocultan elementos que estaban visibles (`ModelItem.IsHidden` se lee), y se muestran exactamente esos. `ResetAllHidden` no se llama: revelaría lo que el coordinador había ocultado |
| Rejilla | completa | `Grids.ActiveSystem` y `RenderMode` se leen y se escriben |
| Fondo | **imposible de leer** | ver abajo |

La respuesta trae `document.modified_before` y `modified_after` para que un
documento ensuciado se vea en el momento y no una semana después.

### El documento queda marcado como modificado, y no se puede evitar

Medido sobre un federado real abierto limpio, aislando causa por causa:

| Paso | `modified` |
|---|---|
| Recién abierto | `false` |
| Tras `clash/export` (el análisis completo, 2.701 cruces) | `false` |
| Una imagen **sin color, sin aislamiento, sin rejilla y sin fondo** | `false` → **`true`** |

Con todos los overrides desactivados, mover la cámara es la única mutación que
queda: **es la cámara**. Y no hay salida — Navisworks renderiza la vista
ACTUAL, `GenerateImage` no acepta una cámara propia y el API no expone render
fuera de pantalla, así que producir la imagen exige mover el punto de vista.
Escribirlo por `ActiveView.CopyViewpointFrom` en vez de por `CurrentViewpoint`
tampoco lo evita; se dejó así porque es el objeto correcto para mover una
cámara y porque es simétrico con la restauración.

Lo que sí se garantiza: **no se guarda nada**, la **huella del documento no
cambia**, y cámara, selección, materiales, visibilidad y rejilla vuelven a como
estaban. `navis_pdf_report` lo dice en `images.document_note` cuando ha
ocurrido, para que quien corre el informe sepa que puede cerrar sin guardar en
vez de encontrarse el diálogo sin explicación. Si el documento ya estaba
modificado antes, no dice nada: culpar al informe del trabajo sin guardar de
otro manda a buscar un cambio que nadie hizo.

## Límites reales del API de Navisworks 2026

Comprobados por reflexión sobre `Autodesk.Navisworks.Api.dll` y
`Autodesk.Navisworks.Clash.dll` instalados, no supuestos:

- **El fondo se puede escribir y no se puede leer.** Existen
  `SetPlainBackground`, `SetGraduatedBackground` y `SetHorizonBackground`; no
  existe ningún getter. Por eso `background_color` viaja siempre con un color
  de restauración explícito y, sin él, el complemento **se niega** a tocarlo y
  lo dice (`refused_no_restore_color`). La alternativa era dejarle a un
  coordinador el cielo blanco después de generar un informe.
- **El marcador de choque de Navisworks no se puede provocar.**
  `ScenePlusOverlay` dibuja la capa de superposición del resultado que el
  Clash Detective tenga **seleccionado**, y el API de choques de 2026 no
  expone forma de seleccionar uno: `TestsViewpointForResult` es el único
  miembro que acepta un resultado. En un render automatizado la superposición
  sale vacía. Por eso el recuadro lo dibuja el servidor, sobre las
  coordenadas de pantalla que el propio complemento ya calculó para juzgar el
  encuadre — así el marcador y la comprobación no pueden discrepar.
- **No hay «aislar» en el API.** Ocultar las raíces y luego `MakeVisible` no
  funciona: mostrar un elemento desoculta sus ancestros, y desocultar una raíz
  devuelve todo. La única construcción correcta es la que hace un usuario a
  mano — recorrer los ancestros de lo que debe sobrevivir y ocultar los
  *otros* hijos de cada uno. Es lo que hace `VisualScope.Isolate`.
- **`ClashResult.BoundingBox` puede venir vacía** en pruebas de holgura. Se
  reconstruye con la intersección de las dos cajas y, en último término, con
  un cubo alrededor de `Center`. Devolver una caja vacía pondría la cámara en
  el origen del mundo, que renderiza como cielo.
- **No hay render fuera de pantalla.** `GenerateImage` dibuja la vista actual y
  no acepta una cámara; por eso una imagen ensucia el flag de modificado del
  documento aunque no cambie nada más. Detalle y medición arriba.
- **Nivel y eje**: un `ClashResult` no lleva ninguno de los dos. Se resuelven
  con `GridSystem.ClosestIntersection` y el mismo formateador que usa la barra
  de estado (`FormatCombinedDisplayString`), así que el informe dice lo que el
  coordinador ve en pantalla. Si el modelo no tiene rejillas, sale vacío.

### Qué necesita Navisworks Manage y qué no

- **Todo lo de esta página necesita Navisworks Manage**, porque necesita
  resultados de choque, y Clash Detective solo existe en Manage. En Simulate
  el complemento arranca y `clash/*` no responde.
- **No** necesita Manage: el motor de análisis, el plan, la matriz, el PDF sin
  imágenes y toda la validación de calidad sobre PNG ya generados. Por eso la
  suite de Python corre entera en un runner sin licencia.

## Coste

Cada imagen es un render de Navisworks sobre el hilo de UI, así que el tiempo
del informe lo fijan las imágenes y no el análisis. Dos consecuencias
prácticas:

- La escalera de reintentos está acotada (`max_attempts`, 3 por defecto) y cada
  escalón cambia algo: nunca se repite un render idéntico.
- `with_images=False` sigue siendo la forma de sacar un borrador rápido, y
  `render_quality="draft"` acota cada render en tiempo para un federado pesado.

## Ejemplos

Antes — una llamada que sigue funcionando igual:

```json
{"name": "navis_pdf_report", "arguments": {"path": "C:/salida/coordinacion.pdf"}}
```

Después — el mismo informe, encuadrando por contexto y aislando:

```json
{"name": "navis_pdf_report",
 "arguments": {"path": "C:/salida/coordinacion.pdf",
               "camera_mode": "context",
               "hide_unrelated_geometry": true,
               "margin_percent": 40,
               "image_width": 1600,
               "image_height": 1000,
               "render_quality": "high"}}
```

Afinar una sola antes de gastar veinticinco renders:

```json
{"name": "navis_clash_image",
 "arguments": {"issue_id": "ISS-0007",
               "save_to": "C:/salida/iss-0007.png",
               "camera_mode": "plan"}}
```

`navis_clash_image` nunca devuelve la imagen en base64 a la conversación: son
cientos de kilobytes que no aportan nada a la decisión. Devuelve las métricas
y, si se pide, escribe el PNG dentro de las rutas autorizadas.
