"""Ilustracoes SINTETICAS no formato que o prompt V2 pede a IA (duas cenas
empilhadas, sem texto, fundo transparente ou opaco) - para testar o
compositor (`app/exercise_card_composer.py`) e o fluxo dos cards sem chamar a
OpenAI. Tudo desenhado com Pillow, deterministico.

    illustration_png()                         # 1024x1536, fundo transparente
    illustration_png(background=(20, 20, 20)) # fundo opaco
    illustration_png(top=Box(...), bottom=Box(...))   # cenas onde quiser
    empty_png()                                # sem primeiro plano nenhum

Cada "cena" e um boneco simples (cabeca, tronco, bracos, pernas) + um
"equipamento" (barra com anilhas) dentro da caixa pedida; a cena de baixo
ganha uma seta laranja, como a da IA.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw

WIDTH = 1024
HEIGHT = 1536

SKIN = (205, 150, 120, 255)
SHIRT = (70, 70, 74, 255)
SHORTS = (25, 25, 28, 255)
STEEL = (150, 155, 160, 255)
PLATE = (40, 42, 46, 255)
ORANGE = (255, 149, 0, 255)


@dataclass(frozen=True)
class SceneBox:
    left: int
    top: int
    right: int
    bottom: int


# Cenas padrao: a de cima larga (deitado no banco), a de baixo mais alta e
# deslocada - tamanhos e posicoes diferentes de proposito.
DEFAULT_TOP = SceneBox(150, 140, 880, 620)
DEFAULT_BOTTOM = SceneBox(260, 900, 760, 1420)


def draw_scene(draw: ImageDraw.ImageDraw, box: SceneBox, *, arrow: bool) -> None:
    """Boneco + barra ocupando EXATAMENTE `box` (a tinta toca as 4 bordas)."""
    w = box.right - box.left
    h = box.bottom - box.top
    x0, y0 = box.left, box.top
    # Barra com anilhas no topo da caixa (toca topo, esquerda e direita).
    draw.rectangle((x0, y0 + int(h * 0.10), box.right - 1, y0 + int(h * 0.10) + 10), fill=STEEL)
    draw.rectangle((x0, y0, x0 + int(w * 0.06), y0 + int(h * 0.24)), fill=PLATE)
    draw.rectangle((box.right - 1 - int(w * 0.06), y0, box.right - 1, y0 + int(h * 0.24)), fill=PLATE)
    cx = x0 + w // 2
    head = int(min(w, h) * 0.08)
    draw.ellipse((cx - head, y0 + int(h * 0.28), cx + head, y0 + int(h * 0.28) + 2 * head), fill=SKIN)
    torso_top = y0 + int(h * 0.28) + 2 * head
    draw.rectangle((cx - int(w * 0.10), torso_top, cx + int(w * 0.10), y0 + int(h * 0.66)), fill=SHIRT)
    # Bracos ate a barra.
    draw.line((cx - int(w * 0.10), torso_top + 10, x0 + int(w * 0.25), y0 + int(h * 0.12)), fill=SKIN, width=14)
    draw.line((cx + int(w * 0.10), torso_top + 10, x0 + int(w * 0.75), y0 + int(h * 0.12)), fill=SKIN, width=14)
    # Pernas ate a base da caixa.
    draw.rectangle((cx - int(w * 0.10), y0 + int(h * 0.66), cx + int(w * 0.10), y0 + int(h * 0.74)), fill=SHORTS)
    draw.line((cx - int(w * 0.06), y0 + int(h * 0.74), cx - int(w * 0.14), box.bottom - 8), fill=SKIN, width=18)
    draw.line((cx + int(w * 0.06), y0 + int(h * 0.74), cx + int(w * 0.14), box.bottom - 8), fill=SKIN, width=18)
    draw.rectangle((cx - int(w * 0.20), box.bottom - 12, cx + int(w * 0.20), box.bottom - 1), fill=SHORTS)
    if arrow:
        ax = x0 + int(w * 0.88)
        draw.rectangle((ax - 6, y0 + int(h * 0.40), ax + 6, y0 + int(h * 0.70)), fill=ORANGE)
        draw.polygon(
            [(ax - 22, y0 + int(h * 0.42)), (ax + 22, y0 + int(h * 0.42)), (ax, y0 + int(h * 0.32))], fill=ORANGE
        )


def illustration(
    *,
    background: Optional[tuple[int, int, int]] = None,
    top: Optional[SceneBox] = DEFAULT_TOP,
    bottom: Optional[SceneBox] = DEFAULT_BOTTOM,
    size: tuple[int, int] = (WIDTH, HEIGHT),
) -> Image.Image:
    """`background=None` -> RGBA transparente; senao RGB opaco nessa cor.
    `top`/`bottom=None` -> metade sem cena."""
    if background is None:
        image = Image.new("RGBA", size, (0, 0, 0, 0))
    else:
        image = Image.new("RGBA", size, (*background, 255))
    draw = ImageDraw.Draw(image)
    if top is not None:
        draw_scene(draw, top, arrow=False)
    if bottom is not None:
        draw_scene(draw, bottom, arrow=True)
    return image if background is None else image.convert("RGB")


def to_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def illustration_png(**kwargs) -> bytes:
    return to_png(illustration(**kwargs))


def empty_png(*, transparent: bool = True, size: tuple[int, int] = (WIDTH, HEIGHT)) -> bytes:
    if transparent:
        return to_png(Image.new("RGBA", size, (0, 0, 0, 0)))
    return to_png(Image.new("RGB", size, (20, 20, 20)))
