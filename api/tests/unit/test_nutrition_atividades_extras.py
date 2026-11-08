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
from app.domain.nutrition import activity_energy
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.daily_target import SessionFacts, day_context_for
from tests.support.nutrition_fake import FakeNutritionDB

HOJE = date(2026, 11, 5)
ALUNO = 2
AUTO = ee.TdeeMode.AUTO_INTERVALS
TMB_FAKE = 1750.0  # TMB do aluno do `FakeNutritionDB` (Mifflin do perfil e do peso dele)


def _extras(*atividades: tuple[str, float | None]) -> tuple[ee.ExtraActivity, ...]:
    return tuple(ee.ExtraActivity(kind=kind.classify(esporte), calories_kcal=kcal, label=esporte) for esporte, kcal in atividades)


# --- motor: soma dos extras ------------------------------------------------------------------------


def test_o_tipo_so_rotula_e_nao_filtra_o_que_soma():
    """2026-11-08: tudo que entra pelo Intervals conta; a classe do dono da classificação é só exibição."""
    assert not hasattr(ee, "EXTRA_ACTIVITY_KINDS")
    tipos = {kind.classify(esporte) for esporte in ("Walk", "WeightTraining", "RockClimbing")}
    assert tipos == {kind.CARDIO, kind.STRENGTH, kind.OTHER}
    resultado = ee.extra_activity_kcal(_extras(("Walk", 100), ("WeightTraining", 200), ("RockClimbing", 50), ("TipoNovoXYZ", 30)))
    assert resultado.kcal == pytest.approx(380)


@pytest.mark.parametrize(
    ("dia", "atividades", "extra"),
    [
        ("05/11: Walk 51 + Walk 79 + VirtualRun 265", (("Walk", 51), ("Walk", 79), ("VirtualRun", 265)), 395),
        ("30/10: Elliptical 25 + VirtualRun 230", (("Elliptical", 25), ("VirtualRun", 230)), 255),
        ("01/11: só VirtualRun 427", (("VirtualRun", 427),), 427),
        ("02/11: VirtualRun 36 + RockClimbing 206 (OTHER entra)", (("VirtualRun", 36), ("RockClimbing", 206)), 242),
        ("04/11: sem atividades", (), 0),
        ("musculação do Intervals sem vínculo com sessão do app conta", (("WeightTraining", 428), ("StrengthTraining", 300)), 728),
        ("Morning Walk e tipo desconhecido contam", (("Morning Walk", 60), ("TipoNovoXYZ", 25)), 85),
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
    # Força líquida do repouso (60 min); os extras do exemplo não têm duração e entram como vieram (com a limitação).
    forca_liquida = 428 - TMB / 1440 * 60
    assert meta.training_kcal.kcal == pytest.approx(forca_liquida)
    assert meta.tdee.kcal == pytest.approx(TMB * 1.3 + forca_liquida + 395)
    assert ee.LIMIT_REST_NOT_DISCOUNTED in meta.limitations


def test_extras_com_duracao_descontam_o_repouso_da_janela_e_sem_duracao_entram_como_vieram():
    com_duracao = (
        ee.ExtraActivity(kind="cardio", calories_kcal=300.0, duration_seconds=1800),
        ee.ExtraActivity(kind="other", calories_kcal=100.0, duration_seconds=600),
    )
    soma = ee.extra_activity_kcal(com_duracao, TMB)
    desconto = TMB / 1440 * 40
    assert soma.kcal == pytest.approx(400 - desconto) and soma.gross_kcal == 400.0
    assert soma.resting_discount_kcal == pytest.approx(desconto) and soma.limitations == ()
    assert soma.to_dict()["gross_kcal"] == 400.0
    misto = ee.extra_activity_kcal((*com_duracao, ee.ExtraActivity(kind="other", calories_kcal=50.0)), TMB)
    assert misto.kcal == pytest.approx(450 - desconto) and misto.limitations == (ee.LIMIT_REST_NOT_DISCOUNTED,)
    pequena = ee.extra_activity_kcal((ee.ExtraActivity(kind="cardio", calories_kcal=20.0, duration_seconds=3600),), TMB)
    assert pequena.kcal == 0.0  # piso 0
    ativa = ee.extra_activity_kcal(com_duracao, TMB, activity_energy.CalorieMode.ACTIVE)
    assert ativa.kcal == 400.0 and ativa.resting_discount_kcal == 0.0
    assert "gross_kcal" not in ativa.to_dict()


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


def test_embutido_nao_soma_extras_nem_com_o_extra_no_contexto():
    modo = ee.TdeeMode.INCLUDED_IN_FACTOR
    com = compute_daily_target(SUJEITO, _plano(modo), day_context_for(HOJE, HOJE, FORCA, _extras(("Walk", 395))))
    sem = compute_daily_target(SUJEITO, _plano(modo), day_context_for(HOJE, HOJE, FORCA))
    assert com.tdee.kcal == pytest.approx(sem.tdee.kcal) and com.extra_activity.kcal == 0


def test_explicito_soma_os_extras_como_o_automatico_sincronizado():
    """2026-11-08: o fator de não-exercício não cobre exercício; o explícito soma o cardio do Intervals (ADR 0008)."""
    extras = (ee.ExtraActivity(kind="cardio", calories_kcal=436, duration_seconds=2601),)
    contexto = day_context_for(HOJE, HOJE, FORCA, extras)  # sem `intervals_in_sync`: não se aplica ao explícito
    explicito = compute_daily_target(SUJEITO, _plano(ee.TdeeMode.EXPLICIT_TRAINING), contexto)
    liquido = 436 - TMB / 1440 * 2601 / 60
    assert explicito.extra_activity.kcal == pytest.approx(liquido)
    forca = 428 - TMB / 1440 * 60
    assert explicito.tdee.kcal == pytest.approx(explicito.bmr.kcal * explicito.tdee.activity_factor + forca + liquido)
    auto = compute_daily_target(SUJEITO, _plano(), day_context_for(HOJE, HOJE, FORCA, extras, intervals_in_sync=True))
    assert auto.extra_activity.kcal == pytest.approx(explicito.extra_activity.kcal)
    assert explicito.training_kcal.kcal == pytest.approx(forca)  # força só pelas sessões: não duplica


def test_explicito_dia_so_de_cardio_inclui_o_gasto_sem_virar_dia_de_treino():
    extras = (ee.ExtraActivity(kind="cardio", calories_kcal=436, duration_seconds=2601),)
    contexto = day_context_for(HOJE, HOJE, (), extras)
    meta = compute_daily_target(SUJEITO, _plano(ee.TdeeMode.EXPLICIT_TRAINING), contexto)
    liquido = 436 - meta.bmr.kcal / 1440 * 2601 / 60
    assert contexto.is_training_day is False and meta.training_kcal is ee.NO_TRAINING_KCAL
    assert meta.tdee.kcal == pytest.approx(meta.bmr.kcal * meta.tdee.activity_factor + liquido)
    assert meta.day_kind == compute_daily_target(SUJEITO, _plano(ee.TdeeMode.EXPLICIT_TRAINING), day_context_for(HOJE, HOJE)).day_kind
    quebra = ee.tdee_breakdown(meta.tdee, meta.extra_activity.kcal, ee.TdeeBasis.CLASSIC)
    partes = {p.code: p.kcal for p in quebra.parts}
    assert "training" not in partes and partes["extras"] == round(liquido)
    assert sum(partes.values()) == quebra.total_kcal == round(meta.tdee.kcal)


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
    assert com == pytest.approx(base + (428 - TMB / 1440 * 60 + 200) / 4)
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
    com.add_intervals_activity(ALUNO, HOJE, "WeightTraining", 500)  # sem vínculo com sessão do app: conta (qualquer tipo)
    com.add_intervals_activity(ALUNO, HOJE, "WeightTraining", 700, linked=True)  # vinculada: é a sessão do app, não vem aqui
    com.add_intervals_activity(ALUNO, HOJE, "Walk", None)  # sem kcal: não soma e não é estimada
    base, linha = _linha(sem), _linha(com)
    assert linha["is_training_day"] is False and linha["training_kcal"] is None
    assert linha["tdee_kcal"] == pytest.approx(base["tdee_kcal"] + 927)
    assert linha["extra_activity"]["kcal"] == pytest.approx(927)
    assert ee.LIMIT_ACTIVITY_WITHOUT_KCAL in [i["code"] for i in linha["limitations"]]
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


def test_servico_plano_embutido_ignora_o_intervals_inteiro_e_mantem_o_hash():
    com = _db(modo="included_in_factor")
    com.add_intervals_activity(ALUNO, HOJE, "VirtualRun", 427)
    sem = _db(modo="included_in_factor", sincronizado=False)
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
    # A prova de que o job rodou é só a de ATIVIDADES; wellness (manual, refresh) não prova nada.
    assert svc.INTERVALS_SYNC_AGE_SQL.count("%s") == 1 and "FROM intervals_activities WHERE user_id = %s" in svc.INTERVALS_SYNC_AGE_SQL
    assert "wellness" not in svc.INTERVALS_SYNC_AGE_SQL


def test_projecao_e_ia_usam_o_mesmo_total_pelo_mesmo_dono_no_automatico():
    db = _db()
    db.add_session(ALUNO, HOJE - timedelta(days=2), 428)
    db.add_intervals_activity(ALUNO, HOJE - timedelta(days=2), "Walk", 130)
    db.add_intervals_activity(ALUNO, HOJE - timedelta(days=3), "VirtualRun", 427)
    settings = svc.settings_from_row(db.plans[1])
    with db.conn() as conn, conn.cursor() as cur:
        entradas = svc.load_user_inputs(cur, ALUNO, HOJE - timedelta(days=ee_window()), HOJE, HOJE, [settings])
    passado = svc.past_training_kcal(entradas, settings, HOJE)
    assert passado[-2] == pytest.approx(558 - TMB_FAKE / 1440 * 60)  # força líquida do repouso; o extra sem duração, como veio
    assert passado[-3] == pytest.approx(427)
    embutido = svc.settings_from_row({**db.plans[1], "training_kcal_mode": "included_in_factor"})
    assert svc.past_training_kcal(entradas, embutido, HOJE)[-3] == pytest.approx(0.0)  # o embutido não vê o cardio
    explicito = svc.settings_from_row({**db.plans[1], "training_kcal_mode": "explicit_training"})
    assert svc.past_training_kcal(entradas, explicito, HOJE)[-3] == pytest.approx(427)  # o explícito soma o cardio


def ee_window() -> int:
    from app.domain.nutrition.plan import PROJECTION_TRAINING_WINDOW_DAYS

    return PROJECTION_TRAINING_WINDOW_DAYS


# --- rodada de correção: P2, P3, P4, P6, P7, P9 ----------------------------------------------------------


def _consultas(db, plans):
    visto: list[str] = []
    with db.conn() as conn, conn.cursor() as cur:
        original = cur.execute

        def espiar(sql, params=None):
            visto.append(sql)
            return original(sql, params)

        cur.execute = espiar
        entradas = svc.load_user_inputs(cur, ALUNO, HOJE - timedelta(days=3), HOJE, HOJE, plans)
    return entradas, visto


def test_plano_explicito_so_le_os_extras_o_embutido_nada_e_o_automatico_tudo():
    novas = (svc.INTERVALS_CONNECTION_SQL, svc.INTERVALS_SYNC_AGE_SQL, svc.EXTRA_ACTIVITIES_SQL)
    explicito = _db(modo="explicit_training")
    entradas, visto = _consultas(explicito, [svc.settings_from_row(explicito.plans[1])])
    assert svc.EXTRA_ACTIVITIES_SQL in visto and entradas.intervals_loaded is True
    assert svc.INTERVALS_CONNECTION_SQL not in visto and svc.INTERVALS_SYNC_AGE_SQL not in visto  # sem prova de sync
    classico = _db(modo="included_in_factor")
    entradas, visto = _consultas(classico, [svc.settings_from_row(classico.plans[1])])
    assert not [sql for sql in visto if sql in novas] and entradas.intervals_loaded is False
    assert not [sql for sql in _consultas(classico, [])[1] if sql in novas]  # sem plano nenhum, idem
    automatico = _db()
    entradas, visto = _consultas(automatico, [svc.settings_from_row(automatico.plans[1])])
    assert set(novas) <= set(visto) and entradas.intervals_loaded is True and entradas.intervals_in_sync is True


def test_so_a_sincronizacao_de_atividades_prova_frescor_wellness_nao():
    # Conectado, mas a última sincronização de ATIVIDADES passou do limite: fallback, mesmo que haja wellness recente.
    db = _db()
    db.intervals_minutes_since_sync = ee.INTERVALS_SYNC_MAX_AGE_MINUTES + 60
    entradas, _ = _consultas(db, [svc.settings_from_row(db.plans[1])])
    assert entradas.intervals_in_sync is False
    sem_atividade = _db()
    sem_atividade.intervals_minutes_since_sync = None  # nenhuma atividade na janela do job: sem prova
    entradas, _ = _consultas(sem_atividade, [svc.settings_from_row(sem_atividade.plans[1])])
    assert entradas.intervals_in_sync is False


def test_referencias_do_plano_automatico_usam_a_rotina_e_nao_o_fallback_com_exercicio():
    from app.domain.nutrition import weekly_checkin as wc
    from app.domain.nutrition.daily_target import rest_day_context

    assert rest_day_context(AUTO, HOJE).intervals_in_sync is True
    assert rest_day_context(ee.TdeeMode.EXPLICIT_TRAINING, HOJE).intervals_in_sync is None
    perfil_active = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.ACTIVE)
    sem_nivel = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=None)
    settings = svc.settings_from_row(_db().plans[1])
    for sujeito in (perfil_active, sem_nivel):
        macros = wc._macros_of_day(settings, type("I", (), {"subject": sujeito, "ffm_age_days": None, "scale_bmr_kcal": None})(), HOJE)
        assert macros.kcal == pytest.approx(TMB * 1.3)  # rotina leve (1,3), nunca 1,725 nem sem kcal
    base = wc._base_tdee_kcal(settings, perfil_active, TMB)
    assert base == pytest.approx(TMB * 1.3)


def test_plano_automatico_sem_nivel_no_perfil_e_bloqueado_com_erro_do_motor():
    from app.domain.nutrition.plan import auto_intervals_blockers

    settings = svc.settings_from_row(_db().plans[1])
    assert auto_intervals_blockers(settings, None) == (ee.LIMIT_AUTO_REQUIRES_PROFILE_LEVEL,)
    assert auto_intervals_blockers(settings, ActivityLevel.MODERATE) == ()
    classico = svc.settings_from_row(_db(modo="explicit_training").plans[1])
    assert auto_intervals_blockers(classico, None) == ()


def test_base_do_dia_expoe_o_modo_para_a_tela_dizer_a_verdade():
    sincronizado = compute_daily_target(SUJEITO, _plano(), _dia(FORCA, sincronizado=True))
    fallback = compute_daily_target(SUJEITO, _plano(), _dia(FORCA, sincronizado=False))
    classico = compute_daily_target(SUJEITO, _plano(ee.TdeeMode.EXPLICIT_TRAINING), day_context_for(HOJE, HOJE, FORCA))
    assert [m.tdee_basis for m in (sincronizado, fallback, classico)] == [
        ee.TdeeBasis.AUTO_SYNCED, ee.TdeeBasis.AUTO_FALLBACK, ee.TdeeBasis.CLASSIC
    ]
    assert fallback.to_dict()["tdee_basis"] == "auto_fallback"


def test_rotulo_da_atividade_extra_tem_limite_de_tamanho_no_motor():
    longo = ee.ExtraActivity(kind="cardio", calories_kcal=10, label="x" * 500)
    assert len(longo.to_dict()["label"]) == ee.EXTRA_ACTIVITY_LABEL_MAX_CHARS
    assert ee.ExtraActivity(kind="cardio", calories_kcal=10, label="   ").to_dict()["label"] is None


def test_alvo_do_servico_gravado_traz_a_base_e_o_extra_no_json_que_a_rota_le():
    com = _db()
    com.add_intervals_activity(ALUNO, HOJE, "VirtualRun", 427)
    _linha(com)
    gravado = com.targets[(ALUNO, HOJE)]["inputs_json"]
    assert gravado["extra_activity"]["kcal"] == pytest.approx(427) and gravado["tdee_basis"] == "auto_synced"
    # a leitura pública usa SÓ as colunas projetadas pela SQL (o fake projeta como o banco)
    with com.conn() as conn, conn.cursor() as cur:
        [linha] = svc.read_targets(cur, ALUNO, HOJE, HOJE)
    publico = svc.target_row_public(linha, HOJE)
    assert publico["extra_activity"]["kcal"] == pytest.approx(427) and publico["tdee_basis"] == "auto_synced"
    assert "inputs_json" not in linha


def test_dia_do_plano_automatico_nao_e_regravado_no_fallback_quando_as_entradas_vieram_so_com_o_plano_novo():
    # Ontem valia o plano automático; hoje o clássico. As entradas trazidas de fora foram carregadas só com o plano novo
    # (sem o Intervals): `ensure_daily_targets` recarrega para o dia de ontem não cair no fallback.
    db = _db()
    antigo = next(iter(db.plans))
    db.plans[antigo]["superseded_at"] = datetime(2026, 11, 5, 10)
    db.add_plan(ALUNO, status="published", effective_from=HOJE, published_at=datetime(2026, 11, 5, 10),
                training_kcal_mode="included_in_factor")
    ontem = HOJE - timedelta(days=1)
    db.add_intervals_activity(ALUNO, ontem, "VirtualRun", 200)
    classico = svc.settings_from_row(db.plans[2])
    with db.conn() as conn, conn.cursor() as cur:
        entradas = svc.load_user_inputs(cur, ALUNO, ontem, HOJE, HOJE, [classico])
    assert entradas.intervals_loaded is False
    janela = svc.TargetWindow(date_from=ontem, date_to=HOJE, create_until=HOJE)
    linhas = {l["local_date"]: l for l in svc.ensure_daily_targets(db.conn(), ALUNO, janela, today=HOJE, inputs=entradas)}
    assert linhas[ontem.isoformat()]["tdee_basis"] == "auto_synced"
    assert linhas[ontem.isoformat()]["extra_activity"]["kcal"] == pytest.approx(200)
    assert linhas[HOJE.isoformat()]["tdee_basis"] == "classic"


def test_explicito_dia_so_de_cardio_no_servico_soma_e_recalcula_quando_a_atividade_chega_depois():
    """Caso 08/11: a meta de hoje saiu antes do sync (só TMB × fator); a atividade nova muda o hash das entradas e a
    meta de hoje é recalculada na leitura seguinte, sem virar dia de treino nem trocar o tipo de dia do ciclo."""
    db = _db(modo="explicit_training")
    antes = dict(_linha(db))
    hash_antes = db.targets[(ALUNO, HOJE)]["inputs_hash"]
    db.add_intervals_activity(ALUNO, HOJE, "VirtualRun", 436, elapsed=2601)
    depois = _linha(db)
    liquido = 436 - TMB_FAKE / 1440 * 2601 / 60
    assert depois["is_training_day"] is False and depois["training_kcal"] is None
    assert depois["extra_activity"]["kcal"] == pytest.approx(liquido, abs=0.01)
    assert depois["tdee_kcal"] == pytest.approx(antes["tdee_kcal"] + liquido, abs=0.01)
    assert depois["day_kind"] == antes["day_kind"] and depois["tdee_basis"] == "classic"
    assert db.targets[(ALUNO, HOJE)]["inputs_hash"] != hash_antes


def test_sessao_do_app_sem_intervals_conta_e_a_vinculada_conta_uma_vez():
    """Sessão do app sem atividade no Intervals conta (liquida); com a atividade VINCULADA (a lista de extras não a
    traz) segue contando uma vez só."""
    sem_intervals = _db(modo="explicit_training")
    sem_intervals.add_session(ALUNO, HOJE, 428)
    vinculada = _db(modo="explicit_training")
    vinculada.add_session(ALUNO, HOJE, 428)
    vinculada.add_intervals_activity(ALUNO, HOJE, "WeightTraining", 428, linked=True)
    a, b = _linha(sem_intervals), _linha(vinculada)
    assert a["training_kcal"] == pytest.approx(428 - TMB_FAKE / 1440 * 60, abs=0.01)
    assert a["tdee_kcal"] == pytest.approx(b["tdee_kcal"]) and b["extra_activity"]["kcal"] == 0


def test_breakdown_traz_uma_parcela_por_atividade_com_o_nome_do_intervals_e_soma_o_total():
    extras = (
        ee.ExtraActivity(kind="cardio", calories_kcal=436, label="Morning Walk", duration_seconds=2601),
        ee.ExtraActivity(kind="strength", calories_kcal=300, label="Weight Training", duration_seconds=1800),
    )
    soma = ee.extra_activity_kcal(extras, TMB)
    resultado = ee.tdee(TMB, ActivityLevel.LIGHT, ee.TdeeMode.EXPLICIT_TRAINING, soma.kcal)
    quebra = ee.tdee_breakdown(resultado, soma.kcal, ee.TdeeBasis.CLASSIC, soma.to_dict()["items"])
    assert [p.label for p in quebra.parts[1:]] == ["Morning Walk", "Weight Training"]
    assert sum(p.kcal for p in quebra.parts) == quebra.total_kcal == round(resultado.kcal)
    from app.domain.nutrition import labels as lb

    publico = lb.tdee_breakdown_public(quebra)
    assert [p["label"] for p in publico["parts"][1:]] == ["Morning Walk", "Weight Training"]
    assert publico["parts"][1]["kcal"] == round(436 - TMB / 1440 * 2601 / 60)
