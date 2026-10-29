"""Rotas das refeições do diário (`app/nutrition_meals.py`, L4) sem banco real:
gravação com os nutrientes do MOTOR congelados por item (E4), preview sem
gravar (E9), 400 com os códigos do motor (unidade sem conversão, slot, itens,
faixa), alimento/porção que o diário não usa (404), acesso (o próprio aluno;
admin/PT para qualquer aluno; terceiro 403; IDOR entre alunos 403), macro
ausente da TACO e 503 com as tabelas ausentes.

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
from app.nutrition_schema import diary_tables_missing_error
from app.security import Identity, get_current_identity
from tests.support.nutrition_diary_fake import FakeDiaryDB

DIA = date(2026, 10, 17)
ALUNO, OUTRO, ADMIN = 2, 4, 1

# Linhas reais da TACO 4ª ed. (seed da 081): arroz tipo 1 cozido (3), óleo de soja (272), sal grosso (517).
ARROZ = {"energy_kcal": 128.26, "carbohydrates_g": 28.06, "protein_g": 2.52, "total_fat_g": 0.23, "fiber_g": 1.56,
         "sodium_mg": 1.20}
OLEO = {"energy_kcal": 884.00, "total_fat_g": 100.00}
SAL = {"sodium_mg": 39943.20}


@pytest.fixture
def db(monkeypatch):
    banco = FakeDiaryDB()
    monkeypatch.setattr(meals_mod, "get_conn", banco.conn)
    monkeypatch.setattr(meals_mod, "require_diary_tables", lambda cur: None)
    monkeypatch.setattr(meals_mod, "require_food_tables", lambda cur: None)
    banco.arroz = banco.add_food("Arroz, tipo 1, cozido", **ARROZ)
    banco.oleo = banco.add_food("Óleo, de soja", **OLEO)
    banco.sal = banco.add_food("Sal, grosso", **SAL)
    return banco


def _client(role: str = "regular", user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    app.include_router(meals_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _refeicao(db, **extra) -> dict:
    corpo = {
        "local_date": DIA.isoformat(),
        "meal_slot": "lunch",
        "items": [
            {"food_id": db.arroz, "quantity": 150, "unit": "g"},
            {"food_id": db.oleo, "quantity": 1, "unit": "colher_sopa"},
        ],
    }
    corpo.update(extra)
    return corpo


def _motor(per_100: dict, quantidade, unidade, base="g", porcao=None) -> mm.ItemNutrition:
    return mm.item_nutrition(mm.nutrients_from_values(per_100), mm.resolve_amount(quantidade, unidade, base, porcao))


# ---------------------------------------------------------------------------
# Router no app real e 503
# ---------------------------------------------------------------------------


def test_o_router_esta_no_app_real_da_api_core():
    from app.main import app

    caminhos = app.openapi()["paths"]
    for caminho in ("/nutrition/meals", "/nutrition/meals/preview", "/nutrition/meals/{meal_id}"):
        assert caminho in caminhos, caminho
    assert TestClient(app, raise_server_exceptions=False).post("/nutrition/meals/preview", json={}).status_code in (401, 403)


def test_tabelas_ausentes_respondem_503_citando_a_migracao(db, monkeypatch):
    def _sem_tabelas(cur):
        raise diary_tables_missing_error()

    monkeypatch.setattr(meals_mod, "require_diary_tables", _sem_tabelas)
    resposta = _client().post("/nutrition/meals", json=_refeicao(db))
    assert resposta.status_code == 503 and resposta.json()["detail"]["migration"] == "082/083"
    assert db.meals == {}


# ---------------------------------------------------------------------------
# Gravação: nutrientes do motor, congelados por item
# ---------------------------------------------------------------------------


def test_grava_a_refeicao_com_os_nutrientes_do_motor(db):
    resposta = _client().post("/nutrition/meals", json=_refeicao(db, eaten_at="12:30", notes="pós-treino"))
    assert resposta.status_code == 201, resposta.text
    refeicao = resposta.json()
    arroz, oleo = _motor(ARROZ, 150, "g"), _motor(OLEO, 1, "colher_sopa")
    assert refeicao["user_id"] == ALUNO and refeicao["eaten_at"] == "12:30" and refeicao["source"] == "manual"
    assert [i["label"] for i in refeicao["items"]] == ["Arroz, tipo 1, cozido", "Óleo, de soja"]
    for item, motor in zip(refeicao["items"], (arroz, oleo)):
        assert item["amount"] == pytest.approx(motor.amount)
        for campo in mm.NUTRIENT_FIELDS:
            esperado = motor.nutrients.get(campo)
            assert item["nutrients"][campo] == (None if esperado is None else pytest.approx(esperado, abs=0.01)), campo
    # Óleo: 15 ml × 1,0 g/ml assumido -> 15 g; proteína e carbo inferidos 0 (Atwater), sódio desconhecido.
    codigos_oleo = [lim["code"] for lim in refeicao["items"][1]["limitations"]]
    assert codigos_oleo[0] == mm.LIMIT_ASSUMED_DENSITY and "macro_inferred_zero:protein_g" in codigos_oleo
    assert "nutrient_unknown:sodium_mg" in codigos_oleo
    totais = refeicao["totals"]
    assert totais["nutrients"]["energy_kcal"] == pytest.approx(arroz.nutrients.energy_kcal + oleo.nutrients.energy_kcal, abs=0.01)
    assert totais["incomplete_fields"] == ["sodium_mg"] and totais["complete"] is False
    assert totais["nature"] == "DERIVED" and totais["item_count"] == 2
    assert [lim["code"] for lim in totais["limitations"]] == ["total_incomplete:sodium_mg"]
    # Snapshot do catálogo gravado por item (E4).
    gravado = next(iter(db.items.values()))
    snapshot = json.loads(gravado["catalog_snapshot_json"])
    assert snapshot["food"]["per_100"]["energy_kcal"] == ARROZ["energy_kcal"] and snapshot["portion"] is None
    assert db.meals[refeicao["id"]]["created_by"] == ALUNO


def test_preview_calcula_sem_gravar_e_bate_com_a_gravacao(db):
    cliente = _client()
    preview = cliente.post("/nutrition/meals/preview", json=_refeicao(db))
    assert preview.status_code == 200, preview.text
    assert db.writes == [] and db.meals == {} and db.items == {}
    gravada = cliente.post("/nutrition/meals", json=_refeicao(db)).json()
    for no_preview, na_gravacao in zip(preview.json()["items"], gravada["items"]):
        for campo in mm.NUTRIENT_FIELDS:
            if no_preview["nutrients"][campo] is None:
                assert na_gravacao["nutrients"][campo] is None
            else:
                assert na_gravacao["nutrients"][campo] == pytest.approx(no_preview["nutrients"][campo], abs=0.01)
    assert preview.json()["totals"]["incomplete_fields"] == gravada["totals"]["incomplete_fields"]


def test_sal_sem_energia_deixa_o_total_incompleto_sem_virar_zero(db):
    corpo = _refeicao(db, items=[{"food_id": db.arroz, "quantity": 100, "unit": "g"},
                                 {"food_id": db.sal, "quantity": 1, "unit": "g"}])
    totais = _client().post("/nutrition/meals/preview", json=corpo).json()["totals"]
    assert totais["nutrients"]["energy_kcal"] == pytest.approx(128.26)  # o que se conhece
    assert {"energy_kcal", "protein_g", "carbohydrates_g", "total_fat_g", "fiber_g"} <= set(totais["incomplete_fields"])
    assert totais["complete"] is False
    assert totais["nutrients"]["sodium_mg"] == pytest.approx(1.20 + 399.432)


# ---------------------------------------------------------------------------
# 400 com o código do motor e 404 de alimento/porção
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("itens", "slot", "codigos"),
    [
        ([{"quantity": 1, "unit": "kg"}], "lunch", [diary.ERR_UNIT_WITHOUT_CONVERSION]),
        ([{"quantity": 1, "unit": "unit"}], "lunch", [diary.ERR_UNIT_WITHOUT_CONVERSION]),  # sem peso da unidade
        ([{"quantity": 1, "unit": "portion"}], "lunch", [diary.ERR_UNIT_WITHOUT_CONVERSION]),  # sem porção nem rótulo
        ([{"quantity": 0, "unit": "g"}], "lunch", [mm.LIMIT_MISSING_QUANTITY]),
        ([{"quantity": diary.ITEM_AMOUNT_MAX + 1, "unit": "g"}], "lunch", [diary.ERR_ITEM_AMOUNT_OUT_OF_RANGE]),
        ([], "lunch", [diary.ERR_MEAL_WITHOUT_ITEMS]),
        ([{"quantity": 100, "unit": "g"}], "brunch", [diary.ERR_INVALID_MEAL_SLOT]),
    ],
)
def test_entrada_fora_da_regra_do_motor_responde_400_sem_gravar(db, itens, slot, codigos):
    corpo = _refeicao(db, meal_slot=slot, items=[{"food_id": db.arroz, **item} for item in itens])
    for caminho in ("/nutrition/meals", "/nutrition/meals/preview"):
        resposta = _client().post(caminho, json=corpo)
        assert resposta.status_code == 400, resposta.text
        detalhe = resposta.json()["detail"]
        assert detalhe["error"] == "invalid_meal" and [e["code"] for e in detalhe["errors"]] == codigos
        assert all(e["label"] for e in detalhe["errors"])
    assert db.meals == {} and db.items == {}


def test_erro_do_item_traz_o_indice(db):
    corpo = _refeicao(db, items=[{"food_id": db.arroz, "quantity": 100, "unit": "g"},
                                 {"food_id": db.arroz, "quantity": 1, "unit": "kg"}])
    detalhe = _client().post("/nutrition/meals", json=corpo).json()["detail"]
    assert [bloco["index"] for bloco in detalhe["items"]] == [1]


def test_campo_desconhecido_e_422(db):
    assert _client().post("/nutrition/meals", json=_refeicao(db, x=1)).status_code == 422
    corpo = _refeicao(db, items=[{"food_id": db.arroz, "quantity": 100, "unit": "g", "kcal": 999}])
    assert _client().post("/nutrition/meals", json=corpo).status_code == 422  # o FE não manda macro


def test_alimento_que_o_diario_nao_usa_e_404(db):
    privado_do_outro = db.add_food("Bolo da vó", source="user", owner=OUTRO, **ARROZ)
    inativo = db.add_food("Antigo", active=False, **ARROZ)
    for food_id in (privado_do_outro, inativo, 999_999):
        corpo = _refeicao(db, items=[{"food_id": food_id, "quantity": 100, "unit": "g"}])
        resposta = _client().post("/nutrition/meals", json=corpo)
        assert resposta.status_code == 404 and resposta.json()["detail"]["error"] == "food_not_found"
    meu = db.add_food("Meu pão", source="user", owner=ALUNO, **ARROZ)
    assert _client().post("/nutrition/meals", json=_refeicao(db, items=[{"food_id": meu, "quantity": 50, "unit": "g"}])).status_code == 201


def test_porcao_cadastrada_rotulo_e_porcao_de_outro_alimento(db):
    pao = db.add_food("Pão francês", serving_size=50, **ARROZ)
    porcao = db.add_portion(pao, "1 unidade", 50)
    de_outro_alimento = db.add_portion(db.arroz, "1 escumadeira", 90)
    privada_do_outro = db.add_portion(pao, "1 fatia", 25, owner=OUTRO)
    cliente = _client()
    com_porcao = cliente.post("/nutrition/meals/preview", json=_refeicao(db, items=[
        {"food_id": pao, "quantity": 2, "unit": "unit", "portion_id": porcao}])).json()
    assert com_porcao["items"][0]["amount"] == pytest.approx(100.0) and com_porcao["items"][0]["portion_label"] == "1 unidade"
    rotulo = cliente.post("/nutrition/meals/preview", json=_refeicao(db, items=[
        {"food_id": pao, "quantity": 1, "unit": "portion"}])).json()
    assert rotulo["items"][0]["amount"] == pytest.approx(50.0)  # porção do rótulo (serving_size)
    for portion_id in (de_outro_alimento, privada_do_outro, 999_999):
        resposta = cliente.post("/nutrition/meals", json=_refeicao(db, items=[
            {"food_id": pao, "quantity": 1, "unit": "portion", "portion_id": portion_id}]))
        assert resposta.status_code == 404 and resposta.json()["detail"]["error"] == "portion_not_found"


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------


def test_aluno_nao_registra_para_terceiro(db):
    assert _client().post("/nutrition/meals", json=_refeicao(db, user_id=OUTRO)).status_code == 403
    assert _client().post("/nutrition/meals/preview", json=_refeicao(db, user_id=OUTRO)).status_code == 403
    assert db.meals == {}


def test_idor_entre_alunos_editar_ou_apagar_refeicao_alheia_e_403(db):
    do_outro = _client(user_id=OUTRO).post("/nutrition/meals", json=_refeicao(db)).json()
    intruso = _client(user_id=ALUNO)
    assert intruso.put(f"/nutrition/meals/{do_outro['id']}", json=_refeicao(db)).status_code == 403
    assert intruso.delete(f"/nutrition/meals/{do_outro['id']}").status_code == 403
    assert intruso.post("/nutrition/meals/preview", json=_refeicao(db, meal_id=do_outro["id"])).status_code == 404
    assert do_outro["id"] in db.meals and len(db.items) == 2


@pytest.mark.parametrize("papel", ["admin", "personal_trainer"])
def test_admin_e_pt_registram_para_qualquer_aluno_sem_vinculo(db, papel):
    privado_do_pt = db.add_food("Receita do PT", source="user", owner=ADMIN, **ARROZ)
    privado_do_aluno = db.add_food("Receita do aluno", source="user", owner=ALUNO, **ARROZ)
    corpo = _refeicao(db, user_id=ALUNO, items=[{"food_id": privado_do_pt, "quantity": 100, "unit": "g"},
                                                {"food_id": privado_do_aluno, "quantity": 100, "unit": "g"}])
    resposta = _client(papel, user_id=ADMIN).post("/nutrition/meals", json=corpo)
    assert resposta.status_code == 201, resposta.text
    refeicao = resposta.json()
    assert refeicao["user_id"] == ALUNO and db.meals[refeicao["id"]]["created_by"] == ADMIN
    assert _client(papel, user_id=ADMIN).delete(f"/nutrition/meals/{refeicao['id']}").status_code == 200


def test_aluno_inexistente_e_404(db):
    resposta = _client("admin", user_id=ADMIN).post("/nutrition/meals", json=_refeicao(db, user_id=999))
    assert resposta.status_code == 404


# ---------------------------------------------------------------------------
# Edição: snapshot congelado (E4) e exclusão
# ---------------------------------------------------------------------------


def test_editar_o_catalogo_nao_muda_a_refeicao_e_a_edicao_reusa_o_snapshot(db):
    cliente = _client()
    criada = cliente.post("/nutrition/meals", json=_refeicao(db)).json()
    energia_original = criada["items"][0]["nutrients"]["energy_kcal"]
    db.foods[db.arroz]["energy_kcal"] = 999  # o catálogo muda depois
    item_arroz, item_oleo = criada["items"]
    corpo = _refeicao(db, meal_slot="dinner", items=[
        {"id": item_arroz["id"], "food_id": db.arroz, "quantity": 300, "unit": "g"},  # mantido: snapshot, dobra
        {"food_id": db.arroz, "quantity": 100, "unit": "g"},  # novo: catálogo atual
    ])
    editada = cliente.put(f"/nutrition/meals/{criada['id']}", json=corpo)
    assert editada.status_code == 200, editada.text
    itens = editada.json()["items"]
    assert editada.json()["meal_slot"] == "dinner" and len(itens) == 2
    assert itens[0]["nutrients"]["energy_kcal"] == pytest.approx(2 * energia_original, abs=0.02)
    assert itens[1]["nutrients"]["energy_kcal"] == pytest.approx(999.0)
    assert item_oleo["id"] not in db.items  # itens substituídos
    # Item de OUTRA refeição não pode ser reaproveitado.
    outra = cliente.post("/nutrition/meals", json=_refeicao(db)).json()
    resposta = cliente.put(f"/nutrition/meals/{criada['id']}", json=_refeicao(db, items=[
        {"id": outra["items"][0]["id"], "food_id": db.arroz, "quantity": 100, "unit": "g"}]))
    assert resposta.status_code == 404 and resposta.json()["detail"]["error"] == "meal_item_not_found"


def test_preview_da_edicao_usa_o_snapshot(db):
    cliente = _client()
    criada = cliente.post("/nutrition/meals", json=_refeicao(db)).json()
    db.foods[db.arroz]["energy_kcal"] = 999
    corpo = _refeicao(db, meal_id=criada["id"], items=[
        {"id": criada["items"][0]["id"], "food_id": db.arroz, "quantity": 150, "unit": "g"}])
    db.writes.clear()
    preview = cliente.post("/nutrition/meals/preview", json=corpo).json()
    assert preview["items"][0]["nutrients"]["energy_kcal"] == pytest.approx(criada["items"][0]["nutrients"]["energy_kcal"], abs=0.01)
    assert db.writes == []


def test_apagar_leva_os_itens_e_refeicao_inexistente_e_404(db):
    cliente = _client()
    criada = cliente.post("/nutrition/meals", json=_refeicao(db)).json()
    assert cliente.delete(f"/nutrition/meals/{criada['id']}").json() == {"status": "deleted", "id": criada["id"]}
    assert db.meals == {} and db.items == {}
    assert cliente.delete(f"/nutrition/meals/{criada['id']}").status_code == 404
    assert cliente.put("/nutrition/meals/123456", json=_refeicao(db)).status_code == 404
