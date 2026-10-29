"""Mapa corporal v1 (frente + costas) - render deterministico, so Pillow.

Arte e regioes aprovadas pelo usuario em 2026-10-03 ("caminho 3" do
prototipo, todas as fronteiras como estao). Assets versionados em
`app/assets/bodymap/v1/` (origem, aprovacao e como regenerar: README de la e
`tools/bodymap/`):

- `bodymap-base.webp`: arte grafite 1536x1024 RGBA (frente a esquerda, costas
  a direita), fundo transparente;
- `masks/<slug>.png`: uma mascara 1 bit por slug, rasterizada dos paths de
  `regions.json` (fonte canonica; a web usa os paths);
- `manifest.json`: caixas das vistas, aliases e os parametros da formula.

Formula (a MESMA da web - SVG `feColorMatrix` sobre a arte recortada pelo
path do slug - e do prototipo `tools/bodymap/README.md`):

    L      = luminancia da arte (modo "L" do Pillow: .299 R + .587 G + .114 B)
    n      = clamp((L - lum_lo) / (lum_hi - lum_lo))
    tinta  = cor * (K0 + K1 * n)                  K0 + K1 <= 1: sem desvio de matiz
    fundo  = arte * dim                           o resto escurece um pouco
    saida  = mix(fundo, tinta, mascara_suavizada * strength)
    glow   = (so com `RegionStyle.glow`) mascara desfocada (raio 18 px no canvas
             1536) misturada em "screen" com a cor a 50 %; o alfa da saida vira
             max(alfa da arte, 0,7 * glow) - o brilho vaza um pouco do corpo.

Regioes mais fracas sao pintadas primeiro (o principal fica por cima). Slug
desconhecido ou sem regiao (`knees`, `head`) e ignorado; aliases do manifest
(`abductors` -> `adductor`) sao resolvidos antes.

Determinismo: mesma entrada -> mesmos bytes (mesma versao do Pillow); nada
aleatorio, LUTs inteiras, assets lidos UMA vez por processo
(`load_body_map_assets`, cache de modulo). Sem numpy (as imagens Lambda nao o
tem).

Mudou a arte, as mascaras ou a formula? Nova pasta `v2/` + nova versao no
manifest; o card sobe `LAYOUT_VERSION`.
"""

from __future__ import annotations

import functools
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from PIL import Image, ImageChops, ImageFilter

ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "bodymap" / "v1"
MANIFEST_VERSION = "BODYMAP_V1"

FRONT = "front"
BACK = "back"
VIEWS = (FRONT, BACK)

ACCENT = "#FF9500"  # BRAND accent (frontend/src/theme.ts)
SECONDARY_STRENGTH = 0.45

# Medidas no canvas nativo (1536 px de largura); escalam com a largura da arte.
FEATHER_RADIUS = 1.0  # borda suave da mascara (min. 0,6 px)
GLOW_RADIUS = 18.0  # desfoque do brilho do principal
GLOW_GAIN = 1.1  # brilho = mascara desfocada * 1,1 (satura no 255)
GLOW_MIX = 0.5  # quanto do "screen" entra na cor
GLOW_ALPHA = 0.7  # quanto do brilho vira alfa fora do corpo
VIEW_GAP = 40  # px nativos entre a frente e as costas recortadas
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class BodyMapError(RuntimeError):
    """Asset do mapa corporal ausente ou invalido."""


@dataclass(frozen=True)
class RegionStyle:
    """Como uma regiao e destacada: `color` (#RRGGBB), `strength` (0..1,
    opacidade da tinta) e `glow` (brilho suave em volta - so o principal)."""

    color: str = ACCENT
    strength: float = 1.0
    glow: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.color, str) or not _HEX_RE.match(self.color):
            raise ValueError(f"Cor invalida para o mapa corporal: {self.color!r} (esperado #RRGGBB).")
        strength = float(self.strength)
        if math.isnan(strength):
            raise ValueError("strength invalido (NaN).")
        object.__setattr__(self, "strength", min(1.0, max(0.0, strength)))
        object.__setattr__(self, "glow", bool(self.glow))


# Os dois papeis do card e do muscle-map.png (o rodape do prototipo aprovado).
PRIMARY_STYLE = RegionStyle(ACCENT, 1.0, glow=True)
SECONDARY_STYLE = RegionStyle(ACCENT, SECONDARY_STRENGTH)


@dataclass(frozen=True)
class BodyMapAssets:
    """Arte, mascaras e parametros do manifest, ja prontos para compor."""

    canvas: tuple[int, int]
    views: Mapping[str, tuple[int, int, int, int]]
    aliases: Mapping[str, str]
    masks: Mapping[str, Image.Image]  # "L" 0/255 no tamanho do canvas
    alpha: Image.Image  # "L"
    lum: Image.Image  # "L" da arte
    dimmed: Image.Image  # "RGB" arte * dim (fundo de toda composicao)
    lum_lo: int
    lum_hi: int
    k0: float
    k1: float
    dim: float
    version: str = MANIFEST_VERSION
    slugs: tuple[str, ...] = field(default=())  # slugs com mascara, ordenados


def _box(value, name: str) -> tuple[int, int, int, int]:
    try:
        left, top, right, bottom = (int(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise BodyMapError(f"Caixa invalida no manifest: {name}.") from exc
    if not (left < right and top < bottom):
        raise BodyMapError(f"Caixa vazia no manifest: {name}.")
    return left, top, right, bottom


@functools.lru_cache(maxsize=4)
def load_body_map_assets(assets_dir: Path = ASSETS_DIR) -> BodyMapAssets:
    """Le arte + mascaras + manifest UMA vez por processo. Arquivo ausente ou
    incoerente -> `BodyMapError`."""
    assets_dir = Path(assets_dir)
    try:
        manifest = json.loads((assets_dir / "manifest.json").read_text(encoding="utf-8"))
        with Image.open(assets_dir / "bodymap-base.webp") as raw:
            base = raw.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise BodyMapError(f"Assets do mapa corporal indisponiveis em {assets_dir}: {exc}") from exc

    canvas = tuple(int(v) for v in manifest.get("canvas") or ())
    if canvas != base.size:
        raise BodyMapError(f"Arte {base.size} diferente do canvas do manifest {canvas}.")
    views = {name: _box(manifest.get("views", {}).get(name), name) for name in VIEWS}
    comp = manifest.get("compositor") or {}
    try:
        lum_lo, lum_hi = int(comp["lum_lo"]), int(comp["lum_hi"])
        k0, k1, dim = float(comp["K0"]), float(comp["K1"]), float(comp["dim"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BodyMapError("Parametros do compositor ausentes no manifest.") from exc
    if not (0 <= lum_lo < lum_hi <= 255) or k0 < 0 or k1 < 0 or k0 + k1 > 1.0 + 1e-9 or not 0 < dim <= 1:
        raise BodyMapError("Parametros do compositor fora da faixa no manifest.")

    masks: dict[str, Image.Image] = {}
    mask_dir = assets_dir / "masks"
    try:
        paths = sorted(mask_dir.glob("*.png"))
    except OSError as exc:
        raise BodyMapError(f"Mascaras indisponiveis em {mask_dir}: {exc}") from exc
    for path in paths:
        try:
            with Image.open(path) as raw:
                mask = raw.convert("L").point(lambda v: 255 if v > 127 else 0)
        except OSError as exc:
            raise BodyMapError(f"Mascara ilegivel: {path.name}") from exc
        if mask.size != base.size:
            raise BodyMapError(f"Mascara {path.name} {mask.size} fora do canvas {base.size}.")
        masks[path.stem] = mask
    if not masks:
        raise BodyMapError(f"Nenhuma mascara em {mask_dir}.")
    aliases = {str(k): str(v) for k, v in (manifest.get("aliases") or {}).items()}

    alpha = base.getchannel("A")
    # O WebP guarda cor arbitraria embaixo do alfa 0; zera para o brilho que
    # vaza do corpo nao herdar essa cor escondida (resultado independe do
    # encoder).
    visible = alpha.point(lambda v: 255 if v else 0)
    rgb = Image.composite(base.convert("RGB"), Image.new("RGB", base.size, (0, 0, 0)), visible)
    dim_lut = [int(v * dim) for v in range(256)]
    return BodyMapAssets(
        canvas=(base.width, base.height),
        views=views,
        aliases=aliases,
        masks=masks,
        alpha=alpha,
        lum=rgb.convert("L"),
        dimmed=rgb.point(dim_lut * 3),
        lum_lo=lum_lo,
        lum_hi=lum_hi,
        k0=k0,
        k1=k1,
        dim=dim,
        version=str(manifest.get("version") or ""),
        slugs=tuple(sorted(masks)),
    )


def canonical_slug(slug: str, assets: Optional[BodyMapAssets] = None) -> Optional[str]:
    """Slug com regiao no mapa (alias resolvido) ou `None` se nao ha regiao."""
    assets = assets or load_body_map_assets()
    name = str(slug or "").strip()
    name = assets.aliases.get(name, name)
    return name if name in assets.masks else None


def resolve_styles(
    slug_styles: Mapping[str, Optional[RegionStyle]], assets: Optional[BodyMapAssets] = None
) -> dict[str, RegionStyle]:
    """{slug canonico: estilo}. Ignora slug sem regiao e estilo vazio
    (strength 0). Dois nomes para a mesma regiao (alias): fica o mais forte
    (strength, depois glow); empate, o primeiro em ordem alfabetica."""
    assets = assets or load_body_map_assets()
    resolved: dict[str, RegionStyle] = {}
    for raw in sorted(slug_styles, key=str):
        style = slug_styles[raw]
        if style is None or style.strength <= 0:
            continue
        slug = canonical_slug(raw, assets)
        if slug is None:
            continue
        current = resolved.get(slug)
        if current is None or (style.strength, style.glow) > (current.strength, current.glow):
            resolved[slug] = style
    return resolved


def styles_for_roles(
    primary: Sequence[str] = (), secondary: Sequence[str] = ()
) -> dict[str, RegionStyle]:
    """Principais em laranja forte com brilho, secundarios em tom fraco; o
    principal vence se o slug aparece nos dois papeis."""
    styles: dict[str, RegionStyle] = {slug: SECONDARY_STYLE for slug in secondary if slug}
    styles.update({slug: PRIMARY_STYLE for slug in primary if slug})
    return styles


def _view_boxes(assets: BodyMapAssets, views: Sequence[str], crop_views: bool) -> list[tuple[int, int, int, int]]:
    if not views:
        raise ValueError("Pelo menos uma vista (front/back).")
    width, height = assets.canvas
    half = width // 2
    halves = {FRONT: (0, 0, half, height), BACK: (half, 0, width, height)}
    boxes = []
    for view in views:
        if view not in VIEWS:
            raise ValueError(f"Vista desconhecida: {view!r} (use {VIEWS}).")
        boxes.append(assets.views[view] if crop_views else halves[view])
    return boxes


def _spacing(crop_views: bool, gap: Optional[int]) -> int:
    """Espaco entre as vistas: `gap`, ou `VIEW_GAP` com recorte e 0 sem (as
    metades do canvas ja sao contiguas)."""
    if gap is None:
        return VIEW_GAP if crop_views else 0
    return max(0, int(gap))


def native_size(
    views: Sequence[str] = VIEWS,
    *,
    crop_views: bool = True,
    gap: Optional[int] = None,
    assets: Optional[BodyMapAssets] = None,
) -> tuple[int, int]:
    """Tamanho (largura, altura) da composicao em pixels da arte, antes de
    escalar: as vistas lado a lado, separadas por `_spacing`."""
    assets = assets or load_body_map_assets()
    boxes = _view_boxes(assets, views, crop_views)
    width = sum(b[2] - b[0] for b in boxes) + _spacing(crop_views, gap) * (len(boxes) - 1)
    height = max(b[3] - b[1] for b in boxes)
    return width, height


@functools.lru_cache(maxsize=64)
def _tint_luts(color: str, lo: int, hi: int, k0: float, k1: float) -> tuple[int, ...]:
    """LUT de 768 entradas (R, G, B) que leva L -> cor * (K0 + K1 * n)."""
    rgb = (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
    lut: list[int] = []
    for channel in rgb:
        for value in range(256):
            n = min(1.0, max(0.0, (value - lo) / (hi - lo)))
            lut.append(int(round(min(255.0, max(0.0, channel * (k0 + k1 * n))))))
    return tuple(lut)


def _scaled_lut(factor: float, cap: int = 255) -> list[int]:
    return [min(cap, int(v * factor)) for v in range(256)]


def _render_box(
    assets: BodyMapAssets, box: tuple[int, int, int, int], styles: Sequence[tuple[str, RegionStyle]]
) -> Image.Image:
    """Compoe a caixa `box` do canvas (RGBA). Cada regiao so mexe na caixa
    da sua mascara + a margem dos desfoques (3 sigmas: alem disso o
    `GaussianBlur` do Pillow ja e zero) - mesmo resultado, bem mais rapido."""
    out = assets.dimmed.crop(box)
    alpha = assets.alpha.crop(box)
    lum = assets.lum.crop(box)
    unit = assets.canvas[0] / 1536.0
    feather = max(0.6, FEATHER_RADIUS * unit)
    glow_radius = GLOW_RADIUS * unit
    glow_alpha: Optional[Image.Image] = None
    for slug, style in styles:
        mask = assets.masks[slug].crop(box)
        found = mask.getbbox()
        if found is None:
            continue  # a regiao nao aparece nesta vista
        margin = math.ceil(3 * (glow_radius if style.glow else feather)) + 4
        sub = (
            max(0, found[0] - margin),
            max(0, found[1] - margin),
            min(out.width, found[2] + margin),
            min(out.height, found[3] + margin),
        )
        region = mask.crop(sub)
        part = out.crop(sub)
        tint = lum.crop(sub).convert("RGB").point(
            list(_tint_luts(style.color, assets.lum_lo, assets.lum_hi, assets.k0, assets.k1))
        )
        soft = ImageChops.multiply(region.filter(ImageFilter.GaussianBlur(feather)), alpha.crop(sub))  # so no corpo
        part = Image.composite(tint, part, soft.point(_scaled_lut(style.strength)))
        if style.glow:
            glow = region.filter(ImageFilter.GaussianBlur(glow_radius)).point(_scaled_lut(GLOW_GAIN))
            layer = Image.new("RGB", part.size, style.color)
            part = Image.composite(ImageChops.screen(part, layer), part, glow.point(_scaled_lut(GLOW_MIX)))
            spread = Image.new("L", out.size, 0)
            spread.paste(glow, sub[:2])
            glow_alpha = spread if glow_alpha is None else ImageChops.lighter(glow_alpha, spread)
        out.paste(part, sub[:2])
    result = out.convert("RGBA")
    if glow_alpha is not None:
        alpha = ImageChops.lighter(alpha, glow_alpha.point(_scaled_lut(GLOW_ALPHA)))
    result.putalpha(alpha)
    return result


def render_body_map(
    slug_styles: Mapping[str, Optional[RegionStyle]],
    *,
    width: Optional[int] = None,
    views: Sequence[str] = VIEWS,
    crop_views: bool = True,
    gap: Optional[int] = None,
    assets: Optional[BodyMapAssets] = None,
) -> Image.Image:
    """Mapa corporal RGBA (fundo transparente) com `slug_styles` destacados.

    - `views`: quais vistas e em que ordem (`("front", "back")`, so uma...);
    - `crop_views=True`: cada vista recortada pela sua caixa do manifest e
      as vistas lado a lado com `gap` px nativos (padrao `VIEW_GAP`) - tira
      o vao grande entre as figuras; `False`: as metades do canvas original;
    - `width`: largura final em px (altura proporcional; reducao LANCZOS
      sobre a composicao nativa); `None` = tamanho nativo.
    """
    assets = assets or load_body_map_assets()
    boxes = _view_boxes(assets, views, crop_views)
    resolved = resolve_styles(slug_styles, assets)
    order = sorted(resolved.items(), key=lambda item: (item[1].strength, item[1].glow, item[0]))
    total_w, total_h = native_size(views, crop_views=crop_views, gap=gap, assets=assets)
    sheet = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    x = 0
    for box in boxes:
        part = _render_box(assets, box, order)
        sheet.paste(part, (x, (total_h - part.height) // 2))
        x += part.width + _spacing(crop_views, gap)
    if width is None or int(width) == total_w:
        return sheet
    target_w = max(1, int(width))
    target_h = max(1, int(math.floor(total_h * target_w / total_w + 0.5)))
    return sheet.resize((target_w, target_h), Image.Resampling.LANCZOS)


def fit_body_map(
    slug_styles: Mapping[str, Optional[RegionStyle]],
    max_width: int,
    max_height: int,
    *,
    views: Sequence[str] = VIEWS,
    crop_views: bool = True,
    gap: Optional[int] = None,
    assets: Optional[BodyMapAssets] = None,
) -> Image.Image:
    """`render_body_map` no maior tamanho que cabe em `max_width` x
    `max_height`, mantendo a proporcao."""
    assets = assets or load_body_map_assets()
    total_w, total_h = native_size(views, crop_views=crop_views, gap=gap, assets=assets)
    width = min(int(max_width), int(math.floor(int(max_height) * total_w / total_h)))
    if width < 1:
        raise ValueError("Caixa pequena demais para o mapa corporal.")
    image = render_body_map(slug_styles, width=width, views=views, crop_views=crop_views, gap=gap, assets=assets)
    if image.height > max_height:  # arredondamento da altura
        image = image.resize((image.width, int(max_height)), Image.Resampling.LANCZOS)
    return image
