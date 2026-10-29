"""set-eval-v5 (2026-10-12): regressao do eval pago da v4 no caso de dor
muscular 9/10 (Rosca inversa, 3 reps numa faixa 12-15, RPE 10 -> a v4
respondeu HOLD 15 kg com "Alvo atingido").

Prova, sem chamar a OpenAI:
1. o status por serie e de ESFORCO (rotulo do dono) e as reps contra a faixa
   vem do motor (`progression_engine.set_reps_position`), na resposta do
   salvamento e no prompt;
2. o limiar de dor muscular alta e o do motor de recuperacao
   (`muscle_pain_blocks_direct_work` / `MUSCLE_PAIN_BLOCK_ABOVE`), o mesmo do
   `classify_readiness`, interpolado no prompt;
3. o prompt manda explicar com palavras proprias (sem copiar a linha).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import exercise_set_evaluation_ai as mod
from app.domain import effective_intensity as ei
from app.domain import progression_engine as pe
from app.domain.intensity_status_labels import INTENSITY_STATUS_LABELS, REPS_POSITION_LABELS, reps_position_label
from app.domain.training_safety import IntensityComposition
from app.engine_decision_context import EngineDecision, PairEngineContext, evaluation_reps_range
from app.intelligence.muscle_recovery_service import (
    MUSCLE_PAIN_BLOCK_ABOVE,
    MuscleRecoveryService,
    muscle_pain_blocks_direct_work,
)
from tests.support.privado import exigir

# ---------------------------------------------------------------------------
# 1. Reps contra a faixa (motor) e rotulos de esforco (dono)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reps", "faixa", "esperado"),
    [
        (3, (12, 15), pe.REPS_BELOW_FLOOR),
        (11, (12, 15), pe.REPS_BELOW_FLOOR),
        (12, (12, 15), pe.REPS_IN_RANGE),
        (14, (12, 15), pe.REPS_IN_RANGE),
        (15, (12, 15), pe.REPS_AT_TOP),
        (16, (12, 15), pe.REPS_ABOVE_TOP),
        (12, (12, 12), pe.REPS_AT_TOP),
        (11, (12, 12), pe.REPS_BELOW_FLOOR),
        (None, (12, 15), None),
        (10, (None, None), None),
        (10, (15, 12), None),
        (True, (1, 3), None),
    ],
)
def test_posicao_das_reps_contra_a_faixa(reps, faixa, esperado):
    assert pe.set_reps_position(reps, *faixa) == esperado


def test_rotulos_dizem_que_o_status_e_de_esforco():
    assert INTENSITY_STATUS_LABELS[pe.INTENSITY_REACHED] == "Esforço atingido"
    assert INTENSITY_STATUS_LABELS[pe.INTENSITY_BELOW_TARGET] == "Esforço abaixo do alvo"
    assert not any("Alvo atingido" in label for label in INTENSITY_STATUS_LABELS.values())
    assert set(REPS_POSITION_LABELS) == {pe.REPS_BELOW_FLOOR, pe.REPS_IN_RANGE, pe.REPS_AT_TOP, pe.REPS_ABOVE_TOP}
    assert reps_position_label(None) is None


INTENSIDADE = ei.EffectiveIntensity(rpe_target_effective=10, ceiling=10, failure_allowed=True)


def test_leitura_traz_as_reps_contra_a_faixa_so_em_serie_de_trabalho():
    leitura = ei.set_intensity_reading("working", Decimal(10), INTENSIDADE, reps=3, reps_range=(12, 15))
    assert leitura.status == pe.INTENSITY_REACHED
    assert (leitura.reps, leitura.reps_min, leitura.reps_max) == (3, 12, 15)
    assert leitura.reps_position == pe.REPS_BELOW_FLOOR
    aquecimento = ei.set_intensity_reading("warmup", Decimal(5), INTENSIDADE, reps=15, reps_range=(12, 15))
    assert aquecimento.reps_position is None and aquecimento.reps is None
    sem_faixa = ei.set_intensity_reading("working", Decimal(10), INTENSIDADE, reps=12)
    assert sem_faixa.reps_position is None


def test_faixa_da_ficha_prefere_a_prescricao_congelada():
    assert ei.plan_reps_range('{"reps_target": "10-12"}', "6-8") == (10, 12)
    assert ei.plan_reps_range(None, "6-8") == (6, 8)
    assert ei.plan_reps_range(None, None) == (None, None)


def test_faixa_da_avaliacao_prefere_a_decisao_do_motor():
    decisao = EngineDecision(decision=pe.HOLD, load_kg=30.0, reps_min=10, reps_max=12)
    com_decisao = PairEngineContext(step_kg=Decimal("2.5"), decision=decisao)
    assert evaluation_reps_range(com_decisao, "12-15") == (10, 12)
    assert evaluation_reps_range(PairEngineContext(step_kg=Decimal("2.5")), "12-15") == (12, 15)
    assert evaluation_reps_range(None, None) == (None, None)


# ---------------------------------------------------------------------------
# 2. Limiar de dor muscular alta: um predicado so, no dono
# ---------------------------------------------------------------------------


def test_limiar_de_dor_e_o_mesmo_do_classify_readiness():
    limiar = MUSCLE_PAIN_BLOCK_ABOVE
    assert not muscle_pain_blocks_direct_work(limiar)
    assert muscle_pain_blocks_direct_work(limiar + 1)
    assert not muscle_pain_blocks_direct_work(None)
    assert not muscle_pain_blocks_direct_work("x")
    for dor in range(0, 11):
        bloqueia = MuscleRecoveryService.classify_readiness(100.0, None, False, dor, False) == "NO_DIRECT_WORK"
        assert bloqueia == muscle_pain_blocks_direct_work(dor)


# ---------------------------------------------------------------------------
# 3. Prompt v5
# ---------------------------------------------------------------------------

PAYLOAD_DOR = {
    "exercise_name": "Rosca inversa com barra reta",
    "reps_target": "12-15",
    "load_target": "12kg",
    "rpe_target": "8-9-10",
    "reps": 3,
    "weight_kg": 15,
    "rpe": 10,
    "set_number": 3,
    "sets_count": 3,
    "muscle_pain": MUSCLE_PAIN_BLOCK_ABOVE + 3,
    "joint_pain": False,
    "notes": "Dor no músculo aumentou bastante",
}
COMPOSICAO = IntensityComposition(ceiling=10, failure_allowed=True, target=10)
MOTOR = PairEngineContext(step_kg=Decimal("2.5"))


def _prompt(payload):
    leitura = ei.set_intensity_reading(
        "working",
        payload["rpe"],
        INTENSIDADE,
        reps=payload["reps"],
        reps_range=evaluation_reps_range(MOTOR, payload["reps_target"]),
    )
    return mod.build_prompt(payload, "RAG", engine=MOTOR, composition=COMPOSICAO, set_reading=leitura)


def test_versao_do_prompt():
    assert mod.PROMPT_VERSION == "set-eval-v8"


def test_prompt_com_dor_alta_traz_a_regra_com_o_limiar_do_motor():
    """A regra de dor alta entra renderizada com o limiar do dono e a categoria
    de reduzir (a redação é conferida no privado,
    `api/privado/tests/unit/test_set_evaluation_prompt.py`)."""
    prompt = _prompt(PAYLOAD_DOR)
    regra = mod._format_muscle_pain_rule(PAYLOAD_DOR, MOTOR)
    exigir(regra is not None and regra in prompt, "regra de dor alta no prompt")
    exigir(f"{MUSCLE_PAIN_BLOCK_ABOVE}/10" in regra, "limiar do motor de recuperação interpolado")
    exigir(mod._category_text(pe.DECREASE_LOAD) in regra, "categoria de reduzir na regra")


def test_prompt_sem_dor_alta_nao_traz_a_regra():
    regra = mod._format_muscle_pain_rule(PAYLOAD_DOR, MOTOR)
    for dor in (None, MUSCLE_PAIN_BLOCK_ABOVE):
        assert mod._format_muscle_pain_rule({**PAYLOAD_DOR, "muscle_pain": dor}, MOTOR) is None
        prompt = _prompt({**PAYLOAD_DOR, "muscle_pain": dor})
        exigir(regra.split(":")[0] not in prompt, "sem a regra de dor alta")


def test_prompt_separa_esforco_de_repeticoes():
    prompt = _prompt(PAYLOAD_DOR)
    exigir(INTENSITY_STATUS_LABELS[pe.INTENSITY_REACHED] in prompt, "rótulo de esforço do dono")
    exigir(
        "- Repetições: Reps abaixo da faixa (3 reps executadas; faixa de 12 a 15 reps)." in prompt,
        "linha das reps contra a faixa",
    )
    exigir("Alvo atingido" not in prompt, "sem o rótulo antigo")


def test_prompt_sem_faixa_omite_as_repeticoes():
    prompt = _prompt({**PAYLOAD_DOR, "reps_target": None})
    exigir("- Repetições:" not in prompt, "sem a linha das reps")


def test_prompt_traz_a_regra_de_direcao_da_categoria_do_dono():
    from app.domain.set_evaluation_categories import category_rule_text

    prompt = _prompt(PAYLOAD_DOR)
    exigir(category_rule_text("15 kg") in prompt, "regra de direção da categoria do dono")
