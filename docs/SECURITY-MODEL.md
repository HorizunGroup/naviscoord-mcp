# Modelo de seguridad

## La premisa

**Los datos del modelo son datos influidos por un tercero.** El nombre de una
familia, de un tipo, de un sistema, y todo texto compuesto a partir de ellos,
nace en un archivo de Revit que alguien más autoró y publicó a ACC. Nada de
eso puede rechazarse en la puerta: es contenido legítimo que tiene que llegar
al informe. Se neutraliza en el borde, una vez, donde se usa.

**Quien conduce la sesión MCP acaba de leer ese modelo.** Las rutas de salida,
los nombres de archivo y los argumentos de las herramientas los elige un
modelo que ya procesó contenido no confiable. Una herramienta que escribe
donde le pidan es una primitiva de escritura arbitraria, no una función de
exportación.

## Superficies y qué las protege

### 1. El puente HTTP

`http://127.0.0.1:8781` (o el siguiente libre hasta 8788).

- **Solo loopback.** Un endpoint remoto que llegue aquí se rechaza con 403.
- **Token por sesión.** 32 bytes de `RandomNumberGenerator`, comparados en
  tiempo fijo. Alcanzar el puente exige poder leer el AppData de ese usuario,
  no solo abrir un socket en localhost — que es lo que dejan abierto los demás
  puentes de Navisworks publicados.
- **Una sola respuesta sin autenticar: `GET /health`.** Devuelve `ok`,
  versión, PID, session_id, puerto, si hay documento abierto y si está
  ocupado. **No** devuelve la ruta del documento, ni el título, ni la lista de
  modelos, ni nada extraído del modelo, ni el token. `POST /health` con token
  da el detalle completo. Verbos distintos a propósito: es lo que impide que
  la respuesta mínima se convierta poco a poco en la real.
- **Todo lo demás exige token**, incluidas las lecturas.

### 2. El archivo de sesión

`%LOCALAPPDATA%\NavisCoord\sessions\<pid>-<session-id>.json`

Lleva el token que da control sobre ese Navisworks. Por eso:

- DACL explícita: usuario actual, SYSTEM y Administrators. Nada más.
- Herencia desactivada (`SetAccessRuleProtection(true, false)`), porque copiar
  las entradas heredadas es exactamente cómo se filtra un perfil permisivo.
- Escritura atómica: temporal + `File.Replace`. La DACL se aplica al temporal
  **antes** de escribir contenido, así que el token nunca es legible por
  todos ni por un instante.
- Auditoría al arrancar: si el directorio quedó alcanzable por otros, se
  escribe un aviso explícito en `bridge.log` en vez de fingir que está
  protegido.
- `navis_sessions` **redacta el token** de las entradas ajenas: estar
  autenticado contra una instancia no da los tokens de las demás.

En plataformas POSIX el runtime del launcher usa 0700 para directorios.

### 3. Las rutas de salida

Política central, misma en Python (`naviscoord/paths.py`) y en C#
(`PathPolicy.cs`). Aplica a PDF, handoff, CSV, JSON, export y `save_as`.

- Se resuelve y normaliza **antes** de autorizar, así `..` no puede salir y un
  symlink plantado dentro de una raíz no puede apuntar fuera.
- Solo se escribe bajo raíces configuradas o la carpeta de exportación por
  defecto (`%LOCALAPPDATA%\NavisCoord\exports`).
- Se rechazan: traversal, UNC (`\\host\share`), espacios de dispositivo
  (`\\.\`, `\\?\`), nombres reservados de DOS (`NUL`, `COM1`…), flujos
  alternos NTFS (`archivo.pdf:oculto`) y bytes nulos.
- `overwrite=false` por defecto en todo lo irreversible.

Configuración por variable de entorno, **no por argumento de herramienta**:
una política que quien llama puede ampliar no es una política.

```powershell
$env:NAVISCOORD_OUTPUT_ROOTS = "D:\Coordinacion\salidas;E:\entregas"
```

`navis_output_policy` lista las raíces vigentes.

### 4. El PDF (ReportLab)

`Paragraph` de ReportLab parsea un dialecto mini-HTML. Un elemento llamado
`<img src="file:///C:/Windows/win.ini">` no es un nombre gracioso: es una
petición de embeber un archivo local, y `<a href="\\atacante\share">` una de
alcanzar una ruta de red.

Todo valor externo pasa por `safety.escape_markup` antes de entrar en un
`Paragraph`. El formato interno se genera con helpers (`bold`, `italic`,
`coloured`) que ponen la etiqueta propia y escapan el contenido; nunca se
concatena un valor externo dentro de markup. Incluso el color se valida en
vez de interpolarse.

Las pruebas no comprueban la cadena escapada: le dan el resultado a ReportLab,
parsean y verifican que el texto renderizado **es igual al payload original**
— y una prueba paralela confirma que sin escapar ReportLab sí interpreta la
etiqueta, para que el guardia no se pruebe a sí mismo.

### 5. Los CSV

Excel, LibreOffice y Power BI interpretan una celda que empieza por `=`, `+`,
`-`, `@`, tabulador o retorno de carro como fórmula. Un tipo de Revit llamado
`=cmd|'/c calc'!A1` se ejecuta al abrir.

`safety.sanitize_csv` neutraliza esos comienzos, incluida la cabecera, en todo
CSV exportado — y deja intactos los números, **incluidos los negativos**:
coordenadas y profundidades lo son de rutina, y mangleárlas corrompería el
modelo de Power BI con mucha más fiabilidad que cualquier macro. El mismo
criterio en `RepeatSeries.CsvCell` del lado C#.

### 6. Las mutaciones

- `expected_document_fingerprint` en toda escritura: no se muta un documento
  distinto del que quien llama creía. Obligatorio en `save`/`save_as`.
- Con varias instancias abiertas y sin target elegido, **una mutación se
  rechaza**. Las lecturas sí eligen la más reciente y lo dicen: leer el modelo
  equivocado cuesta un minuto, escribir en él cuesta un ciclo de coordinación.
- `idempotency_key` colapsa reintentos sin duplicar trabajo.
- `verified` sale siempre de releer el documento. Un handler que no verificó
  reporta `failed`, no `completed`.

## Lo que este modelo NO cubre

- **Un usuario local malicioso con la sesión abierta.** Si alguien puede leer
  tu `%LOCALAPPDATA%`, tiene el token. La DACL sube el listón a «ser tú o ser
  administrador»; no lo elimina.
- **La integridad del modelo de origen.** Si el `.nwc` publicado ya trae
  geometría o propiedades erróneas, esto las procesa fielmente.
- **El contenido del perfil.** Un perfil es configuración de confianza: quien
  puede editarlo puede cambiar qué se considera grave. Se valida su
  estructura, no sus intenciones.
- **Cifrado en reposo.** Los informes y los handoff se escriben en claro.

## Reportar un problema

Ver [SECURITY.md](../SECURITY.md).
