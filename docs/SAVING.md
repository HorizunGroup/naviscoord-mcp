# Guardado, y el caso ACC/.nwfacc

## Por qué existe

Todo lo que produce el flujo —search sets, matriz de clash, resultados,
grupos por nivel, nombres de choques— vive **solo en memoria** hasta que
alguien guarda. Cerrar Navisworks sin hacerlo descarta una corrida entera de
veinte minutos. La cinta solo podía pedirlo por favor con un recordatorio;
eso no es un problema de recordatorio, es una capacidad que faltaba.

## Cerrar sin una decisión de guardado interactiva

`navis_close_document` y `navis_exit` nunca delegan **la decisión de
guardado** a un cuadro «¿Guardar cambios?». Exigen
`expected_document_fingerprint`, empiezan con `dry_run=true` y obligan a
elegir una política:

| `disposition` | Resultado |
|---|---|
| `save` | Guarda en la ruta actual, verifica el archivo y solo entonces cierra |
| `discard` | Descarta los cambios de forma explícita |
| `require_clean` | Cierra únicamente si el documento ya está limpio |

Un documento sin destino local debe pasar primero por `navis_save_as`; el
cierre no inventa una ruta ni puede abrir un selector de archivos. La ruta
`document/close` usa `Application.MainDocument.Clear()` y comprueba que el
documento activo haya quedado vacío.

`Clear()` publica el cambio de documento a todos los complementos cargados.
NavisCoord no puede impedir que un complemento de terceros responda a ese
evento mostrando su propio diálogo. Esto se reprodujo con un complemento de
gestión de conjuntos instalado en la máquina de pruebas: el documento sí
quedó vacío, pero el tercero mostró después «No hay plugin que abra (null)».
Sin ese complemento, la misma operación fue silenciosa. Para coordinación
desatendida, valida el conjunto real de complementos instalado; si necesitas
cerrar también la aplicación, prefiere `navis_exit`, que comprueba el PID y
devuelve `partial` mientras cualquier diálogo mantenga vivo Navisworks.

Para salir de Navisworks, el puente devuelve primero la aceptación y después
solicita el cierre de la ventana principal. El servidor Python observa el PID:
solo responde `completed` si el proceso terminó; si otro complemento muestra
un diálogo o la ventana rechaza el cierre, responde `partial`.

## Las dos rutas

| Ruta | Herramienta | Qué hace |
|---|---|---|
| `document/save` | `navis_save` | Guarda en su propia ruta |
| `document/save_as` | `navis_save_as` | Guarda una copia en otra ruta |

## Restricciones

Es la ruta más peligrosa del add-in, así que es la más restringida.

1. **`expected_document_fingerprint` obligatorio.** Guardar es irreversible;
   quien llama tiene que declarar sobre qué documento cree que actúa. Se lee
   de `navis_health`.
2. **La ruta pasa por la política de salida** — raíces autorizadas, sin UNC,
   sin espacios de dispositivo, sin ADS, sin nombres reservados. Para
   `save_as` se añade la carpeta del documento abierto, que el usuario eligió
   al abrirlo desde ahí; un documento en la nube no aporta ninguna, porque su
   «carpeta» es una caché de Desktop Connector.
3. **`overwrite=false` por defecto.** Un archivo existente no se reemplaza
   por accidente.
4. **Solo `.nwf` y `.nwd`.** Es lo que `Document.SaveFile` sabe escribir.
5. **Se verifica releyendo.**

## La verificación

Después de escribir se comprueba, contra el disco y contra el documento:

| Comprobación | Qué detecta |
|---|---|
| El archivo existe en la ruta resuelta | Un `TrySaveFile` que devolvió true sin escribir |
| Tiene bytes | Un archivo truncado a cero |
| La marca de tiempo o el tamaño cambiaron | Un no-op silencioso |
| `doc.FileName` apunta al destino | Un `save_as` que no reasignó el documento |
| `doc.IsModified == false` | Un guardado que no cubrió todos los cambios |

Cualquier fallo deja el envelope en `failed` o `partial`. Nunca `completed`.

## ACC y `.nwfacc` — el caso que hay que entender

**Un `.nwfacc` no es un `.nwf` con la extensión más larga.** Es la cara local
de un documento cuyo maestro vive en Autodesk Construction Cloud.

Consecuencias, y por qué el add-in las impone en vez de confiar:

- **Guardar encima NO publica nada a ACC.** El equipo no ve el trabajo.
- **El siguiente sync de Desktop Connector puede reemplazarlo**, y con él toda
  la corrida.

Por eso:

```
navis_save  sobre un .nwfacc  → rechazado, con la razón y qué hacer
navis_save_as a un .nwfacc    → rechazado: ese formato no lo escribe SaveFile
navis_save_as a un .nwf local → permitido, y verificado
```

`navis_capabilities` lo declara **antes** de intentar nada:

```jsonc
"save": {
  "save": false,
  "save_as": true,
  "cloud_hosted": true,
  "reason": "El documento abierto es un .nwfacc de ACC. Guardar sobre él no publica nada en la nube y el siguiente sync de Desktop Connector puede reemplazarlo. Usa document/save_as hacia un .nwf local."
}
```

Cuando un `save_as` local sale bien desde un documento de ACC, el envelope
lleva un aviso: el documento sigue apuntando a la nube, y **subirlo es un paso
manual** desde Desktop Connector o desde la web de ACC.

## Cuando el API no puede

Si una versión concreta de Navisworks no admite la operación, la respuesta es
`capability=false` con el motivo. No se simula éxito.

### La carrera que Save As no puede cerrar

Las salidas de Python (PDF, CSV, JSON, handoff) **reservan** su destino con
una creación exclusiva y publican por renombrado: de dos escritores compitiendo
por el mismo nombre, exactamente uno gana y el otro recibe un rechazo.

**Save As no puede hacer eso.** `Document.TrySaveFile` recibe una *ruta* y abre
el archivo por su cuenta; no hay sobrecarga que acepte un handle o un stream.
Escribir a un temporal y renombrar después tampoco sirve: el `FileName` del
documento quedaría apuntando a un archivo que ya no existe con ese nombre, lo
cual es peor que la carrera.

Queda entonces la comprobación inmediatamente anterior a la llamada y la
verificación inmediatamente posterior. Entre esos dos instantes otro proceso
podría crear el destino y verlo sobrescrito. La ventana es de milisegundos —no
de minutos, como llegó a ser la del PDF— pero **no es cero**, y no se presenta
como una garantía.

## Flujo típico

```
navis_health                       → lee document_fingerprint y save.capability
navis_configure(...)               → paso 1
navis_run(run_async=True)          → paso 2
navis_group_levels(run_async=True) → paso 3
navis_save_as(
    path=r"D:\Coordinacion\Torre4-2026-08-14.nwf",
    expected_document_fingerprint="a1b2c3d4e5f60718")
```

## Prueba pendiente

La verificación post-guardado se ejerce contra el disco y contra
`Document.IsModified`, pero **escribir un `.nwf` real requiere Navisworks
abierto con un federado**. Las pruebas automatizadas cubren la política de
rutas, el rechazo de formatos y el contrato; el guardado real está marcado
como prueba manual pendiente en [TESTING.md](TESTING.md).
