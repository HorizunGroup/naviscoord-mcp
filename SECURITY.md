# Política de seguridad

## Versiones soportadas

| Versión | Soporte |
|---|---|
| 0.2.2 | Sí |

El complemento de Navisworks se soporta contra Navisworks Manage 2024, 2025 y
2026.

## Reportar una vulnerabilidad

**No abras un issue público.** Un informe de vulnerabilidad en un issue queda
visible para todo el mundo antes de que exista el arreglo, que es exactamente
el reparto de información que hay que evitar.

Usa el reporte privado de GitHub:

1. Ve a la pestaña **Security** del repositorio.
2. **Report a vulnerability**.

Si esa opción no aparece todavía, **no la sustituyas por un issue**: escribe a
la organización HorizunGroup en GitHub y pide un canal privado antes de dar
ningún detalle.

### Qué incluir

- Versión de NavisCoord, de Navisworks y de Python.
- Qué superficie afecta: puente HTTP, archivo de sesión, rutas de salida,
  generación de PDF, exportación CSV, guardado.
- Pasos para reproducir. Si el disparador es contenido del modelo, un nombre
  de tipo o de familia mínimo que lo provoque es suficiente — **no envíes
  modelos de cliente**.
- Qué esperabas y qué pasó.

### Tiempos

| Etapa | Objetivo |
|---|---|
| Acuse de recibo | 5 días hábiles |
| Evaluación inicial | 10 días hábiles |
| Corrección o plan | 30 días |

Este es un proyecto pequeño sin equipo de seguridad dedicado; los plazos son
objetivos, no garantías contractuales.

## Alcance

Dentro de alcance:

- Escritura de archivos fuera de las raíces autorizadas.
- Ejecución de contenido del modelo como markup, fórmula o código.
- Exposición del token de sesión o de las rutas del documento sin autenticar.
- Elevación desde otro usuario local vía el runtime, el registro de sesiones
  o el directorio de exportación.
- Mutaciones que reportan éxito sin haberse aplicado.

Fuera de alcance:

- Un usuario local que ya puede leer tu `%LOCALAPPDATA%`. Ver
  [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md#lo-que-este-modelo-no-cubre).
- Vulnerabilidades de Autodesk Navisworks. Repórtalas a Autodesk.
- Vulnerabilidades de `mcp`, `reportlab` o `Pillow`. Repórtalas aguas arriba;
  avísanos si nos afecta el uso que hacemos.
- Un perfil malicioso. El perfil es configuración de confianza.

## Divulgación

Coordinada. Publicamos la corrección y el aviso a la vez, y damos crédito a
quien reportó salvo que prefiera lo contrario.
