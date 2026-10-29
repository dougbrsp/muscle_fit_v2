"""Fase 5 - PR 1: a edição do registro (PUT /exercises/logs/{id}) preserva a
série. Regra pura em app/exercise_set_update_merge.py."""

from decimal import Decimal

from app.exercise_set_update_merge import merge_sets_for_update
from app.models import ExerciseLogUpdateIn

STORED = [
    {"set_number": 1, "set_type": "warmup", "rest_seconds": 60, "hr_avg_bpm": Decimal("110.00"),
     "hr_max_bpm": Decimal("125.00"), "notes": "leve", "muscle_pain": None, "joint_pain": None},
    {"set_number": 2, "set_type": "working", "rest_seconds": 90, "hr_avg_bpm": Decimal("130.00"),
     "hr_max_bpm": Decimal("150.00"), "notes": None, "muscle_pain": 3, "joint_pain": 0},
    {"set_number": 3, "set_type": "drop_set", "rest_seconds": 0, "hr_avg_bpm": None,
     "hr_max_bpm": None, "notes": "drop 1", "muscle_pain": None, "joint_pain": 1},
    {"set_number": 4, "set_type": "rest_pause", "rest_seconds": 15, "hr_avg_bpm": None,
     "hr_max_bpm": None, "notes": None, "muscle_pain": 5, "joint_pain": None},
]


def _sem_campos_extras(n, reps=10):
    return {"set_number": n, "reps": reps, "weight_kg": 50, "rpe": 8}


def test_campo_omitido_preserva_o_valor_gravado_da_mesma_serie():
    merged = merge_sets_for_update([_sem_campos_extras(n) for n in (1, 2, 3, 4)], STORED)

    campos = ("set_type", "rest_seconds", "hr_avg_bpm", "hr_max_bpm", "notes", "muscle_pain", "joint_pain")
    assert [tuple(m[c] for c in campos) for m in merged] == [tuple(s[c] for c in campos) for s in STORED]
    # os valores editados continuam valendo
    assert [m["reps"] for m in merged] == [10, 10, 10, 10]


def test_campo_enviado_vence_inclusive_null_explicito():
    merged = merge_sets_for_update(
        [{**_sem_campos_extras(3), "set_type": "working", "notes": None, "rest_seconds": 45}], STORED
    )

    assert merged[0]["set_type"] == "working"
    assert merged[0]["notes"] is None
    assert merged[0]["rest_seconds"] == 45
    assert merged[0]["joint_pain"] == 1  # omitido -> preservado


def test_serie_nova_recebe_o_default_da_coluna():
    merged = merge_sets_for_update([_sem_campos_extras(5)], STORED)

    assert merged[0]["set_type"] == "working"
    assert all(merged[0][c] is None for c in ("rest_seconds", "hr_avg_bpm", "notes", "muscle_pain", "joint_pain"))


def test_set_type_legado_e_normalizado_e_set_number_ausente_usa_a_posicao():
    merged = merge_sets_for_update([{"reps": 8, "set_type": "dropset"}, {"reps": 8}], STORED)

    assert [(m["set_number"], m["set_type"]) for m in merged] == [(1, "drop_set"), (2, "working")]


def test_rota_so_repassa_as_chaves_enviadas_por_serie():
    """O default `set_type="working"` do Pydantic não pode contar como
    enviado, senão a edição volta a apagar o drop-set (mesmo dump da rota)."""
    payload = ExerciseLogUpdateIn(
        local_date="2026-09-29",
        original_name="Supino reto",
        sets=[{"set_number": 3, "reps": 8}, {"set_number": 4, "reps": 6, "set_type": "rest_pause", "joint_pain": None}],
    )
    sent = [s.model_dump(exclude_unset=True) for s in payload.sets]

    assert sent == [
        {"set_number": 3, "reps": 8},
        {"set_number": 4, "reps": 6, "set_type": "rest_pause", "joint_pain": None},
    ]
    assert [m["set_type"] for m in merge_sets_for_update(sent, STORED)] == ["drop_set", "rest_pause"]
