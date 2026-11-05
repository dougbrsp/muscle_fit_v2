"""Cardio, caminhada e outras atividades do Intervals no gasto do dia (2026-11-05).

Dono: `energy_expenditure.extra_activity_kcal` (soma) e `intervals_activity_kind` (classe). Os números dos dias vêm
da medição em produção de 05/11: força nunca entra por esta via; sem kcal não soma nem estima; o extra não faz
o dia virar "Treino" nem mexe na regra de MET da força."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app import intervals_activity_kind as kind
from app import nutrition_targets_service as svc
from app.domain.nutrition import DayContext, PlanParams, compute_daily_target
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.daily_target import day_context_for
from tests.support.nutrition_fake import FakeNutritionDB

HOJE = date(2026, 11, 5)
ALUNO = 2


def _extras(*atividades: tuple[str, float | None]) -> tuple[ee.ExtraActivity, ...]:
    return tuple(ee.ExtraActivity(kind=kind.classify(esporte), calories_kcal=kcal, label=esporte) for esporte, kcal in atividades)


def test_o_vocabulario_do_motor_bate_com_o_dono_da_classificacao():
    assert ee.EXTRA_ACTIVITY_KINDS == {kind.CARDIO, kind.OTHER}
    assert kind.STRENGTH not in ee.EXTRA_ACTIVITY_KINDS


@pytest.mark.parametrize(
    ("dia", "atividades", "extra"),
    [
        ("05/11: Walk 51 + Walk 79 + VirtualRun 265", (("Walk", 51), ("Walk", 79), ("VirtualRun", 265)), 395),
        ("30/10: Elliptical 25 + VirtualRun 230", (("Elliptical", 25), ("VirtualRun", 230)), 255),
        ("01/11: só VirtualRun 427", (("VirtualRun", 427),), 427),
        ("02/11: VirtualRun 36 + RockClimbing 206 (OTHER entra)", (("VirtualRun", 36), ("RockClimbing", 206)), 242),
        ("04/11: sem atividades", (), 0),
        ("WeightTraining sem vínculo não soma", (("WeightTraining", 428), ("StrengthTraining", 300)), 0),
        ("sem kcal não soma nem estima", (("VirtualRun", None), ("Walk", 0), ("Walk", 40)), 40),
    ],
)
def test_extra_dos_dias_medidos_em_producao(dia, atividades, extra):
    resultado = ee.extra_activity_kcal(_extras(*atividades))
    assert resultado.kcal == pytest.approx(extra), dia
    assert (resultado.kcal_nature is None) == (extra == 0)
    if extra:
        assert resultado.kcal_nature == ee.TrainingKcalNature.REPORTED


def test_so_o_dono_classifica_walk_e_climbing_nao_viram_forca():
    assert kind.classify("Walk") == kind.CARDIO and kind.classify("VirtualRun") == kind.CARDIO
    assert kind.classify("RockClimbing") == kind.OTHER and kind.classify("WeightTraining") == kind.STRENGTH


def test_total_de_atividade_soma_treino_e_extra_e_treino_desconhecido_segue_desconhecido():
    assert ee.activity_total_kcal(428, 395) == pytest.approx(823)
    assert ee.activity_total_kcal(0.0, 427) == pytest.approx(427)
    assert ee.activity_total_kcal(428, 0) == pytest.approx(428)
    assert ee.activity_total_kcal(None, 395) is None


def test_tdee_de_05_11_sobe_do_treino_ao_total_com_fator_1_5():
    bmr = 2020.24
    sem = ee.tdee(bmr, ActivityLevel.ACTIVE, ee.TdeeMode.EXPLICIT_TRAINING, 428)
    com = ee.tdee(bmr, ActivityLevel.ACTIVE, ee.TdeeMode.EXPLICIT_TRAINING, ee.activity_total_kcal(428, 395))
    assert sem.kcal == pytest.approx(3458.36, abs=0.01) and com.kcal == pytest.approx(3853.36, abs=0.01)


SUJEITO = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.MODERATE)
TMB = 1780.0


def _plano(**extra) -> PlanParams:
    return PlanParams(strategy_params=st.StrategyParams.defaults_for(st.Strategy.MAINTENANCE), **extra)


def test_dia_so_com_cardio_soma_ao_gasto_mas_nao_vira_dia_de_treino():
    contexto = day_context_for(HOJE, HOJE, (), _extras(("VirtualRun", 427)))
    meta = compute_daily_target(SUJEITO, _plano(), contexto)
    assert contexto.is_training_day is False and meta.training_kcal is ee.NO_TRAINING_KCAL
    assert meta.extra_activity.kcal == pytest.approx(427)
    assert meta.tdee.kcal == pytest.approx(TMB * 1.4 + 427)
    assert meta.kcal_target.kcal == pytest.approx(TMB * 1.4 + 427)  # manutenção acompanha o gasto


def test_forca_mais_cardio_soma_os_dois_e_a_regra_de_met_da_forca_nao_muda():
    sessoes = (svc.SessionFacts(calories_kcal=None, duration_seconds=3600),)  # força sem kcal: cai para MET
    so_forca = compute_daily_target(SUJEITO, _plano(), day_context_for(HOJE, HOJE, sessoes))
    com_cardio = compute_daily_target(SUJEITO, _plano(), day_context_for(HOJE, HOJE, sessoes, _extras(("Walk", 147))))
    assert com_cardio.training_kcal.kcal_nature == ee.TrainingKcalNature.MET  # o extra não vira "informada" da força
    assert com_cardio.training_kcal.kcal == pytest.approx(so_forca.training_kcal.kcal)
    assert com_cardio.tdee.kcal == pytest.approx(so_forca.tdee.kcal + 147)


def test_modo_embutido_no_fator_ignora_o_extra_como_ignora_o_treino():
    contexto = day_context_for(HOJE, HOJE, (), _extras(("VirtualRun", 427)))
    embutido = compute_daily_target(SUJEITO, _plano(tdee_mode=ee.TdeeMode.INCLUDED_IN_FACTOR), contexto)
    sem_extra = compute_daily_target(SUJEITO, _plano(tdee_mode=ee.TdeeMode.INCLUDED_IN_FACTOR), DayContext(day=HOJE))
    assert embutido.tdee.kcal == pytest.approx(sem_extra.tdee.kcal)


def test_dia_futuro_nao_tem_extra():
    amanha = HOJE + timedelta(days=1)
    contexto = day_context_for(amanha, HOJE, (), _extras(("VirtualRun", 427)))
    assert compute_daily_target(SUJEITO, _plano(), contexto).extra_activity.kcal == 0


def test_o_hash_so_muda_em_dia_com_extra():
    sem = DayContext(day=HOJE).to_dict()
    assert "extra_activities" not in sem  # dia sem atividade extra mantém as entradas (e o hash) de antes
    com = day_context_for(HOJE, HOJE, (), _extras(("Walk", 51))).to_dict()
    assert com["extra_activities"] == [{"kind": "cardio", "calories_kcal": 51.0, "label": "Walk"}]


# --- serviço: carga e materialização -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fita_existe(monkeypatch):
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)


def _db() -> FakeNutritionDB:
    db = FakeNutritionDB()
    db.add_weight(ALUNO, HOJE - timedelta(days=30), 80)
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10), published_at=datetime(2026, 10, 7, 12))
    return db


def _linhas(db, dia):
    janela = svc.TargetWindow(date_from=dia, date_to=dia, create_until=dia)
    return {linha["local_date"]: linha for linha in svc.ensure_daily_targets(db.conn(), ALUNO, janela, today=HOJE)}


def test_servico_materializa_o_gasto_com_o_extra_sem_virar_dia_de_treino_e_sem_coluna_nova():
    sem = _db()
    com = _db()
    com.add_intervals_activity(ALUNO, HOJE, "VirtualRun", 427)
    com.add_intervals_activity(ALUNO, HOJE, "WeightTraining", 500)  # sem vínculo: força não soma por esta via
    com.add_intervals_activity(ALUNO, HOJE, "Walk", None)  # sem kcal: não soma
    base = _linhas(sem, HOJE)[HOJE.isoformat()]
    linha = _linhas(com, HOJE)[HOJE.isoformat()]
    assert linha["is_training_day"] is False and linha["training_kcal"] is None
    assert linha["tdee_kcal"] == pytest.approx(base["tdee_kcal"] + 427)
    gravada = com.targets[(ALUNO, HOJE)]
    assert gravada["inputs_json"]["day_context"]["extra_activities"][0]["calories_kcal"] == 427.0
    assert gravada["inputs_hash"] != sem.targets[(ALUNO, HOJE)]["inputs_hash"]  # a mudança recalcula o dia


def test_servico_nao_le_atividade_de_outro_usuario_nem_de_outro_dia():
    db = _db()
    db.add_intervals_activity(3, HOJE, "VirtualRun", 999)
    db.add_intervals_activity(ALUNO, HOJE - timedelta(days=1), "VirtualRun", 111)
    base = _linhas(_db(), HOJE)[HOJE.isoformat()]
    assert _linhas(db, HOJE)[HOJE.isoformat()]["tdee_kcal"] == pytest.approx(base["tdee_kcal"])


def test_a_consulta_e_parametrizada_filtra_usuario_e_exclui_as_vinculadas_sem_like():
    sql = svc.EXTRA_ACTIVITIES_SQL
    assert sql.count("%s") == 3 and "ia.user_id = %s" in sql
    assert "LEFT JOIN activity_intervals_link" in sql and "l.id IS NULL" in sql
    assert "LIKE" not in sql.upper()


def test_projecao_e_ia_usam_o_mesmo_total_de_atividade_pelo_mesmo_dono():
    db = _db()
    db.add_session(ALUNO, HOJE - timedelta(days=2), 428)
    db.add_intervals_activity(ALUNO, HOJE - timedelta(days=2), "Walk", 130)  # força 428 + extra 130
    db.add_intervals_activity(ALUNO, HOJE - timedelta(days=3), "VirtualRun", 427)  # só cardio
    plano = db.plans[1]
    settings = svc.settings_from_row(plano)
    with db.conn() as conn, conn.cursor() as cur:
        entradas = svc.load_user_inputs(cur, ALUNO, HOJE - timedelta(days=ee_window()), HOJE, HOJE)
    passado = svc.past_training_kcal(entradas, settings, HOJE)
    assert passado[-2] == pytest.approx(558)  # 428 + 130 (índice 0 = há N dias; -1 = ontem)
    assert passado[-3] == pytest.approx(427)
    assert sum(1 for v in passado if v == 0.0) == len(passado) - 2


def ee_window() -> int:
    from app.domain.nutrition.plan import PROJECTION_TRAINING_WINDOW_DAYS

    return PROJECTION_TRAINING_WINDOW_DAYS
