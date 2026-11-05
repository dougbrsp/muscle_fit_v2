"""Registro rápido de alimentação (nutrição, 2026-11-04): base da tabela do alimento (D1), lançamento
rápido sem catálogo (D2), refeição pela hora (D3) e ordem cronológica do dia (D5).

Dono: `domain/nutrition/catalog.py` (base da tabela) e `domain/nutrition/diary.py` (lançamento, hora -> refeição,
ordem). As rotas só consomem. Bancos em memória: `tests/support/nutrition_foods_fake.py` (com a precisão
DECIMAL(n,2) das colunas) e `tests/support/nutrition_diary_fake.py`."""

from __future__ import annotations

import json
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_days as days_mod
from app import nutrition_foods as foods_mod
from app import nutrition_meals as meals_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import catalog, diary, labels, meal_math as mm
from app.security import Identity, get_current_identity
from tests.support.nutrition_diary_fake import FakeDiaryDB
from tests.support.nutrition_foods_fake import FakeFoodsDB

ALUNO = 2
DIA = date(2026, 11, 4)


# ---------------------------------------------------------------------------
# D1 - base da tabela (motor)
# ---------------------------------------------------------------------------


def _valores(**extra):
    return {"energy_kcal": 66, "carbohydrates_g": 3, "protein_g": 3.3, "total_fat_g": 3.3, **extra}


def test_valores_digitados_por_30_g_viram_por_100_pelo_motor():
    validacao = catalog.validate_food_values(_valores(), "g", table_base=30)
    assert validacao.ok and validacao.table_base == 30
    assert validacao.values["protein_g"] == pytest.approx(11.0)
    assert validacao.values["energy_kcal"] == pytest.approx(220.0)
    # A porção do rótulo não é convertida: continua na base digitada.
    com_porcao = catalog.validate_food_values(_valores(serving_size=30), "g", table_base=30)
    assert com_porcao.values["serving_size"] == 30


def test_sem_base_ou_base_100_o_alimento_segue_por_100_e_nada_e_gravado():
    sem = catalog.validate_food_values(_valores(), "g")
    cem = catalog.validate_food_values(_valores(), "g", table_base=100)
    assert sem.table_base is None and cem.table_base is None  # NULL = 100
    assert sem.values == cem.values and sem.values["protein_g"] == 3.3


@pytest.mark.parametrize("base", [0, -5, 0.004, 0.0099, 10000.01, float("inf"), float("nan"), "abc", True])
def test_base_invalida_e_erro_do_motor(base):
    validacao = catalog.validate_food_values(_valores(), "g", table_base=base)
    assert catalog.ERR_INVALID_TABLE_BASE in validacao.errors and not validacao.ok
    assert labels.limitation_label(catalog.ERR_INVALID_TABLE_BASE) != catalog.ERR_INVALID_TABLE_BASE


def test_valor_negativo_na_base_digitada_continua_erro_de_valor():
    validacao = catalog.validate_food_values(_valores(protein_g=-1), "g", table_base=30)
    assert f"{catalog.ERR_INVALID_VALUE}:protein_g" in validacao.errors


def test_teto_da_base_vale_sobre_o_valor_por_100_convertido():
    # 40 g de gordura em 30 g de alimento = 133 g por 100 g: impossivel.
    validacao = catalog.validate_food_values(_valores(total_fat_g=40), "g", table_base=30)
    assert f"{catalog.ERR_ABOVE_BASIS}:total_fat_g" in validacao.errors


def test_ida_e_volta_30_g_com_3_3_g_de_proteina_pelo_motor():
    por_100 = catalog.validate_food_values(_valores(), "g", table_base=30).values
    assert catalog.values_at_base(por_100, 30)["protein_g"] == 3.3
    assert catalog.values_at_base(por_100, None)["protein_g"] == 11.0  # alimento antigo: por 100


# ---------------------------------------------------------------------------
# D1 - base da tabela (rotas, com a precisao DECIMAL(n,2) do banco)
# ---------------------------------------------------------------------------


@pytest.fixture
def foods_db(monkeypatch):
    banco = FakeFoodsDB()
    banco.add_food("Arroz, tipo 1, cozido", energy_kcal=128.26, carbohydrates_g=28.06, protein_g=2.52,
                   total_fat_g=0.23)
    monkeypatch.setattr(foods_mod, "get_conn", banco.conn)
    monkeypatch.setattr(foods_mod, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(foods_mod, "has_favorites_table", lambda cur: True)
    return banco


def _foods_client() -> TestClient:
    app = FastAPI()
    app.include_router(foods_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=ALUNO, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def test_cadastro_por_30_g_reabre_como_foi_digitado_e_lancar_60_g_da_6_6(foods_db):
    corpo = {"name": "Iogurte proteico", "table_base_amount": 30, **_valores()}
    criado = _foods_client().post("/nutrition/foods", json=corpo)
    assert criado.status_code == 201, criado.text
    alimento = criado.json()
    assert alimento["per_100"]["protein_g"] == 11.0
    assert alimento["table_base_amount"] == 30
    assert alimento["per_table_base"]["protein_g"] == 3.3 and alimento["per_table_base"]["energy_kcal"] == 66.0
    lido = _foods_client().get(f"/nutrition/foods/{alimento['id']}").json()
    assert lido["table_base_amount"] == 30 and lido["per_table_base"]["protein_g"] == 3.3
    gravado = foods_db.foods[alimento["id"]]
    assert gravado["table_base_amount"] == 30
    # Lancar 60 g: a conta do diario (motor) sobre o valor por 100 gravado.
    por_100 = mm.nutrients_from_values(lido["per_100"])
    item = mm.item_nutrition(por_100, mm.resolve_amount(60, "g", "g"))
    assert item.nutrients.protein_g == pytest.approx(6.6, abs=0.001)


def test_edicao_troca_a_base_e_sem_base_volta_para_por_100(foods_db):
    cliente = _foods_client()
    criado = cliente.post("/nutrition/foods", json={"name": "Barra", "table_base_amount": 40, **_valores()}).json()
    editado = cliente.put(f"/nutrition/foods/{criado['id']}", json={"name": "Barra", **_valores()}).json()
    assert editado["table_base_amount"] == 100 and editado["per_100"]["protein_g"] == 3.3
    assert foods_db.foods[criado["id"]]["table_base_amount"] is None


def test_alimento_antigo_sem_base_expoe_100_e_os_mesmos_valores(foods_db):
    antigo = _foods_client().get("/nutrition/foods/1").json()
    assert antigo["table_base_amount"] == 100
    assert antigo["per_table_base"] == antigo["per_100"]


def test_payload_sem_base_continua_valido_e_base_invalida_e_400(foods_db):
    assert _foods_client().post("/nutrition/foods", json={"name": "Aveia", **_valores()}).status_code == 201
    for base in (0, -1):
        resposta = _foods_client().post("/nutrition/foods", json={"name": "X", "table_base_amount": base, **_valores()})
        assert resposta.status_code == 400
        assert catalog.ERR_INVALID_TABLE_BASE in [e["code"] for e in resposta.json()["detail"]["errors"]]


def _ida_e_volta(base: float, digitado: float) -> float:
    """Digita `digitado` g por `base` g -> motor converte para por 100 -> coluna DECIMAL(7,2) (2 casas, meio para
    cima, como o MySQL) -> motor devolve na base."""
    por_100 = catalog.validate_food_values(_valores(protein_g=digitado), "g", table_base=base).values["protein_g"]
    gravado = float(Decimal(str(por_100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return catalog.values_at_base({"protein_g": gravado}, base)["protein_g"]


def test_precisao_decimal_da_ida_e_volta_numeros_medidos():
    """Varredura: proteina com 1 casa (0,1 a 20,0 g) digitada em bases usuais volta igual depois de passar
    por DECIMAL(7,2)? Fixa o que foi medido: ate 100 g preserva 1 casa; acima, depende da base (ver abaixo)."""
    bases_ate_100 = (10, 15, 20, 25, 30, 33, 35, 40, 45, 50, 60, 70, 80, 100)
    bases_maiores = (120, 150, 200, 250, 500)
    falhas_ate_100 = [(b, d / 10) for b in bases_ate_100 for d in range(1, 201) if d / 10 <= b
                      and _ida_e_volta(b, d / 10) != d / 10]
    assert falhas_ate_100 == []
    falhas_maiores = [(b, d / 10) for b in bases_maiores for d in range(1, 201) if _ida_e_volta(b, d / 10) != d / 10]
    # Medido: so a base 150 g quebra (69 de 200 valores, ex.: 0,1 g em 150 g volta 0,11, porque 0,0667 por 100 vira
    # 0,07); 120, 200, 250 e 500 g voltam exatos. Documentado no contrato.
    assert {base for base, _ in falhas_maiores} == {150}
    assert (150, 0.1) in falhas_maiores and len(falhas_maiores) == 69
    assert _ida_e_volta(30, 3.3) == 3.3
    assert _ida_e_volta(60, 6.6) == 6.6


# ---------------------------------------------------------------------------
# D2 - lançamento rápido (motor)
# ---------------------------------------------------------------------------

RAPIDO = {"name": "  Barra   de proteína ", "weight_g": 45, "energy_kcal": 180, "protein_g": 15,
          "carbohydrates_g": 17, "total_fat_g": 6}


def test_lancamento_rapido_valido_guarda_os_valores_absolutos_sem_conversao():
    validacao = diary.validate_quick_entry(RAPIDO)
    assert validacao.errors == () and validacao.entry is not None
    assert validacao.entry.name == "Barra de proteína" and validacao.entry.weight_g == 45
    item = diary.quick_entry_item_nutrition(validacao.entry)
    assert item.nature is mm.MealNature.USER_ENTERED and item.amount == 45 and item.basis is mm.Unit.GRAM
    assert (item.nutrients.energy_kcal, item.nutrients.protein_g) == (180, 15)
    assert item.nutrients.fiber_g is None and item.nutrients.sodium_mg is None
    assert item.not_requested == ("fiber_g", "sodium_mg") and item.limitations == ()
    # Fibra e sodio nao pedidos: nao tornam o total da refeicao incompleto, mas seguem sem numero.
    total = mm.sum_items([item])
    assert total.incomplete_fields == () and total.nutrients.fiber_g is None and not total.is_complete("fiber_g")


@pytest.mark.parametrize("campo", list(diary.QUICK_ENTRY_REQUIRED_FIELDS))
def test_campo_ausente_e_obrigatorio(campo):
    validacao = diary.validate_quick_entry({**RAPIDO, campo: None})
    assert validacao.entry is None and f"{catalog.ERR_MISSING_FIELD}:{campo}" in validacao.errors


def test_nome_vazio_peso_e_valores_invalidos():
    assert catalog.ERR_INVALID_NAME in diary.validate_quick_entry({**RAPIDO, "name": "   "}).errors
    assert diary.ERR_QUICK_ENTRY_WEIGHT in diary.validate_quick_entry({**RAPIDO, "weight_g": 0}).errors
    assert diary.ERR_ITEM_AMOUNT_OUT_OF_RANGE in diary.validate_quick_entry(
        {**RAPIDO, "weight_g": diary.ITEM_AMOUNT_MAX + 1}).errors
    assert f"{catalog.ERR_INVALID_VALUE}:protein_g" in diary.validate_quick_entry({**RAPIDO, "protein_g": -1}).errors
    assert f"{catalog.ERR_INVALID_VALUE}:energy_kcal" in diary.validate_quick_entry(
        {**RAPIDO, "energy_kcal": True}).errors
    # Zero e valido nos nutrientes (agua, cafe).
    assert diary.validate_quick_entry({**RAPIDO, "energy_kcal": 0, "protein_g": 0, "carbohydrates_g": 0,
                                       "total_fat_g": 0}).errors == ()


def test_macro_acima_do_peso_e_kcal_acima_do_possivel_sao_erro():
    erros = diary.validate_quick_entry({**RAPIDO, "weight_g": 10, "protein_g": 11, "energy_kcal": 91}).errors
    assert f"{diary.ERR_QUICK_ABOVE_WEIGHT}:protein_g" in erros and f"{diary.ERR_QUICK_ABOVE_WEIGHT}:energy_kcal" in erros
    assert "base da tabela" not in labels.limitation_label(f"{diary.ERR_QUICK_ABOVE_WEIGHT}:protein_g")


def test_origem_do_item_exatamente_uma_das_tres():
    assert diary.item_source_errors(True, False) == () and diary.item_source_errors(False, True) == ()
    assert diary.item_source_errors(False, False, True) == ()
    for combinacao in ((False, False, False), (True, True, False), (True, False, True), (False, True, True)):
        assert diary.item_source_errors(*combinacao) == (diary.ERR_ITEM_FOOD_OR_ESTIMATE,)


def test_snapshot_do_lancamento_ida_e_volta_e_nao_e_estimativa():
    entrada = diary.validate_quick_entry(RAPIDO).entry
    snapshot = diary.quick_entry_snapshot(entrada)
    assert diary.snapshot_is_quick_entry(snapshot["food"])
    assert diary.quick_entry_from_snapshot(json.loads(json.dumps(snapshot))["food"]) == entrada
    from app.domain.nutrition import meal_parse

    assert not meal_parse.snapshot_is_estimate(snapshot["food"])
    assert diary.quick_entry_from_snapshot({"source": "quick_entry"}) is None


def test_natureza_nova_cabe_na_coluna_e_tem_rotulo():
    assert len(mm.MealNature.USER_ENTERED.value) <= 16  # meal_items.nature / meals.totals_nature
    assert labels.MEAL_NATURE_LABELS[mm.MealNature.USER_ENTERED]
    assert set(labels.MEAL_NATURE_LABELS) == set(mm.MealNature)
    nat = mm.MealNature
    assert mm.meal_nature([nat.USER_ENTERED, nat.USER_ENTERED]) is nat.USER_ENTERED
    assert mm.meal_nature([nat.USER_ENTERED, nat.DERIVED]) is nat.MIXED


# ---------------------------------------------------------------------------
# D3 / D5 - hora -> refeição e ordem do dia (motor)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hora", "esperado"),
    [
        ("00:00", "supper"), ("03:59", "supper"), ("04:00", "breakfast"), ("07:30", "breakfast"),
        ("09:29", "breakfast"), ("09:30", "morning_snack"), ("11:29", "morning_snack"), ("11:30", "lunch"),
        ("12:45", "lunch"), ("14:59", "lunch"), ("15:00", "afternoon_snack"), ("17:59", "afternoon_snack"),
        ("18:00", "dinner"), ("21:29", "dinner"), ("21:30", "supper"), ("23:59", "supper"), ("08:15:30", "breakfast"),
    ],
)
def test_hora_local_deriva_a_refeicao_pelas_janelas_nomeadas(hora, esperado):
    assert diary.meal_slot_for_time(hora).value == esperado


def test_hora_como_time_e_hora_invalida():
    assert diary.meal_slot_for_time(time(12, 0)) is diary.MealSlot.LUNCH
    for invalida in (None, "", "24:00", "12:60", "meio-dia", "7", "12:3x"):
        assert diary.meal_slot_for_time(invalida) is None


def test_janelas_cobrem_o_dia_sem_buraco_e_nunca_sugerem_o_que_depende_do_treino():
    inicios = [inicio for inicio, _ in diary.MEAL_SLOT_WINDOWS]
    assert inicios[0] == 0 and inicios == sorted(inicios) and len(set(inicios)) == len(inicios)
    sugeridas = {diary.meal_slot_for_time(f"{h:02d}:{m:02d}") for h in range(24) for m in (0, 30)}
    assert sugeridas.isdisjoint({diary.MealSlot.PRE_WORKOUT, diary.MealSlot.POST_WORKOUT, diary.MealSlot.OTHER})


def test_ordem_cronologica_sem_hora_por_ultimo_depois_slot_e_id():
    refeicoes = [("breakfast", None, 1), ("dinner", "20:00", 2), ("lunch", "12:00", 4), ("breakfast", "12:00", 3),
                 ("snack?", None, 7), ("supper", "07:00", 9), ("other", None, 5)]
    ordenadas = [r[2] for r in sorted(refeicoes, key=lambda r: diary.meal_sort_key(*r))]
    # 07:00, 12:00 (breakfast antes de lunch pelo slot), 20:00; sem hora: breakfast, other, slot desconhecido
    assert ordenadas == [9, 3, 4, 2, 1, 5, 7]


# ---------------------------------------------------------------------------
# D2/D3/D5 - rotas do diário
# ---------------------------------------------------------------------------

ARROZ = {"energy_kcal": 128.26, "carbohydrates_g": 28.06, "protein_g": 2.52, "total_fat_g": 0.23, "fiber_g": 1.56,
         "sodium_mg": 1.20}


@pytest.fixture
def db(monkeypatch):
    banco = FakeDiaryDB()
    banco.arroz = banco.add_food("Arroz, tipo 1, cozido", **ARROZ)
    for modulo in (days_mod, meals_mod):
        monkeypatch.setattr(modulo, "get_conn", banco.conn)
        monkeypatch.setattr(modulo, "require_diary_tables", lambda cur: None)
    monkeypatch.setattr(meals_mod, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(days_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(days_mod, "_today_for", lambda conn, user_id: DIA)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    return banco


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(days_mod.router)
    app.include_router(meals_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=ALUNO, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _refeicao(db, itens, **extra) -> dict:
    return {"local_date": DIA.isoformat(), "meal_slot": "lunch", "items": itens, **extra}


def _item_rapido(**extra) -> dict:
    return {"quick_entry": {**RAPIDO, **extra}}


def test_grava_o_lancamento_rapido_sem_catalogo_sem_aviso_de_ia(db):
    antes = dict(db.foods)
    resposta = _client().post("/nutrition/meals", json=_refeicao(db, [_item_rapido()]))
    assert resposta.status_code == 201, resposta.text
    refeicao = resposta.json()
    [item] = refeicao["items"]
    assert item["food_id"] is None and item["label"] == "Barra de proteína" and item["unit"] == "g"
    assert item["quantity"] == 45 and item["amount"] == 45 and item["basis"] == "g"
    assert item["nature"] == "USER_ENTERED" and item["food_source"] == "quick_entry" and item["citation"] is None
    assert item["nutrients"] == {"energy_kcal": 180, "protein_g": 15, "carbohydrates_g": 17, "total_fat_g": 6,
                                 "fiber_g": None, "sodium_mg": None}
    assert item["limitations"] == []  # nada de aviso de IA (`ai_estimate`)
    assert item["quick_entry"]["weight_g"] == 45 and item["quick_entry"]["nutrients"]["protein_g"] == 15
    assert refeicao["source"] == "manual" and refeicao["ai_job_id"] is None
    assert refeicao["totals"]["nature"] == "USER_ENTERED"
    assert refeicao["totals"]["nutrients"]["protein_g"] == 15
    assert refeicao["totals"]["incomplete_fields"] == [] and refeicao["totals"]["complete"] is True
    assert db.foods == antes  # nenhum alimento criado no catalogo
    [gravado] = db.items.values()
    assert gravado["food_id"] is None and gravado["nature"] == "USER_ENTERED"
    assert json.loads(gravado["catalog_snapshot_json"])["food"]["source"] == "quick_entry"


def test_mistura_com_catalogo_vira_misto_e_soma_os_totais(db):
    itens = [{"food_id": db.arroz, "quantity": 100, "unit": "g"}, _item_rapido()]
    refeicao = _client().post("/nutrition/meals", json=_refeicao(db, itens)).json()
    assert [i["nature"] for i in refeicao["items"]] == ["DERIVED", "USER_ENTERED"]
    assert refeicao["totals"]["nature"] == "MIXED"
    assert refeicao["totals"]["nutrients"]["energy_kcal"] == pytest.approx(128.26 + 180, abs=0.01)


def test_preview_do_lancamento_nao_grava_e_sugere_a_refeicao_pela_hora(db):
    corpo = _refeicao(db, [_item_rapido()], eaten_at="07:45", meal_slot=None)
    resposta = _client().post("/nutrition/meals/preview", json=corpo)
    assert resposta.status_code == 200, resposta.text
    dados = resposta.json()
    assert dados["suggested_meal_slot"] == "breakfast" and dados["items"][0]["nature"] == "USER_ENTERED"
    assert dados["totals"]["nutrients"]["energy_kcal"] == 180
    assert db.meals == {} and db.items == {} and db.foods.keys() == {db.arroz}
    sem_hora = _client().post("/nutrition/meals/preview", json=_refeicao(db, [_item_rapido()])).json()
    assert sem_hora["suggested_meal_slot"] is None


@pytest.mark.parametrize(
    ("ajuste", "codigo"),
    [
        ({"weight_g": None}, "missing_field:weight_g"),
        ({"protein_g": None}, "missing_field:protein_g"),
        ({"weight_g": 0}, diary.ERR_QUICK_ENTRY_WEIGHT),
        ({"energy_kcal": -1}, "invalid_value:energy_kcal"),
    ],
)
def test_lancamento_invalido_responde_400_com_o_indice_e_os_codigos_do_motor(db, ajuste, codigo):
    resposta = _client().post("/nutrition/meals", json=_refeicao(db, [_item_rapido(**ajuste)]))
    assert resposta.status_code == 400
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == "invalid_meal" and codigo in [e["code"] for e in detalhe["errors"]]
    assert detalhe["items"][0]["index"] == 0 and all(e["label"] != e["code"] for e in detalhe["errors"])
    assert db.meals == {}


def test_origem_unica_quick_entry_com_food_ou_estimate_e_400(db):
    com_food = {"food_id": db.arroz, "quantity": 10, "unit": "g", **_item_rapido()}
    com_estimate = {"estimate": {"name": "x", "energy_kcal": 100, "protein_g": 1, "carbohydrates_g": 1,
                                 "total_fat_g": 1}, "quantity": 10, "unit": "g", **_item_rapido()}
    for item in (com_food, com_estimate):
        resposta = _client().post("/nutrition/meals", json=_refeicao(db, [item]))
        assert resposta.status_code == 400
        assert resposta.json()["detail"]["items"][0]["errors"][0]["code"] == diary.ERR_ITEM_FOOD_OR_ESTIMATE


def test_item_do_catalogo_sem_quantidade_continua_400_do_motor(db):
    resposta = _client().post("/nutrition/meals", json=_refeicao(db, [{"food_id": db.arroz}]))
    assert resposta.status_code == 400
    assert "missing_quantity" in [e["code"] for e in resposta.json()["detail"]["errors"]]


def test_lancamento_nao_aceita_porcao(db):
    resposta = _client().post("/nutrition/meals", json=_refeicao(db, [{**_item_rapido(), "portion_id": 1}]))
    assert resposta.status_code == 404 and resposta.json()["detail"]["error"] == "portion_not_found"


def test_edicao_mantem_o_snapshot_do_lancamento_ou_troca_pelos_valores_novos(db):
    cliente = _client()
    criada = cliente.post("/nutrition/meals", json=_refeicao(db, [_item_rapido()])).json()
    item_id = criada["items"][0]["id"]
    mantida = cliente.put(f"/nutrition/meals/{criada['id']}",
                          json={"local_date": DIA.isoformat(), "meal_slot": "lunch", "items": [{"id": item_id}]})
    assert mantida.status_code == 200, mantida.text
    [item] = mantida.json()["items"]
    assert item["nature"] == "USER_ENTERED" and item["nutrients"]["energy_kcal"] == 180 and item["amount"] == 45
    item_id = item["id"]  # a edicao regrava os itens: o id novo vale para a proxima
    trocada = cliente.put(
        f"/nutrition/meals/{criada['id']}",
        json={"local_date": DIA.isoformat(), "meal_slot": "lunch",
              "items": [{"id": item_id, **_item_rapido(weight_g=90, energy_kcal=360, protein_g=30,
                                                       carbohydrates_g=34, total_fat_g=12)}]},
    )
    assert trocada.status_code == 200, trocada.text
    assert trocada.json()["items"][0]["nutrients"]["energy_kcal"] == 360 and trocada.json()["items"][0]["amount"] == 90


def test_item_do_catalogo_editado_continua_derived_e_id_sem_origem_reutilizavel_e_400(db):
    cliente = _client()
    criada = cliente.post("/nutrition/meals", json=_refeicao(db, [{"food_id": db.arroz, "quantity": 100, "unit": "g"}]))
    item_id = criada.json()["items"][0]["id"]
    editada = cliente.put(f"/nutrition/meals/{criada.json()['id']}", json={
        "local_date": DIA.isoformat(), "meal_slot": "lunch",
        "items": [{"id": item_id, "food_id": db.arroz, "quantity": 200, "unit": "g"}]})
    assert editada.status_code == 200 and editada.json()["items"][0]["nature"] == "DERIVED"
    item_id = editada.json()["items"][0]["id"]  # a edicao regrava os itens
    sem_origem = cliente.put(f"/nutrition/meals/{criada.json()['id']}", json={
        "local_date": DIA.isoformat(), "meal_slot": "lunch", "items": [{"id": item_id}]})
    assert sem_origem.status_code == 400  # id sem food_id nem snapshot reutilizavel


def test_endpoint_hora_para_refeicao_e_hora_invalida(db):
    cliente = _client()
    ok = cliente.get("/nutrition/meals/slot-for-time", params={"time": "12:30"})
    assert ok.status_code == 200
    assert ok.json() == {"time": "12:30", "meal_slot": "lunch",
                         "meal_slot_label": labels.MEAL_SLOT_LABELS[diary.MealSlot.LUNCH]}
    ruim = cliente.get("/nutrition/meals/slot-for-time", params={"time": "25:00"})
    assert ruim.status_code == 400 and ruim.json()["detail"]["error"] == diary.ERR_INVALID_CLOCK_TIME


def test_ultima_hora_registrada_sem_historico_e_null_e_depois_traz_a_ultima_com_hora(db):
    cliente = _client()
    assert cliente.get("/nutrition/meals/last-time").json() == {"time": None}
    base = _refeicao(db, [{"food_id": db.arroz, "quantity": 100, "unit": "g"}])
    assert cliente.post("/nutrition/meals", json={**base, "eaten_at": "12:30"}).status_code == 201
    assert cliente.get("/nutrition/meals/last-time").json() == {"time": "12:30"}
    sem_hora = {k: v for k, v in base.items() if k != "eaten_at"}
    assert cliente.post("/nutrition/meals", json=sem_hora).status_code == 201
    assert cliente.get("/nutrition/meals/last-time").json() == {"time": "12:30"}  # sem hora nao apaga a referencia
    assert cliente.post("/nutrition/meals", json={**base, "eaten_at": "19:05"}).status_code == 201
    assert cliente.get("/nutrition/meals/last-time").json() == {"time": "19:05"}


def test_ultima_hora_e_isolada_por_usuario_e_exige_autenticacao(db):
    cliente = _client()
    outra = {"id": 999, "user_id": 4, "eaten_at": time(23, 10), "local_date": DIA}
    db.meals[999] = outra  # refeicao de OUTRO usuario, mais recente
    assert cliente.get("/nutrition/meals/last-time").json() == {"time": None}
    app = FastAPI()
    app.include_router(meals_mod.router)
    anonimo = TestClient(app, raise_server_exceptions=False)
    assert anonimo.get("/nutrition/meals/last-time").status_code in (401, 403)


@pytest.mark.parametrize("serving,base,basis,esperado", [
    (30, None, "g", {"quantity": 30.0, "unit": "g"}),
    (30, 50, "g", {"quantity": 30.0, "unit": "g"}),  # a porcao do rotulo vence a base digitada
    (None, 50, "ml", {"quantity": 50.0, "unit": "ml"}),
    (None, None, "g", None),
    (0, None, "g", None),
    (-5, None, "g", None),
    ("abc", 30, "g", {"quantity": 30.0, "unit": "g"}),  # porcao invalida cai na base
    (30, None, "kg", None),  # base desconhecida
    (99999, None, "g", None),  # acima do teto da porcao
    (True, None, "g", None),
    (None, 5000, "g", None),  # base digitada acima do teto da porcao
    (None, 2000, "g", {"quantity": 2000.0, "unit": "g"}),
])
def test_porcao_padrao_do_motor(serving, base, basis, esperado):
    porcao = catalog.default_portion(serving, base, basis)
    assert (porcao.public() if porcao else None) == esperado


def test_food_public_expoe_a_porcao_padrao_do_motor():
    linha = {"id": 1, "serving_size": Decimal("30"), "basis": "g", "table_base_amount": None, "is_active": 1}
    identidade = Identity(user_id=2, role="regular", source="session")
    assert foods_mod.food_public(linha, identidade)["default_portion"] == {"quantity": 30.0, "unit": "g"}
    assert foods_mod.food_public({**linha, "serving_size": None}, identidade)["default_portion"] is None


def test_o_endpoint_esta_no_app_real_da_api_core():
    from app.main import app

    assert "/nutrition/meals/slot-for-time" in app.openapi()["paths"]


def test_dia_lista_as_refeicoes_em_ordem_cronologica_e_o_balanco_cobre_kcal_e_3_macros(db):
    cliente = _client()
    for slot, hora, nome in (("dinner", "20:10", "Jantar"), ("breakfast", None, "Sem hora"),
                             ("lunch", "12:30", "Almoço"), ("breakfast", "07:00", "Café")):
        corpo = _refeicao(db, [_item_rapido(name=nome)], meal_slot=slot)
        if hora:
            corpo["eaten_at"] = hora
        assert cliente.post("/nutrition/meals", json=corpo).status_code == 201
    dia = cliente.get(f"/nutrition/days/{DIA.isoformat()}")
    assert dia.status_code == 200, dia.text
    corpo = dia.json()
    assert [m["items"][0]["label"] for m in corpo["meals"]] == ["Café", "Almoço", "Jantar", "Sem hora"]
    assert [m["eaten_at"] for m in corpo["meals"]] == ["07:00", "12:30", "20:10", None]
    for meal in corpo["meals"]:
        assert meal["items"][0]["label"] and meal["totals"]["nutrients"]["energy_kcal"] == 180
    # Sem plano em vigor: sem meta, e o balanco entrega so o consumido (sem partes do grafico).
    assert corpo["target"] is None
    linhas = {r["field"]: r for r in corpo["balance"]["rows"]}
    assert set(linhas) == {"energy_kcal", "protein_g", "carbohydrates_g", "total_fat_g", "fiber_g"}
    # Fibra nao e informada no lancamento rapido: ingestao incompleta, sem partes e sem quebrar os demais.
    assert linhas["fiber_g"]["intake_complete"] is False and linhas["fiber_g"]["within_target"] is None
    assert linhas["fiber_g"]["intake"] is None  # sem numero falso
    assert corpo["totals"]["complete"] is True and corpo["totals"]["incomplete_fields"] == []
    assert linhas["energy_kcal"]["intake"] == 720 and linhas["energy_kcal"]["target"] is None
    assert linhas["protein_g"]["intake"] == 60
    assert linhas["energy_kcal"]["within_target"] is None and linhas["energy_kcal"]["to_consume"] is None
    assert corpo["totals"]["nature"] == "USER_ENTERED"


def test_motor_expoe_constantes_do_lancamento_e_da_base_para_o_front():
    from app.domain import nutrition

    bloco = nutrition.export_for_frontend()
    assert bloco["quick_entry"] == {
        "weight_field": "weight_g", "nutrient_fields": ["energy_kcal", "protein_g", "carbohydrates_g", "total_fat_g"],
        "weight_max": diary.ITEM_AMOUNT_MAX, "name_max_chars": diary.QUICK_ENTRY_NAME_MAX_CHARS,
        "nature": "USER_ENTERED", "source": "quick_entry", "unit": "g",
    }
    assert bloco["table_base"] == {
        "default": 100.0, "min": 0.01, "max": catalog.TABLE_BASE_AMOUNT_MAX, "display_decimals": 2,
        "units": ["g", "ml"], "unit_labels": {"g": "g", "ml": "ml"},
    }
    assert bloco["meal_nature_mixed"] == "MIXED"
    assert "USER_ENTERED" in bloco["meal_natures"] and "USER_ENTERED" in bloco["meal_nature_labels"]
    gerado = json.loads((__import__("pathlib").Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib"
                         / "motor-constants.generated.json").read_text(encoding="utf-8"))
    assert gerado["nutrition"]["quick_entry"] == bloco["quick_entry"]
    assert gerado["nutrition"]["table_base"] == bloco["table_base"]


def test_motor_da_hora_nao_le_o_relogio():
    import inspect

    assert "datetime.now" not in inspect.getsource(diary)  # a hora vem do usuario


# ---------------------------------------------------------------------------
# Correcoes da rodada 1
# ---------------------------------------------------------------------------


def _item_catalogo_com_fibra():
    por_100 = mm.nutrients_from_values({"energy_kcal": 100, "protein_g": 10, "carbohydrates_g": 10,
                                        "total_fat_g": 1, "fiber_g": 3, "sodium_mg": 50})
    return mm.item_nutrition(por_100, mm.resolve_amount(100, "g", "g"))


def _item_rapido_motor():
    return diary.quick_entry_item_nutrition(diary.validate_quick_entry(RAPIDO).entry)


def _reler(total):
    """Gravar e reler: so os valores e `incomplete_fields` persistem."""
    return mm.totals_from_stored(total.nutrients.to_dict(), list(total.incomplete_fields), total.nature.value,
                                 total.item_count)


def test_refeicao_com_rapido_e_catalogo_com_fibra_marca_fibra_e_sodio_incompletos():
    total = mm.sum_items([_item_catalogo_com_fibra(), _item_rapido_motor()])
    assert total.nutrients.fiber_g == 3 and set(total.incomplete_fields) == {"fiber_g", "sodium_mg"}
    assert not total.is_complete("fiber_g")
    assert mm.sum_items([_item_rapido_motor(), _item_rapido_motor()]).incomplete_fields == ()


def test_dia_mistura_refeicoes_e_sobrevive_a_gravar_e_reler_os_totais():
    cheia = mm.sum_items([_item_catalogo_com_fibra()])
    rapida = mm.sum_items([_item_rapido_motor()])
    assert cheia.incomplete_fields == () and rapida.incomplete_fields == ()
    dia = mm.combine_totals([_reler(cheia), _reler(rapida)])
    assert dia.nutrients.fiber_g == 3 and {"fiber_g", "sodium_mg"} <= set(dia.incomplete_fields)
    so_rapido = mm.combine_totals([_reler(rapida), _reler(rapida)])
    assert so_rapido.incomplete_fields == () and so_rapido.nutrients.fiber_g is None
    mista = mm.sum_items([_item_catalogo_com_fibra(), _item_rapido_motor()])
    assert {"fiber_g", "sodium_mg"} <= set(mm.combine_totals([_reler(mista)]).incomplete_fields)


def test_base_minima_e_valida_e_exportada():
    assert catalog.TABLE_BASE_AMOUNT_MIN == 0.01
    zeros = {"energy_kcal": 0, "carbohydrates_g": 0, "protein_g": 0, "total_fat_g": 0}
    assert catalog.validate_food_values(zeros, "g", table_base=0.01).errors == ()
    assert catalog.ERR_INVALID_TABLE_BASE in catalog.validate_food_values(zeros, "g", table_base=0.0049).errors


def test_dia_misto_com_alimento_sem_macro_continua_incompleto(db):
    """O alimento do catalogo que realmente nao tem o macro segue marcando o total; o lancamento rapido nao."""
    sem_gordura = db.add_food("Caldo sem gordura informada", energy_kcal=50, protein_g=2, carbohydrates_g=8)
    cliente = _client()
    so_rapido = cliente.post("/nutrition/meals", json=_refeicao(db, [_item_rapido()])).json()
    assert so_rapido["totals"]["incomplete_fields"] == []
    mista = cliente.post("/nutrition/meals", json=_refeicao(db, [
        {"food_id": sem_gordura, "quantity": 100, "unit": "g"}, _item_rapido()], meal_slot="dinner")).json()
    assert "total_fat_g" in mista["totals"]["incomplete_fields"] or mista["totals"]["complete"] is False
    dia = cliente.get(f"/nutrition/days/{DIA.isoformat()}").json()
    assert dia["totals"]["complete"] is False
    # Dia so com lancamento rapido: completo.
    cliente.delete(f"/nutrition/meals/{mista['id']}")
    dia2 = cliente.get(f"/nutrition/days/{DIA.isoformat()}").json()
    assert dia2["totals"]["complete"] is True and dia2["totals"]["incomplete_fields"] == []


def test_lancamento_rapido_nao_cria_alimento_e_excluir_tira_do_diario_e_do_balanco(db):
    cliente = _client()
    antes = len(db.foods)
    criada = cliente.post("/nutrition/meals", json=_refeicao(db, [_item_rapido()])).json()
    assert len(db.foods) == antes
    dia = cliente.get(f"/nutrition/days/{DIA.isoformat()}").json()
    assert len(dia["meals"]) == 1 and dia["balance"]["rows"][0]["intake"] == 180
    assert cliente.delete(f"/nutrition/meals/{criada['id']}").status_code == 200
    dia = cliente.get(f"/nutrition/days/{DIA.isoformat()}").json()
    assert dia["meals"] == [] and dia["totals"] is None
    assert all(r["intake"] is None for r in dia["balance"]["rows"]) and len(db.foods) == antes


def test_alimento_com_base_200_ml_lancado_em_100_ml_pelas_rotas(monkeypatch, db):
    """200 ml com 20 g de proteina: por 100 ml = 10 g; lancar 100 ml da 10 g (conta do diario, nada na tela)."""
    from app import nutrition_foods as foods_mod

    banco = FakeFoodsDB()
    monkeypatch.setattr(foods_mod, "get_conn", banco.conn)
    monkeypatch.setattr(foods_mod, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(foods_mod, "has_favorites_table", lambda cur: True)
    app = FastAPI()
    app.include_router(foods_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=ALUNO, role="regular", source="session")
    criado = TestClient(app).post("/nutrition/foods", json={
        "name": "Bebida lactea", "basis": "ml", "table_base_amount": 200, "energy_kcal": 160, "protein_g": 20,
        "carbohydrates_g": 16, "total_fat_g": 4})
    assert criado.status_code == 201, criado.text
    assert criado.json()["per_100"]["protein_g"] == 10.0 and criado.json()["per_table_base"]["protein_g"] == 20.0
    alimento = db.add_food("Bebida lactea", basis="ml", **{k: v for k, v in criado.json()["per_100"].items() if v is not None})
    refeicao = _client().post("/nutrition/meals", json=_refeicao(db, [{"food_id": alimento, "quantity": 100, "unit": "ml"}]))
    assert refeicao.status_code == 201, refeicao.text
    item = refeicao.json()["items"][0]
    assert item["basis"] == "ml" and item["amount"] == 100 and item["nutrients"]["protein_g"] == pytest.approx(10.0)
    assert item["nutrients"]["energy_kcal"] == pytest.approx(80.0)
