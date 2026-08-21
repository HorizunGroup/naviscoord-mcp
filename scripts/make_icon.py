"""Genera el icono de NavisCoord.

El icono es un requisito de los directorios (el bundle MCPB lo declara, y los
formularios de envío lo piden), y también es lo único de este repositorio que
alguien va a ver antes de leer una sola línea de documentación.

Se genera con código, no se dibuja a mano, por dos razones: queda bajo control
de versiones como todo lo demás, y cambiar un color no obliga a reabrir un
editor de imágenes ni a confiar en que el PNG que hay en disco sea el que
alguien dice que es.

El motivo es lo que hace la herramienta: dos elementos de disciplinas
distintas que se cruzan, y el punto de cruce marcado. Dos barras y un punto
sobreviven al escalado a 16 px, que es donde mueren los logotipos con detalle.

    python scripts/make_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets" / "icon.png"

# Se dibuja a 4x y se reduce: es el antialiasing que ImageDraw no hace solo.
SCALE = 4
SIZE = 512
CANVAS = SIZE * SCALE

BACKGROUND = (18, 22, 28, 255)
DISCIPLINE_A = (31, 111, 235, 255)   # el brandColor ya declarado en .codex-plugin
DISCIPLINE_B = (242, 162, 58, 255)   # estructura
CLASH = (255, 82, 82, 255)


def bar(draw: ImageDraw.ImageDraw, degrees: float, width: int, color) -> None:
    """Una barra centrada, girada, que sale del lienzo por los dos extremos.

    Larga a propósito: una barra que termina dentro del icono se lee como un
    trazo suelto, y lo que tiene que leerse es que dos cosas se cruzan.
    """
    radians = math.radians(degrees)
    dx, dy = math.cos(radians), math.sin(radians)
    px, py = -dy, dx
    half = CANVAS
    centre = CANVAS / 2
    points = [
        (centre + dx * half + px * width / 2, centre + dy * half + py * width / 2),
        (centre + dx * half - px * width / 2, centre + dy * half - py * width / 2),
        (centre - dx * half - px * width / 2, centre - dy * half - py * width / 2),
        (centre - dx * half + px * width / 2, centre - dy * half + py * width / 2),
    ]
    draw.polygon(points, fill=color)


def main() -> int:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Fondo redondeado, recortado después contra sí mismo para que las barras
    # no se salgan por las esquinas.
    radius = int(CANVAS * 0.22)
    draw.rounded_rectangle([(0, 0), (CANVAS - 1, CANVAS - 1)], radius, fill=BACKGROUND)

    bars = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    pen = ImageDraw.Draw(bars)
    thickness = int(CANVAS * 0.155)
    bar(pen, -28, thickness, DISCIPLINE_A)
    bar(pen, 34, thickness, DISCIPLINE_B)
    pen.ellipse(
        [
            (CANVAS / 2 - thickness * 0.62, CANVAS / 2 - thickness * 0.62),
            (CANVAS / 2 + thickness * 0.62, CANVAS / 2 + thickness * 0.62),
        ],
        fill=CLASH,
    )

    # Las barras solo se pegan donde hay fondo: su propio alfa multiplicado
    # por la silueta redondeada, o se escapan por las esquinas.
    silhouette = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(silhouette).rounded_rectangle(
        [(0, 0), (CANVAS - 1, CANVAS - 1)], radius, fill=255
    )
    image.paste(bars, (0, 0), ImageChops.multiply(bars.split()[3], silhouette))

    final = image.resize((SIZE, SIZE), Image.LANCZOS)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    final.save(OUTPUT, "PNG")
    print(f"{OUTPUT.relative_to(ROOT)}: {SIZE}x{SIZE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
