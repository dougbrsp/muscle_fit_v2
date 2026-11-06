"""Compositor do card fotorrealista V3 (`app/exercise_card_composer.py`): a IA
entrega duas fotos (cena 1 = posicao inicial, cena 2 = movimento); a aplicacao
escreve titulo, rotulos, musculos, desenha a silhueta do mapa corporal v1 no
rodape (`CARD_LAYOUT_V3_PHOTO`) e cola o logo.

Fotos sinteticas (`tests/support/card_illustrations.py: scene_photo_png`);
fontes e logo REAIS do repositorio (`load_card_assets`). Os testes provam
propriedades (caixas, pixels por regiao, cover sem borda vazia, bytes iguais),
nunca comparam com uma imagem de referencia - o Pillow do CI e o local podem
rasterizar diferente.

Rodar: cd api && python -m pytest tests/unit/test_exercise_card_composer.py
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops

from app import exercise_card_composer as c
from app import exercise_card_processor as processor
from app import exercise_card_prompt as prompt
from tests.support import card_illustrations as ill

LONGEST = "Rosca inversa com controle de punho (variação Lalá)"  # nome real mais longo (51)
CONTENT = c.CardContent(
    "Supino reto com barra", ("Peitoral",), ("Tríceps", "Ombros"), ("chest",), ("triceps", "front-deltoids")
)
START_COLOR = (150, 60, 60)
END_COLOR = (60, 60, 150)
MIN_VISIBLE_WIDTH = 0.70  # literal; A2 troca pela constante do prompt


@pytest.fixture(scope="module")
def assets() -> c.CardAssets:
    return c.load_card_assets()


@pytest.fixture(scope="module")
def start_png() -> bytes:
    return ill.scene_photo_png(1024, 1536, START_COLOR)


@pytest.fixture(scope="module")
def end_png() -> bytes:
    return ill.scene_photo_png(1024, 1536, END_COLOR)


@pytest.fixture(scope="module")
def card(start_png, end_png, assets) -> Image.Image:
    return _open(c.compose_card_v3(start_png, end_png, CONTENT, assets))


def compose(content: c.CardContent, assets: c.CardAssets, start=None, end=None) -> bytes:
    start = start if start is not None else ill.scene_photo_png(1024, 1536, START_COLOR)
    end = end if end is not None else ill.scene_photo_png(1024, 1536, END_COLOR)
    return c.compose_card_v3(start, end, content, assets)


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _blank_outside(image: Image.Image, keep: list[c.Box], color) -> Image.Image:
    """Copia com tudo DENTRO das caixas `keep` pintado de `color` - o que
    sobra diferente de `color` esta fora delas."""
    out = image.copy()
    for box in keep:
        out.paste(color, (box.left, box.top, box.right, box.bottom))
    return out


def _is_solid(image: Image.Image, color) -> bool:
    return ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, color)).getbbox() is None


# ---------------------------------------------------------------------------
# Layout (geometria)
# ---------------------------------------------------------------------------


def test_versao_do_layout_e_cores_da_marca():
    assert c.COLOR_BACKGROUND == (0x0A, 0x0A, 0x0A)
    assert c.COLOR_PANEL == (0x14, 0x14, 0x14)
    assert c.COLOR_BORDER == (0x2A, 0x2A, 0x2A)
    assert c.COLOR_ACCENT == (0xFF, 0x95, 0x00)
    assert c.COLOR_TEXT == (0xF5, 0xF5, 0xF5)
    assert c.COLOR_TEXT_SECONDARY == (0xE0, 0xE0, 0xE0)
    # Mesma fonte das cores do prompt, que um teste compara com o theme.ts.
    assert c.COLOR_ACCENT == c._rgb(prompt.BRAND_COLORS["accent"])


def test_logo_box_e_a_posicao_oficial_do_logo(assets):
    placement = processor.logo_placement(c.CARD_WIDTH, c.CARD_HEIGHT, *assets.logo.size)
    assert (placement.x, placement.y, placement.width, placement.height) == (51, 77, 225, 186)
    assert c.LOGO_BOX == c.Box(placement.x, placement.y, placement.x + placement.width, placement.y + placement.height)


@pytest.mark.parametrize("has_muscles, has_silhouette", [(True, True), (True, False), (False, False)])
def test_caixas_do_layout_sem_sobreposicao(has_muscles, has_silhouette):
    layout = c.card_layout_v3(has_muscles, has_silhouette)
    card_box = c.Box(0, 0, c.CARD_WIDTH, c.CARD_HEIGHT)
    assert not c.LOGO_BOX.intersects(c.TITLE_BOX)
    assert c.TITLE_BOX.left - c.LOGO_BOX.right == c.TITLE_GAP
    assert (c.TITLE_BOX.top, c.TITLE_BOX.bottom) == (c.LOGO_BOX.top, c.LOGO_BOX.bottom)
    assert c.TITLE_BOX.bottom <= c.DIVIDER_Y < layout.panels[0].top
    # Lado a lado: mesma faixa vertical, esquerda = cena 1, direita = cena 2.
    left, right = layout.panels
    assert (left.top, left.bottom) == (right.top, right.bottom)
    assert left.right + c.PANEL_GAP == right.left
    assert (left.left, right.right) == (c.CONTENT_LEFT, c.CONTENT_RIGHT)
    for panel, row, scene in zip(layout.panels, layout.label_rows, layout.scenes):
        assert card_box.contains(panel)
        assert panel.contains(row) and panel.contains(scene)
        assert not row.intersects(scene)
        assert row.bottom <= scene.top
        assert scene.height > scene.width  # retrato
    assert (layout.scenes[0].width, layout.scenes[0].height) == (layout.scenes[1].width, layout.scenes[1].height)
    if has_muscles:
        assert layout.panels[0].bottom < layout.footer.top
        assert layout.footer.contains(layout.footer_text)
        assert layout.footer.bottom == c.CONTENT_BOTTOM
        if has_silhouette:
            assert layout.footer.contains(layout.silhouette)
            assert not layout.footer_text.intersects(layout.silhouette)
            assert layout.silhouette.right + c.FOOTER_SILHOUETTE_GAP == layout.footer_text.left
        else:
            assert layout.silhouette is None
            assert layout.footer_text.left == layout.footer.left + c.FOOTER_TEXT_PADDING_X
    else:
        assert layout.footer is None and layout.silhouette is None


def test_altura_dos_paineis_e_fixa_com_ou_sem_musculos():
    """O rodape e sempre reservado: as caixas de cena nao mudam sem musculos."""
    com, sem = c.card_layout_v3(True), c.card_layout_v3(False)
    assert com.panels == sem.panels
    assert com.scenes == sem.scenes
    assert sem.panels[0].bottom + c.FOOTER_GAP + c.FOOTER_HEIGHT == c.CONTENT_BOTTOM


def test_medidas_do_layout_v3():
    layout = c.card_layout_v3(True)
    assert [(p.left, p.right) for p in layout.panels] == [(51, 502), (522, 973)]
    assert all((p.top, p.bottom) == (317, 1209) for p in layout.panels)
    assert all((s.width, s.height) == (415, 798) for s in layout.scenes)  # 892 - 76 - 18
    assert (layout.footer.left, layout.footer.top, layout.footer.right, layout.footer.bottom) == (51, 1229, 973, 1485)
    assert (layout.silhouette.width, layout.silhouette.height) == (244, 228)
    assert (layout.footer_text.left, layout.footer_text.right) == (343, 949)
    # A silhueta (frente + costas recortadas) cabe pela altura, sem sobra grande.
    largura, altura = c.bodymap.native_size()
    assert layout.silhouette.height * largura / altura <= layout.silhouette.width
    assert layout.silhouette.height * largura / altura >= layout.silhouette.width - 8


def test_versao_e_constantes_do_v3():
    assert c.LAYOUT_VERSION == "CARD_LAYOUT_V3_PHOTO"
    assert c.PANEL_LABELS == ("Posição inicial", "Movimento")
    assert c.PHOTO_RADIUS == 16


@pytest.mark.parametrize("has_muscles", [True, False])
def test_rotulos_ficam_fora_das_areas_de_cena(assets, has_muscles):
    layout = c.card_layout_v3(has_muscles)
    for ink, row in zip(c.label_ink_boxes(layout, assets), layout.label_rows):
        assert row.contains(ink)
        for scene in layout.scenes:
            assert not ink.intersects(scene)


# ---------------------------------------------------------------------------
# Titulo
# ---------------------------------------------------------------------------


def test_titulo_mais_longo_cabe_em_ate_3_linhas_dentro_da_caixa():
    assert len(LONGEST) == 51
    layout = c.layout_title(LONGEST)
    assert 1 <= len(layout.lines) <= c.TITLE_MAX_LINES
    assert c.TITLE_BOX.contains(layout.ink)
    assert layout.size >= c.TITLE_MIN_SIZE
    assert " ".join(layout.lines) == LONGEST.upper()  # nada cortado


def test_titulo_em_caixa_alta_preserva_os_acentos():
    layout = c.layout_title("Extensão de tríceps na máquina")
    assert " ".join(layout.lines) == "EXTENSÃO DE TRÍCEPS NA MÁQUINA"


def test_titulo_curto_fica_maior_e_equilibrado():
    curto = c.layout_title("Supino reto com barra")
    assert curto.size > c.layout_title(LONGEST).size
    assert curto.lines == ("SUPINO RETO", "COM BARRA")  # nao "SUPINO RETO COM / BARRA"
    assert c.TITLE_BOX.contains(curto.ink)


def test_nome_absurdo_ainda_cabe_na_caixa_com_reticencias():
    layout = c.layout_title("Supercalifragilistico" * 6 + " " + "palavra " * 30)
    assert len(layout.lines) <= c.TITLE_MAX_LINES
    assert c.TITLE_BOX.contains(layout.ink)
    assert layout.lines[-1].endswith(c.ELLIPSIS)


@pytest.mark.parametrize("titulo", ["", "   "])
def test_titulo_vazio_e_erro_tipado(assets, titulo):
    with pytest.raises(c.CardCompositionError) as exc:
        compose(c.CardContent(titulo), assets)
    assert (exc.value.code, exc.value.reason) == ("CARD_COMPOSITION_FAILED", "empty_title")


def test_cabecalho_so_tem_logo_titulo_e_fundo(assets):
    """Com o nome mais longo: no cabecalho (acima do divisor), fora do
    retangulo do logo e da caixa do titulo, so o fundo da marca."""
    image = _open(compose(c.CardContent(LONGEST, ("Bíceps",)), assets))
    header = image.crop((0, 0, c.CARD_WIDTH, c.DIVIDER_Y))
    assert not _is_solid(header.crop((c.TITLE_BOX.left, c.TITLE_BOX.top, c.TITLE_BOX.right, c.TITLE_BOX.bottom)),
                         c.COLOR_BACKGROUND)
    assert _is_solid(_blank_outside(header, [c.LOGO_BOX, c.TITLE_BOX], c.COLOR_BACKGROUND), c.COLOR_BACKGROUND)


def test_acentos_sao_desenhados_no_card(assets):
    """Mesmo card com e sem acento no titulo: a diferenca existe e fica so
    na caixa do titulo."""
    com = _open(compose(c.CardContent("Elevação"), assets))
    sem = _open(compose(c.CardContent("Elevacao"), assets))
    diff = ImageChops.difference(com, sem).getbbox()
    assert diff is not None
    assert c.TITLE_BOX.contains(c.Box(*diff))


# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------


def test_dentro_do_retangulo_do_logo_so_ha_logo_e_fundo(card, assets):
    box = c.LOGO_BOX
    esperado = Image.new("RGBA", (box.width, box.height), (*c.COLOR_BACKGROUND, 255))
    esperado.alpha_composite(assets.logo.resize((box.width, box.height), Image.Resampling.LANCZOS))
    real = card.crop((box.left, box.top, box.right, box.bottom))
    assert ImageChops.difference(real, esperado.convert("RGB")).getbbox() is None


# ---------------------------------------------------------------------------
# Cenas
# ---------------------------------------------------------------------------

SCENE_SIZES = [(1024, 1536), (1536, 1024), (800, 800), (400, 300)]


def _scene_crop(image: Image.Image, box: c.Box) -> Image.Image:
    return image.crop((box.left, box.top, box.right, box.bottom))


def _count_color(image: Image.Image, color) -> int:
    """Quantos pixels sao EXATAMENTE `color`."""
    diff = ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, color))
    red, green, blue = diff.split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()[0]


def _inside_rounded(box: c.Box) -> c.Box:
    """Caixa da foto sem os cantos arredondados (para provar cobertura total)."""
    m = c.PHOTO_RADIUS
    return c.Box(box.left + m, box.top + m, box.right - m, box.bottom - m)


@pytest.mark.parametrize("size", SCENE_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_cena_de_qualquer_proporcao_preenche_a_caixa_sem_borda_vazia(assets, size):
    png = ill.scene_photo_png(*size, START_COLOR)
    image = _open(compose(CONTENT, assets, start=png))
    box = c.card_layout_v3(True).scenes[0]
    inner = _scene_crop(image, _inside_rounded(box))
    # Nenhum pixel do miolo da caixa e o do painel: a foto cobre tudo (cover).
    assert ImageChops.difference(inner, Image.new("RGB", inner.size, c.COLOR_PANEL)).getbbox() is not None
    assert _count_color(inner, c.COLOR_PANEL) == 0
    # Faixas nas 4 bordas (fora dos cantos) tambem sao foto.
    for strip in (
        c.Box(box.left + c.PHOTO_RADIUS, box.top, box.right - c.PHOTO_RADIUS, box.top + 2),
        c.Box(box.left + c.PHOTO_RADIUS, box.bottom - 2, box.right - c.PHOTO_RADIUS, box.bottom),
        c.Box(box.left, box.top + c.PHOTO_RADIUS, box.left + 2, box.bottom - c.PHOTO_RADIUS),
        c.Box(box.right - 2, box.top + c.PHOTO_RADIUS, box.right, box.bottom - c.PHOTO_RADIUS),
    ):
        pixels = _scene_crop(image, strip)
        assert ImageChops.difference(pixels, Image.new("RGB", pixels.size, c.COLOR_PANEL)).getbbox() is not None
        assert _count_color(pixels, c.COLOR_PANEL) == 0


def test_cada_cena_no_seu_painel_cena1_esquerda_cena2_direita(card):
    layout = c.card_layout_v3(True)
    centro = lambda box: (box.left + box.width // 2, box.top + box.height // 4)  # noqa: E731 - fora do bloco claro
    esq, dir_ = card.getpixel(centro(layout.scenes[0])), card.getpixel(centro(layout.scenes[1]))
    assert esq[0] > esq[2] and dir_[2] > dir_[0]  # vermelho a esquerda, azul a direita


def test_cantos_da_foto_sao_arredondados(card):
    box = c.card_layout_v3(True).scenes[0]
    # O pixel de canto e do painel; o centro da aresta (fora do raio) e foto.
    assert card.getpixel((box.left, box.top)) == c.COLOR_PANEL
    assert card.getpixel((box.left + box.width // 2, box.top)) != c.COLOR_PANEL
    assert card.getpixel((box.left + c.PHOTO_RADIUS + 4, box.top + c.PHOTO_RADIUS + 4)) != c.COLOR_PANEL


def test_so_ha_foto_dentro_das_caixas_de_cena_e_o_resto_e_painel(card):
    layout = c.card_layout_v3(True)
    # Fora das caixas de cena e do restante do conteudo, o painel e o fundo continuam como antes: o diff entre
    # dois cards com fotos DIFERENTES fica so dentro das caixas de cena.
    outro = _open(compose(CONTENT, c.load_card_assets(), start=ill.scene_photo_png(1024, 1536, (20, 160, 20))))
    diff = ImageChops.difference(card, outro).getbbox()
    assert diff is not None and layout.scenes[0].contains(c.Box(*diff))


def test_rotulos_ficam_fora_das_fotos(card, assets):
    layout = c.card_layout_v3(True)
    for ink, scene in zip(c.label_ink_boxes(layout, assets), layout.scenes):
        assert not ink.intersects(scene)
    # E fora da foto desenhada: a linha de rotulo tem selo/texto, a foto nao.
    for row in layout.label_rows:
        assert not _is_solid(_scene_crop(card, c.Box(row.left + c.PANEL_BORDER, row.top, row.right - c.PANEL_BORDER, row.bottom)), c.COLOR_PANEL)


def test_rotulos_tem_os_textos_do_v3_e_aparecem_acentuados(assets):
    com = _open(compose(CONTENT, assets))
    layout = c.card_layout_v3(True)
    primeiro = _scene_crop(com, layout.label_rows[0])
    segundo = _scene_crop(com, layout.label_rows[1])
    assert not _is_solid(primeiro, c.COLOR_PANEL) and not _is_solid(segundo, c.COLOR_PANEL)
    # O selo laranja existe em cada linha de rotulo.
    assert _orange(primeiro) > 0 and _orange(segundo) > 0


@pytest.mark.parametrize("size", [(1024, 1536), (1536, 1024), (800, 800)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_cena_de_outro_tamanho_vira_card_1024x1536(assets, size):
    png = ill.scene_photo_png(*size)
    assert _open(compose(CONTENT, assets, start=png, end=png)).size == (1024, 1536)


def test_fracao_visivel_da_largura_na_foto_padrao():
    box = c.card_layout_v3(True).scenes[0]
    fracao = c.scene_visible_width_fraction(1024, 1536, box)
    assert fracao >= MIN_VISIBLE_WIDTH
    assert fracao < 1.0  # o cover corta as laterais (caixa mais estreita que 2:3)
    # Mesma conta do cover: fracao = largura da caixa / largura da foto ampliada.
    escala = max(box.width / 1024, box.height / 1536)
    assert fracao == pytest.approx(box.width / (1024 * escala))


def test_fracao_visivel_foto_mais_estreita_que_a_caixa_nao_perde_largura():
    box = c.card_layout_v3(True).scenes[0]
    assert c.scene_visible_width_fraction(200, 800, box) == pytest.approx(1.0)  # mais estreita: corta a altura, nao a largura
    assert c.scene_visible_width_fraction(1536, 1024, box) < c.scene_visible_width_fraction(1024, 1536, box)
    with pytest.raises(ValueError):
        c.scene_visible_width_fraction(0, 10, box)


def test_cover_centraliza_o_corte(assets):
    """Foto com o 'sujeito' (bloco claro) no centro: o centro da caixa e claro,
    e o corte e simetrico (esquerda e direita da caixa iguais)."""
    png = ill.scene_photo_png(1024, 1536, START_COLOR)
    image = _open(compose(CONTENT, assets, start=png))
    box = c.card_layout_v3(True).scenes[0]
    assert image.getpixel((box.left + box.width // 2, box.top + box.height // 2)) == (235, 235, 235)
    esquerda = image.getpixel((box.left + 40, box.top + box.height // 2))
    direita = image.getpixel((box.right - 41, box.top + box.height // 2))
    assert esquerda == direita


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"nao e imagem" * 20,
        ill.scene_photo_png(255, 1536),
        ill.scene_photo_png(1024, 100),
        ill.scene_photo_png(4097, 300),
    ],
    ids=["vazio", "lixo", "menor-lado-255", "menor-lado-100", "maior-lado-4097"],
)
@pytest.mark.parametrize("posicao", ["inicial", "final"])
def test_cena_invalida_e_erro_tipado(assets, data, posicao):
    valida = ill.scene_photo_png(1024, 1536)
    start, end = (data, valida) if posicao == "inicial" else (valida, data)
    with pytest.raises(c.CardCompositionError) as exc:
        c.compose_card_v3(start, end, CONTENT, assets)
    assert (exc.value.code, exc.value.reason) == ("CARD_COMPOSITION_FAILED", "invalid_scene")


def test_png_truncado_e_cena_invalida(assets):
    truncado = ill.scene_photo_png(1024, 1536)[:200]
    with pytest.raises(c.CardCompositionError) as exc:
        compose(CONTENT, assets, start=truncado)
    assert exc.value.reason == "invalid_scene"


def test_limites_de_lado_aceitos_nas_bordas(assets):
    # 256 de menor lado e aceito; so o que esta fora da faixa e recusado.
    png = ill.scene_photo_png(256, 300)
    assert _open(compose(CONTENT, assets, start=png)).size == (1024, 1536)


def test_titulo_vazio_vem_antes_da_validacao_das_cenas(assets):
    with pytest.raises(c.CardCompositionError) as exc:
        c.compose_card_v3(b"", b"", c.CardContent(" "), assets)
    assert exc.value.reason == "empty_title"


def test_codigo_v2_foi_removido():
    for nome in (
        "compose_card", "extract_scenes", "_foreground", "find_cut_row", "plan_scenes", "_load_illustration",
        "ExtractedScenes", "ScenePlacement", "CUT_RANGE", "ALPHA_THRESHOLD", "SCENE_GAP_RANGE", "card_layout",
    ):
        assert not hasattr(c, nome), nome


# ---------------------------------------------------------------------------
# Rodape
# ---------------------------------------------------------------------------


def test_linhas_do_rodape():
    assert c.footer_rows(CONTENT) == [("Principais:", "Peitoral"), ("Secundários:", "Tríceps, Ombros")]
    assert c.footer_rows(c.CardContent("X", ("Peitoral", "Tríceps"))) == [("Principais:", "Peitoral, Tríceps")]
    assert c.footer_rows(c.CardContent("X")) == []


LONGO = c.CardContent(
    "X",
    ("Anterior de coxas", "Posterior de coxas", "Adutores do quadril"),
    ("Glúteos",),
    ("quadriceps", "hamstring", "adductor"),
    ("gluteal",),
)
PIOR_CASO = c.CardContent(
    "X",
    ("Peitoral médio/inferior", "Panturrilha posterior", "Adutores do quadril"),
    ("Flexores do quadril", "Manguito rotador", "Costas superior"),
    ("chest", "calves", "adductor"),
    ("upper-back",),
)


def _orange(image: Image.Image) -> int:
    """Soma de (vermelho - azul): ~0 no grafite e no painel, alta no #FF9500."""
    red, _, blue = image.convert("RGB").split()
    return sum(v * n for v, n in enumerate(red.histogram())) - sum(v * n for v, n in enumerate(blue.histogram()))


def test_texto_do_rodape_maior_que_no_v1_e_nunca_abaixo_de_30px(assets):
    assert c.FOOTER_LINE_MIN_SIZE >= 30 and c.FOOTER_HEADING_SIZE >= 30
    assert c.FOOTER_LINE_MIN_SIZE > 28  # o maximo do V1 (20-28 px) ficou pequeno no celular
    layout = c.card_layout_v3(True)
    for conteudo in (CONTENT, LONGO, PIOR_CASO):
        footer = c.layout_footer(conteudo, assets, layout.footer_text)
        assert footer.heading_size == c.FOOTER_HEADING_SIZE
        assert all(c.FOOTER_LINE_MIN_SIZE <= line.size <= c.FOOTER_LINE_MAX_SIZE for line in footer.lines)
    # Conteudo curto usa o maior tamanho.
    curto = c.layout_footer(CONTENT, assets, layout.footer_text)
    assert {line.size for line in curto.lines} == {c.FOOTER_LINE_MAX_SIZE}


@pytest.mark.parametrize("conteudo", [CONTENT, LONGO, PIOR_CASO], ids=["curto", "longo", "pior-caso"])
@pytest.mark.parametrize("has_silhouette", [True, False])
def test_texto_do_rodape_cabe_na_coluna_sem_tocar_a_silhueta(assets, conteudo, has_silhouette):
    layout = c.card_layout_v3(True, has_silhouette)
    footer = c.layout_footer(conteudo, assets, layout.footer_text)
    assert layout.footer_text.contains(footer.ink)
    if has_silhouette:
        assert not footer.ink.intersects(layout.silhouette)
    assert 1 <= len(footer.lines) <= c.FOOTER_MAX_LINES
    for line in footer.lines:
        value = c.font(assets.inter_regular, line.size)
        assert line.value_x + c._text_width(line.value, value) <= layout.footer_text.right
    baselines = [line.baseline for line in footer.lines]
    assert baselines == sorted(set(baselines))


def test_musculos_longos_quebram_com_recuo_pendurado_sem_cortar(assets):
    layout = c.card_layout_v3(True)
    footer = c.layout_footer(LONGO, assets, layout.footer_text)
    labels = [line.label for line in footer.lines]
    assert labels[0] == "Principais:" and "Secundários:" in labels
    principais = footer.lines[: labels.index("Secundários:")]
    assert len(principais) > 1  # quebrou
    assert all(not line.label for line in principais[1:])
    assert len({line.value_x for line in principais}) == 1  # continuacao alinhada ao 1o valor
    assert " ".join(line.value for line in principais) == "Anterior de coxas, Posterior de coxas, Adutores do quadril"
    assert all(c.ELLIPSIS not in line.value for line in footer.lines)


def test_pior_caso_corta_com_reticencias_em_ate_4_linhas(assets):
    layout = c.card_layout_v3(True)
    footer = c.layout_footer(PIOR_CASO, assets, layout.footer_text)
    assert len(footer.lines) == c.FOOTER_MAX_LINES
    assert footer.lines[-1].value.endswith(c.ELLIPSIS)
    assert {line.size for line in footer.lines} == {c.FOOTER_LINE_MIN_SIZE}


def test_sem_secundarios_so_a_linha_dos_principais(assets):
    layout = c.card_layout_v3(True)
    conteudo = c.CardContent("Supino reto com barra", ("Peitoral",), (), ("chest",))
    footer = c.layout_footer(conteudo, assets, layout.footer_text)
    assert [(line.label, line.value) for line in footer.lines] == [("Principais:", "Peitoral")]
    image = _open(compose(conteudo, assets))
    # Na coluna do texto, fora da tinta do bloco, so o fundo do painel.
    texto = layout.footer_text
    coluna = _blank_outside(image, [footer.ink], c.COLOR_PANEL).crop((texto.left, texto.top, texto.right, texto.bottom))
    assert _is_solid(coluna, c.COLOR_PANEL)
    assert not _is_solid(image.crop((footer.ink.left, footer.ink.top, footer.ink.right, footer.ink.bottom)), c.COLOR_PANEL)


def test_sem_musculos_nao_ha_rodape(assets):
    # Slug sem nome de musculo nao cria rodape (nem silhueta).
    image = _open(compose(c.CardContent("Prancha", primary_slugs=("abs",)), assets))
    reservado = c.Box(c.CONTENT_LEFT, c.CONTENT_BOTTOM - c.FOOTER_HEIGHT, c.CONTENT_RIGHT, c.CONTENT_BOTTOM)
    assert _is_solid(_scene_crop(image, reservado), c.COLOR_BACKGROUND)  # espaco reservado, vazio
    assert _is_solid(image.crop((0, c.CONTENT_BOTTOM + 1, c.CARD_WIDTH, c.CARD_HEIGHT)), c.COLOR_BACKGROUND)
    # Os paineis NAO crescem: terminam onde terminam com musculos.
    painel = c.card_layout_v3(False).panels[0]
    assert painel.bottom == c.card_layout_v3(True).panels[0].bottom
    assert _is_solid(image.crop((painel.left, painel.bottom + 1, painel.right, reservado.top)), c.COLOR_BACKGROUND)


def test_silhueta_acende_os_slugs_e_fica_dentro_da_caixa(card, assets):
    """Trocar so os slugs muda so a caixa da silhueta; em volta dela (ate o
    texto) so o painel."""
    layout = c.card_layout_v3(True)
    perna = _open(compose(c.CardContent(
        CONTENT.title, CONTENT.primary_muscles, CONTENT.secondary_muscles, ("quadriceps",), ("calves",)), assets))
    diff = ImageChops.difference(card, perna).getbbox()
    assert diff is not None and layout.silhouette.contains(c.Box(*diff))
    box = layout.silhouette
    assert not _is_solid(card.crop((box.left, box.top, box.right, box.bottom)), c.COLOR_PANEL)
    assert _is_solid(card.crop((box.right, box.top, layout.footer_text.left, box.bottom)), c.COLOR_PANEL)
    # Acima e abaixo da caixa (fora do canto arredondado do painel): so o painel.
    left = layout.footer.left + c.PANEL_RADIUS
    assert _is_solid(card.crop((left, layout.footer.top + c.PANEL_BORDER, box.right, box.top)), c.COLOR_PANEL)
    assert _is_solid(card.crop((left, box.bottom, box.right, layout.footer.bottom - c.PANEL_BORDER)), c.COLOR_PANEL)


def test_silhueta_principal_laranja_forte_secundario_fraco(assets):
    box = c.card_layout_v3(True).silhouette

    def laranja(primary_slugs, secondary_slugs):
        conteudo = c.CardContent("Supino reto com barra", ("Peitoral",), (), primary_slugs, secondary_slugs)
        image = _open(compose(conteudo, assets))
        return _orange(image.crop((box.left, box.top, box.right, box.bottom)))

    nada = laranja(("neck",), ())  # o pescoco quase nao aparece: base de comparacao
    secundario = laranja(("neck",), ("chest",))
    principal = laranja(("neck", "chest"), ())
    assert nada < secundario < principal


def test_sem_slug_com_regiao_o_texto_ocupa_o_painel(assets):
    caixa = c.card_layout_v3(True).silhouette
    sem = c.card_layout_v3(True, has_silhouette=False)
    for conteudo in (c.CardContent("Prancha", ("Core",)), c.CardContent("Prancha", ("Core",), (), ("knees", "xyz"))):
        image = _open(compose(conteudo, assets))
        footer = c.layout_footer(conteudo, assets, sem.footer_text)
        assert footer.ink.left < caixa.right  # o texto comeca onde a silhueta estaria
        regiao = _blank_outside(image, [footer.ink], c.COLOR_PANEL).crop((caixa.left, caixa.top, caixa.right, caixa.bottom))
        assert _is_solid(regiao, c.COLOR_PANEL)


def test_acentos_do_rodape_sao_desenhados(assets):
    com = _open(compose(c.CardContent("Supino", ("Tríceps",), (), ("triceps",)), assets))
    sem = _open(compose(c.CardContent("Supino", ("Triceps",), (), ("triceps",)), assets))
    diff = ImageChops.difference(com, sem).getbbox()
    assert diff is not None
    assert c.card_layout_v3(True).footer_text.contains(c.Box(*diff))
    assert (c.FOOTER_HEADING, c.FOOTER_SECONDARY_LABEL) == ("MÚSCULOS", "Secundários")


def test_mapa_corporal_ausente_e_erro_tipado(assets, monkeypatch):
    def quebrado():
        raise c.bodymap.BodyMapError("sem assets")

    monkeypatch.setattr(c.bodymap, "load_body_map_assets", quebrado)
    sem_mapa = c.CardAssets(logo=assets.logo)
    with pytest.raises(c.CardCompositionError) as exc:
        compose(CONTENT, sem_mapa)
    assert exc.value.reason == "assets_missing"
    # Sem slug o card nem precisa do mapa.
    assert compose(c.CardContent("Prancha", ("Core",)), sem_mapa)


def test_conteudo_do_contexto_usa_as_regras_do_prompt():
    ctx = prompt.ExerciseCardContext(
        exercise_id=1,
        name="  Desenvolvimento   com halteres ",
        primary_muscles=("Ombros / Deltoides", "Tríceps"),
        secondary_muscles=("Trapézio", "Tríceps", "Core", "Peitoral superior"),
        stabilizer_muscles=("Abdômen",),
        primary_slugs=("front-deltoids", "back-deltoids", "triceps"),
        secondary_slugs=("trapezius", "chest"),
    )
    conteudo = c.content_from_context(ctx)
    assert conteudo.title == "Desenvolvimento com halteres"
    assert conteudo.primary_muscles == ("Ombros", "Tríceps")
    assert conteudo.secondary_muscles == ("Trapézio", "Core")  # <= 4 no total, sem repetir principal
    assert "Abdômen" not in conteudo.primary_muscles + conteudo.secondary_muscles
    # A silhueta acende todos os slugs dos dois papeis (o texto e que e limitado).
    assert conteudo.primary_slugs == ("front-deltoids", "back-deltoids", "triceps")
    assert conteudo.secondary_slugs == ("trapezius", "chest")


# ---------------------------------------------------------------------------
# Saida
# ---------------------------------------------------------------------------


def test_saida_png_rgb_1024x1536_sem_metadados(assets):
    data = compose(CONTENT, assets)
    image = _open(data)
    assert (image.format, image.mode, image.size) == ("PNG", "RGB", (1024, 1536))
    assert image.info == {}
    assert len(image.getexif()) == 0


def test_deterministico_mesma_entrada_mesmos_bytes(assets):
    assert compose(CONTENT, assets) == compose(CONTENT, assets)


def test_cena_com_metadados_nao_vaza_nada(assets):
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Comment", "segredo da cena")
    buffer = io.BytesIO()
    Image.open(io.BytesIO(ill.scene_photo_png(1024, 1536))).save(buffer, format="PNG", pnginfo=info, dpi=(300, 300))
    data = compose(CONTENT, assets, start=buffer.getvalue(), end=buffer.getvalue())
    assert b"segredo" not in data
    assert _open(data).info == {}


def test_cena_jpeg_e_aceita_e_vira_png_sem_metadados(assets):
    buffer = io.BytesIO()
    Image.open(io.BytesIO(ill.scene_photo_png(1024, 1536))).save(buffer, format="JPEG")
    data = compose(CONTENT, assets, start=buffer.getvalue())
    assert (_open(data).format, _open(data).size) == ("PNG", (1024, 1536))


def test_assets_carregados_uma_vez_por_processo():
    assert c.load_card_assets() is c.load_card_assets()


def test_logo_ausente_e_erro_tipado(monkeypatch, tmp_path):
    c.load_card_assets.cache_clear()
    monkeypatch.setattr(processor, "DEFAULT_LOGO_PATH", tmp_path / "nao-existe.png")
    try:
        with pytest.raises(c.CardCompositionError) as exc:
            c.load_card_assets()
        assert exc.value.reason == "assets_missing"
    finally:
        monkeypatch.undo()
        c.load_card_assets.cache_clear()


def test_fonte_ausente_e_erro_tipado(assets, tmp_path):
    sem_fonte = c.CardAssets(logo=assets.logo, oswald_bold=tmp_path / "nao-existe.ttf")
    with pytest.raises(c.CardCompositionError) as exc:
        compose(CONTENT, sem_fonte)
    assert exc.value.code == "CARD_COMPOSITION_FAILED"
