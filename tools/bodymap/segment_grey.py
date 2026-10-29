"""Passo 4: componentes CINZA da edicao de segmentacao.

A IA deixa em cinza parte das regioes que deveria colorir (no prototipo,
dorsal e lombar), alem de cabeca, maos, pes e articulacoes. Este passo numera
esses trechos (ids >= 1001, separados em tom escuro e claro) para a
rotulagem humana tambem poder atribui-los a um slug.

Reconstruido depois do prototipo de 2026-10-03, que fez este passo inline:
nas mesmas entradas acha os mesmos 17 componentes do `grey.npy` do prototipo
(IoU ~0,92 na uniao, ~0,8 por componente; ver README). Ao regenerar a arte,
rotule a partir do atlas desta execucao - os ids podem mudar.

Entradas: `segment.registered.png`, `segment.components.npy`, `base.png`.
Saidas: `segment.grey.npy`, `segment.grey.json`, `segment.grey_atlas.png`.
"""
import json

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from align import lum, silhouette
from common import BASE_NAME, SEG_NAME, load_rgba, work
from segment_components import ATLAS_FONT

FIRST_ID = 1001
TONES = (("dark", 60.0, 140.0), ("light", 140.0, 256.0))  # faixas de luminancia da edicao
MIN_AREA = 400


def main() -> None:
    base = load_rgba(work(f"{BASE_NAME}.png"))
    seg_r = load_rgba(work(f"{SEG_NAME}.registered.png"))
    comp = np.load(work(f"{SEG_NAME}.components.npy"))
    rgb = seg_r[..., :3].astype(np.uint8)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    body = silhouette(seg_r) & silhouette(base)
    colored = body & (hsv[..., 1] > 70) & (hsv[..., 2] > 60)
    cand = body & ~colored & (comp == 0)
    L = lum(seg_r)

    grey = np.zeros(comp.shape, np.int32)
    info = []
    nid = FIRST_ID
    for tone, lo, hi in TONES:
        m = (cand & (L >= lo) & (L < hi)).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lab, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
        for k in range(1, n):
            area = int(stats[k, cv2.CC_STAT_AREA])
            if area < MIN_AREA:
                continue
            part = lab == k
            grey[part] = nid
            info.append({"id": nid, "tone": tone, "area": area, "cx": float(cents[k][0]), "cy": float(cents[k][1]),
                         "mean": float(L[part].mean())})
            nid += 1

    np.save(work(f"{SEG_NAME}.grey.npy"), grey)
    work(f"{SEG_NAME}.grey.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print("componentes cinza:", len(info))

    font = ImageFont.truetype(str(ATLAS_FONT), 15)
    a = base[..., 3:4] / 255
    atlas = 10 * (1 - a) + base[..., :3] * a * 0.6
    for it in info:
        tint = (90, 200, 255) if it["tone"] == "dark" else (255, 230, 90)
        part = grey == it["id"]
        atlas[part] = atlas[part] * 0.3 + np.array(tint) * 0.7
    im = Image.fromarray(atlas.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    for it in info:
        d.text((it["cx"] - 14, it["cy"] - 8), str(it["id"]), fill=(0, 0, 0), font=font, stroke_width=2,
               stroke_fill=(255, 255, 255))
    im.save(work(f"{SEG_NAME}.grey_atlas.png"))


if __name__ == "__main__":
    main()
