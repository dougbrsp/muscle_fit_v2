"""Processamento grafico dos cards ilustrados - Pillow puro, SEM I/O de rede.

Dois papeis, ambos sem desenho de texto:

- **Logo oficial** (`logo_placement`, `compose_logo`, `load_brand_logo`):
  coordenadas deterministicas do logo e a colagem do asset oficial, pixel a
  pixel, sempre no mesmo lugar. Quem chama e o COMPOSITOR
  (`exercise_card_composer.compose_card`, no worker, logo depois da OpenAI) -
  o logo entra uma vez so, no card que o admin revisa.
- **Publicacao** (`encode_card`, na aprovacao, api-core):

      bytes do card COMPOSTO (o preview que o admin aprovou)
          -> validar (integro, PNG, largura, razao, tamanho)
          -> remover metadados
          -> card.webp (q85) + card.png (master) + thumb.webp (400 px)

  Sem recompor nada: o que o admin revisou e exatamente o que e publicado
  (Estrategia B, 2026-10-03).

Regra absoluta (secoes 7, 70 e 78 da especificacao): o logo NUNCA e
desenhado pela IA. Ninguem posiciona o logo a mao.

Sem I/O de proposito: quem le o rascunho do S3 e grava os 3 arquivos e o
servico (`exercise_card_storage`). A unica leitura de disco e
`load_brand_logo()`, separada e opcional.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, UnidentifiedImageError

# Validacao do PNG de entrada (secao 47). A geracao padrao e 1024x1536
# (razao 1,5); a faixa aceita folga para os outros tamanhos retrato.
MIN_WIDTH = 768
MAX_WIDTH = 2048
MIN_RATIO = 1.4  # altura / largura
MAX_RATIO = 1.6
MAX_BYTES = 12 * 1024 * 1024

# Posicao do logo (secao 71): margem de 5 % da largura em x e 5 % da altura
# em y; largura do logo = 22 % da largura do card, altura pela proporcao do
# asset. Para 1024x1536 e o logo 1200x992: x=51, y=77, logo 225x186 - o
# compositor reserva exatamente esse retangulo (`exercise_card_composer.LOGO_BOX`).
LOGO_MARGIN_X_RATIO = 0.05
LOGO_MARGIN_Y_RATIO = 0.05
LOGO_WIDTH_RATIO = 0.22

WEBP_QUALITY = 85
THUMB_WIDTH = 400

DEFAULT_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "branding" / "musclefit-logo.png"

IMAGE_PROCESSING_FAILED = "IMAGE_PROCESSING_FAILED"


class ImageProcessingError(ValueError):
    """Imagem recusada ou falha ao processar. `code` vai para o `error_code`
    da geracao; `reason` diz qual regra falhou (para a mensagem ao admin)."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.code = IMAGE_PROCESSING_FAILED
        self.reason = reason


@dataclass(frozen=True)
class LogoPlacement:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ProcessedCard:
    card_webp: bytes
    card_png: bytes
    thumb_webp: bytes
    width: int
    height: int
    thumb_width: int
    thumb_height: int


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


# ---------------------------------------------------------------------------
# Validacao
# ---------------------------------------------------------------------------


def validate_png(data: bytes) -> Image.Image:
    """Abre e valida o PNG; devolve a imagem ja carregada. Ordem barata ->
    cara: tamanho em bytes, cabecalho (formato e dimensoes, sem decodificar),
    integridade (`verify`), decodificacao completa."""
    if not data:
        raise ImageProcessingError("Imagem vazia.", reason="empty")
    if len(data) > MAX_BYTES:
        raise ImageProcessingError(
            f"Imagem com {len(data)} bytes, acima do limite de {MAX_BYTES}.", reason="too_large"
        )
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt, (width, height) = probe.format, probe.size
            if fmt != "PNG":
                raise ImageProcessingError(f"Formato {fmt} recebido, esperado PNG.", reason="not_png")
            _check_dimensions(width, height)
            probe.verify()
        image = Image.open(io.BytesIO(data))
        image.load()
    except ImageProcessingError:
        raise
    except Exception as exc:  # noqa: BLE001 - fronteira de parser: qualquer falha do decodificador = arquivo ruim
        # Pillow sinaliza PNG quebrado com UnidentifiedImageError, OSError,
        # SyntaxError (CRC), struct.error, DecompressionBombError...
        raise ImageProcessingError(f"PNG corrompido ou ilegivel: {type(exc).__name__}.", reason="corrupt") from exc
    return image


def _check_dimensions(width: int, height: int) -> None:
    if not MIN_WIDTH <= width <= MAX_WIDTH:
        raise ImageProcessingError(
            f"Largura {width}px fora da faixa {MIN_WIDTH}-{MAX_WIDTH}px.", reason="width_out_of_range"
        )
    ratio = height / width
    if not MIN_RATIO <= ratio <= MAX_RATIO:
        raise ImageProcessingError(
            f"Proporcao {width}x{height} (altura/largura {ratio:.3f}) fora da faixa {MIN_RATIO}-{MAX_RATIO}.",
            reason="ratio_out_of_range",
        )


# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------


def logo_placement(card_width: int, card_height: int, logo_width: int, logo_height: int) -> LogoPlacement:
    """Coordenadas deterministicas do logo. So REDUZ: um asset menor que a
    largura-alvo e usado no tamanho nativo (ampliar borraria o logo)."""
    if logo_width <= 0 or logo_height <= 0:
        raise ImageProcessingError("Logo sem dimensoes.", reason="invalid_logo")
    target_width = min(_round_half_up(card_width * LOGO_WIDTH_RATIO), logo_width)
    target_height = max(1, _round_half_up(target_width * logo_height / logo_width))
    return LogoPlacement(
        x=_round_half_up(card_width * LOGO_MARGIN_X_RATIO),
        y=_round_half_up(card_height * LOGO_MARGIN_Y_RATIO),
        width=target_width,
        height=target_height,
    )


def compose_logo(card: Image.Image, logo: Image.Image) -> tuple[Image.Image, LogoPlacement]:
    """Cola o logo (RGBA, alfa respeitado) sobre o card e devolve o card RGB
    opaco + a posicao usada."""
    placement = logo_placement(card.width, card.height, logo.width, logo.height)
    rgba_logo = logo.convert("RGBA")
    if (rgba_logo.width, rgba_logo.height) != (placement.width, placement.height):
        rgba_logo = rgba_logo.resize((placement.width, placement.height), Image.Resampling.LANCZOS)
    base = card.convert("RGBA")
    base.alpha_composite(rgba_logo, dest=(placement.x, placement.y))
    # Card final opaco: fundo escuro da marca, sem canal alfa no WebP/PNG.
    return base.convert("RGB"), placement


# ---------------------------------------------------------------------------
# Saida
# ---------------------------------------------------------------------------


def _without_metadata(image: Image.Image) -> Image.Image:
    """Imagem NOVA so com os pixels: sem EXIF, ICC, texto do PNG, XMP, dpi.
    (`Image.save` reaproveita `info` da imagem de origem - ex.: o perfil ICC -
    entao copiar so os pixels e o jeito garantido de nao vazar nada.)"""
    return Image.frombytes(image.mode, image.size, image.tobytes())


def _encode(image: Image.Image, fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def encode_card(png: bytes) -> ProcessedCard:
    """Publicacao do card ja COMPOSTO (preview aprovado): valida, tira
    metadados e codifica WebP q85 + PNG master + thumbnail 400 px. NAO
    compoe logo nem nada: o logo ja esta no preview (`compose_card`), e
    compor de novo duplicaria o logo. Erros: `ImageProcessingError`."""
    source = validate_png(png)
    try:
        clean = _without_metadata(source.convert("RGB"))
        thumb_height = max(1, _round_half_up(THUMB_WIDTH * clean.height / clean.width))
        thumb = clean.resize((THUMB_WIDTH, thumb_height), Image.Resampling.LANCZOS)
        card_webp = _encode(clean, "WEBP", quality=WEBP_QUALITY)
        card_png = _encode(clean, "PNG")
        thumb_webp = _encode(_without_metadata(thumb), "WEBP", quality=WEBP_QUALITY)
    except (OSError, ValueError) as exc:
        raise ImageProcessingError(f"Falha ao processar o card: {type(exc).__name__}.", reason="processing") from exc
    return ProcessedCard(
        card_webp=card_webp,
        card_png=card_png,
        thumb_webp=thumb_webp,
        width=clean.width,
        height=clean.height,
        thumb_width=THUMB_WIDTH,
        thumb_height=thumb_height,
    )


def load_brand_logo(path: Optional[Path] = None) -> Image.Image:
    """Le o logo oficial (`api/app/assets/branding/musclefit-logo.png`, PNG
    RGBA versionado pelo PR de infra) e devolve RGBA carregado. Unica funcao
    com I/O deste modulo."""
    target = Path(path) if path else DEFAULT_LOGO_PATH
    try:
        with Image.open(target) as raw:
            raw.load()
            return raw.convert("RGBA")
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise ImageProcessingError(f"Logo oficial ilegivel em {target.name}.", reason="logo_missing") from exc
