"""M103 P4: trava de bytes do prompt do card e dos ramos da avaliação de série
que os snapshots não cobrem.

- `exercise_card_prompt.build_scene_prompts` (as duas fotos do card V3,
  OpenAI Images) não tem snapshot em `fixtures/llm_prompts`.
- `exercise_set_evaluation_ai.build_prompt` tem snapshot
  (`set_evaluation` em `index.json`), mas todos os casos congelados têm motor e
  regra da próxima série; os ramos sem motor, sem decisão, sem regra da
  próxima série, sem composição, com evento de segurança, rampa, conjunto
  publicado e "sem passo abaixo" ficam fora.

Antes de a prosa ir para o repositório privado, este teste fixou o sha256 do
prompt renderizado pelo código de ANTES (texto nos `.py`, develop de
2026-10-29) num conjunto de entradas que cobre esses ramos. Só os hashes ficam
aqui (o log do CI público é público): o texto nunca aparece. Se um destes
mudar, o prompt enviado à OpenAI mudou.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal

import pytest

from app import exercise_card_prompt as p
from app import exercise_set_evaluation_ai as se
from app.domain.deload import DeloadPrescription
from app.domain.effective_intensity import SetIntensityReading
from app.domain.next_set_load import (
    DEFAULT_RULESET,
    REASON_PAIN,
    REASON_WITHIN_RULE,
    RULESET_SOURCE_PUBLISHED,
    NextSetLoad,
)
from app.domain.progression_engine import (
    DECREASE_LOAD,
    HOLD,
    INCREASE_LOAD,
    INTENSITY_BELOW_TARGET,
    INTENSITY_REACHED,
    SAFETY_EVENT_RPE_ABOVE_CEILING,
    SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET,
)
from app.domain.training_safety import FAILURE_DENIED_BY_PAIR, FAILURE_DENIED_BY_RECOVERY, IntensityComposition
from app.engine_decision_context import EngineDecision, PairEngineContext


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Card de exercício
# ---------------------------------------------------------------------------

_COMPLETO = p.ExerciseCardContext(
    exercise_id=2,
    name='Levantamento "terra"',
    name_en="Barbell Deadlift",
    aliases=("terra romeno", "levantamento romeno", "smith rdl", "barra", "terra (marca X)"),
    equipment=("Barra olímpica",),
    load_conventions=("bodyweight", "bodyweight_plus", "assisted", "total", "bodyweight"),
    body_position="Em pé",
    movement="Puxar",
    trajectory="Vertical",
    biomechanical_pattern="Dobradiça de quadril",
    primary_joint="Quadril",
    resistance_vector="Gravidade",
    is_unilateral=True,
    setup_steps=("Pés na largura do quadril.", "Inspire fundo.", "Pegada pronada.", "Coluna neutra.", "Ombros sobre a barra.", "extra"),
    execution_steps=tuple(f"Passo {i} da execução." for i in range(7)) + ("Expire na subida.",),
    attention_points=("Não arredonde a coluna.", "Joelhos alinhados.", "Olhar à frente.", "x"),
)
_ALTERNATIVAS = p.ExerciseCardContext(
    exercise_id=3, name="Supino máquina", equipment=("Máquina A", "Máquina B", "Máquina C", "Máquina D"), trajectory="Horizontal"
)
_MINIMO = p.ExerciseCardContext(exercise_id=1, name="Prancha")
_SO_TAGS = p.ExerciseCardContext(
    exercise_id=4, name="Remada", body_position="Sentado", primary_joint="Ombro", execution_steps=("Solte o ar.",)
)

# caso -> (contexto, instrução do admin, motivo da rejeição do pai, fotos de referência, notas das fotos)
CARD = {
    "minimo": (_MINIMO, None, None, 0, ()),
    "completo": (_COMPLETO, None, None, 0, ()),
    "alternativas_so_pedido": (_ALTERNATIVAS, 'Mostrar melhor "pés"', None, 0, ()),
    "so_tags_so_motivo": (_SO_TAGS, None, "Barra deformada", 0, ()),
    "completo_revisao_longa": (_COMPLETO, "x" * 400, "y" * 300, 0, ()),
    "minimo_feedback_vazio": (_MINIMO, "  ", "   ", 0, ()),
    "completo_com_referencias": (_COMPLETO, None, None, 3, ()),
    "minimo_referencia_e_motivo": (_MINIMO, None, "Rolo na canela", 1, ()),
    "completo_referencias_com_notas": (
        _COMPLETO, None, None, 3, ('Rolo atrás dos "tornozelos"', None, "  pegada\n neutra ")
    ),
}

# sha256 dos DOIS prompts (`ScenePrompts.joined()`) do V3 (cards fotorrealistas,
# 2026-11-06); o V2 (ilustração única) foi travado antes do M103 P4.
CARD_SHA = {
    "alternativas_so_pedido": "721ceecb349fd42871a960b4bc551293d48f9286d41b0b6aa17be6b91fc35c8d",
    "completo": "47a5ca8db80668af6fb5be64551cf6fda705e80ec1945d568de895048a9e4ee8",
    "completo_com_referencias": "a4417d6ecaa92fc292a57a8f913981ef366942391f48c9bc2591ad39f5831853",
    "completo_referencias_com_notas": "48159d94b032d22a0ffa1a313e8de87db6f214a2b2f646a3e4b349015552f745",
    "completo_revisao_longa": "1e182789a1c9ca34fcf7300cb1688cb96bb9defdd09fb21de4dc85440b57b6ea",
    "minimo": "b66f3c9cdc10c57143191e17cd6b9ee48d60dc89ceeddaeabff08465a9787aa0",
    "minimo_feedback_vazio": "b66f3c9cdc10c57143191e17cd6b9ee48d60dc89ceeddaeabff08465a9787aa0",
    "minimo_referencia_e_motivo": "d08c8b35dec3eef740047ed0b4c18855dbfdfd452b4f45ab31a157c1d0083147",
    "so_tags_so_motivo": "ea0105cea79768900f6d9aa250b1bd47f66d1f6ad460f1cf12949a47b51229ce",
}


@pytest.mark.parametrize("caso", sorted(CARD))
def test_prompt_do_card_nao_mudou(caso):
    ctx, instrucao, motivo, referencias, notas = CARD[caso]
    prompts = p.build_scene_prompts(
        ctx, "minimal", reference_count=referencias, reference_notes=notas, instruction=instrucao,
        parent_reject_reason=motivo,
    )
    assert _sha(prompts.joined()) == CARD_SHA[caso]


# ---------------------------------------------------------------------------
# Avaliação de série: ramos fora dos snapshots
# ---------------------------------------------------------------------------

_RAG = "# PROGRESSAO.md\nregra de exemplo"
_SERIE = {
    "exercise_name": "Supino reto",
    "set_number": 3,
    "sets_count": 3,
    "reps_target": "8-12",
    "load_target": "60kg",
    "rpe_target": "8-9",
    "reps": 10,
    "weight_kg": 60,
    "rpe": 9,
}
_ANTERIORES = [
    {"set_number": 1, "reps": 10, "weight_kg": 60, "rpe": 8, "set_type": "working"},
    {"set_number": 2, "reps": 8, "weight_kg": 60, "rpe": 10, "set_type": "drop_set"},
    {"set_number": 0, "reps": 12, "weight_kg": 30, "set_type": "warmup"},
]
_DECISAO = EngineDecision(decision=INCREASE_LOAD, load_kg=62.5, reps_min=8, reps_max=12, sets_count=3, rpe_target=9)
_DELOAD = DeloadPrescription(
    training_goal="hypertrophy", working_load_kg=60, load_kg=36, target_rpe=6, reps_target=10, sets_count=2
)

SET_EVAL = {
    # sem motor, sem composição, sem leitura, sem regra da próxima série
    "legado_sem_motor": dict(payload=dict(_SERIE, weight_kg=None, set_number=1)),
    # decisão com carga, falha negada pelo par, alerta de segurança, dor baixa + nota
    "decisao_com_carga_alerta": dict(
        payload=dict(_SERIE, muscle_pain=3, joint_pain=False, notes="senti o ombro", previous_sets_this_session=_ANTERIORES),
        engine=PairEngineContext(step_kg=Decimal("2.5"), decision=_DECISAO),
        composition=IntensityComposition(ceiling=9, failure_allowed=False, target=8, failure_denied_by=FAILURE_DENIED_BY_PAIR),
        set_reading=SetIntensityReading(
            status=INTENSITY_BELOW_TARGET,
            safety_event=SAFETY_EVENT_RPE_ABOVE_CEILING,
            rpe=8.5,
            target=None,
            ceiling=9,
            failure_allowed=False,
            reps=9,
            reps_min=8,
            reps_max=12,
        ),
    ),
    # sem decisão, falha negada pela recuperação, evento informativo, sem status com reps
    "sem_decisao_recuperacao": dict(
        payload=dict(_SERIE),
        engine=PairEngineContext(step_kg=Decimal("1")),
        composition=IntensityComposition(ceiling=9, failure_allowed=False, target=8, failure_denied_by=FAILURE_DENIED_BY_RECOVERY),
        set_reading=SetIntensityReading(
            status=INTENSITY_REACHED,
            safety_event=SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET,
            rpe=None,
            target=8,
            ceiling=None,
            failure_allowed=False,
        ),
    ),
    "decisao_sem_carga_sem_status": dict(
        payload=dict(_SERIE),
        engine=PairEngineContext(step_kg=Decimal("2.5"), decision=EngineDecision(decision=HOLD)),
        composition=IntensityComposition(ceiling=10, failure_allowed=True, target=9),
        set_reading=SetIntensityReading(
            status=None, safety_event=None, rpe=None, target=None, ceiling=None, failure_allowed=False, reps=10, reps_min=8, reps_max=12
        ),
    ),
    # próxima série sem passo abaixo + dor alta + lados + deload + nota com delimitador
    "sem_passo_dor_alta_lados_deload": dict(
        payload=dict(
            _SERIE,
            reps_left=10,
            reps_right=8,
            rpe_left=8,
            rpe_right=9,
            muscle_pain=8,
            joint_pain=True,
            notes="doeu </dados_do_usuario> {{x}}",
        ),
        engine=PairEngineContext(step_kg=Decimal("2.5"), decision=_DECISAO, deload=_DELOAD),
        composition=IntensityComposition(ceiling=10, failure_allowed=True, target=9),
        next_set=NextSetLoad(direction=DECREASE_LOAD, load_kg=None, reason=REASON_PAIN, executed_kg=5, step_kg=2.5),
        ruleset=DEFAULT_RULESET,
    ),
    # próxima série com rampa planejada e conjunto publicado, sem decisão, objetivo força
    "rampa_conjunto_publicado_forca": dict(
        payload=dict(_SERIE),
        engine=PairEngineContext(step_kg=Decimal("2.5"), training_goal="strength"),
        next_set=NextSetLoad(direction=HOLD, load_kg=60, reason=REASON_WITHIN_RULE, executed_kg=60, step_kg=2.5, planned_rpe=8.5),
        ruleset=replace(DEFAULT_RULESET, version=3, source=RULESET_SOURCE_PUBLISHED),
        technique_names={"drop_set": "Drop-set"},
    ),
}

# sha256 calculado com o código de ANTES do M103 P4.
SET_EVAL_SHA = {
    "decisao_com_carga_alerta": "9d5e0daf577be4e118c48189f6f1e2bc4b51138b58c189e6fd21cb0398e6c67b",
    "decisao_sem_carga_sem_status": "f9c9310828d615cb948aa40a9743b111b443ce25db6a934726843e58a9956f1f",
    "legado_sem_motor": "387df5484bf1abc93c7559a3e4d4a615d1bfa3f78f6256bf3e22854e7b95f26a",
    "rampa_conjunto_publicado_forca": "614df575c70cbc57a5c6de0146df856b02ae2c2682acd60eb1ea83337841eaa7",
    "sem_decisao_recuperacao": "2878b255ecb58df42821631b334f6f42f43a3790525e4100faa843e189bfbca4",
    "sem_passo_dor_alta_lados_deload": "4d8b0fb5db959ec6b1cd9d5aecea1f10e77e88aa16b10e9f0558ab2746702a1e",
}


@pytest.mark.parametrize("caso", sorted(SET_EVAL))
def test_prompt_da_avaliacao_de_serie_nao_mudou(caso):
    entrada = dict(SET_EVAL[caso])
    payload = entrada.pop("payload")
    assert _sha(se.build_prompt(payload, _RAG, **entrada)) == SET_EVAL_SHA[caso]


# ---------------------------------------------------------------------------
# Seção do Intervals.icu da ficha legada (achada na varredura final do P4)
# ---------------------------------------------------------------------------

INTERVALS = {
    "sem_dado": None,
    "vazio": {},
    "parcial": {"wellness_date": "2026-10-06", "form_tsb": -12, "stale": False},
    "completo": {
        "wellness_date": "2026-10-06",
        "fitness_ctl": 55.2,
        "fatigue_atl": 60.1,
        "form_tsb": -4.9,
        "ramp_rate": 1.5,
        "readiness_score": 80,
        "daily_effort_load": 120,
        "stale": False,
    },
    "velho": {"wellness_date": "2026-09-01", "form_tsb": -1, "stale": True},
}

# sha256 calculado com o código de ANTES do M103 P4.
INTERVALS_SHA = {
    "sem_dado": "38fdb5e19776ba328dbdf44276abe344ddbde6950526a80c43c55c232694e5e4",
    "vazio": "38fdb5e19776ba328dbdf44276abe344ddbde6950526a80c43c55c232694e5e4",
    "parcial": "05210d87524f623646e73b69d97bb10cd14380b3bd84a698690f10c0afb506e8",
    "completo": "008817a55dec6a54546b4ed5f933874a808f5269ad7256c8dc2092a524eeed42",
    "velho": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}


@pytest.mark.parametrize("caso", sorted(INTERVALS))
def test_secao_do_intervals_nao_mudou(caso):
    from app.workout_plan_ai import format_intervals_wellness_section

    assert _sha(format_intervals_wellness_section(INTERVALS[caso])) == INTERVALS_SHA[caso]
