"""Issue #138 / ADR 0016 (`nutrition-v5`): toda kcal INFORMADA vinda do Intervals entra ATIVA por padrão.

Precedência: correção manual da atividade > modo explícito do plano > padrão ativa. A kcal por MET segue bruta.
Dono: `activity_energy` (`DEFAULT_REPORTED_CALORIE_MODE`), mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app import intervals_store
from app import nutrition_targets_service as svc
from app.domain.nutrition import activity_energy as ae
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition.daily_target import ENGINE_VERSION, SessionFacts
from app.domain.nutrition.energy_expenditure import ExtraActivity
from app.domain.nutrition.plan import DEFAULT_WATCH_CALORIE_MODE, EnergyParams, build_plan_settings
from app.domain.nutrition.subject import Reading, SubjectSeries
from app.nutrition_energy_service import DEFAULT_CALORIE_MODE, raw_activity_of

TMB = 1440.0  # 1 kcal/min: o repouso da janela é igual aos minutos
INICIO = datetime(2026, 11, 10, 7, 0)
LINHA = {
    "id": 11, "external_id": "i100", "activity_date": date(2026, 11, 10), "start_time": INICIO,
    "elapsed_time_seconds": 3600, "moving_time_seconds": 3000, "sport_type": "Run", "name": "Corrida",
    "calories_kcal": 500,
}


def _normalizada(calorie_mode=DEFAULT_CALORIE_MODE, correcoes=()):
    crua = raw_activity_of(LINHA, calorie_mode)
    (crua,) = ae.apply_activity_corrections((crua,), correcoes)
    return ae.normalize_activity(crua, TMB)


def test_padroes_derivam_do_dono_e_sao_ativa():
    assert ae.DEFAULT_REPORTED_CALORIE_MODE == ae.CalorieMode.ACTIVE
    assert DEFAULT_WATCH_CALORIE_MODE is ae.DEFAULT_REPORTED_CALORIE_MODE
    assert ee.DEFAULT_TDEE_CALORIE_MODE is ae.DEFAULT_REPORTED_CALORIE_MODE
    assert DEFAULT_CALORIE_MODE == ae.CalorieMode.ACTIVE.value
    assert ee.MET_KCAL_CALORIE_MODE == ae.CalorieMode.GROSS
    assert ENGINE_VERSION == "nutrition-v7"


@pytest.mark.parametrize("valor", [None, "", "  "])
def test_modo_ausente_vira_ativa_e_unknown_explicito_continua_unknown(valor):
    assert ae.normalize_calorie_mode(valor) == ae.CalorieMode.ACTIVE
    assert ae.normalize_calorie_mode("unknown") == ae.CalorieMode.UNKNOWN
    assert ae.normalize_calorie_mode("xyz") == ae.CalorieMode.UNKNOWN  # texto estranho: conservador


def test_a_importada_sem_correcao_entra_ativa_sem_desconto_e_sem_precisao_reduzida():
    atividade = _normalizada()
    assert atividade.active_kcal == 500.0
    assert atividade.calorie_mode == ae.CalorieMode.ACTIVE
    assert ae.ActivityFlag.REDUCED_PRECISION not in atividade.flags
    publico = atividade.to_dict()
    assert publico["source"] == ae.FINGERPRINT_SOURCE_DEFAULT and publico["correction"] is None


def test_b_reclassificar_para_gross_desconta_e_prevalece_sobre_o_padrao_e_o_plano():
    correcao = ae.ActivityCorrection("11", ae.CORRECTION_ACTION_RECLASSIFY, ae.CalorieMode.GROSS.value)
    atividade = _normalizada(ae.CalorieMode.ACTIVE.value, (correcao,))
    assert atividade.active_kcal == 440.0  # 500 − 60 min × 1 kcal/min
    assert atividade.to_dict()["correction"]["action"] == ae.CORRECTION_ACTION_RECLASSIFY
    # reclassificar para ativa vence o plano `gross`
    ativa = ae.ActivityCorrection("11", ae.CORRECTION_ACTION_RECLASSIFY, ae.CalorieMode.ACTIVE.value)
    assert _normalizada(ae.CalorieMode.GROSS.value, (ativa,)).active_kcal == 500.0


def test_c_desfazer_a_correcao_volta_ao_padrao_ativa():
    assert _normalizada(correcoes=()).active_kcal == 500.0


@pytest.mark.parametrize("modo, reduzida", [("gross", False), ("unknown", True)])
def test_d_plano_com_modo_explicito_desconta(modo, reduzida):
    atividade = _normalizada(modo)
    assert atividade.active_kcal == 440.0
    assert (ae.ActivityFlag.REDUCED_PRECISION in atividade.flags) is reduzida
    params = EnergyParams(watch_calorie_mode=ae.CalorieMode(modo))
    assert not params.is_default and params.to_dict() == {"watch_calorie_mode": modo}
    assert EnergyParams(watch_calorie_mode=ae.CalorieMode.ACTIVE).is_default


def test_e_kcal_por_met_segue_bruta_com_desconto():
    treino = ee.resolve_training_kcal(reported_kcal=None, weight_kg=80, duration_min=60, met=5.0, bmr_kcal=TMB)
    assert treino.kcal_nature == ee.TrainingKcalNature.MET
    assert treino.resting_discount_kcal == pytest.approx(60.0)
    assert treino.kcal == pytest.approx(treino.gross_kcal - 60.0)


SERIE = SubjectSeries(
    sex="male", birth_date=date(1996, 1, 1), height_cm=180, activity_level="active",
    weight=(Reading(80.0, date(2026, 10, 1)),),
)
HOJE = date(2026, 10, 14)


@pytest.mark.parametrize("modo", ["explicit_training", "auto_intervals"])
def test_f_kcal_informada_do_treino_vinculado_entra_ativa_no_explicito_e_no_automatico(modo):
    settings = build_plan_settings(strategy="maintenance", tdee_mode=modo, activity_level="moderate").settings
    entradas = svc.UserInputs(
        series=SERIE, sessions={HOJE: [SessionFacts(calories_kcal=428.0, duration_seconds=3600)]},
        extra_activities={HOJE: [ExtraActivity(kind="cardio", calories_kcal=200.0, label="Esteira", duration_seconds=1800)]},
        intervals_in_sync=True, intervals_loaded=modo == "auto_intervals",
    )
    alvo = svc.compute_day(entradas, settings, HOJE, HOJE).target
    assert alvo.tdee.training_kcal == pytest.approx(628.0)  # 428 + 200, sem desconto do repouso


def test_g_atividade_vinculada_ao_treino_nao_conta_duas_vezes():
    # Leitura: só atividade SEM vínculo é extra.
    assert "LEFT JOIN activity_intervals_link" in svc.EXTRA_ACTIVITIES_SQL and "l.id IS NULL" in svc.EXTRA_ACTIVITIES_SQL
    # Motor: a do Intervals que é a mesma sessão do app (início e duração) não soma.
    sessao = ee.SessionKcal(calories_kcal=428.0, duration_min=60, start=INICIO)
    extra = ExtraActivity(kind="strength", calories_kcal=430.0, label="Força", duration_seconds=3600, start=INICIO)
    resultado = ee.extra_activity_kcal((extra,), TMB, sessions=(sessao,))
    assert resultado.kcal == 0.0


def test_h_mesmo_id_externo_nao_duplica():
    a = ExtraActivity(kind="cardio", calories_kcal=200.0, label="A", duration_seconds=1800, start=INICIO, external_id="x1")
    b = ExtraActivity(kind="cardio", calories_kcal=200.0, label="A", duration_seconds=1800, start=INICIO, external_id="x1")
    assert ee.extra_activity_kcal((a, b), TMB).kcal == pytest.approx(200.0)


def test_i_ressincronizacao_nao_toca_nas_correcoes():
    textos = [v for v in vars(intervals_store).values() if isinstance(v, str)]
    assert not any("activity_energy_corrections" in t for t in textos)
    upsert = intervals_store._UPSERT_ACTIVITIES_SQL
    assert "ON DUPLICATE KEY UPDATE" in upsert  # o id da atividade (chave da correção) é preservado
    assert "DELETE" not in upsert.upper()


# ---------------------------------------------------------------------------
# Correção manual nos modos explícito e automático (issue #138: "a classificação manual prevalece")
# ---------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from app.domain.nutrition import labels  # noqa: E402
from tests.support.nutrition_fake import FakeNutritionDB  # noqa: E402

BRUTA = ae.ActivityCorrection("11", "reclassify", "gross")
ATIVA = ae.ActivityCorrection("11", "reclassify", "active")
EXCLUIR = ae.ActivityCorrection("11", "exclude")


def _extra(correcao=None, activity_id="11", **kw):
    dados = dict(kind="cardio", calories_kcal=500.0, label="Corrida", duration_seconds=3600, start=INICIO)
    dados.update(kw)
    return ExtraActivity(activity_id=activity_id, correction=correcao, **dados)


def test_motor_extra_reclassificada_bruta_desconta_e_marca_a_correcao():
    resultado = ee.extra_activity_kcal((_extra(BRUTA),), TMB)
    assert resultado.kcal == pytest.approx(440.0)  # 500 - 60 min de repouso
    (linha,) = resultado.to_dict()["items"]
    assert linha["correction"] == {"action": "reclassify", "energy_kind": "gross"}
    assert linha["calorie_mode"] == "gross" and linha["activity_id"] == "11" and linha["excluded_reason"] is None


def test_motor_extra_excluida_nao_soma_com_motivo_proprio():
    resultado = ee.extra_activity_kcal((_extra(EXCLUIR), _extra(None, activity_id="12", calories_kcal=100.0)), TMB)
    assert resultado.kcal == pytest.approx(100.0)
    excluida = resultado.to_dict()["items"][0]
    assert excluida["excluded_reason"] == ee.EXCLUDED_BY_USER == ae.ActivityFlag.EXCLUDED_BY_USER.value
    assert excluida["net_kcal"] == 0.0 and excluida["correction"]["action"] == "exclude"
    assert ee.EXCLUDED_BY_USER in labels.EXCLUDED_ACTIVITY_REASON_LABELS


def test_motor_sem_correcao_volta_ao_padrao_ativa_e_correcao_de_outro_id_e_ignorada():
    padrao = ee.extra_activity_kcal((_extra(None),), TMB)
    outra = ee.extra_activity_kcal((_extra(ae.ActivityCorrection("999", "reclassify", "gross")),), TMB)
    assert padrao.kcal == pytest.approx(500.0) and outra.kcal == pytest.approx(500.0)
    assert "correction" not in outra.to_dict()["items"][0]
    assert ee.extra_activity_kcal((_extra(ATIVA),), TMB, calorie_mode="gross").kcal == pytest.approx(500.0)  # manual vence


def test_motor_deduplicacao_inalterada_com_correcao():
    a = _extra(BRUTA, external_id="x1")
    b = _extra(None, activity_id="12", external_id="x1")
    assert ee.extra_activity_kcal((a, b), TMB).kcal == pytest.approx(440.0)  # mesmo id externo: soma uma vez só
    sessao = ee.SessionKcal(calories_kcal=428.0, duration_min=60, start=INICIO)
    forca = _extra(BRUTA, kind="strength", calories_kcal=430.0)
    assert ee.extra_activity_kcal((forca,), TMB, sessions=(sessao,)).kcal == 0.0  # é a sessão do app


def test_motor_sessao_vinculada_reclassificada_desconta_e_excluida_continua_contando():
    def treino(correcao):
        return ee.resolve_sessions_kcal((ee.SessionKcal(428.0, 60, correction=correcao),), 80, 5.0, TMB).kcal
    assert treino(None) == pytest.approx(428.0)
    assert treino(BRUTA) == pytest.approx(368.0)
    assert treino(EXCLUIR) == pytest.approx(428.0)  # exclusão de atividade vinculada nunca apaga o treino do dia


ALUNO_S = 2
HOJE_S = date(2026, 11, 5)


def _db_servico(modo):
    db = FakeNutritionDB()
    db.add_weight(ALUNO_S, HOJE_S - timedelta(days=30), 80)
    db.add_plan(ALUNO_S, status="published", effective_from=HOJE_S - timedelta(days=10),
                published_at=datetime(2026, 10, 7, 12), training_kcal_mode=modo)
    db.intervals_connected = True
    db.intervals_minutes_since_sync = 120
    db.add_intervals_activity(ALUNO_S, HOJE_S, "Run", 500, elapsed=3600)  # id 1
    return db


def _meta(db):
    janela = svc.TargetWindow(date_from=HOJE_S, date_to=HOJE_S, create_until=HOJE_S)
    linhas = svc.ensure_daily_targets(db.conn(), ALUNO_S, janela, today=HOJE_S)
    return {linha["local_date"]: linha for linha in linhas}[HOJE_S.isoformat()]


@pytest.mark.parametrize("modo", ["explicit_training", "auto_intervals"])
def test_servico_le_as_correcoes_e_a_meta_recalcula(modo, monkeypatch):
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    db = _db_servico(modo)
    base = _meta(db)
    hash_base = db.targets[(ALUNO_S, HOJE_S)]["inputs_hash"]
    assert base["extra_activity"]["items"][0]["activity_id"] == "1"
    db.corrections[(ALUNO_S, "1")] = {"action": "reclassify", "energy_kind": "gross"}  # PUT da correção
    bruta = _meta(db)
    assert bruta["extra_activity"]["kcal"] == pytest.approx(base["extra_activity"]["kcal"] - bruta["extra_activity"]["resting_discount_kcal"])
    assert bruta["extra_activity"]["resting_discount_kcal"] > 0 and bruta["tdee_kcal"] < base["tdee_kcal"]
    assert db.targets[(ALUNO_S, HOJE_S)]["inputs_hash"] != hash_base
    db.corrections[(ALUNO_S, "1")] = {"action": "exclude", "energy_kind": None}
    excluida = _meta(db)
    assert excluida["extra_activity"]["kcal"] == 0
    assert excluida["extra_activity"]["items"][0]["excluded_reason"] == ee.EXCLUDED_BY_USER
    del db.corrections[(ALUNO_S, "1")]  # DELETE: volta ao padrão ativa
    assert _meta(db)["tdee_kcal"] == pytest.approx(base["tdee_kcal"])
    assert db.targets[(ALUNO_S, HOJE_S)]["inputs_hash"] == hash_base


def test_servico_entrega_a_correcao_da_atividade_vinculada_a_sessao(monkeypatch):
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    assert "LEFT JOIN activity_intervals_link l ON l.activity_id = a.id" in svc._SESSIONS_SQL
    assert "l.intervals_activity_id" in svc._SESSIONS_SQL
    db = _db_servico("explicit_training")
    db.intervals_extras[0]["linked"] = True  # a atividade do Intervals É a da sessão: não entra como extra
    db.corrections[(ALUNO_S, "7")] = {"action": "reclassify", "energy_kind": "gross"}
    plano = svc.settings_from_row(db.plans[1])
    with db.conn() as conn, conn.cursor() as cur:
        original = cur.execute

        def sessao_vinculada(sql, params=None):
            if sql == svc._SESSIONS_SQL:  # o fake não projeta o vínculo: a linha vem como o banco a devolveria
                return cur._result([{"local_date": HOJE_S, "calories_kcal": 428, "duration_seconds": 3600,
                                     "started_at": None, "intervals_activity_id": 7}])
            return original(sql, params)

        cur.execute = sessao_vinculada
        entradas = svc.load_user_inputs(cur, ALUNO_S, HOJE_S, HOJE_S, HOJE_S, [plano])
    (sessao,) = entradas.sessions[HOJE_S]
    assert sessao.correction == ae.ActivityCorrection("7", "reclassify", "gross")
    com = svc.compute_day(entradas, plano, HOJE_S, HOJE_S)
    entradas.sessions[HOJE_S] = [SessionFacts(calories_kcal=428, duration_seconds=3600,
                                              correction=ae.ActivityCorrection("7", "exclude"))]
    excluida = svc.compute_day(entradas, plano, HOJE_S, HOJE_S)
    assert excluida.target.tdee.training_kcal == pytest.approx(428.0)  # exclusão ignorada: o treino continua contando
    assert 0 < com.target.tdee.training_kcal < 428.0  # bruta: desconta o repouso da janela
    assert "training_sessions" in com.context.to_dict()  # a correção entra no hash do dia
