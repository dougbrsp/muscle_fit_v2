"""Modo Treino - recorde e e1RM "nesse equipamento" em
`get_last_session_sets` (GET /exercises/last-session-sets).

Recorde (M76 Lote 3b, decisão 3): a regra do dashboard
(`session_records.record_events`) na chave da sessão anterior - exercício
canônico + equipamento global + convenção -, sobre o histórico inteiro, sem a
sessão atual. A 1ª exposição não é recorde (M76 Lote 3d: vira a "Melhor
carga", `kind = "best_load"`); empate de carga com mais reps não
é recorde; reps da série recorde só informam. e1RM:
`app.domain.equipment_record.summarize_equipment_history`.

Roda so no CI (MySQL).
"""

from __future__ import annotations

import pytest

from app import services
from tests.integration.test_pr14_persistencia_intensidade import (  # noqa: F401 (fixtures)
    _ficha,
    _grava,
    _sessao,
    academia,
    client,
)


def test_recorde_e_a_carga_que_superou_a_marca_e_e1rm_do_historico_do_equipamento(client, catalog, academia):  # noqa: F811 - fixture do pytest importada (falso positivo)
    plan = _ficha(catalog, academia, rpe_target="9")
    antiga, ex_antiga = _sessao(client, catalog, academia, plan)
    # aquecimento mais pesado NAO pode virar recorde
    assert _grava(client, antiga, ex_antiga, set_type="warmup", reps=5, weight_kg=80, rpe=None).status_code == 200
    assert _grava(client, antiga, ex_antiga, set_type="working", reps=13, weight_kg=30, rpe=9).status_code == 200
    assert _grava(client, antiga, ex_antiga, set_type="working", reps=10, weight_kg=30, rpe=10).status_code == 200

    media, ex_media = _sessao(client, catalog, academia, plan)
    assert _grava(client, media, ex_media, set_type="working", reps=8, weight_kg=32.5, rpe=None).status_code == 200
    assert _grava(client, media, ex_media, set_type="working", reps=6, weight_kg=32.5, rpe=None).status_code == 200

    recente, ex_recente = _sessao(client, catalog, academia, plan)
    # mesma carga do recorde com MAIS reps: empate na carga nao e recorde
    assert _grava(client, recente, ex_recente, set_type="working", reps=12, weight_kg=32.5, rpe=None).status_code == 200

    atual, ex_atual = _sessao(client, catalog, academia, plan)
    # a sessao atual fica FORA do historico
    assert _grava(client, atual, ex_atual, set_type="working", reps=12, weight_kg=60, rpe=8).status_code == 200

    item = services.get_last_session_sets(
        catalog.exercises["Supino reto"],
        atual,
        user_id=catalog.user_id,
        equipment_id=academia["Banco reto"],
    )

    assert item is not None
    assert item["activity_id"] == recente
    assert item["record_scope"] == "equipment"
    # 32,5 superou 30 (a 1a exposicao); reps = as da serie recorde (sessao media)
    assert item["record"] == {"reps": 8, "weight_kg": 32.5, "kind": "record"}
    # A serie de 13 reps passa do teto de confiabilidade do Epley (12 reps,
    # mesma regra do dashboard): o e1RM vem da de 10 reps a RPE 10 ->
    # 30 x (1 + 10 / 30) = 40,0. As series sem RPE nao geram e1RM.
    assert item["e1rm_kg"] == pytest.approx(40.0)
    assert item["load_convention"] == "total"


def test_primeira_exposicao_nao_e_recorde_mostra_melhor_carga(client, catalog, academia):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """M76 Lote 3d: sem recorde vigente, a marca a bater é a "Melhor carga"
    da chave (`kind = "best_load"`), com as reps daquela série."""
    plan = _ficha(catalog, academia, rpe_target="9")
    antiga, ex_antiga = _sessao(client, catalog, academia, plan)
    assert _grava(client, antiga, ex_antiga, set_type="working", reps=10, weight_kg=40, rpe=None).status_code == 200

    item = services.get_last_session_sets(catalog.exercises["Supino reto"], 0, user_id=catalog.user_id)

    # sem equipamento pedido, vale a chave da sessao anterior (Banco reto)
    assert item["record_scope"] == "equipment"
    assert item["record"] == {"reps": 10, "weight_kg": 40.0, "kind": "best_load"}
    assert item["e1rm_kg"] is None
