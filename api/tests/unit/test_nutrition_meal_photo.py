"""Motor da foto do prato (`domain/nutrition/meal_photo`, L10): upload pelas regras de
`label_photo` com códigos próprios, chave e retenção com paridade no Terraform, revisão
do que a IA viu (sem comida = nada de item; gramas no teto do diário) e a natureza do
item: casado no catálogo é DERIVED pelo `meal_math`, sem casamento é ESTIMATED."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from app.domain.nutrition import diary, food_text, label_photo, meal_math, meal_parse, meal_photo as mp
from app.domain.nutrition import labels as lb
from app.domain.nutrition.meal_math import MealNature, Unit
from app.domain.nutrition.meal_parse import MatchStatus
from app.domain.nutrition.meal_photo import PlateStatus

INFRA = Path(__file__).resolve().parents[3] / "infra"
FOTO = "0123456789abcdef0123456789abcdef"
ESTIMATIVA = {"energy_kcal": 150, "protein_g": 10, "carbohydrates_g": 20, "total_fat_g": 3, "fiber_g": 1}


def _item(**extra):
    return {
        "name": "arroz branco",
        "quantity": None,
        "unit": "g",
        "grams_estimate": 120,
        "preparation": "cozido",
        "confidence": 0.8,
        meal_parse.ESTIMATE_KEY: ESTIMATIVA,
        **extra,
    }


# ---------------------------------------------------------------------------
# Upload, chave e paridades
# ---------------------------------------------------------------------------


def test_limites_sao_os_do_transporte_do_rotulo():
    assert mp.MEAL_PHOTO_MAX_BYTES == label_photo.LABEL_PHOTO_MAX_BYTES
    assert mp.MEAL_PHOTO_CONTENT_TYPE == label_photo.LABEL_PHOTO_CONTENT_TYPE
    assert mp.MEAL_IMAGE_DETAIL == label_photo.LABEL_IMAGE_DETAIL


@pytest.mark.parametrize(
    ("tamanho", "tipo", "codigo"),
    [
        (0, mp.MEAL_PHOTO_CONTENT_TYPE, mp.ERR_MEAL_PHOTO_EMPTY),
        (mp.MEAL_PHOTO_MAX_BYTES + 1, mp.MEAL_PHOTO_CONTENT_TYPE, mp.ERR_MEAL_PHOTO_TOO_LARGE),
        (10, "image/png", mp.ERR_MEAL_PHOTO_UNSUPPORTED_TYPE),
        (10, None, mp.ERR_MEAL_PHOTO_UNSUPPORTED_TYPE),
        (mp.MEAL_PHOTO_MAX_BYTES, mp.MEAL_PHOTO_CONTENT_TYPE, None),
    ],
)
def test_upload_error_fala_com_codigo_da_foto_do_prato(tamanho, tipo, codigo):
    assert mp.upload_error(tamanho, tipo) == codigo


def test_decode_upload_traduz_o_codigo_e_nao_fala_de_rotulo():
    assert mp.decode_upload("") == (None, mp.ERR_MEAL_PHOTO_EMPTY)
    assert mp.decode_upload("não é base64!") == (None, mp.ERR_MEAL_PHOTO_INVALID_ENCODING)
    assert mp.decode_upload("A" * (label_photo.LABEL_PHOTO_MAX_BASE64_CHARS + 1)) == (None, mp.ERR_MEAL_PHOTO_TOO_LARGE)
    assert mp.decode_upload(base64.b64encode(b"abc").decode("ascii")) == (b"abc", None)
    for codigo in (mp.ERR_MEAL_PHOTO_TOO_LARGE, mp.ERR_MEAL_PHOTO_NOT_FOUND, mp.LIMIT_PHOTO_NO_FOOD, mp.LIMIT_PHOTO_UNCLEAR):
        texto = lb.limitation_label(codigo)
        assert "rótulo" not in texto and "tabela" not in texto and texto != codigo


def test_chave_tem_so_ids_internos_e_confere_o_dono():
    chave = mp.meal_photo_key(7, FOTO)
    assert chave == f"{mp.MEAL_PHOTO_S3_PREFIX}/7/{FOTO}.jpg"
    assert mp.is_meal_photo_key_of(chave, 7)
    assert not mp.is_meal_photo_key_of(chave, 8)
    assert not mp.is_meal_photo_key_of(label_photo.label_photo_key(7, FOTO), 7)  # outro prefixo
    assert not mp.is_meal_photo_key_of(f"{mp.MEAL_PHOTO_S3_PREFIX}/7/../8/{FOTO}.jpg", 7)
    assert not mp.is_meal_photo_key_of(None, 7)
    with pytest.raises(ValueError):
        mp.meal_photo_key(7, "../etc")
    with pytest.raises(ValueError):
        mp.meal_photo_key("x", FOTO)


def test_rotulo_continua_igual_depois_de_generalizar_a_chave():
    assert label_photo.label_photo_key(7, FOTO) == f"{label_photo.LABEL_PHOTO_S3_PREFIX}/7/{FOTO}.jpg"
    assert label_photo.is_label_photo_key_of(label_photo.label_photo_key(7, FOTO), 7)
    assert not label_photo.is_label_photo_key_of(mp.meal_photo_key(7, FOTO), 7)


def _regra_lifecycle(nome: str) -> str:
    texto = (INFRA / "s3_uploads.tf").read_text(encoding="utf-8")
    inicio = texto.index(f'id     = "{nome}"')
    return texto[inicio : texto.index("\n  }\n", inicio)]


def test_retencao_e_prefixo_sao_espelho_do_lifecycle_do_bucket():
    regra = _regra_lifecycle("nutrition-meals-expire-30d")
    assert f'prefix = "{mp.MEAL_PHOTO_S3_PREFIX}/"' in regra
    assert re.search(r"expiration \{\s*days = (\d+)", regra).group(1) == str(mp.MEAL_PHOTO_RETENTION_DAYS)
    assert 'status = "Enabled"' in regra


def test_worker_le_so_o_prefixo_do_dono():
    texto = (INFRA / "lambda_ai_generators.tf").read_text(encoding="utf-8")
    inicio = texto.index('resource "aws_iam_role_policy" "musclefit_ai_generators_nutrition_meals"')
    politica = texto[inicio : texto.index("\n}\n", inicio)]
    assert 'Action   = ["s3:GetObject"]' in politica
    assert f'${{aws_s3_bucket.musclefit_uploads.arn}}/{mp.MEAL_PHOTO_S3_PREFIX}/*"' in politica
    assert "PutObject" not in politica and "DeleteObject" not in politica and "ListBucket" not in politica


# ---------------------------------------------------------------------------
# Revisão do que a IA viu
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "limitacao"),
    [(PlateStatus.NO_FOOD, mp.LIMIT_PHOTO_NO_FOOD), (PlateStatus.UNCLEAR, mp.LIMIT_PHOTO_UNCLEAR)],
)
def test_foto_sem_comida_descarta_item_que_veio_junto(status, limitacao):
    revisao = mp.review_reading({"plate_status": status.value, "items": [_item()]})
    assert revisao.items == () and revisao.limitations == (limitacao,) and revisao.status is status


@pytest.mark.parametrize("bruto", ["desconhecido", None, 3])
def test_status_desconhecido_nao_aproveita_item(bruto):
    revisao = mp.review_reading({"plate_status": bruto, "items": [_item()]})
    assert revisao.items == () and revisao.limitations == (mp.LIMIT_PHOTO_UNCLEAR,)


def test_reconhecido_sem_item_valido_vira_nao_identificado():
    revisao = mp.review_reading({"plate_status": PlateStatus.RECOGNIZED.value, "items": [{"name": "  "}]})
    assert revisao.items == () and revisao.status is PlateStatus.UNCLEAR
    assert revisao.limitations == (mp.LIMIT_PHOTO_UNCLEAR,)


def test_reconhecido_passa_pela_limpeza_do_dono_e_corta_no_teto_do_diario():
    itens = [_item(name=f"alimento {n}") for n in range(diary.MEAL_MAX_ITEMS + 2)]
    revisao = mp.review_reading({"plate_status": PlateStatus.RECOGNIZED.value, "items": itens})
    assert len(revisao.items) == diary.MEAL_MAX_ITEMS
    assert revisao.limitations == (meal_parse.LIMIT_ITEMS_TRUNCATED,)
    assert revisao.items[0].grams_estimate == 120 and revisao.items[0].estimate is not None


def test_gramas_acima_do_teto_do_motor_nao_viram_proposta():
    revisao = mp.review_reading(
        {"plate_status": "recognized", "items": [_item(grams_estimate=diary.ITEM_AMOUNT_MAX + 1, quantity=2, unit="unit")]}
    )
    [item] = revisao.items
    assert item.name == "arroz branco" and item.grams_estimate is None and item.quantity is None
    assert mp.LIMIT_GRAMS_OUT_OF_RANGE in item.limitations
    decidido = meal_parse.classify_item(0, item, ())
    assert decidido.quantity is None and meal_parse.LIMIT_QUANTITY_MISSING in decidido.limitations
    no_teto = mp.review_reading({"plate_status": "recognized", "items": [_item(grams_estimate=diary.ITEM_AMOUNT_MAX)]})
    assert no_teto.items[0].grams_estimate == diary.ITEM_AMOUNT_MAX


# ---------------------------------------------------------------------------
# DERIVED x ESTIMATED: quem calcula é o motor
# ---------------------------------------------------------------------------


def _candidato(food_id: int, nome: str, consulta: str = "arroz branco"):
    return food_text.rank_candidates(consulta, [(food_id, nome)])


def test_casado_no_catalogo_e_derived_pelo_meal_math():
    [item] = mp.review_reading({"plate_status": "recognized", "items": [_item()]}).items
    decidido = meal_parse.classify_item(0, item, _candidato(9, "Arroz branco"))
    assert decidido.status is MatchStatus.SELECTED and decidido.food_id == 9
    assert mp.item_nature(decidido) is MealNature.DERIVED
    assert (decidido.quantity, decidido.unit) == (120, Unit.GRAM)
    # Os valores são os do catálogo, nunca a estimativa da IA.
    catalogo = meal_math.nutrients_from_values(
        {"energy_kcal": 128, "protein_g": 2.5, "carbohydrates_g": 28, "total_fat_g": 0.2, "fiber_g": 1.6}
    )
    nutricao = meal_math.item_nutrition(catalogo, meal_math.resolve_amount(decidido.quantity, decidido.unit, "g"))
    assert nutricao.nature is MealNature.DERIVED
    assert nutricao.nutrients.get("energy_kcal") == pytest.approx(128 * 1.2)


def test_sem_casamento_e_estimated_destacado_com_a_estimativa_da_ia():
    [item] = mp.review_reading({"plate_status": "recognized", "items": [_item(name="moqueca capixaba")]}).items
    decidido = meal_parse.classify_item(0, item, ())
    assert decidido.status is MatchStatus.ESTIMATED and decidido.food_id is None
    assert mp.item_nature(decidido) is MealNature.ESTIMATED
    assert decidido.to_dict()["uses_estimate"] is True and meal_parse.LIMIT_AI_ESTIMATE in decidido.limitations
    nutricao = meal_parse.estimated_item_nutrition(item.estimate, meal_math.resolve_amount(decidido.quantity, decidido.unit, "g"))
    assert nutricao.nature is MealNature.ESTIMATED
    assert nutricao.nutrients.get("energy_kcal") == pytest.approx(150 * 1.2)
    assert meal_parse.LIMIT_AI_ESTIMATE in nutricao.limitations


def test_escolha_pendente_nao_tem_natureza():
    [item] = mp.review_reading({"plate_status": "recognized", "items": [_item()]}).items
    candidatos = food_text.rank_candidates("arroz branco", [(1, "Arroz branco cozido"), (2, "Arroz branco cru")])
    decidido = meal_parse.classify_item(0, item, candidatos)
    assert decidido.status is MatchStatus.CHOICE and mp.item_nature(decidido) is None
    [sem_estimativa] = mp.review_reading(
        {"plate_status": "recognized", "items": [_item(**{meal_parse.ESTIMATE_KEY: None})]}
    ).items
    nao_achado = meal_parse.classify_item(0, sem_estimativa, ())
    assert nao_achado.status is MatchStatus.UNMATCHED and mp.item_nature(nao_achado) is None


def test_origem_da_refeicao_gravada():
    assert meal_parse.meal_source(None, diary.MealSource.AI_PHOTO.value) is diary.MealSource.MANUAL
    assert meal_parse.meal_source("job-1") is diary.MealSource.AI_TEXT
    assert meal_parse.meal_source("job-1", "outra") is diary.MealSource.AI_TEXT
    assert meal_parse.meal_source("job-1", diary.MealSource.AI_PHOTO.value) is diary.MealSource.AI_PHOTO
