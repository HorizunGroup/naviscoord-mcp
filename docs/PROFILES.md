# El perfil

## Un solo formato

Antes había dos archivos que no compartían nada salvo la palabra «perfil»:
el del motor (`default.json` — disciplinas, pesos, tolerancias) y el del
add-in (`naviscoord-profile.json` — search sets, pares de clash, reglas de
ignorados). Dos archivos, dos juegos de defaults, dos sitios donde
equivocarse, y ninguna forma de que un lado notara que el otro se había
retocado.

Ahora es **un formato con dos mitades**, bajo una versión:

```json
{
  "$schema": "naviscoord.profile/v1",
  "name": "corporativo-2026",

  "disciplines": { },
  "severity":    { },
  "noise_filter":{ },
  "clustering":  { },
  "root_cause":  { },
  "interop":     { },

  "sets":   { "folders": [ ] },
  "clash":  { "pairs":   [ ] },
  "reglas": { "pairs":   [ ] }
}
```

Cualquiera de las dos mitades puede faltar —el motor no necesita pares de
clash y la cinta no necesita pesos de severidad— pero lo que esté presente se
valida.

## Versión

| Valor | Estado |
|---|---|
| `naviscoord.profile/v1` | Vigente |
| `naviscoord.profile/1` | Anterior, se acepta con aviso de migración |
| cualquier otro | Se rechaza, nombrando las versiones que sí se leen |

Sin `$schema` no valida: un perfil sin versión es un perfil que nadie puede
migrar después.

## Validación

`navis_profile_info` (add-in) y `navis_load_profile` (servidor) validan y
reportan **todos** los problemas, no el primero: un perfil se edita a mano en
un editor de texto, y enterarse de una errata por vez es cómo se pierde una
tarde.

Qué se detecta:

| Categoría | Ejemplos |
|---|---|
| Estructura | `sets` sin `folders`; carpeta sin `folder`; set sin `name` |
| Duplicados | Dos carpetas iguales; dos sets iguales; dos pares que generan el mismo nombre de test |
| Operadores | `no_equals` en vez de `not_equals` — antes caía en `default` y se convertía en un `equals` silencioso, así que un set que debía excluir algo lo incluía todo |
| Referencias | Un par de clash que apunta a una carpeta que `sets` no define |
| Unidades | `tolerance_m: 10` — la tolerancia va en METROS; probablemente querías milímetros |
| Pesos | Los de severidad deben sumar 1.00 |
| Secciones | Una sección desconocida (`setss`) se avisa: se ignoraría en silencio |

Un perfil que no valida **no se carga**. Sigue vigente el anterior. Meter uno
roto y reportar los problemas aparte es cómo una corrida acaba puntuada por
criterios que nadie eligió.

## Operadores de condición

| Operador | Qué hace |
|---|---|
| `equals` | Valor exacto. El único con reintento en modo entero |
| `not_equals` | Excluye por valor exacto |
| `contains` / `not_contains` | Subcadena |
| `wildcard` / `not_wildcard` | Patrón |
| `has` | La propiedad existe, sin comparar valor |

Dos detalles no obvios:

- **Las negaciones aceptan ítems sin la propiedad.** Excluir «todo lo que no
  diga ESTRUCTURA» no debe excluir además todo lo que no tiene Keynote.
- **Solo `equals` participa en el reintento entero.** Navisworks guarda
  `CategoryId` como int, así que un `equals` que no encuentra nada como texto
  se reintenta como entero — pero un `not_contains` sobre un Keynote en el
  mismo set no tiene forma entera, y dejarle vetar el reintento fue cómo
  disciplinas completas volvieron con cero elementos.

## Checksum

Cada perfil produce un checksum que viaja en el estado y en los resultados,
para poder trazar un informe hasta los criterios que lo produjeron.

Es **identidad**, no hash del archivo: las claves que empiezan por `_`
(comentarios) se excluyen y las demás se ordenan, así que reescribir una nota
no invalida ningún resultado cacheado, y cambiar un peso sí.

Los dos lados calculan el **mismo** checksum sobre el mismo perfil. La forma
canónica está escrita a mano en `naviscoord/profile.py` y reproducida en
`ProfileSchema.Canonical`, en vez de delegarla a `json.dumps` de un lado y a
un recorrido propio del otro — que es lo que había hasta 0.2.0, con un
comentario en cada archivo afirmando que reflejaba al otro. Nunca coincidieron
para ningún perfil: cada lado solo comparaba su salida consigo misma. El
vector fijado en `test_profile_sync.py` y en `LogicTests.CanonicalFormTests`
existe para que eso no pueda repetirse.

Cambiar de perfil descarta el análisis en memoria y los overrides de
disciplina. Mueve severidad, tolerancias, reglas y filtro de ruido a la vez:
un resultado calculado con el anterior no está «algo desactualizado»,
responde a otra pregunta.

## Un solo perfil vigente por instancia

`navis_load_profile` instala el perfil **en los dos lados**: lo valida aquí,
lo envía al complemento por el puerto autenticado y este lo vuelve a validar
antes de adoptarlo. A partir de ahí `workflow/rules`, Configurar, los search
sets, la matriz y los botones de la cinta usan **ese** perfil.

Hasta 0.2.0 no era así: `navis_load_profile` cargaba el perfil solo en el
servidor MCP y todo lo que corre dentro de Navisworks seguía leyendo su propio
archivo del disco. Cargar un perfil y correr las reglas aplicaba criterios que
el llamador acababa de reemplazar, sin decirlo en ninguna parte.

Reglas del contrato:

- se envía el **contenido** validado, nunca una ruta para que el complemento
  la abra — una ruta convertiría `profile/load` en un lector de archivos
  arbitrario corriendo dentro de Navisworks, con el token ya comprobado;
- el complemento **no lo escribe en disco**. Vive en memoria del proceso, que
  es lo que hace que dos instancias de Navisworks abiertas a la vez puedan
  trabajar con perfiles distintos sin pisarse;
- el tamaño está acotado en **bytes** (1 MB);
- **no se puede cambiar el perfil mientras hay una mutación corriendo**: el
  job congela el checksum al enviarse y `profile/load` responde
  `profile_locked`. Un trabajo que empieza con un perfil y verifica con otro
  no es un resultado, es una coincidencia.

`navis_profile_info` responde qué perfil usará de verdad el próximo paso, con
su origen:

| `source` | Significa |
|---|---|
| `explicit_mcp` | El que enviaste con `navis_load_profile` |
| `plugin_default` | El que el complemento encontró en `%NAVISCOORD_PROFILE%` o en `%LOCALAPPDATA%` |
| `packaged_default` | El que el instalador dejó junto al DLL |

Trae también el checksum del servidor y un `in_sync`, para que «¿los dos lados
están usando el mismo?» se responda con una llamada en vez de con dos y una
suposición.

`navis_reset_profile` deshace el envío: el complemento vuelve a su archivo de
disco. Es cómo se regresa al perfil corporativo de la máquina después de
probar uno alternativo.

## Dónde vive

| Ruta | Quién la usa |
|---|---|
| enviado por `navis_load_profile` | **Manda sobre todo lo demás**, para esa sesión |
| `server/naviscoord/profiles/default.json` | Motor. Genérico, dentro del paquete |
| `%NAVISCOORD_PROFILE%` | Add-in. Un archivo explícito, para una máquina con varios proyectos |
| `%LOCALAPPDATA%\NavisCoord\naviscoord-profile.json` | Add-in. Personalización local |
| junto al DLL del complemento | Add-in. La copia corporativa que despliega el instalador |

Sin nada enviado por MCP, el complemento busca en ese orden y declara lo que
encontró como `plugin_default` o `packaged_default`. Los botones de la cinta
no tienen servidor que les envíe nada, así que ese camino sigue existiendo —
pero deja de ser invisible.

## Motor público y configuración privada

**El motor no trae opinión propia.** Todo criterio —qué es grave, qué se
filtra, qué categoría es de qué disciplina, qué pares se prueban— sale del
perfil. Eso es lo que permite que dos proyectos discrepen sobre severidad sin
bifurcar el código.

La consecuencia práctica: el **esquema, la validación y los operadores** son
motor y son públicos; el **contenido** de un perfil corporativo —siglas
internas, nombres de carpetas, ids de categoría de un proyecto, alias de
disciplina— es configuración y no vive en este repositorio: aquí solo se
publica `profiles/example-profile.json`, deliberadamente neutral.

Para adaptar el motor a otra empresa se escribe un perfil, no se toca Python
ni C#.
