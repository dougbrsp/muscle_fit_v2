"""Foto do rótulo nutricional (dono `app/domain/nutrition/label_photo.py`, L6):
upload (tamanho, tipo), chave S3 do usuário, retenção (paridade com o
lifecycle do `infra/s3_uploads.tf`) e a revisão determinística da leitura da
IA (base, kJ -> kcal, por 100 derivado da porção, regras do catálogo com
Atwater, porção × por 100, código de barras, foto ilegível)."""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path

import pytest

from app.domain import nutrition
from app.domain.nutrition import body_composition, catalog
from app.domain.nutrition import label_photo as lp
from app.domain.nutrition import labels as lb
from app.domain.nutrition import meal_math
from app.domain.nutrition.label_photo import LabelLegibility

INFRA = Path(__file__).resolve().parents[3] / "infra"

UUID = "0123456789abcdef0123456789abcdef"

# Biscoito: 100 g = 450 kcal; porção de 30 g = 135 kcal (valores coerentes por Atwater).
POR_100 = {
    "energy_kcal": 450, "energy_kj": 1884, "carbohydrates_g": 65, "total_sugars_g": 20, "added_sugars_g": 15,
    "protein_g": 8, "total_fat_g": 18, "saturated_fat_g": 6, "trans_fat_g": 0, "fiber_g": 4, "sodium_mg": 300,
}
POR_PORCAO = {chave: round(valor * 0.3, 2) for chave, valor in POR_100.items()}


def _leitura(**extra):
    base = {
        "product_name": "Biscoito de aveia", "brand": "Marca X", "barcode": "7891000100103", "basis": "g",
        "serving_size": 30, "household_measure": "3 unidades", "servings_per_package": 6,
        "per_100": dict(POR_100), "per_serving": dict(POR_PORCAO), "legibility": "ok", "warnings": [],
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Upload, chave e retenção
# ---------------------------------------------------------------------------


def test_limites_do_upload_vem_do_dono():
    assert lp.LABEL_PHOTO_MAX_BYTES == int(lp.LABEL_PHOTO_MAX_MIB * lp.BYTES_PER_MIB)
    assert lp.LABEL_PHOTO_ACCEPTED_CONTENT_TYPES == {lp.LABEL_PHOTO_CONTENT_TYPE} == {"image/jpeg"}
    assert lp.RESIZE_JPEG_QUALITY == lp.RESIZE_JPEG_QUALITY_PERCENT / body_composition.PERCENT
    assert 0 < lp.RESIZE_JPEG_QUALITY <= 1 and lp.RESIZE_MAX_EDGE_PX > 0
    assert lp.LABEL_PHOTO_IMAGES_PER_READING == 1


@pytest.mark.parametrize(
    ("tamanho", "tipo", "esperado"),
    [
        (0, "image/jpeg", lp.ERR_LABEL_PHOTO_EMPTY),
        (lp.LABEL_PHOTO_MAX_BYTES + 1, "image/jpeg", lp.ERR_LABEL_PHOTO_TOO_LARGE),
        (lp.LABEL_PHOTO_MAX_BYTES, "image/jpeg", None),
        (1000, "image/png", lp.ERR_LABEL_PHOTO_UNSUPPORTED_TYPE),
        (1000, None, lp.ERR_LABEL_PHOTO_UNSUPPORTED_TYPE),
    ],
)
def test_erro_do_upload(tamanho, tipo, esperado):
    assert lp.upload_error(tamanho, tipo) == esperado


def test_corpo_em_base64_sem_padding_vira_os_bytes():
    dados = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(range(256))
    texto = base64.b64encode(dados).decode("ascii")
    assert lp.decode_upload(texto.rstrip("=")) == (dados, None)
    assert lp.decode_upload(texto) == (dados, None)  # com padding também
    # O alfabeto sem padding não tem os caracteres das assinaturas do CRS do WAF.
    assert not set(texto.rstrip("=")) & set("<>:.=\"'&%")


@pytest.mark.parametrize(
    ("texto", "codigo"),
    [
        ("", lp.ERR_LABEL_PHOTO_EMPTY),
        (None, lp.ERR_LABEL_PHOTO_EMPTY),
        ("abc<script>", lp.ERR_LABEL_PHOTO_INVALID_ENCODING),
        ("abcd===", lp.ERR_LABEL_PHOTO_INVALID_ENCODING),
        ("abcde", lp.ERR_LABEL_PHOTO_INVALID_ENCODING),  # resto 1: nenhum byte termina assim
        ("ab cd", lp.ERR_LABEL_PHOTO_INVALID_ENCODING),
        ("A" * (lp.LABEL_PHOTO_MAX_BASE64_CHARS + 1), lp.ERR_LABEL_PHOTO_TOO_LARGE),
    ],
    ids=["vazio", "none", "html", "padding_demais", "resto_1", "espaco", "grande"],
)
def test_corpo_invalido_tem_o_codigo_do_motor(texto, codigo):
    assert lp.decode_upload(texto) == (None, codigo)


def test_limite_do_texto_e_o_tamanho_do_limite_em_base64():
    no_limite = base64.b64encode(bytes([0xFF]) * lp.LABEL_PHOTO_MAX_BYTES).decode("ascii").rstrip("=")
    assert len(no_limite) <= lp.LABEL_PHOTO_MAX_BASE64_CHARS
    dados, erro = lp.decode_upload(no_limite)
    assert erro is None and len(dados) == lp.LABEL_PHOTO_MAX_BYTES


def test_chave_so_com_ids_internos_e_do_usuario():
    chave = lp.label_photo_key(7, UUID)
    assert chave == f"{lp.LABEL_PHOTO_S3_PREFIX}/7/{UUID}{lp.LABEL_PHOTO_EXTENSION}"
    assert lp.is_label_photo_key_of(chave, 7)
    assert lp.is_label_photo_key_of(chave, "7")
    assert not lp.is_label_photo_key_of(chave, 8)
    nova = lp.label_photo_key(7, uuid.uuid4().hex)
    assert lp.is_label_photo_key_of(nova, 7)


@pytest.mark.parametrize(
    "chave",
    [
        None, 7, "", f"outra/7/{UUID}.jpg", f"{lp.LABEL_PHOTO_S3_PREFIX}/7/{UUID}.png",
        f"{lp.LABEL_PHOTO_S3_PREFIX}/7/../8/{UUID}.jpg", f"{lp.LABEL_PHOTO_S3_PREFIX}/7/nome-livre.jpg",
        f"{lp.LABEL_PHOTO_S3_PREFIX}/07/{UUID}.jpg", f"{lp.LABEL_PHOTO_S3_PREFIX}/7/{UUID}.jpg/x",
    ],
)
def test_chave_fora_da_forma_nao_e_do_usuario(chave):
    assert not lp.is_label_photo_key_of(chave, 7)


@pytest.mark.parametrize(("usuario", "foto"), [(0, UUID), (True, UUID), ("x", UUID), (7, "nao-hex"), (7, UUID.upper())])
def test_chave_recusa_id_invalido(usuario, foto):
    with pytest.raises(ValueError):
        lp.label_photo_key(usuario, foto)


def _regra_lifecycle(nome: str) -> str:
    texto = (INFRA / "s3_uploads.tf").read_text(encoding="utf-8")
    inicio = texto.index(f'id     = "{nome}"')
    return texto[inicio : texto.index("\n  }\n", inicio)]


def test_retencao_e_espelho_do_lifecycle_do_bucket():
    regra = _regra_lifecycle("nutrition-labels-expire-30d")
    assert f'prefix = "{lp.LABEL_PHOTO_S3_PREFIX}/"' in regra
    assert re.search(r"expiration \{\s*days = (\d+)", regra).group(1) == str(lp.LABEL_PHOTO_RETENTION_DAYS)
    assert 'status = "Enabled"' in regra


def test_worker_le_so_o_prefixo_do_dono():
    texto = (INFRA / "lambda_ai_generators.tf").read_text(encoding="utf-8")
    bloco = texto[texto.index('resource "aws_iam_role_policy" "musclefit_ai_generators_nutrition_labels"'):]
    bloco = bloco[: bloco.index("\n}\n")]
    assert 'Action   = ["s3:GetObject"]' in bloco
    assert f'/{lp.LABEL_PHOTO_S3_PREFIX}/*"' in bloco
    assert "musclefit_ai_generators_exec" in bloco


# ---------------------------------------------------------------------------
# Revisão da leitura
# ---------------------------------------------------------------------------


def test_leitura_completa_vira_o_formulario_do_catalogo():
    revisao = lp.review_reading(_leitura())
    assert revisao.legibility == LabelLegibility.OK and revisao.readable
    assert revisao.errors == () and revisao.warnings == ()
    alimento = revisao.food_prefill()
    assert set(alimento) == {"name", "brand", "category", "basis", *catalog.PER_100_FIELDS, *catalog.SERVING_FIELDS}
    assert alimento["name"] == "Biscoito de aveia" and alimento["basis"] == "g"
    assert alimento["energy_kcal"] == 450 and alimento["sodium_mg"] == 300
    assert alimento["serving_size"] == 30 and alimento["servings_per_package"] == 6
    assert "energy_kj" not in alimento  # o catálogo guarda só kcal
    assert revisao.barcode == "7891000100103"
    assert revisao.atwater is not None and revisao.atwater.consistent
    assert revisao.label_check is not None and revisao.label_check.consistent
    corpo = revisao.to_dict()
    assert corpo["readable"] is True and corpo["food"] == alimento


def test_ilegivel_nao_pre_preenche_nada():
    revisao = lp.review_reading(_leitura(legibility="unreadable"))
    assert not revisao.readable
    assert revisao.errors == (lp.ERR_LABEL_UNREADABLE,)
    assert all(valor is None for campo, valor in revisao.food_prefill().items() if campo != "basis")


def test_parcial_e_codigo_desconhecido_pedem_conferencia():
    assert lp.WARN_LABEL_PARTIAL in lp.review_reading(_leitura(legibility="partial")).warnings
    assert lp.normalize_legibility("talvez") == LabelLegibility.PARTIAL


def test_sem_base_lida_usa_a_padrao_com_aviso():
    revisao = lp.review_reading(_leitura(basis=None))
    assert revisao.basis == catalog.DEFAULT_FOOD_BASIS
    assert lp.WARN_LABEL_BASIS_ASSUMED in revisao.warnings
    assert lp.review_reading(_leitura(basis="ml")).basis == meal_math.Unit.MILLILITER


def test_energia_so_em_kj_vira_kcal_pelo_dono():
    por_100 = {**POR_100, "energy_kcal": None}
    por_porcao = {**POR_PORCAO, "energy_kcal": None}
    revisao = lp.review_reading(_leitura(per_100=por_100, per_serving=por_porcao))
    assert revisao.per_100["energy_kcal"] == round(meal_math.kj_to_kcal(1884), lp.PREFILL_DECIMALS)
    assert revisao.warnings.count(lp.WARN_LABEL_ENERGY_FROM_KJ) == 1


def test_coluna_por_100_ausente_sai_da_porcao_com_aviso_por_campo():
    """Rótulo antigo (só por porção): por 100 = por porção × 100 / porção."""
    revisao = lp.review_reading(_leitura(per_100={}, basis="g"))
    assert revisao.per_100["energy_kcal"] == 450 and revisao.per_100["protein_g"] == 8
    derivados = [a for a in revisao.warnings if a.startswith(lp.WARN_LABEL_DERIVED_FROM_SERVING)]
    assert len(derivados) == len(catalog.PER_100_FIELDS)
    assert revisao.errors == ()


def test_sem_quantidade_significativa_vira_zero_do_motor_com_aviso():
    """A IA só lista o campo em `not_significant` (valor null); o zero é do motor,
    com aviso, e valor impresso na tabela não é sobrescrito."""
    por_100 = {**POR_100, "added_sugars_g": None, "trans_fat_g": None, "sodium_mg": 300}
    por_porcao = {**POR_PORCAO, "added_sugars_g": None, "trans_fat_g": None}
    revisao = lp.review_reading(_leitura(per_100=por_100, per_serving=por_porcao,
                                         not_significant=["added_sugars_g", "trans_fat_g", "sodium_mg", "x"]))
    assert revisao.per_100["added_sugars_g"] == 0 and revisao.per_100["trans_fat_g"] == 0
    assert revisao.per_100["sodium_mg"] == 300  # impresso: fica
    avisos = [a for a in revisao.warnings if a.startswith(lp.WARN_LABEL_NOT_SIGNIFICANT)]
    assert avisos == [f"{lp.WARN_LABEL_NOT_SIGNIFICANT}:added_sugars_g", f"{lp.WARN_LABEL_NOT_SIGNIFICANT}:trans_fat_g"]
    assert revisao.per_serving["trans_fat_g"] == 0
    assert not [a for a in revisao.warnings if a.startswith(lp.WARN_LABEL_DERIVED_FROM_SERVING)]


def test_observacoes_da_ia_limitadas_pelo_dono():
    notas = lp.clean_ai_notes(["  a  b ", "a b", "x" * (lp.AI_NOTE_MAX_CHARS + 50)] + [f"n{i}" for i in range(9)])
    assert notas[0] == "a b" and len(notas[1]) == lp.AI_NOTE_MAX_CHARS
    assert len(notas) == lp.AI_NOTES_MAX
    assert lp.clean_ai_notes(None) == () and lp.clean_ai_notes("texto solto") == ()


def test_por_100_a_partir_da_porcao_e_do_meal_math():
    assert meal_math.per_reference_from_serving(135, 30) == pytest.approx(450)
    assert meal_math.per_reference_from_serving(None, 30) is None
    assert meal_math.per_reference_from_serving(10, 0) is None
    assert meal_math.per_reference_from_serving(-1, 30) is None


def test_porcao_inconsistente_avisa_o_campo_pela_tolerancia_do_dono():
    fora = 1 + meal_math.LABEL_SERVING_TOLERANCE_SHARE * 2
    revisao = lp.review_reading(_leitura(per_serving={**POR_PORCAO, "protein_g": POR_PORCAO["protein_g"] * fora}))
    assert f"{lp.WARN_LABEL_SERVING_INCONSISTENT}:protein_g" in revisao.warnings
    dentro = 1 + meal_math.LABEL_SERVING_TOLERANCE_SHARE / 2
    ok = lp.review_reading(_leitura(per_serving={**POR_PORCAO, "protein_g": POR_PORCAO["protein_g"] * dentro}))
    assert not [a for a in ok.warnings if a.startswith(lp.WARN_LABEL_SERVING_INCONSISTENT)]


def test_regras_do_catalogo_viram_erros_e_avisos_da_leitura():
    revisao = lp.review_reading(
        _leitura(per_100={**POR_100, "energy_kcal": 900, "protein_g": -1, "total_sugars_g": 80}, product_name=None)
    )
    assert f"{catalog.ERR_INVALID_VALUE}:protein_g" in revisao.errors
    assert revisao.per_100["protein_g"] is None
    assert f"{catalog.WARN_SUBTOTAL_ABOVE_TOTAL}:total_sugars_g" in revisao.warnings
    assert lp.WARN_LABEL_NAME_MISSING in revisao.warnings


def test_atwater_fora_da_tolerancia_e_aviso_nao_erro():
    revisao = lp.review_reading(_leitura(per_100={**POR_100, "energy_kcal": 700}, per_serving={}))
    assert catalog.WARN_ATWATER in revisao.warnings and revisao.errors == ()


def test_codigo_de_barras_invalido_some():
    assert lp.review_reading(_leitura(barcode="123")).barcode is None
    assert lp.review_reading(_leitura(barcode="789 1000 100103")).barcode == "7891000100103"


# ---------------------------------------------------------------------------
# Textos e export
# ---------------------------------------------------------------------------


def test_todo_codigo_tem_texto_com_numero_do_dono():
    for nome, valor in vars(lp).items():
        if nome.startswith(("ERR_", "WARN_")) and isinstance(valor, str):
            assert lb.limitation_label(valor) != valor or valor in lb.PREFIXED_LIMITATION_LABELS, nome
    assert "1,5 MB" in lb.limitation_label(lp.ERR_LABEL_PHOTO_TOO_LARGE)
    assert str(lp.LABEL_PHOTO_RETENTION_DAYS) in lb.limitation_label(lp.ERR_LABEL_PHOTO_NOT_FOUND)
    texto = lb.limitation_label(f"{lp.WARN_LABEL_SERVING_INCONSISTENT}:protein_g")
    assert texto.startswith("Proteínas (g)") and f"{meal_math.LABEL_SERVING_TOLERANCE_SHARE:.0%}" in texto


def test_campos_de_nutriente_sao_os_da_rdc429_sem_os_da_porcao():
    rdc = [campo.code for campo in lb.RDC429_FIELDS if campo.code not in catalog.SERVING_FIELDS]
    assert list(lp.LABEL_NUTRIENT_FIELDS) == rdc


def test_export_para_a_tela():
    bloco = nutrition.export_for_frontend()
    assert bloco["label_photo"] == {
        "max_bytes": lp.LABEL_PHOTO_MAX_BYTES,
        "content_type": lp.LABEL_PHOTO_CONTENT_TYPE,
        "resize_max_edge_px": lp.RESIZE_MAX_EDGE_PX,
        "resize_quality": lp.RESIZE_JPEG_QUALITY,
        "retention_days": lp.LABEL_PHOTO_RETENTION_DAYS,
        "food_source": catalog.FoodSource.LABEL_PHOTO.value,
    }
    assert bloco["label_legibilities"] == [item.value for item in LabelLegibility]
    assert bloco["label_legibility_labels"] == lb.labels_by_code(lb.LABEL_LEGIBILITY_LABELS)


def test_cadastro_do_usuario_aceita_manual_e_foto_do_rotulo():
    assert catalog.user_entry_source(None) == catalog.FoodSource.USER
    assert catalog.user_entry_source("label_photo") == catalog.FoodSource.LABEL_PHOTO
    assert catalog.user_entry_source(" USER ") == catalog.FoodSource.USER
    for fonte in ("taco", "usda", "off", "ai_estimate", "tbca_manual", "nada"):
        assert catalog.user_entry_source(fonte) is None
