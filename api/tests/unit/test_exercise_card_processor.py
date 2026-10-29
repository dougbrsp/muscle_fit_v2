"""Processador grafico do card ilustrado (`app/exercise_card_processor.py`):
posicao/colagem do logo oficial (usada pelo compositor) e `encode_card`, a
codificacao da publicacao - que NAO recompoe nada (Estrategia B: o preview ja
e o card final com logo).

Tudo gerado no proprio teste: o "card composto" e um PNG 1024x1536 sintetico
(com metadados de proposito) e o logo e um RGBA sintetico com a MESMA
proporcao do asset oficial (1200x992, viewBox 902x746 do `logo.svg`). So o
teste de ponta a ponta com o compositor usa os assets reais.

Rodar: cd api && python -m pytest tests/unit/test_exercise_card_processor.py
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops, ImageDraw
from PIL.PngImagePlugin import PngInfo

from app import exercise_card_composer as composer
from app import exercise_card_processor as proc

LOGO_SIZE = (1200, 992)


def _synthetic_card(width: int = 1024, height: int = 1536, fmt: str = "PNG", with_metadata: bool = True) -> bytes:
    """Fundo escuro com degrade e formas - pixels distintos o bastante para
    provar que NADA fora do retangulo do logo muda."""
    image = Image.new("RGB", (width, height), (10, 10, 10))
    draw = ImageDraw.Draw(image)
    for y in range(0, height, 8):
        draw.line([(0, y), (width, y)], fill=(10 + (y * 30) // height, 12, 14 + (y * 20) // height))
    draw.rectangle([width // 4, height // 3, width * 3 // 4, height * 2 // 3], fill=(255, 149, 0))
    draw.ellipse([20, 20, width // 3, height // 6], fill=(40, 40, 40))  # passa pela area do logo
    buffer = io.BytesIO()
    options = {}
    if with_metadata and fmt == "PNG":
        info = PngInfo()
        info.add_text("Comment", "gerado pela IA - nao deveria sobreviver")
        exif = Image.Exif()
        exif[0x010E] = "descricao EXIF que nao deveria sobreviver"  # ImageDescription
        options = {"pnginfo": info, "exif": exif.tobytes(), "dpi": (300, 300)}
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def _synthetic_logo(size: tuple[int, int] = LOGO_SIZE) -> Image.Image:
    """RGBA transparente com blocos OPACOS grandes (alfa 255) e bordas
    suavizadas pelo redimensionamento."""
    width, height = size
    logo = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(logo)
    draw.rectangle([width // 10, height // 10, width // 2, height // 2], fill=(255, 149, 0, 255))
    draw.rectangle([width // 2, height // 2, width * 9 // 10, height * 9 // 10], fill=(245, 245, 245, 255))
    draw.ellipse([width // 2, height // 10, width * 9 // 10, height // 2], fill=(232, 69, 28, 255))
    draw.rectangle([width // 10, height * 6 // 10, width * 4 // 10, height * 9 // 10], fill=(255, 255, 255, 128))
    return logo


@pytest.fixture(scope="module")
def card_png() -> bytes:
    return _synthetic_card()


@pytest.fixture(scope="module")
def processed(card_png):
    return proc.encode_card(card_png)


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _pixels(image: Image.Image) -> list[tuple[int, ...]]:
    """Pixels como tuplas, pelos bytes crus (estavel entre Pillow 11 do CI e
    versoes novas, onde `getdata` foi depreciado)."""
    raw = image.tobytes()
    bands = len(image.getbands())
    return [tuple(raw[i : i + bands]) for i in range(0, len(raw), bands)]


# ---------------------------------------------------------------------------
# Posicao do logo
# ---------------------------------------------------------------------------


def test_coordenadas_deterministicas_para_1024x1536_e_logo_1200x992():
    _, placement = proc.compose_logo(_open(_synthetic_card()), _synthetic_logo())
    assert placement == proc.LogoPlacement(x=51, y=77, width=225, height=186)


def test_logo_placement_e_pura_e_repetivel():
    assert proc.logo_placement(1024, 1536, *LOGO_SIZE) == proc.LogoPlacement(51, 77, 225, 186)
    assert proc.logo_placement(1024, 1536, *LOGO_SIZE) == proc.logo_placement(1024, 1536, *LOGO_SIZE)


def test_logo_cabe_no_retangulo_que_o_compositor_reserva():
    """O compositor nao desenha nada no retangulo do logo; o logo composto
    ocupa exatamente esse retangulo."""
    p = proc.logo_placement(composer.CARD_WIDTH, composer.CARD_HEIGHT, *LOGO_SIZE)
    box = composer.LOGO_BOX
    assert (p.x, p.y, p.x + p.width, p.y + p.height) == (box.left, box.top, box.right, box.bottom)


def test_logo_so_reduz_nunca_amplia():
    pequeno = _synthetic_logo((100, 83))
    p = proc.logo_placement(1024, 1536, 100, 83)
    assert (p.width, p.height) == (100, 83)
    card, placement = proc.compose_logo(_open(_synthetic_card()), pequeno)
    assert placement == p
    crop = card.crop((p.x, p.y, p.x + p.width, p.y + p.height))
    for got, src in zip(_pixels(crop), _pixels(pequeno)):
        if src[3] == 255:
            assert got == src[:3]


@pytest.fixture(scope="module")
def with_logo(card_png):
    return proc.compose_logo(_open(card_png), _synthetic_logo())


def test_pixels_do_logo_identicos_ao_logo_redimensionado(card_png, with_logo):
    """Onde o logo e opaco (alfa 255) o pixel do card e EXATAMENTE o do logo
    redimensionado (LANCZOS); em alfa 251-254 so a mistura minima com o fundo;
    onde o logo e transparente, o pixel original do card."""
    final, p = with_logo
    esperado = _synthetic_logo().resize((p.width, p.height), Image.Resampling.LANCZOS)
    original = _open(card_png).convert("RGB")
    box = (p.x, p.y, p.x + p.width, p.y + p.height)
    opacos = 0
    for got, src, under in zip(_pixels(final.crop(box)), _pixels(esperado), _pixels(original.crop(box))):
        alpha = src[3]
        if alpha == 255:
            opacos += 1
            assert got == src[:3]
        elif alpha > 250:
            assert all(abs(g - s) <= (255 - alpha) + 1 for g, s in zip(got, src[:3]))
        elif alpha == 0:
            assert got == under
    assert opacos > 10_000, "o logo sintetico precisa de area opaca para o teste valer"


def test_nada_fora_do_retangulo_do_logo_muda(card_png, with_logo):
    final, p = with_logo
    assert final.mode == "RGB"
    diff = ImageChops.difference(final, _open(card_png).convert("RGB"))
    bbox = diff.getbbox()
    assert bbox is not None
    assert bbox[0] >= p.x and bbox[1] >= p.y
    assert bbox[2] <= p.x + p.width and bbox[3] <= p.y + p.height


# ---------------------------------------------------------------------------
# encode_card (publicacao do card ja composto)
# ---------------------------------------------------------------------------


def test_encode_nao_recompoe_nada_o_png_publicado_e_o_preview(card_png, processed):
    """O que o admin revisou e o que e publicado: os pixels do card.png sao
    EXATAMENTE os do preview (sem logo de novo, sem nada)."""
    assert ImageChops.difference(_open(processed.card_png), _open(card_png).convert("RGB")).getbbox() is None
    assert not hasattr(proc, "process_card")
    assert not hasattr(processed, "logo")


def test_encode_de_card_composto_de_verdade_preserva_os_pixels():
    from tests.support import card_illustrations as ill

    preview = composer.compose_card(
        ill.illustration_png(), composer.CardContent("Supino reto com barra", ("Peitoral",)), composer.load_card_assets()
    )
    publicado = proc.encode_card(preview)
    assert _open(publicado.card_png).tobytes() == _open(preview).tobytes()
    assert (publicado.width, publicado.height) == (1024, 1536)
    assert len(publicado.card_webp) < len(publicado.card_png)  # WebP q85 e o formato leve servido pela CDN



def test_tres_saidas_nos_formatos_e_tamanhos_certos(processed):
    webp, png, thumb = _open(processed.card_webp), _open(processed.card_png), _open(processed.thumb_webp)
    assert (webp.format, webp.size) == ("WEBP", (1024, 1536))
    assert (png.format, png.size) == ("PNG", (1024, 1536))
    assert (thumb.format, thumb.size) == ("WEBP", (400, 600))
    assert (processed.thumb_width, processed.thumb_height) == (400, 600)
    assert png.mode == "RGB"


def test_metadados_removidos(processed):
    png = _open(processed.card_png)
    assert png.info == {}
    assert len(png.getexif()) == 0
    for data in (processed.card_webp, processed.thumb_webp):
        image = _open(data)
        assert not {"exif", "icc_profile", "xmp", "dpi"} & set(image.info)
        assert len(image.getexif()) == 0


def test_entrada_rgba_e_aceita_e_sai_opaca():
    rgba = Image.new("RGBA", (1024, 1536), (10, 10, 10, 255))
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG")
    result = proc.encode_card(buffer.getvalue())
    assert _open(result.card_png).mode == "RGB"


# ---------------------------------------------------------------------------
# Rejeicoes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "size, reason",
    [
        ((700, 1050), "width_out_of_range"),
        ((2100, 3150), "width_out_of_range"),
        ((1024, 1024), "ratio_out_of_range"),
        ((1536, 1024), "ratio_out_of_range"),
        ((1024, 1700), "ratio_out_of_range"),
    ],
)
def test_rejeita_largura_e_proporcao_fora_da_faixa(size, reason):
    with pytest.raises(proc.ImageProcessingError) as exc:
        proc.encode_card(_synthetic_card(*size, with_metadata=False))
    assert exc.value.reason == reason
    assert exc.value.code == "IMAGE_PROCESSING_FAILED"


@pytest.mark.parametrize("size", [(768, 1152), (2048, 3072), (1024, 1434), (1024, 1638)])
def test_aceita_os_limites_da_faixa(size):
    image = proc.validate_png(_synthetic_card(*size, with_metadata=False))
    assert image.size == size


def test_rejeita_arquivo_acima_do_limite(monkeypatch, card_png):
    monkeypatch.setattr(proc, "MAX_BYTES", len(card_png) - 1)
    with pytest.raises(proc.ImageProcessingError) as exc:
        proc.encode_card(card_png)
    assert exc.value.reason == "too_large"


def test_limite_padrao_e_12_mb():
    assert proc.MAX_BYTES == 12 * 1024 * 1024


@pytest.mark.parametrize(
    "data_fn, reason",
    [
        (lambda png: png[: len(png) // 2], "corrupt"),
        (lambda png: png[:8] + b"\x00" * 64, "corrupt"),
        (lambda png: b"nao e imagem nenhuma" * 10, "corrupt"),
        (lambda png: b"", "empty"),
        (lambda png: _synthetic_card(fmt="JPEG", with_metadata=False), "not_png"),
    ],
    ids=["truncado", "cabecalho_sem_corpo", "lixo", "vazio", "jpeg"],
)
def test_rejeita_arquivo_corrompido_ou_que_nao_e_png(card_png, data_fn, reason):
    with pytest.raises(proc.ImageProcessingError) as exc:
        proc.encode_card(data_fn(card_png))
    assert exc.value.reason == reason


def test_crc_corrompido_e_recusado(card_png):
    """Byte trocado no meio do IDAT: o cabecalho continua valido, so a
    verificacao de integridade pega."""
    data = bytearray(card_png)
    meio = len(data) // 2
    data[meio] ^= 0xFF
    with pytest.raises(proc.ImageProcessingError) as exc:
        proc.validate_png(bytes(data))
    assert exc.value.reason == "corrupt"


def test_logo_sem_dimensoes_e_erro_tipado():
    with pytest.raises(proc.ImageProcessingError) as exc:
        proc.logo_placement(1024, 1536, 0, 0)
    assert exc.value.reason == "invalid_logo"


# ---------------------------------------------------------------------------
# load_brand_logo (unica funcao com I/O, separada)
# ---------------------------------------------------------------------------


def test_load_brand_logo_le_rgba_do_caminho_dado(tmp_path):
    path = tmp_path / "logo.png"
    _synthetic_logo().save(path, format="PNG")
    logo = proc.load_brand_logo(path)
    assert logo.mode == "RGBA" and logo.size == LOGO_SIZE


def test_load_brand_logo_converte_para_rgba(tmp_path):
    path = tmp_path / "logo_rgb.png"
    Image.new("RGB", (120, 99), (255, 149, 0)).save(path, format="PNG")
    assert proc.load_brand_logo(path).mode == "RGBA"


def test_load_brand_logo_ausente_e_erro_tipado(tmp_path):
    with pytest.raises(proc.ImageProcessingError) as exc:
        proc.load_brand_logo(tmp_path / "nao-existe.png")
    assert exc.value.reason == "logo_missing"


def test_caminho_padrao_do_logo_e_o_asset_versionado():
    assert proc.DEFAULT_LOGO_PATH.parts[-4:] == ("app", "assets", "branding", "musclefit-logo.png")
