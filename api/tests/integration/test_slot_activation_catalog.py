"""calculate_slot_activation ponta a ponta com o catalogo real do banco
(exercise_muscle_contributions + muscle_groups), validando tambem a fixture
`catalog` que os PRs seguintes vao reutilizar."""

from __future__ import annotations

import pytest

from app.intelligence.template_volume_validator import calculate_slot_activation


def test_ativacao_usa_contribuicoes_do_catalogo(catalog):
    mg, ex = catalog.muscle_groups, catalog.exercises
    names = {group_id: name for name, group_id in mg.items()}

    result = calculate_slot_activation(
        [
            {"mapping_id": ex["Supino reto"], "sets_count": 4},
            {"mapping_id": ex["Crucifixo"], "sets_count": 2},
            {"mapping_id": ex["Remada baixa"], "sets_count": 3},
        ],
        names,
    )

    assert result[mg["Peitoral"]]["primary"] == pytest.approx(6.0)
    assert result[mg["Tríceps"]]["secondary"] == pytest.approx(2.0)
    assert result[mg["Ombros"]]["secondary"] == pytest.approx(1.2)
    assert result[mg["Costas"]]["total"] == pytest.approx(3.0)
    assert result[mg["Bíceps"]]["secondary"] == pytest.approx(1.5)
    assert mg["Quadríceps"] not in result


def test_contribuicao_inativa_e_ignorada(db_conn, catalog):
    mg, ex = catalog.muscle_groups, catalog.exercises
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE exercise_muscle_contributions SET is_active = 0 WHERE exercise_id = %s AND muscle_group_id = %s",
            (ex["Supino reto"], mg["Tríceps"]),
        )
    db_conn.commit()

    result = calculate_slot_activation(
        [{"mapping_id": ex["Supino reto"], "sets_count": 4}],
        {group_id: name for name, group_id in mg.items()},
    )
    assert mg["Tríceps"] not in result
    assert result[mg["Peitoral"]]["primary"] == pytest.approx(4.0)
