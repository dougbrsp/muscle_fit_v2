"""M76 Lote 3c: pesos por papel e rótulos de status com um dono só.

- Defaults de ESCRITA por papel: `muscle_contribution_resolver.DEFAULT_WRITE_WEIGHTS`
  (catálogo legado, fallback do `VolumeService` e admin FE via
  `motor-constants.generated.json`, conferido em `test_m76_lote1b_espelhos_fe.py`).
- Política única para exercício sem contribuição cadastrada: PRIMARY em "Outros".
- Rótulos de status de progressão: `domain/progression_status_labels.py`.
- Comparabilidade com a chave atual pronta para o FE: `comparability.mismatch_with_current`.

A tela Tendência ponderada pelo `stimulus_weight` está em
`test_muscle_groups_mapped_aggregate.py`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

from app import exercise_mapping_catalog
from app.domain import exercise_briefing
from app.domain.progression_status_labels import EXERCISE_PROGRESSION_LABELS
from app.intelligence import muscle_contribution_resolver as resolver
from app.intelligence.dashboard import comparability as cmp
from app.intelligence.dashboard import metrics_registry as reg
from app.intelligence.volume_service import OUTROS_GROUP, VolumeService

RAIZ = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Defaults de escrita por papel
# ---------------------------------------------------------------------------


def test_defaults_de_escrita_tem_os_tres_papeis_e_batem_com_o_seed():
    assert dict(resolver.DEFAULT_WRITE_WEIGHTS) == {"PRIMARY": 1.0, "SECONDARY": 0.5, "STABILIZER": 0.25}
    seed = (RAIZ / "api/db/migrations/001_schema.sql").read_text(encoding="utf-8")
    # Backfill original: PRIMARY 1,00 e SECONDARY 0,50 (sem participação cadastrada).
    assert re.search(r"'PRIMARY',\s*1\.00,\s*1\.00", seed)
    assert "COALESCE(zm.secondary_participation, 0.50)" in seed
    with pytest.raises(TypeError):
        resolver.DEFAULT_WRITE_WEIGHTS["SECONDARY"] = 0.3  # type: ignore[index]


def test_catalogo_legado_grava_com_os_defaults_do_dono():
    assert exercise_mapping_catalog._PRIMARY_CONTRIBUTION_WEIGHT == resolver.DEFAULT_WRITE_WEIGHTS["PRIMARY"]
    assert exercise_mapping_catalog._SECONDARY_CONTRIBUTION_WEIGHT == resolver.DEFAULT_WRITE_WEIGHTS["SECONDARY"]


def test_fallback_estrutural_do_volume_service_usa_os_defaults_do_dono(monkeypatch):
    from app.intelligence import volume_service

    monkeypatch.setattr(volume_service, "DEFAULT_WRITE_WEIGHTS", {"PRIMARY": 0.9, "SECONDARY": 0.4, "STABILIZER": 0.1})
    resolved = VolumeService._resolve_contributions(None, {}, "Costas", "Bíceps", 1, 2, None)
    assert [(c["muscle_group_name"], c["role"], c["stimulus_weight"]) for c in resolved] == [
        ("Costas", "PRIMARY", 0.9),
        ("Bíceps", "SECONDARY", 0.4),
    ]
    # Participação explícita continua vencendo o default.
    explicit = VolumeService._resolve_contributions(None, {}, "Costas", "Bíceps", 1, 2, 0.7)
    assert explicit[1]["stimulus_weight"] == 0.7


# ---------------------------------------------------------------------------
# Política única: exercício sem contribuição cadastrada
# ---------------------------------------------------------------------------


@pytest.fixture
def sem_banco(monkeypatch):
    cadastradas = {
        7: [{"muscle_group_id": 3, "role": "PRIMARY", "stimulus_weight": 1.0, "fatigue_weight": 0.8}],
    }
    monkeypatch.setattr(
        VolumeService,
        "_fetch_contributions_by_mapping",
        lambda self, ids: {int(i): list(cadastradas[int(i)]) for i in ids if int(i) in cadastradas},
    )
    monkeypatch.setattr(VolumeService, "fetch_all", lambda self, sql, params=(): [{"id": 3, "name": "Peitoral"}])


def test_resolver_e_volume_service_tem_a_mesma_politica_para_exercicio_sem_contribuicao(sem_banco):
    resolved = resolver._resolve_muscle_contributions_for_mappings({7, 99})

    assert [(c["muscle_group_name"], c["role"], c["stimulus_weight"]) for c in resolved[7]] == [("Peitoral", "PRIMARY", 1.0)]
    # Antes: `[]` (o exercício sumia da ativação). Agora: o mesmo "Outros" do VolumeService.
    assert resolved[99] == VolumeService._resolve_contributions(99, {}, None, None, None, None, None)
    [outros] = resolved[99]
    assert (outros["muscle_group_name"], outros["muscle_group_id"], outros["role"]) == (OUTROS_GROUP, None, "PRIMARY")
    assert outros["stimulus_weight"] == resolver.DEFAULT_WRITE_WEIGHTS["PRIMARY"]


def test_quem_agrega_por_grupo_id_ignora_outros(sem_banco):
    """Reforço ABC / Modo A agregam por `muscle_group_id`: "Outros" (id None)
    não entra, então a política nova não muda o motor de geração."""
    contributions = resolver._resolve_muscle_contributions_for_mappings({7, 99})
    totals: dict[int, float] = defaultdict(float)
    resolver._accumulate_exercise_contribution({"mapping_id": 99, "sets_count": 3}, contributions, totals)
    resolver._accumulate_exercise_contribution({"mapping_id": 7, "sets_count": 2}, contributions, totals)
    assert dict(totals) == {3: 2.0}


def test_ativacao_por_nome_mostra_outros(sem_banco):
    rows = VolumeService()._build_muscle_activation_rows(
        [{"mapping_id": 99, "sets_count": 3, "avg_weight_kg": None}],
        resolver._resolve_muscle_contributions_for_mappings({99}),
    )
    assert [(row["muscle_group"], row["primary_equivalent"]) for row in rows] == [(OUTROS_GROUP, 3.0)]


# ---------------------------------------------------------------------------
# C13: rótulos de status com um dono
# ---------------------------------------------------------------------------


def test_rotulos_tem_um_dono_com_acento():
    codigos = {status.value for status in reg.ExerciseProgressionStatus}
    assert set(EXERCISE_PROGRESSION_LABELS) == codigos
    assert {status.value: label for status, label in reg.EXERCISE_PROGRESSION_STATUS_LABELS.items()} == dict(
        EXERCISE_PROGRESSION_LABELS
    )
    for code, label in exercise_briefing.PERFORMANCE_LABELS.items():
        assert label == EXERCISE_PROGRESSION_LABELS[code]
    assert set(exercise_briefing.PERFORMANCE_LABELS) == set(exercise_briefing.COMPARABLE_STATUSES)
    assert EXERCISE_PROGRESSION_LABELS["STABLE"] == "Estável"
    assert EXERCISE_PROGRESSION_LABELS["PLATEAU_SUSPECTED"] == "Suspeita de platô"
    assert EXERCISE_PROGRESSION_LABELS["NOT_COMPARABLE"] == "Não comparável"


# ---------------------------------------------------------------------------
# C13: comparabilidade com a chave atual (o FE não refaz keys_match)
# ---------------------------------------------------------------------------


def _key(equipment_id, convention="total", agnostic=False):
    return cmp.build_key(10, equipment_id, convention, equipment_agnostic=agnostic)


@pytest.mark.parametrize(
    "key,current,expected",
    [
        (_key(7), _key(7), None),
        (_key(9), _key(7), cmp.MISMATCH_EQUIPMENT),
        (_key(7, "per_dumbbell"), _key(7), cmp.MISMATCH_LOAD_CONVENTION),
        (_key(None), _key(7), cmp.MISMATCH_EQUIPMENT_UNKNOWN),
        (_key(None, agnostic=True), _key(None, agnostic=True), None),
        # Chave atual sem equipamento e sem a marca agnóstica: nada é comparável.
        (_key(None), _key(None), cmp.MISMATCH_EQUIPMENT_UNKNOWN),
    ],
)
def test_mismatch_with_current_segue_keys_match(key, current, expected):
    assert cmp.mismatch_with_current(key, current) == expected
    assert (expected is None) == cmp.keys_match(key, current)
    assert cmp.comparison_with_current(key, current) == {
        "comparable_with_current": expected is None,
        "mismatch_with_current": expected,
    }


def test_sem_chave_atual_nada_e_marcado():
    assert cmp.comparison_with_current(_key(7), None) == {"comparable_with_current": True, "mismatch_with_current": None}


def test_contrato_publica_os_campos_de_comparabilidade():
    from app.intelligence.dashboard.contracts import ExposureOut

    campos = ExposureOut.model_fields
    assert campos["comparable_with_current"].default is True
    assert campos["mismatch_with_current"].default is None
