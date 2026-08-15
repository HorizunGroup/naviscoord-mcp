# Arquitectura

NavisCoord tiene tres piezas y una regla que las separa.

```
┌──────────────┐   MCP/stdio   ┌──────────────────┐  HTTP 127.0.0.1  ┌──────────────────┐
│  Claude Code │ ────────────► │  Servidor Python │ ───────────────► │  Add-in .NET     │
│  / Codex     │ ◄──────────── │  (naviscoord)    │ ◄─────────────── │  (Navisworks)    │
└──────────────┘   resúmenes   └──────────────────┘   JSON tipado    └──────────────────┘
                                       │                                      │
                                 motor de análisis                      documento vivo
                                 (puro, sin deps)                       (hilo de UI)
```

**La regla: la lógica de negocio vive UNA vez.** Los pasos de coordinación
(auditar, configurar, correr, agrupar) son servicios del add-in
(`CoordinationWorkflow`). Los botones de la cinta y las rutas HTTP llaman a
los mismos métodos; Python tipa, valida, llama y transforma. Dos
implementaciones de la misma decisión derivan, y la que nadie pulsa deriva en
silencio.

## Qué hace cada pieza

### Motor de análisis — `server/naviscoord/analysis/`

Puro Python, sin dependencias, sin Navisworks. Entra un export de choques,
salen problemas puntuados y explicados. Es lo que hace todo el proyecto
testeable sin abrir Navisworks.

Orden de etapas, que no es arbitrario:

```
etiquetar → filtrar → colapsar → congestión → puntuar → causa raíz → ordenar
```

Etiquetar primero porque todo lo demás depende de la disciplina. Filtrar
antes de colapsar para que el ruido no infle un clúster y finja un problema
sistémico. Congestión sobre problemas colapsados, no sobre choques crudos,
para que un cruce desordenado no parezca una zona saturada.

### Servidor MCP — `server/naviscoord/mcp_server.py`

Transporte y estado. Dos reglas de diseño:

- **Los datos pesados nunca llegan al modelo.** Un export son decenas de MB:
  se trae, se analiza y se cachea en este proceso, y a la conversación cruza
  solo un resumen ordenado. Lo que serían miles de filas se devuelve como una
  ruta a un archivo.
- **Las escrituras son en dos pasos.** Toda herramienta que modifica el
  documento tiene `dry_run=True` por defecto. Confirmar es una llamada
  aparte, y la respuesta reporta lo **verificado releyendo** el documento, no
  lo intentado.

El estado va atado a `(target_id, huella de documento, checksum de perfil)`.
Ver [SESSIONS.md](SESSIONS.md).

### Add-in — `addin/NavisCoord.Addin/`

.NET Framework 4.8 dentro del proceso de Navisworks. Compila contra el API de
cada versión (2024=v21, 2025=v22, 2026=v23) por separado.

| Archivo | Responsabilidad |
|---|---|
| `HttpBridge.cs` | Listener loopback, token, concurrencia, rutas sin documento |
| `Router.cs` | Tabla de rutas, lectura del documento, export de choques |
| `UiDispatcher.cs` | Marshalling al hilo de UI de Navisworks (cola acotada) |
| `SessionStore.cs` | Registro de sesiones por PID, DACL explícita, escritura atómica |
| `JobManager.cs` | Máquina de estados de trabajos, progreso honesto |
| `MutationContract.cs` | Envelope uniforme + huella + idempotencia |
| `CoordinationWorkflow.cs` | Los cuatro pasos, como servicios |
| `WorkflowText.cs` | Render a prosa para los diálogos de la cinta |
| `SaveHandlers.cs` | `document/save` y `document/save_as` verificados |
| `PathPolicy.cs` | Confinamiento de rutas de salida |
| `ProfileSchema.cs` | Validación estructural del perfil |
| `RulePrecedence.cs` | Precedencia de reglas y operadores de búsqueda |
| `CoordinationLogic.cs` | Niveles, series repetidas, pureza de vistas |

Los archivos **sin ninguna referencia a Autodesk** están así a propósito: son
los que tuvieron errores, y son los que `NavisCoord.Tests` compila y prueba en
un runner sin licencia.

## Por qué el add-in y no automatización externa

El API de Navisworks es de un solo hilo y solo existe dentro del proceso.
Cualquier cosa que toque el documento tiene que correr en el hilo de UI de
Navisworks; hacerlo desde otro hilo no lanza una excepción, corrompe o tumba
el proceso. Por eso todo pasa por `UiDispatcher`, y por eso las operaciones
largas son trabajos en vez de peticiones síncronas.

## Contratos entre piezas

| Contrato | Versión | Dónde se declara |
|---|---|---|
| Rutas HTTP | `naviscoord.api/2` | `Capabilities.ApiVersion` |
| Registro de sesiones | `naviscoord.session/2` | `SessionStore.ContractVersion` |
| Perfil | `naviscoord.profile/v1` | `ProfileSchemaVersion.Current` / `mcp_server.PROFILE_SCHEMA` |
| Export de choques | `naviscoord.clashexport/1` | `NavisContext.Schema` / `model.SCHEMA` |
| Handoff a otras herramientas | `naviscoord.coordination/1` | `interop.HANDOFF_SCHEMA` |

El servidor MCP y el add-in se actualizan por separado —el servidor con un
wheel, el add-in solo cuando alguien cierra Navisworks y ejecuta el
instalador— así que en cualquier momento hay dos versiones que nunca se
probaron juntas. `navis_capabilities` es lo que convierte «unknown_route» en
«actualiza el complemento a esta versión».

## Ver también

- [SECURITY-MODEL.md](SECURITY-MODEL.md) — qué protege qué y contra qué
- [SESSIONS.md](SESSIONS.md) — multiinstancia, targeting, estado
- [JOBS.md](JOBS.md) — operaciones largas y progreso
- [SAVING.md](SAVING.md) — guardado y el caso ACC/.nwfacc
- [PROFILES.md](PROFILES.md) — el esquema único
- [INSTALL.md](INSTALL.md) — instalación y Python requerido
