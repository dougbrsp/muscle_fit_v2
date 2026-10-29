"""Compositor do card ilustrado (`app/exercise_card_composer.py`) -
Estrategia B: a IA entrega so a ilustracao; a aplicacao escreve titulo,
rotulos, musculos, desenha a silhueta do mapa corporal v1 no rodape
(`CARD_LAYOUT_V2`) e cola o logo.

Ilustracoes sinteticas (`tests/support/card_illustrations.py`: dois bonecos
com barra, fundo transparente ou opaco); fontes e logo REAIS do repositorio
(`load_card_assets`). Os testes provam propriedades (caixas, pixels por
regiao, mesma escala, bytes iguais), nunca comparam com uma imagem de
referencia - o Pillow do CI (12.3, FreeType 2.14) e o local podem rasterizar diferente.

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
PAD_TOLERANCE = 3  # px: abertura morfologica e espessura das linhas sinteticas


@pytest.fixture(scope="module")
def assets() -> c.CardAssets:
    return c.load_card_assets()


@pytest.fixture(scope="module")
def illustration() -> bytes:
    return ill.illustration_png()


@pytest.fixture(scope="module")
def card(illustration, assets) -> Image.Image:
    return _open(c.compose_card(illustration, CONTENT, assets))


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
    assert c.LAYOUT_VERSION == "CARD_LAYOUT_V2"
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
    layout = c.card_layout(has_muscles, has_silhouette)
    card_box = c.Box(0, 0, c.CARD_WIDTH, c.CARD_HEIGHT)
    assert not c.LOGO_BOX.intersects(c.TITLE_BOX)
    assert c.TITLE_BOX.left - c.LOGO_BOX.right == c.TITLE_GAP
    assert (c.TITLE_BOX.top, c.TITLE_BOX.bottom) == (c.LOGO_BOX.top, c.LOGO_BOX.bottom)
    assert c.TITLE_BOX.bottom <= c.DIVIDER_Y < layout.panels[0].top
    assert layout.panels[0].bottom < layout.panels[1].top
    for panel, row, scene in zip(layout.panels, layout.label_rows, layout.scenes):
        assert card_box.contains(panel)
        assert panel.contains(row) and panel.contains(scene)
        assert not row.intersects(scene)
        assert row.bottom <= scene.top
    assert layout.scenes[0].width == layout.scenes[1].width and layout.scenes[0].height == layout.scenes[1].height
    if has_muscles:
        assert layout.panels[1].bottom < layout.footer.top
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
        assert layout.panels[1].bottom == c.CONTENT_BOTTOM


def test_sem_musculos_os_paineis_crescem():
    com, sem = c.card_layout(True), c.card_layout(False)
    assert sem.scenes[0].height > com.scenes[0].height


def test_medidas_do_layout_v2():
    """As medidas documentadas no modulo (rodape com silhueta; paineis um
    pouco menores que no V1 - 498 -> 436)."""
    layout = c.card_layout(True)
    assert [(p.top, p.bottom) for p in layout.panels] == [(317, 753), (773, 1209)]
    assert all((s.width, s.height) == (886, 342) for s in layout.scenes)
    assert (layout.footer.left, layout.footer.top, layout.footer.right, layout.footer.bottom) == (51, 1229, 973, 1485)
    assert (layout.silhouette.width, layout.silhouette.height) == (244, 228)
    assert (layout.footer_text.left, layout.footer_text.right) == (343, 949)
    # A silhueta (frente + costas recortadas) cabe pela altura, sem sobra grande.
    largura, altura = c.bodymap.native_size()
    assert layout.silhouette.height * largura / altura <= layout.silhouette.width
    assert layout.silhouette.height * largura / altura >= layout.silhouette.width - 8


@pytest.mark.parametrize("has_muscles", [True, False])
def test_rotulos_ficam_fora_das_areas_de_cena(assets, has_muscles):
    layout = c.card_layout(has_muscles)
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
def test_titulo_vazio_e_erro_tipado(illustration, assets, titulo):
    with pytest.raises(c.CardCompositionError) as exc:
        c.compose_card(illustration, c.CardContent(titulo), assets)
    assert (exc.value.code, exc.value.reason) == ("CARD_COMPOSITION_FAILED", "empty_title")


def test_cabecalho_so_tem_logo_titulo_e_fundo(illustration, assets):
    """Com o nome mais longo: no cabecalho (acima do divisor), fora do
    retangulo do logo e da caixa do titulo, so o fundo da marca."""
    image = _open(c.compose_card(illustration, c.CardContent(LONGEST, ("Bíceps",)), assets))
    header = image.crop((0, 0, c.CARD_WIDTH, c.DIVIDER_Y))
    assert not _is_solid(header.crop((c.TITLE_BOX.left, c.TITLE_BOX.top, c.TITLE_BOX.right, c.TITLE_BOX.bottom)),
                         c.COLOR_BACKGROUND)
    assert _is_solid(_blank_outside(header, [c.LOGO_BOX, c.TITLE_BOX], c.COLOR_BACKGROUND), c.COLOR_BACKGROUND)


def test_acentos_sao_desenhados_no_card(illustration, assets):
    """Mesmo card com e sem acento no titulo: a diferenca existe e fica so
    na caixa do titulo."""
    com = _open(c.compose_card(illustration, c.CardContent("Elevação"), assets))
    sem = _open(c.compose_card(illustration, c.CardContent("Elevacao"), assets))
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


def _expected_box(scene: ill.SceneBox, cut: int, top_half: bool) -> c.Box:
    low, high = (0, cut) if top_half else (cut, ill.HEIGHT)
    return c.Box(
        max(0, scene.left - c.SCENE_PAD),
        max(low, scene.top - c.SCENE_PAD),
        min(ill.WIDTH, scene.right + c.SCENE_PAD),
        min(high, scene.bottom + c.SCENE_PAD),
    )


def _assert_close(got: c.Box, want: c.Box) -> None:
    for attr in ("left", "top", "right", "bottom"):
        assert abs(getattr(got, attr) - getattr(want, attr)) <= PAD_TOLERANCE, (attr, got, want)


@pytest.mark.parametrize(
    "background, transparente",
    [(None, True), ((235, 235, 235), False), ((20, 20, 20), False)],
    ids=["transparente", "opaco-claro", "opaco-escuro"],
)
def test_extrai_as_duas_cenas_e_corta_no_vao(background, transparente):
    extraidas = c.extract_scenes(ill.illustration(background=background))
    assert extraidas.transparent is transparente
    # O vao entre as cenas (620..900) esta todo dentro de 40-60 % da altura.
    assert ill.DEFAULT_TOP.bottom <= extraidas.cut_y < ill.DEFAULT_BOTTOM.top
    assert 0.40 * ill.HEIGHT <= extraidas.cut_y <= 0.60 * ill.HEIGHT
    esperadas = (
        _expected_box(ill.DEFAULT_TOP, extraidas.cut_y, True),
        _expected_box(ill.DEFAULT_BOTTOM, extraidas.cut_y, False),
    )
    for got, want in zip(extraidas.boxes, esperadas):
        if background == (20, 20, 20):
            # Fundo escuro opaco: as anilhas (quase da cor do fundo) viram
            # fundo - a caixa encolhe, mas nunca passa da cena desenhada.
            assert want.contains(got) and got.width > want.width // 2
        else:
            _assert_close(got, want)
    for cena, box in zip(extraidas.scenes, extraidas.boxes):
        assert cena.mode == "RGBA" and cena.size == (box.width, box.height)


def test_corta_na_linha_mais_vazia_mesmo_com_algo_cruzando():
    """Um "cabo" fino cruza o meio inteiro: nao ha linha vazia, o corte cai
    na linha com menos tinta (dentro de 40-60 %)."""
    image = ill.illustration(top=ill.SceneBox(150, 140, 880, 690), bottom=ill.SceneBox(260, 760, 760, 1420))
    from PIL import ImageDraw

    ImageDraw.Draw(image).rectangle((500, 600, 505, 900), fill=(150, 155, 160, 255))
    extraidas = c.extract_scenes(image)
    assert 690 <= extraidas.cut_y < 760


def test_cenas_desalinhadas_e_de_tamanhos_diferentes_na_mesma_escala():
    pequena = ill.SceneBox(60, 300, 330, 520)  # 270x220, encostada a esquerda
    grande = ill.SceneBox(380, 820, 990, 1500)  # 610x680, a direita
    extraidas = c.extract_scenes(ill.illustration(top=pequena, bottom=grande))
    layout = c.card_layout(True)
    sizes = [scene.size for scene in extraidas.scenes]
    placements = c.plan_scenes(sizes, layout.scenes)
    escala = min(
        [c.MAX_SCENE_SCALE] + [a.width / w for (w, _), a in zip(sizes, layout.scenes)]
        + [a.height / h for (_, h), a in zip(sizes, layout.scenes)]
    )
    for (w, h), placement, area in zip(sizes, placements, layout.scenes):
        assert placement.scale == pytest.approx(escala)
        assert abs(placement.box.width - w * escala) <= 1 and abs(placement.box.height - h * escala) <= 1
        assert area.contains(placement.box)
        # Centralizada na area.
        assert abs((placement.box.left - area.left) - (area.right - placement.box.right)) <= 1
        assert abs((placement.box.top - area.top) - (area.bottom - placement.box.bottom)) <= 1
    # A grande limita a escala (preenche a altura da area); a pequena fica menor.
    assert placements[1].box.height == layout.scenes[1].height
    assert placements[0].box.width < placements[1].box.width


def test_escala_limitada_para_nao_borrar():
    placements = c.plan_scenes([(100, 80), (120, 90)], c.card_layout(True).scenes)
    assert all(p.scale == c.MAX_SCENE_SCALE for p in placements)
    assert (placements[0].box.width, placements[0].box.height) == (150, 120)


def test_cena_so_altera_pixels_dentro_das_areas_de_cena(card, assets):
    outra = ill.illustration_png(top=ill.SceneBox(100, 200, 600, 600), bottom=ill.SceneBox(500, 950, 950, 1300))
    diferente = _open(c.compose_card(outra, CONTENT, assets))
    diff = ImageChops.difference(card, diferente)
    assert diff.getbbox() is not None
    layout = c.card_layout(True)
    assert _blank_outside(diff, list(layout.scenes), (0, 0, 0)).getbbox() is None


@pytest.mark.parametrize("background", [None, (235, 235, 235)], ids=["transparente", "opaco"])
def test_sem_emenda_retangular_em_volta_da_cena(assets, background):
    """O fundo da ilustracao (mesmo opaco e claro) nao vira um retangulo
    sobre o painel: em volta da figura, dentro da caixa colada, so #141414."""
    png = ill.illustration_png(background=background)
    image = _open(c.compose_card(png, CONTENT, assets))
    layout = c.card_layout(True)
    extraidas = c.extract_scenes(_open(png))
    placements = c.plan_scenes([s.size for s in extraidas.scenes], layout.scenes)
    for placement in placements:
        box = placement.box
        faixa_de_cima = image.crop((box.left, box.top, box.right, box.top + 3))
        assert _is_solid(faixa_de_cima, c.COLOR_PANEL)
        cantos = image.crop((box.left, box.bottom - 3, box.left + 3, box.bottom))
        assert _is_solid(cantos, c.COLOR_PANEL)


@pytest.mark.parametrize(
    "png",
    [
        ill.empty_png(transparent=True),
        ill.empty_png(transparent=False),
        ill.illustration_png(bottom=None),
        ill.illustration_png(top=None),
        ill.illustration_png(background=(40, 40, 40), top=None, bottom=None),
    ],
    ids=["transparente-vazia", "opaca-vazia", "so-cena-de-cima", "so-cena-de-baixo", "opaca-lisa"],
)
def test_sem_primeiro_plano_e_erro_tipado(assets, png):
    with pytest.raises(c.CardCompositionError) as exc:
        c.compose_card(png, CONTENT, assets)
    assert (exc.value.code, exc.value.reason) == ("CARD_COMPOSITION_FAILED", "no_foreground")


@pytest.mark.parametrize(
    "data",
    [b"", b"nao e imagem" * 20, ill.to_png(Image.new("RGBA", (100, 150), (0, 0, 0, 0)))],
    ids=["vazio", "lixo", "pequena-demais"],
)
def test_ilustracao_invalida_e_erro_tipado(assets, data):
    with pytest.raises(c.CardCompositionError) as exc:
        c.compose_card(data, CONTENT, assets)
    assert exc.value.reason == "invalid_illustration"


def test_ilustracao_em_outro_tamanho_vira_card_1024x1536(assets):
    png = ill.illustration_png(
        size=(1024, 1024), top=ill.SceneBox(150, 80, 880, 400), bottom=ill.SceneBox(260, 600, 760, 960)
    )
    assert _open(c.compose_card(png, CONTENT, assets)).size == (1024, 1536)


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
    layout = c.card_layout(True)
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
    layout = c.card_layout(True, has_silhouette)
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
    layout = c.card_layout(True)
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
    layout = c.card_layout(True)
    footer = c.layout_footer(PIOR_CASO, assets, layout.footer_text)
    assert len(footer.lines) == c.FOOTER_MAX_LINES
    assert footer.lines[-1].value.endswith(c.ELLIPSIS)
    assert {line.size for line in footer.lines} == {c.FOOTER_LINE_MIN_SIZE}


def test_sem_secundarios_so_a_linha_dos_principais(illustration, assets):
    layout = c.card_layout(True)
    conteudo = c.CardContent("Supino reto com barra", ("Peitoral",), (), ("chest",))
    footer = c.layout_footer(conteudo, assets, layout.footer_text)
    assert [(line.label, line.value) for line in footer.lines] == [("Principais:", "Peitoral")]
    image = _open(c.compose_card(illustration, conteudo, assets))
    # Na coluna do texto, fora da tinta do bloco, so o fundo do painel.
    texto = layout.footer_text
    coluna = _blank_outside(image, [footer.ink], c.COLOR_PANEL).crop((texto.left, texto.top, texto.right, texto.bottom))
    assert _is_solid(coluna, c.COLOR_PANEL)
    assert not _is_solid(image.crop((footer.ink.left, footer.ink.top, footer.ink.right, footer.ink.bottom)), c.COLOR_PANEL)


def test_sem_musculos_nao_ha_rodape(illustration, assets):
    # Slug sem nome de musculo nao cria rodape (nem silhueta).
    image = _open(c.compose_card(illustration, c.CardContent("Prancha", primary_slugs=("abs",)), assets))
    assert _is_solid(image.crop((0, c.CONTENT_BOTTOM + 1, c.CARD_WIDTH, c.CARD_HEIGHT)), c.COLOR_BACKGROUND)
    # O 2o painel desce ate onde o rodape estaria.
    painel = c.card_layout(False).panels[1]
    assert not _is_solid(image.crop((painel.left, painel.bottom - 4, painel.right, painel.bottom)), c.COLOR_BACKGROUND)


def test_silhueta_acende_os_slugs_e_fica_dentro_da_caixa(card, illustration, assets):
    """Trocar so os slugs muda so a caixa da silhueta; em volta dela (ate o
    texto) so o painel."""
    layout = c.card_layout(True)
    perna = _open(c.compose_card(illustration, c.CardContent(
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


def test_silhueta_principal_laranja_forte_secundario_fraco(illustration, assets):
    box = c.card_layout(True).silhouette

    def laranja(primary_slugs, secondary_slugs):
        conteudo = c.CardContent("Supino reto com barra", ("Peitoral",), (), primary_slugs, secondary_slugs)
        image = _open(c.compose_card(illustration, conteudo, assets))
        return _orange(image.crop((box.left, box.top, box.right, box.bottom)))

    nada = laranja(("neck",), ())  # o pescoco quase nao aparece: base de comparacao
    secundario = laranja(("neck",), ("chest",))
    principal = laranja(("neck", "chest"), ())
    assert nada < secundario < principal


def test_sem_slug_com_regiao_o_texto_ocupa_o_painel(illustration, assets):
    caixa = c.card_layout(True).silhouette
    sem = c.card_layout(True, has_silhouette=False)
    for conteudo in (c.CardContent("Prancha", ("Core",)), c.CardContent("Prancha", ("Core",), (), ("knees", "xyz"))):
        image = _open(c.compose_card(illustration, conteudo, assets))
        footer = c.layout_footer(conteudo, assets, sem.footer_text)
        assert footer.ink.left < caixa.right  # o texto comeca onde a silhueta estaria
        regiao = _blank_outside(image, [footer.ink], c.COLOR_PANEL).crop((caixa.left, caixa.top, caixa.right, caixa.bottom))
        assert _is_solid(regiao, c.COLOR_PANEL)


def test_acentos_do_rodape_sao_desenhados(illustration, assets):
    com = _open(c.compose_card(illustration, c.CardContent("Supino", ("Tríceps",), (), ("triceps",)), assets))
    sem = _open(c.compose_card(illustration, c.CardContent("Supino", ("Triceps",), (), ("triceps",)), assets))
    diff = ImageChops.difference(com, sem).getbbox()
    assert diff is not None
    assert c.card_layout(True).footer_text.contains(c.Box(*diff))
    assert (c.FOOTER_HEADING, c.FOOTER_SECONDARY_LABEL) == ("MÚSCULOS", "Secundários")


def test_mapa_corporal_ausente_e_erro_tipado(illustration, assets, monkeypatch):
    def quebrado():
        raise c.bodymap.BodyMapError("sem assets")

    monkeypatch.setattr(c.bodymap, "load_body_map_assets", quebrado)
    sem_mapa = c.CardAssets(logo=assets.logo)
    with pytest.raises(c.CardCompositionError) as exc:
        c.compose_card(illustration, CONTENT, sem_mapa)
    assert exc.value.reason == "assets_missing"
    # Sem slug o card nem precisa do mapa.
    assert c.compose_card(illustration, c.CardContent("Prancha", ("Core",)), sem_mapa)


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


def test_saida_png_rgb_1024x1536_sem_metadados(illustration, assets):
    data = c.compose_card(illustration, CONTENT, assets)
    image = _open(data)
    assert (image.format, image.mode, image.size) == ("PNG", "RGB", (1024, 1536))
    assert image.info == {}
    assert len(image.getexif()) == 0


def test_deterministico_mesma_entrada_mesmos_bytes(illustration, assets):
    assert c.compose_card(illustration, CONTENT, assets) == c.compose_card(illustration, CONTENT, assets)


def test_ilustracao_com_metadados_nao_vaza_nada(assets):
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Comment", "segredo da ilustracao")
    buffer = io.BytesIO()
    ill.illustration().save(buffer, format="PNG", pnginfo=info, dpi=(300, 300))
    data = c.compose_card(buffer.getvalue(), CONTENT, assets)
    assert b"segredo" not in data
    assert _open(data).info == {}


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


def test_fonte_ausente_e_erro_tipado(illustration, assets, tmp_path):
    sem_fonte = c.CardAssets(logo=assets.logo, oswald_bold=tmp_path / "nao-existe.ttf")
    with pytest.raises(c.CardCompositionError) as exc:
        c.compose_card(illustration, CONTENT, sem_fonte)
    assert exc.value.code == "CARD_COMPOSITION_FAILED"
