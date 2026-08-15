# Trabajos asíncronos

## Por qué

Navisworks ejecuta todo en su hilo de UI, y un Run All sobre un federado real
lo retiene durante minutos. Con el modelo petición/respuesta anterior:

- quien llamaba simplemente esperaba, el socket expiraba y no había forma de
  preguntar si la corrida había empezado, se había colgado o había terminado;
- peor, el bucle del listener se bloqueaba también, así que ni `health`
  respondía — la herramienta parecía muerta justo cuando estaba más ocupada.

Los trabajos arreglan el **reporte**, no el modelo de hilos. Sigue corriendo
una cosa a la vez porque el API no permite otra cosa. Lo que cambia es que
enviar devuelve de inmediato y `job/status` se responde desde una tabla en
memoria, sin tocar el documento, así que sigue vivo todo el tiempo.

## Estados

```
queued ──► running ──► verifying ──► completed
   │          │                  └──► partial
   │          └────────────────────► failed
   └──► cancelled                └──► cancelled (solo si es seguro)
```

| Estado | Qué significa |
|---|---|
| `queued` | Aceptado, no ha tocado el documento |
| `running` | Dentro del API de Navisworks |
| `verifying` | Releyendo el documento para contar lo aplicado |
| `completed` | Verificado; `verified` cubre todo lo aplicado |
| `partial` | Verificado < aplicado, o aplicado < pedido |
| `failed` | Excepción, o nada verificado habiendo aplicado |
| `cancelled` | Descartado en cola, o detenido en un límite seguro |

El estado final **sale del envelope de mutación**, no lo asigna el handler:
un paso que olvidó verificar reporta `failed`, que es la respuesta correcta a
«¿comprobaste?».

## Rutas y herramientas

| Ruta HTTP | Herramienta MCP |
|---|---|
| `job/submit` | `run_async=True` en `navis_run`, `navis_configure`, … |
| `job/status` | `navis_job_status(job_id)` |
| `job/list` | `navis_jobs()` |
| `job/cancel` | `navis_cancel_job(job_id)` |

`job/status`, `job/list`, `job/cancel`, `sessions` y `output_policy` se
responden **en el hilo del listener**, sin marshalling al hilo de UI. Por eso
siguen contestando mientras un trabajo lo ocupa.

## Progreso honesto

Un trabajo reporta una de dos formas, y nunca inventa la otra:

```jsonc
// Un paso que recorre unidades reales
"progress": {"kind": "units", "done": 7, "total": 24, "percent": 29.2}

// Un paso que es UNA llamada atómica del API
"progress": {
  "kind": "indeterminate",
  "note": "La API de Navisworks ejecuta este paso de forma atómica: hay fase, no porcentaje."
}
```

`TestsRunAllTests` es atómico: no hay unidades que contar y cualquier barra
sería inventada, así que se reporta fase. `workflow/group_levels` recorre
tests y sí reporta unidades.

## Cancelación

Se cancela solo donde cancelar es de verdad seguro:

- **En cola** → se descarta. No ha tocado nada.
- **Corriendo con unidades** → se registra la solicitud y el trabajo se
  detiene en el próximo límite seguro, reportando `partial` con lo ya
  verificado.
- **Corriendo y atómico** → **no se puede**, y se dice. No existe interrupción
  soportada dentro de una llamada del API en el hilo de UI; abortar el hilo
  corrompe el documento en vez de detener el trabajo.

`job/cancel` devuelve `cancelled: false` con el motivo en los dos últimos
casos. Nunca finge haber cancelado.

## Concurrencia

Dos mutaciones sobre el mismo documento no se entrelazan: un `job/submit` que
colisiona con un trabajo exclusivo en curso sobre la misma huella se rechaza
con `job_conflict` nombrando el trabajo activo. Una mutación sobre un
documento **distinto** no colisiona.

## Ejemplo

```
navis_run(run_async=True)
  → {"accepted": true, "job_id": "job-4f2a91c0d3", "state": "queued", ...}

navis_job_status("job-4f2a91c0d3")
  → {"state": "running", "phase": "corriendo todos los tests",
     "progress": {"kind": "indeterminate", ...}, ...}

navis_job_status("job-4f2a91c0d3")
  → {"state": "completed", "requested": 24, "applied": 24, "verified": 24,
     "result": { ...envelope de mutación completo... }}
```
