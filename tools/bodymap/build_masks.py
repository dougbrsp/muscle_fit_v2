"""Passo 5: componentes + rotulagem (`assignment.json`) -> uma mascara por slug,
com as fronteiras assentadas nos sulcos da arte base (watershed com marcadores
conservadores) e limpeza morfologica leve. Mede o alinhamento fronteira x
sulco antes/depois (`segment.mask_metrics.json`).

Entradas: `base.png`, `segment.components.npy`, `segment.grey.npy`.
Saidas: `masks-raw/<slug>.png`, `segment.final_overlay.png` (fronteiras em
laranja sobre a arte, para conferir).
"""
import json

import cv2
import numpy as np
from PIL import Image

from align import boundary_score, clean_mask, iou
from common import ASSIGNMENT, BASE_NAME, H, SEG_NAME, W, ensure, load_rgba, save_mask, work

ERODE_MARKER = 7  # marcador = nucleo da regiao atribuida (erosao eliptica)
REST_DISTANCE = 9  # corpo sem atribuicao a > 9 px de qualquer regiao vira 'resto'
SEED_GUARD = 28  # ... exceto perto das sementes de slugs (pescoco, trapezio)


def main() -> None:
    assign = json.loads(ASSIGNMENT.read_text(encoding="utf-8"))
    base = load_rgba(work(f"{BASE_NAME}.png"))
    comp = np.load(work(f"{SEG_NAME}.components.npy"))
    grey = np.load(work(f"{SEG_NAME}.grey.npy"))
    allc = comp.copy()
    allc[grey > 0] = grey[grey > 0]

    alpha = base[..., 3]
    body = alpha > 128
    slugs = list(assign["components"].keys()) + [
        s for s in assign["seeds"] if not s.startswith("_") and s not in assign["components"]
    ]
    sid = {s: i + 1 for i, s in enumerate(slugs)}
    rest = len(slugs) + 1
    background = len(slugs) + 2

    pre = {s: np.isin(allc, ids) & body for s, ids in assign["components"].items()}

    # --- marcadores ---
    markers = np.zeros((H, W), np.int32)
    k_marker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ERODE_MARKER, ERODE_MARKER))
    for s, m in pre.items():
        er = cv2.erode(m.astype(np.uint8), k_marker) > 0
        # componentes pequenos que somem na erosao: mantem o nucleo (erosao menor)
        small = cv2.erode(m.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        n, lab = cv2.connectedComponents(small.astype(np.uint8))
        for k in range(1, n):
            part = lab == k
            if not (er & part).any():
                er |= part
        markers[er] = sid[s]
    for cid in assign.get("rest_components", []):
        er = cv2.erode((allc == cid).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        markers[er & body] = rest
    for s, pts in assign["seeds"].items():
        lbl = rest if s == "_rest" else sid[s]
        for x, y, r in pts:
            cv2.circle(markers, (int(x), int(y)), int(r), int(lbl), -1)
    assigned_any = np.zeros((H, W), bool)
    for m in pre.values():
        assigned_any |= m
    far = cv2.distanceTransform((~assigned_any).astype(np.uint8), cv2.DIST_L2, 5) > REST_DISTANCE
    near_seed = np.zeros((H, W), np.uint8)
    for s, pts in assign["seeds"].items():
        if s.startswith("_"):
            continue
        for x, y, r in pts:
            cv2.circle(near_seed, (int(x), int(y)), int(r) + SEED_GUARD, 1, -1)
    markers[(markers == 0) & far & body & (near_seed == 0)] = rest
    markers[alpha < 20] = background

    # imagem do watershed: arte sobre fundo escuro, levemente suavizada
    rgb = base[..., :3] * (alpha[..., None] / 255) + 10 * (1 - alpha[..., None] / 255)
    rgb = cv2.GaussianBlur(rgb.astype(np.uint8), (0, 0), 1.0)
    ws = cv2.watershed(rgb, markers.copy())
    lines = ws == -1  # linhas de fronteira herdam do vizinho
    if lines.any():
        tmp = ws.copy()
        tmp[lines] = 0
        dil = cv2.dilate(tmp.astype(np.float32), np.ones((3, 3), np.uint8)).astype(np.int32)
        ws[lines] = dil[lines]

    final = {s: clean_mask((ws == sid[s]) & body, open_px=3, close_px=5, min_area_frac=0.02) & body for s in slugs}
    for alias, target in assign.get("aliases", {}).items():
        final[alias] = final[target].copy()

    metrics = {"slugs": {}}
    for s in slugs:
        p = pre.get(s)
        has_pre = p is not None and p.any()
        metrics["slugs"][s] = {
            "px": int(final[s].sum()),
            "snap_iou_pre_post": round(iou(p, final[s]), 3) if has_pre else None,
            "pre": boundary_score(base, p) if has_pre else None,
            "post": boundary_score(base, final[s]),
        }
    work(f"{SEG_NAME}.mask_metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    print(json.dumps({s: v["post"] for s, v in metrics["slugs"].items()}, indent=1))

    md = ensure(work("masks-raw"))
    for s, m in final.items():
        save_mask(m, md / f"{s}.png")

    vis = rgb.astype(np.float32).copy()
    lab = np.zeros((H, W), np.int32)
    for s in slugs:
        lab[final[s]] = sid[s]
    e = cv2.morphologyEx(lab.astype(np.float32), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    vis[e & body] = (255, 149, 0)
    Image.fromarray(vis.clip(0, 255).astype(np.uint8)).save(work(f"{SEG_NAME}.final_overlay.png"))


if __name__ == "__main__":
    main()
