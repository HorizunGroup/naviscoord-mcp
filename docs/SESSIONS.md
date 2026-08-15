# Sesiones, multiinstancia y targeting

## El problema

Un coordinador tiene abiertas dos Navisworks a la vez con toda naturalidad:
2024 con el modelo archivado y 2026 con el vivo, o dos proyectos en paralelo.

Hasta la versión anterior ambas escribían el **mismo** `session.json`, con
tres consecuencias, las tres observadas:

- **Ganaba la última.** El servidor MCP solo podía alcanzar la que abriera
  después, y nadie podía saber sobre qué documento estaba a punto de escribir.
- **Cualquiera de las dos lo borraba al cerrar.** Cerrar la que NO estabas
  usando eliminaba el handshake de la que sí, y la siguiente llamada fallaba
  con «no hay sesión activa» contra un puente perfectamente vivo.
- **Permisos heredados.** El archivo lleva un token que conduce el modelo del
  usuario.

## El registro

```
%LOCALAPPDATA%\NavisCoord\sessions\
    24680-a1b2c3d4e5f60718.json      ← Navisworks 2026, PID 24680
    31204-9f8e7d6c5b4a3021.json      ← Navisworks 2024, PID 31204
```

Cada entrada:

| Campo | Para qué |
|---|---|
| `contract` | Versión del formato (`naviscoord.session/2`) |
| `session_id` | Identidad estable de este puente; es el `target_id` |
| `pid`, `process_started` | Liveness, y detección de PID reciclado |
| `port` | El puerto que ESTA instancia ganó |
| `token` | Bearer, protegido por DACL |
| `product`, `addin_version`, `api_version` | Negociación de capacidades |
| `document` | Título, huella y si está abierto |
| `started`, `heartbeat` | Antigüedad |

Reglas:

- Cada instancia **crea, actualiza y elimina solo su propio archivo**.
- Cerrar una instancia no toca la sesión de otra.
- Las entradas obsoletas se limpian comprobando que el **PID siga vivo**, no
  por antigüedad: una instancia sana puede pasar veinte minutos anexando un
  federado en el hilo de UI, y ese es justo el momento en que menos quieres
  que desaparezca su handshake. El PID reciclado se descarta comparando la
  hora de arranque del proceso.
- Se sigue escribiendo `session.json` como **puntero de compatibilidad** para
  servidores MCP anteriores al registro. Lleva el PID dueño, y al cerrar solo
  lo borra quien lo escribió; si queda otra instancia viva, se repunta a ella.
- El puntero de compatibilidad obedece la misma regla de vida: si su PID murió
  no se usa como sesión activa, aunque el archivo haya quedado tras un cierre
  abrupto.

### `target_id`, no PID

El `target_id` es el `session_id`, no el PID: Windows recicla PIDs y un PID no
significa nada tras un reinicio, mientras que el `session_id` se acuña por
puente y aparece en todas sus respuestas.

## Elegir instancia

```
navis_sessions          → todas las activas, con documento y target_id
navis_target(id)        → fija a cuál van las llamadas siguientes
navis_current_target    → cuál está seleccionada ahora
```

**La regla que importa: las lecturas pueden elegir, las escrituras no.**

- Una lectura con dos instancias y sin target toma la más reciente y lo dice.
- Una **mutación** con dos instancias y sin target se **rechaza**, con la
  lista de candidatas. Elegir en silencio es cómo un plan calculado sobre
  Torre A termina escrito en Torre B, y el operador se entera cuando ve los
  grupos en el archivo equivocado.

`navis_target` descarta el análisis en memoria: pertenecía a la otra
instancia.

## `NAVISCOORD_SESSION`

Override explícito. Acepta un archivo o un directorio y **corta en seco** el
descubrimiento normal.

```powershell
$env:NAVISCOORD_SESSION = "C:\ruta\a\sessions\24680-a1b2c3d4.json"
```

Existe para cualquier despliegue donde el servidor MCP no corre como el
usuario interactivo dueño de Navisworks — una cuenta de servicio, un
contenedor, un arnés de pruebas — y sin él el servidor solo puede reportar «no
hay sesión activa» sin sitio al que mirar.

`navis_sessions` reporta `override_active: true` cuando está en uso.

### Perfiles redirigidos

La raíz se resuelve leyendo `%LOCALAPPDATA%` en **ambos** lados, no
`SpecialFolder.LocalApplicationData` en C# y la variable en Python. Coinciden
en una máquina normal y divergen justo en las que importan —perfil
redirigido, Citrix, FSLogix, cuenta de servicio— donde el add-in publicaría el
handshake donde el servidor nunca mira, con «no hay sesión activa» como único
síntoma.

## Estado atado al documento

El servidor MCP es un proceso largo con un análisis caro en memoria. Ese caché
solo tiene sentido para el documento del que salió.

El estado guarda su procedencia: `(target_id, huella del documento, checksum
del perfil)`. Cuando cualquiera de los tres cambia, lo derivado se descarta:

| Cambia | Se descarta |
|---|---|
| Documento | export, análisis, discovery, group_key, roles |
| Instancia (`navis_target`) | todo lo anterior |
| Perfil (`navis_load_profile`) | todo lo anterior + overrides de disciplina |
| Discover sin recomendación confiable | group_key, roles, discovery |

Y:

- Una lectura del análisis cuya huella ya no corresponde **falla explicando**
  de qué documento era, en vez de responder con datos de otro modelo.
- Una mutación cuya `expected_document_fingerprint` no coincide se rechaza sin
  tocar nada.

### Qué es la huella

Identidad, **no** hash de contenido: ruta, título, número de modelos y la ruta
de origen de cada uno. Si cambiara con cada edición, la segunda llamada de
cualquier flujo de dos pasos sería rechazada por el guardia que instaló la
primera. Lo que tiene que detectar es que alguien cerró Torre A y abrió Torre
5 —o anexó otro modelo— entre calcular un plan y aplicarlo, porque entonces
cada `path_id` del plan apunta a geometría distinta.
