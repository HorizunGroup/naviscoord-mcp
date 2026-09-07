# NavisCoord tool reference

53 tools generated from the registered server. Names and input schemas are authoritative.

Document mutations check the intended document fingerprint. Where a tool exposes `dry_run`, inspect the preview before execution.

## `navis_analysis_state`

Lee la revisión del documento; identifica si el análisis cacheado necesita repetirse.

```json
{
  "properties": {},
  "title": "navis_analysis_stateArguments",
  "type": "object"
}
```

## `navis_analyze`

Extrae los cruces de Navisworks y corre el motor de coordinación.

Este es el comando central. Filtra ruido, colapsa cruces redundantes en
problemas reales, puntúa cada uno con justificación, detecta causas raíz
sistémicas y pliega los problemas que una sola decisión cierra.

Devuelve un resumen compacto — nunca la lista cruda — con los problemas
mejor puntuados y las causas raíz. El resultado completo queda en memoria
para navis_issue_detail, navis_apply_groups y el resto.

```json
{
  "properties": {
    "tests": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Tests"
    },
    "limit": {
      "default": 0,
      "title": "Limit",
      "type": "integer"
    },
    "top": {
      "default": 15,
      "title": "Top",
      "type": "integer"
    }
  },
  "title": "navis_analyzeArguments",
  "type": "object"
}
```

## `navis_apply_clash_rules`

Marca con un estado los cruces cuyos dos lados casan un par a ignorar.

Es el triaje explícito: el llamador nombra los pares y sus conjuntos, a
diferencia de `navis_run_rules_workflow`, que los deriva del perfil.

`sets_index`: {"EST": {"scope_model_contains": "-EST-",
                       "category_ids": ["-2000011"]}, ...}
`pairs`: [{"test": "EST vs HID", "a": "EST", "b": "HID"}]

Un par casa en cualquiera de los dos sentidos. Con `only_new=True` solo
toca los cruces en estado New, que es lo que hace la repetición inocua:
lo ya aprobado no se vuelve a tocar.

```json
{
  "properties": {
    "pairs": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Pairs",
      "type": "array"
    },
    "sets_index": {
      "additionalProperties": true,
      "title": "Sets Index",
      "type": "object"
    },
    "status": {
      "default": "Approved",
      "title": "Status",
      "type": "string"
    },
    "only_new": {
      "default": true,
      "title": "Only New",
      "type": "boolean"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    },
    "run_async": {
      "default": false,
      "title": "Run Async",
      "type": "boolean"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    }
  },
  "required": [
    "pairs",
    "sets_index"
  ],
  "title": "navis_apply_clash_rulesArguments",
  "type": "object"
}
```

## `navis_apply_groups`

Escribe los problemas calculados como grupos de clash en Navisworks.

Es lo que hace que el análisis aterrice en el .nwf que el equipo abre, en
vez de quedarse en un reporte aparte. Los nombres llevan prioridad y
responsable para que la lista sea legible dentro de Clash Detective.

Idempotente: el complemento reutiliza un grupo que ya exista con el mismo
nombre en lugar de duplicarlo, y `idempotency_key` hace que un reintento
devuelva el resultado original sin volver a tocar el documento.

```json
{
  "properties": {
    "limit": {
      "default": 30,
      "title": "Limit",
      "type": "integer"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    }
  },
  "title": "navis_apply_groupsArguments",
  "type": "object"
}
```

## `navis_audit_models`

Paso 0 — audita los modelos anexados ANTES de coordinar.

Comprueba co-ubicación por caja envolvente (un modelo publicado sin las
coordenadas compartidas correctas queda a kilómetros y TODOS sus tests dan
cero: el falso verde más peligroso de la coordinación), la nomenclatura
BEP, y la pureza de cada vista de coordinación — muros dentro del modelo
eléctrico delatan una plantilla de vista sin filtrar en el Revit de origen.

No modifica nada.

```json
{
  "properties": {
    "run_async": {
      "default": false,
      "title": "Run Async",
      "type": "boolean"
    }
  },
  "title": "navis_audit_modelsArguments",
  "type": "object"
}
```

## `navis_build_clash_matrix`

Genera la suite completa de tests disciplina contra disciplina.

Cada par recibe el tipo (Hard / HardConservative / Clearance) y la
tolerancia que dicta el perfil — estructura contra instalaciones a 1 mm,
holguras de mantenimiento a 50 mm.

Usa los conjuntos de selección que YA tiene el documento, emparejando el
nombre de cada uno con una especialidad. Un equipo que coordina ya los
tiene hechos, y construir otros paralelos obliga a recorrer el modelo
entero — que es lento y no hace falta. Si existe un conjunto `<prefijo> -
<CÓDIGO>` se usa ese y se ignoran los del proyecto para esa especialidad.

Los pares que no se pueden armar salen en `skipped` con el motivo: que no
se comparara estructura contra sanitaria y que no haya sanitaria en el
modelo son cosas distintas.

```json
{
  "properties": {
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    },
    "prefix": {
      "default": "NC",
      "title": "Prefix",
      "type": "string"
    },
    "replace_existing": {
      "default": false,
      "title": "Replace Existing",
      "type": "boolean"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    }
  },
  "title": "navis_build_clash_matrixArguments",
  "type": "object"
}
```

## `navis_build_search_sets`

Crea carpetas de search sets definidos por criterio, no por lista.

Complementa `navis_build_sets`, no lo reemplaza: aquel congela QUÉ
elementos se probaron, para que dos corridas de la matriz sigan siendo
comparables; éste guarda la REGLA, de modo que la plantilla sobreviva a
una actualización del federado. El ancla recomendada es independiente del
idioma —alcance por modelo y `CategoryId` numérico— porque el nombre de
categoría cambia con el idioma del Revit que publicó.

`folders`: [{"folder": "MEP", "scope_model_contains": "-MEP-",
             "sets": [{"name": "Tubería", ...criterios}]}]

Con `replace_existing=False` una carpeta que ya existe se respeta en vez
de recrearse: los clash tests la referencian, y recrearla rompería el
enlace y borraría resultados corridos.

```json
{
  "properties": {
    "folders": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Folders",
      "type": "array"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    },
    "replace_existing": {
      "default": true,
      "title": "Replace Existing",
      "type": "boolean"
    },
    "observe_categories": {
      "default": false,
      "title": "Observe Categories",
      "type": "boolean"
    },
    "run_async": {
      "default": false,
      "title": "Run Async",
      "type": "boolean"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    }
  },
  "required": [
    "folders"
  ],
  "title": "navis_build_search_setsArguments",
  "type": "object"
}
```

## `navis_build_sets`

Crea un conjunto de selección explícito por disciplina.

Conjuntos explícitos y no de búsqueda, a propósito: un search set se
reevalúa cuando cambia el modelo, y entonces dos corridas de la matriz
dejan de ser comparables. Un conjunto explícito es una declaración
congelada y auditable de qué se probó.

Con dry_run=True (por defecto) solo reporta cuántos elementos caerían en
cada disciplina.

```json
{
  "properties": {
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    },
    "prefix": {
      "default": "NC",
      "title": "Prefix",
      "type": "string"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    }
  },
  "title": "navis_build_setsArguments",
  "type": "object"
}
```

## `navis_cancel_job`

Cancela un trabajo, cuando cancelarlo es realmente seguro.

Uno que sigue en cola no ha tocado el documento: se descarta y ya. Uno en
curso está dentro de una llamada del API en el hilo de UI y no existe
interrupción soportada — abortar el hilo corrompe el documento en vez de
detener el trabajo. Si el paso recorre unidades, se detiene en el próximo
límite seguro y reporta «partial»; si es atómico, se te dice que no se
puede y que terminará solo.

```json
{
  "properties": {
    "job_id": {
      "title": "Job Id",
      "type": "string"
    }
  },
  "required": [
    "job_id"
  ],
  "title": "navis_cancel_jobArguments",
  "type": "object"
}
```

## `navis_capabilities`

Qué sabe hacer el complemento instalado, antes de pedírselo.

Versión de contrato, rutas disponibles, operaciones soportadas, versión de
Navisworks, si puede guardar y guardar como, si admite trabajos asíncronos
y qué versión de esquema de perfil lee.

El servidor MCP y el complemento se actualizan por separado, así que
consultar esto es lo que convierte «unknown_route» en «actualiza el
complemento a esta versión».

```json
{
  "properties": {
    "refresh": {
      "default": false,
      "title": "Refresh",
      "type": "boolean"
    }
  },
  "title": "navis_capabilitiesArguments",
  "type": "object"
}
```

## `navis_clash_image`

Renderiza UNA interferencia y dice si la imagen sirve.

Para afinar el encuadre antes de gastar veinticinco renders en un PDF, y
para comprobar una que salió mal. Devuelve las métricas completas —qué
cámara se usó, qué fracción de la pantalla ocupa cada elemento, cuántos
píxeles de cada color se contaron, qué intentos hubo y por qué— sin meter
la imagen en la conversación.

Con `save_to` escribe el PNG (dentro de las rutas autorizadas); sin él solo
informa. La imagen nunca se devuelve en base64 al modelo: son cientos de
kilobytes que no aportan nada a la decisión.

Para inspeccionar por qué se descartó una imagen, `keep_rejected=True`
guarda igualmente el mejor intento: mirar el encuadre rechazado es lo único
que distingue «el muro tapaba el tubo» de «el control se pasó de estricto».

```json
{
  "properties": {
    "issue_id": {
      "default": "",
      "title": "Issue Id",
      "type": "string"
    },
    "clash_guid": {
      "default": "",
      "title": "Clash Guid",
      "type": "string"
    },
    "save_to": {
      "default": "",
      "title": "Save To",
      "type": "string"
    },
    "camera_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Camera Mode"
    },
    "margin_percent": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Margin Percent"
    },
    "min_distance": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Min Distance"
    },
    "max_distance": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Max Distance"
    },
    "image_width": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image Width"
    },
    "image_height": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image Height"
    },
    "background_color": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Background Color"
    },
    "hide_unrelated_geometry": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Hide Unrelated Geometry"
    },
    "colorize_by_discipline": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Colorize By Discipline"
    },
    "show_clash_marker": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Show Clash Marker"
    },
    "show_level": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Show Level"
    },
    "show_grid": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Show Grid"
    },
    "render_quality": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Render Quality"
    },
    "min_screen_fraction": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Min Screen Fraction"
    },
    "min_pixel_fraction": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Min Pixel Fraction"
    },
    "max_attempts": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Max Attempts"
    },
    "keep_rejected": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Keep Rejected"
    },
    "overwrite": {
      "default": false,
      "title": "Overwrite",
      "type": "boolean"
    }
  },
  "title": "navis_clash_imageArguments",
  "type": "object"
}
```

## `navis_close_document`

Cierra el documento sin dejar una decisión de guardado a un diálogo.

`disposition` es obligatorio: `save` guarda en la ruta actual antes de
cerrar, `discard` descarta los cambios de forma explícita y
`require_clean` se niega si queda algo sin guardar. Exige la huella del
documento y por defecto solo muestra el plan (`dry_run=true`). Para un
archivo sin destino local, usa primero `navis_save_as`.

```json
{
  "properties": {
    "disposition": {
      "title": "Disposition",
      "type": "string"
    },
    "expected_document_fingerprint": {
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    }
  },
  "required": [
    "disposition",
    "expected_document_fingerprint"
  ],
  "title": "navis_close_documentArguments",
  "type": "object"
}
```

## `navis_color_by_priority`

Colorea en el modelo los elementos de los problemas más graves.

Rojo crítico, naranja alto, amarillo medio. Colorea el lado que DEBE
MOVERSE, que es el que alguien tiene que encontrar.

Qué lado es se resuelve por la disciplina del propio elemento, no
comparando el responsable contra la pareja ordenada de disciplinas: esa
inferencia acertaba aproximadamente la mitad de las veces y pintaba la
estructura inamovible en rojo mientras el tubo que había que correr
quedaba gris.

```json
{
  "properties": {
    "limit": {
      "default": 50,
      "title": "Limit",
      "type": "integer"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    }
  },
  "title": "navis_color_by_priorityArguments",
  "type": "object"
}
```

## `navis_compare_snapshot`

Compara una revisión guardada con el análisis vigente: nuevos, persistentes y cambios.

```json
{
  "properties": {
    "path": {
      "title": "Path",
      "type": "string"
    }
  },
  "required": [
    "path"
  ],
  "title": "navis_compare_snapshotArguments",
  "type": "object"
}
```

## `navis_configure`

Paso 1 — crea los search sets por disciplina y la matriz de clash.

Lee el perfil corporativo y aplica exactamente lo mismo que el botón
«1. Configurar» de la cinta: no hay dos implementaciones que puedan
discrepar. Nunca destruye resultados ya corridos en silencio; un test con
definición cambiada pero con resultados se conserva y se reporta como
desactualizado, para que la decisión sea humana.

```json
{
  "properties": {
    "run_async": {
      "default": false,
      "title": "Run Async",
      "type": "boolean"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    }
  },
  "title": "navis_configureArguments",
  "type": "object"
}
```

## `navis_coordination_matrix`

Matriz de coordinación: decisiones abiertas por cruce de disciplinas.

Responde una pregunta distinta a la lista priorizada. La lista dice qué
abrir primero; la matriz dice **qué dos equipos tienen que sentarse**, que
es lo que decide a quién se convoca a la reunión.

```json
{
  "properties": {},
  "title": "navis_coordination_matrixArguments",
  "type": "object"
}
```

## `navis_current_target`

Qué instancia y qué documento están seleccionados ahora mismo.

```json
{
  "properties": {},
  "title": "navis_current_targetArguments",
  "type": "object"
}
```

## `navis_decisions`

Los problemas a atacar, ya sin los que otra decisión cierra.

Filtra opcionalmente por prioridad: critical, high, medium, low.

```json
{
  "properties": {
    "limit": {
      "default": 20,
      "title": "Limit",
      "type": "integer"
    },
    "priority": {
      "default": "",
      "title": "Priority",
      "type": "string"
    }
  },
  "title": "navis_decisionsArguments",
  "type": "object"
}
```

## `navis_discover`

Lee cómo está estructurado ESTE modelo, sin asumir nada.

Muestrea el modelo y reporta cada propiedad que existe con su cobertura
real, detecta sistemas de clasificación por la FORMA de los valores
(OmniClass, Uniclass, MasterFormat, UniFormat, IFC, códigos corporativos) —no por
el nombre del parámetro, que cambia según quién armó el modelo— y evalúa
qué propiedad separa de verdad las especialidades.

La prueba clave: una llave de disciplina agrupa por QUÉ SON las cosas; una
de nivel agrupa por DÓNDE ESTÁN. Se mide con información mutua contra la
categoría, así que funciona en cualquier idioma.

Después infiere el rol de cada grupo por su CONTENIDO, no por su nombre:
un grupo lleno de ductos es ventilación se llame como se llame.

```json
{
  "properties": {
    "sample": {
      "default": 30000,
      "title": "Sample",
      "type": "integer"
    }
  },
  "title": "navis_discoverArguments",
  "type": "object"
}
```

## `navis_exit`

Cierra el documento y sale de Navisworks de forma verificable.

Usa las mismas disposiciones explícitas de `navis_close_document`. El
puente responde antes de solicitar WM_CLOSE y este servidor comprueba que
el PID terminó. Si Navisworks sigue abierto —por ejemplo, por un diálogo
de otro complemento— devuelve `partial`, nunca un falso `completed`.
Con `dry_run=true` no cierra nada.

```json
{
  "properties": {
    "disposition": {
      "title": "Disposition",
      "type": "string"
    },
    "expected_document_fingerprint": {
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    },
    "verify_timeout": {
      "default": 15.0,
      "title": "Verify Timeout",
      "type": "number"
    }
  },
  "required": [
    "disposition",
    "expected_document_fingerprint"
  ],
  "title": "navis_exitArguments",
  "type": "object"
}
```

## `navis_group_levels`

Paso 3 — agrupa los resultados de cada test por nivel.

Es la organización con la que se preparan las incidencias en ACC. Solo
toca resultados sueltos: lo ya agrupado, por una corrida anterior o a
mano, se respeta, así el paso es re-ejecutable tras cada Update All.

Además renombra los choques con nombre por defecto (Clash 1, Conflicto 2)
a «NNN · elemento vs elemento» y detecta interferencias-tipo repetidas en
tres o más niveles, para abrir UNA incidencia por serie en vez de una por
piso. Recuerda guardar: nada de esto queda en disco hasta hacerlo.

```json
{
  "properties": {
    "run_async": {
      "default": true,
      "title": "Run Async",
      "type": "boolean"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    }
  },
  "title": "navis_group_levelsArguments",
  "type": "object"
}
```

## `navis_handoff`

Genera los artefactos que consumen las herramientas que consumen la coordinación.

Escribe:
  · coordination_handoff.json — el contrato compartido, con los problemas
    resueltos hasta ElementId de Revit y código de presupuesto
  · revit_worklist.json — el trabajo agrupado por modelo de autoría, listo
    para que el MCP de Revit lo ejecute modelo por modelo
  · fct_issues.csv + brg_issue_elements.csv + dimensiones — esquema
    estrella para Power BI, unido por el mismo código de presupuesto que
    ya usa el tablero

Reporta cuántos problemas son rastreables hasta Revit; los que no lo son
se declaran en 'caveats' en vez de desaparecer.

La carpeta se resuelve contra la política de salida (navis_output_policy).

```json
{
  "properties": {
    "directory": {
      "title": "Directory",
      "type": "string"
    }
  },
  "required": [
    "directory"
  ],
  "title": "navis_handoffArguments",
  "type": "object"
}
```

## `navis_health`

Estado del puente y del documento abierto en Navisworks.

Llama esto PRIMERO. Reporta la versión de Navisworks, qué documento está
activo, cuántos modelos tiene la federación y cuántos tests y resultados
de clash existen. Todo lo demás actúa sobre ese documento.

Si hay varias instancias de Navisworks abiertas, responde la seleccionada
con navis_target o, si no elegiste ninguna, la más reciente — y lo dice.

```json
{
  "properties": {},
  "title": "navis_healthArguments",
  "type": "object"
}
```

## `navis_hotspots`

Zonas del modelo con más severidad acumulada, para recorrerlas en orden.

`cell_size_m` discretiza el modelo en una rejilla, así que es un divisor:
con cero, el motor falla varias llamadas más adentro y el error no señala
al argumento que lo causó.

```json
{
  "properties": {
    "cell_size_m": {
      "default": 5.0,
      "title": "Cell Size M",
      "type": "number"
    },
    "limit": {
      "default": 10,
      "title": "Limit",
      "type": "integer"
    }
  },
  "title": "navis_hotspotsArguments",
  "type": "object"
}
```

## `navis_issue_detail`

Detalle completo de un problema: elementos, propiedades, por qué puntúa así.

```json
{
  "properties": {
    "issue_id": {
      "title": "Issue Id",
      "type": "string"
    }
  },
  "required": [
    "issue_id"
  ],
  "title": "navis_issue_detailArguments",
  "type": "object"
}
```

## `navis_job_status`

Estado, fase y avance de un trabajo en curso.

Se responde sin tocar el documento, así que sigue contestando mientras
Navisworks está ocupado con el trabajo. El avance es honesto: cuando el
paso recorre unidades reporta unidades y porcentaje; cuando es una sola
llamada atómica del API dice «indeterminate» y la fase, en vez de inventar
una barra.

```json
{
  "properties": {
    "job_id": {
      "title": "Job Id",
      "type": "string"
    }
  },
  "required": [
    "job_id"
  ],
  "title": "navis_job_statusArguments",
  "type": "object"
}
```

## `navis_jobs`

Los trabajos de esta sesión del puente, con el que esté activo.

```json
{
  "properties": {},
  "title": "navis_jobsArguments",
  "type": "object"
}
```

## `navis_list_search_sets`

Lista los conjuntos de selección del documento. No modifica nada.

Es la lectura con la que se comprueba lo que `navis_build_search_sets`
dejó escrito, y la única de este grupo que no exige huella ni destino
para mutar: sin escritura no hay nada que proteger.

`item_count` solo es real para conjuntos explícitos. Un search set guarda
la regla, no la lista, así que Navisworks no reporta un tamaño hasta
reevaluarla — se devuelve `null` en vez de un cero que se leería como
«vacío».

```json
{
  "properties": {},
  "title": "navis_list_search_setsArguments",
  "type": "object"
}
```

## `navis_list_tests`

Lista los tests de clash del documento con su estado y conteo.

```json
{
  "properties": {},
  "title": "navis_list_testsArguments",
  "type": "object"
}
```

## `navis_load_profile`

Carga un perfil de proyecto (pesos, tolerancias, reglas de disciplina).

Sin argumento recarga el perfil por defecto. El perfil es la única fuente
de criterio: cambiarlo cambia qué se considera grave, sin tocar código.

El perfil se instala en LOS DOS lados: aquí y en el complemento, que lo
recibe por el puerto autenticado y lo usa para `workflow/rules`,
Configurar, los search sets y la matriz. Antes solo se cargaba aquí y el
complemento seguía leyendo su propio archivo del disco, así que cargar un
perfil y correr las reglas aplicaba criterios distintos sin decirlo.

La respuesta trae `checksum`: el mismo valor a ambos lados identifica los
criterios con los que se produjo un resultado.

Cambiar de perfil INVALIDA el análisis en memoria: mueve severidad,
tolerancias, reglas de disciplina y filtro de ruido a la vez, así que un
resultado calculado con el anterior no está «algo desactualizado» —
responde a otra pregunta.

```json
{
  "properties": {
    "path": {
      "default": "",
      "title": "Path",
      "type": "string"
    }
  },
  "title": "navis_load_profileArguments",
  "type": "object"
}
```

## `navis_model_census`

Inventario de la federación: archivos, tamaños e histograma de categorías.

Úsalo antes de armar la matriz de tests. El histograma de categorías por
archivo es lo que permite decidir la disciplina de cada modelo con
evidencia en lugar de adivinar por el nombre del archivo.

```json
{
  "properties": {},
  "title": "navis_model_censusArguments",
  "type": "object"
}
```

## `navis_narrative_report`

El informe redactado: qué le pasa a este modelo, en prosa.

Separa lo que es problema de DISEÑO (decisiones que no se tomaron), de
MODELADO (el modelo no representa el alcance real) y de COORDINACIÓN (dos
especialidades que no han hablado) — que es la distinción que una lista de
cruces nunca hace y de la que depende a quién se le pide qué.

Emite además un veredicto de si el modelo está en condiciones de
coordinarse punto por punto.

```json
{
  "properties": {},
  "title": "navis_narrative_reportArguments",
  "type": "object"
}
```

## `navis_output_policy`

Dónde puede escribir este servidor, y cómo autorizar otra carpeta.

Toda salida —PDF, handoff, CSV, export, save_as— se resuelve contra esta
política. Una ruta fuera de las raíces permitidas se rechaza con el
motivo, en vez de escribirse en cualquier sitio que pida quien llama.

```json
{
  "properties": {},
  "title": "navis_output_policyArguments",
  "type": "object"
}
```

## `navis_pdf_report`

Genera el informe PDF de coordinación.

Portada con el resumen y las causas raíz, la matriz de coordinación con
la agenda sugerida, y **una página por interferencia** con su imagen 3D
renderizada por Navisworks, la ubicación, quién mueve, por qué importa y
qué hacer.

**Las imágenes se verifican, no se asumen.** De cada una se comprueba que
los dos elementos en conflicto están dentro del encuadre (proyectando sus
cajas por la cámara que se aplicó de verdad) y que ambos aparecen en los
píxeles, que es lo único que detecta que un muro tapaba el cruce. La que no
pasa se vuelve a tomar más abierta o aislando la geometría de por medio, y
si aun así no sirve se descarta contándolo, en vez de publicar una foto que
no muestra el problema. El resultado trae el recuento de pedidas,
generadas, fallidas y descartadas, con el motivo de cada descarte.

Todos los argumentos visuales son opcionales: sin ninguno, el informe sale
igual que siempre pero con el encuadre nuevo.

- `camera_mode`: `closeup` (por defecto), `context` o `plan`.
- `margin_percent`: aire alrededor del cruce, 25 por defecto.
- `min_distance` / `max_distance`: metros; acotan la cámara sin cambiar lo
  que entra en el cuadro.
- `hide_unrelated_geometry`: aísla los dos elementos ocultando lo demás;
  se restaura al terminar cada captura.
- `colorize_by_discipline`: un color estable por disciplina (por defecto).
  Con `False`, rojo es quien mueve y azul quien no se toca.
- `render_quality`: `draft`, `standard` (por defecto) o `high`.

Las imágenes se piden una por una y Navisworks tiene que posicionar cámara
y renderizar cada vez, así que 25 problemas tardan algo. Usa
with_images=False para un borrador rápido.

```json
{
  "properties": {
    "path": {
      "title": "Path",
      "type": "string"
    },
    "max_issues": {
      "default": 25,
      "title": "Max Issues",
      "type": "integer"
    },
    "with_images": {
      "default": true,
      "title": "With Images",
      "type": "boolean"
    },
    "overwrite": {
      "default": false,
      "title": "Overwrite",
      "type": "boolean"
    },
    "camera_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Camera Mode"
    },
    "margin_percent": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Margin Percent"
    },
    "min_distance": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Min Distance"
    },
    "max_distance": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Max Distance"
    },
    "image_width": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image Width"
    },
    "image_height": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image Height"
    },
    "background_color": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Background Color"
    },
    "hide_unrelated_geometry": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Hide Unrelated Geometry"
    },
    "colorize_by_discipline": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Colorize By Discipline"
    },
    "show_clash_marker": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Show Clash Marker"
    },
    "show_level": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Show Level"
    },
    "show_grid": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Show Grid"
    },
    "render_quality": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Render Quality"
    },
    "min_screen_fraction": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Min Screen Fraction"
    },
    "min_pixel_fraction": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Min Pixel Fraction"
    },
    "max_attempts": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Max Attempts"
    },
    "keep_rejected": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Keep Rejected"
    }
  },
  "required": [
    "path"
  ],
  "title": "navis_pdf_reportArguments",
  "type": "object"
}
```

## `navis_profile_info`

Qué perfil usará de verdad el complemento en el próximo paso.

Reporta esquema, checksum, nombre, cuándo se cargó, la sesión a la que
pertenece y de dónde salió: `explicit_mcp` si lo enviaste con
`navis_load_profile`, `plugin_default` o `packaged_default` si el
complemento lo encontró en disco.

Trae también el perfil del servidor, para que la pregunta «¿los dos lados
están usando el mismo?» se responda con dos checksums en la misma
respuesta en vez de con dos llamadas y una suposición.

```json
{
  "properties": {},
  "title": "navis_profile_infoArguments",
  "type": "object"
}
```

## `navis_propose_disciplines`

Propone el mapeo archivo → disciplina, para revisión humana.

No aplica nada. Un mapeo equivocado envenena en silencio toda la
priorización posterior y termina culpando al equipo incorrecto, así que
la propuesta se confirma antes de usarse.

```json
{
  "properties": {},
  "title": "navis_propose_disciplinesArguments",
  "type": "object"
}
```

## `navis_reset_appearance`

Quita todos los colores aplicados sobre el modelo.

Sin argumentos borra TODO override de apariencia del documento, incluidos
los que puso una persona a mano. Es la mutación más fácil de disparar por
error y la única sin dry_run, así que la huella se comprueba igual.

```json
{
  "properties": {
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    }
  },
  "title": "navis_reset_appearanceArguments",
  "type": "object"
}
```

## `navis_reset_profile`

Devuelve el complemento a su perfil por defecto (el del disco).

Deshace lo que hizo `navis_load_profile` en el complemento: a partir de
aquí los pasos que corren dentro de Navisworks vuelven a usar el archivo
que encuentre en disco, reportado como `plugin_default`. Útil para volver
al perfil corporativo de la máquina tras probar uno alternativo.

El servidor sigue con el perfil que tenga cargado; `navis_profile_info`
muestra los dos lados.

```json
{
  "properties": {},
  "title": "navis_reset_profileArguments",
  "type": "object"
}
```

## `navis_root_causes`

Patrones sistémicos detectados: cota errada, pasos faltantes, detalle repetido, zonas saturadas.

Esto es lo que comprime un ciclo de coordinación. Una causa raíz con
cuarenta problemas debajo se cierra con una decisión, no con cuarenta.

Devuelve las `limit` más grandes más un conteo por tipo. Un modelo real
genera decenas de causas del mismo tipo —48 sistemas mal trazados, 19
zonas saturadas— y volcarlas todas son 61 KB de texto casi repetido.

```json
{
  "properties": {
    "limit": {
      "default": 10,
      "title": "Limit",
      "type": "integer"
    }
  },
  "title": "navis_root_causesArguments",
  "type": "object"
}
```

## `navis_run`

Paso 2 — corre todos los clash tests (equivale a Run All).

Por defecto asíncrono: en un federado real esto retiene el hilo de UI de
Navisworks durante minutos, y esperar en la conexión HTTP solo consigue
que expire. Consulta el avance con navis_job_status.

El progreso se reporta por FASE, no por porcentaje: TestsRunAllTests es
una sola llamada atómica del API y cualquier barra sería inventada.

```json
{
  "properties": {
    "run_async": {
      "default": true,
      "title": "Run Async",
      "type": "boolean"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    }
  },
  "title": "navis_runArguments",
  "type": "object"
}
```

## `navis_run_rules_workflow`

Paso 4 — aplica las reglas residuales que declara el PERFIL.

Compone dos cosas y nada más: corre todos los tests **solo si** el bloque
de reglas del perfil lo pide con `run_tests`, y luego aplica esos pares
con `clash/apply_rules`. No agrupa, no colorea y no vuelve a construir
sets; si ya corriste los tests, deja `run_tests` en falso en el perfil
para no repetirlos.

La diferencia con `navis_apply_clash_rules` es de dónde salen los pares:
aquí del perfil, allí del llamador. Con un perfil sin bloque de reglas el
paso no es un error — devuelve «nada que aprobar», que con el estándar
actual es el estado normal, porque las exclusiones ya viven en las
selecciones de cada test.

**El perfil que usa este paso es el que tengas cargado.** Si lo enviaste
con `navis_load_profile`, ese es; si no, el complemento usa el suyo de
disco y lo declara como `plugin_default`. La respuesta trae
`profile_checksum` y `profile_source`, tanto en la vía síncrona como en el
resultado del job, así que se puede comprobar en vez de suponerlo.

Antes este paso leía SIEMPRE el archivo del complemento e ignoraba
en silencio el perfil cargado por MCP. `navis_profile_info` responde de un
vistazo si los dos lados coinciden.

```json
{
  "properties": {
    "run_async": {
      "default": true,
      "title": "Run Async",
      "type": "boolean"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    }
  },
  "title": "navis_run_rules_workflowArguments",
  "type": "object"
}
```

## `navis_run_tests`

Corre los tests indicados, o todos si no se especifica ninguno.

Reporta el conteo de resultados antes y después de cada test, para que un
test que no encontró nada se distinga de uno que no llegó a correr.

Correr un test escribe sus resultados dentro del documento, así que pasa
por el mismo guardia que el resto de las mutaciones aunque no tenga
dry_run: no hay ensayo posible de una corrida de clash.

```json
{
  "properties": {
    "tests": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Tests"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    }
  },
  "title": "navis_run_testsArguments",
  "type": "object"
}
```

## `navis_save`

Guarda el documento abierto en su propia ruta.

Todo lo que produce el flujo —sets, matriz, resultados, grupos, nombres—
vive solo en memoria hasta que alguien guarda: cerrar Navisworks sin
hacerlo descarta una corrida entera.

Exige `expected_document_fingerprint` (léelo de navis_health): guardar es
irreversible y no voy a escribir sobre un documento distinto del que
creías. Si el archivo viene de ACC (.nwfacc) esto se rechaza con la razón
— guardar encima NO publica nada en la nube y el siguiente sync de Desktop
Connector puede reemplazarlo: usa navis_save_as a un .nwf local.

```json
{
  "properties": {
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "run_async": {
      "default": false,
      "title": "Run Async",
      "type": "boolean"
    },
    "dry_run": {
      "default": false,
      "title": "Dry Run",
      "type": "boolean"
    }
  },
  "title": "navis_saveArguments",
  "type": "object"
}
```

## `navis_save_as`

Guarda una copia del documento en otra ruta (.nwf o .nwd).

Es la salida correcta para un archivo de ACC: un .nwfacc es la cara local
de un documento cuyo maestro vive en la nube, no un .nwf con otro nombre.
Guardar como .nwfacc se rechaza; guarda a un .nwf local y publica desde
Desktop Connector.

La ruta pasa por la política de salida del complemento (raíces
autorizadas, sin UNC ni rutas de dispositivo) y `overwrite` es false por
defecto. Después de escribir se verifica releyendo: que el archivo exista,
que tenga bytes, que la marca de tiempo se haya movido, que el documento
apunte al destino y que Navisworks ya no lo marque como modificado.

```json
{
  "properties": {
    "path": {
      "title": "Path",
      "type": "string"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "overwrite": {
      "default": false,
      "title": "Overwrite",
      "type": "boolean"
    },
    "run_async": {
      "default": false,
      "title": "Run Async",
      "type": "boolean"
    },
    "dry_run": {
      "default": false,
      "title": "Dry Run",
      "type": "boolean"
    }
  },
  "required": [
    "path"
  ],
  "title": "navis_save_asArguments",
  "type": "object"
}
```

## `navis_save_export`

Guarda el export crudo en disco, para reanalizar sin abrir Navisworks.

Útil para afinar el perfil: cambias un peso, corres `python -m naviscoord
analyze` sobre el archivo y ves moverse el ranking.

La ruta se resuelve contra la política de salida: solo se escribe bajo
las raíces autorizadas (`navis_output_policy` las lista) y nunca se
reemplaza un archivo existente sin overwrite=true.

```json
{
  "properties": {
    "path": {
      "title": "Path",
      "type": "string"
    },
    "overwrite": {
      "default": false,
      "title": "Overwrite",
      "type": "boolean"
    }
  },
  "required": [
    "path"
  ],
  "title": "navis_save_exportArguments",
  "type": "object"
}
```

## `navis_save_viewpoints`

Guarda un viewpoint por problema, para recorrerlos dentro de Navisworks.

```json
{
  "properties": {
    "limit": {
      "default": 20,
      "title": "Limit",
      "type": "integer"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    }
  },
  "title": "navis_save_viewpointsArguments",
  "type": "object"
}
```

## `navis_select_issue`

Selecciona en Navisworks los elementos de un problema.

Cambiar la selección es una mutación del documento, aunque no se guarde: se
exige la huella y se valida el target, como cualquier otra. Antes no lo
hacía, así que después de cerrar Torre A y abrir Torre B esta herramienta
mandaba los path id del análisis anterior y el complemento seleccionaba lo
que esos identificadores nombraran en el modelo nuevo.

```json
{
  "properties": {
    "issue_id": {
      "title": "Issue Id",
      "type": "string"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    }
  },
  "required": [
    "issue_id"
  ],
  "title": "navis_select_issueArguments",
  "type": "object"
}
```

## `navis_sessions`

Instancias de Navisworks activas, con su documento y su target_id.

Un coordinador tiene a menudo dos abiertas —2024 con el modelo archivado
y 2026 con el vivo, o dos proyectos a la vez—. Cada una publica su propia
sesión: aquí se ven todas, con cuál está seleccionada.

```json
{
  "properties": {},
  "title": "navis_sessionsArguments",
  "type": "object"
}
```

## `navis_set_disciplines`

Fija el mapeo archivo → disciplina, sobrescribiendo la heurística.

mapping: {"PROY-HVAC-T1.rvt": "HVAC", ...}

```json
{
  "properties": {
    "mapping": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Mapping",
      "type": "object"
    }
  },
  "required": [
    "mapping"
  ],
  "title": "navis_set_disciplinesArguments",
  "type": "object"
}
```

## `navis_set_status`

Marca los cruces de esos problemas con un estado (New/Active/Reviewed/Approved/Resolved).

```json
{
  "properties": {
    "issue_ids": {
      "items": {
        "type": "string"
      },
      "title": "Issue Ids",
      "type": "array"
    },
    "status": {
      "default": "Reviewed",
      "title": "Status",
      "type": "string"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    },
    "expected_document_fingerprint": {
      "default": "",
      "title": "Expected Document Fingerprint",
      "type": "string"
    },
    "idempotency_key": {
      "default": "",
      "title": "Idempotency Key",
      "type": "string"
    }
  },
  "required": [
    "issue_ids"
  ],
  "title": "navis_set_statusArguments",
  "type": "object"
}
```

## `navis_snapshot`

Guarda todas las incidencias con IDs persistentes, perfil y revisión del modelo.

```json
{
  "properties": {
    "path": {
      "title": "Path",
      "type": "string"
    },
    "overwrite": {
      "default": false,
      "title": "Overwrite",
      "type": "boolean"
    }
  },
  "required": [
    "path"
  ],
  "title": "navis_snapshotArguments",
  "type": "object"
}
```

## `navis_target`

Fija a qué instancia de Navisworks van las llamadas siguientes.

Obligatorio para mutar cuando hay más de una abierta: elegir en silencio
la última es cómo un plan calculado sobre Torre A termina escrito en
Torre B. Los ids salen de navis_sessions.

```json
{
  "properties": {
    "target_id": {
      "title": "Target Id",
      "type": "string"
    }
  },
  "required": [
    "target_id"
  ],
  "title": "navis_targetArguments",
  "type": "object"
}
```

## `navis_work_plan`

El plan de trabajo: las decisiones agrupadas en paquetes ejecutables.

Mil decisiones ordenadas no son un plan. Nadie trabaja una lista de 1.375
ítems, y entregarla es como termina un informe de coordinación sin leer:
correcto, completo e inútil.

Esto devuelve paquetes. Primero los SISTÉMICOS —una decisión que cierra
cientos, como aprobar el listado de pasos— porque al aterrizar encogen
todo lo demás. Después SESIONES: una pareja de especialidades, una zona,
dimensionada para que quepa en una reunión, con un responsable. El resto
queda contado como cola, no listado, porque fingir que es accionable
infla el plan de vuelta a mil filas.

```json
{
  "properties": {
    "limit": {
      "default": 15,
      "title": "Limit",
      "type": "integer"
    },
    "session_max": {
      "default": 25,
      "title": "Session Max",
      "type": "integer"
    }
  },
  "title": "navis_work_planArguments",
  "type": "object"
}
```
