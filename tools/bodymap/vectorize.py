"""Passo 6: mascaras -> paths SVG por slug (potrace via `potracer`, puro
Python), viewBox 0 0 1536 1024, evenodd. Rasteriza de volta para medir a
fidelidade (IoU vetor x raster) e grava `regions.json` (fonte canonica, usada
pela web e pelo backend) + `regions.svg` (so para inspecao).

Entradas: `masks-raw/<slug>.png`. Saidas: `regions.json`, `regions.svg`,
`vectorize_report.json` (pasta de trabalho).
"""
import json

import numpy as np
import potrace
from PIL import Image, ImageChops, ImageDraw

from common import H, W, work


def fmt(v):
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


def trace(mask: np.ndarray):
    bm = potrace.Bitmap(~mask)  # potracer inverte (convencao PIL "1": escuro = preenchido)
    plist = bm.trace(turdsize=30, turnpolicy=potrace.POTRACE_TURNPOLICY_MINORITY, alphamax=1.0,
                     opticurve=True, opttolerance=0.4)
    d_parts, polys = [], []
    for curve in plist:
        sp = curve.start_point
        d = [f"M{fmt(sp.x)} {fmt(sp.y)}"]
        pts = [(sp.x, sp.y)]
        cur = (sp.x, sp.y)
        for seg in curve.segments:
            if seg.is_corner:
                d.append(f"L{fmt(seg.c.x)} {fmt(seg.c.y)}L{fmt(seg.end_point.x)} {fmt(seg.end_point.y)}")
                pts += [(seg.c.x, seg.c.y), (seg.end_point.x, seg.end_point.y)]
            else:
                d.append(f"C{fmt(seg.c1.x)} {fmt(seg.c1.y)} {fmt(seg.c2.x)} {fmt(seg.c2.y)} "
                         f"{fmt(seg.end_point.x)} {fmt(seg.end_point.y)}")
                p0, p1, p2, p3 = cur, (seg.c1.x, seg.c1.y), (seg.c2.x, seg.c2.y), (seg.end_point.x, seg.end_point.y)
                for i in range(1, 9):
                    t = i / 8
                    a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
                    pts.append((a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]))
            cur = (seg.end_point.x, seg.end_point.y)
        d.append("Z")
        d_parts.append("".join(d))
        polys.append(pts)
    return "".join(d_parts), polys


def rasterize(polys, ss=2):
    acc = Image.new("1", (W * ss, H * ss), 0)
    for pts in polys:
        im = Image.new("1", (W * ss, H * ss), 0)
        ImageDraw.Draw(im).polygon([(x * ss, y * ss) for x, y in pts], fill=1)
        acc = ImageChops.logical_xor(acc, im)  # evenodd
    return np.asarray(acc.convert("L").resize((W, H), Image.BILINEAR)) > 127


def main() -> None:
    mask_dir = work("masks-raw")
    regions, report = {}, {}
    for path in sorted(mask_dir.glob("*.png")):
        slug = path.stem
        m = np.asarray(Image.open(path).convert("L")) > 127
        d, polys = trace(m)
        r = rasterize(polys)
        regions[slug] = d
        report[slug] = {"iou_vector_vs_raster": round(float((m & r).sum() / (m | r).sum()), 4),
                        "subpaths": len(polys), "d_chars": len(d)}
        print(slug, report[slug])

    manifest = {"viewBox": [0, 0, W, H], "fillRule": "evenodd", "regions": regions}
    with open(work("regions.json"), "w", newline="\n") as fh:
        json.dump(manifest, fh, separators=(",", ":"))
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">']
    svg += [f'<path id="{slug}" fill-rule="evenodd" d="{d}"/>' for slug, d in regions.items()]
    svg.append("</svg>")
    work("regions.svg").write_text("\n".join(svg), encoding="utf-8")
    work("vectorize_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    total = sum(v["d_chars"] for v in report.values())
    mean_iou = float(np.mean([v["iou_vector_vs_raster"] for v in report.values()]))
    print("total chars", total, "IoU medio", round(mean_iou, 4))


if __name__ == "__main__":
    main()
