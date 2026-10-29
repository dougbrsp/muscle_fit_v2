"""Registro (alinhamento) entre a arte base e uma edicao da OpenAI, e metricas.

Modelo: por metade (frente/costas) uma similaridade sem rotacao (escala s, dx,
dy) estimada pela silhueta (alfa). Busca grossa pela caixa delimitadora +
refino em grade maximizando o IoU das silhuetas. Tudo deterministico."""
from __future__ import annotations

import cv2
import numpy as np

from common import H, SPLIT_X, W


def silhouette(rgba: np.ndarray, thr: float = 128) -> np.ndarray:
    return rgba[..., 3] > thr


def iou(a: np.ndarray, b: np.ndarray) -> float:
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 1.0


def _half(mask: np.ndarray, side: str) -> np.ndarray:
    out = np.zeros_like(mask)
    if side == "front":
        out[:, :SPLIT_X] = mask[:, :SPLIT_X]
    else:
        out[:, SPLIT_X:] = mask[:, SPLIT_X:]
    return out


def _bbox(m: np.ndarray):
    ys, xs = np.where(m)
    return xs.min(), xs.max(), ys.min(), ys.max()


def _warp(img: np.ndarray, s: float, dx: float, dy: float, cx: float, cy: float, interp=cv2.INTER_NEAREST):
    """Leva coordenadas da imagem EDITADA para a BASE: x_b = cx + s*(x_e - cx) + dx."""
    M = np.array([[s, 0, cx - s * cx + dx], [0, s, cy - s * cy + dy]], np.float32)
    return cv2.warpAffine(img, M, (W, H), flags=interp, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def register(base_sil: np.ndarray, edit_sil: np.ndarray) -> dict:
    """Parametros por metade e IoU antes/depois."""
    res = {}
    for side in ("front", "back"):
        b = _half(base_sil, side)
        e = _half(edit_sil, side)
        bx0, bx1, by0, by1 = _bbox(b)
        ex0, ex1, ey0, ey1 = _bbox(e)
        cx, cy = (ex0 + ex1) / 2, (ey0 + ey1) / 2
        s0 = (by1 - by0) / max(1, (ey1 - ey0))
        dx0 = (bx0 + bx1) / 2 - cx
        dy0 = (by0 + by1) / 2 - cy
        e8 = e.astype(np.uint8)
        best = (iou(b, e), 1.0, 0.0, 0.0)
        before = best[0]
        cands = []
        for s in np.linspace(s0 - 0.02, s0 + 0.02, 5).tolist() + [1.0]:
            for dx in range(-6, 7, 3):
                for dy in range(-6, 7, 3):
                    cands.append((s, dx0 + dx, dy0 + dy))
        for s, dx, dy in cands:
            v = iou(b, _warp(e8, s, dx, dy, cx, cy) > 0)
            if v > best[0]:
                best = (v, s, dx, dy)
        _, s1, dx1, dy1 = best
        for s in np.linspace(s1 - 0.006, s1 + 0.006, 5):
            for dx in np.arange(dx1 - 2, dx1 + 2.01, 1):
                for dy in np.arange(dy1 - 2, dy1 + 2.01, 1):
                    v = iou(b, _warp(e8, s, dx, dy, cx, cy) > 0)
                    if v > best[0]:
                        best = (v, s, dx, dy)
        v, s, dx, dy = best
        res[side] = {"iou_before": round(before, 4), "iou_after": round(v, 4), "scale": round(float(s), 4),
                     "dx": round(float(dx), 1), "dy": round(float(dy), 1), "cx": float(cx), "cy": float(cy)}
    return res


def apply(img: np.ndarray, reg: dict, interp=cv2.INTER_NEAREST) -> np.ndarray:
    """Leva uma imagem/mascara do espaco da edicao para o da base."""
    out = np.zeros_like(img)
    for side in ("front", "back"):
        p = reg[side]
        part = np.zeros_like(img)
        if side == "front":
            part[:, :SPLIT_X] = img[:, :SPLIT_X]
        else:
            part[:, SPLIT_X:] = img[:, SPLIT_X:]
        w = _warp(part, p["scale"], p["dx"], p["dy"], p["cx"], p["cy"], interp)
        if side == "front":
            out[:, :SPLIT_X] = w[:, :SPLIT_X]
        else:
            out[:, SPLIT_X:] = w[:, SPLIT_X:]
    return out


def lum(rgba: np.ndarray) -> np.ndarray:
    return rgba[..., 0] * 0.2126 + rgba[..., 1] * 0.7152 + rgba[..., 2] * 0.0722


def edge_map(rgba: np.ndarray) -> np.ndarray:
    l = cv2.GaussianBlur(lum(rgba).astype(np.float32), (0, 0), 1.5)
    gx = cv2.Sobel(l, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(l, cv2.CV_32F, 0, 1)
    return np.hypot(gx, gy)


def clean_mask(m: np.ndarray, open_px: int = 3, close_px: int = 7, min_area_frac: float = 0.04) -> np.ndarray:
    """Abre (tira pontos), fecha (frestas), descarta componentes pequenos
    (< min_area_frac do maior) e preenche buracos."""
    m8 = m.astype(np.uint8)
    if open_px:
        m8 = cv2.morphologyEx(m8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_px, open_px)))
    if close_px:
        m8 = cv2.morphologyEx(m8, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m8, connectivity=8)
    if n <= 1:
        return m8 > 0
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = np.zeros(n, bool)
    keep[1:] = areas >= max(40, areas.max() * min_area_frac)
    m8 = keep[lab].astype(np.uint8)
    inv = (1 - m8).astype(np.uint8)
    n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    for i in range(1, n2):
        x, y, w, h, a = stats2[i]
        if x > 0 and y > 0 and x + w < W and y + h < H and a < 4000:
            m8[lab2 == i] = 1
    return m8 > 0


def boundary_score(art: np.ndarray, m: np.ndarray, search: int = 8) -> dict | None:
    """Alinhamento fronteira x sulco da arte: forca media do gradiente da arte
    sobre a fronteira da mascara (p90 do corpo = 1) e o deslocamento local
    (+-search px) que maximiza essa forca. Bem assentada => desloc. ~0."""
    body = silhouette(art)
    inner = cv2.erode(body.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    E = edge_map(art)
    norm = np.percentile(E[inner], 90)
    b = (cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0) & inner
    if b.sum() < 20:
        return None
    ys, xs = np.where(b)
    s0, best = None, (-1.0, 0, 0)
    for dy in range(-search, search + 1, 2):
        for dx in range(-search, search + 1, 2):
            v = float(E[np.clip(ys + dy, 0, H - 1), np.clip(xs + dx, 0, W - 1)].mean() / norm)
            if dx == 0 and dy == 0:
                s0 = v
            if v > best[0]:
                best = (v, dx, dy)
    return {"edge_at_boundary": round(s0, 3), "body_mean": round(float(E[inner].mean() / norm), 3),
            "best": round(best[0], 3), "best_shift": [best[1], best[2]]}
