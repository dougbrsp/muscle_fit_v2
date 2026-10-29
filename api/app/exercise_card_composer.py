"""Compositor do card ilustrado (Estrategia B) - Pillow puro, SEM rede.

    ilustracao da IA (PNG; duas cenas empilhadas, sem texto, de preferencia
    com fundo transparente)
    + conteudo do catalogo (`CardContent`: titulo, musculos e os slugs do
      mapa corporal de cada papel)
    + assets versionados (`CardAssets`: logo oficial, fontes Oswald e Inter,
      mapa corporal v1 de `app/assets/bodymap/v1/`)
        -> card final: PNG RGB 1024x1536, sem metadados

Principio (decisao do usuario, 2026-10-03): **o que o admin revisa e
exatamente o que e publicado**. O worker compoe logo depois da OpenAI e grava
o card composto como preview; a aprovacao so codifica
(`exercise_card_processor.encode_card`), sem recompor nada.

Layout (`LAYOUT_VERSION` = CARD_LAYOUT_V2; todas as medidas em pixels do card
1024x1536, numa secao so, abaixo):

    y   0-291  cabecalho (igual ao V1): logo oficial em LOGO_BOX (51,77 -
               225x186, mesma posicao de `exercise_card_processor.logo_placement`);
               titulo em CAIXA ALTA (Oswald Bold, #F5F5F5) em TITLE_BOX
               (x 300-973), centrado na altura do logo, 1-3 linhas com tamanho
               decrescente ate caber; divisor #2A2A2A em y=291
    y 317-1209 dois paineis empilhados de 436 px (#141414, borda #2A2A2A,
               cantos 24): linha de rotulo (selo laranja "1"/"2" + "Posicao
               inicial" / "Movimento", Oswald, igual ao V1) ACIMA da area da
               cena (886x342); a cena (recortada da ilustracao) centrada na
               area, as duas na MESMA escala
    y 1229-1485 rodape (painel de 922x256, mesmo estilo): a esquerda a
               silhueta do mapa corporal v1 (frente + costas, cada vista
               recortada pela caixa do manifest; principais em laranja forte
               com brilho, secundarios em tom fraco - `app/body_map_render.py`)
               numa caixa de 244x228; a direita "MUSCULOS" (Oswald SemiBold
               40, laranja) + "Principais: ..." / "Secundarios: ..." (Inter
               34 -> 30, nunca menor; quebra com recuo pendurado, ate 4
               linhas), bloco centrado na altura. Sem slug com regiao no
               mapa: sem silhueta, o texto ocupa o painel todo. Sem musculos:
               sem rodape, e os paineis ocupam a altura toda.

V1 -> V2 (2026-10-03): o texto do rodape do V1 (20-28 px) ficou pequeno no
celular e a area da silhueta estava vazia; o V2 poe a silhueta e sobe o texto
para >= 30 px. Os paineis encolheram de 498 para 436 px (cena 404 -> 342).

Extracao das cenas (`extract_scenes`): primeiro plano pelo alfa (fundo
transparente) ou pela distancia de cor ao fundo estimado nas bordas (fundo
opaco, com mascara suave - sem emenda retangular sobre o painel); corte na
linha mais vazia entre 40 % e 60 % da altura; caixa de cada cena com folga.
Sem primeiro plano em alguma metade -> `CardCompositionError`.

Determinismo: mesma entrada -> mesmos bytes (mesma versao do Pillow). Texto
com `ImageFont.Layout.BASIC` (nao depende de libraqm existir na imagem),
formas com supersampling e reducao `BOX`, nada aleatorio. Fontes, logo e mapa
corporal sao carregados uma vez por processo (`load_card_assets`, cache de
modulo).

Mudou a aparencia (medidas, cores, fontes, regras de extracao)? Suba
`LAYOUT_VERSION`.
"""

from __future__ import annotations

import functools
import io
import itertools
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import body_map_render as bodymap
from . import exercise_card_processor as processor
from .exercise_card_prompt import BRAND_COLORS, SCENE_GAP_RANGE, ExerciseCardContext, card_muscles, card_title

LAYOUT_VERSION = "CARD_LAYOUT_V2"

CARD_COMPOSITION_FAILED = "CARD_COMPOSITION_FAILED"


class CardCompositionError(ValueError):
    """Falha ao compor o card. `code` vai para o `error_code` da geracao;
    `reason` diz qual regra falhou (`invalid_illustration`, `no_foreground`,
    `empty_title`, `assets_missing`, `processing`)."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.code = CARD_COMPOSITION_FAILED
        self.reason = reason


# ===========================================================================
# LAYOUT - todas as medidas e cores do card moram aqui
# ===========================================================================


@dataclass(frozen=True)
class Box:
    """Retangulo em pixels do card; `right`/`bottom` EXCLUSIVOS (convencao do
    Pillow em `crop`/`getbbox`)."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, other: "Box") -> bool:
        return (
            self.left <= other.left and self.top <= other.top
            and other.right <= self.right and other.bottom <= self.bottom
        )

    def intersects(self, other: "Box") -> bool:
        return self.left < other.right and other.left < self.right and self.top < other.bottom and other.top < self.bottom


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


# Cores: tokens de `frontend/src/theme.ts` via `exercise_card_prompt.BRAND_COLORS`
# (um teste compara com o theme.ts).
COLOR_BACKGROUND = _rgb(BRAND_COLORS["background"])  # #0A0A0A fundo do card
COLOR_PANEL = _rgb(BRAND_COLORS["panel"])  # #141414 paineis
COLOR_BORDER = _rgb(BRAND_COLORS["border"])  # #2A2A2A bordas e divisor
COLOR_ACCENT = _rgb(BRAND_COLORS["accent"])  # #FF9500 selos e "MUSCULOS"
COLOR_TEXT = _rgb(BRAND_COLORS["text_primary"])  # #F5F5F5 titulo, rotulos, valores
COLOR_TEXT_SECONDARY = _rgb(BRAND_COLORS["text_secondary"])  # #E0E0E0 "Principais:"
COLOR_BADGE_TEXT = COLOR_BACKGROUND  # numero escuro no selo laranja (contraste ~9:1)

CARD_WIDTH = 1024
CARD_HEIGHT = 1536
MARGIN_X = 51  # margens laterais = margem do logo (5 % da largura)
MARGIN_BOTTOM = 51
CONTENT_LEFT = MARGIN_X
CONTENT_RIGHT = CARD_WIDTH - MARGIN_X  # 973

# Cabecalho. LOGO_BOX = `processor.logo_placement(1024, 1536, 1200, 992)` (o
# asset oficial); um teste prova que o logo real cabe exatamente nele.
LOGO_BOX = Box(51, 77, 276, 263)
TITLE_GAP = 24  # entre o logo e o titulo
TITLE_BOX = Box(LOGO_BOX.right + TITLE_GAP, LOGO_BOX.top, CONTENT_RIGHT, LOGO_BOX.bottom)  # 300,77 - 973,263
TITLE_MAX_SIZE = 72
TITLE_MIN_SIZE = 34
TITLE_SIZE_STEP = 2
TITLE_MAX_LINES = 3
TITLE_LINE_PITCH = 1.14  # distancia entre linhas de base, em multiplos do tamanho
DIVIDER_Y = 291
DIVIDER_THICKNESS = 2

# Paineis
CONTENT_TOP = DIVIDER_Y + DIVIDER_THICKNESS + 24  # 317
CONTENT_BOTTOM = CARD_HEIGHT - MARGIN_BOTTOM  # 1485
PANEL_GAP = 20
PANEL_RADIUS = 24
PANEL_BORDER = 2
PANEL_PADDING = 18  # entre a borda do painel e a area da cena (lados e base)
LABEL_ROW_HEIGHT = 76  # do topo do painel ao topo da area da cena
BADGE_DIAMETER = 44
BADGE_INSET_X = 20  # do lado esquerdo do painel ao selo
BADGE_INSET_Y = 18  # do topo do painel ao selo
BADGE_FONT_SIZE = 28
LABEL_FONT_SIZE = 32
LABEL_GAP = 14  # entre o selo e o texto do rotulo
PANEL_LABELS = ("Posição inicial", "Movimento")

# Rodape (so com musculos): um painel com a silhueta a esquerda e o texto
# a direita (estilo do mock aprovado, `rodape-card-mock.png` do prototipo).
FOOTER_HEIGHT = 256
FOOTER_GAP = 20  # entre o 2o painel e o rodape
FOOTER_HEADING = "MÚSCULOS"
FOOTER_HEADING_SIZE = 40
FOOTER_LINE_MAX_SIZE = 34
FOOTER_LINE_MIN_SIZE = 30  # piso: o V1 (20-28 px) ficou pequeno no celular
FOOTER_FIRST_LINE_GAP = 1.6  # linha de base do titulo -> da 1a linha (x tamanho da linha)
FOOTER_LINE_PITCH = 1.36  # entre linhas de base (x tamanho da linha)
FOOTER_MAX_LINES = 4  # linhas de musculos (com as continuacoes)
FOOTER_PRIMARY_LABEL = "Principais"
FOOTER_SECONDARY_LABEL = "Secundários"
FOOTER_TEXT_PADDING_X = 24  # borda do painel -> texto (lado direito; e esquerdo sem silhueta)
FOOTER_TEXT_PADDING_Y = 16
# Silhueta do mapa corporal v1 (frente + costas recortadas); a figura e
# encaixada (proporcao mantida) e centrada na caixa. 244x228 cabe a
# composicao do manifest (976x921) pela altura.
FOOTER_SILHOUETTE_INSET_X = 16
FOOTER_SILHOUETTE_INSET_Y = 14
FOOTER_SILHOUETTE_WIDTH = 244
FOOTER_SILHOUETTE_GAP = 32  # silhueta -> texto

# Extracao das cenas
ALPHA_THRESHOLD = 24  # alfa acima disto = primeiro plano (fundo transparente)
TRANSPARENT_MIN_FRACTION = 0.10  # >= 10 % dos pixels transparentes = usar o alfa
BACKGROUND_BORDER = 8  # faixa das bordas usada para estimar o fundo opaco
COLOR_DISTANCE_LOW = 16  # distancia (maior diferenca por canal) <= isto = fundo
COLOR_DISTANCE_HIGH = 48  # >= isto = primeiro plano; entre os dois, alfa suave
NOISE_FILTER_SIZE = 3  # abertura morfologica: some com pontos isolados < 3 px
# Faixa da altura onde procurar a linha mais vazia: a MESMA que o prompt pede
# vazia entre as cenas (`exercise_card_prompt.SCENE_GAP_RANGE`, fonte unica).
CUT_RANGE = SCENE_GAP_RANGE
SCENE_PAD = 12  # folga em volta da caixa de cada cena (px da ilustracao)
MAX_SCENE_SCALE = 1.5  # ampliar mais que isso borra a ilustracao
MIN_ILLUSTRATION_SIDE = 256
MAX_ILLUSTRATION_SIDE = 4096

SUPERSAMPLE = 4  # formas (cantos, selos) desenhadas 4x maiores e reduzidas

# Fontes (api/app/assets/fonts/README.md: origem, versao, SHA-256)
FONTS_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_OSWALD_MEDIUM = FONTS_DIR / "oswald" / "Oswald-Medium.ttf"
FONT_OSWALD_SEMIBOLD = FONTS_DIR / "oswald" / "Oswald-SemiBold.ttf"
FONT_OSWALD_BOLD = FONTS_DIR / "oswald" / "Oswald-Bold.ttf"
FONT_INTER_REGULAR = FONTS_DIR / "inter" / "Inter-Regular.ttf"
FONT_INTER_SEMIBOLD = FONTS_DIR / "inter" / "Inter-SemiBold.ttf"

ELLIPSIS = "…"


# ===========================================================================
# Entradas
# ===========================================================================


@dataclass(frozen=True)
class CardContent:
    """O que o card escreve e acende. `title` e o nome do exercicio como esta
    no catalogo (a caixa alta e do compositor); `primary_slugs` /
    `secondary_slugs` sao os slugs do mapa corporal (`muscle_group_body_map`)
    de cada papel - acendem na silhueta do rodape."""

    title: str
    primary_muscles: tuple[str, ...] = ()
    secondary_muscles: tuple[str, ...] = ()
    primary_slugs: tuple[str, ...] = ()
    secondary_slugs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CardAssets:
    """Logo oficial (RGBA) + caminhos das fontes + mapa corporal.
    `load_card_assets()` monta o padrao a partir dos arquivos versionados;
    `body_map=None` usa o mapa padrao (`body_map_render.load_body_map_assets`)."""

    logo: Image.Image
    oswald_medium: Path = FONT_OSWALD_MEDIUM
    oswald_semibold: Path = FONT_OSWALD_SEMIBOLD
    oswald_bold: Path = FONT_OSWALD_BOLD
    inter_regular: Path = FONT_INTER_REGULAR
    inter_semibold: Path = FONT_INTER_SEMIBOLD
    body_map: Optional[bodymap.BodyMapAssets] = None

    def font_paths(self) -> tuple[Path, ...]:
        return (self.oswald_medium, self.oswald_semibold, self.oswald_bold, self.inter_regular, self.inter_semibold)


def content_from_context(ctx: ExerciseCardContext) -> CardContent:
    """Conteudo do card a partir do contexto do catalogo, pelas MESMAS regras
    do prompt (`card_title`, `card_muscles`: 1o sinonimo, no maximo 4,
    principais primeiro). A silhueta acende TODOS os slugs dos principais e
    secundarios (estabilizadores nunca)."""
    primary, secondary = card_muscles(ctx)
    return CardContent(
        title=card_title(ctx),
        primary_muscles=tuple(primary),
        secondary_muscles=tuple(secondary),
        primary_slugs=tuple(ctx.primary_slugs),
        secondary_slugs=tuple(ctx.secondary_slugs),
    )


@functools.lru_cache(maxsize=256)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    """Fonte carregada uma vez por (arquivo, tamanho) no processo."""
    return _font(str(path), int(size))


def _load_body_map() -> bodymap.BodyMapAssets:
    try:
        return bodymap.load_body_map_assets()
    except bodymap.BodyMapError as exc:
        raise CardCompositionError(f"Mapa corporal indisponivel: {exc}", reason="assets_missing") from exc


@functools.lru_cache(maxsize=1)
def load_card_assets() -> CardAssets:
    """Logo oficial + fontes + mapa corporal versionados, lidos UMA vez por
    processo. Arquivo ausente ou ilegivel -> `CardCompositionError("assets_missing")`."""
    try:
        logo = processor.load_brand_logo()
    except processor.ImageProcessingError as exc:
        raise CardCompositionError(f"Logo oficial indisponivel: {exc}", reason="assets_missing") from exc
    assets = CardAssets(logo=logo, body_map=_load_body_map())
    for path in assets.font_paths():
        try:
            font(path, 24)
        except OSError as exc:
            raise CardCompositionError(f"Fonte ausente ou ilegivel: {path.name}", reason="assets_missing") from exc
    return assets


# ===========================================================================
# Geometria
# ===========================================================================


@dataclass(frozen=True)
class CardLayout:
    panels: tuple[Box, Box]
    label_rows: tuple[Box, Box]  # faixa do rotulo de cada painel (fora da cena)
    scenes: tuple[Box, Box]  # area onde a cena e encaixada
    footer: Optional[Box]  # painel do rodape
    footer_text: Optional[Box]  # onde o texto pode ter tinta
    silhouette: Optional[Box]  # onde a silhueta do mapa corporal e encaixada


def card_layout(has_muscles: bool, has_silhouette: Optional[bool] = None) -> CardLayout:
    """Caixas do card. `has_silhouette` (padrao = `has_muscles`): reserva a
    caixa da silhueta a esquerda do rodape; sem ela o texto ocupa o painel."""
    if has_silhouette is None:
        has_silhouette = has_muscles
    panels_bottom = CONTENT_BOTTOM - (FOOTER_HEIGHT + FOOTER_GAP if has_muscles else 0)
    panel_height = (panels_bottom - CONTENT_TOP - PANEL_GAP) // 2
    panels = []
    label_rows = []
    scenes = []
    for index in range(2):
        top = CONTENT_TOP + index * (panel_height + PANEL_GAP)
        panel = Box(CONTENT_LEFT, top, CONTENT_RIGHT, top + panel_height)
        panels.append(panel)
        label_rows.append(Box(panel.left, panel.top, panel.right, panel.top + LABEL_ROW_HEIGHT))
        scenes.append(
            Box(
                panel.left + PANEL_PADDING,
                panel.top + LABEL_ROW_HEIGHT,
                panel.right - PANEL_PADDING,
                panel.bottom - PANEL_PADDING,
            )
        )
    footer = footer_text = silhouette = None
    if has_muscles:
        footer = Box(CONTENT_LEFT, CONTENT_BOTTOM - FOOTER_HEIGHT, CONTENT_RIGHT, CONTENT_BOTTOM)
        text_left = footer.left + FOOTER_TEXT_PADDING_X
        if has_silhouette:
            left = footer.left + FOOTER_SILHOUETTE_INSET_X
            silhouette = Box(
                left,
                footer.top + FOOTER_SILHOUETTE_INSET_Y,
                left + FOOTER_SILHOUETTE_WIDTH,
                footer.bottom - FOOTER_SILHOUETTE_INSET_Y,
            )
            text_left = silhouette.right + FOOTER_SILHOUETTE_GAP
        footer_text = Box(
            text_left,
            footer.top + FOOTER_TEXT_PADDING_Y,
            footer.right - FOOTER_TEXT_PADDING_X,
            footer.bottom - FOOTER_TEXT_PADDING_Y,
        )
    return CardLayout(
        panels=(panels[0], panels[1]),
        label_rows=(label_rows[0], label_rows[1]),
        scenes=(scenes[0], scenes[1]),
        footer=footer,
        footer_text=footer_text,
        silhouette=silhouette,
    )


def _round(value: float) -> int:
    return int(math.floor(value + 0.5))


# ===========================================================================
# Extracao das cenas
# ===========================================================================


@dataclass(frozen=True)
class ExtractedScenes:
    """As duas cenas recortadas (RGBA, alfa = primeiro plano), a linha do
    corte e as caixas na ilustracao original."""

    scenes: tuple[Image.Image, Image.Image]
    boxes: tuple[Box, Box]
    cut_y: int
    transparent: bool


def _load_illustration(data: bytes) -> Image.Image:
    if not data:
        raise CardCompositionError("Ilustracao vazia.", reason="invalid_illustration")
    if len(data) > processor.MAX_BYTES:
        raise CardCompositionError(f"Ilustracao com {len(data)} bytes, acima do limite.", reason="invalid_illustration")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 - fronteira de parser: qualquer falha = arquivo ruim
        raise CardCompositionError(
            f"Ilustracao ilegivel: {type(exc).__name__}.", reason="invalid_illustration"
        ) from exc
    width, height = image.size
    if min(width, height) < MIN_ILLUSTRATION_SIDE or max(width, height) > MAX_ILLUSTRATION_SIDE:
        raise CardCompositionError(f"Ilustracao {width}x{height} fora da faixa aceita.", reason="invalid_illustration")
    return image


def _border_color(rgb: Image.Image) -> tuple[int, int, int]:
    """Cor do fundo opaco: mediana, por canal, de uma faixa das 4 bordas."""
    width, height = rgb.size
    band = min(BACKGROUND_BORDER, width // 4, height // 4)
    strips = [
        rgb.crop((0, 0, width, band)),
        rgb.crop((0, height - band, width, height)),
        rgb.crop((0, band, band, height - band)),
        rgb.crop((width - band, band, width, height - band)),
    ]
    color = []
    for channel in range(3):
        histogram = [0] * 256
        for strip in strips:
            for value, count in enumerate(strip.getchannel(channel).histogram()):
                histogram[value] += count
        half = (sum(histogram) + 1) // 2
        running = 0
        for value, count in enumerate(histogram):
            running += count
            if running >= half:
                color.append(value)
                break
    return color[0], color[1], color[2]


def _ramp_lut(low: int, high: int) -> list[int]:
    return [0 if v <= low else 255 if v >= high else _round(255 * (v - low) / (high - low)) for v in range(256)]


def _foreground(image: Image.Image) -> tuple[Image.Image, Image.Image, bool]:
    """(ilustracao RGBA com alfa = primeiro plano suave, mascara binaria
    limpa para medir, fundo_transparente?)."""
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    histogram = alpha.histogram()
    transparent_pixels = sum(histogram[: ALPHA_THRESHOLD + 1])
    transparent = transparent_pixels >= TRANSPARENT_MIN_FRACTION * rgba.width * rgba.height
    if transparent:
        soft = alpha
        binary = alpha.point([0 if v <= ALPHA_THRESHOLD else 255 for v in range(256)])
    else:
        rgb = rgba.convert("RGB")
        background = Image.new("RGB", rgb.size, _border_color(rgb))
        red, green, blue = ImageChops.difference(rgb, background).split()
        distance = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        soft = distance.point(_ramp_lut(COLOR_DISTANCE_LOW, COLOR_DISTANCE_HIGH))
        if image.mode in ("RGBA", "LA", "PA"):
            soft = ImageChops.multiply(soft, alpha)
        threshold = (COLOR_DISTANCE_LOW + COLOR_DISTANCE_HIGH) // 2
        binary = ImageChops.multiply(
            distance.point([0 if v < threshold else 255 for v in range(256)]),
            alpha.point([0 if v <= ALPHA_THRESHOLD else 255 for v in range(256)]),
        )
    cleaned = binary.filter(ImageFilter.MinFilter(NOISE_FILTER_SIZE)).filter(ImageFilter.MaxFilter(NOISE_FILTER_SIZE))
    out = rgba.copy()
    out.putalpha(soft)
    return out, cleaned, transparent


def _row_count(mask: Image.Image, y: int) -> int:
    return mask.crop((0, y, mask.width, y + 1)).histogram()[255]


def find_cut_row(mask: Image.Image) -> int:
    """Linha mais vazia (menos pixels de primeiro plano) entre 40 % e 60 % da
    altura. Empate: a sequencia mais longa de linhas com o minimo; empate de
    novo, a mais perto do meio. Corta no centro da sequencia."""
    height = mask.height
    low = int(math.floor(height * CUT_RANGE[0]))
    high = max(low + 1, int(math.ceil(height * CUT_RANGE[1])))
    counts = [_row_count(mask, y) for y in range(low, high)]
    minimum = min(counts)
    runs: list[tuple[int, int]] = []
    start: Optional[int] = None
    for offset, count in enumerate(counts + [minimum + 1]):
        if count == minimum and start is None:
            start = offset
        elif count != minimum and start is not None:
            runs.append((low + start, low + offset))
            start = None
    middle = height / 2
    best = min(runs, key=lambda run: (-(run[1] - run[0]), abs((run[0] + run[1]) / 2 - middle), run[0]))
    return (best[0] + best[1]) // 2


def extract_scenes(image: Image.Image) -> ExtractedScenes:
    """Recorta a cena de cima (inicio) e a de baixo (movimento)."""
    foreground, mask, transparent = _foreground(image)
    width, height = foreground.size
    cut = find_cut_row(mask)
    halves = (Box(0, 0, width, cut), Box(0, cut, width, height))
    boxes = []
    for index, half in enumerate(halves, start=1):
        found = mask.crop((half.left, half.top, half.right, half.bottom)).getbbox()
        if not found:
            raise CardCompositionError(
                f"Nenhum primeiro plano detectavel na cena {index} da ilustracao.", reason="no_foreground"
            )
        left, top, right, bottom = found
        boxes.append(
            Box(
                max(half.left, left - SCENE_PAD),
                max(half.top, half.top + top - SCENE_PAD),
                min(half.right, right + SCENE_PAD),
                min(half.bottom, half.top + bottom + SCENE_PAD),
            )
        )
    crops = tuple(foreground.crop((b.left, b.top, b.right, b.bottom)) for b in boxes)
    return ExtractedScenes(scenes=(crops[0], crops[1]), boxes=(boxes[0], boxes[1]), cut_y=cut, transparent=transparent)


@dataclass(frozen=True)
class ScenePlacement:
    box: Box  # onde a cena redimensionada fica no card
    scale: float


def plan_scenes(sizes: Sequence[tuple[int, int]], areas: Sequence[Box]) -> tuple[ScenePlacement, ...]:
    """A MESMA escala para as duas cenas: a maior que faz as duas caberem nas
    suas areas (limitada a `MAX_SCENE_SCALE`); cada uma centralizada."""
    scale = min(
        [MAX_SCENE_SCALE]
        + [area.width / w for (w, _), area in zip(sizes, areas)]
        + [area.height / h for (_, h), area in zip(sizes, areas)]
    )
    placements = []
    for (w, h), area in zip(sizes, areas):
        target_w = max(1, min(area.width, _round(w * scale)))
        target_h = max(1, min(area.height, _round(h * scale)))
        left = area.left + (area.width - target_w) // 2
        top = area.top + (area.height - target_h) // 2
        placements.append(ScenePlacement(box=Box(left, top, left + target_w, top + target_h), scale=scale))
    return tuple(placements)


# ===========================================================================
# Texto
# ===========================================================================


@dataclass(frozen=True)
class TitleLayout:
    lines: tuple[str, ...]
    size: int
    xs: tuple[int, ...]  # x da origem de cada linha (alinhada a esquerda da caixa)
    baselines: tuple[int, ...]  # y de cada linha de base
    ink: Box  # caixa da tinta do bloco inteiro (acentos e descendentes inclusos)


def _text_width(text: str, face: ImageFont.FreeTypeFont) -> int:
    left, _, right, _ = face.getbbox(text, anchor="ls")
    return right - min(0, left)


def _balanced_split(
    words: Sequence[str], lines: int, face: ImageFont.FreeTypeFont, limit: int
) -> tuple[list[str], int]:
    """Quebra as palavras em `lines` linhas minimizando a linha mais larga
    (titulo equilibrado, sem "SUPINO RETO COM / BARRA"). A busca usa o
    avanco de cada palavra (soma de prefixos, O(1) por linha candidata); a
    largura devolvida e a da TINTA das linhas escolhidas - medida so quando
    o avanco passa perto de `limit` (medir tinta de linha longa e caro)."""
    advances = [face.getlength(word) for word in words]
    space = face.getlength(" ")
    prefix = [0.0]
    for advance in advances:
        prefix.append(prefix[-1] + advance)

    def line_width(start: int, end: int) -> float:
        return prefix[end] - prefix[start] + space * (end - start - 1)

    best: Optional[tuple[float, tuple[int, ...]]] = None
    for cuts in itertools.combinations(range(1, len(words)), lines - 1):
        bounds = (0, *cuts, len(words))
        widest = max(line_width(bounds[i], bounds[i + 1]) for i in range(lines))
        if best is None or widest < best[0]:
            best = (widest, bounds)
    assert best is not None
    bounds = best[1]
    chosen = [" ".join(words[bounds[i]: bounds[i + 1]]) for i in range(lines)]
    if best[0] > limit * 1.05:
        return chosen, int(math.ceil(best[0]))
    return chosen, max(_text_width(line, face) for line in chosen)


def _place_title(lines: Sequence[str], face: ImageFont.FreeTypeFont, size: int, box: Box) -> TitleLayout:
    pitch = _round(size * TITLE_LINE_PITCH)
    cap_height = -face.getbbox("H", anchor="ls")[1]
    # Centra o bloco das MAIUSCULAS na altura da caixa (o centro visual do
    # titulo em caixa alta); acentos e descendentes podem sair do bloco, mas
    # nunca da caixa (ajuste abaixo).
    block = cap_height + pitch * (len(lines) - 1)
    first = box.top + (box.height - block) // 2 + cap_height
    baselines = [first + i * pitch for i in range(len(lines))]
    bboxes = [face.getbbox(line, anchor="ls") for line in lines]
    # Tinta alinhada a borda esquerda da caixa, mesmo com espaco lateral
    # negativo no 1o glifo.
    xs = [box.left + max(0, -bbox[0]) for bbox in bboxes]

    def ink(base: list[int]) -> Box:
        return Box(
            min(x + bbox[0] for x, bbox in zip(xs, bboxes)),
            min(y + bbox[1] for y, bbox in zip(base, bboxes)),
            max(x + bbox[2] for x, bbox in zip(xs, bboxes)),
            max(y + bbox[3] for y, bbox in zip(base, bboxes)),
        )

    current = ink(baselines)
    shift = 0
    if current.top < box.top:
        shift = box.top - current.top
    elif current.bottom > box.bottom:
        shift = box.bottom - current.bottom
    baselines = [y + shift for y in baselines]
    return TitleLayout(lines=tuple(lines), size=size, xs=tuple(xs), baselines=tuple(baselines), ink=ink(baselines))


def _fits(layout: TitleLayout, box: Box) -> bool:
    return box.contains(layout.ink)


def _wrap_chars(text: str, face: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Ultimo recurso (palavra maior que a caixa): quebra por caractere."""
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if _text_width(candidate, face) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for char in word:
            if _text_width(current + char, face) <= width:
                current += char
            else:
                lines.append(current)
                current = char
    if current:
        lines.append(current)
    return lines


def _truncate(text: str, face: ImageFont.FreeTypeFont, width: int) -> str:
    """O maior prefixo que, com reticencias, cabe em `width` (busca binaria:
    a largura cresce com o prefixo)."""
    if _text_width(text, face) <= width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _text_width(text[:middle].rstrip() + ELLIPSIS, face) <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + ELLIPSIS


def layout_title(title: str, font_path: Path = FONT_OSWALD_BOLD, box: Box = TITLE_BOX) -> TitleLayout:
    """Titulo em CAIXA ALTA no maior tamanho (de `TITLE_MAX_SIZE` para baixo)
    em que cabe na caixa em ate 3 linhas equilibradas. Nada cabe nem no
    minimo (nome absurdo): quebra por caractere e corta com reticencias."""
    text = " ".join(str(title or "").upper().split())
    if not text:
        raise CardCompositionError("Titulo vazio.", reason="empty_title")
    words = text.split(" ")
    for size in range(TITLE_MAX_SIZE, TITLE_MIN_SIZE - 1, -TITLE_SIZE_STEP):
        face = font(font_path, size)
        for count in range(1, min(TITLE_MAX_LINES, len(words)) + 1):
            lines, widest = _balanced_split(words, count, face, box.width)
            if widest > box.width:
                continue
            layout = _place_title(lines, face, size, box)
            if _fits(layout, box):
                return layout
    face = font(font_path, TITLE_MIN_SIZE)
    lines = _wrap_chars(text, face, box.width)
    if len(lines) > TITLE_MAX_LINES:
        lines = lines[: TITLE_MAX_LINES - 1] + [_truncate(" ".join(lines[TITLE_MAX_LINES - 1:]), face, box.width)]
    lines = [_truncate(line, face, box.width) for line in lines]
    return _place_title(lines, face, TITLE_MIN_SIZE, box)


@dataclass(frozen=True)
class FooterLine:
    label: str  # "Principais:" na 1a linha do papel; "" nas continuacoes
    value: str  # "Peitoral, Tríceps"
    baseline: int
    size: int
    value_x: int  # x da origem do valor (as continuacoes ficam penduradas nele)


@dataclass(frozen=True)
class FooterLayout:
    heading_baseline: int
    heading_size: int
    lines: tuple[FooterLine, ...]
    ink: Box  # tinta do bloco inteiro (titulo + linhas), dentro da caixa do texto


def _footer_roles(content: CardContent) -> list[tuple[str, tuple[str, ...]]]:
    roles = []
    if content.primary_muscles:
        roles.append((f"{FOOTER_PRIMARY_LABEL}:", tuple(content.primary_muscles)))
    if content.secondary_muscles:
        roles.append((f"{FOOTER_SECONDARY_LABEL}:", tuple(content.secondary_muscles)))
    return roles


def footer_rows(content: CardContent) -> list[tuple[str, str]]:
    """(rotulo, valores) de cada papel do rodape: principais primeiro; papel
    sem musculo nao vira linha."""
    return [(label, ", ".join(items)) for label, items in _footer_roles(content)]


def _wrap_items(items: Sequence[str], face: ImageFont.FreeTypeFont, width: int) -> tuple[list[str], bool]:
    """Itens separados por ", " em linhas de ate `width` px (a virgula fica no
    fim da linha que quebra). Item maior que a linha e cortado com
    reticencias - devolve `cabe=False` nesse caso."""
    lines: list[str] = []
    current = ""
    fits = True
    for index, item in enumerate(items):
        piece = item if index == len(items) - 1 else f"{item},"
        candidate = f"{current} {piece}" if current else piece
        if _text_width(candidate, face) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        if _text_width(piece, face) > width:
            piece = _truncate(piece, face, width)
            fits = False
        current = piece
    if current:
        lines.append(current)
    return lines, fits


def _footer_ink(
    heading: tuple[str, int, ImageFont.FreeTypeFont],
    lines: Sequence[FooterLine],
    left: int,
    assets: CardAssets,
) -> Box:
    text, baseline, face = heading
    boxes = []
    l, t, r, b = face.getbbox(text, anchor="ls")
    boxes.append((left + l, baseline + t, left + r, baseline + b))
    for line in lines:
        if line.label:
            l, t, r, b = font(assets.inter_semibold, line.size).getbbox(line.label, anchor="ls")
            boxes.append((left + l, line.baseline + t, left + r, line.baseline + b))
        l, t, r, b = font(assets.inter_regular, line.size).getbbox(line.value, anchor="ls")
        boxes.append((line.value_x + l, line.baseline + t, line.value_x + r, line.baseline + b))
    return Box(min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes))


def _footer_lines(roles, assets: CardAssets, size: int, text_box: Box) -> tuple[list[FooterLine], bool]:
    """Linhas (baselines relativas: titulo em 0) num tamanho; `cabe` se nada
    foi cortado."""
    label_face = font(assets.inter_semibold, size)
    value_face = font(assets.inter_regular, size)
    space = _round(label_face.getlength(" "))
    first = _round(size * FOOTER_FIRST_LINE_GAP)
    pitch = _round(size * FOOTER_LINE_PITCH)
    lines: list[FooterLine] = []
    fits = True
    for label, items in roles:
        value_x = text_box.left + _text_width(label, label_face) + space
        wrapped, ok = _wrap_items(items, value_face, text_box.right - value_x)
        fits = fits and ok
        for index, value in enumerate(wrapped):
            baseline = first + len(lines) * pitch
            lines.append(FooterLine(label if index == 0 else "", value, baseline, size, value_x))
    return lines, fits


def layout_footer(content: CardContent, assets: CardAssets, text_box: Box) -> FooterLayout:
    """Rodape no maior tamanho (`FOOTER_LINE_MAX_SIZE` -> `FOOTER_LINE_MIN_SIZE`)
    em que tudo cabe sem cortar em ate `FOOTER_MAX_LINES` linhas e na altura
    da caixa; nada cabe nem no minimo: o minimo, cortando o excesso com
    reticencias. Bloco centrado na altura da caixa do texto."""
    roles = _footer_roles(content)
    heading_face = font(assets.oswald_semibold, FOOTER_HEADING_SIZE)
    chosen: Optional[tuple[list[FooterLine], Box]] = None
    for size in range(FOOTER_LINE_MAX_SIZE, FOOTER_LINE_MIN_SIZE - 1, -1):
        lines, fits = _footer_lines(roles, assets, size, text_box)
        ink = _footer_ink((FOOTER_HEADING, 0, heading_face), lines, text_box.left, assets)
        if fits and len(lines) <= FOOTER_MAX_LINES and ink.height <= text_box.height:
            chosen = (lines, ink)
            break
    if chosen is None:
        lines, _ = _footer_lines(roles, assets, FOOTER_LINE_MIN_SIZE, text_box)
        if len(lines) > FOOTER_MAX_LINES:
            last = lines[FOOTER_MAX_LINES - 1]
            face = font(assets.inter_regular, last.size)
            value = _truncate(last.value.rstrip(",") + ELLIPSIS, face, text_box.right - last.value_x)
            lines = lines[: FOOTER_MAX_LINES - 1] + [FooterLine(last.label, value, last.baseline, last.size, last.value_x)]
        chosen = (lines, _footer_ink((FOOTER_HEADING, 0, heading_face), lines, text_box.left, assets))
    lines, ink = chosen
    shift = text_box.top + (text_box.height - ink.height) // 2 - ink.top
    placed = tuple(FooterLine(x.label, x.value, x.baseline + shift, x.size, x.value_x) for x in lines)
    return FooterLayout(
        heading_baseline=shift,
        heading_size=FOOTER_HEADING_SIZE,
        lines=placed,
        ink=Box(ink.left, ink.top + shift, ink.right, ink.bottom + shift),
    )


# ===========================================================================
# Desenho
# ===========================================================================


def _aa_mask(size: tuple[int, int], draw_shape) -> Image.Image:
    """Mascara antisserrilhada: desenha `SUPERSAMPLE` vezes maior e reduz
    com `BOX` (media exata, deterministica)."""
    width, height = size
    big = Image.new("L", (width * SUPERSAMPLE, height * SUPERSAMPLE), 0)
    draw_shape(ImageDraw.Draw(big), SUPERSAMPLE)
    return big.resize((width, height), Image.Resampling.BOX)


def _draw_panel(card: Image.Image, panel: Box) -> None:
    def outer(draw: ImageDraw.ImageDraw, k: int) -> None:
        draw.rounded_rectangle((0, 0, panel.width * k - 1, panel.height * k - 1), radius=PANEL_RADIUS * k, fill=255)

    def inner(draw: ImageDraw.ImageDraw, k: int) -> None:
        b = PANEL_BORDER * k
        draw.rounded_rectangle(
            (b, b, panel.width * k - 1 - b, panel.height * k - 1 - b), radius=(PANEL_RADIUS - PANEL_BORDER) * k, fill=255
        )

    size = (panel.width, panel.height)
    card.paste(COLOR_BORDER, (panel.left, panel.top, panel.right, panel.bottom), _aa_mask(size, outer))
    card.paste(COLOR_PANEL, (panel.left, panel.top, panel.right, panel.bottom), _aa_mask(size, inner))


def _badge_box(label_row: Box) -> Box:
    left = label_row.left + BADGE_INSET_X
    top = label_row.top + BADGE_INSET_Y
    return Box(left, top, left + BADGE_DIAMETER, top + BADGE_DIAMETER)


def label_ink_boxes(layout: CardLayout, assets: CardAssets) -> tuple[Box, Box]:
    """Caixa da tinta (selo + texto) de cada rotulo de painel, para provar
    que fica fora da area da cena."""
    boxes = []
    face = font(assets.oswald_medium, LABEL_FONT_SIZE)
    for row, text in zip(layout.label_rows, PANEL_LABELS):
        badge = _badge_box(row)
        x, baseline = _label_origin(badge, face)
        left, top, right, bottom = face.getbbox(text, anchor="ls")
        boxes.append(
            Box(badge.left, min(badge.top, baseline + top), x + right, max(badge.bottom, baseline + bottom))
        )
    return boxes[0], boxes[1]


def _label_origin(badge: Box, face: ImageFont.FreeTypeFont) -> tuple[int, int]:
    cap_height = -face.getbbox("H", anchor="ls")[1]
    center = badge.top + badge.height / 2
    return badge.right + LABEL_GAP, _round(center + cap_height / 2)


def _draw_label(card: Image.Image, draw: ImageDraw.ImageDraw, row: Box, number: int, text: str, assets: CardAssets) -> None:
    badge = _badge_box(row)

    def circle(d: ImageDraw.ImageDraw, k: int) -> None:
        d.ellipse((0, 0, badge.width * k - 1, badge.height * k - 1), fill=255)

    card.paste(COLOR_ACCENT, (badge.left, badge.top, badge.right, badge.bottom), _aa_mask((badge.width, badge.height), circle))
    digit_face = font(assets.oswald_bold, BADGE_FONT_SIZE)
    digit = str(number)
    left, top, right, bottom = digit_face.getbbox(digit, anchor="ls")
    x = _round(badge.left + badge.width / 2 - (left + right) / 2)
    y = _round(badge.top + badge.height / 2 - (top + bottom) / 2)
    draw.text((x, y), digit, font=digit_face, fill=COLOR_BADGE_TEXT, anchor="ls")
    label_face = font(assets.oswald_medium, LABEL_FONT_SIZE)
    x, baseline = _label_origin(badge, label_face)
    draw.text((x, baseline), text, font=label_face, fill=COLOR_TEXT, anchor="ls")


def _draw_title(draw: ImageDraw.ImageDraw, title: TitleLayout, assets: CardAssets) -> None:
    face = font(assets.oswald_bold, title.size)
    for line, x, baseline in zip(title.lines, title.xs, title.baselines):
        draw.text((x, baseline), line, font=face, fill=COLOR_TEXT, anchor="ls")


def _draw_footer(draw: ImageDraw.ImageDraw, footer: FooterLayout, text_box: Box, assets: CardAssets) -> None:
    heading = font(assets.oswald_semibold, footer.heading_size)
    draw.text((text_box.left, footer.heading_baseline), FOOTER_HEADING, font=heading, fill=COLOR_ACCENT, anchor="ls")
    for line in footer.lines:
        if line.label:
            label_face = font(assets.inter_semibold, line.size)
            draw.text((text_box.left, line.baseline), line.label, font=label_face, fill=COLOR_TEXT_SECONDARY, anchor="ls")
        value_face = font(assets.inter_regular, line.size)
        draw.text((line.value_x, line.baseline), line.value, font=value_face, fill=COLOR_TEXT, anchor="ls")


def silhouette_styles(content: CardContent) -> dict[str, bodymap.RegionStyle]:
    """Principais em laranja forte com brilho, secundarios em tom fraco."""
    return bodymap.styles_for_roles(content.primary_slugs, content.secondary_slugs)


def _draw_silhouette(card: Image.Image, box: Box, styles, body_map_assets: bodymap.BodyMapAssets) -> None:
    """Frente + costas (cada vista recortada pela caixa do manifest),
    encaixadas na caixa com a proporcao mantida e centradas."""
    figure = bodymap.fit_body_map(styles, box.width, box.height, assets=body_map_assets)
    left = box.left + (box.width - figure.width) // 2
    top = box.top + (box.height - figure.height) // 2
    card.paste(figure.convert("RGB"), (left, top), figure.getchannel("A"))


def _paste_scene(card: Image.Image, scene: Image.Image, placement: ScenePlacement) -> None:
    target = (placement.box.width, placement.box.height)
    # RGBA: o Pillow redimensiona com alfa pre-multiplicado (sem franja).
    resized = scene if scene.size == target else scene.resize(target, Image.Resampling.LANCZOS)
    card.paste(resized.convert("RGB"), (placement.box.left, placement.box.top), resized.getchannel("A"))


def _encode_png(image: Image.Image) -> bytes:
    clean = Image.frombytes(image.mode, image.size, image.tobytes())  # sem info/metadados
    buffer = io.BytesIO()
    clean.save(buffer, format="PNG")
    return buffer.getvalue()


def compose_card(illustration_png: bytes, content: CardContent, assets: CardAssets) -> bytes:
    """Card final (PNG RGB 1024x1536, sem metadados). Erros:
    `CardCompositionError` (nunca outra excecao do Pillow)."""
    if not str(content.title or "").strip():
        raise CardCompositionError("Titulo vazio.", reason="empty_title")
    illustration = _load_illustration(illustration_png)
    try:
        extracted = extract_scenes(illustration)
        has_muscles = bool(content.primary_muscles or content.secondary_muscles)
        styles: dict[str, bodymap.RegionStyle] = {}
        body_map_assets: Optional[bodymap.BodyMapAssets] = None
        if has_muscles and (content.primary_slugs or content.secondary_slugs):
            body_map_assets = assets.body_map or _load_body_map()
            styles = bodymap.resolve_styles(silhouette_styles(content), body_map_assets)
        layout = card_layout(has_muscles, has_silhouette=bool(styles))
        title = layout_title(content.title, assets.oswald_bold, TITLE_BOX)

        card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), COLOR_BACKGROUND)
        draw = ImageDraw.Draw(card)
        draw.rectangle((CONTENT_LEFT, DIVIDER_Y, CONTENT_RIGHT - 1, DIVIDER_Y + DIVIDER_THICKNESS - 1), fill=COLOR_BORDER)
        for panel in layout.panels:
            _draw_panel(card, panel)
        placements = plan_scenes([scene.size for scene in extracted.scenes], layout.scenes)
        for scene, placement in zip(extracted.scenes, placements):
            _paste_scene(card, scene, placement)
        for number, (row, text) in enumerate(zip(layout.label_rows, PANEL_LABELS), start=1):
            _draw_label(card, draw, row, number, text, assets)
        _draw_title(draw, title, assets)
        if layout.footer is not None and layout.footer_text is not None:
            _draw_panel(card, layout.footer)
            if layout.silhouette is not None and body_map_assets is not None:
                _draw_silhouette(card, layout.silhouette, styles, body_map_assets)
            _draw_footer(draw, layout_footer(content, assets, layout.footer_text), layout.footer_text, assets)
        # Logo por ULTIMO, por cima de tudo, pelo mesmo codigo da posicao
        # oficial (`processor.compose_logo`).
        card, _ = processor.compose_logo(card, assets.logo)
        return _encode_png(card)
    except CardCompositionError:
        raise
    except (OSError, ValueError, processor.ImageProcessingError) as exc:
        raise CardCompositionError(f"Falha ao compor o card: {type(exc).__name__}.", reason="processing") from exc
