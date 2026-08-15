# Contribuir

## Antes de nada: nunca subas datos de proyecto

Este repositorio es público y el trabajo real es privado. **Nunca** commitees:

- `.nwc`, `.nwd`, `.nwf`, `.nwfacc`, `.rvt`, `.ifc`, `.dwg`
- exports, handoff, CSV o PDF generados desde un modelo real
- capturas con nombres de cliente, de proyecto o de persona
- perfiles corporativos afinados para un cliente, siglas internas, ids de
  categoría de un proyecto concreto
- rutas con `C:\Users\<tu-usuario>`

`.gitignore` cubre lo previsible. Lo imprevisible es tu responsabilidad: mira
el diff antes de commitear.

## Poner en marcha

```bash
git clone https://github.com/HorizunGroup/naviscoord-mcp.git
cd naviscoord-mcp/server
pip install -e ".[dev]"
python -m pytest tests -q
```

Para el complemento hace falta Navisworks Manage instalado:

```powershell
$env:NavisworksDir = "C:\Program Files\Autodesk\Navisworks Manage 2026"
dotnet build addin\NavisCoord.Addin\NavisCoord.Addin.csproj -c Release
dotnet run --project addin\NavisCoord.Tests
```

Las pruebas C# **no** necesitan licencia: solo enlazan los archivos sin
referencias a Autodesk.

## Las reglas que hacen este proyecto lo que es

Si contribuyes, estas no son negociables, porque cada una existe por algo que
salió mal:

**1. Verificar releyendo, nunca contando llamadas.** Un contador de llamadas
que no lanzaron no prueba nada. Después de mutar, relee el documento y reporta
lo observado. Si la relectura no confirma el cambio, el estado es `partial` o
`failed`, jamás `completed`.

**2. Ninguna cifra inventada.** Si una API es atómica, reporta fase e
`indeterminate`. Una barra de progreso falsa es peor que ninguna.

**3. El texto del modelo es dato, no markup.** Todo valor externo se escapa
antes de entrar en un `Paragraph` y se neutraliza antes de entrar en un CSV.

**4. Las rutas de salida pasan por la política.** Siempre. Sin excepciones
«solo para esta herramienta».

**5. La lógica de negocio vive una vez.** Si un botón de la cinta y una ruta
HTTP hacen lo mismo, llaman al mismo método de `CoordinationWorkflow`. Nada
de reimplementar en Python lo que ya decide C#.

**6. Lo que puede probarse sin Navisworks, se escribe para poder probarse.**
Mantén la lógica pura en archivos sin referencias a Autodesk y enlázalos en
`NavisCoord.Tests`.

## Estilo

- **Python**: type hints, `from __future__ import annotations`, sin
  dependencias nuevas en el motor de análisis.
- **C#**: .NET Framework 4.8, solo miembros del API presentes desde v21
  (2024). Si algo solo existe desde v22, resuélvelo por reflexión y verifica
  el resultado.
- **Comentarios**: explica el *porqué*, sobre todo cuando el código parezca
  raro. Un comentario que repite el código es ruido; uno que dice «esto se
  hace así porque la alternativa obvia tumbaba Navisworks» vale una tarde.
- **Mensajes al usuario**: en español, concretos, con qué hacer a
  continuación.

## Pruebas

Toda corrección de un bug llega con una prueba que **falla con el código
anterior**. Si no puedes escribirla, dilo en el PR y explica por qué.

```bash
cd server && python -m pytest tests -q          # Python
dotnet run --project addin/NavisCoord.Tests     # C#
python scripts/build_artifacts.py               # wheel + sdist verificados
claude plugin validate .                        # manifiestos
```

## Pull requests

1. Rama desde `main`.
2. Un cambio por PR.
3. Rellena la plantilla: qué, por qué, cómo se probó.
4. CI en verde. **No bajes cobertura ni relajes una validación para lograr
   verde**; si una prueba estorba, discútelo en el PR.

## Código de conducta

Ver [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
