"""PNG do mapa corporal (frente + costas) com os grupos musculares da ficha
destacados - `GET /workout-plans/{plan_id}/muscle-map.png` (pagina da ficha
na web; o app do relogio Zepp tem o download desligado por enquanto).

Desde 2026-10-03 usa o mapa corporal v1 (`app/body_map_render.py`: arte
grafite + mascaras por slug, aprovado pelo usuario), o mesmo do rodape do
card ilustrado de exercicio, no lugar dos poligonos do react-body-highlighter.
Os slugs de entrada sao os mesmos (`muscle_group_body_map`, via
`services.resolve_plan_muscle_slugs`).

Contrato (inalterado): PNG RGB; intensidade 2 = principal (laranja da marca
forte, com brilho), 1 = secundario (laranja em tom fraco), 0/ausente = sem
destaque; slug sem regiao no mapa e ignorado. `scale` multiplica a largura
(2.0 -> 420 px, a mesma do PNG antigo); fundo #141414, o painel da web onde a
imagem aparece.
"""

from __future__ import annotations

import io
import math

from PIL import Image

from .body_map_render import PRIMARY_STYLE, SECONDARY_STYLE, RegionStyle, render_body_map

WIDTH_PER_SCALE = 210  # px de largura por unidade de `scale`
PADDING_PER_SCALE = 6  # margem em volta das figuras
BACKGROUND_COLOR = (20, 20, 20)  # #141414 (BRAND.bgElevated / painel)


def _styles(slug_intensity: dict[str, int]) -> dict[str, RegionStyle]:
    styles: dict[str, RegionStyle] = {}
    for slug, intensity in slug_intensity.items():
        try:
            level = int(intensity)
        except (TypeError, ValueError):
            continue
        if level >= 2:
            styles[slug] = PRIMARY_STYLE
        elif level == 1:
            styles[slug] = SECONDARY_STYLE
    return styles


def render_muscle_map_png(slug_intensity: dict[str, int], scale: float = 2.0) -> bytes:
    """slug_intensity: {"chest": 2, "triceps": 1, ...} (2=primario, 1=secundario).
    Retorna PNG com as vistas anterior (esquerda) e posterior (direita) lado a lado.
    """
    if not (isinstance(scale, (int, float)) and math.isfinite(scale) and scale > 0):
        raise ValueError(f"scale invalido: {scale!r}")
    width = max(32, int(round(WIDTH_PER_SCALE * scale)))
    padding = max(1, int(round(PADDING_PER_SCALE * scale)))
    figure = render_body_map(_styles(slug_intensity or {}), width=width - 2 * padding)
    image = Image.new("RGB", (width, figure.height + 2 * padding), BACKGROUND_COLOR)
    image.paste(figure, (padding, padding), figure)

    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
