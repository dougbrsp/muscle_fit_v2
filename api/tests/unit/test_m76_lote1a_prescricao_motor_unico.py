"""M76, Lote 1a: prescrição com um motor só (backend).

Cobre os donos novos/consolidados deste lote, sem banco:

- `domain/session_loads.py`: cargas de aquecimento/ajuste/técnica no passo do
  equipamento (C7);
- `domain/prescription_service.py`: sugestão por par só com a decisão do motor
  v2, última execução sem aquecimento nem técnica, calibração pela execução,
  trilha mais recente quando o equipamento não vem (C1-C3);
- `prescription.parse_rpe` derivado de `parse_rpe_ramp` + `clamp_rpe_text`
  único + teto do contrato REDUCED_DOSE nos caminhos legados (C4);
- B1: snapshot sem decisão compõe com o contrato do grupo, e o overlay nunca
  passa do teto congelado;
- C10: tetos por prontidão lidos do dono (`_RECOMMENDATIONS`) e janela de 21
  sessões com um dono.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain import effective_intensity as ei
from app.domain import prescription as pd
from app.domain import prescription_service as svc
from app.domain import progression_engine as pe
from app.domain import session_loads as sl
from app.domain import training_safety as ts
from app.domain.training_history import DEFAULT_SESSION_WINDOW, parse_rpe_ramp
from tests.support.fake_db import CursorFactoryConn


# ---------------------------------------------------------------------------
# C7: cargas das séries que não são de trabalho
# ---------------------------------------------------------------------------


def test_aquecimento_ajuste_e_drop_no_passo_default():
    plano = sl.session_load_plan(100, load_increment_kg=None, feeler_sets_count=3, application_loads_kg=[80.0])
    assert plano == {
        "load_anchor_kg": 100.0,
        "load_increment_kg": 2.5,
        "warmup_loads_kg": [20.0, 40.0],
        "feeler_load_kg": 50.0,
        "feeler_loads_kg": [50.0, 70.0, 90.0],  # aditivo: 0,5 / 0,7 / 0,9
        "application_loads_kg": [80.0],  # o congelado, sem recalcular
    }


def test_cargas_arredondam_ao_passo_do_equipamento():
    # 47,5 kg: 20% = 9,5 -> passo 5 = 10; 40% = 19 -> 20; ajuste 23,75 -> 25
    assert sl.warmup_loads(47.5, 5) == [10.0, 20.0]
    assert sl.feeler_load(47.5, 1, 5) == 25.0
    # passo 1 kg: drop x0,8 composto a partir de 50 -> 40, 32
    assert sl.technique_application_loads("drop_set", 50, 2, 1) == [40.0, 32.0]
    assert sl.technique_application_loads("rest_pause", 41, 2, 2) == [41.0, 41.0]  # repetição: sem arredondar
    assert sl.load_step(0) == sl.DEFAULT_LOAD_STEP_KG and sl.load_step("x") == sl.DEFAULT_LOAD_STEP_KG


def test_sem_ancora_nao_inventa_carga_mas_informa_o_passo():
    plano = sl.session_load_plan(None, load_increment_kg=1.25)
    assert plano["load_increment_kg"] == 1.25
    assert plano["warmup_loads_kg"] == [] and plano["feeler_load_kg"] is None


def test_motor_usa_o_mesmo_dono_para_a_carga_do_drop_e_o_passo_default():
    assert pe.DEFAULT_LOAD_STEP_KG is sl.DEFAULT_LOAD_STEP_KG
    assert pe.DROP_SET_LOAD_FACTOR is sl.DROP_SET_LOAD_FACTOR
    params = pe.technique_parameters(
        "drop_set", source="model", applications=2, working_load_kg=Decimal("50"), load_step_kg=Decimal("1")
    )
    assert params["application_loads_kg"] == [40.0, 32.0]
    # sem passo do equipamento: o default de 2,5 kg (comportamento anterior)
    params = pe.technique_parameters("drop_set", source="model", applications=2, working_load_kg=Decimal("50"))
    assert params["application_loads_kg"] == [40.0, 32.5]


# ---------------------------------------------------------------------------
# C4: parser e corte únicos de RPE em texto
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto, esperado",
    [("8-9-10", 10), ("8-9", 9), ("9", 9), ("8,5", 8), ("8-8.5-9-9.5-10", 10), (None, None), ("", None)],
)
def test_parse_rpe_e_o_ultimo_degrau_da_rampa(texto, esperado):
    assert pd.parse_rpe(texto) == esperado
    rampa = parse_rpe_ramp(texto)
    assert (int(rampa[-1]) if rampa else None) == esperado


def test_rampa_8_9_10_vale_o_ultimo_degrau_limitado_pelo_teto_em_todo_lugar():
    par_sem_falha = ts.build_training_safety(equipment_max_safe_rpe=9)
    composicao = ts.compose(par_sem_falha, ts.readiness_for_status(ts.READY), pd.parse_rpe("8-9-10"))
    assert composicao.target == 9 and composicao.ceiling == 9
    assert ei.clamp_rpe_text("8-9-10", composicao.ceiling)[0] == "8-9"
    # a ficha/overlay exibem o mesmo alvo que o motor cobra
    legado = ei.from_legacy_plan("8-9-10", par_sem_falha)
    assert legado.rpe_target_effective == 9


def test_geracao_legada_corta_rpe_de_dose_reduzida_pelo_teto_do_contrato():
    exercicios = [
        {"muscle_primary": "Peitoral", "rpe_target": "8-9-10"},
        {"muscle_primary": "Costas", "rpe_target": "8-9-10"},
        {"muscle_primary": "peitoral", "rpe_target": None},
    ]
    ei.clamp_reduced_dose_rpe_targets(exercicios, ["Peitoral"])
    teto = ts.readiness_for_status(ts.REDUCED_DOSE).max_rpe
    assert teto == 9
    assert [item["rpe_target"] for item in exercicios] == ["8-9", "8-9-10", None]


# ---------------------------------------------------------------------------
# C10: constantes com um dono
# ---------------------------------------------------------------------------


def test_tetos_por_prontidao_sao_lidos_do_dono_da_recuperacao():
    from app.intelligence.muscle_recovery_service import MuscleRecoveryService

    for status, rec in MuscleRecoveryService._RECOMMENDATIONS.items():
        readiness = ts.readiness_for_status(status)
        assert (readiness.max_rpe, readiness.allow_failure) == (rec["maximum_rpe"], rec["allow_failure_on_last_set"])
    assert not hasattr(ts, "_READINESS_DEFAULTS")


def test_janela_de_sessoes_e_teto_default_tem_um_dono():
    from app import exercise_briefing, progression_state
    from app.domain import progression_evaluator

    assert progression_state.SESSION_WINDOW == exercise_briefing.HISTORY_WINDOW == DEFAULT_SESSION_WINDOW
    assert progression_evaluator.DEFAULT_MAX_SAFE_RPE is ts.DEFAULT_MAX_SAFE_RPE
    assert not hasattr(pe, "ACTIVE_TRACK_WINDOW_SESSIONS")


# ---------------------------------------------------------------------------
# B1: sem decisão, o contrato do grupo; overlay nunca acima do congelado
# ---------------------------------------------------------------------------


def _linha(**kwargs):
    base = {"sets_count": 3, "reps_target": "10-12", "load_target": "40kg", "rpe_target": "10", "advanced_technique": None}
    base.update(kwargs)
    return base


def test_linha_manual_em_dose_reduzida_nao_sai_com_teto_10_nem_falha():
    par_com_falha = ts.build_training_safety(equipment_max_safe_rpe=10)
    pronto = pd.build_prescription(_linha(), safety=par_com_falha)
    assert (pronto["effective_rpe_ceiling"], pronto["failure_allowed"]) == (10, True)

    reduzida = pd.build_prescription(
        _linha(), safety=par_com_falha, readiness=ts.readiness_for_status(ts.REDUCED_DOSE)
    )
    assert reduzida["effective_rpe_ceiling"] == 9
    assert reduzida["failure_allowed"] is False
    assert reduzida["effective_rpe_target"] == 9
    assert reduzida["clamps"] == [{"field": "rpe_target", "from": 10, "to": 9, "reason": ts.REASON_RECOVERY}]


def test_overlay_nunca_devolve_teto_maior_que_o_congelado():
    par_com_falha = ts.build_training_safety(equipment_max_safe_rpe=10)
    congelado = pd.build_prescription(
        _linha(), safety=par_com_falha, readiness=ts.readiness_for_status(ts.REDUCED_DOSE)
    )
    exibido, overlay = pd.apply_safety_overlay(congelado, par_com_falha)
    assert overlay["effective_rpe_ceiling"] == 9 and overlay["failure_allowed"] is False
    assert exibido["effective_rpe_ceiling"] == 9 and exibido["failure_allowed"] is False

    # e continua restringindo quando o par piorou desde o congelamento
    exibido, overlay = pd.apply_safety_overlay(congelado, ts.build_training_safety(equipment_max_safe_rpe=8))
    assert overlay["effective_rpe_ceiling"] == 8 and exibido["effective_rpe_target"] == 8


# ---------------------------------------------------------------------------
# C1-C3: serviço de sugestão por par
# ---------------------------------------------------------------------------


def _log(mapping_id, sets, *, equipment_id=None, local_date=date(2026, 10, 1)):
    return {"mapping_id": mapping_id, "equipment_catalog_id": equipment_id, "local_date": local_date, "sets": sets}


def _s(n, kg, reps, rpe=8, set_type="working", **extra):
    return {"set_number": n, "weight_kg": kg, "reps": reps, "rpe": rpe, "set_type": set_type, **extra}


def test_ultima_execucao_ignora_aquecimento_e_aplicacao_de_tecnica():
    logs = [
        _log(10, [_s(1, 20, 10, set_type="warmup"), _s(2, 60, 10), _s(3, 62.5, 9), _s(4, 45, 8, set_type="drop_set")]),
        _log(10, [_s(1, 55, 12)], local_date=date(2026, 9, 25)),
    ]
    ultima = svc.last_execution_from_logs(logs, 10)
    assert (ultima.last_load_kg, ultima.last_reps) == (62.5, 9)
    assert ultima.working_load_kg == 60.0  # calibração: mínimo das séries de trabalho


def test_ultima_execucao_pula_ocorrencia_so_de_aquecimento_e_prefere_o_equipamento():
    logs = [
        _log(10, [_s(1, 20, 10, set_type="warmup")], equipment_id=7),
        _log(10, [_s(1, 50, 10)], equipment_id=8, local_date=date(2026, 9, 28)),
        _log(10, [_s(1, 40, 10)], equipment_id=7, local_date=date(2026, 9, 20)),
    ]
    assert svc.last_execution_from_logs(logs, 10).last_load_kg == 50.0
    assert svc.last_execution_from_logs(logs, 10, 7).last_load_kg == 40.0
    assert svc.last_execution_from_logs(logs, 99) is None


def _decisao(**kwargs):
    base = dict(group_state_id=1, muscle_group_id=3, track_id=5, suggested_reps_min=10, suggested_reps_max=12)
    base.update(kwargs)
    return pe.ProgressionDecision(**base)


class _Stubs:
    """`prescribe_pairs` e leituras do estado da progressão sem banco."""

    def __init__(self, monkeypatch, decisions, tracks=None, steps=None):
        from app import progression_state as ps
        from app.generation import deterministic_prescription as dp

        self.requests = []
        self.readiness = None

        def fake_prescribe(conn, user_id, requests, *, readiness=None, now=None, model_fixed_sets=True):
            self.requests = list(requests)
            self.readiness = readiness
            return {
                request.key: dp.PairPrescription(decision=decisions[request.key], safety=None, technique=None)
                for request in requests
                if request.key in decisions
            }

        monkeypatch.setattr(dp, "prescribe_pairs", fake_prescribe)
        monkeypatch.setattr(ps, "load_tracks", lambda conn, user_id: dict(tracks or {}))
        monkeypatch.setattr(ps, "load_equipment_steps", lambda conn, ids: dict(steps or {}))


def test_sugestao_sobe_carga_quando_o_motor_manda(monkeypatch):
    decisao = _decisao(
        decision=pe.INCREASE_LOAD,
        suggested_load_kg=65.0,
        effective_rpe_target=8,
        effective_rpe_ceiling=9,
        evidence={"load_increment_kg": 5.0},
    )
    stubs = _Stubs(monkeypatch, {(10, 7): decisao}, steps={7: Decimal("5")})
    logs = [_log(10, [_s(1, 60, 12), _s(2, 60, 12)], equipment_id=7)]
    contrato = {3: pe.ReadinessContract(muscle_group_id=3, status="REDUCED_DOSE", max_rpe=9, allow_failure=False)}

    sugestao = svc.suggest_for_pair(object(), 1, 10, 7, readiness=contrato, logs=logs)

    assert stubs.readiness is contrato  # nunca READY fixo: o contrato passa adiante
    assert sugestao.suggested_load_kg == 65.0 and sugestao.load_source == svc.LOAD_SOURCE_ENGINE
    assert sugestao.reps_target == "10-12" and sugestao.rpe_target == "8"
    assert sugestao.progression == svc.PROGRESSION_INCREASED and sugestao.trend == "up"
    assert sugestao.load_increment_kg == 5.0
    assert svc.progression_note(sugestao) == "Aumentar carga: +5 kg (passo do equipamento)."


def test_trilha_sem_carga_calibra_pela_ultima_execucao(monkeypatch):
    _Stubs(monkeypatch, {(10, 7): _decisao(decision=pe.HOLD, suggested_load_kg=None, effective_rpe_target=8)})
    logs = [_log(10, [_s(1, 20, 10, set_type="warmup"), _s(2, 42.5, 10), _s(3, 40, 10)], equipment_id=7)]

    sugestao = svc.suggest_for_pair(object(), 1, 10, 7, readiness={}, logs=logs)

    assert sugestao.suggested_load_kg == 40.0  # mínimo das séries de trabalho, nunca o aquecimento
    assert sugestao.load_source == svc.LOAD_SOURCE_LAST_EXECUTION
    assert "calibração" in svc.progression_note(sugestao)


def test_sem_equipamento_usa_a_trilha_executada_mais_recentemente(monkeypatch):
    antiga = pe.TrackState(exercise_id=10, equipment_id=7, id=1, last_executed_at=datetime(2026, 9, 1))
    recente = pe.TrackState(exercise_id=10, equipment_id=8, id=2, last_executed_at=datetime(2026, 10, 1))
    outra = pe.TrackState(exercise_id=11, equipment_id=9, id=3, last_executed_at=datetime(2026, 10, 5))
    stubs = _Stubs(
        monkeypatch,
        {(10, 8): _decisao(decision=pe.HOLD, suggested_load_kg=50.0, effective_rpe_target=8)},
        tracks={(10, 7): antiga, (10, 8): recente, (11, 9): outra},
    )

    sugestao = svc.suggest_for_pair(object(), 1, 10, None, readiness={}, logs=[])

    assert [request.key for request in stubs.requests] == [(10, 8)]
    assert sugestao.equipment_id == 8 and sugestao.suggested_load_kg == 50.0


def test_sem_historico_nem_trilha_nao_inventa_carga(monkeypatch):
    _Stubs(monkeypatch, {(10, None): _decisao(decision=pe.HOLD, suggested_load_kg=None, effective_rpe_target=8)})
    sugestao = svc.suggest_for_pair(object(), 1, 10, None, readiness={}, logs=[])
    assert sugestao.suggested_load_kg is None and sugestao.progression is None
    assert svc.progression_note(sugestao).startswith("Sem histórico registrado")
    assert svc.legacy_indicators(None) == {
        "progression": None,
        "progression_note": None,
        "suggested_load_kg": None,
        "suggested_reps": None,
    }


def test_sem_contrato_informado_projeta_a_recuperacao_de_agora(monkeypatch):
    stubs = _Stubs(monkeypatch, {(10, 7): _decisao(suggested_load_kg=50.0)})
    contrato = {3: pe.ReadinessContract(muscle_group_id=3, status="REDUCED_DOSE", max_rpe=9, allow_failure=False)}
    monkeypatch.setattr(svc, "current_readiness", lambda user_id: contrato)
    svc.suggest_for_pair(object(), 1, 10, 7, logs=[])
    assert stubs.readiness is contrato


def test_dose_reduzida_explica_que_a_progressao_foi_adiada(monkeypatch):
    decisao = _decisao(decision=pe.HOLD, suggested_load_kg=50.0, evidence={pe.EVIDENCE_TRANSITION_DEFERRED: True})
    _Stubs(monkeypatch, {(10, 7): decisao})
    sugestao = svc.suggest_for_pair(object(), 1, 10, 7, readiness={}, logs=[_log(10, [_s(1, 50, 10)], equipment_id=7)])
    assert svc.progression_note(sugestao).startswith("Dose reduzida pela recuperação")


# ---------------------------------------------------------------------------
# Contrato de `GET /exercises/suggested-progression` (services)
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        return None

    def fetchone(self):
        return self.row


def test_suggested_progression_mantem_o_contrato_e_acrescenta_a_decisao(monkeypatch):
    from app import services

    @contextmanager
    def fake_transaction():
        yield CursorFactoryConn(_Cursor, {"id": 10})

    decisao = _decisao(
        decision=pe.INCREASE_LOAD,
        suggested_load_kg=62.5,
        effective_rpe_target=8,
        effective_rpe_ceiling=9,
        failure_allowed=False,
        evidence={"load_increment_kg": 2.5, "safety_clamps": [{"field": "rpe_target", "from": 10, "to": 9}]},
    )
    _Stubs(monkeypatch, {(10, 7): decisao}, steps={7: Decimal("2.5")})
    monkeypatch.setattr(services, "transaction", fake_transaction)
    monkeypatch.setattr(services, "_resolve_canonical_exercise_id", lambda mapping_id, cur: 10)
    monkeypatch.setattr(ei, "equipment_id_of_gym_equipment", lambda conn, gym_equipment_id: 7)
    monkeypatch.setattr(
        services,
        "list_exercise_logs",
        # PERF-04: a rota pede so as linhas do proprio exercicio (canonico).
        lambda limit, user_id, only_mapping_id: (
            [_log(10, [_s(1, 60, 12), _s(2, 60, 12)], equipment_id=7)] if only_mapping_id == 10 else []
        ),
    )
    monkeypatch.setattr(svc, "current_readiness", lambda user_id: {})

    resposta = services.suggested_progression_for_mapping(10, user_id=1, gym_equipment_id=33)

    # campos que o FE já lê (contrato mantido)
    assert resposta["reps_target"] == "10-12"
    assert resposta["load_target"] == "62,5kg"
    assert resposta["rpe_target"] == "8"
    assert resposta["has_history"] is True
    assert resposta["progression"] == "increased" and resposta["trend"] == "up"
    assert (resposta["last_load_kg"], resposta["last_reps"]) == (60.0, 12)
    assert isinstance(resposta["progression_note"], str) and resposta["progression_note"]
    # aditivos
    assert resposta["decision"] == pe.INCREASE_LOAD and resposta["suggested_reps"] == 12
    assert resposta["effective_rpe_ceiling"] == 9 and resposta["failure_allowed"] is False
    assert resposta["clamps"] == [{"field": "rpe_target", "from": 10, "to": 9}]
    assert resposta["load_increment_kg"] == 2.5 and resposta["equipment_id"] == 7
    assert resposta["engine_version"] == svc.PAIR_SUGGESTION_ENGINE_VERSION


# ---------------------------------------------------------------------------
# B1 + C1 no congelamento: linhas fora dos Modos A/B
# ---------------------------------------------------------------------------


def _patch_manual(monkeypatch, *, suggestions=None, falha=False):
    from app import progression_state as ps
    from app import services
    from app.generation import freeze_mode_b

    contrato = pe.ReadinessContract(muscle_group_id=3, status="REDUCED_DOSE", max_rpe=9, allow_failure=False)
    monkeypatch.setattr(freeze_mode_b, "projected_readiness", lambda user_id, plan_date, plan_time: {3: contrato})
    monkeypatch.setattr(freeze_mode_b, "equipment_by_gym_equipment", lambda conn, ids: {33: 7})
    monkeypatch.setattr(freeze_mode_b, "coherent_equipment_id", lambda conn, by_gym, row, plan_id: 7)
    monkeypatch.setattr(services, "list_exercise_logs", lambda limit, user_id: [])
    monkeypatch.setattr(ps, "load_primary_groups", lambda conn, ids: ({10: 3}, {10: 1.0}))

    def fake_suggest(conn, user_id, queries, *, readiness=None, logs=None, now=None):
        if falha:
            raise RuntimeError("motor fora")
        assert readiness == {3: contrato}
        return {query.key: suggestions[query.key] for query in queries if query.key in suggestions}

    monkeypatch.setattr(svc, "suggest_for_pairs", fake_suggest)
    return services


def _linha_manual():
    return {
        "workout_plan_exercise_id": 501,
        "sequence": 1,
        "exercise_name": "Supino",
        "mapping_id": 10,
        "equipment_name": "Banco",
        "preferred_equipment_id": 33,
        "sets_count": 3,
        "reps_target": "8-10",
        "load_target": "40kg",
        "rpe_target": "10",
        "advanced_technique": None,
    }


def _congelar(services, par):
    return services._manual_rows_snapshots(
        CursorFactoryConn(_Cursor, {"user_id": 1, "plan_date": date(2026, 10, 7), "plan_time": None}),
        900,
        [_linha_manual()],
        user_id=1,
        safety_by_row={501: par},
        source=pd.SOURCE_PLAN_CREATED,
        frozen_at=datetime(2026, 10, 6),
    )


def test_linha_manual_congela_com_a_decisao_do_motor_v2(monkeypatch):
    decisao = _decisao(
        decision=pe.HOLD,
        suggested_load_kg=40.0,
        effective_rpe_target=9,
        effective_rpe_ceiling=9,
        failure_allowed=False,
        safety_constraints={"max_safe_rpe": 10, "source": "equipment_default"},
    )
    sugestao = svc.PairSuggestion(
        exercise_id=10,
        equipment_id=7,
        decision=decisao.to_dict(),
        suggested_load_kg=40.0,
        load_source=svc.LOAD_SOURCE_ENGINE,
        load_increment_kg=2.5,
    )
    services = _patch_manual(monkeypatch, suggestions={(10, 7): sugestao})

    snap = _congelar(services, ts.build_training_safety(equipment_max_safe_rpe=10))[501]

    assert snap["engine_version"] == svc.PAIR_SUGGESTION_ENGINE_VERSION
    assert (snap["effective_rpe_ceiling"], snap["failure_allowed"]) == (9, False)
    assert snap["decision"] == pe.HOLD and snap["suggested_load_kg"] == 40.0
    assert snap["rpe_target"] == "10" and snap["reps_target"] == "8-10"  # o que a pessoa escreveu fica


def test_linha_manual_sem_decisao_compoe_com_o_contrato_do_grupo(monkeypatch):
    services = _patch_manual(monkeypatch, falha=True)

    snap = _congelar(services, ts.build_training_safety(equipment_max_safe_rpe=10))[501]

    assert snap["engine_version"] == pd.LEGACY_ENGINE_VERSION and snap["decision"] is None
    # B1: nada de teto 10 / falha permitida num grupo em dose reduzida
    assert (snap["effective_rpe_ceiling"], snap["failure_allowed"], snap["effective_rpe_target"]) == (9, False, 9)


def test_round_to_equipment_step_e_o_helper_publico_unico():
    assert pe.round_to_equipment_step("42,4", 5) == 40.0
    assert pe.round_to_equipment_step(43.75, None) == 45.0  # passo default 2,5
    assert pe.round_to_equipment_step(0.4, 2.5) == 2.5  # nunca zera uma carga positiva
    assert pe.round_to_equipment_step(None) is None and pe.round_to_equipment_step("x") is None
    assert pe.round_to_equipment_step(-5) is None
    # o Lote 1c consome o MESMO helper (sem cópia nem nome privado)
    from app import engine_decision_context

    assert engine_decision_context.round_to_equipment_step is pe.round_to_equipment_step
    assert not hasattr(pe, "_round_step")


# ---------------------------------------------------------------------------
# C6: falha permitida nos analytics = mesma regra do safety_event
# ---------------------------------------------------------------------------


class _BulkCursor(_Cursor):
    def fetchall(self):
        return self.row


def test_falha_permitida_nos_analytics_segue_a_prescricao_composta(monkeypatch):
    import json

    from app.domain import training_safety

    par_com_falha = ts.build_training_safety(exercise_id=10, equipment_id=7, equipment_max_safe_rpe=10)
    reduzida = pd.build_prescription(
        _linha(), safety=par_com_falha, readiness=ts.readiness_for_status(ts.REDUCED_DOSE)
    )
    linhas = [
        # ficha congelada em dose reduzida: o par permite falha, a sessao nao
        {"id": 1, "mapping_id": 10, "catalog_equipment_id": 7, "workout_plan_exercise_id": 900,
         "plan_rpe_target": "10", "prescription_json": json.dumps(reduzida)},
        # treino livre no mesmo par: so o par decide (como o avaliador)
        {"id": 2, "mapping_id": 10, "catalog_equipment_id": 7, "workout_plan_exercise_id": None},
        # sem equipamento: desconhecido -> sem falha (D3)
        {"id": 3, "mapping_id": 10, "catalog_equipment_id": None, "workout_plan_exercise_id": None},
    ]

    class _BulkConn:
        def cursor(self):
            return _BulkCursor(linhas)

    monkeypatch.setattr(training_safety, "resolve_training_safety_bulk", lambda conn, pairs: {(10, 7): par_com_falha})
    assert ei.failure_allowed_by_logged_exercise(_BulkConn(), [1, 2, 3]) == {1: False, 2: True, 3: False}
