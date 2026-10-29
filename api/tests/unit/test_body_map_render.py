"""Mapa corporal v1 (`app/body_map_render.py` + `app/assets/bodymap/v1/`).

Arte e regioes aprovadas pelo usuario em 2026-10-03. Os testes provam o
pacote (hashes do manifest, `regions.json` igual ao da web), a formula (LUT),
e propriedades do render: cor so dentro da mascara do slug, principal x
secundario, alias, slug desconhecido, vistas/recorte/escala e determinismo.
Nada compara com imagem de referencia (o Pillow do CI e o local podem
rasterizar diferente); tudo sem rede.

Rodar: cd api && python -m pytest tests/unit/test_body_map_render.py
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageFilter

from app import body_map_render as bm

ASSETS = bm.ASSETS_DIR
FRONTEND_REGIONS = Path(__file__).resolve().parents[3] / "frontend" / "public" / "bodymap" / "v1" / "regions.json"
SEM_BRILHO = bm.RegionStyle("#FF9500", 1.0, glow=False)


@pytest.fixture(scope="module")
def assets() -> bm.BodyMapAssets:
    return bm.load_body_map_assets()


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _binary(image: Image.Image) -> Image.Image:
    return image.convert("L").point(lambda v: 255 if v else 0)


def _diff_mask(a: Image.Image, b: Image.Image) -> Image.Image:
    """255 onde qualquer canal (RGBA) difere."""
    channels = ImageChops.difference(a, b).split()
    acc = channels[0]
    for channel in channels[1:]:
        acc = ImageChops.lighter(acc, channel)
    return _binary(acc)


def _mean_in(image: Image.Image, mask: Image.Image) -> tuple[float, ...]:
    """Media (R, G, B) dos pixels dentro de `mask`."""
    out = []
    for channel in image.convert("RGB").split():
        hist = channel.histogram(mask=mask)
        out.append(sum(value * count for value, count in enumerate(hist)) / sum(hist))
    return tuple(out)


# ---------------------------------------------------------------------------
# Pacote de assets
# ---------------------------------------------------------------------------


def test_manifest_descreve_o_pacote_v1(manifest, assets):
    assert manifest["version"] == bm.MANIFEST_VERSION == assets.version
    assert manifest["canvas"] == [1536, 1024] and assets.canvas == (1536, 1024)
    assert manifest["views"] == {"front": [211, 26, 664, 947], "back": [831, 26, 1314, 946]}
    comp = manifest["compositor"]
    assert (comp["lum_lo"], comp["lum_hi"], comp["K0"], comp["K1"], comp["dim"]) == (4, 181, 0.3, 0.7, 0.82)
    assert comp["K0"] + comp["K1"] <= 1.0  # tinta nunca estoura o canal dominante
    assert manifest["aliases"] == {"abductors": "adductor"}


def test_cada_arquivo_bate_com_o_sha256_do_manifest(manifest):
    files = manifest["files"]
    on_disk = {p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*") if p.is_file()}
    # Tudo que o manifest lista existe; fora dele, so o proprio manifest e o README.
    assert set(files) <= on_disk
    assert on_disk - set(files) == {"manifest.json", "README.md"}
    for name, meta in files.items():
        path = ASSETS / name
        assert path.stat().st_size == meta["bytes"], name
        assert _sha256(path) == meta["sha256"], name


def test_regions_json_e_o_canonico_e_bate_com_a_web(manifest):
    regions = ASSETS / "regions.json"
    assert _sha256(regions) == manifest["files"]["regions.json"]["sha256"]
    data = json.loads(regions.read_text(encoding="utf-8"))
    assert data["viewBox"] == [0, 0, 1536, 1024] and data["fillRule"] == "evenodd"
    # Uma mascara por path (rasterizada dos paths pelo tools/bodymap/export_assets.py).
    masks = {p.stem for p in (ASSETS / "masks").glob("*.png")}
    assert set(data["regions"]) == masks
    assert set(manifest["slugs_db"]) | set(manifest["slugs_extra"]) | set(manifest["aliases"]) == masks
    if FRONTEND_REGIONS.is_file():  # o PR do frontend versiona o MESMO arquivo
        assert FRONTEND_REGIONS.read_bytes() == regions.read_bytes()


def test_mascaras_sao_1_bit_no_tamanho_do_canvas_e_dentro_do_corpo(assets):
    body = assets.alpha.point(lambda v: 255 if v > 0 else 0)
    for slug in assets.slugs:
        with Image.open(ASSETS / "masks" / f"{slug}.png") as raw:
            assert raw.mode == "1" and raw.size == (1536, 1024), slug
        mask = assets.masks[slug]
        assert mask.getbbox() is not None, slug
        assert {value for _, value in mask.getcolors()} <= {0, 255}, slug
        # Nada de mascara fora da silhueta da arte.
        fora = ImageChops.multiply(mask, ImageChops.invert(body))
        assert fora.getbbox() is None, slug


def test_alias_tem_a_mesma_regiao_do_canonico(assets):
    assert ImageChops.difference(assets.masks["abductors"], assets.masks["adductor"]).getbbox() is None


def test_arte_sem_cor_escondida_sob_o_alfa_zero(assets):
    invisivel = assets.alpha.point(lambda v: 255 if v == 0 else 0)
    escondido = Image.composite(assets.dimmed, Image.new("RGB", assets.canvas, 0), invisivel)
    assert escondido.getbbox() is None


def test_assets_carregados_uma_vez_por_processo():
    assert bm.load_body_map_assets() is bm.load_body_map_assets()


def test_pasta_sem_assets_e_erro_tipado(tmp_path):
    with pytest.raises(bm.BodyMapError):
        bm.load_body_map_assets(tmp_path)


def test_manifest_com_parametros_fora_da_faixa_e_erro_tipado(tmp_path, manifest):
    import shutil

    shutil.copytree(ASSETS, tmp_path / "v1")
    ruim = dict(manifest, compositor={**manifest["compositor"], "K0": 0.6, "K1": 0.7})
    (tmp_path / "v1" / "manifest.json").write_text(json.dumps(ruim), encoding="utf-8")
    with pytest.raises(bm.BodyMapError):
        bm.load_body_map_assets(tmp_path / "v1")


# ---------------------------------------------------------------------------
# Formula
# ---------------------------------------------------------------------------


def test_lut_da_tinta_segue_a_formula_do_prototipo():
    lut = bm._tint_luts("#FF9500", 4, 181, 0.3, 0.7)
    red, green, blue = lut[:256], lut[256:512], lut[512:]
    assert len(lut) == 768 and set(blue) == {0}
    # n = 0 abaixo de lum_lo: cor * K0; n = 1 acima de lum_hi: cor * (K0 + K1) = cor.
    assert red[0] == red[4] == round(255 * 0.3) and green[0] == round(149 * 0.3)
    assert red[181] == red[255] == 255 and green[181] == 149
    meio = round(4 + (181 - 4) / 2)
    n = (meio - 4) / (181 - 4)
    assert red[meio] == round(255 * (0.3 + 0.7 * n))
    assert list(red) == sorted(red)  # monotona em L: o sombreado da arte e preservado


@pytest.mark.parametrize("cor", ["FF9500", "#FF95", "#GG9500", "", None])
def test_cor_invalida_e_recusada(cor):
    with pytest.raises(ValueError):
        bm.RegionStyle(cor)


def test_strength_fica_entre_0_e_1():
    assert bm.RegionStyle(strength=2).strength == 1.0
    assert bm.RegionStyle(strength=-1).strength == 0.0


# ---------------------------------------------------------------------------
# Resolucao de slugs
# ---------------------------------------------------------------------------


def test_resolve_alias_ignora_desconhecido_e_mantem_o_mais_forte(assets):
    fraco = bm.SECONDARY_STYLE
    forte = bm.PRIMARY_STYLE
    got = bm.resolve_styles(
        {"abductors": forte, "adductor": fraco, "knees": forte, "head": forte, "nao-existe": forte, "chest": None,
         "abs": bm.RegionStyle(strength=0)},
        assets,
    )
    assert got == {"adductor": forte}
    assert bm.canonical_slug("abductors", assets) == "adductor"
    assert bm.canonical_slug("knees", assets) is None


def test_styles_for_roles_principal_vence():
    styles = bm.styles_for_roles(["chest", "triceps"], ["triceps", "front-deltoids", ""])
    assert styles == {"chest": bm.PRIMARY_STYLE, "triceps": bm.PRIMARY_STYLE, "front-deltoids": bm.SECONDARY_STYLE}
    assert bm.PRIMARY_STYLE.glow and not bm.SECONDARY_STYLE.glow
    assert bm.PRIMARY_STYLE.color == bm.SECONDARY_STYLE.color == "#FF9500"
    assert bm.SECONDARY_STYLE.strength < bm.PRIMARY_STYLE.strength


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_render_deterministico_e_independe_da_ordem_do_dicionario(assets):
    a = {"chest": bm.PRIMARY_STYLE, "triceps": bm.SECONDARY_STYLE, "front-deltoids": bm.SECONDARY_STYLE}
    b = dict(reversed(list(a.items())))
    primeiro = _png(bm.render_body_map(a, width=420))
    assert primeiro == _png(bm.render_body_map(a, width=420))
    assert primeiro == _png(bm.render_body_map(b, width=420))


def test_sem_destaque_e_a_arte_escurecida(assets):
    vazio = bm.render_body_map({}, crop_views=False)
    assert vazio.mode == "RGBA" and vazio.size == assets.canvas
    assert ImageChops.difference(vazio.convert("RGB"), assets.dimmed).getbbox() is None
    assert ImageChops.difference(vazio.getchannel("A"), assets.alpha).getbbox() is None
    assert vazio.getpixel((0, 0))[3] == 0  # fundo transparente


@pytest.mark.parametrize("slug", ["chest", "quadriceps", "upper-back", "calves"])
def test_cor_so_dentro_da_mascara_do_slug(assets, slug):
    """Sem brilho: so muda o que esta na mascara (+ a borda suave: o desfoque
    de raio 1 do Pillow alcanca 3 px)."""
    vazio = bm.render_body_map({}, crop_views=False)
    pintado = bm.render_body_map({slug: SEM_BRILHO}, crop_views=False)
    mudou = _diff_mask(vazio, pintado)
    assert mudou.getbbox() is not None
    vizinhanca = assets.masks[slug].filter(ImageFilter.MaxFilter(9))  # mascara + 4 px
    assert ImageChops.multiply(mudou, ImageChops.invert(vizinhanca)).getbbox() is None
    # e o alfa nao muda (sem brilho nada vaza do corpo)
    assert ImageChops.difference(pintado.getchannel("A"), assets.alpha).getbbox() is None


def test_brilho_do_principal_fica_perto_da_regiao(assets):
    vazio = bm.render_body_map({}, crop_views=False)
    pintado = bm.render_body_map({"chest": bm.PRIMARY_STYLE}, crop_views=False)
    mudou = _diff_mask(vazio, pintado)
    left, top, right, bottom = assets.masks["chest"].getbbox()
    margem = int(3 * bm.GLOW_RADIUS) + 4  # alem de 3 sigmas o desfoque do Pillow e zero
    got = mudou.getbbox()
    assert got[0] >= left - margem and got[1] >= top - margem and got[2] <= right + margem and got[3] <= bottom + margem
    # o brilho passa da mascara e vaza um pouco do corpo (alfa sobe fora da silhueta)
    vizinhanca = assets.masks["chest"].filter(ImageFilter.MaxFilter(5))
    assert ImageChops.multiply(mudou, ImageChops.invert(vizinhanca)).getbbox() is not None
    assert ImageChops.subtract(pintado.getchannel("A"), assets.alpha).getbbox() is not None


def test_principal_mais_forte_que_secundario(assets):
    regiao = assets.masks["chest"].filter(ImageFilter.MinFilter(9))  # miolo, longe da borda suave
    nada = _mean_in(bm.render_body_map({}, crop_views=False), regiao)
    sec = _mean_in(bm.render_body_map({"chest": bm.SECONDARY_STYLE}, crop_views=False), regiao)
    pri = _mean_in(bm.render_body_map({"chest": bm.PRIMARY_STYLE}, crop_views=False), regiao)

    def laranja(rgb):
        return rgb[0] - rgb[2]  # vermelho - azul: 0 no grafite, alto no #FF9500

    assert abs(laranja(nada)) < 8
    assert laranja(nada) + 20 < laranja(sec) < laranja(pri) - 20
    assert pri[0] > sec[0] > nada[0]


def test_alias_desenha_igual_ao_canonico():
    assert _png(bm.render_body_map({"abductors": bm.PRIMARY_STYLE})) == _png(
        bm.render_body_map({"adductor": bm.PRIMARY_STYLE})
    )


def test_slug_desconhecido_ou_sem_regiao_e_ignorado():
    vazio = _png(bm.render_body_map({}, width=300))
    assert _png(bm.render_body_map({"knees": bm.PRIMARY_STYLE, "head": bm.PRIMARY_STYLE, "xyz": bm.PRIMARY_STYLE},
                                   width=300)) == vazio


def test_vistas_recortadas_pelas_caixas_do_manifest(assets):
    fx0, fy0, fx1, fy1 = assets.views["front"]
    bx0, by0, bx1, by1 = assets.views["back"]
    assert bm.render_body_map({}, views=("front",)).size == (fx1 - fx0, fy1 - fy0)
    assert bm.render_body_map({}, views=("back",)).size == (bx1 - bx0, by1 - by0)
    ambas = bm.render_body_map({})
    assert ambas.size == bm.native_size() == ((fx1 - fx0) + bm.VIEW_GAP + (bx1 - bx0), max(fy1 - fy0, by1 - by0))
    assert bm.render_body_map({}, gap=0).width == ambas.width - bm.VIEW_GAP
    # Recortar tira o vao grande entre as figuras: bem mais estreito que o canvas.
    assert ambas.width < 0.7 * assets.canvas[0]
    # As bordas da composicao recortada sao transparentes (nada cortado ao meio).
    alpha = ambas.getchannel("A")
    for faixa in ((0, 0, 1, ambas.height), (ambas.width - 1, 0, ambas.width, ambas.height),
                  (0, 0, ambas.width, 1), (0, ambas.height - 1, ambas.width, ambas.height)):
        assert alpha.crop(faixa).getextrema()[1] == 0


def test_sem_recorte_usa_o_canvas_original(assets):
    assert bm.render_body_map({}, crop_views=False).size == assets.canvas
    assert bm.render_body_map({}, crop_views=False, views=("back",)).size == (assets.canvas[0] // 2, assets.canvas[1])


def test_largura_pedida_e_altura_proporcional():
    w, h = bm.native_size()
    img = bm.render_body_map({"chest": bm.PRIMARY_STYLE}, width=420)
    assert img.width == 420 and abs(img.height - h * 420 / w) <= 0.5


def test_fit_cabe_na_caixa_mantendo_a_proporcao():
    w, h = bm.native_size()
    for caixa in ((244, 228), (600, 100), (90, 400)):
        img = bm.fit_body_map({"chest": bm.PRIMARY_STYLE}, *caixa)
        assert img.width <= caixa[0] and img.height <= caixa[1]
        assert img.width == caixa[0] or img.height >= caixa[1] - 1
        assert abs(img.width / img.height - w / h) < 0.03


@pytest.mark.parametrize("views", [(), ("lado",), ("front", "frente")])
def test_vista_invalida_e_recusada(views):
    with pytest.raises(ValueError):
        bm.render_body_map({}, views=views)
