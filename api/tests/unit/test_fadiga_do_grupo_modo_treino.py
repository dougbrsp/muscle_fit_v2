"""M79 - indicador de fadiga do grupo no Modo Treino (dono `domain/prior_fatigue.py`).

Duas leituras no MESMO dono:
- CONTEXTO (`prior_fatigue_for_sequence`/`context_band`, `stimulus_weight`, 3
  faixas, por exercicio): chave `fatigue_context` da comparabilidade e das
  trilhas - NAO pode mudar;
- INDICADOR (`group_fatigue_for_sequence`, `fatigue_weight`, 4 faixas, por
  SERIE): o que o Modo Treino mostra.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import prior_fatigue as pf
from app.domain.prior_fatigue import (
    GroupFatigueBand,
    GroupFatigueItem,
    MuscleContribution,
    PriorFatigueBand,
)
from app.domain.session_fatigue import (
    ExecutedExercise,
    group_fatigue_before_exercise,
    group_fatigue_by_set_key,
    session_contexts,
    session_group_fatigue,
)
from app.intelligence.session_fatigue_reader import build_executed_sessions

PEITO, TRICEPS_G, OMBRO = 1, 2, 3
SUPINO, CRUCIFIXO, TRICEPS = 10, 11, 12


def c(group_id, role="PRIMARY", stimulus=1.0, fatigue=None, name=None):
    return MuscleContribution(group_id, name or f"G{group_id}", role, stimulus, fatigue)


def working(n, set_id=None):
    return {"set_id": set_id, "set_number": n, "set_type": "working", "reps": 10, "rpe": 9, "technique_code": None, "anchor_set_number": None}


def exercise(row_id, sequence, exercise_id, sets, first_set_id=None):
    return ExecutedExercise(
        row_id=row_id,
        sequence=sequence,
        exercise_id=exercise_id,
        set_rows=tuple(working(i + 1, None if first_set_id is None else first_set_id + i) for i in range(sets)),
    )


# --- Cortes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prior_sets", "band"),
    [
        (0.0, GroupFatigueBand.RESTED),
        (1.99, GroupFatigueBand.RESTED),
        (pf.PRIOR_FATIGUE_PRE_FATIGUED_MIN_SETS, GroupFatigueBand.PRE_FATIGUED),
        (3.99, GroupFatigueBand.PRE_FATIGUED),
        (pf.GROUP_FATIGUE_FATIGUED_MIN_SETS, GroupFatigueBand.FATIGUED),
        (5.99, GroupFatigueBand.FATIGUED),
        (pf.PRIOR_FATIGUE_HEAVILY_FATIGUED_MIN_SETS, GroupFatigueBand.HEAVILY_FATIGUED),
        (30.0, GroupFatigueBand.HEAVILY_FATIGUED),
    ],
)
def test_cortes_das_quatro_faixas(prior_sets, band):
    assert pf.group_band_for(prior_sets) is band


def test_corte_do_fatigado_fica_dentro_da_faixa_pre_fatigada_do_contexto():
    assert pf.PRIOR_FATIGUE_PRE_FATIGUED_MIN_SETS < pf.GROUP_FATIGUE_FATIGUED_MIN_SETS
    assert pf.GROUP_FATIGUE_FATIGUED_MIN_SETS < pf.PRIOR_FATIGUE_HEAVILY_FATIGUED_MIN_SETS


@pytest.mark.parametrize("prior_sets", [0, 0.5, 1.99, 2, 3, 3.99, 4, 5.5, 5.99, 6, 9, 12])
def test_indicador_refina_o_contexto_sem_contradizer(prior_sets):
    """Mesmos pesos -> a faixa nova e um refinamento da do contexto."""
    refinement = {
        PriorFatigueBand.RESTED: {GroupFatigueBand.RESTED},
        PriorFatigueBand.PRE_FATIGUED: {GroupFatigueBand.PRE_FATIGUED, GroupFatigueBand.FATIGUED},
        PriorFatigueBand.HEAVILY_FATIGUED: {GroupFatigueBand.HEAVILY_FATIGUED},
    }
    assert pf.group_band_for(prior_sets) in refinement[pf.band_for(prior_sets)]


def test_rotulos_das_quatro_faixas():
    assert [pf.GROUP_FATIGUE_BAND_LABELS[band] for band in GroupFatigueBand] == [
        "Descansado",
        "Pré-fatigado",
        "Fatigado",
        "Muito fatigado",
    ]
    assert pf.group_band_label("fatigued") == "Fatigado"
    assert pf.group_band_label("xpto") is None
    assert pf.group_band_label(None) is None


# --- Por serie ---------------------------------------------------------------


def test_cada_serie_soma_as_anteriores_do_proprio_exercicio_e_dos_anteriores():
    supino = (c(PEITO), c(TRICEPS_G, "SECONDARY", 0.5, 0.5))
    crucifixo = (c(PEITO),)
    first, second = pf.group_fatigue_for_sequence(
        [
            GroupFatigueItem(set_units=(1.0, 1.0, 1.0), contributions=supino),
            GroupFatigueItem(set_units=(1.0, 1.0, 1.0, 1.0), contributions=crucifixo),
        ]
    )
    assert [r.prior_sets for r in first.per_set] == [0, 1, 2]
    assert first.before.prior_sets == 0
    assert [r.band for r in first.per_set] == [
        GroupFatigueBand.RESTED,
        GroupFatigueBand.RESTED,
        GroupFatigueBand.PRE_FATIGUED,
    ]
    assert second.before.prior_sets == 3
    assert [r.prior_sets for r in second.per_set] == [3, 4, 5, 6]
    assert [r.band_label for r in second.per_set] == ["Pré-fatigado", "Fatigado", "Fatigado", "Muito fatigado"]
    assert second.per_set[0].level == pf.level_for(3)


def test_usa_fatigue_weight_e_nao_stimulus_weight():
    # supino com fadiga de triceps 1,0 (estimulo 0,5) antes do triceps
    supino = (c(PEITO), c(TRICEPS_G, "SECONDARY", stimulus=0.5, fatigue=1.0))
    _, triceps = pf.group_fatigue_for_sequence(
        [
            GroupFatigueItem(set_units=(1.0,) * 4, contributions=supino),
            GroupFatigueItem(set_units=(1.0,), contributions=(c(TRICEPS_G),)),
        ]
    )
    assert triceps.before.prior_sets == 4.0
    assert triceps.before.band is GroupFatigueBand.FATIGUED


def test_fatigue_weight_ausente_cai_no_stimulus_weight():
    assert pf.fatigue_weight_of(c(PEITO, "SECONDARY", stimulus=0.5, fatigue=None)) == 0.5
    assert pf.fatigue_weight_of(c(PEITO, "SECONDARY", stimulus=0.5, fatigue=0.0)) == 0.0
    supino = (c(PEITO), c(TRICEPS_G, "SECONDARY", stimulus=0.5))
    _, triceps = pf.group_fatigue_for_sequence(
        [
            GroupFatigueItem(set_units=(1.0,) * 4, contributions=supino),
            GroupFatigueItem(set_units=(1.0,), contributions=(c(TRICEPS_G),)),
        ]
    )
    assert triceps.before.prior_sets == 2.0


def test_sem_musculo_principal_nao_tem_leitura():
    [timeline] = pf.group_fatigue_for_sequence([GroupFatigueItem(set_units=(1.0,), contributions=())])
    assert timeline.before is None and timeline.per_set == (None,)


def test_contribution_from_mapping_le_o_fatigue_weight():
    row = {"muscle_group_id": 4, "muscle_group_name": "Costas", "role": "PRIMARY", "stimulus_weight": "1", "fatigue_weight": "0.8"}
    assert pf.contribution_from_mapping(row).fatigue_weight == 0.8
    del row["fatigue_weight"]
    assert pf.contribution_from_mapping(row).fatigue_weight is None


# --- Contexto INALTERADO ---------------------------------------------------------


def test_contexto_ignora_fatigue_weight():
    """O mesmo treino com `fatigue_weight` muito diferente do estimulo: o
    contexto (chave `fatigue_context`) continua o mesmo."""
    supino_estimulo = (c(PEITO), c(TRICEPS_G, "SECONDARY", 0.5))
    supino_fadiga = (c(PEITO, fatigue=3.0), c(TRICEPS_G, "SECONDARY", 0.5, fatigue=2.0))
    exercises = [exercise(1, 1, SUPINO, 4), exercise(2, 2, CRUCIFIXO, 3), exercise(3, 3, TRICEPS, 3)]
    base = {SUPINO: supino_estimulo, CRUCIFIXO: (c(PEITO),), TRICEPS: (c(TRICEPS_G),)}
    alterado = {**base, SUPINO: supino_fadiga, CRUCIFIXO: (c(PEITO, fatigue=0.1),)}
    assert session_contexts(exercises, base) == session_contexts(exercises, alterado)
    assert session_contexts(exercises, base) == {
        1: PriorFatigueBand.RESTED,
        2: PriorFatigueBand.PRE_FATIGUED,  # 4 series de supino no peito
        3: PriorFatigueBand.PRE_FATIGUED,  # 4 x 0,5 de triceps no supino
    }
    pf_base = pf.prior_fatigue_for_sequence(
        [pf.FatigueItem(sets=4, contributions=supino_estimulo), pf.FatigueItem(sets=3, contributions=(c(TRICEPS_G),))]
    )
    pf_alt = pf.prior_fatigue_for_sequence(
        [pf.FatigueItem(sets=4, contributions=supino_fadiga), pf.FatigueItem(sets=3, contributions=(c(TRICEPS_G),))]
    )
    assert [p.prior_sets for p in pf_base] == [p.prior_sets for p in pf_alt] == [0, 2.0]


# --- Sessao real -------------------------------------------------------------


CONTRIB = {
    SUPINO: (c(PEITO), c(TRICEPS_G, "SECONDARY", 0.5, 0.5)),
    CRUCIFIXO: (c(PEITO),),
    TRICEPS: (c(TRICEPS_G),),
}


def test_sessao_real_por_serie_indexada_pelo_id_da_serie():
    exercises = [exercise(1, 1, SUPINO, 3, first_set_id=100), exercise(2, 2, CRUCIFIXO, 3, first_set_id=200)]
    timelines = session_group_fatigue(exercises, CONTRIB)
    by_set = group_fatigue_by_set_key(exercises[1], timelines[2])
    assert {k: v.prior_sets for k, v in by_set.items()} == {200: 3, 201: 4, 202: 5}
    assert group_fatigue_by_set_key(exercises[1], None) == {}


def test_fadiga_atual_antes_do_bloco_exercicio_ainda_nao_registrado():
    exercises = [exercise(1, 1, SUPINO, 4), exercise(2, 2, TRICEPS, 3)]
    reading = group_fatigue_before_exercise(exercises, CONTRIB, CRUCIFIXO)
    assert reading.prior_sets == 4 and reading.band is GroupFatigueBand.FATIGUED


def test_fadiga_atual_com_bloco_em_andamento_ignora_as_series_do_proprio_bloco():
    exercises = [exercise(1, 1, SUPINO, 3), exercise(2, 2, CRUCIFIXO, 2)]
    reading = group_fatigue_before_exercise(exercises, CONTRIB, CRUCIFIXO)
    assert reading.prior_sets == 3 and reading.band_label == "Pré-fatigado"


def test_fadiga_atual_sem_nada_registrado_e_descansado():
    reading = group_fatigue_before_exercise([], CONTRIB, SUPINO)
    assert reading.prior_sets == 0 and reading.band is GroupFatigueBand.RESTED


def test_leitor_preserva_o_id_da_serie():
    rows = [
        {"activity_id": 9, "exercise_row_id": 1, "sequence": 1, "exercise_id": SUPINO, "set_id": 55, "set_number": 1, "set_type": "working", "reps": 8},
    ]
    [ex] = build_executed_sessions(rows)[9]
    assert ex.set_rows[0]["set_id"] == 55


# --- Contrato do endpoint ------------------------------------------------------


def test_contrato_last_session_sets_traz_fadiga_atual_e_por_serie(monkeypatch):
    from app import main
    from app.security import Identity, get_current_identity

    reading = pf.GroupFatigue(PEITO, "Peitoral", 4.0, pf.level_for(4.0), GroupFatigueBand.FATIGUED).as_dict()
    item = {
        "activity_id": 5,
        "local_date": "2026-10-01",
        "sets": [{"set_number": 1, "set_type": "working", "reps": 11, "weight_kg": 45.36, "rpe": 10, "group_fatigue": reading}],
        "record_scope": "equipment",
        "record": None,
        "e1rm_kg": None,
        "load_convention": "total",
    }
    calls = {}
    monkeypatch.setattr(main, "get_last_session_sets", lambda *a, **k: item)

    def fake_current(mapping_id, activity_id, user_id):
        calls["current"] = (mapping_id, activity_id, user_id)
        return reading

    monkeypatch.setattr(main, "get_current_group_fatigue", fake_current)
    main.app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=1, role="regular", source="session")
    try:
        resp = TestClient(main.app).get("/exercises/last-session-sets", params={"mapping_id": 10, "exclude_activity_id": 77})
    finally:
        main.app.dependency_overrides.pop(get_current_identity, None)
    assert resp.status_code == 200
    body = resp.json()
    assert calls["current"] == (10, 77, 1)
    assert body["current_group_fatigue"] == {
        "muscle_group_id": PEITO,
        "muscle_group_name": "Peitoral",
        "prior_sets": 4.0,
        "level": round(pf.level_for(4.0), 4),
        "band": "fatigued",
        "band_label": "Fatigado",
    }
    assert body["item"]["sets"][0]["group_fatigue"]["band_label"] == "Fatigado"
