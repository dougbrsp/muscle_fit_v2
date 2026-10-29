"""Integracao do servico canonico de vinculos equipamento <-> exercicio
(app/equipment_exercises.py, PR-05) contra MySQL real (schema de migrations).

Cobre a escrita primaria em `equipment_exercises`, o espelho legado em
`equipment_exercise_mappings` (pulado de forma deterministica quando violaria
a FK legada) e o recalculo de `gym_equipment` com os avisos propagados.
"""

from __future__ import annotations

import pytest

from app import equipment_exercises, gym_equipment_sync
from app.equipment_exercises import (
    MIRROR_OK,
    deactivate_equipment_exercise,
    upsert_equipment_exercise,
)


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    # Deteccoes de FK sao cacheadas por processo; isola cada teste.
    equipment_exercises.reset_legacy_fk_cache()
    gym_equipment_sync.reset_fk_ref_cache()


def gym_rows(conn, gym_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT mapping_id, equipment_id, is_active FROM gym_equipment WHERE gym_id = %s ORDER BY mapping_id, equipment_id",
            (gym_id,),
        )
        return [(r["mapping_id"], r["equipment_id"], r["is_active"]) for r in cur.fetchall()]


def fetch_one(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def test_vinculo_com_exercicio_so_no_catalogo_espelha_e_materializa(db_conn, catalog):
    """No formato de PRODUCAO (schema compartilhado com todas as migracoes,
    M25) `equipment_exercise_mappings.mapping_id` e `gym_equipment.mapping_id`
    referenciam `exercise_catalog`: um exercicio que existe SO no catalogo
    canonico nao e mais orfao - o espelho e escrito e o cruzamento
    materializado, sem aviso nenhum.

    O ramo LEGADO (FK ainda em `exercise_zepp_mappings`, espelho pulado com
    `MIRROR_SKIPPED_FK_TARGET_MISSING` e aviso de exclusao no sync) continua
    coberto em `test_migration_023_mapping_fks.py::test_antes_da_023_exclui_do_
    sync_e_pula_espelho_depois_materializa_e_espelha` e no equivalente sobre o
    Data API (`dataapi/test_migration_023_dataapi.py`), que montam o schema
    001..021 proprio - o unico lugar onde esse estado existe de verdade.
    """
    polia = catalog.equipment["Polia"]  # academia principal possui
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (2002, 'Outro so no catalogo')")

    row = upsert_equipment_exercise(db_conn, polia, 2002)

    # escrita canonica feita
    assert row["equipment_id"] == polia
    assert row["exercise_id"] == 2002
    assert row["is_active"] is True
    assert fetch_one(
        db_conn,
        "SELECT is_active FROM equipment_exercises WHERE equipment_id = %s AND exercise_id = 2002",
        (polia,),
    )["is_active"] == 1

    # espelho legado escrito: a FK aponta para o catalogo canonico
    assert row["legacy_mirror"] == MIRROR_OK
    assert fetch_one(db_conn, "SELECT COUNT(*) AS n FROM equipment_exercise_mappings WHERE mapping_id = 2002")["n"] == 1

    # sync sem aviso e com o exercicio materializado na academia que tem a Polia
    assert row["gym_sync_warnings"] == []
    assert 2002 in [mapping_id for mapping_id, _, _ in gym_rows(db_conn, catalog.gym_id)]

    # demais exercicios da academia seguem materializados
    ex, eq = catalog.exercises, catalog.equipment
    assert (ex["Crucifixo"], eq["Polia"], 1) in gym_rows(db_conn, catalog.gym_id)
    assert (ex["Supino reto"], eq["Banco reto"], 1) in gym_rows(db_conn, catalog.gym_id)


def test_exercicio_legado_e_espelhado_materializado_e_desativado(db_conn, catalog):
    rack = catalog.equipment["Rack"]  # so a outra academia possui
    supino = catalog.exercises["Supino reto"]  # existe nos dois catalogos

    row = upsert_equipment_exercise(db_conn, rack, supino)

    assert row["legacy_mirror"] == MIRROR_OK
    assert row["gym_sync_warnings"] == []
    assert fetch_one(
        db_conn,
        "SELECT is_active FROM equipment_exercise_mappings WHERE equipment_id = %s AND mapping_id = %s",
        (rack, supino),
    )["is_active"] == 1
    assert (supino, rack, 1) in gym_rows(db_conn, catalog.other_gym_id)
    assert all(equipment_id != rack for _, equipment_id, _ in gym_rows(db_conn, catalog.gym_id))

    # idempotente: reenviar reativa a mesma linha, sem duplicar
    again = upsert_equipment_exercise(db_conn, rack, supino)
    assert again["id"] == row["id"]

    deactivated = deactivate_equipment_exercise(db_conn, row["id"])

    assert deactivated["is_active"] is False
    assert deactivated["gym_sync_warnings"] == []
    assert fetch_one(
        db_conn,
        "SELECT is_active FROM equipment_exercises WHERE id = %s",
        (row["id"],),
    )["is_active"] == 0
    assert fetch_one(
        db_conn,
        "SELECT is_active FROM equipment_exercise_mappings WHERE equipment_id = %s AND mapping_id = %s",
        (rack, supino),
    )["is_active"] == 0
    assert (supino, rack, 0) in gym_rows(db_conn, catalog.other_gym_id)

    # reativacao pelo mesmo par volta a materializar
    reactivated = upsert_equipment_exercise(db_conn, rack, supino)
    assert reactivated["id"] == row["id"]
    assert (supino, rack, 1) in gym_rows(db_conn, catalog.other_gym_id)


def test_desativar_id_inexistente_retorna_404(db_conn, catalog):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        deactivate_equipment_exercise(db_conn, 999999)
    assert exc.value.status_code == 404
