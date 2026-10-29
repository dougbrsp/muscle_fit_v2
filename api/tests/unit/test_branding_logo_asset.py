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
