"""Materialização das metas diárias (`app/nutrition_targets_service.py`, L2b):
janela do motor (dia congelado nunca é escrito; meta nova só até
`create_until`), plano em vigor por dia (o substituído vale para o passado),
sem plano nada é materializado, séries por grandeza, kcal do treino das
sessões concluídas, `training_known` lido da coluna (decisão gravada pelo
motor) e idempotência sem regravar (hash das entradas, lote único).

Banco em memória: `tests/support/nutrition_fake.py` (responde os statements
exatos do módulo)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app import nutrition_targets_service as svc
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition.daily_target import LIMIT_TRAINING_UNKNOWN_FUTURE_DAY, TargetWindow
from app.domain.nutrition.plan import build_plan_settings
from tests.support.nutrition_fake import FakeNutritionDB

HOJE = date(2026, 10, 17)  # sábado
ALUNO = 2


@pytest.fixture(autouse=True)
def _fita_existe(monkeypatch):
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)


def _db_com_plano(**plano) -> FakeNutritionDB:
    db = FakeNutritionDB()
    db.add_weight(ALUNO, HOJE - timedelta(days=30), 80)
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10),
                published_at=datetime(2026, 10, 7, 12), **plano)
    return db


def _janela(de: date, ate: date, criar_ate: date | None = None) -> TargetWindow:
    return TargetWindow(date_from=de, date_to=ate, create_until=criar_ate or ate)


def _ensure(db, de, ate, *, hoje=HOJE, criar_ate=None, **kw):
    return svc.ensure_daily_targets(db.conn(), ALUNO, _janela(de, ate, criar_ate), today=hoje, **kw)


def test_sem_plano_publicado_nada_e_materializado():
    db = FakeNutritionDB()
    db.add_weight(ALUNO, HOJE, 80)
    db.add_plan(ALUNO, status="draft")
    assert _ensure(db, HOJE - timedelta(days=3), HOJE + timedelta(days=3)) == []
    assert db.writes == []


def test_so_os_dias_nao_congelados_sao_escritos_num_lote_so():
    db = _db_com_plano()
    linhas = _ensure(db, HOJE - timedelta(days=4), HOJE + timedelta(days=1))
    escritos = sorted(w.split()[1] for w in db.writes)
    # anteontem e antes ficam congelados (lag de 2 dias pelo sync do Intervals)
    assert escritos == [(HOJE + timedelta(days=d)).isoformat() for d in (-1, 0, 1)]
    assert db.batches == [3]  # um executemany (BatchExecuteStatement na Data API)
    assert [linha["local_date"] for linha in linhas] == escritos
    assert all(linha["frozen"] is False for linha in linhas)


def test_meta_nova_so_ate_create_until_e_existente_sempre_atualiza():
    db = _db_com_plano()
    longe = HOJE + timedelta(days=20)
    _ensure(db, longe, longe)  # cria a meta de daqui a 20 dias
    db.writes.clear()
    db.plans[1]["protein_g_per_kg"] = Decimal("2.20")  # muda o plano: as entradas mudam
    _ensure(db, HOJE, longe, criar_ate=HOJE + timedelta(days=1))
    escritos = {w.split()[1] for w in db.writes}
    assert longe.isoformat() in escritos  # existente: atualizada mesmo além de create_until
    assert (HOJE + timedelta(days=5)).isoformat() not in escritos  # nova além de create_until: não criada
    assert {HOJE.isoformat(), (HOJE + timedelta(days=1)).isoformat()} <= escritos


def test_dia_congelado_existente_fica_intacto():
    db = _db_com_plano()
    anteontem = HOJE - timedelta(days=2)
    db.targets[(ALUNO, anteontem)] = {
        "user_id": ALUNO, "local_date": anteontem, "plan_id": 99, "day_kind": "normal", "is_training_day": 0,
        "training_known": 0, "phase_kind": None, "kcal_target": Decimal("1234.00"), "protein_g": None, "carbs_g": None,
        "fat_g": None, "fiber_g": None, "water_ml": None, "bmr_kcal": None, "bmr_formula": "auto", "tdee_kcal": None,
        "training_kcal": None, "training_kcal_nature": None, "limitations_json": "[]", "inputs_hash": None,
        "engine_version": "nutrition-v1", "computed_at": datetime(2026, 10, 15, 8),
    }
    linhas = _ensure(db, anteontem, HOJE)
    assert linhas[0]["kcal"] == 1234.0 and linhas[0]["frozen"] is True and linhas[0]["plan_id"] == 99
    assert f"upsert {anteontem.isoformat()}" not in db.writes


def test_idempotente_sem_regravar():
    db = _db_com_plano()
    primeira = _ensure(db, HOJE, HOJE + timedelta(days=2))
    db.writes.clear()
    segunda = _ensure(db, HOJE, HOJE + timedelta(days=2))
    assert primeira == segunda
    assert db.writes == []  # plano, versão do motor e hash das entradas iguais: nada é regravado


def test_training_known_vem_da_coluna_gravada_pelo_motor():
    """Terça materializada na segunda (dia futuro: treino desconhecido), lida na
    quinta já congelada: continua `training_known=False` com o aviso, sem a
    rota recalcular comparando a data com hoje."""
    db = _db_com_plano()
    segunda, terca, quinta = date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 15)
    db.add_weight(ALUNO, segunda - timedelta(days=1), 80)
    _ensure(db, terca, terca, hoje=segunda)
    linha = _ensure(db, terca, terca, hoje=quinta)[0]
    assert linha["frozen"] is True
    assert linha["training_known"] is False
    assert LIMIT_TRAINING_UNKNOWN_FUTURE_DAY in [item["code"] for item in linha["limitations"]]


def test_plano_substituido_vale_para_o_passado_e_o_novo_de_hoje_em_diante():
    db = _db_com_plano()
    antigo = next(iter(db.plans))
    db.plans[antigo]["superseded_at"] = datetime(2026, 10, 17, 10)
    novo = db.add_plan(ALUNO, status="published", strategy="bulking", effective_from=HOJE,
                       published_at=datetime(2026, 10, 17, 10), surplus_share=Decimal("0.1000"),
                       carb_min_g_per_kg=Decimal("3.00"))
    linhas = {linha["local_date"]: linha for linha in _ensure(db, HOJE - timedelta(days=1), HOJE)}
    assert linhas[(HOJE - timedelta(days=1)).isoformat()]["plan_id"] == antigo
    assert linhas[HOJE.isoformat()]["plan_id"] == novo
    assert linhas[HOJE.isoformat()]["kcal"] > linhas[(HOJE - timedelta(days=1)).isoformat()]["kcal"]


def test_kcal_do_treino_vem_da_sessao_concluida_e_dia_futuro_avisa():
    db = _db_com_plano()
    db.add_session(ALUNO, HOJE - timedelta(days=1), 412.37)
    linhas = {linha["local_date"]: linha for linha in _ensure(db, HOJE - timedelta(days=1), HOJE + timedelta(days=1))}
    ontem = linhas[(HOJE - timedelta(days=1)).isoformat()]
    assert ontem["is_training_day"] is True and ontem["training_known"] is True
    assert ontem["training_kcal"] == pytest.approx(412.37)
    assert ontem["training_kcal_nature"] == ee.TrainingKcalNature.REPORTED.value
    amanha = linhas[(HOJE + timedelta(days=1)).isoformat()]
    assert amanha["training_kcal"] is None and amanha["training_known"] is False
    assert LIMIT_TRAINING_UNKNOWN_FUTURE_DAY in [item["code"] for item in amanha["limitations"]]
    assert all(item["label"] for item in amanha["limitations"])


def test_gravacao_copia_o_motor_e_guarda_as_entradas():
    db = _db_com_plano()
    _ensure(db, HOJE, HOJE)
    linha = db.targets[(ALUNO, HOJE)]
    entradas = linha["inputs_json"]
    assert entradas["subject"]["sex"] == "male" and entradas["subject"]["weight_kg"] == 80.0
    assert entradas["plan"]["strategy"] == "maintenance"
    assert entradas["day_context"]["training_known"] is True
    assert linha["engine_version"] == "nutrition-v1" and linha["training_known"] == 1
    assert len(linha["inputs_hash"]) == 64
    assert json.loads(linha["limitations_json"]) == []


def test_aluno_sem_dados_grava_meta_nao_calculavel_com_limitacoes():
    db = FakeNutritionDB()
    db.add_plan(3, status="published", effective_from=HOJE)
    linhas = svc.ensure_daily_targets(db.conn(), 3, _janela(HOJE, HOJE), today=HOJE)
    assert linhas[0]["kcal"] is None and linhas[0]["protein_g"] is None  # None, nunca 0
    codigos = {item["code"] for item in linhas[0]["limitations"]}
    assert {"missing_sex", "missing_or_invalid_height", "missing_age", "missing_weight"} <= codigos


def test_series_por_grandeza_e_historico_inteiro():
    db = FakeNutritionDB()
    # Bioimpedância antiga e completa; depois uma com %G inválido; depois só pesagens.
    db.add_weight(ALUNO, date(2026, 1, 10), 90, body_fat_percent=Decimal("20.0"),
                  fat_free_mass_kg=Decimal("72.0"), bmr_kcal=Decimal("1800"))
    db.add_weight(ALUNO, date(2026, 6, 1), 88, body_fat_percent=Decimal("95.0"))
    db.add_weight(ALUNO, date(2026, 10, 1), 85)
    db.add_weight(ALUNO, HOJE, 84)
    db.measurements.append({"user_id": ALUNO, "site": "neck", "local_date": date(2026, 9, 1), "value_cm": Decimal("40.0")})
    entradas = svc.load_user_inputs(db.conn().cursor(), ALUNO, HOJE - timedelta(days=1), HOJE, HOJE)
    ontem = entradas.series.readings_on(HOJE - timedelta(days=1))
    assert ontem.weight.value == 85.0 and ontem.weight.local_date == date(2026, 10, 1)
    # O %G inválido de junho não esconde o %G, a MLG e a TMB válidos de janeiro.
    assert ontem.bioimpedance_body_fat.value == 20.0
    assert ontem.bioimpedance_ffm.value == 72.0 and ontem.scale_bmr.value == 1800.0
    assert ontem.neck.value == 40.0
    assert entradas.series.readings_on(HOJE).weight.value == 84.0


def test_sessoes_so_ate_hoje():
    db = FakeNutritionDB()
    db.add_session(ALUNO, HOJE + timedelta(days=1), 300)
    db.add_session(ALUNO, HOJE, 250)
    entradas = svc.load_user_inputs(db.conn().cursor(), ALUNO, HOJE, HOJE + timedelta(days=2), HOJE)
    assert list(entradas.sessions) == [HOJE]


def test_settings_da_linha_ida_e_volta():
    db = _db_com_plano(strategy="cutting", weekly_rate_share=Decimal("0.0070"), protein_g_per_kg=Decimal("2.60"),
                       fat_kcal_share=Decimal("0.2500"), meals_per_day=4, activity_level="light", advanced=0)
    linha = next(iter(db.plans.values()))
    settings = svc.settings_from_row(linha)
    esperado = build_plan_settings(strategy="cutting", weekly_rate_share=0.007, protein_g_per_kg=2.6,
                                   fat_kcal_share=0.25, meals_per_day=4, activity_level="light").settings
    assert settings == esperado


def test_impressao_muda_com_plano_entradas_ou_plano_id():
    db = _db_com_plano()
    entradas = svc.load_user_inputs(db.conn().cursor(), ALUNO, HOJE, HOJE, HOJE)
    settings = svc.settings_from_row(next(iter(db.plans.values())))
    comp = svc.compute_day(entradas, settings, HOJE, HOJE)
    base = svc.inputs_fingerprint(1, comp)
    assert base == svc.inputs_fingerprint(1, svc.compute_day(entradas, settings, HOJE, HOJE))
    assert base != svc.inputs_fingerprint(2, comp)
    outro = build_plan_settings(strategy="bulking").settings
    assert base != svc.inputs_fingerprint(1, svc.compute_day(entradas, outro, HOJE, HOJE))


def test_semana_alinhada_aos_dias():
    dias = [HOJE + timedelta(days=i) for i in range(3)]
    linhas = [{"local_date": dias[1].isoformat(), "kcal": 1.0}]
    assert svc.week_rows(linhas, dias) == [None, linhas[0], None]
