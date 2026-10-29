"""Item estimado pela IA no diário (`app/nutrition_meals.py`, L5b): gravação
com natureza ESTIMATED pela conta do motor (`meal_parse.estimated_item_nutrition`),
snapshot `ai_estimate` congelado (E4), refeição `ai_text` com `ai_job_id` e
`description_raw`, `match_confidence` do casamento, MIXED com item do catálogo,
reuso do snapshot na edição e os 400/404 da origem do item.

Banco em memória: `tests/support/nutrition_diary_fake.py`."""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_meals as meals_mod
from app.domain.nutrition import diary
from app.domain.nutrition import meal_math as mm
from app.domain.nutrition import meal_parse as mp
from app.security import Identity, get_current_identity
from tests.support.nutrition_diary_fake import FakeDiaryDB

DIA = date(2026, 10, 18)
ALUNO = 2
ARROZ = {"energy_kcal": 128.26, "carbohydrates_g": 28.06, "protein_g": 2.52, "total_fat_g": 0.23, "fiber_g": 1.56,
         "sodium_mg": 1.20}
WHEY = {"name": "whey protein", "energy_kcal": 400, "protein_g": 80, "carbohydrates_g": 10, "total_fat_g": 5,
        "fiber_g": None}


@pytest.fixture
def db(monkeypatch):
    banco = FakeDiaryDB()
    monkeypatch.setattr(meals_mod, "get_conn", banco.conn)
    monkeypatch.setattr(meals_mod, "require_diary_tables", lambda cur: None)
    monkeypatch.setattr(meals_mod, "require_food_tables", lambda cur: None)
    banco.arroz = banco.add_food("Arroz, tipo 1, cozido", **ARROZ)
    return banco


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(meals_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=ALUNO, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _refeicao(db, **extra) -> dict:
    corpo = {
        "local_date": DIA.isoformat(),
        "meal_slot": "post_workout",
        "items": [
            {"food_id": db.arroz, "quantity": 120, "unit": "g", "match_confidence": 1.0},
            {"estimate": dict(WHEY), "quantity": 30, "unit": "g"},
        ],
        "ai_job_id": "job-123",
        "description_raw": "  arroz e 30 g de whey ",
    }
    corpo.update(extra)
    return corpo


def test_grava_o_item_estimado_pela_conta_do_motor(db):
    resposta = _client().post("/nutrition/meals", json=_refeicao(db))
    assert resposta.status_code == 201, resposta.text
    refeicao = resposta.json()
    assert refeicao["source"] == diary.MealSource.AI_TEXT.value and refeicao["ai_job_id"] == "job-123"
    assert refeicao["description_raw"] == "arroz e 30 g de whey"
    arroz, whey = refeicao["items"]
    assert arroz["nature"] == "DERIVED" and arroz["match_confidence"] == 1.0
    esperado = mp.estimated_item_nutrition(mp.validate_estimate(WHEY).nutrients, mm.resolve_amount(30, "g", "g"))
    assert whey["food_id"] is None and whey["label"] == "whey protein" and whey["nature"] == "ESTIMATED"
    assert whey["food_source"] == "ai_estimate" and whey["citation"] is None
    for campo in mm.NUTRIENT_FIELDS:
        valor = esperado.nutrients.get(campo)
        assert whey["nutrients"][campo] == (None if valor is None else pytest.approx(valor, abs=0.01)), campo
    codigos = [lim["code"] for lim in whey["limitations"]]
    assert mp.LIMIT_AI_ESTIMATE in codigos and "nutrient_unknown:sodium_mg" in codigos
    assert refeicao["totals"]["nature"] == "MIXED" and "sodium_mg" in refeicao["totals"]["incomplete_fields"]
    gravado = [i for i in db.items.values() if i["food_id"] is None][0]
    snapshot = json.loads(gravado["catalog_snapshot_json"])
    assert snapshot["food"]["source"] == "ai_estimate" and snapshot["food"]["per_100"]["energy_kcal"] == 400


def test_refeicao_manual_segue_manual_e_sem_texto(db):
    corpo = _refeicao(db, ai_job_id=None)
    corpo["items"] = corpo["items"][:1]
    refeicao = _client().post("/nutrition/meals", json=corpo).json()
    assert refeicao["source"] == "manual" and refeicao["description_raw"] is None and refeicao["ai_job_id"] is None


@pytest.mark.parametrize(
    "item",
    [
        {"quantity": 30, "unit": "g"},  # nenhum dos dois
        {"food_id": 0, "estimate": dict(WHEY), "quantity": 30, "unit": "g"},  # os dois
    ],
)
def test_origem_do_item_e_exatamente_uma(db, item):
    if "food_id" in item:
        item["food_id"] = db.arroz
    resposta = _client().post("/nutrition/meals", json=_refeicao(db, items=[item]))
    assert resposta.status_code == 400
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == "invalid_meal"
    assert detalhe["items"] == [{"index": 0, "errors": [{"code": diary.ERR_ITEM_FOOD_OR_ESTIMATE,
                                                          "label": detalhe["items"][0]["errors"][0]["label"]}]}]
    assert db.meals == {}


def test_estimativa_invalida_responde_os_codigos_do_catalogo(db):
    resposta = _client().post(
        "/nutrition/meals", json=_refeicao(db, items=[{"estimate": {**WHEY, "energy_kcal": None}, "quantity": 30,
                                                       "unit": "g"}])
    )
    assert resposta.status_code == 400
    assert "missing_field:energy_kcal" in [e["code"] for e in resposta.json()["detail"]["errors"]]


def test_estimado_nao_aceita_porcao_nem_unidade_sem_conversao(db):
    porcao = _client().post("/nutrition/meals", json=_refeicao(db, items=[
        {"estimate": dict(WHEY), "quantity": 1, "unit": "portion", "portion_id": 999}]))
    assert porcao.status_code == 404 and porcao.json()["detail"]["error"] == "portion_not_found"
    unidade = _client().post("/nutrition/meals", json=_refeicao(db, items=[
        {"estimate": dict(WHEY), "quantity": 1, "unit": "unit"}]))
    assert unidade.status_code == 400
    assert [e["code"] for e in unidade.json()["detail"]["errors"]] == [diary.ERR_UNIT_WITHOUT_CONVERSION]


def test_edicao_reusa_o_snapshot_do_item_estimado(db):
    cliente = _client()
    refeicao = cliente.post("/nutrition/meals", json=_refeicao(db)).json()
    whey = refeicao["items"][1]
    corpo = {
        "local_date": DIA.isoformat(),
        "meal_slot": "post_workout",
        "items": [{"id": whey["id"], "quantity": 60, "unit": "g"}],
    }
    editada = cliente.put(f"/nutrition/meals/{refeicao['id']}", json=corpo)
    assert editada.status_code == 200, editada.text
    [item] = editada.json()["items"]
    assert item["food_id"] is None and item["nature"] == "ESTIMATED" and item["food_source"] == "ai_estimate"
    assert item["nutrients"]["energy_kcal"] == pytest.approx(2 * whey["nutrients"]["energy_kcal"], abs=0.01)
    # A edição não mexe na origem nem no texto da leitura.
    assert editada.json()["source"] == "ai_text" and editada.json()["description_raw"] == "arroz e 30 g de whey"


def test_preview_do_item_estimado_sem_gravar(db):
    resposta = _client().post("/nutrition/meals/preview", json=_refeicao(db))
    assert resposta.status_code == 200, resposta.text
    assert [i["nature"] for i in resposta.json()["items"]] == ["DERIVED", "ESTIMATED"]
    assert db.meals == {} and db.items == {}


def test_match_confidence_fora_da_faixa_e_cortada(db):
    corpo = _refeicao(db)
    corpo["items"][0]["match_confidence"] = 7
    refeicao = _client().post("/nutrition/meals", json=corpo).json()
    assert refeicao["items"][0]["match_confidence"] == mp.CONFIDENCE_RANGE[1]
