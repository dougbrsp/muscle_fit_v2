"""Carga da PRÓXIMA série dentro da sessão (regra do usuário de 2026-10-12).

Dono: `app/domain/next_set_load.py`. Consumidores: `bound_ai_load` (carga
final da IA por série), o prompt set-eval-v6, o salvamento da série
(`workout_sessions.add_session_set`) e o Modo Treino.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import exercise_set_evaluation_ai as set_ai
from app import prompt_store, workout_sessions
from app.domain import effective_intensity as ei
from app.domain import next_set_load as nsl
from app.domain import progression_engine as pe
from app.engine_decision_context import (
    EngineDecision,
    PairEngineContext,
    REASON_ENGINE_TARGET,
    bound_ai_load,
    next_set_load_for,
)
from app.intelligence.muscle_recovery_service import MUSCLE_PAIN_BLOCK_ABOVE
from app.domain.training_safety import FAILURE_RPE, IntensityComposition
from tests.support.fake_db import NullContext, fake_transaction
from tests.support.privado import exigir

PASSO = Decimal("2.5")


def _sugerir(reps, peso, rpe, **extra):
    return nsl.suggest_next_set_load(set_type="working", reps=reps, weight_kg=peso, rpe=rpe, step_kg=PASSO, **extra)


# ---------------------------------------------------------------------------
# 1. A regra (tabela do usuário + premissas do TL)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reps", "peso", "rpe", "extra", "direcao", "carga", "motivo"),
    [
        (16, 20, 8, {}, pe.INCREASE_LOAD, 22.5, nsl.REASON_REPS_ABOVE_LIMIT),
        (15, 20, 10, {}, pe.HOLD, 20.0, nsl.REASON_WITHIN_RULE),
        (12, 20, 8, {}, pe.INCREASE_LOAD, 22.5, nsl.REASON_LOW_RPE_AT_REPS),
        (12, 20, 8.5, {}, pe.INCREASE_LOAD, 22.5, nsl.REASON_LOW_RPE_AT_REPS),
        (12, 20, 9, {}, pe.HOLD, 20.0, nsl.REASON_WITHIN_RULE),
        (11, 20, 7, {}, pe.HOLD, 20.0, nsl.REASON_WITHIN_RULE),
        (5, 20, 10, {}, pe.DECREASE_LOAD, 17.5, nsl.REASON_FAILURE_BEFORE_MIN_REPS),
        (6, 20, 10, {}, pe.HOLD, 20.0, nsl.REASON_WITHIN_RULE),
        (5, 20, 9.5, {}, pe.HOLD, 20.0, nsl.REASON_WITHIN_RULE),
        (16, 20, 8, {"muscle_pain": 9}, pe.DECREASE_LOAD, 17.5, nsl.REASON_PAIN),
        (16, 20, 8, {"muscle_pain": MUSCLE_PAIN_BLOCK_ABOVE}, pe.INCREASE_LOAD, 22.5, nsl.REASON_REPS_ABOVE_LIMIT),
        (10, 20, 8, {"joint_pain": True}, pe.DECREASE_LOAD, 17.5, nsl.REASON_PAIN),
        (16, 20, None, {}, pe.INCREASE_LOAD, 22.5, nsl.REASON_REPS_ABOVE_LIMIT),
        (12, 20, None, {}, pe.HOLD, 20.0, nsl.REASON_WITHIN_RULE),
        (4, 20, None, {}, pe.HOLD, 20.0, nsl.REASON_WITHIN_RULE),
        (16, 20, 8, {"load_convention": "bodyweight_plus"}, pe.INCREASE_LOAD, 22.5, nsl.REASON_REPS_ABOVE_LIMIT),
    ],
)
def test_tabela_da_regra(reps, peso, rpe, extra, direcao, carga, motivo):
    sugestao = _sugerir(reps, peso, rpe, **extra)
    assert (sugestao.direction, sugestao.load_kg, sugestao.reason) == (direcao, carga, motivo)
    assert sugestao.executed_kg == float(peso)
    assert sugestao.direction_label == pe_label(direcao)


def pe_label(code):
    from app.domain.exercise_briefing import DECISION_LABELS

    return DECISION_LABELS[code]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"set_type": "warmup"},
        {"set_type": "feeler"},
        {"set_type": "drop_set"},
        {"set_type": "rest_pause"},
        {"weight_kg": 0},
        {"weight_kg": None},
        {"reps": None},
        {"reps": 0},
        {"load_convention": "assisted"},
        {"load_convention": "bodyweight"},
    ],
)
def test_fora_da_regra_nao_sugere(kwargs):
    base = {"set_type": "working", "reps": 16, "weight_kg": 20, "rpe": 8, "step_kg": PASSO}
    assert nsl.suggest_next_set_load(**{**base, **kwargs}) is None


def test_passo_do_equipamento_e_arredondamento():
    # passo 1 kg
    assert nsl.suggest_next_set_load(set_type="working", reps=16, weight_kg=20, rpe=8, step_kg=1).load_kg == 21.0
    # carga fora do passo (libras): executada ± passo, arredondada ao passo (meio para cima)
    assert _sugerir(16, 21.3, 8).load_kg == 25.0  # 23,8 arredonda para 25
    assert _sugerir(5, 21.3, 10).load_kg == 20.0
    assert _sugerir(10, 33, 8, joint_pain=True).load_kg == 30.0  # 30,5 arredonda para 30
    # manter devolve a carga executada EXATA (alcançável)
    assert _sugerir(10, 45.36, 9).load_kg == 45.36
    # sem passo do equipamento: o default do motor
    sem_passo = nsl.suggest_next_set_load(set_type="working", reps=16, weight_kg=20, rpe=8)
    assert sem_passo.step_kg == float(pe.DEFAULT_LOAD_STEP_KG)


def test_reduzir_sem_passo_abaixo_fica_sem_carga():
    sugestao = _sugerir(4, 2.5, 10)
    assert sugestao.direction == pe.DECREASE_LOAD and sugestao.load_kg is None


def test_constantes_e_rotulos_vem_das_fontes():
    assert nsl.NEXT_SET_FAILURE_RPE == FAILURE_RPE
    rotulos = nsl.reason_labels()
    assert set(rotulos) == set(nsl.NEXT_SET_REASONS)
    assert f"{MUSCLE_PAIN_BLOCK_ABOVE}/10" in rotulos[nsl.REASON_PAIN]
    assert f"Mais de {nsl.NEXT_SET_INCREASE_ABOVE_REPS} repetições" in rotulos[nsl.REASON_REPS_ABOVE_LIMIT]
    assert f"RPE {FAILURE_RPE} antes de {nsl.NEXT_SET_DECREASE_BELOW_REPS}" in rotulos[nsl.REASON_FAILURE_BEFORE_MIN_REPS]


# ---------------------------------------------------------------------------
# 2. bound_ai_load obedece a regra
# ---------------------------------------------------------------------------

ALVO_25 = PairEngineContext(step_kg=PASSO, decision=EngineDecision(decision=pe.HOLD, load_kg=25.0))


def test_ia_nao_sobe_ate_o_alvo_do_motor_quando_a_regra_manda_manter():
    regra = _sugerir(10, 20, 10)
    carga, ajustes = bound_ai_load(25.0, ALVO_25, executed_kg=20, rule=regra)
    assert carga == 20.0
    assert ajustes == [{"field": "load_kg", "from": 25.0, "to": 20.0, "reason": nsl.REASON_NEXT_SET_RULE}]


def test_ia_nao_mantem_quando_a_regra_manda_subir_mesmo_acima_do_alvo():
    regra = _sugerir(17, 25, 10)
    carga, ajustes = bound_ai_load(25.0, ALVO_25, executed_kg=25, rule=regra)
    assert carga == 27.5
    assert [a["reason"] for a in ajustes] == [nsl.REASON_NEXT_SET_RULE]
    assert REASON_ENGINE_TARGET not in [a["reason"] for a in ajustes]


def test_ia_igual_a_regra_sem_ajuste():
    assert bound_ai_load(17.5, ALVO_25, executed_kg=20, rule=_sugerir(5, 20, 10)) == (17.5, [])


def test_ia_sem_carga_recebe_a_da_regra():
    carga, ajustes = bound_ai_load(None, ALVO_25, executed_kg=20, rule=_sugerir(16, 20, 8))
    assert carga == 22.5 and ajustes[0]["from"] is None


def test_fora_da_regra_continua_o_teto_do_motor():
    carga, ajustes = bound_ai_load(30.0, ALVO_25, executed_kg=20, rule=None)
    assert carga == 25.0 and ajustes[-1]["reason"] == REASON_ENGINE_TARGET


def test_apply_engine_load_bound_categoria_e_a_direcao_da_regra():
    regra = _sugerir(10, 20, 10)
    resultado = set_ai.apply_engine_load_bound(
        {"category": pe.INCREASE_LOAD, "suggested_next_load_kg": 25.0, "clamps": []},
        ALVO_25,
        executed_kg=20,
        next_set=regra,
    )
    assert resultado["suggested_next_load_kg"] == 20.0
    assert resultado["category"] == pe.HOLD and resultado["category_from_ai"] == pe.INCREASE_LOAD
    assert resultado["category_label"] == "Manter"
    assert resultado["next_set_load"]["direction"] == pe.HOLD
    assert resultado["next_set_load"]["reason"] == nsl.REASON_WITHIN_RULE


def test_apply_engine_load_bound_reduzir_sem_passo_mantem_a_direcao():
    regra = _sugerir(4, 2.5, 10)
    resultado = set_ai.apply_engine_load_bound(
        {"category": pe.HOLD, "suggested_next_load_kg": 2.5, "clamps": []},
        PairEngineContext(step_kg=PASSO),
        executed_kg=2.5,
        next_set=regra,
    )
    assert resultado["suggested_next_load_kg"] is None
    assert resultado["category"] == pe.DECREASE_LOAD


def test_next_set_load_for_usa_passo_e_convencao_do_contexto():
    contexto = PairEngineContext(step_kg=Decimal("1"), load_convention="assisted")
    assert next_set_load_for({"reps": 16, "weight_kg": 20, "rpe": 8}, contexto) is None
    contexto = PairEngineContext(step_kg=Decimal("1"))
    assert next_set_load_for({"reps": 16, "weight_kg": 20, "rpe": 8}, contexto).load_kg == 21.0
    assert next_set_load_for({"reps": 16, "weight_kg": 20, "rpe": 8, "set_type": "drop_set"}, contexto) is None


# ---------------------------------------------------------------------------
# 3. Prompt set-eval-v6: sugestão pronta, sem número à mão
# ---------------------------------------------------------------------------

PAYLOAD = {
    "exercise_name": "Remada",
    "set_number": 1,
    "sets_count": 3,
    "reps_target": "10-12",
    "load_target": "40kg",
    "rpe_target": "10",
    "reps": 17,
    "weight_kg": 40,
    "rpe": 10,
}


def test_prompt_recebe_a_sugestao_pronta_do_motor():
    regra = next_set_load_for(PAYLOAD, PairEngineContext(step_kg=PASSO))
    composicao = IntensityComposition(ceiling=10, failure_allowed=True, target=10)
    motor = PairEngineContext(step_kg=PASSO)
    prompt = set_ai.build_prompt(PAYLOAD, "", engine=motor, composition=composicao, next_set=regra)
    assert set_ai.PROMPT_VERSION == "set-eval-v8"
    exigir('INCREASE_LOAD ("Aumentar carga") → 42,5 kg (executado 40 kg; passo 2,5 kg).' in prompt, "direção e carga")
    exigir(f"- Motivo: {nsl.reason_labels()[nsl.REASON_REPS_ABOVE_LIMIT]}." in prompt, "motivo do dono")
    # v8: a regra que casou e o conjunto vigente vêm de `describe_rule`/`describe_ruleset` (dono)
    exigir(f"- Regra que casou: {regra.rule_text}." in prompt, "regra que casou")
    for linha in nsl.describe_ruleset(nsl.DEFAULT_RULESET).splitlines():
        exigir(linha in prompt, "regra do conjunto vigente")
    # a regra antiga ("RPE 10 nunca reduz", "só dentro do limite do motor") sai
    # quando a regra vale: a regra crítica, a decisão e o escopo são os da v6
    exigir(set_ai._format_intensity_rule(PAYLOAD, composicao, regra) in prompt, "regra crítica da v6")
    exigir(set_ai._format_intensity_rule(PAYLOAD, composicao) not in prompt, "sem a regra crítica da v5")
    exigir(set_ai._format_engine_section(motor, regra) in prompt, "decisão como contexto da sessão")
    exigir(set_ai._format_engine_section(motor) not in prompt, "sem a decisão da v5")
    exigir(set_ai._text(".escopo_proxima_serie") in prompt, "escopo da regra da próxima série")


def test_prompt_fora_da_regra_continua_como_v5():
    motor = PairEngineContext(step_kg=PASSO)
    prompt = set_ai.build_prompt(PAYLOAD, "", engine=motor)
    cabecalho = prompt_store.load(set_ai.PROMPT_FEATURE_DIR, f"{set_ai.PROMPT_VERSION}.proxima_serie").split("(")[0]
    exigir(cabecalho not in prompt, "sem a seção da próxima série")
    exigir(set_ai._format_engine_section(motor) in prompt, "decisão do motor da v5")


# ---------------------------------------------------------------------------
# 4. Salvamento da série devolve a sugestão (sem IA)
# ---------------------------------------------------------------------------


class _CursorDaSerie(NullContext):
    def __init__(self):
        self.executados: list[str] = []

    def execute(self, sql, params=None):
        self.executados.append(sql)

    def fetchone(self):
        return {
            "id": 5,
            "plan_technique": None,
            "plan_reps_target": "10-12",
            "prescription_json": None,
            "mapping_id": 11,
            "gym_equipment_id": 2,
        }

    def fetchall(self):
        return []


def _salvar(monkeypatch, payload, contexto):
    cursor = _CursorDaSerie()
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(workout_sessions, "ensure_valid_codes", lambda codes: None)
    monkeypatch.setattr(workout_sessions, "_get_live_session", lambda cur, sid, uid: {"id": sid})
    monkeypatch.setattr(workout_sessions, "_refresh_exercise_aggregates", lambda cur, eid: None)
    monkeypatch.setattr(workout_sessions, "_refresh_activity_avg_rpe", lambda cur, sid: None)
    monkeypatch.setattr(workout_sessions, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(
        workout_sessions.effective_intensity,
        "resolve_for_logged_exercise",
        lambda conn, eid: ei.EffectiveIntensity(rpe_target_effective=10, ceiling=10, failure_allowed=True),
    )
    lidos = []

    def fake_contexto(conn, mapping_id, gym_equipment_id):
        lidos.append((mapping_id, gym_equipment_id))
        if isinstance(contexto, Exception):
            raise contexto
        return contexto

    monkeypatch.setattr(workout_sessions, "load_pair_step_context", fake_contexto)
    resposta = workout_sessions.add_session_set(1, 5, payload, identity=SimpleNamespace(user_id=1))
    return resposta, lidos


def test_salvar_serie_devolve_a_carga_da_proxima_serie(monkeypatch):
    payload = workout_sessions.SessionSetIn(set_number=1, reps=17, weight_kg=Decimal(40), rpe=Decimal(10))
    resposta, lidos = _salvar(monkeypatch, payload, PairEngineContext(step_kg=PASSO))
    assert lidos == [(11, 2)]
    proxima = resposta["next_set_load"]
    assert proxima["direction"] == pe.INCREASE_LOAD and proxima["load_kg"] == 42.5
    assert proxima["direction_label"] == "Aumentar carga"
    assert proxima["reason"] == nsl.REASON_REPS_ABOVE_LIMIT
    assert proxima["reason_label"] == nsl.reason_labels()[nsl.REASON_REPS_ABOVE_LIMIT]


def test_salvar_serie_de_tecnica_nao_sugere(monkeypatch):
    payload = workout_sessions.SessionSetIn(
        set_number=2, set_type="drop_set", reps=17, weight_kg=Decimal(32), rpe=Decimal(10), technique_code="DROP_SET"
    )
    resposta, _ = _salvar(monkeypatch, payload, PairEngineContext(step_kg=PASSO))
    assert resposta["next_set_load"] is None


def test_falha_ao_ler_o_par_usa_o_passo_default_e_nao_derruba_a_serie(monkeypatch):
    payload = workout_sessions.SessionSetIn(set_number=1, reps=4, weight_kg=Decimal(40), rpe=Decimal(10))
    resposta, _ = _salvar(monkeypatch, payload, RuntimeError("banco fora"))
    assert resposta["status"] == "ok"
    assert resposta["next_set_load"]["direction"] == pe.DECREASE_LOAD
    assert resposta["next_set_load"]["step_kg"] == float(pe.DEFAULT_LOAD_STEP_KG)


# ---------------------------------------------------------------------------
# 5. Rampa PLANEJADA de RPE como exceção (decisão do usuário, 2026-10-12)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("serie", "rpe", "direcao", "motivo"),
    [
        (0, 8, pe.HOLD, nsl.REASON_WITHIN_PLANNED_RAMP),  # série 1 planejada 8: 12@8 mantém
        (0, 7, pe.INCREASE_LOAD, nsl.REASON_LOW_RPE_AT_REPS),  # 12@7 abaixo do planejado: sobe
        (1, 8, pe.INCREASE_LOAD, nsl.REASON_LOW_RPE_AT_REPS),  # série 2 planejada 9: 12@8 sobe
        (2, 8, pe.INCREASE_LOAD, nsl.REASON_LOW_RPE_AT_REPS),  # série 3 planejada 10: limiar fica 9
        (5, 9, pe.HOLD, nsl.REASON_WITHIN_RULE),  # rampa mais curta repete o último degrau
    ],
)
def test_rampa_planejada_8_9_10(serie, rpe, direcao, motivo):
    planejado = nsl.planned_ramp_rpe("8-9-10", serie)
    sugestao = _sugerir(12, 20, rpe, planned_rpe=planejado)
    assert (sugestao.direction, sugestao.reason) == (direcao, motivo)


def test_rampa_nao_muda_reps_acima_do_limite_reducao_nem_dor():
    planejado = nsl.planned_ramp_rpe("7-8-9", 0)  # planejada 7
    assert _sugerir(16, 20, 7, planned_rpe=planejado).direction == pe.INCREASE_LOAD
    assert _sugerir(5, 20, 10, planned_rpe=planejado).direction == pe.DECREASE_LOAD
    assert _sugerir(12, 20, 7, planned_rpe=planejado, muscle_pain=9).reason == nsl.REASON_PAIN


def test_rotulo_cita_a_rampa_com_o_alvo_da_serie():
    sugestao = _sugerir(12, 20, 8, planned_rpe=nsl.planned_ramp_rpe("8-9-10", 0))
    assert sugestao.reason_label == "RPE dentro da rampa planejada (alvo 8 nesta série)"
    assert sugestao.to_dict()["planned_rpe"] == 8.0
    meio = _sugerir(12, 20, 8.5, planned_rpe=nsl.planned_ramp_rpe("8.5-9-10", 0))
    assert meio.reason_label == "RPE dentro da rampa planejada (alvo 8,5 nesta série)"


@pytest.mark.parametrize("alvo", ["9", "10", "9-9", None, "", "RPE alto"])
def test_alvo_unico_ou_sem_alvo_nao_e_rampa(alvo):
    assert nsl.planned_ramp_rpe(alvo, 0) is None
    assert _sugerir(12, 20, 8, planned_rpe=nsl.planned_ramp_rpe(alvo, 0)).direction == pe.INCREASE_LOAD


def test_payload_da_ia_usa_rpe_target_e_series_de_trabalho_anteriores():
    contexto = PairEngineContext(step_kg=PASSO)
    base = {"reps": 12, "weight_kg": 20, "rpe": 8, "rpe_target": "8-9-10"}
    primeira = next_set_load_for({**base, "previous_sets_this_session": [{"set_type": "warmup"}]}, contexto)
    assert primeira.direction == pe.HOLD and primeira.planned_rpe == 8.0
    anteriores = [{"set_type": "warmup"}, {"set_type": None}]
    segunda = next_set_load_for({**base, "previous_sets_this_session": anteriores}, contexto)
    assert segunda.direction == pe.INCREASE_LOAD and segunda.planned_rpe == 9.0
    livre = next_set_load_for({**base, "rpe_target": None}, contexto)
    assert livre.direction == pe.INCREASE_LOAD and livre.planned_rpe is None


def test_prompt_v7_diz_a_rampa_so_quando_ha_rampa():
    contexto = PairEngineContext(step_kg=PASSO)
    payload = {**PAYLOAD, "reps": 12, "rpe": 8, "rpe_target": "8-9-10"}
    proxima = next_set_load_for(payload, contexto)
    prompt = set_ai.build_prompt(payload, "", engine=contexto, next_set=proxima)
    rampa = set_ai._text(".rampa", {"rpe_planejado": "8"})
    exigir(proxima.planned_rpe == 8.0 and rampa in prompt, "rampa com o RPE planejado da série")
    exigir("RPE dentro da rampa planejada (alvo 8 nesta série)" in prompt, "motivo da rampa (dono)")
    sem = set_ai.build_prompt(PAYLOAD, "", engine=contexto, next_set=next_set_load_for(PAYLOAD, contexto))
    exigir(rampa.split(":")[0] not in sem, "sem rampa, sem a linha")


class _CursorComFicha(_CursorDaSerie):
    def __init__(self, anteriores):
        super().__init__()
        self.anteriores = anteriores

    def fetchone(self):
        if self.executados and "COUNT(*)" in self.executados[-1]:
            return {"n": self.anteriores}
        return {**super().fetchone(), "prescription_json": {"rpe_target": "8-9-10"}, "plan_rpe_target": "9"}


@pytest.mark.parametrize(("anteriores", "direcao"), [(0, pe.HOLD), (1, pe.INCREASE_LOAD)])
def test_salvamento_le_a_rampa_da_ficha_congelada(monkeypatch, anteriores, direcao):
    cursor = _CursorComFicha(anteriores)
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(workout_sessions, "ensure_valid_codes", lambda codes: None)
    monkeypatch.setattr(workout_sessions, "_get_live_session", lambda cur, sid, uid: {"id": sid})
    monkeypatch.setattr(workout_sessions, "_refresh_exercise_aggregates", lambda cur, eid: None)
    monkeypatch.setattr(workout_sessions, "_refresh_activity_avg_rpe", lambda cur, sid: None)
    monkeypatch.setattr(workout_sessions, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(workout_sessions, "load_pair_step_context", lambda conn, m, g: PairEngineContext(step_kg=PASSO))
    monkeypatch.setattr(
        workout_sessions.effective_intensity,
        "resolve_for_logged_exercise",
        lambda conn, eid: ei.EffectiveIntensity(rpe_target_effective=10, ceiling=10, failure_allowed=True),
    )
    payload = workout_sessions.SessionSetIn(set_number=anteriores + 2, reps=12, weight_kg=Decimal(20), rpe=Decimal(8))
    resposta = workout_sessions.add_session_set(1, 5, payload, identity=SimpleNamespace(user_id=1))
    assert resposta["next_set_load"]["direction"] == direcao
    assert any("COUNT(*)" in sql for sql in cursor.executados)
