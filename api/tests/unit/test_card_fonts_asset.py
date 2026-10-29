"""Fontes versionadas dos cards ilustrados (`app/assets/fonts/`).

O compositor (`app/exercise_card_composer.py`) escreve titulo, rotulos e
musculos em portugues; uma fonte sem o glifo de um acento cai no `.notdef`
(um retangulo) sem erro nenhum. Este teste prova, para cada arquivo:

1. carrega com `ImageFont.truetype` e tem a familia/peso esperados (TTF
   estatico - o peso nao depende de eixo variavel);
2. renderiza `ÁÂÃÀÇÉÊÍÓÔÕÚáâãàçéêíóôõú` sem cair no `.notdef` (pixels de cada
   caractere comparados com os de um code point que a fonte nao tem) e cada
   letra acentuada difere da letra sem acento;
3. o SHA-256 confere com o README (origem auditavel) e o `OFL.txt` de cada
   familia esta ao lado.

Sem banco, sem rede.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from app import exercise_card_composer as composer

FONTS = Path(__file__).resolve().parents[2] / "app" / "assets" / "fonts"
README = FONTS / "README.md"
ACCENTS = "ÁÂÃÀÇÉÊÍÓÔÕÚáâãàçéêíóôõú"
BASE = "AAAACEEIOOOUaaaaceeiooou"
MISSING_CODE_POINT = ""  # uso privado: nenhuma das duas familias tem

EXPECTED = {
    "oswald/Oswald-Medium.ttf": ("Oswald", "Medium"),
    "oswald/Oswald-SemiBold.ttf": ("Oswald", "SemiBold"),
    "oswald/Oswald-Bold.ttf": ("Oswald", "Bold"),
    "inter/Inter-Regular.ttf": ("Inter", "Regular"),
    "inter/Inter-SemiBold.ttf": ("Inter", "SemiBold"),
}


def _glyph(face: ImageFont.FreeTypeFont, char: str) -> tuple[tuple[int, int], bytes]:
    """Pixels do caractere renderizado sozinho."""
    left, top, right, bottom = face.getbbox(char)
    image = Image.new("L", (max(1, right - left) + 4, max(1, bottom - top) + 4), 0)
    ImageDraw.Draw(image).text((2 - left, 2 - top), char, font=face, fill=255)
    return image.size, image.tobytes()


def test_cinco_arquivos_esperados_e_nada_mais():
    achados = {str(p.relative_to(FONTS)).replace("\\", "/") for p in FONTS.glob("*/*.ttf")}
    assert achados == set(EXPECTED)


def test_o_compositor_aponta_para_os_arquivos_versionados():
    paths = {
        composer.FONT_OSWALD_MEDIUM, composer.FONT_OSWALD_SEMIBOLD, composer.FONT_OSWALD_BOLD,
        composer.FONT_INTER_REGULAR, composer.FONT_INTER_SEMIBOLD,
    }
    assert {FONTS / rel for rel in EXPECTED} == paths


@pytest.mark.parametrize("relativo, nome", EXPECTED.items())
def test_carrega_com_familia_e_peso_esperados(relativo, nome):
    face = ImageFont.truetype(str(FONTS / relativo), 48, layout_engine=ImageFont.Layout.BASIC)
    assert face.getname() == nome
    # Estatica: sem tabela `fvar` (o peso e o do arquivo, nao de um eixo).
    assert b"fvar" not in (FONTS / relativo).read_bytes()[:2048]


@pytest.mark.parametrize("relativo", EXPECTED)
def test_renderiza_os_acentos_do_portugues_sem_notdef(relativo):
    face = ImageFont.truetype(str(FONTS / relativo), 48, layout_engine=ImageFont.Layout.BASIC)
    notdef = _glyph(face, MISSING_CODE_POINT)
    assert any(notdef[1]), "o .notdef desta fonte deveria ter tinta (o teste depende disso)"
    for acentuado, base in zip(ACCENTS, BASE):
        glifo = _glyph(face, acentuado)
        assert any(glifo[1]), f"'{acentuado}' sem tinta"
        assert glifo != notdef, f"'{acentuado}' caiu no glifo .notdef de {relativo}"
        assert glifo != _glyph(face, base), f"'{acentuado}' saiu igual a '{base}' (acento perdido)"


def _readme_hashes() -> dict[str, str]:
    texto = README.read_text(encoding="utf-8")
    return dict(re.findall(r"^\| `([^`]+)` \|.*\| `([0-9a-f]{64})` \|$", texto, re.M))


def test_sha256_confere_com_o_readme():
    hashes = _readme_hashes()
    for relativo in [*EXPECTED, "oswald/OFL.txt", "inter/OFL.txt"]:
        assert relativo in hashes, f"{relativo} sem linha no README"
        assert hashlib.sha256((FONTS / relativo).read_bytes()).hexdigest() == hashes[relativo], relativo


@pytest.mark.parametrize("familia", ["oswald", "inter"])
def test_licenca_ofl_de_cada_familia(familia):
    licenca = (FONTS / familia / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License" in licenca and "Version 1.1" in licenca
