# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
Este proyecto sigue [SemVer](https://semver.org/lang/es/).

## [Unreleased]

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

[Unreleased]: https://github.com/HorizunGroup/naviscoord-mcp/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/HorizunGroup/naviscoord-mcp/releases/tag/v0.2.2
[0.2.1]: https://github.com/HorizunGroup/naviscoord-mcp/releases/tag/v0.2.1
