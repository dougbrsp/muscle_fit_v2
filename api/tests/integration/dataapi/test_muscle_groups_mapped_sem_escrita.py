"""Leitura SEM escrita nas rotas canonicas de grupos musculares
(`/analytics/muscle-groups-mapped-{trend,table,health,debug}` e
`/mapping-canonical/unmapped`), nos dois backends (PyMySQL e shim RDS Data
API + dublê).

Antes, cada GET rodava `CREATE INDEX` (DDL, commit implicito) e
`UPDATE exercises SET mapping_id=...` para todo exercicio sem vinculo. Agora:

(a) nenhum GET emite INSERT/UPDATE/DELETE/CREATE/ALTER... - capturado no
    cursor PyMySQL e nas chamadas `ExecuteStatement` do dublê;
(b) os numeros sao os mesmos do comportamento anterior (oraculo: reclassificar
    na leitura, como antes, e ler) - inclusive quando um vinculo muda depois
    da ultima leitura: a reclassificacao acontece na escrita do catalogo;
(c) a migracao 024 nao emite DDL com o indice existente e faz o backfill uma
    unica vez (reexecucao: 0 statements).
"""

from __future__ import annotations

import pytest

from app import exercise_catalog_admin
from app import exercise_mapping_canonical as emc
from app import migrate as m
from app import muscle_groups_mapped as mgm
from app.security import Identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.muscle_groups_mapped import (
    SUPINO_ID,
    UNMAPPED_NAME,
    expected_summary,
    seed_canonical_history,
    table_summary,
)
from tests.support.sql_capture import recorded_sql
from tests.support.sql_capture import writes as _writes

MIGRATION_024 = m.DEFAULT_MIGRATIONS_DIR / "024_exercises_mapping_id_leitura_sem_escrita.py"


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="regular", source="session")


GET_ROUTES = {
    "trend": lambda ident: mgm.trend(
        period="weekly", metric="volume", days=180, include_secondary=True, identity=ident
    ),
    "table": lambda ident: mgm.table(
        period="yearly", metric="volume", days=30, include_secondary=False, identity=ident
    ),
    "health": lambda ident: mgm.health(days=180, identity=ident),
    "debug": lambda ident: mgm.debug(days=180, identity=ident),
    "mapping-canonical-unmapped": lambda ident: emc.unmapped(limit=100, group_by=None, offset=0),
    "mapping-canonical-unmapped-by-name": lambda ident: emc.unmapped(limit=100, group_by="name", offset=0),
}


def _mapping_ids(raw) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute("SELECT id, mapping_id FROM exercises ORDER BY id")
        return list(cur.fetchall())


def _unmapped_names(payload: dict) -> list[str]:
    return [item["exercise"] for item in payload["unmapped"]]


def _dashboard(user_id: int) -> dict:
    return mgm.table(
        period="yearly", metric="volume", days=30, include_secondary=False, identity=_identity(user_id)
    )


def _legacy_read(read):
    """Comportamento ANTES desta mudanca: a leitura reclassificava os
    exercicios sem vinculo e so entao agregava."""
    emc.reclassify_exercises(limit=20000, only_missing=True)
    return read()


@pytest.fixture
def history(raw):
    return seed_canonical_history(raw)


# -- (a) GET sem DDL/DML ------------------------------------------------------


def test_instrumentacao_captura_escrita(history, db_backend, monkeypatch, fake_rds):
    """Garante que a captura abaixo nao e vazia por construcao."""
    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        emc.execute("UPDATE exercises SET notes = notes WHERE id = %s", (0,))
    assert len(_writes(statements)) == 1, statements


@pytest.mark.parametrize("route", sorted(GET_ROUTES))
def test_get_nao_emite_ddl_nem_dml(history, raw, db_backend, monkeypatch, fake_rds, route):
    # O seed tem um exercicio sem vinculo e sem correspondencia
    # (UNMAPPED_NAME): antes, todo GET gravava UPDATE ... SET mapping_id=NULL
    # nele, alem do CREATE INDEX.
    before = _mapping_ids(raw)
    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        GET_ROUTES[route](_identity(history["user"]))
    assert statements, "nenhum statement capturado - instrumentacao quebrada"
    assert _writes(statements) == []
    assert _mapping_ids(raw) == before


# -- (b) mesmos numeros de antes -----------------------------------------------


@pytest.mark.parametrize("include_secondary", [False, True], ids=["so-primario", "com-secundario"])
@pytest.mark.parametrize("days", [30, 90])
def test_mesmos_numeros_do_comportamento_anterior(history, monkeypatch, fake_rds, mysql_schema, days, include_secondary):
    results = {}
    for backend in BACKENDS:  # pymysql primeiro; data-api troca ate o teardown
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)

        def read():
            return mgm.aggregate("weekly", "volume", days, include_secondary, user_id=history["user"])

        now = read()
        assert now == _legacy_read(read)
        results[backend] = now
    assert results["data-api"] == results["pymysql"]
    assert table_summary(results["pymysql"]) == expected_summary(days, include_secondary)


def test_vinculo_que_muda_depois_da_ultima_leitura(raw, monkeypatch, fake_rds, mysql_schema):
    """Um nome sem correspondencia ganha um alias no catalogo depois de ja ter
    sido lido. Antes: o proximo GET reclassificava. Agora: a rota do catalogo
    reclassifica na escrita. O Dashboard mostra o mesmo numero nos dois casos."""
    monkeypatch.setattr(exercise_catalog_admin, "_require_admin_identity", lambda *args, **kwargs: None)
    results = {}
    for backend in BACKENDS:
        # ANTES (oraculo): alias gravado direto no banco + reclassificacao na leitura.
        history = seed_canonical_history(raw)
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        assert UNMAPPED_NAME in _unmapped_names(_dashboard(history["user"]))
        with raw.cursor() as cur:
            cur.execute(
                "INSERT INTO exercise_aliases (exercise_id, alias, source) VALUES (%s, %s, 'teste')",
                (SUPINO_ID, UNMAPPED_NAME),
            )
        # A leitura nova nao escreve: sem passar pela escrita do catalogo, nada muda.
        assert UNMAPPED_NAME in _unmapped_names(_dashboard(history["user"]))
        legacy = _legacy_read(lambda: _dashboard(history["user"]))

        # DEPOIS: mesmo alias pela rota admin do catalogo (reclassifica na escrita).
        history = seed_canonical_history(raw)
        assert UNMAPPED_NAME in _unmapped_names(_dashboard(history["user"]))
        exercise_catalog_admin.create_exercise_alias(
            SUPINO_ID, item={"alias": UNMAPPED_NAME}, x_api_token=None, musclefit_session=None
        )
        with recorded_sql(backend, monkeypatch, fake_rds) as statements:
            now = _dashboard(history["user"])
        assert _writes(statements) == []

        assert now == legacy
        assert UNMAPPED_NAME not in _unmapped_names(now)
        peitoral = next(row for row in now["table_rows"] if row["group"] == "Peitoral")
        assert peitoral["sets"] == 5  # 3 do supino + 2 da serie antes sem vinculo
        results[backend] = now
    assert results["data-api"] == results["pymysql"]


# -- (c) migracao 024 ----------------------------------------------------------


def test_migracao_024_sem_ddl_e_backfill_unico(raw, monkeypatch, fake_rds, mysql_schema):
    up = m.load_python_migration(MIGRATION_024)
    results = {}
    for backend in BACKENDS:
        history = seed_canonical_history(raw)
        # Dado legado inconsistente: exercicios que casam pelo nome, sem vinculo.
        with raw.cursor() as cur:
            cur.execute("UPDATE exercises SET mapping_id = NULL WHERE original_name = 'Supino reto'")
            legacy_rows = cur.rowcount
        assert legacy_rows == 4
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        import app.db as db  # atributo trocado por use_backend

        conn = db.get_conn()
        try:
            dry = m.MigrationDB(conn, dry_run=True)
            up(dry)
            # Indice ja existe (baseline): nenhum ALTER; so os UPDATEs do backfill.
            assert dry.statements == ["UPDATE `exercises` SET mapping_id=%s WHERE id=%s"] * legacy_rows

            with recorded_sql(backend, monkeypatch, fake_rds) as statements:
                real = m.MigrationDB(conn)
                up(real)
                conn.commit()
            assert len(real.statements) == legacy_rows
            writes = _writes(statements)
            assert len(writes) == legacy_rows and all(w.upper().startswith("UPDATE") for w in writes), writes

            again = m.MigrationDB(conn)
            up(again)
            conn.commit()
            assert again.statements == []
        finally:
            conn.close()

        result = mgm.aggregate("weekly", "volume", 30, False, user_id=history["user"])
        assert table_summary(result) == expected_summary(30, False)
        results[backend] = result
    assert results["data-api"] == results["pymysql"]
