"""Rotas do dia e da semana do diário (`app/nutrition_days.py`, L4) sem banco
real: meta do dia pela decisão do motor (materializa o não congelado; dia que
congelou SEM meta é calculado e gravado UMA vez com `target_backfilled`; o
congelado com meta fica intacto; além da semana não cria), totais e balanço do
motor, kcal do treino "informado × estimado" com a proveniência do dono, água e
fome com a faixa do motor, semana (orçamento, banco, aderência) e acesso
(terceiro 403; admin/PT qualquer aluno).

Banco em memória: `tests/support/nutrition_diary_fake.py`."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_days as days_mod
from app import nutrition_meals as meals_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import daily_target as dt
from app.domain.nutrition import diary
from app.domain.nutrition import weekly_budget as wb
from app.domain.nutrition.energy_expenditure import training_kcal_met
from app.nutrition_schema import diary_tables_missing_error
from app.security import Identity, get_current_identity
from tests.support.nutrition_diary_fake import FakeDiaryDB

HOJE = date(2026, 10, 17)  # sábado; semana de 12/10 a 18/10
ALUNO, OUTRO, ADMIN = 2, 4, 1
INICIO_DO_PLANO = date(2026, 10, 1)
ARROZ = {"energy_kcal": 128.26, "carbohydrates_g": 28.06, "protein_g": 2.52, "total_fat_g": 0.23, "fiber_g": 1.56,
         "sodium_mg": 1.20}


@pytest.fixture
def db(monkeypatch):
    banco = FakeDiaryDB()
    banco.add_weight(ALUNO, date(2026, 9, 20), 80)
    banco.plano = banco.add_plan(ALUNO, status="published", effective_from=INICIO_DO_PLANO,
                                 published_at=datetime(2026, 10, 1, 9, 0, 0))
    banco.arroz = banco.add_food("Arroz, tipo 1, cozido", **ARROZ)
    banco.sal = banco.add_food("Sal, grosso", sodium_mg=39943.20)
    for modulo in (days_mod, meals_mod):
        monkeypatch.setattr(modulo, "get_conn", banco.conn)
        monkeypatch.setattr(modulo, "require_diary_tables", lambda cur: None)
    monkeypatch.setattr(meals_mod, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(days_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(days_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    return banco


def _client(role: str = "regular", user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    app.include_router(days_mod.router)
    app.include_router(meals_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _registrar(cliente, dia: date, gramas: float = 500, food_id=None, db=None, **extra):
    corpo = {"local_date": dia.isoformat(), "meal_slot": "lunch",
             "items": [{"food_id": food_id, "quantity": gramas, "unit": "g"}], **extra}
    resposta = cliente.post("/nutrition/meals", json=corpo)
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def _codigos(itens) -> list[str]:
    return [item["code"] for item in itens]


# ---------------------------------------------------------------------------
# Router no app real e 503
# ---------------------------------------------------------------------------


def test_o_router_esta_no_app_real_da_api_core():
    from app.main import app

    caminhos = app.openapi()["paths"]
    for caminho in ("/nutrition/days/{local_date}", "/nutrition/weeks"):
        assert caminho in caminhos, caminho
    assert TestClient(app, raise_server_exceptions=False).get("/nutrition/weeks").status_code in (401, 403)


def test_tabelas_ausentes_respondem_503(db, monkeypatch):
    def _sem(cur):
        raise diary_tables_missing_error()

    monkeypatch.setattr(days_mod, "require_diary_tables", _sem)
    cliente = _client()
    assert cliente.get(f"/nutrition/days/{HOJE.isoformat()}").status_code == 503
    assert cliente.get("/nutrition/weeks").status_code == 503
    assert cliente.put(f"/nutrition/days/{HOJE.isoformat()}", json={"hunger": 3}).status_code == 503
    assert db.targets == {}


# ---------------------------------------------------------------------------
# Meta do dia (decisão do motor, R8)
# ---------------------------------------------------------------------------


def test_hoje_materializa_a_meta_e_mostra_totais_e_balanco(db):
    cliente = _client()
    refeicao = _registrar(cliente, HOJE, 500, food_id=db.arroz)
    dia = cliente.get(f"/nutrition/days/{HOJE.isoformat()}")
    assert dia.status_code == 200, dia.text
    corpo = dia.json()
    assert corpo["target_action"] == dt.TargetAction.MATERIALIZE.value and corpo["target_notices"] == []
    meta = corpo["target"]
    assert meta["local_date"] == HOJE.isoformat() and meta["kcal"] > 0 and meta["backfilled"] is False
    assert (ALUNO, HOJE) in db.targets
    assert [m["id"] for m in corpo["meals"]] == [refeicao["id"]]
    assert corpo["totals"]["nutrients"]["energy_kcal"] == pytest.approx(5 * 128.26)
    energia = corpo["balance"]["rows"][0]
    assert energia["field"] == "energy_kcal" and energia["target"] == pytest.approx(meta["kcal"])
    assert energia["remaining"] == pytest.approx(meta["kcal"] - 5 * 128.26, abs=0.01)
    assert corpo["balance"]["energy_balance_kcal"] == pytest.approx(5 * 128.26 - meta["tdee_kcal"], abs=0.01)
    assert corpo["day_log"] is None and corpo["training"]["sessions"] == []


def test_dia_congelado_sem_meta_e_calculado_e_gravado_uma_vez(db):
    passado = HOJE - timedelta(days=10)
    cliente = _client()
    primeiro = cliente.get(f"/nutrition/days/{passado.isoformat()}").json()
    assert primeiro["target_action"] == dt.TargetAction.BACKFILL.value
    meta = primeiro["target"]
    assert meta["backfilled"] is True and meta["frozen"] is True and meta["kcal"] > 0
    assert dt.LIMIT_TARGET_BACKFILLED in _codigos(meta["limitations"])
    gravada = db.targets[(ALUNO, passado)]
    entradas = gravada["inputs_json"]
    assert entradas["materialization"] == {"kind": dt.BACKFILL_KIND, "computed_on": HOJE.isoformat()}
    assert db.writes == [f"backfill {passado.isoformat()}"]
    # Segunda leitura: congelada COM meta -> só lê, nada é regravado.
    segundo = cliente.get(f"/nutrition/days/{passado.isoformat()}").json()
    assert segundo["target_action"] == dt.TargetAction.READ.value and segundo["target"]["kcal"] == meta["kcal"]
    assert db.writes == [f"backfill {passado.isoformat()}"]


def test_dia_congelado_com_meta_fica_intacto(db):
    passado = HOJE - timedelta(days=5)
    _client().get(f"/nutrition/days/{passado.isoformat()}")  # grava a retroativa
    db.targets[(ALUNO, passado)]["kcal_target"] = 1234  # o que estiver gravado é o que vale
    db.writes.clear()
    db.add_weight(ALUNO, passado, 95)  # dado novo do passado não reescreve o dia congelado
    corpo = _client().get(f"/nutrition/days/{passado.isoformat()}").json()
    assert corpo["target"]["kcal"] == 1234.0 and db.writes == []


def test_meta_retroativa_nao_sobrescreve_quem_gravou_no_meio(db, monkeypatch):
    """Corrida: outra requisição gravou a meta entre a leitura e o INSERT -> a primeira fica."""
    passado = HOJE - timedelta(days=9)
    original = svc._backfill_frozen_days

    def _concorrente(cur, user_id, days, today):
        db.targets[(ALUNO, passado)] = {**dict(zip(svc.UPSERT_COLUMNS, [None] * len(svc.UPSERT_COLUMNS))),
                                        "user_id": ALUNO, "local_date": passado, "plan_id": db.plano,
                                        "day_kind": "normal", "is_training_day": 0, "training_known": 1,
                                        "kcal_target": 1111, "bmr_formula": "mifflin_st_jeor",
                                        "limitations_json": "[]", "engine_version": "nutrition-v1",
                                        "computed_at": db.now, "water_ml": None}
        return original(cur, user_id, days, today)

    monkeypatch.setattr(svc, "_backfill_frozen_days", _concorrente)
    corpo = _client().get(f"/nutrition/days/{passado.isoformat()}").json()
    assert corpo["target"]["kcal"] == 1111.0


def test_sem_plano_em_vigor_nao_ha_meta_nem_escrita(db):
    antes_do_plano = INICIO_DO_PLANO - timedelta(days=3)
    corpo = _client().get(f"/nutrition/days/{antes_do_plano.isoformat()}").json()
    assert corpo["target"] is None and corpo["target_action"] == dt.TargetAction.BACKFILL.value
    assert wb.LIMIT_NO_DAY_TARGET in _codigos(corpo["balance"]["limitations"])
    assert db.targets == {} and db.writes == []
    sem_plano = _client(user_id=OUTRO).get(f"/nutrition/days/{HOJE.isoformat()}").json()
    assert sem_plano["target"] is None and db.targets == {}


def test_dia_alem_da_semana_nao_cria_meta(db):
    longe = HOJE + timedelta(days=10)
    corpo = _client().get(f"/nutrition/days/{longe.isoformat()}").json()
    assert corpo["target_action"] == dt.TargetAction.SKIP.value and corpo["target"] is None
    assert _codigos(corpo["target_notices"]) == [dt.LIMIT_TARGET_FUTURE_BEYOND_WEEK]
    assert db.targets == {}


def test_total_incompleto_nao_inventa_o_balanco(db):
    cliente = _client()
    _registrar(cliente, HOJE, 1, food_id=db.sal)
    corpo = cliente.get(f"/nutrition/days/{HOJE.isoformat()}").json()
    assert "energy_kcal" in corpo["totals"]["incomplete_fields"]
    energia = corpo["balance"]["rows"][0]
    assert energia["intake_complete"] is False and energia["remaining"] is None
    assert corpo["balance"]["energy_balance_kcal"] is None
    assert "total_incomplete:energy_kcal" in _codigos(corpo["balance"]["limitations"])


# ---------------------------------------------------------------------------
# kcal do treino: informado × estimado (natureza) e proveniência (dono calories_source)
# ---------------------------------------------------------------------------


def test_kcal_do_treino_informada_com_proveniencia_e_estimativa_met(db):
    db.add_activity(ALUNO, HOJE, 410, 3600, intervals_kcal=410, name="Treino A")
    treino = _client().get(f"/nutrition/days/{HOJE.isoformat()}").json()["training"]
    assert treino["is_training_day"] is True
    sessao = treino["sessions"][0]
    assert sessao["calories_kcal"] == 410.0 and sessao["calories_source"] == "intervals"
    assert sessao["calories_source_label"] == "Intervals" and sessao["name"] == "Treino A"
    comparacao = treino["comparison"]
    assert comparacao["reported"]["kcal"] == 410.0 and comparacao["reported"]["kcal_nature"] == "reported"
    assert comparacao["estimated"]["kcal"] == pytest.approx(training_kcal_met(80, 60))  # MET do motor, nunca gravada
    assert comparacao["used"]["kcal_nature"] == "reported"


def test_kcal_manual_e_sessao_sem_kcal(db):
    ontem = HOJE - timedelta(days=1)
    db.add_activity(ALUNO, ontem, 300, 2700, source="manual")
    db.add_activity(ALUNO, ontem, None, 1800)
    treino = _client().get(f"/nutrition/days/{ontem.isoformat()}").json()["training"]
    assert [s["calories_source"] for s in treino["sessions"]] == ["manual", None]
    # Uma sessão sem kcal: a soma informada cai (regra de `day_context_for`) e o motor usa a MET das durações.
    assert treino["comparison"]["reported"]["kcal"] is None
    assert treino["comparison"]["estimated"]["kcal"] == pytest.approx(training_kcal_met(80, 75))
    assert treino["comparison"]["used"]["kcal_nature"] == "met"


# ---------------------------------------------------------------------------
# Água e fome
# ---------------------------------------------------------------------------


def test_agua_e_fome_gravam_e_voltam_no_dia(db):
    cliente = _client()
    resposta = cliente.put(f"/nutrition/days/{HOJE.isoformat()}", json={"water_ml": 2500, "hunger": 3, "notes": "ok"})
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["day_log"] == {"water_ml": 2500.0, "hunger": 3, "notes": "ok", "updated_at": "2026-10-17T12:00:00"}
    assert cliente.get(f"/nutrition/days/{HOJE.isoformat()}").json()["day_log"]["hunger"] == 3
    limpo = cliente.put(f"/nutrition/days/{HOJE.isoformat()}", json={}).json()["day_log"]
    assert limpo["water_ml"] is None and limpo["hunger"] is None  # PUT substitui a linha


@pytest.mark.parametrize(
    ("corpo", "codigos"),
    [
        ({"hunger": 6}, [diary.ERR_INVALID_HUNGER]),
        ({"hunger": 2.5}, [diary.ERR_INVALID_HUNGER]),
        ({"water_ml": -1}, [diary.ERR_INVALID_WATER]),
        ({"water_ml": diary.WATER_ML_RANGE[1] + 1, "hunger": 0}, [diary.ERR_INVALID_WATER, diary.ERR_INVALID_HUNGER]),
    ],
)
def test_agua_e_fome_fora_da_faixa_do_motor_respondem_400(db, corpo, codigos):
    resposta = _client().put(f"/nutrition/days/{HOJE.isoformat()}", json=corpo)
    assert resposta.status_code == 400 and resposta.json()["detail"]["error"] == "invalid_day_log"
    assert _codigos(resposta.json()["detail"]["errors"]) == codigos
    assert db.day_logs == {}
    assert _client().put(f"/nutrition/days/{HOJE.isoformat()}", json={"x": 1}).status_code == 422


# ---------------------------------------------------------------------------
# Semana
# ---------------------------------------------------------------------------


def test_semana_com_metas_totais_banco_e_aderencia(db):
    cliente = _client()
    segunda = date(2026, 10, 12)
    for deslocamento in range(5):
        _registrar(cliente, segunda + timedelta(days=deslocamento), 1500, food_id=db.arroz)
    _registrar(cliente, segunda + timedelta(days=5), 1, food_id=db.sal)  # sábado: total incompleto
    resposta = cliente.get(f"/nutrition/weeks?start={(segunda + timedelta(days=3)).isoformat()}")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["start"] == segunda.isoformat() and corpo["end"] == (segunda + timedelta(days=6)).isoformat()
    dias = corpo["days"]
    assert [d["local_date"] for d in dias] == [(segunda + timedelta(days=i)).isoformat() for i in range(7)]
    # Seg a qua (12 a 14/10) congelados sem meta -> retroativas; qui a dom materializados pelo fluxo normal.
    assert [d["target_action"] for d in dias] == ["backfill"] * 4 + ["materialize"] * 3
    assert all(d["target"] is not None for d in dias)
    assert [d["target"]["backfilled"] for d in dias] == [True] * 4 + [False] * 3
    assert [d["logged"] for d in dias] == [True] * 5 + [False, False]
    assert [d["intake_incomplete"] for d in dias] == [False] * 5 + [True, False]
    assert dias[6]["totals"] is None  # domingo sem refeição: None, nunca 0
    relatorio = corpo["report"]
    assert relatorio["adherence"]["logged_days"] == 5 and relatorio["adherence"]["valid_week"] is True
    assert relatorio["budget"]["days_with_target"] == 7
    assert wb.LIMIT_INCOMPLETE_INTAKE_DAYS in _codigos(relatorio["limitations"])
    json.dumps(corpo)


def test_semana_padrao_e_a_de_hoje(db):
    corpo = _client().get("/nutrition/weeks").json()
    assert corpo["start"] == "2026-10-12" and corpo["today"] == HOJE.isoformat()


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------


def test_aluno_nao_le_nem_escreve_o_diario_de_terceiro(db):
    intruso = _client(user_id=OUTRO)
    assert intruso.get(f"/nutrition/days/{HOJE.isoformat()}?user_id={ALUNO}").status_code == 403
    assert intruso.get(f"/nutrition/weeks?user_id={ALUNO}").status_code == 403
    assert intruso.put(f"/nutrition/days/{HOJE.isoformat()}?user_id={ALUNO}", json={"hunger": 3}).status_code == 403
    assert db.targets == {} and db.day_logs == {}


@pytest.mark.parametrize("papel", ["admin", "personal_trainer"])
def test_admin_e_pt_veem_e_anotam_o_dia_do_aluno(db, papel):
    cliente = _client(papel, user_id=ADMIN)
    corpo = cliente.get(f"/nutrition/days/{HOJE.isoformat()}?user_id={ALUNO}").json()
    assert corpo["user"]["id"] == ALUNO and corpo["target"] is not None
    assert cliente.put(f"/nutrition/days/{HOJE.isoformat()}?user_id={ALUNO}", json={"water_ml": 1000}).status_code == 200
    assert db.day_logs[(ALUNO, HOJE)]["updated_by"] == ADMIN
    assert cliente.get(f"/nutrition/days/{HOJE.isoformat()}?user_id=999").status_code == 404
