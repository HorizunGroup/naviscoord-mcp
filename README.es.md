# NavisCoord

### Empieza la reunión de coordinación con decisiones, no con 10.000 filas de cruces.

**Conecta Autodesk Navisworks con ChatGPT Desktop Work, Claude Desktop, Claude Code y Codex.** Analiza interferencias, revisa la evidencia y prepara un plan de coordinación.

[English](README.md) · [Instalación](docs/INSTALL.md) · [Evidencia de verificación](docs/RELEASE-1.0-VERIFICATION.md) · [Descargas](https://github.com/HorizunGroup/naviscoord-mcp/releases/latest)

![De interferencias a decisiones](docs/assets/naviscoord-flow.svg)

> «Analiza el modelo abierto, explica cuáles son los problemas prioritarios y prepara el plan de trabajo. Muéstrame los cambios propuestos antes de aplicarlos».

**53 herramientas, un servidor y cuatro clientes.** El paquete de escritorio incluye su runtime: no necesitas instalar Python ni proporcionar una clave de API para ejecutar el MCP local. Navisworks y el cliente de IA mantienen sus propios requisitos de licencia y cuenta.

| Cliente | Instalación |
|---|---|
| **ChatGPT Desktop — Work** | Instalador del plugin local; después **Plugins → Personal → NavisCoord → Install** |
| **Codex** | El mismo plugin personal o registro del ejecutable mediante su CLI |
| **Claude Desktop** | Extensión `.mcpb` con el servidor incluido |
| **Claude Code** | Plugin `naviscoord-mcp@horizun-navis` o registro del ejecutable |

Para ChatGPT de escritorio se utiliza el plugin local. No requiere OpenAI Platform, túneles ni claves de API. [Documentación oficial](https://learn.chatgpt.com/docs/enterprise/plugin-management).

## Qué entrega

- Problemas priorizados con puntuaciones explicables y trazabilidad a los cruces originales.
- Grupos espaciales acotados, causas raíz propuestas y todos los elementos afectados.
- Inventario de pasos del modelo y comprobaciones de anfitrión y geometría.
- Revisiones guardadas e identificación de incidencias nuevas, persistentes y modificadas.
- Plan por especialidad, narrativa, PDF con imágenes y archivos para Revit y Power BI.
- Escrituras dirigidas al documento revisado, con controles de vigencia y comprobación posterior.

El motor exige evidencia para descartar contactos por diseño. No elimina un cruce solo porque un elemento pertenezca a una categoría. Una incidencia ausente en otra entrega tampoco se declara resuelta automáticamente.

## Primer uso

1. Cierra Navisworks y ejecuta [`Install-NavisCoord.ps1`](Install-NavisCoord.ps1) para instalar el complemento correspondiente.
2. Instala el paquete de tu cliente siguiendo [estas instrucciones](docs/INSTALL.md).
3. Abre la federación en Navisworks Manage y pide: **«Comprueba NavisCoord e identifica el documento activo antes de analizarlo»**.

El complemento funciona en Windows con **Navisworks Manage 2024, 2025 o 2026**. El motor también analiza exportaciones guardadas. Puedes adaptar disciplinas, tolerancias y criterios mediante el [perfil de ejemplo](profiles/example-profile.json).

## Verificación y privacidad

Consulta [el registro de pruebas del lanzamiento](docs/RELEASE-1.0-VERIFICATION.md): separa pruebas automatizadas, pruebas en Navisworks y comprobaciones en clientes. Las revisiones de directorios externos se registran por separado.

El servidor procesa el modelo localmente. Las respuestas solicitadas pueden incluir propiedades, coordenadas, nombres e imágenes que recibe tu cliente de IA. No se envía telemetría a Horizun. [Privacidad](docs/PRIVACY.md).

Los resultados apoyan la coordinación; las decisiones de ingeniería se revisan dentro del proyecto. Los archivos de entrega permiten continuar con [Horizun Revit MCP](https://github.com/HorizunGroup/horizun-revit-mcp) y [Horizun PBI MCP](https://github.com/HorizunGroup/horizun-pbi-mcp).

Si te resulta útil, comparte un caso reproducible sin datos confidenciales y añade una estrella al repositorio para que otros coordinadores lo encuentren.

MIT · [HorizunGroup](https://github.com/HorizunGroup) · Proyecto independiente de Autodesk, OpenAI y Anthropic.
