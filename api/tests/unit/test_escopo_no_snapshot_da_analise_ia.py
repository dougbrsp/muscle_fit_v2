"""#177: o item de exercicio do snapshot da analise por IA carrega o escopo do
status (`status_scope`, dono `progression_status_labels`), e o prompt de IA
continua identico (o campo e so da tela)."""

from __future__ import annotations

import copy

from app import report_ai
from app.domain.progression_status_labels import SCOPE_EXERCISE_LAST_VS_PREVIOUS, progression_scope
from app.intelligence.progression_service import exercise_progression_context
from tests.unit.test_progressao_tela_mesma_regra import agachamento_real
from tests.unit.test_progressao_unica_m74 import _lote


def _item() -> dict:
    (item,) = exercise_progression_context(_lote(agachamento_real()))
    return item


def _sem_campo(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "status_scope"}


def test_item_do_snapshot_traz_o_escopo_do_dono():
    assert _item()["status_scope"] == progression_scope(SCOPE_EXERCISE_LAST_VS_PREVIOUS)


def test_prompt_identico_com_e_sem_o_campo():
    item = _item()
    com = {"days": 90, "exercise_progression": [item]}
    sem = {"days": 90, "exercise_progression": [_sem_campo(item)]}

    assert report_ai.build_prompt(com, 90) == report_ai.build_prompt(sem, 90)
    assert "status_scope" not in report_ai.build_prompt(com, 90)
    assert report_ai.build_cache_prefix(com, 90) == report_ai.build_cache_prefix(sem, 90)


def test_payload_da_ia_nao_muta_a_entrada():
    training = {"days": 90, "exercise_progression": [_item()]}
    antes = copy.deepcopy(training)

    saida = report_ai._ai_training_payload(training)

    assert training == antes
    assert "status_scope" in training["exercise_progression"][0]
    assert "status_scope" not in saida["exercise_progression"][0]


def test_snapshot_antigo_sem_o_campo_segue_funcionando():
    antigo = {"days": 90, "exercise_progression": [_sem_campo(_item())]}
    (saida,) = report_ai._ai_training_payload(antigo)["exercise_progression"]
    assert saida["status"] == antigo["exercise_progression"][0]["status"]
    assert "status_scope" not in saida
    assert report_ai._ai_training_payload({})["exercise_progression"] == []
