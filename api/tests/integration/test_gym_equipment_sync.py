"""Caracterizacao de gym_equipment_sync.sync_gym_equipment_for_gym /
sync_gym_equipment_for_equipment contra MySQL real (schema de migrations)."""

from __future__ import annotations

import pytest

from app import gym_equipment_sync
from app.gym_equipment_sync import sync_gym_equipment_for_equipment, sync_gym_equipment_for_gym


@pytest.fixture(autouse=True)
def _reset_fk_cache():
    # A deteccao da FK de gym_equipment.mapping_id e cacheada por processo;
    # zera para cada teste nao depender da ordem de execucao.
    gym_equipment_sync.reset_fk_ref_cache()
    yield
    gym_equipment_sync.reset_fk_ref_cache()


def gym_rows(conn, gym_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT mapping_id, equipment_id, is_active FROM gym_equipment WHERE gym_id = %s ORDER BY mapping_id, equipment_id",
            (gym_id,),
        )
        return [(r["mapping_id"], r["equipment_id"], r["is_active"]) for r in cur.fetchall()]


def execute(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)


def test_materializa_cruzamento_academia_x_equipamento_x_exercicio(db_conn, catalog):
    sync_gym_equipment_for_gym(db_conn, catalog.gym_id)
    ex, eq = catalog.exercises, catalog.equipment
    assert gym_rows(db_conn, catalog.gym_id) == [
        (ex["Supino reto"], eq["Banco reto"], 1),
        (ex["Crucifixo"], eq["Polia"], 1),
        (ex["Remada baixa"], eq["Polia"], 1),
    ]
    # outra academia nao e tocada
    assert gym_rows(db_conn, catalog.other_gym_id) == []


def test_idempotente(db_conn, catalog):
    sync_gym_equipment_for_gym(db_conn, catalog.gym_id)
    first = gym_rows(db_conn, catalog.gym_id)
    sync_gym_equipment_for_gym(db_conn, catalog.gym_id)
    assert gym_rows(db_conn, catalog.gym_id) == first


def test_equipamento_removido_da_academia_desativa_sem_apagar(db_conn, catalog):
    sync_gym_equipment_for_gym(db_conn, catalog.gym_id)
    execute(
        db_conn,
        "UPDATE gym_equipment_catalog SET is_active = 0 WHERE gym_id = %s AND equipment_id = %s",
        (catalog.gym_id, catalog.equipment["Polia"]),
    )
    sync_gym_equipment_for_gym(db_conn, catalog.gym_id)
    ex, eq = catalog.exercises, catalog.equipment
    assert gym_rows(db_conn, catalog.gym_id) == [
        (ex["Supino reto"], eq["Banco reto"], 1),
        (ex["Crucifixo"], eq["Polia"], 0),
        (ex["Remada baixa"], eq["Polia"], 0),
    ]


def test_vinculo_equipamento_exercicio_inativo_desativa_e_reativa(db_conn, catalog):
    sync_gym_equipment_for_gym(db_conn, catalog.gym_id)
    ex, eq = catalog.exercises, catalog.equipment
    execute(
        db_conn,
        "UPDATE equipment_exercises SET is_active = 0 WHERE equipment_id = %s AND exercise_id = %s",
        (eq["Polia"], ex["Crucifixo"]),
    )
    sync_gym_equipment_for_gym(db_conn, catalog.gym_id)
    assert (ex["Crucifixo"], eq["Polia"], 0) in gym_rows(db_conn, catalog.gym_id)

    execute(
        db_conn,
        "UPDATE equipment_exercises SET is_active = 1 WHERE equipment_id = %s AND exercise_id = %s",
        (eq["Polia"], ex["Crucifixo"]),
    )
    sync_gym_equipment_for_gym(db_conn, catalog.gym_id)
    assert (ex["Crucifixo"], eq["Polia"], 1) in gym_rows(db_conn, catalog.gym_id)


def test_tabela_legada_equipment_exercise_mappings_e_ignorada(db_conn, catalog):
    # a fonte lida e SO equipment_exercises (canonica desde o PR-05);
    # equipment_exercise_mappings e apenas espelho legado.
    execute(
        db_conn,
        "INSERT INTO equipment_exercise_mappings (equipment_id, mapping_id) VALUES (%s, %s)",
        (catalog.equipment["Banco reto"], catalog.exercises["Crucifixo"]),
    )
    sync_gym_equipment_for_gym(db_conn, catalog.gym_id)
    assert (catalog.exercises["Crucifixo"], catalog.equipment["Banco reto"], 1) not in gym_rows(db_conn, catalog.gym_id)


def test_sync_ve_a_fk_canonica_de_gym_equipment_mapping_id(db_conn):
    # O schema compartilhado aplica TODAS as migracoes (M25), entao aqui
    # `gym_equipment.mapping_id` referencia `exercise_catalog` - o mesmo que a
    # Aurora de producao, depois da 023/028.
    #
    # O ramo LEGADO (FK ainda em `exercise_zepp_mappings`, exclusao com aviso e
    # espelho pulado) nao deixou de ser testado: ele vive em
    # `test_migration_023_mapping_fks.py::test_antes_da_023_exclui_do_sync_e_
    # pula_espelho_depois_materializa_e_espelha`, que monta o schema 001..021
    # proprio (`..._fk023_test`) - onde esse estado realmente existe - e confere
    # o aviso (`code`, `fk_referenced_table`, `excluded_exercise_ids`), o
    # espelho pulado e a materializacao dos demais exercicios; depois roda a
    # 023 e reconfere tudo no estado canonico. O mesmo cenario roda no caminho
    # de producao (Data API) em `dataapi/test_migration_023_dataapi.py`.
    with db_conn.cursor() as cur:
        assert (
            gym_equipment_sync.resolve_fk_referenced_table(cur, "gym_equipment", "mapping_id")
            == "exercise_catalog"
        )


def test_exercicio_so_no_catalogo_canonico_e_materializado_sem_aviso(db_conn, catalog):
    # Depois da 023/028 (formato de producao) "so no catalogo canonico" deixa de
    # ser um orfao: a FK de `gym_equipment.mapping_id` aponta justamente para
    # `exercise_catalog`, entao o exercicio entra no cruzamento normalmente e
    # NENHUM aviso e emitido.
    execute(db_conn, "INSERT INTO exercise_catalog (id, name) VALUES (2001, 'So no catalogo novo')")
    execute(
        db_conn,
        "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, 2001)",
        (catalog.equipment["Banco reto"],),
    )

    assert sync_gym_equipment_for_gym(db_conn, catalog.gym_id) == []

    ex, eq = catalog.exercises, catalog.equipment
    assert gym_rows(db_conn, catalog.gym_id) == [
        (ex["Supino reto"], eq["Banco reto"], 1),
        (ex["Crucifixo"], eq["Polia"], 1),
        (ex["Remada baixa"], eq["Polia"], 1),
        (2001, eq["Banco reto"], 1),
    ]


def test_sync_sem_orfaos_nao_gera_aviso(db_conn, catalog):
    assert sync_gym_equipment_for_gym(db_conn, catalog.gym_id) == []


def test_sync_por_equipamento_resincroniza_todas_as_academias_que_o_possuem(db_conn, catalog):
    ex, eq = catalog.exercises, catalog.equipment
    execute(
        db_conn,
        "INSERT INTO gym_equipment_catalog (gym_id, equipment_id) VALUES (%s, %s)",
        (catalog.other_gym_id, eq["Polia"]),
    )
    sync_gym_equipment_for_equipment(db_conn, eq["Polia"])
    assert gym_rows(db_conn, catalog.other_gym_id) == [
        (ex["Crucifixo"], eq["Polia"], 1),
        (ex["Remada baixa"], eq["Polia"], 1),
        # sincroniza a academia INTEIRA, nao so o equipamento alterado
        (ex["Agachamento"], eq["Rack"], 1),
    ]
    assert (ex["Supino reto"], eq["Banco reto"], 1) in gym_rows(db_conn, catalog.gym_id)
