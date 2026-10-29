"""`VolumeService.get_daily_muscle_activation` - campos aditivos `direct_sets`
e `muscle_group_id` (tabela "Ativação muscular (últimos 7 dias)" de
/dashboard/musculos, comparada com as faixas dos macro-grupos de volume).

`direct_sets` usa a unidade do motor da metodologia: séries dos itens com
contribuição PRIMARY no grupo, SEM `stimulus_weight`. `sets_count` continua
sendo a série equivalente ponderada (contrato de sempre, consumido pela janela
de ativação da Fase 7)."""

from __future__ import annotations

import pytest

from app.intelligence import muscle_contribution_resolver as resolver
from app.intelligence.volume_service import VolumeService

ROWS = [
    # Supino: 4 séries no dia 1 (PRIMARY peitoral, SECONDARY tríceps a 0.5)
    {"exercise_id": 1, "mapping_id": 10, "sets_count": 4, "local_date": "2026-09-28", "had_rpe_9_10": 1, "technique_rows": 0},
    # Tríceps testa: 3 séries no dia 1 (PRIMARY tríceps)
    {"exercise_id": 2, "mapping_id": 20, "sets_count": 3, "local_date": "2026-09-28", "had_rpe_9_10": 0, "technique_rows": 0},
    # Supino de novo no dia 2
    {"exercise_id": 3, "mapping_id": 10, "sets_count": 2, "local_date": "2026-09-29", "had_rpe_9_10": 0, "technique_rows": 0},
]

CONTRIBUTIONS = {
    10: [
        {"muscle_group_id": 1, "muscle_group_name": "Peitoral", "role": "PRIMARY", "stimulus_weight": 1.0},
        {"muscle_group_id": 2, "muscle_group_name": "Tríceps", "role": "SECONDARY", "stimulus_weight": 0.5},
    ],
    20: [
        {"muscle_group_id": 2, "muscle_group_name": "Tríceps", "role": "primary", "stimulus_weight": 1.0},
    ],
}


@pytest.fixture
def items(monkeypatch):
    service = VolumeService()
    service._technique_catalog = {}
    service.fetch_all = lambda sql, params=(): [dict(row) for row in ROWS]
    monkeypatch.setattr(
        resolver, "_resolve_muscle_contributions_for_mappings", lambda ids: {k: CONTRIBUTIONS[k] for k in ids}
    )
    return {(i["local_date"], i["muscle_group"]): i for i in service.get_daily_muscle_activation(days=7, user_id=1)}


def test_direct_sets_conta_so_primary_sem_peso(items):
    assert items[("2026-09-28", "Peitoral")]["direct_sets"] == 4
    # 3 diretas do tríceps testa; as 4 do supino entram só no ponderado
    assert items[("2026-09-28", "Tríceps")]["direct_sets"] == 3
    assert items[("2026-09-28", "Tríceps")]["sets_count"] == pytest.approx(3 + 4 * 0.5)


def test_grupo_so_com_trabalho_indireto_tem_direct_zero(items):
    triceps_dia2 = items[("2026-09-29", "Tríceps")]
    assert triceps_dia2["direct_sets"] == 0
    assert triceps_dia2["sets_count"] == pytest.approx(1.0)


def test_muscle_group_id_e_contrato_antigo_preservado(items):
    peitoral = items[("2026-09-28", "Peitoral")]
    assert peitoral["muscle_group_id"] == 1
    assert peitoral["sets_count"] == 4
    assert peitoral["had_rpe_9_10"] is True
    assert set(peitoral) == {"local_date", "muscle_group", "muscle_group_id", "sets_count", "direct_sets", "had_rpe_9_10"}


def test_modo_ponderado_soma_a_fracao_do_secundario_pelo_motor(monkeypatch):
    """Lote 4 do modo de contagem: em `primary_secondary` o peso de cada
    contribuicao em `direct_sets` e o do motor (`counted_contributions`), e a
    parte do SECONDARY vem em `direct_secondary_sets` (decide a frequencia).
    `sets_count` (regua equivalente P+S+E) nao muda."""
    from app.intelligence.methodology_volume import VolumeCountingMode

    service = VolumeService()
    service._technique_catalog = {}
    service.fetch_all = lambda sql, params=(): [dict(row) for row in ROWS]
    monkeypatch.setattr(
        resolver, "_resolve_muscle_contributions_for_mappings", lambda ids: {k: CONTRIBUTIONS[k] for k in ids}
    )
    rows = service.get_daily_muscle_activation(days=7, user_id=1, counting_mode=VolumeCountingMode.PRIMARY_SECONDARY)
    items = {(i["local_date"], i["muscle_group"]): i for i in rows}
    triceps = items[("2026-09-28", "Tríceps")]
    assert triceps["direct_sets"] == pytest.approx(3 + 4 * 0.5)
    assert triceps["direct_secondary_sets"] == pytest.approx(2.0)
    assert triceps["sets_count"] == pytest.approx(3 + 4 * 0.5)
    assert items[("2026-09-29", "Tríceps")]["direct_sets"] == pytest.approx(1.0)
    assert items[("2026-09-28", "Peitoral")]["direct_secondary_sets"] == 0
