"""M6 - ranking DETERMINISTICO de substituicao de exercicio por outro do
mesmo grupo (`generation/exercise_substitution.py`, funcoes puras).

Decisoes do usuario (2026-09-21) que estes testes fixam:

- "IA" = ranking deterministico, sem LLM: mesma entrada -> mesma escolha,
  qualquer que seja a ordem em que os candidatos chegam;
- D13a: candidato com `max_safe_rpe` < o do par original e rejeitado, com
  motivo auditavel;
- tecnica fixa do item tem de ser valida no par candidato;
- exercicio que ja esta no slot nunca e candidato;
- estrelas do par (PR-08) sao o primeiro criterio; seguranca desempata.
"""

from __future__ import annotations

import random

import pytest

from app.domain import training_safety as ts
from app.generation import exercise_substitution as subst


def _safety(exercise_id, equipment_id, max_safe_rpe=10, techniques=None):
    return ts.build_training_safety(
        exercise_id=exercise_id,
        equipment_id=equipment_id,
        equipment_max_safe_rpe=max_safe_rpe,
        equipment_techniques=techniques or {},
        technique_failure_requirements={"rest_pause": "failure_not_required", "drop_set": "requires_failure"},
    )


def _candidate(gym_equipment_id, exercise_id, equipment_id, *, name="Ex", equipment="Eq", stars=None, weight=1.0):
    return subst.SubstituteCandidate(
        gym_equipment_id=gym_equipment_id,
        exercise_id=exercise_id,
        exercise_name=name,
        equipment_id=equipment_id,
        equipment_name=equipment,
        muscle_group_id=7,
        stimulus_weight=weight,
        user_preference_stars=stars,
    )


@pytest.fixture
def cenario():
    """Tres candidatos do mesmo grupo: A (5 estrelas), B (sem nota = 3), C
    (4 estrelas, mas teto 8)."""
    candidates = [
        _candidate(11, 101, 1, name="Supino inclinado", equipment="Banco", stars=5),
        _candidate(12, 102, 2, name="Crossover", equipment="Polia", stars=None),
        _candidate(13, 103, 3, name="Peck deck", equipment="Maquina", stars=4),
    ]
    safety = {
        (101, 1): _safety(101, 1, 10, {"rest_pause": 1}),
        (102, 2): _safety(102, 2, 10, {"rest_pause": 1}),
        (103, 3): _safety(103, 3, 8, {"rest_pause": 1}),
    }
    return candidates, safety


def test_escolhe_por_estrelas_e_rejeita_teto_menor(cenario):
    candidates, safety = cenario
    eligible, rejected = subst.rank_candidates(candidates, safety, original_max_safe_rpe=9)
    assert [item.candidate.exercise_id for item in eligible] == [101, 102]
    assert rejected == [
        {"gym_equipment_id": 13, "exercise_id": 103, "equipment_id": 3, "reason": "MAX_SAFE_RPE_LOWER", "max_safe_rpe": 8}
    ]


def test_teto_igual_ao_original_e_aceito(cenario):
    """D13a e `>=`: o candidato com o MESMO teto do original passa."""
    candidates, safety = cenario
    eligible, _ = subst.rank_candidates(candidates, safety, original_max_safe_rpe=8)
    assert {item.candidate.exercise_id for item in eligible} == {101, 102, 103}


def test_tecnica_fixa_invalida_rejeita(cenario):
    candidates, safety = cenario
    # `drop_set` exige falha: teto 10 permite; ninguem a marcou compativel -> incompativel.
    eligible, rejected = subst.rank_candidates(candidates, safety, original_max_safe_rpe=10, technique="drop_set")
    assert eligible == []
    assert {item["reason"] for item in rejected} == {"TECHNIQUE_INCOMPATIBLE", "MAX_SAFE_RPE_LOWER"}


def test_exercicio_ja_no_slot_nunca_e_candidato(cenario):
    candidates, safety = cenario
    eligible, rejected = subst.rank_candidates(
        candidates, safety, original_max_safe_rpe=10, exclude_exercise_ids=[101]
    )
    assert [item.candidate.exercise_id for item in eligible] == [102]
    assert any(item["reason"] == "EXERCISE_ALREADY_IN_SLOT" and item["exercise_id"] == 101 for item in rejected)


def test_par_sem_seguranca_lida_e_rejeitado(cenario):
    candidates, safety = cenario
    del safety[(101, 1)]
    eligible, rejected = subst.rank_candidates(candidates, safety, original_max_safe_rpe=9)
    assert [item.candidate.exercise_id for item in eligible] == [102]
    assert any(item["exercise_id"] == 101 and item["max_safe_rpe"] is None for item in rejected)


def test_determinismo_independe_da_ordem_de_entrada(cenario):
    """Mesma entrada -> mesma escolha, em qualquer ordem dos candidatos e sem
    nenhum criterio que mude entre duas chamadas (recencia fica de fora)."""
    candidates, safety = cenario
    baseline = subst.choose_substitute(
        candidates, safety, from_exercise_id=999, from_equipment_id=9, original_max_safe_rpe=9
    )
    assert baseline is not None
    rng = random.Random(42)
    for _ in range(20):
        shuffled = list(candidates)
        rng.shuffle(shuffled)
        again = subst.choose_substitute(
            shuffled, safety, from_exercise_id=999, from_equipment_id=9, original_max_safe_rpe=9
        )
        assert again is not None
        assert again.audit() == baseline.audit()


def test_desempate_por_seguranca_e_nome():
    """Mesmas estrelas: menos clamps no RPE do modelo primeiro, depois maior
    teto, depois maior estimulo, depois nome - nunca o id da entrada."""
    candidates = [
        _candidate(21, 201, 1, name="B", stars=4),  # teto 9, RPE 10 do modelo clampado
        _candidate(22, 202, 2, name="A", stars=4),  # teto 10, sem clamp
        _candidate(23, 203, 3, name="C", stars=4),  # teto 10, sem clamp
    ]
    safety = {(201, 1): _safety(201, 1, 9), (202, 2): _safety(202, 2, 10), (203, 3): _safety(203, 3, 10)}
    eligible, _ = subst.rank_candidates(candidates, safety, original_max_safe_rpe=9, prescribed_rpe=10)
    assert [item.candidate.exercise_id for item in eligible] == [202, 203, 201]


def test_nenhum_elegivel_devolve_none(cenario):
    candidates, safety = cenario
    assert (
        subst.choose_substitute(
            candidates, safety, from_exercise_id=1, from_equipment_id=None, original_max_safe_rpe=10, technique="drop_set"
        )
        is None
    )


def test_auditoria_diz_de_onde_para_onde_e_por_que(cenario):
    candidates, safety = cenario
    chosen = subst.choose_substitute(
        candidates, safety, from_exercise_id=999, from_equipment_id=9, original_max_safe_rpe=9
    )
    audit = chosen.audit()
    assert audit["kind"] == "EXERCISE"
    assert audit["reason"] == "EXERCISE_UNAVAILABLE_IN_GYM"
    assert (audit["from_exercise_id"], audit["from_equipment_id"]) == (999, 9)
    assert (audit["to_exercise_id"], audit["to_equipment_id"], audit["to_gym_equipment_id"]) == (101, 1, 11)
    assert audit["original_max_safe_rpe"] == 9 and audit["chosen_max_safe_rpe"] == 10
    assert [item["exercise_id"] for item in audit["ranking"]] == [101, 102]
    assert audit["rejected"][0]["reason"] == "MAX_SAFE_RPE_LOWER"


def test_auditoria_de_troca_de_equipamento_tem_o_mesmo_formato():
    audit = subst.equipment_substitution_audit(
        reason=subst.REASON_USER_CHOICE, exercise_id=5, from_equipment_id=1, to_equipment_id=2, to_gym_equipment_id=77
    )
    assert audit["kind"] == "EQUIPMENT"
    assert audit["from_exercise_id"] == audit["to_exercise_id"] == 5
    assert set(audit) == set(
        subst.choose_substitute(
            [_candidate(1, 2, 3)], {(2, 3): _safety(2, 3)}, from_exercise_id=1, from_equipment_id=None, original_max_safe_rpe=0
        ).audit()
    )
