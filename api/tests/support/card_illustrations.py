"""Fotos SINTETICAS no formato que o prompt V3 pede a IA (uma foto retrato
opaca por cena, sem texto) - para testar o compositor
(`app/exercise_card_composer.py`), o worker e o fluxo dos cards sem chamar a
OpenAI. Tudo desenhado com Pillow, deterministico.

    scene_photo_png(1024, 1536)                 # foto opaca, cor padrao
    scene_photo_png(1024, 1536, (60, 80, 110))  # outra cor (cena 2)
    jpeg_photo(1024, 768)                       # foto de referencia do admin (JPEG)
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

WIDTH = 1024
HEIGHT = 1536
START_COLOR = (120, 90, 70)
END_COLOR = (60, 80, 110)


def to_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _photo(w: int, h: int, color: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (w, h), color)
    draw = ImageDraw.Draw(image)
    for y in range(h):
        shade = int(40 * y / max(1, h - 1))
        draw.line((0, y, w - 1, y), fill=tuple(min(255, v + shade) for v in color))
    cx, cy = w // 2, h // 2
    draw.rectangle((cx - w // 8, cy - h // 6, cx + w // 8, cy + h // 6), fill=(235, 235, 235))
    return image


def scene_photo_png(w: int = WIDTH, h: int = HEIGHT, color: tuple[int, int, int] = START_COLOR) -> bytes:
    """Foto SINTETICA opaca (RGB) `w` x `h` no formato do V3: sem transparencia,
    cobrindo TODOS os pixels (sem borda vazia) - base na cor pedida, degrade
    vertical suave e um bloco claro no centro (o "sujeito"). Deterministica."""
    return to_png(_photo(w, h, color))


def jpeg_photo(w: int = WIDTH, h: int = 768, color: tuple[int, int, int] = (90, 90, 90)) -> bytes:
    """Foto de referencia do admin como o servidor grava (JPEG RGB)."""
    buffer = io.BytesIO()
    _photo(w, h, color).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
