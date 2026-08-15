# Soporte

## Antes de abrir nada

Casi todo lo que falla al empezar está en
[docs/INSTALL.md § Problemas frecuentes](docs/INSTALL.md#problemas-frecuentes).

Y llama estas tres, en este orden — responden la mayoría de las preguntas:

```
navis_health         → ¿hay puente? ¿qué documento? ¿código actualizado?
navis_capabilities   → ¿qué sabe hacer el complemento instalado?
navis_sessions       → ¿cuántas instancias hay y cuál está seleccionada?
```

## Dónde preguntar

| Qué | Dónde |
|---|---|
| Algo no funciona | [Issues](https://github.com/HorizunGroup/naviscoord-mcp/issues) → *Reportar un error* |
| Falta algo | [Issues](https://github.com/HorizunGroup/naviscoord-mcp/issues) → *Proponer una mejora* |
| Duda de uso | [Issues](https://github.com/HorizunGroup/naviscoord-mcp/issues) → *Pregunta* |
| Vulnerabilidad | **No un issue.** Ver [SECURITY.md](SECURITY.md) |

> Las dudas de uso van por Issues porque **Discussions no está habilitado** en
> este repositorio. Mandar a la gente a una pestaña que no existe cuesta más
> que ofrecerle la que sí. Si se habilita, esta fila cambia a Discussions y no
> hay nada más que mover.

## Qué hace útil un reporte

Lo que más tiempo ahorra:

1. La salida de `navis_health` **completa** (trae versiones de todo).
2. Las últimas líneas de `%LOCALAPPDATA%\NavisCoord\bridge.log`. El
   complemento nunca abre diálogos desde el puente: todo lo escribe ahí.
3. Qué esperabas y qué pasó.

**Anonimiza antes de pegar.** El log y `navis_health` traen la ruta y el
título del documento. Sustituye nombres de cliente y de proyecto; los números
—cuántos modelos, cuántos choques, qué versión— son lo que importa.

**Nunca adjuntes modelos.** Si el problema depende de contenido del modelo, un
nombre de tipo o de familia mínimo que lo reproduzca es suficiente.

## Lo que este proyecto no cubre

- **Navisworks en sí.** Errores del producto van a Autodesk.
- **Cómo coordinar.** Esto ordena y explica interferencias; qué hacer con
  ellas es criterio de obra.
- **Perfiles corporativos.** El esquema y la validación son públicos; el
  contenido de un perfil de empresa es de esa empresa.
- **Versiones de Navisworks fuera de 2024–2026.**

## Tiempos

Proyecto pequeño, mantenido junto al trabajo real. Sin SLA. Los reportes con
`navis_health` y log se atienden mucho antes, sencillamente porque se pueden
diagnosticar sin ida y vuelta.
