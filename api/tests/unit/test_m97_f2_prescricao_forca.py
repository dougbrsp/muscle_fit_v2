"""M97 F2: prescricao de FORCA nos motores donos (mapa §66.2).

Sem MySQL. Prova, ramo a ramo, que o objetivo FORCA tem perfil proprio em
cada dono e que HIPERTROFIA (default) sai identica ao que era:

- `prescription_defaults.defaults_for` (reps/RPE/descanso/series/teto);
- `training_safety` (alvo 8, teto 9, tecnicas metabolicas fora);
- `progression_engine` (escada de uma fase, progressao dupla, platô = recuo);
- `next_set_load` (conjunto de forca, selecao pelo objetivo);
- `periodization/limits` + `time_estimate` (piso de exercicios, descanso);
- `methodology_volume` (peso da serie de forca no volume hipertrofico);
- cenario ponta a ponta: ficha de forca -> proxima serie -> finish -> trilha
  de forca progride e a de hipertrofia do MESMO par fica intacta.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from app.domain import prescription_defaults as pd
from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain import training_goal as tg
from app.domain import training_safety as ts
from app.domain.next_set_load import (
    DEFAULT_RULESET,
    STRENGTH_RULESET,
    SetFacts,
    evaluate_ruleset,
    ruleset_for_goal,
    ruleset_uses_published,
    validate_ruleset,
)
from app.domain.prescription import WARNING_MODEL_TECHNIQUE_INVALID, build_prescription
from app.domain.training_history import PrescribedSpec
from app.intelligence import methodology_volume as mv
from app.periodization import limits
from app.periodization.time_estimate import estimate_slot_seconds
from tests.support import progression_fixtures as fx

H = tg.TrainingGoal.HYPERTROPHY.value
S = tg.TrainingGoal.STRENGTH.value
PEITO = 10
SUPINO, BANCO = 1, 100
NOW = datetime(2026, 9, 1, 18)


# ---------------------------------------------------------------------------
# prescription_defaults
# ---------------------------------------------------------------------------


def test_defaults_de_hipertrofia_sao_as_constantes_de_sempre():
    hip = pd.defaults_for(None)
    assert hip is pd.defaults_for(H) is pd.HYPERTROPHY_DEFAULTS
    assert (hip.reps_target, hip.rpe_target) == (pd.DEFAULT_REPS_TARGET, pd.DEFAULT_RPE_TARGET)
    assert (hip.min_rest_seconds, hip.max_rest_seconds) == (pd.DEFAULT_MIN_REST_SECONDS, pd.DEFAULT_MAX_REST_SECONDS)
    assert hip.rest_seconds == pd.default_rest_seconds()
    assert hip.sets_count == pd.DEFAULT_SETS_COUNT
    assert pd.rest_seconds_sanity_max(None) == pd.REST_SECONDS_SANITY_MAX


def test_defaults_de_forca():
    forca = pd.defaults_for(S)
    assert forca.to_dict() == {
        "training_goal": S,
        "reps_target": "3-5",
        "rpe_target": "7-9",
        "min_rest_seconds": 180,
        "max_rest_seconds": 300,
        "rest_seconds": 240,
        "sets_count": 3,
        "rest_seconds_sanity_min": pd.REST_SECONDS_SANITY_MIN,
        "rest_seconds_sanity_max": 300,
    }
    # o teto de sanidade de forca nunca corta os 5 min da fonte
    assert pd.rest_seconds_sanity_max(S) >= forca.max_rest_seconds


@pytest.mark.parametrize("minimo,maximo", [(30, 90), (45, 120), (15, 240)])
def test_descanso_do_item_hipertrofia_e_o_ponto_medio_pedido_e_forca_o_dela(minimo, maximo):
    assert pd.item_rest_seconds(None, minimo, maximo) == pd.midpoint_rest_seconds(minimo, maximo)
    assert pd.item_rest_seconds(H, minimo, maximo) == pd.midpoint_rest_seconds(minimo, maximo)
    assert pd.item_rest_seconds(S, minimo, maximo) == 240
    assert pd.item_reps_target(None) == pd.DEFAULT_REPS_TARGET
    assert pd.item_reps_target(S) == "3-5"


# ---------------------------------------------------------------------------
# training_safety
# ---------------------------------------------------------------------------

_READY = ts.readiness_for_status(ts.READY)


@pytest.mark.parametrize("teto_par", [6, 7, 8, 9, 10])
@pytest.mark.parametrize("status", ["READY", "REDUCED_DOSE"])
@pytest.mark.parametrize("prescrito", [None, 7, 8, 9, 10])
def test_compose_hipertrofia_identico_com_e_sem_objetivo(teto_par, status, prescrito):
    safety = fx.safety(max_safe_rpe=teto_par)
    readiness = ts.readiness_for_status(status)
    assert ts.compose(safety, readiness, prescrito, training_goal=H) == ts.compose(safety, readiness, prescrito)
    assert ts.compose(safety, readiness, prescrito, training_goal=None) == ts.compose(safety, readiness, prescrito)


def test_compose_forca_alvo_8_teto_9_sem_falha():
    composition = ts.compose(fx.safety(max_safe_rpe=10), _READY, None, training_goal=S)
    assert (composition.target, composition.ceiling, composition.failure_allowed) == (8, 9, False)
    assert composition.clamps == ()
    # par que libera a falha: a negacao e informativa (nao e do par)
    assert composition.failure_denied_by == ts.FAILURE_DENIED_BY_RECOVERY


def test_compose_forca_corta_o_prescrito_pelo_teto_do_objetivo():
    composition = ts.compose(fx.safety(max_safe_rpe=10), _READY, 10, training_goal=S)
    assert composition.target == 9
    assert [clamp.reason for clamp in composition.clamps] == [ts.REASON_TRAINING_GOAL]
    # par mais restritivo que o objetivo: o motivo e o do par, como sempre
    par8 = ts.compose(fx.safety(max_safe_rpe=8), _READY, 9, training_goal=S)
    assert (par8.target, par8.ceiling) == (8, 8)
    assert [clamp.reason for clamp in par8.clamps] == [ts.REASON_EQUIPMENT_SAFETY]
    assert par8.failure_denied_by == ts.FAILURE_DENIED_BY_PAIR


def test_tecnicas_metabolicas_fora_para_forca_e_hipertrofia_intocada():
    safety = fx.safety(max_safe_rpe=10)
    assert ts.safety_for_goal(safety, H) is safety
    forca = ts.safety_for_goal(safety, S)
    assert forca.allowed_codes.isdisjoint(ts.STRENGTH_DISALLOWED_TECHNIQUES)
    bloqueadas = {item.code: item.reason for item in forca.blocked_techniques}
    for code in ("drop_set", "rest_pause", "fst7", "gvt"):
        assert bloqueadas[code] == ts.TECHNIQUE_NOT_ALLOWED_FOR_GOAL
        assert not ts.validate_technique(code, forca).valid
    assert ts.goal_allows_technique("forced_reps", S) and not ts.goal_allows_technique("drop_set", S)
    assert all(ts.goal_allows_technique(code, H) for code in fx.CATALOG)


# ---------------------------------------------------------------------------
# progression_engine
# ---------------------------------------------------------------------------


def _strength_track(**extra):
    base = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=100, is_compound=True)
    return replace(base, training_goal=S, **extra)


def test_escada_por_objetivo():
    assert pe.ladder_for(None) is pe.ladder_for(H) is pe.HYPERTROPHY_LADDER
    assert pe.HYPERTROPHY_LADDER.engine_version == pe.ENGINE_VERSION
    assert pe.HYPERTROPHY_LADDER.evidence() == {}
    forca = pe.ladder_for(S)
    assert forca.engine_version == pe.STRENGTH_ENGINE_VERSION and len(forca.engine_version) <= 40
    assert forca.evidence() == {pe.EVIDENCE_LADDER: pe.STRENGTH_LADDER_VERSION}
    assert pe.reps_for_phase(pe.F1, H) == pe.F1_REPS and pe.reps_for_phase(pe.F2, H) == pe.PROGRESSED_REPS
    assert pe.reps_for_phase(pe.F1, S) == pe.reps_for_phase(pe.F6, S) == pe.STRENGTH_REPS


def test_grupo_novo_de_forca_nasce_na_fase_de_carga_com_a_faixa_de_forca():
    hip = pe.new_group_state(PEITO, NOW)
    assert (hip.phase, hip.reps_range) == (pe.F1, pe.F1_REPS)
    assert pe.empty_group_state(None) == pe.GroupState(muscle_group_id=0)
    forca = pe.new_group_state(PEITO, NOW, S)
    assert (forca.phase, forca.reps_range, forca.training_goal) == (pe.STRENGTH_LOAD_PHASE, (3, 5), S)
    assert pe.empty_group_state(S).reps_range == (3, 5)


def _forca_exec(reps, rpe, weight=100):
    execution = fx.execution(
        exercise_id=SUPINO,
        equipment_id=BANCO,
        set_list=fx.sets(reps=reps, rpe=rpe, weight=weight),
        prescribed=PrescribedSpec(sets=3, reps_min=3, reps_max=5, rpe_target=Decimal(8)),
    )
    return replace(execution, training_goal=S)


@pytest.mark.parametrize(
    "rpe,esperado,motivo",
    [(7, pe.QUALIFY, None), (8, pe.QUALIFY, None), (9, pe.RESET, pe.REASON_RPE_ABOVE_TARGET)],
)
def test_forca_qualifica_no_topo_com_reserva(rpe, esperado, motivo):
    outcome = pe.evaluate_track_session(
        _forca_exec(5, rpe), _strength_track(), reps_min=3, reps_max=5, ceiling=9, failure_allowed=False
    )
    assert outcome.outcome == esperado and outcome.reason == motivo
    assert outcome.excess_reps is False


def test_forca_abaixo_do_topo_nao_qualifica():
    outcome = pe.evaluate_track_session(
        _forca_exec(4, 7), _strength_track(), reps_min=3, reps_max=5, ceiling=9, failure_allowed=False
    )
    assert (outcome.outcome, outcome.reason) == (pe.RESET, pe.REASON_REPS_BELOW_MAX)


def test_next_step_de_forca_nao_sobe_faixa_tecnica_nem_serie():
    grupo = pe.new_group_state(PEITO, NOW, S)
    corpo = replace(_strength_track(), load_status=pe.LOAD_NOT_APPLICABLE, working_load_kg=None)
    step = pe.next_step(grupo, [corpo], now=NOW)
    assert step.pending_action is None and step.phase_after == grupo.phase
    assert (step.reps_min, step.reps_max) == (3, 5)
    assert step.blocked_by == (pe.BLOCKED_STRENGTH_LADDER,)


def test_prescribe_forca_teto_alvo_versao_tecnica_e_series():
    grupo = pe.new_group_state(PEITO, NOW, S)
    track = replace(_strength_track(), sets_count=4)
    decision = pe.prescribe(
        track,
        grupo,
        fx.safety(exercise_id=SUPINO, equipment_id=BANCO, max_safe_rpe=10),
        now=NOW,
        catalog=fx.CATALOG,
        model_technique="drop_set",
        model_fixed_sets=False,
    )
    assert decision.engine_version == pe.STRENGTH_ENGINE_VERSION
    assert decision.evidence[pe.EVIDENCE_LADDER] == pe.STRENGTH_LADDER_VERSION
    assert (decision.suggested_reps_min, decision.suggested_reps_max) == (3, 5)
    assert (decision.effective_rpe_target, decision.effective_rpe_ceiling, decision.failure_allowed) == (8, 9, False)
    assert decision.advanced_technique is None  # drop-set fora da politica de forca
    assert decision.suggested_sets == pe.STRENGTH_MAX_SETS_COUNT  # Modo A: teto de forca
    # Modo B: o Modelo manda nas series (D9)
    fixed = pe.prescribe(track, grupo, fx.safety(exercise_id=SUPINO, equipment_id=BANCO), now=NOW, model_fixed_sets=True)
    assert fixed.suggested_sets == 4


def test_prescribe_hipertrofia_sem_marca_de_ramo():
    grupo = pe.new_group_state(PEITO, NOW)
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    decision = pe.prescribe(track, grupo, fx.safety(exercise_id=SUPINO, equipment_id=BANCO), now=NOW)
    assert decision.engine_version == pe.ENGINE_VERSION
    assert pe.EVIDENCE_LADDER not in decision.evidence


@pytest.mark.parametrize(
    "carga,passo,esperado",
    [(100, 2.5, Decimal("90")), (75, 2.5, Decimal("67.5")), (20, 5, Decimal("15")), (2.5, 2.5, None)],
)
def test_recuo_de_forca_no_passo_e_ao_menos_um_passo(carga, passo, esperado):
    track = replace(_strength_track(), working_load_kg=Decimal(str(carga)), load_increment_kg=Decimal(str(passo)))
    assert pe.strength_plateau_reset_load(track) == esperado


def test_estagnacao_so_conta_sessoes_na_carga_atual():
    track = _strength_track()

    def out(value, load):
        return pe.TrackSessionOutcome(key=track.key, outcome=value, working_load_kg=Decimal(load))

    tres = [out(pe.RESET, 100)] * pe.STRENGTH_STALL_SESSIONS
    assert pe.strength_stall_triggered(tres, track)
    assert not pe.strength_stall_triggered(tres[:-1], track)
    assert not pe.strength_stall_triggered(tres[:-1] + [out(pe.QUALIFY, 100)], track)
    assert not pe.strength_stall_triggered([out(pe.RESET, 95)] + tres[1:], track)
    # hipertrofia nunca recua por estagnacao (segue a D10)
    assert not pe.strength_stall_triggered(tres, replace(track, training_goal=H))


# ---------------------------------------------------------------------------
# next_set_load
# ---------------------------------------------------------------------------


def test_conjunto_pelo_objetivo():
    publicado = replace(DEFAULT_RULESET, version=7)
    assert ruleset_for_goal(None, publicado) is publicado
    assert ruleset_for_goal(H, None) is DEFAULT_RULESET
    assert ruleset_for_goal(S, publicado) is STRENGTH_RULESET
    assert ruleset_uses_published(H) and not ruleset_uses_published(S)
    assert validate_ruleset(STRENGTH_RULESET) == []
    assert (STRENGTH_RULESET.reps_floor, STRENGTH_RULESET.reps_ceiling) == pe.STRENGTH_REPS


@pytest.mark.parametrize(
    "reps,rpe,direcao,carga",
    [
        (5, 7, pe.INCREASE_LOAD, 105.0),  # teto com reserva: +5%
        (9, None, pe.INCREASE_LOAD, 110.0),  # bem acima do teto: +10%
        (2, 10, pe.DECREASE_LOAD, 90.0),  # falha antes do piso: -10%
        (4, 8, pe.HOLD, 100.0),
        (5, 9, pe.HOLD, 100.0),
    ],
)
def test_proxima_serie_de_forca(reps, rpe, direcao, carga):
    result = evaluate_ruleset(SetFacts(reps=reps, rpe=rpe, weight_kg=100, step_kg=2.5), STRENGTH_RULESET)
    assert (result.direction, result.load_kg) == (direcao, carga)


# ---------------------------------------------------------------------------
# periodization/limits + time_estimate
# ---------------------------------------------------------------------------


def test_piso_de_exercicios_e_descanso_do_complemento_por_objetivo():
    assert limits.session_exercises_min(None) == limits.SESSION_EXERCISES_MIN
    assert limits.session_exercises_min(S) == limits.STRENGTH_SESSION_EXERCISES_MIN < limits.SESSION_EXERCISES_MIN
    itens_forca = [{"item_kind": "equipment", "training_goal": S}, {"item_kind": "cardio"}]
    assert limits.slot_items_goal(itens_forca) is tg.TrainingGoal.STRENGTH
    assert limits.slot_items_goal([{"item_kind": "equipment"}], S) is tg.TrainingGoal.STRENGTH
    assert limits.slot_items_goal([{"training_goal": S}, {"training_goal": None}]) is tg.TrainingGoal.HYPERTROPHY
    assert limits.complement_default_rest_seconds({}) == limits.COMPLEMENT_DEFAULT_REST_SECONDS
    assert limits.complement_default_rest_seconds({"training_goal": S}) == 300


def test_estimativa_de_tempo_com_descanso_por_item():
    itens = [
        {"item_kind": "equipment", "sets_count": 3, "reps_target": "10"},
        {"item_kind": "equipment", "sets_count": 3, "reps_target": "4", "training_goal": S},
    ]
    sempre = estimate_slot_seconds(itens, limits.COMPLEMENT_DEFAULT_REST_SECONDS)
    por_item = estimate_slot_seconds(itens, limits.COMPLEMENT_DEFAULT_REST_SECONDS, limits.complement_default_rest_seconds)
    assert por_item - sempre == 3 * (300 - 90)
    so_hip = itens[:1]
    assert estimate_slot_seconds(so_hip, 90, limits.complement_default_rest_seconds) == estimate_slot_seconds(so_hip, 90)


# ---------------------------------------------------------------------------
# methodology_volume (volume hipertrofico)
# ---------------------------------------------------------------------------


def test_peso_da_serie_de_forca_no_volume_hipertrofico():
    assert mv.hypertrophy_volume_weight(None) == 1.0
    assert mv.hypertrophy_volume_weight(S) == mv.STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT == 0.5
    assert mv.hypertrophy_volume_units(3, H) == 3 and isinstance(mv.hypertrophy_volume_units(3, H), int)
    assert mv.hypertrophy_volume_units(3.0, S) == 1.5


def test_metodologia_pondera_o_item_de_forca():
    from tests.unit.test_fase5_planejado_tecnica import PRIMARY_COM_ROSCA_B, ROSCA_B
    from tests.unit.test_methodology_volume import BICEPS, GROUPS, SEED, block

    def avaliar(goal):
        sessao = (
            mv.MethodologyItemInput(mapping_id=104, canonical_exercise_id=104, sets_count=4, training_goal=goal),
            mv.MethodologyItemInput(mapping_id=ROSCA_B, canonical_exercise_id=ROSCA_B, sets_count=2),
        )
        slots = [mv.MethodologySlotInput(sequence=1, slot_type="normal", name="A", items=sessao)]
        slots += [mv.MethodologySlotInput(sequence=s, slot_type="descanso", name="D") for s in range(2, 8)]
        return block(
            mv.evaluate_methodology_volume(slots, PRIMARY_COM_ROSCA_B, GROUPS, "equipment_series", None, SEED), BICEPS
        )

    assert avaliar(None) == avaliar(H)
    assert avaliar(H).direct_sets_per_session == 6
    assert avaliar(S).direct_sets_per_session == 4  # 4 x 0,5 + 2


# ---------------------------------------------------------------------------
# Ponta a ponta: ficha de forca -> proxima serie -> finish
# ---------------------------------------------------------------------------


def _sessoes_forca_e_hipertrofia():
    """Supino no banco: hipertrofia (12 reps a 40 kg) e, na mesma sessao, o
    supino de FORCA do mesmo par. Forca: calibra a 70 kg, bate 3x5 @7 (sobe),
    depois estagna 3 sessoes a 75 kg (recua)."""
    forca_sets = [
        fx.sets(reps=5, rpe=7, weight=70),  # calibra
        fx.sets(reps=5, rpe=7, weight=70),  # topo com reserva -> +5 kg
        fx.sets(reps=4, rpe=9, weight=75),
        fx.sets(reps=4, rpe=9, weight=75),
        fx.sets(reps=4, rpe=9, weight=75),  # 3a sem qualificar a 75 -> recuo
    ]
    sessions = []
    for index, set_list in enumerate(forca_sets):
        hip = fx.execution(exercise_id=SUPINO, equipment_id=BANCO, sequence=1, set_list=fx.sets(reps=12, rpe=9))
        forca = replace(
            fx.execution(
                exercise_id=SUPINO,
                equipment_id=BANCO,
                sequence=2,
                set_list=set_list,
                prescribed=PrescribedSpec(sets=3, reps_min=3, reps_max=5, rpe_target=Decimal(8)),
            ),
            exercise_row_id=9000 + index,
            training_goal=S,
        )
        sessions.append(fx.session(activity_id=100 + index, day=f"2026-08-{1 + 3 * index:02d}", executions=[hip, forca]))
    return sessions


def _inputs():
    return fx.inputs(tracks=[fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, is_compound=True)])


def test_cenario_forca_ponta_a_ponta():
    sessions = _sessoes_forca_e_hipertrofia()
    key = pe.track_key(SUPINO, BANCO, pe.DEFAULT_FATIGUE_CONTEXT, S)

    # 1) ficha de forca: snapshot com o teto e a politica do objetivo
    snapshot = build_prescription(
        {"training_goal": S, "rpe_target": "7-9", "advanced_technique": "drop_set"},
        safety=fx.safety(exercise_id=SUPINO, equipment_id=BANCO, max_safe_rpe=10),
    )
    assert snapshot["training_goal"] == S
    assert (snapshot["effective_rpe_target"], snapshot["effective_rpe_ceiling"]) == (9, 9)
    assert snapshot["failure_allowed"] is False
    assert WARNING_MODEL_TECHNIQUE_INVALID in snapshot["warnings"]

    # 2) proxima serie: o conjunto de forca decide (3x5 @7 -> +5%)
    serie = evaluate_ruleset(SetFacts(reps=5, rpe=7, weight_kg=70, step_kg=2.5), ruleset_for_goal(S))
    assert (serie.direction, serie.load_kg) == (pe.INCREASE_LOAD, 72.5)

    # 3) finish das duas primeiras sessoes: a trilha de forca arma +5 kg
    duas = ev.evaluate_history_by_goal(sessions[:2], inputs=_inputs())
    forca = duas.results[S]
    track = forca.tracks[key]
    assert track.working_load_kg == Decimal("70") and track.pending_load_kg == Decimal("75")
    grupo = forca.group_states[PEITO]
    assert (grupo.phase, grupo.reps_range) == (pe.STRENGTH_LOAD_PHASE, (3, 5))
    ultima = forca.decisions[-1]
    assert ultima.decision == pe.INCREASE_LOAD and ultima.engine_version == pe.STRENGTH_ENGINE_VERSION
    proxima = pe.prescribe(
        track, grupo, fx.safety(exercise_id=SUPINO, equipment_id=BANCO), now=datetime(2026, 8, 7, 18)
    )
    assert (proxima.decision, proxima.suggested_load_kg) == (pe.INCREASE_LOAD, 75.0)
    assert (proxima.suggested_reps_min, proxima.suggested_reps_max) == (3, 5)

    # 4) tres sessoes estagnadas a 75 kg: recuo para 90% (67,5 kg) e reconstroi
    todas = ev.evaluate_history_by_goal(sessions, inputs=_inputs())
    forca = todas.results[S]
    assert forca.tracks[key].working_load_kg == Decimal("67.5")
    recuo = forca.decisions[-1]
    assert recuo.decision == pe.DECREASE_LOAD
    assert recuo.evidence[pe.EVIDENCE_STRENGTH_PLATEAU_RESET][pe.track_label(key)]["to_kg"] == 67.5
    assert forca.group_states[PEITO].phase == pe.STRENGTH_LOAD_PHASE  # nunca F3

    # 5) a trilha de hipertrofia do MESMO par: identica ao historico sem forca
    so_hip = [replace(session, exercises=session.exercises[:1]) for session in sessions]
    referencia = ev.evaluate_history(so_hip, inputs=_inputs())
    hip = todas.results[H]
    assert hip.tracks == referencia.tracks
    assert hip.group_states == referencia.group_states
    assert [d.to_dict() for d in hip.decisions] == [d.to_dict() for d in referencia.decisions]
    assert all(d.engine_version == pe.ENGINE_VERSION for d in hip.decisions)


# ---------------------------------------------------------------------------
# Volume realizado: overview e ativacao diaria (linhas reais do VolumeService)
# ---------------------------------------------------------------------------

_SEM_COLUNA = object()  # linha sem `training_goal` (forma anterior a F2)


def _overview_com_objetivo(goal):
    from app.intelligence.volume_service import VolumeService
    from tests.unit import test_fase5_volume_realizado as t5

    rows = t5._overview_rows(t5.TRABALHO)
    if goal is not _SEM_COLUNA:
        for row in rows:
            row["training_goal"] = goal
    service = VolumeService()
    service._technique_catalog = t5._catalog()
    service._fetch_window_sets = lambda days, user_id=None: [dict(r) for r in rows]
    service._fetch_full_history_sets = lambda user_id=None, history_days=None: [dict(r) for r in rows]
    service.fetch_all = lambda *a, **k: pytest.fail("teste unitario nao toca o banco")
    now = datetime.now()
    items = service.get_muscle_group_overview(days=14, limit=10, user_id=1, now_local=now)
    return {item["muscle_group"]: item for item in items}


def _estavel(item):
    """Campos que nao dependem do relogio da execucao do teste."""
    return {k: v for k, v in item.items() if "hours" not in k and not k.endswith("_at") and "recovery" not in k}


def test_overview_hipertrofia_identico_e_forca_pondera_so_o_estimulo():
    antes = _overview_com_objetivo(_SEM_COLUNA)
    hip = _overview_com_objetivo(H)
    forca = _overview_com_objetivo(S)
    for grupo in antes:
        assert _estavel(hip[grupo]) == _estavel(antes[grupo])
    assert forca["Costas"]["direct_sets"] == antes["Costas"]["direct_sets"] * 0.5
    assert forca["Bíceps"]["stimulus_equivalent_sets"] == pytest.approx(
        antes["Bíceps"]["stimulus_equivalent_sets"] * 0.5
    )
    # contagem bruta, fadiga e dose seguem cheias
    for campo in ("sets_count", "fatigue_equivalent_sets", "session_dose"):
        assert forca["Costas"][campo] == antes["Costas"][campo]


def _ativacao_com_objetivo(monkeypatch, goal):
    from app.intelligence import muscle_contribution_resolver as resolver
    from app.intelligence.volume_service import VolumeService
    from tests.unit import test_daily_muscle_activation_direct_sets as tda

    rows = [dict(row) if goal is _SEM_COLUNA else dict(row, training_goal=goal) for row in tda.ROWS]
    service = VolumeService()
    service._technique_catalog = {}
    service.fetch_all = lambda sql, params=(): [dict(row) for row in rows]
    monkeypatch.setattr(
        resolver, "_resolve_muscle_contributions_for_mappings", lambda ids: {k: tda.CONTRIBUTIONS[k] for k in ids}
    )
    items = service.get_daily_muscle_activation(days=7, user_id=1)
    return {(item["local_date"], item["muscle_group"]): item for item in items}


def test_ativacao_diaria_hipertrofia_identica_e_forca_ponderada(monkeypatch):
    antes = _ativacao_com_objetivo(monkeypatch, _SEM_COLUNA)
    assert _ativacao_com_objetivo(monkeypatch, H) == antes
    forca = _ativacao_com_objetivo(monkeypatch, S)
    chave = ("2026-09-28", "Peitoral")
    assert forca[chave]["direct_sets"] == antes[chave]["direct_sets"] * 0.5
    assert forca[chave]["sets_count"] == pytest.approx(antes[chave]["sets_count"] * 0.5)


def test_textos_de_forca_derivam_das_constantes_dos_donos():
    assert pd.STRENGTH_REPS_TARGET == f"{pe.STRENGTH_REPS[0]}-{pe.STRENGTH_REPS[1]}"
    assert pd.STRENGTH_RPE_TARGET == f"{pd.STRENGTH_RPE_FLOOR}-{ts.STRENGTH_RPE_CEILING}"
