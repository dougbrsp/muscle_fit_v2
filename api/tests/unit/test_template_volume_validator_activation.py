"""Caracterizacao de template_volume_validator.calculate_slot_activation.

I/O isolado: a unica leitura de banco no caminho e
`muscle_contribution_resolver._resolve_muscle_contributions_for_mappings`,
substituida por um catalogo fake. O calculo real `sets x stimulus_weight`
(`VolumeService._build_muscle_activation_rows`) roda de verdade."""

from __future__ import annotations

import unicodedata

import pytest

from app.intelligence import muscle_contribution_resolver
from app.intelligence.template_volume_validator import calculate_slot_activation

PEITORAL, TRICEPS, OMBROS, COSTAS = 1, 2, 3, 4
NAMES = {PEITORAL: "Peitoral", TRICEPS: "Tríceps", OMBROS: "Ombros", COSTAS: "Costas"}

SUPINO, CRUCIFIXO, REMADA, PRANCHA = 101, 102, 103, 104

FAKE_CONTRIBUTIONS = {
    SUPINO: [
        {"muscle_group_id": PEITORAL, "muscle_group_name": "Peitoral", "role": "PRIMARY", "stimulus_weight": 1.0},
        {"muscle_group_id": TRICEPS, "muscle_group_name": "Tríceps", "role": "SECONDARY", "stimulus_weight": 0.5},
        {"muscle_group_id": OMBROS, "muscle_group_name": "Ombros", "role": "SECONDARY", "stimulus_weight": 0.33},
    ],
    CRUCIFIXO: [
        {"muscle_group_id": PEITORAL, "muscle_group_name": "Peitoral", "role": "PRIMARY", "stimulus_weight": 1.0},
    ],
    REMADA: [
        {"muscle_group_id": COSTAS, "muscle_group_name": "Costas", "role": "PRIMARY", "stimulus_weight": 1.0},
    ],
    PRANCHA: [
        {"muscle_group_id": OMBROS, "muscle_group_name": "Ombros", "role": "STABILIZER", "stimulus_weight": 0.2},
        # papel desconhecido e somado como secundario
        {"muscle_group_id": COSTAS, "muscle_group_name": "Costas", "role": "whatever", "stimulus_weight": 0.1},
    ],
}


@pytest.fixture
def resolver_calls(monkeypatch):
    calls: list[set[int]] = []

    def fake_resolver(mapping_ids):
        calls.append(set(mapping_ids))
        return {mid: FAKE_CONTRIBUTIONS.get(mid, []) for mid in mapping_ids}

    monkeypatch.setattr(
        muscle_contribution_resolver, "_resolve_muscle_contributions_for_mappings", fake_resolver
    )
    return calls


def test_soma_sets_vezes_stimulus_weight_por_papel(resolver_calls):
    result = calculate_slot_activation(
        [
            {"mapping_id": SUPINO, "sets_count": 3},
            {"mapping_id": CRUCIFIXO, "sets_count": 2},
        ],
        NAMES,
    )
    assert result == {
        PEITORAL: {"primary": 5.0, "secondary": 0.0, "stabilizer": 0.0, "total": 5.0},
        TRICEPS: {"primary": 0.0, "secondary": 1.5, "stabilizer": 0.0, "total": 1.5},
        # 3 x 0.33 = 0.99 (arredondado a 2 casas pelo VolumeService)
        OMBROS: {"primary": 0.0, "secondary": 0.99, "stabilizer": 0.0, "total": 0.99},
    }


def test_estabilizador_e_papel_desconhecido(resolver_calls):
    result = calculate_slot_activation([{"mapping_id": PRANCHA, "sets_count": 4}], NAMES)
    assert result[OMBROS]["stabilizer"] == pytest.approx(0.8)
    assert result[OMBROS]["total"] == pytest.approx(0.8)
    assert result[COSTAS]["secondary"] == pytest.approx(0.4)


def test_ignora_itens_sem_mapping_ou_sem_series(resolver_calls):
    result = calculate_slot_activation(
        [
            {"mapping_id": None, "sets_count": 3},
            {"mapping_id": REMADA, "sets_count": 0},
            {"mapping_id": REMADA, "sets_count": None},
            {"sets_count": 3},
            {"mapping_id": CRUCIFIXO, "sets_count": 1},
        ],
        NAMES,
    )
    assert resolver_calls == [{CRUCIFIXO}]
    assert set(result) == {PEITORAL}


def test_sem_exercicios_retorna_vazio(resolver_calls):
    assert calculate_slot_activation([], NAMES) == {}


def test_grupo_fora_do_mapa_de_nomes_e_descartado(resolver_calls):
    result = calculate_slot_activation([{"mapping_id": SUPINO, "sets_count": 2}], {PEITORAL: "Peitoral"})
    assert set(result) == {PEITORAL}


def test_casamento_de_nome_normaliza_caixa_espacos_e_nfc(resolver_calls):
    decomposed = unicodedata.normalize("NFD", "  TRÍCEPS ")
    result = calculate_slot_activation([{"mapping_id": SUPINO, "sets_count": 2}], {77: decomposed})
    assert result == {77: {"primary": 0.0, "secondary": 1.0, "stabilizer": 0.0, "total": 1.0}}


def test_nomes_duplicados_ultimo_id_vence(resolver_calls):
    # comportamento atual (risco documentado no docstring): o resultado e
    # indexado por NOME; dois ids com o mesmo nome colidem e so o ultimo fica.
    result = calculate_slot_activation(
        [{"mapping_id": CRUCIFIXO, "sets_count": 2}], {10: "Peitoral", 20: "peitoral"}
    )
    assert result == {20: {"primary": 2.0, "secondary": 0.0, "stabilizer": 0.0, "total": 2.0}}
