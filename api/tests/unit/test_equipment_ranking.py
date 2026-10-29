"""Ranking unico de equipamento por usuario (PR-08; PLANO_20260912.md secoes
6.1 passos 4-5 e 13): funcoes puras de `app/domain/equipment_ranking.py`."""

from __future__ import annotations

import itertools
import random

import pytest

from app.domain import equipment_ranking as ranking
from app.domain import training_safety as ts

REQUIREMENTS = {"rest_pause": "may_reach_failure", "drop_set": "requires_failure", "cluster": "failure_not_required"}


def _item(id_, name, stars=None, last_used=None, equipment_id=None):
    return {
        "id": id_,
        "equipment_id": equipment_id or id_ * 10,
        "equipment_name": name,
        "user_preference_stars": stars,
        "last_used_by_user_at": last_used,
    }


def _ids(items):
    return [item["id"] for item in items]


def _safety(max_safe_rpe=None, techniques=None, override_techniques=None):
    return ts.build_training_safety(
        equipment_max_safe_rpe=max_safe_rpe,
        equipment_techniques=techniques or {},
        override_techniques=override_techniques or {},
        technique_failure_requirements=REQUIREMENTS,
    )


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [(None, 3), (1, 1), (5, 5), (0, 3), (6, 3), ("4", 4), ("x", 3), (True, 3)])
def test_estrelas_ausentes_ou_sujas_valem_3(value, expected):
    assert ranking.effective_stars(value) == expected


def test_estrelas_vencem_a_recencia():
    items = [
        _item(1, "A", stars=None, last_used=None),  # nunca usado, mas 3 estrelas
        _item(2, "B", stars=5, last_used="2026-09-16T10:00:00"),  # usado ontem, 5 estrelas
    ]
    assert _ids(ranking.rank_items(items)) == [2, 1]


def test_empate_de_estrelas_decide_pela_recencia_do_usuario_nunca_usado_primeiro():
    items = [
        _item(1, "A", stars=4, last_used="2026-09-10T08:00:00"),
        _item(2, "B", stars=4, last_used="2026-09-01T08:00:00"),
        _item(3, "C", stars=4, last_used=None),
    ]
    assert _ids(ranking.rank_items(items)) == [3, 2, 1]


def test_ausente_fica_entre_4_e_2_estrelas():
    items = [_item(1, "A", stars=2), _item(2, "B", stars=None), _item(3, "C", stars=4), _item(4, "D", stars=3)]
    # 4 > (ausente = 3, empatado com 3 -> nome) > 2
    assert _ids(ranking.rank_items(items)) == [3, 2, 4, 1]


def test_empate_total_decide_por_nome_e_depois_id():
    items = [_item(9, "banco"), _item(3, "Banco"), _item(5, "Aparelho"), _item(1, "banco")]
    ranked = ranking.rank_items(items)
    assert ranked[0]["id"] == 5
    assert _ids(ranked) == _ids(ranking.rank_items(reversed(items)))  # independe da ordem de entrada


def test_determinismo_qualquer_permutacao_da_a_mesma_ordem():
    items = [
        _item(1, "Supino", stars=5, last_used="2026-09-02T10:00:00"),
        _item(2, "Supino", stars=5, last_used="2026-09-02T10:00:00"),
        _item(3, "Crossover", stars=None),
        _item(4, "Crossover", stars=3, last_used="2026-08-01T10:00:00"),
        _item(5, "Halteres", stars=1),
    ]
    expected = _ids(ranking.rank_items(items))
    assert expected == [1, 2, 3, 4, 5]
    for permutation in itertools.islice(itertools.permutations(items), 60):
        assert _ids(ranking.rank_items(permutation)) == expected
    shuffled = list(items)
    random.Random(8).shuffle(shuffled)
    assert _ids(ranking.rank_items(shuffled)) == expected


# ---------------------------------------------------------------------------
# required_technique
# ---------------------------------------------------------------------------


def test_sem_required_technique_nada_e_excluido_mesmo_sem_seguranca_cadastrada():
    items = [_item(1, "A"), _item(2, "B")]
    safeties = {1: _safety(), 2: _safety()}  # D3: nada cadastrado
    kept, excluded = ranking.split_by_required_technique(items, safeties, None)
    assert _ids(kept) == [1, 2] and excluded == []
    kept, excluded = ranking.split_by_required_technique(items, safeties, "")
    assert _ids(kept) == [1, 2] and excluded == []


def test_motivos_de_exclusao():
    items = [_item(1, "Sem marcacao"), _item(2, "Falha proibida"), _item(3, "Compativel"), _item(4, "Negada no par")]
    safeties = {
        1: _safety(max_safe_rpe=10),
        2: _safety(max_safe_rpe=9, techniques={"drop_set": 1}),
        3: _safety(max_safe_rpe=10, techniques={"drop_set": 1}),
        4: _safety(max_safe_rpe=10, techniques={"drop_set": 1}, override_techniques={"drop_set": 0}),
    }
    for item in items:
        item.update(ranking.safety_fields(safeties[item["id"]]))
    kept, excluded = ranking.split_by_required_technique(items, safeties, "drop_set")
    assert _ids(kept) == [3]
    assert [(e["gym_equipment_id"], e["reason"], e["max_safe_rpe"]) for e in excluded] == [
        (1, "TECHNIQUE_INCOMPATIBLE", 10),
        (2, "FAILURE_REQUIRED_NOT_ALLOWED", 9),
        (4, "TECHNIQUE_INCOMPATIBLE", 10),
    ]


def test_preferido_incompativel_perde_e_unico_compativel_menos_preferido_vence():
    items = ranking.rank_items([_item(1, "Preferido", stars=5), _item(2, "Menos preferido", stars=1)])
    safeties = {1: _safety(max_safe_rpe=10), 2: _safety(max_safe_rpe=9, techniques={"rest_pause": 1})}
    assert _ids(items) == [1, 2]
    kept, excluded = ranking.split_by_required_technique(items, safeties, "rest_pause")
    assert _ids(kept) == [2]
    assert [(e["id"], e["reason"]) for e in excluded] == [(1, "TECHNIQUE_INCOMPATIBLE")]


def test_tecnica_fora_do_catalogo_exclui_tudo_como_incompativel():
    items = [_item(1, "A")]
    kept, excluded = ranking.split_by_required_technique(items, {1: _safety(10, {"drop_set": 1})}, "inexistente")
    assert kept == [] and excluded[0]["reason"] == "TECHNIQUE_INCOMPATIBLE"


def test_campos_de_seguranca_do_item():
    fields = ranking.safety_fields(_safety(max_safe_rpe=10, techniques={"rest_pause": 1}, override_techniques={"drop_set": 1}))
    assert fields == {
        "max_safe_rpe": 10,
        "failure_allowed": True,
        "allowed_advanced_techniques": [
            {"code": "drop_set", "source": "exercise_equipment_override"},
            {"code": "rest_pause", "source": "equipment_default"},
        ],
        "safety_source": "equipment_default",
    }
    assert ranking.safety_fields(_safety()) == {
        "max_safe_rpe": 9,
        "failure_allowed": False,
        "allowed_advanced_techniques": [],
        "safety_source": None,
    }
