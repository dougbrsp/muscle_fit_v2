"""Passo 7: pacote final em `api/app/assets/bodymap/v1/`.

- `regions.json` (copiado da pasta de trabalho, byte a byte);
- `masks/<slug>.png`: 1 bit, rasterizadas A PARTIR DOS PATHS (fonte canonica,
  a mesma geometria que a web recorta), supersampling 4x;
- `bodymap-base.webp`: a arte (RGBA 1536x1024, WebP q90);
- `manifest.json`: caixas das vistas (silhueta +-12 px), faixa de luminancia
  do corpo (p5/p95), parametros da formula, aliases e sha256 de cada arquivo.

Entradas: `base.png` (mestre, NAO versionado) e `regions.json` da pasta de
trabalho. `--out` troca o destino (ex.: comparar com o pacote atual).
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from common import ALIASES, ASSETS, BASE_NAME, DB_SLUGS, EXTRA_SLUGS, H, W, work

K0, K1 = 0.30, 0.70  # K0 + K1 <= 1: a tinta nunca estoura o canal dominante
DIM = 0.82  # o resto da arte escurece um pouco para o destaque saltar
VIEW_PAD = 12  # folga das caixas das vistas em volta da silhueta
TOK = re.compile(r"[MLCZ]|-?\d+(?:\.\d+)?")


def path_polys(d: str):
    toks = TOK.findall(d)
    polys, cur, pts, i = [], (0.0, 0.0), [], 0
    while i < len(toks):
        t = toks[i]
        if t == "M":
            cur = (float(toks[i + 1]), float(toks[i + 2]))
            pts = [cur]
            i += 3
        elif t == "L":
            cur = (float(toks[i + 1]), float(toks[i + 2]))
            pts.append(cur)
            i += 3
        elif t == "C":
            p1 = (float(toks[i + 1]), float(toks[i + 2]))
            p2 = (float(toks[i + 3]), float(toks[i + 4]))
            p3 = (float(toks[i + 5]), float(toks[i + 6]))
            for k in range(1, 9):
                s = k / 8
                a, b, c, e = (1 - s) ** 3, 3 * (1 - s) ** 2 * s, 3 * (1 - s) * s ** 2, s ** 3
                pts.append((a * cur[0] + b * p1[0] + c * p2[0] + e * p3[0], a * cur[1] + b * p1[1] + c * p2[1] + e * p3[1]))
            cur = p3
            i += 7
        elif t == "Z":
            polys.append(pts)
            i += 1
        else:
            i += 1
    return polys


def raster(d: str, ss: int = 4) -> Image.Image:
    acc = Image.new("1", (W * ss, H * ss), 0)
    for pts in path_polys(d):
        im = Image.new("1", acc.size, 0)
        ImageDraw.Draw(im).polygon([(x * ss, y * ss) for x, y in pts], fill=1)
        acc = ImageChops.logical_xor(acc, im)  # evenodd
    return acc.convert("L").resize((W, H), Image.LANCZOS)


def body_lum_range(base: Image.Image) -> tuple[int, int]:
    """p5/p95 da luminancia (modo 'L') dentro do corpo (alfa > 128)."""
    lum = base.convert("RGB").convert("L")
    alpha = base.getchannel("A").point(lambda v: 255 if v > 128 else 0)
    hist = lum.histogram(mask=alpha)
    total = sum(hist)
    acc, lo, hi = 0, 0, 255
    for v, c in enumerate(hist):
        acc += c
        if acc >= total * 0.05 and lo == 0:
            lo = v
        if acc >= total * 0.95:
            hi = v
            break
    return lo, max(hi, lo + 1)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ASSETS)
    out = parser.parse_args().out
    (out / "masks").mkdir(parents=True, exist_ok=True)

    regions_src = work("regions.json")
    regions = json.loads(regions_src.read_text(encoding="utf-8"))["regions"]
    (out / "regions.json").write_bytes(regions_src.read_bytes())
    for slug, d in regions.items():
        mask = raster(d).point(lambda v: 255 if v > 127 else 0).convert("1")
        mask.save(out / "masks" / f"{slug}.png", optimize=True)

    master = work(f"{BASE_NAME}.png")
    base = Image.open(master).convert("RGBA")
    base.save(out / "bodymap-base.webp", quality=90, method=6)
    lo, hi = body_lum_range(base)
    a = np.asarray(base.getchannel("A")) > 128
    views = {}
    for side, (x0, x1) in {"front": (0, W // 2), "back": (W // 2, W)}.items():
        ys, xs = np.where(a[:, x0:x1])
        views[side] = [int(xs.min() + x0) - VIEW_PAD, int(ys.min()) - VIEW_PAD,
                       int(xs.max() + x0) + VIEW_PAD, int(ys.max()) + VIEW_PAD]

    files = {}
    for path in sorted([out / "bodymap-base.webp", out / "regions.json", *(out / "masks").glob("*.png")]):
        files[path.relative_to(out).as_posix()] = {"bytes": path.stat().st_size, "sha256": sha(path)}
    manifest = {
        "version": "BODYMAP_V1",
        "approved": "2026-10-03 pelo usuario (caminho 3 do prototipo, todas as fronteiras como estao)",
        "canvas": [W, H],
        "views": views,
        "compositor": {"lum_lo": lo, "lum_hi": hi, "K0": K0, "K1": K1, "dim": DIM,
                       "formula": "tint = color * (K0 + K1 * clamp((L - lo)/(hi - lo)))"},
        "slugs_db": DB_SLUGS,
        "slugs_extra": EXTRA_SLUGS,
        "aliases": ALIASES,
        "not_mapped": {"knees": "articulacao, nao e grupo muscular do banco; fica sem destaque na arte",
                       "head": "parte da arte base"},
        "source": {"base": "gpt-image-1.5 high 1536x1024 transparent (tools/bodymap/prompts/base.txt)",
                   "segmentation": "gpt-image-1.5 edit medium input_fidelity=high (tools/bodymap/prompts/segment.txt)",
                   "labels": "tools/bodymap/assignment.json (atribuicao humana unica) + watershed na arte base + potrace"},
        "master": {"file": "base.png (pasta de trabalho do pipeline; NAO versionado)",
                   "bytes": master.stat().st_size, "sha256": sha(master)},
        "files": files,
    }
    with open(out / "manifest.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, indent=1, ensure_ascii=False))
    for name, meta in files.items():
        print(name, meta["bytes"], meta["sha256"][:16])


if __name__ == "__main__":
    main()
