"""Fase 5, PR 2 - vínculo da aplicação de técnica com o catálogo e a âncora.

- Modo Treino: `technique_code`/`anchor_set_number` explícitos são gravados;
  ausentes numa aplicação, são derivados (técnica da ficha ou do `set_type`,
  última série de trabalho anterior); código fora do catálogo -> 422.
- Migração 060: o backfill preenche linhas antigas e é idempotente.

Roda só no CI (MySQL).
"""

from __future__ import annotations

from app import migrate as m
from app.db import transaction
from tests.integration.test_pr14_persistencia_intensidade import (  # noqa: F401 (fixtures)
    _ficha,
    _grava,
    _sessao,
    academia,
    client,
)

MIGRATION_060 = m.DEFAULT_MIGRATIONS_DIR / "060_tecnicas_coeficientes_e_backfill.py"


def _vinculos(conn, exercise_id: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_number, set_type, technique_code, anchor_set_number FROM exercise_sets "
            "WHERE exercise_id = %s ORDER BY set_number",
            (exercise_id,),
        )
        return [(r["set_number"], r["set_type"], r["technique_code"], r["anchor_set_number"]) for r in cur.fetchall()]


def _sessao_com_tecnica(client, catalog, academia, tecnica: str):  # noqa: F811 - fixture do pytest importada (falso positivo)
    plan = _ficha(catalog, academia, rpe_target="10")
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workout_plan_exercises SET advanced_technique = %s, advanced_technique_applications = 2 "
                "WHERE id = %s",
                (tecnica, plan["exercises"][0]["workout_plan_exercise_id"]),
            )
    return _sessao(client, catalog, academia, plan)


def test_aplicacao_sem_vinculo_e_derivada_da_ficha_e_da_ultima_serie_de_trabalho(client, catalog, academia, db_conn):  # noqa: F811 - fixture do pytest importada (falso positivo)
    session_id, exercise_id = _sessao_com_tecnica(client, catalog, academia, "negative_reps")
    assert _grava(client, session_id, exercise_id, set_type="warmup").status_code == 200
    assert _grava(client, session_id, exercise_id, set_type="working").status_code == 200
    assert _grava(client, session_id, exercise_id, set_type="working").status_code == 200
    resp = _grava(client, session_id, exercise_id, set_type="forced_rep", reps=3)
    assert resp.status_code == 200
    assert (resp.json()["technique_code"], resp.json()["anchor_set_number"]) == ("negative_reps", 3)
    assert _vinculos(db_conn, exercise_id)[-1] == (4, "forced_rep", "negative_reps", 3)
    assert _vinculos(db_conn, exercise_id)[1][2:] == (None, None)


def test_vinculo_explicito_e_gravado_e_codigo_invalido_e_422(client, catalog, academia, db_conn):  # noqa: F811 - fixture do pytest importada (falso positivo)
    session_id, exercise_id = _sessao_com_tecnica(client, catalog, academia, "rest_pause")
    _grava(client, session_id, exercise_id, set_type="working")
    _grava(client, session_id, exercise_id, set_type="working")
    resp = _grava(client, session_id, exercise_id, set_type="rest_pause", reps=6,
                  technique_code="rest_pause", anchor_set_number=1)
    assert resp.status_code == 200
    assert _vinculos(db_conn, exercise_id)[-1] == (3, "rest_pause", "rest_pause", 1)
    ruim = _grava(client, session_id, exercise_id, set_type="rest_pause", technique_code="nao_existe")
    assert ruim.status_code == 422


def test_backfill_da_060_preenche_linhas_antigas_e_e_idempotente(client, catalog, academia, db_conn):  # noqa: F811 - fixture do pytest importada (falso positivo)
    session_id, exercise_id = _sessao_com_tecnica(client, catalog, academia, "drop_set")
    for tipo in ("working", "drop_set", "working", "dropset"):
        _grava(client, session_id, exercise_id, set_type=tipo)
    with db_conn.cursor() as cur:
        # linha legada com alias e tudo NULL, como antes da 059/060
        cur.execute("UPDATE exercise_sets SET technique_code = NULL, anchor_set_number = NULL WHERE exercise_id = %s",
                    (exercise_id,))
        cur.execute("UPDATE exercise_sets SET set_type = 'dropset' WHERE exercise_id = %s AND set_number = 4",
                    (exercise_id,))
    db_conn.commit()

    db = m.MigrationDB(db_conn)
    m.load_python_migration(MIGRATION_060)(db)
    db_conn.commit()
    assert _vinculos(db_conn, exercise_id) == [
        (1, "working", None, None),
        (2, "drop_set", "drop_set", 1),
        (3, "working", None, None),
        (4, "dropset", "drop_set", 3),
    ]
    assert m.load_python_migration(MIGRATION_060).__globals__["plan"](m.MigrationDB(db_conn)) == []
