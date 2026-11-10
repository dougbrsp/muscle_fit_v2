"""O PNG do logo oficial usado na composicao dos cards ilustrados de
exercicios (`app/assets/branding/musclefit-logo.png`).

O logo e colado pela aplicacao sobre a ilustracao gerada pela IA, com alfa,
em coordenadas deterministicas e so REDUZINDO o raster. Este teste garante as
tres propriedades de que a composicao depende:

1. modo RGBA (fundo transparente de verdade, nao um retangulo opaco);
2. largura minima de 1024 px (o processador nunca precisa ampliar);
3. proporcao igual a do viewBox de `frontend/public/logo.svg` (+-0,5 %): se o
   SVG mudar e o PNG nao for regenerado
   (`node frontend/scripts/generate-brand-logo-png.mjs`), este teste reprova.

Sem banco, sem rede, sem AWS.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from PIL import Image

API_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = API_DIR.parent
LOGO_PNG = API_DIR / "app" / "assets" / "branding" / "musclefit-logo.png"
LOGO_SVG = REPO_ROOT / "frontend" / "public" / "logo.svg"

LARGURA_MINIMA = 1024
TOLERANCIA_PROPORCAO = 0.005

_VIEWBOX_RE = re.compile(
    r'viewBox="\s*([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)\s*"'
)


def _proporcao_do_viewbox() -> float:
    if not LOGO_SVG.is_file():
        pytest.skip("frontend/public/logo.svg ausente neste checkout")
    achado = _VIEWBOX_RE.search(LOGO_SVG.read_text(encoding="utf-8"))
    assert achado, "frontend/public/logo.svg sem viewBox reconhecivel"
    largura, altura = float(achado.group(3)), float(achado.group(4))
    assert largura > 0 and altura > 0
    return largura / altura


@pytest.fixture(scope="module")
def logo() -> Image.Image:
    assert LOGO_PNG.is_file(), f"asset ausente: {LOGO_PNG}"
    with Image.open(LOGO_PNG) as imagem:
        assert imagem.format == "PNG"
        imagem.load()
        return imagem.copy()


def test_logo_e_rgba(logo):
    assert logo.mode == "RGBA"


def test_logo_tem_fundo_transparente_e_conteudo(logo):
    """RGBA nao basta: um PNG RGBA todo opaco teria um retangulo de fundo."""
    alfa = logo.getchannel("A")
    largura, altura = logo.size
    for canto in ((0, 0), (largura - 1, 0), (0, altura - 1), (largura - 1, altura - 1)):
        assert alfa.getpixel(canto) == 0, f"canto {canto} nao e transparente"
    assert alfa.getbbox() is not None, "logo todo transparente (render vazio)"


def test_logo_largo_o_suficiente_para_so_reduzir(logo):
    assert logo.width >= LARGURA_MINIMA, logo.size


def test_proporcao_igual_ao_viewbox_do_svg(logo):
    esperada = _proporcao_do_viewbox()
    real = logo.width / logo.height
    assert abs(real - esperada) / esperada <= TOLERANCIA_PROPORCAO, (
        f"proporcao do PNG {real:.4f} difere do viewBox do SVG {esperada:.4f} "
        "- regenere com `node frontend/scripts/generate-brand-logo-png.mjs`"
    )


# ---------------------------------------------------------------------------
# Logo para ESTAMPA (anexado a OpenAI como ultima imagem de entrada)
# ---------------------------------------------------------------------------

STAMP_PNG = API_DIR / "app" / "assets" / "branding" / "musclefit-logo-estampa.png"
# Recorte documentado no README: caixa (0, 0, 1200, 925) do PNG oficial 1200x992.
RECORTE_OFICIAL = (1200, 925)


def test_logo_estampa_e_rgb_achatado_com_lado_ate_1024():
    from app import exercise_card_processor as processor

    assert STAMP_PNG.is_file(), f"asset ausente: {STAMP_PNG}"
    with Image.open(STAMP_PNG) as versionado:
        assert versionado.format == "PNG" and versionado.mode == "RGB"
        assert max(versionado.size) == 1024
    pronto = processor.load_stamp_logo_png()
    with Image.open(io.BytesIO(pronto)) as enviado:
        assert enviado.format == "PNG" and enviado.mode == "RGB"  # sem alfa
        assert max(enviado.size) <= processor.STAMP_LOGO_MAX_SIDE
    assert processor.load_stamp_logo_png() is pronto  # carregado uma vez (cache)


def test_logo_estampa_deriva_do_oficial_sem_o_slogan(logo):
    with Image.open(STAMP_PNG) as estampa:
        largura, altura = estampa.size
        esperada = RECORTE_OFICIAL[0] / RECORTE_OFICIAL[1]
        assert abs(largura / altura - esperada) / esperada <= TOLERANCIA_PROPORCAO
        assert (logo.width, logo.height) == (1200, 992), "oficial mudou: refaca o recorte (README)"
        # Mesma arte do oficial: faixa do nome (linhas 780-900 de 1200x992)
        # tem tinta; sem slogan, a base do asset nao tem mais nada alem do nome.
        fundo = estampa.getpixel((2, 2))
        escala = altura / RECORTE_OFICIAL[1]
        assert estampa.getpixel((largura // 2, int(850 * escala))) != fundo or any(
            estampa.getpixel((x, int(850 * escala))) != fundo for x in range(0, largura, 8)
        ), "nome MUSCLE FIT ausente"
        # Margem inferior: nenhuma linha com tinta abaixo do fim do nome (900) no oficial.
        primeira_vazia = int(905 * escala)
        for y in range(primeira_vazia, altura):
            assert all(estampa.getpixel((x, y)) == fundo for x in range(0, largura, 4)), f"tinta na linha {y} (slogan?)"
