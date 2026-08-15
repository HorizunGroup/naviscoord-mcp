## Qué cambia

<!-- Una o dos frases. Qué hace distinto el usuario después de esto. -->

## Por qué

<!-- El problema. Si corrige un bug: qué pasaba, y cómo se veía desde fuera.
     Los bugs que importan suelen parecer éxitos. -->

## Cómo se probó

<!-- Comandos y resultado. Si hizo falta Navisworks, dilo y di qué versión. -->

```
cd server && python -m pytest tests -q      →
dotnet run --project addin/NavisCoord.Tests →
```

## Lista de verificación

- [ ] Si corrige un bug, hay una prueba que **falla con el código anterior**.
- [ ] No hay datos de proyecto en el diff: ni modelos, ni exports, ni PDF, ni
      capturas, ni nombres de cliente, ni perfiles corporativos, ni rutas con
      mi usuario.
- [ ] Toda mutación nueva verifica **releyendo** el documento, y no puede
      reportar `completed` sin haberlo hecho.
- [ ] Ninguna cifra de progreso es inventada: si la API es atómica, hay fase e
      `indeterminate`.
- [ ] Todo texto del modelo que llega a un PDF va escapado; todo el que llega
      a un CSV va neutralizado.
- [ ] Toda ruta de salida nueva pasa por la política de rutas.
- [ ] Si un botón de la cinta y una ruta HTTP hacen lo mismo, llaman al mismo
      método de `CoordinationWorkflow`.
- [ ] Si cambia una versión, cambia en **los seis** sitios (ver
      [docs/RELEASING.md](../docs/RELEASING.md)).
- [ ] Si cambia una ruta o una capacidad, `Capabilities.Operations` lo refleja.

## Pruebas que no pude ejecutar

<!-- Sé explícito. "No tengo Navisworks 2024" es una respuesta útil;
     el silencio no. -->
