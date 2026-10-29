"""Fase 5 - PR 2: a edição do registro (PUT /exercises/logs/{id}) faz o
round-trip de `technique_code`/`anchor_set_number` (colunas da 059) com o
mesmo critério do PR 1: omitido preserva o valor da série de mesmo
`set_number`; enviado vence. Aplicação de técnica sem vínculo recebe o
derivado (mesma regra da importação e do Modo Treino)."""

from __future__ import annotations


import pytest
from fastapi import HTTPException

from app import advanced_techniques, services
from app.domain import effective_intensity
from app.exercise_set_update_merge import (
    link_technique_applications,
    merge_sets_for_update,
    validate_sent_technique_codes,
)
from app.models import ExerciseLogUpdateIn
from tests.support.fake_db import LoopingExecuteMany, fake_transaction

STORED = [
    {"set_number": 1, "set_type": "working", "technique_code": None, "anchor_set_number": None},
    {"set_number": 2, "set_type": "working", "technique_code": None, "anchor_set_number": None},
    {"set_number": 3, "set_type": "forced_rep", "technique_code": "negative_reps", "anchor_set_number": 2},
    {"set_number": 4, "set_type": "drop_set", "technique_code": "drop_set", "anchor_set_number": 1},
]


def _serie(n, **extra):
    return {"set_number": n, "reps": 8, "weight_kg": 40, "rpe": 9, **extra}


def test_omitido_preserva_o_vinculo_gravado_da_mesma_serie():
    merged = merge_sets_for_update([_serie(n) for n in (1, 2, 3, 4)], STORED)
    link_technique_applications(merged)

    assert [(m["technique_code"], m["anchor_set_number"]) for m in merged] == [
        (None, None),
        (None, None),
        ("negative_reps", 2),  # a derivação daria forced_reps: o gravado vence
        ("drop_set", 1),  # a derivação daria 2: o gravado vence
    ]


def test_enviado_vence_o_gravado():
    merged = merge_sets_for_update([_serie(3, technique_code="cheat_reps", anchor_set_number=1)], STORED)
    link_technique_applications(merged)

    assert (merged[0]["technique_code"], merged[0]["anchor_set_number"]) == ("cheat_reps", 1)


def test_aplicacao_sem_vinculo_recebe_o_derivado_com_a_tecnica_da_ficha():
    sent = [_serie(1), _serie(2, set_type="warmup"), _serie(3), _serie(4, set_type="rest_pause"), _serie(5, set_type="forced_rep")]
    merged = merge_sets_for_update(sent, [])
    link_technique_applications(merged, plan_technique="negative_reps")

    assert [(m["technique_code"], m["anchor_set_number"]) for m in merged] == [
        (None, None),
        (None, None),
        (None, None),
        ("rest_pause", 3),  # ficha com negative_reps grava forced_rep: não vale para rest_pause
        ("negative_reps", 3),  # âncora: última working ANTERIOR (o aquecimento não conta)
    ]


def test_null_explicito_numa_aplicacao_recalcula_o_vinculo():
    merged = merge_sets_for_update([_serie(1), _serie(2, set_type="drop_set", technique_code=None, anchor_set_number=None)], [])
    link_technique_applications(merged)

    assert (merged[1]["technique_code"], merged[1]["anchor_set_number"]) == ("drop_set", 1)


def test_rota_so_repassa_as_chaves_enviadas_e_valida_os_limites():
    payload = ExerciseLogUpdateIn(
        local_date="2026-09-30",
        original_name="Supino reto",
        sets=[{"set_number": 3, "reps": 8}, {"set_number": 4, "reps": 6, "technique_code": "drop_set", "anchor_set_number": 1}],
    )
    assert [s.model_dump(exclude_unset=True) for s in payload.sets] == [
        {"set_number": 3, "reps": 8},
        {"set_number": 4, "reps": 6, "technique_code": "drop_set", "anchor_set_number": 1},
    ]
    with pytest.raises(ValueError):
        ExerciseLogUpdateIn(local_date="2026-09-30", original_name="x", sets=[{"set_number": 1, "anchor_set_number": 0}])
    with pytest.raises(ValueError):
        ExerciseLogUpdateIn(local_date="2026-09-30", original_name="x", sets=[{"set_number": 1, "technique_code": "x" * 21}])


def test_so_codigos_enviados_sao_validados_e_normalizados(monkeypatch):
    monkeypatch.setattr(advanced_techniques, "active_codes", lambda: {"drop_set", "rest_pause"})

    with pytest.raises(HTTPException) as erro:
        validate_sent_technique_codes([{"set_number": 1, "technique_code": "inexistente"}])
    assert erro.value.status_code == 422

    sets = [{"set_number": 1, "technique_code": " drop_set "}, {"set_number": 2, "technique_code": ""}, {"set_number": 3}]
    validate_sent_technique_codes(sets)
    assert sets == [{"set_number": 1, "technique_code": "drop_set"}, {"set_number": 2, "technique_code": None}, {"set_number": 3}]


# ---------------------------------------------------------------------------
# update_exercise_log: o vínculo chega ao INSERT (dublê de cursor)
# ---------------------------------------------------------------------------


class _Cursor(LoopingExecuteMany):
    def __init__(self, stored_sets, plan_technique):
        self.stored_sets = stored_sets
        self.plan_technique = plan_technique
        self.inserts: list[tuple] = []
        self.read_plan = False
        self._result: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT e.id, e.activity_id"):
            self._result = [{"id": 10, "activity_id": 20, "original_name": "Supino", "mapping_id": 5,
                             "workout_plan_exercise_id": 30, "equipment_id": None, "muscle_pain": None, "joint_pain": None}]
        elif flat.startswith("SELECT set_number,"):
            assert "technique_code" in flat and "anchor_set_number" in flat
            self._result = self.stored_sets
        elif flat.startswith("SELECT advanced_technique FROM workout_plan_exercises"):
            assert params == (30,)
            self.read_plan = True
            self._result = [{"advanced_technique": self.plan_technique}]
        elif flat.startswith("INSERT INTO exercise_sets"):
            self.inserts.append(params)
            self._result = []
        else:
            self._result = [{"avg_rpe": None}]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


def _run_update(monkeypatch, sets, stored_sets, plan_technique=None):
    cur = _Cursor(stored_sets, plan_technique)

    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    monkeypatch.setattr(
        effective_intensity, "resolve_for_logged_exercise", lambda conn, eid: effective_intensity.UNKNOWN_INTENSITY
    )
    services.update_exercise_log(10, {"local_date": "2026-09-30", "original_name": "Supino", "sets": sets}, user_id=7)
    # (exercise_id, set_number, set_type, ..., technique_code, anchor_set_number, measure, duration_seconds)
    # Lote T3: as duas colunas da medida vieram depois do vinculo da tecnica;
    # Modo Intensivo (092): mais 5 colunas (pendencia, conclusao, carga planejada, origens) no fim;
    # M102 (100): mais 4 (lados da serie unilateral).
    return [(p[1], p[2], p[-13], p[-12]) for p in cur.inserts], cur.read_plan


def test_update_grava_o_vinculo_preservado_e_o_derivado(monkeypatch):
    stored = [
        {"set_number": 1, "set_type": "working", "rest_seconds": None, "hr_avg_bpm": None, "hr_max_bpm": None,
         "notes": None, "muscle_pain": None, "joint_pain": None, "technique_code": None, "anchor_set_number": None},
        {"set_number": 2, "set_type": "forced_rep", "rest_seconds": None, "hr_avg_bpm": None, "hr_max_bpm": None,
         "notes": None, "muscle_pain": None, "joint_pain": None, "technique_code": "cheat_reps", "anchor_set_number": 1},
    ]
    sets = [_serie(1), _serie(2), _serie(3, set_type="forced_rep")]  # a 3 é nova

    linhas, leu_ficha = _run_update(monkeypatch, sets, stored, plan_technique="negative_reps")
    assert leu_ficha

    assert linhas == [
        (1, "working", None, None),
        (2, "forced_rep", "cheat_reps", 1),  # preservado
        (3, "forced_rep", "negative_reps", 1),  # derivado com a técnica da ficha
    ]


def test_update_sem_aplicacao_pendente_nao_le_a_ficha(monkeypatch):
    sets = [_serie(1), _serie(2, set_type="drop_set", technique_code="drop_set", anchor_set_number=1)]
    linhas, leu_ficha = _run_update(monkeypatch, sets, [])

    assert not leu_ficha
    assert linhas == [(1, "working", None, None), (2, "drop_set", "drop_set", 1)]
