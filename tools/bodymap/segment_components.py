"""Passo 3: a edicao de segmentacao (cores chapadas) vira componentes conexos
coloridos, registrados no espaco da arte base, e um atlas numerado para a
rotulagem humana (componente -> slug, em `assignment.json`).

Entradas (pasta de trabalho): `base.png`, `segment.png`.
Saidas: `segment.registered.png`, `segment.components.npy/.json`,
`segment.atlas.png`.
"""
import json

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from align import apply, iou, register, silhouette
from common import BASE_NAME, H, REPO_ROOT, SEG_NAME, W, load_rgba, work

ATLAS_FONT = REPO_ROOT / "api" / "app" / "assets" / "fonts" / "inter" / "Inter-SemiBold.ttf"
K = 22  # clusters do k-means em Lab
MERGE_DELTA_E = 14  # funde clusters mais proximos que isto
MIN_AREA = 250  # componente menor que isto e ruido
ABSORB_PX = 4  # contorno/linha fina a <= 4 px de um componente herda o rotulo dele


def main() -> None:
    base = load_rgba(work(f"{BASE_NAME}.png"))
    seg = load_rgba(work(f"{SEG_NAME}.png"))

    bs, ss = silhouette(base), silhouette(seg)
    reg = register(bs, ss)
    seg_r = apply(seg, reg, cv2.INTER_NEAREST)
    Image.fromarray(seg_r.clip(0, 255).astype(np.uint8)).save(work(f"{SEG_NAME}.registered.png"))
    sr = silhouette(seg_r)

    # --- quantizacao de cor (Lab + k-means), so pixels opacos e saturados/claros ---
    rgb = seg_r[..., :3].astype(np.uint8)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    body = sr & bs
    colored = body & (hsv[..., 1] > 70) & (hsv[..., 2] > 60)  # cores da paleta
    pix = lab[colored]
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 0.5)
    cv2.setRNGSeed(7)
    _, labels, centers = cv2.kmeans(pix, K, None, crit, 4, cv2.KMEANS_PP_CENTERS)
    parent = list(range(K))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(K):
        for j in range(i + 1, K):
            if np.linalg.norm(centers[i] - centers[j]) < MERGE_DELTA_E:
                parent[find(j)] = find(i)
    cl = np.full((H, W), -1, np.int32)
    cl[colored] = np.array([find(int(lbl)) for lbl in labels.ravel()])

    # --- componentes conexos por cluster ---
    comp = np.zeros((H, W), np.int32)
    info = []
    nid = 0
    for c in sorted(set(cl[colored].tolist())):
        m = (cl == c).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lab_c, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
        for k in range(1, n):
            a = stats[k, cv2.CC_STAT_AREA]
            if a < MIN_AREA:
                continue
            nid += 1
            comp[lab_c == k] = nid
            mean_rgb = rgb[lab_c == k].mean(0)
            info.append({"id": nid, "area": int(a), "cx": round(float(cents[k][0]), 1), "cy": round(float(cents[k][1]), 1),
                         "rgb": [int(v) for v in mean_rgb], "view": "front" if cents[k][0] < W / 2 else "back"})

    # contorno/linhas finas entre regioes coloridas herdam o rotulo do vizinho
    # mais proximo (so a <= ABSORB_PX; o cinza 'resto' largo fica sem rotulo)
    unl = body & (comp == 0)
    if nid:
        dist, idx = cv2.distanceTransformWithLabels((comp == 0).astype(np.uint8), cv2.DIST_L2, 5,
                                                     labelType=cv2.DIST_LABEL_PIXEL)
        lut = np.zeros(idx.max() + 1, np.int32)
        zy, zx = np.where(comp > 0)
        lut[idx[zy, zx]] = comp[zy, zx]
        near = unl & (dist <= ABSORB_PX)
        comp[near] = lut[idx[near]]

    np.save(work(f"{SEG_NAME}.components.npy"), comp)
    res = {"registration": reg, "silhouette_iou_raw": round(iou(bs, ss), 4),
           "silhouette_iou_registered": round(iou(bs, sr), 4), "components": info}
    work(f"{SEG_NAME}.components.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "components"}, indent=1))
    print("componentes:", nid)

    # --- atlas: base escurecida + componentes com a cor media e o numero ---
    font = ImageFont.truetype(str(ATLAS_FONT), 15)
    atlas = np.zeros((H, W, 3), np.float32) + 10
    a = base[..., 3:4] / 255
    atlas = atlas * (1 - a) + base[..., :3] * a * 0.6
    for it in info:
        m = comp == it["id"]
        atlas[m] = atlas[m] * 0.3 + np.array(it["rgb"]) * 0.7
    edges = cv2.morphologyEx(comp.astype(np.uint16), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    atlas[edges] = (255, 255, 255)
    im = Image.fromarray(atlas.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    for it in info:
        d.text((it["cx"] - 6, it["cy"] - 8), str(it["id"]), fill=(0, 0, 0), font=font, stroke_width=2,
               stroke_fill=(255, 255, 255))
    im.save(work(f"{SEG_NAME}.atlas.png"))


if __name__ == "__main__":
    main()
