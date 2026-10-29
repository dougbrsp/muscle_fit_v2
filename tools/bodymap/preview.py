"""Passo 8 (conferencia): prancha com combinacoes renderizadas pelo compositor
REAL do backend (`api/app/body_map_render.py`, so Pillow) a partir do pacote
em `api/app/assets/bodymap/v1/` - o que o card e o muscle-map.png vao mostrar.

Saida: `<pasta de trabalho>/preview.png`.
"""
import sys

from PIL import Image

from common import REPO_ROOT, work

sys.path.insert(0, str(REPO_ROOT / "api"))
from app import body_map_render as bm  # noqa: E402

COMBOS = [
    ("Peitoral + triceps", ["chest"], ["triceps"]),
    ("Quadriceps + gluteos", ["quadriceps"], ["gluteal"]),
    ("Biceps + antebraco", ["biceps"], ["forearm"]),
    ("Abdomen + obliquos", ["abs", "obliques"], []),
    ("Costas", ["upper-back", "trapezius"], ["back-deltoids", "lower-back"]),
    ("Posteriores + panturrilha", ["hamstring"], ["calves", "adductor"]),
]
TILE_W = 480
GAP = 16
BG = (20, 20, 20)


def main() -> None:
    tiles = [bm.render_body_map(bm.styles_for_roles(p, s), width=TILE_W) for _, p, s in COMBOS]
    cols = 3
    rows = (len(tiles) + cols - 1) // cols
    tile_h = max(t.height for t in tiles)
    sheet = Image.new("RGB", (GAP + cols * (TILE_W + GAP), GAP + rows * (tile_h + GAP)), BG)
    for i, tile in enumerate(tiles):
        x = GAP + (i % cols) * (TILE_W + GAP)
        y = GAP + (i // cols) * (tile_h + GAP)
        sheet.paste(tile, (x, y), tile)
    target = work("preview.png")
    sheet.save(target)
    print(target)


if __name__ == "__main__":
    main()
