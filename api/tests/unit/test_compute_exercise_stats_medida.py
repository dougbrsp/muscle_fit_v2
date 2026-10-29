"""Agregados do exercício e hashes de importação com a medida da série
(Lote T3 das medidas de série).

- `compute_exercise_stats`: toda série conta em `sets_count`; `reps_total`
  inclui o máximo de reps; `volume_kg` só das séries de repetições;
- `make_external_id`/`row_hash`: a medida AUSENTE (todo cliente antigo, que
  `ActivityIn.model_dump()` passa a trazer como `None`) não muda o hash -
  senão o mesmo treino reimportado sem `external_id` viraria duplicata;
- edição: medida/duração omitidas preservam as gravadas.

Rodar: cd api && python -m pytest tests/unit/test_compute_exercise_stats_medida.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import services
from app.exercise_set_update_merge import PRESERVED_SET_FIELDS, merge_sets_for_update
from app.models import ActivityIn
from app.services import compute_exercise_stats, make_external_id, row_hash
from app.set_measure_input import SetMeasureInputError
from tests.support.fake_db import fake_transaction


def test_volume_so_de_series_de_reps_e_reps_total_com_maximo():
    sets = [
        {"set_type": "working", "reps": 10, "weight_kg": 40, "rpe": 8},
        {"set_type": "working", "reps": 15, "weight_kg": 40, "rpe": 10, "measure": "max_reps"},
        {"set_type": "working", "reps": None, "weight_kg": 10, "rpe": 8, "measure": "duration", "duration_seconds": 60},
        {"set_type": "warmup", "reps": 12, "weight_kg": 20},
    ]
    stats = compute_exercise_stats(sets)
    assert stats["sets_count"] == 3
    assert stats["reps_total"] == 25
    assert stats["volume_kg"] == Decimal(400)


def test_sem_medida_igual_a_antes():
    sets = [{"set_type": "working", "reps": 10, "weight_kg": 40}, {"set_type": "working", "reps": 8, "weight_kg": 40}]
    assert compute_exercise_stats(sets)["volume_kg"] == Decimal(720)


def test_so_prancha_nao_tem_volume():
    sets = [{"set_type": "working", "measure": "duration", "duration_seconds": 60, "weight_kg": 10}]
    stats = compute_exercise_stats(sets)
    assert stats["volume_kg"] is None and stats["sets_count"] == 1


def _payload(**set_extra):
    return {
        "source_name": "telegram",
        "local_date": "2026-10-20",
        "name": "Treino A",
        "exercises": [
            {"sequence": 1, "original_name": "Supino", "sets": [{"set_number": 1, "reps": 10, "weight_kg": 40, **set_extra}]}
        ],
    }


def test_hash_de_importacao_estavel_com_a_medida_ausente():
    antigo = _payload()
    novo = ActivityIn(**_payload()).model_dump()
    # O dump do modelo novo traz `measure`/`duration_seconds` = None nas séries.
    assert novo["exercises"][0]["sets"][0]["measure"] is None
    legado = ActivityIn(**antigo).model_dump()
    for st in legado["exercises"][0]["sets"]:
        st.pop("measure")
        st.pop("duration_seconds")
    assert make_external_id(novo) == make_external_id(legado)
    assert row_hash("x", novo["exercises"][0]) == row_hash("x", legado["exercises"][0])


def test_hash_muda_quando_a_medida_e_enviada():
    sem = ActivityIn(**_payload()).model_dump()
    com = ActivityIn(**_payload(measure="max_reps")).model_dump()
    assert make_external_id(sem) != make_external_id(com)


def test_edicao_preserva_medida_e_duracao_omitidas():
    assert {"measure", "duration_seconds"} <= set(PRESERVED_SET_FIELDS)
    gravadas = [{"set_number": 1, "set_type": "working", "measure": "duration", "duration_seconds": 60}]
    mescladas = merge_sets_for_update([{"set_number": 1, "reps": None, "weight_kg": None}], gravadas)
    assert (mescladas[0]["measure"], mescladas[0]["duration_seconds"]) == ("duration", 60)
    trocada = merge_sets_for_update([{"set_number": 1, "reps": 12, "measure": "reps", "duration_seconds": None}], gravadas)
    assert (trocada[0]["measure"], trocada[0]["duration_seconds"]) == ("reps", None)


class _EdicaoCursor:
    """Só as duas leituras que `update_exercise_log` faz antes de validar."""

    def __init__(self, gravadas):
        self.gravadas = gravadas
        self.sql: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    def fetchone(self):
        return {"id": 7, "activity_id": 3, "original_name": "Prancha", "mapping_id": 1,
                "workout_plan_exercise_id": None, "equipment_id": None, "muscle_pain": None, "joint_pain": None}

    def fetchall(self):
        return self.gravadas


def test_edicao_com_forma_invalida_apos_a_mescla_levanta_antes_de_gravar(monkeypatch):
    cur = _EdicaoCursor([{"set_number": 1, "set_type": "working", "measure": "duration", "duration_seconds": 60}])
    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    # Cliente antigo manda reps numa série gravada por tempo (medida omitida).
    with pytest.raises(SetMeasureInputError) as err:
        services.update_exercise_log(7, {"local_date": "2026-10-20", "sets": [{"set_number": 1, "reps": 12}]}, user_id=1)
    assert "reps_not_allowed" in err.value.codes and err.value.detail()["set_number"] == 1
    assert not any(sql.startswith(("UPDATE", "DELETE", "INSERT")) for sql in cur.sql)
    # A leitura das gravadas traz as colunas da medida (round-trip).
    assert any("measure, duration_seconds, reps_left, reps_right, rpe_left, rpe_right FROM exercise_sets" in sql for sql in cur.sql)
