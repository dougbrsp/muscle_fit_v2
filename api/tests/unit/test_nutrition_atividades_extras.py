"""Modo automático do gasto (`auto_intervals`, 2026-11-05): cardio, caminhada e outras atividades do Intervals.

Dono: `energy_expenditure` (`extra_activity_kcal`, `effective_tdee_basis`, `intervals_in_sync`) e
`intervals_activity_kind` (classe). Só este modo soma os extras; `explicit_training`, `included_in_factor` e os
modos das gramas manuais seguem como estavam. Os números vêm da medição em produção de 05/11."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app import intervals_activity_kind as kind
from app import nutrition_targets_service as svc
from app.domain.nutrition import DayContext, PlanParams, compute_daily_target
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.daily_target import SessionFacts, day_context_for
from tests.support.nutrition_fake import FakeNutritionDB

HOJE = date(2026, 11, 5)
ALUNO = 2
AUTO = ee.TdeeMode.AUTO_INTERVALS


def _extras(*atividades: tuple[str, float | None]) -> tuple[ee.ExtraActivity, ...]:
    return tuple(ee.ExtraActivity(kind=kind.classify(esporte), calories_kcal=kcal, label=esporte) for esporte, kcal in atividades)


# --- motor: soma dos extras ------------------------------------------------------------------------


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


def test_total_de_atividade_soma_treino_e_extra_e_treino_desconhecido_segue_desconhecido():
    assert ee.activity_total_kcal(428, 395) == pytest.approx(823)
    assert ee.activity_total_kcal(0.0, 427) == pytest.approx(427)
    assert ee.activity_total_kcal(None, 395) is None


# --- motor: frescor da sincronização e base do dia -------------------------------------------------


@pytest.mark.parametrize(
    ("conectado", "minutos", "esperado"),
    [
        (True, 0, True),
        (True, ee.INTERVALS_SYNC_MAX_AGE_MINUTES, True),
        (True, ee.INTERVALS_SYNC_MAX_AGE_MINUTES + 1, False),
        (True, None, False),  # conectado mas nunca sincronizou
        (True, -5, False),
        (False, 10, False),
        (False, None, False),
    ],
)
def test_sincronizacao_em_dia_so_dentro_do_limite(conectado, minutos, esperado):
    assert ee.intervals_in_sync(conectado, minutos) is esperado


def test_base_do_dia_nos_modos_classicos_nao_muda_e_no_automatico_depende_da_sincronizacao():
    perfil = ActivityLevel.ACTIVE
    for classico in (ee.TdeeMode.EXPLICIT_TRAINING, ee.TdeeMode.INCLUDED_IN_FACTOR):
        assert ee.effective_tdee_basis(classico, perfil, ActivityLevel.SEDENTARY, True) == (classico, perfil)
    assert ee.effective_tdee_basis(AUTO, perfil, ActivityLevel.MODERATE, True) == (ee.TdeeMode.EXPLICIT_TRAINING, ActivityLevel.MODERATE)
    assert ee.effective_tdee_basis(AUTO, perfil, None, True) == (ee.TdeeMode.EXPLICIT_TRAINING, ee.ROUTINE_ACTIVITY_LEVEL_DEFAULT)
    assert ee.effective_tdee_basis(AUTO, perfil, ActivityLevel.MODERATE, False) == (ee.TdeeMode.INCLUDED_IN_FACTOR, perfil)
    assert ee.effective_tdee_basis(AUTO, perfil, None, None) == (ee.TdeeMode.INCLUDED_IN_FACTOR, perfil)
    assert ee.ROUTINE_ACTIVITY_LEVEL_DEFAULT == ActivityLevel.LIGHT


def test_numeros_medidos_em_producao_tdee_automatico_e_fallback_e_o_explicito_segue():
    bmr = 2020.24
    rotina = ee.tdee(bmr, ActivityLevel.LIGHT, ee.TdeeMode.EXPLICIT_TRAINING, ee.activity_total_kcal(428, 395))
    assert rotina.kcal == pytest.approx(3449.31, abs=0.01)  # TMB x 1,3 + 428 + 395
    assert ee.tdee(2015.92, ActivityLevel.LIGHT, ee.TdeeMode.EXPLICIT_TRAINING, 0.0).kcal == pytest.approx(2620.70, abs=0.01)
    assert ee.tdee(bmr, ActivityLevel.ACTIVE, ee.TdeeMode.INCLUDED_IN_FACTOR, 428).kcal == pytest.approx(3484.91, abs=0.01)
    assert ee.tdee(bmr, ActivityLevel.ACTIVE, ee.TdeeMode.EXPLICIT_TRAINING, 428).kcal == pytest.approx(3458.36, abs=0.01)
    assert ee.rest_tdee(bmr, ActivityLevel.ACTIVE, AUTO, ActivityLevel.LIGHT).kcal == pytest.approx(bmr * 1.3)


# --- motor: dia ------------------------------------------------------------------------------------

SUJEITO = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.ACTIVE)
TMB = 1780.0


def _plano(modo=AUTO, **extra) -> PlanParams:
    return PlanParams(strategy_params=st.StrategyParams.defaults_for(st.Strategy.MAINTENANCE), tdee_mode=modo, **extra)


def _dia(sessoes=(), extras=(), sincronizado=True) -> DayContext:
    return day_context_for(HOJE, HOJE, sessoes, extras, intervals_in_sync=sincronizado)


FORCA = (SessionFacts(calories_kcal=428, duration_seconds=3600),)


def test_automatico_sincronizado_soma_rotina_forca_e_extras():
    meta = compute_daily_target(SUJEITO, _plano(routine_level=ActivityLevel.LIGHT), _dia(FORCA, _extras(("Walk", 130), ("VirtualRun", 265))))
    assert meta.tdee.mode == ee.TdeeMode.EXPLICIT_TRAINING and meta.tdee.activity_factor == pytest.approx(1.3)
    assert meta.extra_activity.kcal == pytest.approx(395)
    assert meta.tdee.kcal == pytest.approx(TMB * 1.3 + 428 + 395)


def test_automatico_sincronizado_sem_atividade_e_descanso_com_a_rotina():
    meta = compute_daily_target(SUJEITO, _plano(), _dia())  # rotina padrão (leve)
    assert meta.tdee.kcal == pytest.approx(TMB * 1.3) and meta.extra_activity.kcal == 0


def test_automatico_dia_so_com_cardio_nao_vira_dia_de_treino():
    contexto = _dia((), _extras(("VirtualRun", 427)))
    meta = compute_daily_target(SUJEITO, _plano(), contexto)
    assert contexto.is_training_day is False and meta.training_kcal is ee.NO_TRAINING_KCAL
    assert meta.tdee.kcal == pytest.approx(TMB * 1.3 + 427)


def test_automatico_sem_sincronizacao_usa_o_fator_com_exercicio_do_perfil_e_ignora_forca_e_extras():
    meta = compute_daily_target(SUJEITO, _plano(), _dia(FORCA, _extras(("Walk", 395)), sincronizado=False))
    assert meta.tdee.mode == ee.TdeeMode.INCLUDED_IN_FACTOR and meta.tdee.activity_factor == pytest.approx(1.725)
    assert meta.tdee.kcal == pytest.approx(TMB * 1.725) and meta.extra_activity.kcal == 0
    sem_info = compute_daily_target(SUJEITO, _plano(), day_context_for(HOJE, HOJE, FORCA))  # contexto sem o estado
    assert sem_info.tdee.kcal == pytest.approx(TMB * 1.725)


@pytest.mark.parametrize("modo", [ee.TdeeMode.EXPLICIT_TRAINING, ee.TdeeMode.INCLUDED_IN_FACTOR])
def test_modos_classicos_nao_somam_extras_nem_com_o_extra_no_contexto(modo):
    com = compute_daily_target(SUJEITO, _plano(modo), day_context_for(HOJE, HOJE, FORCA, _extras(("Walk", 395))))
    sem = compute_daily_target(SUJEITO, _plano(modo), day_context_for(HOJE, HOJE, FORCA))
    assert com.tdee.kcal == pytest.approx(sem.tdee.kcal) and com.extra_activity.kcal == 0


def test_dia_futuro_nao_tem_extra():
    amanha = HOJE + timedelta(days=1)
    contexto = day_context_for(amanha, HOJE, (), _extras(("VirtualRun", 427)), intervals_in_sync=True)
    assert compute_daily_target(SUJEITO, _plano(), contexto).extra_activity.kcal == 0


def test_o_hash_dos_modos_classicos_nao_muda():
    assert DayContext(day=HOJE).to_dict() == day_context_for(HOJE, HOJE).to_dict()
    assert "intervals_in_sync" not in DayContext(day=HOJE).to_dict() and "extra_activities" not in DayContext(day=HOJE).to_dict()
    com = day_context_for(HOJE, HOJE, (), _extras(("Walk", 51)), intervals_in_sync=True).to_dict()
    assert com["extra_activities"] == [{"kind": "cardio", "calories_kcal": 51.0, "label": "Walk"}] and com["intervals_in_sync"] is True


# --- gramas manuais no plano automático -------------------------------------------------------------


def _manual(modo_manual):
    return _plano(
        manual_macros=(160.0, 300.0, 60.0),
        manual_training_mode=modo_manual,
    )


def test_gramas_manuais_add_training_somam_o_total_so_no_dia_sincronizado_e_fixed_nunca():
    from app.domain.nutrition.manual_targets import ManualTrainingMode as M

    base = compute_daily_target(SUJEITO, _manual(M.ADD_TRAINING), _dia()).macros.carb_g
    com = compute_daily_target(SUJEITO, _manual(M.ADD_TRAINING), _dia(FORCA, _extras(("Walk", 200)))).macros.carb_g
    assert com == pytest.approx(base + (428 + 200) / 4)
    fallback = compute_daily_target(SUJEITO, _manual(M.ADD_TRAINING), _dia(FORCA, _extras(("Walk", 200)), sincronizado=False)).macros.carb_g
    assert fallback == pytest.approx(base)  # sem sincronização vale como `fixed`
    fixo = compute_daily_target(SUJEITO, _manual(M.FIXED), _dia(FORCA, _extras(("Walk", 200)))).macros.carb_g
    assert fixo == pytest.approx(base)


# --- serviço: carga e materialização ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fita_existe(monkeypatch):
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)


def _db(modo="auto_intervals", sincronizado=True, **plano) -> FakeNutritionDB:
    db = FakeNutritionDB()
    db.add_weight(ALUNO, HOJE - timedelta(days=30), 80)
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10), published_at=datetime(2026, 10, 7, 12),
                training_kcal_mode=modo, **plano)
    db.intervals_connected = sincronizado
    db.intervals_minutes_since_sync = 120 if sincronizado else None
    return db


def _linha(db, dia=HOJE):
    janela = svc.TargetWindow(date_from=dia, date_to=dia, create_until=dia)
    return {linha["local_date"]: linha for linha in svc.ensure_daily_targets(db.conn(), ALUNO, janela, today=HOJE)}[dia.isoformat()]


def test_servico_automatico_soma_o_extra_e_nao_cria_coluna_nova():
    sem = _db()
    com = _db()
    com.add_intervals_activity(ALUNO, HOJE, "VirtualRun", 427)
    com.add_intervals_activity(ALUNO, HOJE, "WeightTraining", 500)  # sem vínculo: força não soma por esta via
    com.add_intervals_activity(ALUNO, HOJE, "Walk", None)  # sem kcal: não soma
    base, linha = _linha(sem), _linha(com)
    assert linha["is_training_day"] is False and linha["training_kcal"] is None
    assert linha["tdee_kcal"] == pytest.approx(base["tdee_kcal"] + 427)
    assert linha["extra_activity"]["kcal"] == pytest.approx(427)
    gravada = com.targets[(ALUNO, HOJE)]
    assert gravada["inputs_json"]["day_context"]["extra_activities"][0]["calories_kcal"] == 427.0
    assert gravada["inputs_hash"] != sem.targets[(ALUNO, HOJE)]["inputs_hash"]  # a mudança recalcula o dia


def test_servico_automatico_sem_conexao_ou_com_sincronizacao_parada_cai_no_fator_com_exercicio():
    desconectado = _db(sincronizado=False)
    desconectado.add_intervals_activity(ALUNO, HOJE, "VirtualRun", 427)
    linha = _linha(desconectado)
    parado = _db()
    parado.intervals_minutes_since_sync = ee.INTERVALS_SYNC_MAX_AGE_MINUTES + 1
    parado.add_intervals_activity(ALUNO, HOJE, "VirtualRun", 427)
    assert linha["extra_activity"]["kcal"] == 0 and _linha(parado)["extra_activity"]["kcal"] == 0
    assert linha["tdee_kcal"] == pytest.approx(_linha(parado)["tdee_kcal"])  # os dois no fator com exercício do perfil
    assert desconectado.targets[(ALUNO, HOJE)]["inputs_json"]["day_context"]["intervals_in_sync"] is False


def test_servico_plano_classico_ignora_o_intervals_inteiro_e_mantem_o_hash():
    com = _db(modo="explicit_training")
    com.add_intervals_activity(ALUNO, HOJE, "VirtualRun", 427)
    sem = _db(modo="explicit_training", sincronizado=False)
    a, b = _linha(com), _linha(sem)
    assert a["tdee_kcal"] == pytest.approx(b["tdee_kcal"]) and a["extra_activity"]["kcal"] == 0
    contexto = com.targets[(ALUNO, HOJE)]["inputs_json"]["day_context"]
    assert "extra_activities" not in contexto and "intervals_in_sync" not in contexto
    assert com.targets[(ALUNO, HOJE)]["inputs_hash"] == sem.targets[(ALUNO, HOJE)]["inputs_hash"]


def test_servico_nao_le_atividade_de_outro_usuario_nem_de_outro_dia():
    db = _db()
    db.add_intervals_activity(3, HOJE, "VirtualRun", 999)
    db.add_intervals_activity(ALUNO, HOJE - timedelta(days=1), "VirtualRun", 111)
    assert _linha(db)["tdee_kcal"] == pytest.approx(_linha(_db())["tdee_kcal"])


def test_as_consultas_sao_parametrizadas_filtram_usuario_e_nao_classificam_com_like():
    sql = svc.EXTRA_ACTIVITIES_SQL
    assert sql.count("%s") == 3 and "ia.user_id = %s" in sql
    assert "LEFT JOIN activity_intervals_link" in sql and "l.id IS NULL" in sql and "LIKE" not in sql.upper()
    assert svc.INTERVALS_CONNECTION_SQL.count("%s") == 1 and "WHERE id = %s" in svc.INTERVALS_CONNECTION_SQL
    assert svc.INTERVALS_SYNC_AGE_SQL.count("%s") == 2 and svc.INTERVALS_SYNC_AGE_SQL.count("user_id = %s") == 2


def test_projecao_e_ia_usam_o_mesmo_total_pelo_mesmo_dono_no_automatico():
    db = _db()
    db.add_session(ALUNO, HOJE - timedelta(days=2), 428)
    db.add_intervals_activity(ALUNO, HOJE - timedelta(days=2), "Walk", 130)
    db.add_intervals_activity(ALUNO, HOJE - timedelta(days=3), "VirtualRun", 427)
    settings = svc.settings_from_row(db.plans[1])
    with db.conn() as conn, conn.cursor() as cur:
        entradas = svc.load_user_inputs(cur, ALUNO, HOJE - timedelta(days=ee_window()), HOJE, HOJE)
    passado = svc.past_training_kcal(entradas, settings, HOJE)
    assert passado[-2] == pytest.approx(558)
    assert passado[-3] == pytest.approx(427)
    classico = svc.settings_from_row({**db.plans[1], "training_kcal_mode": "explicit_training"})
    assert svc.past_training_kcal(entradas, classico, HOJE)[-3] == pytest.approx(0.0)  # o clássico não vê o cardio


def ee_window() -> int:
    from app.domain.nutrition.plan import PROJECTION_TRAINING_WINDOW_DAYS

    return PROJECTION_TRAINING_WINDOW_DAYS
