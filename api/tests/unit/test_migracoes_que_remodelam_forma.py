"""Migrações que remodelam uma forma que outra anterior confere por igualdade
(`tests/support/migrations.RESHAPING_MIGRATIONS`), sem MySQL.

Por que existe: a CI de integração do PR #556 falhou em
`test_migration_023_mapping_fks.py` e em `test_migrate_dataapi.py` com
"047: body_measurements.site e enum(...,'neck','forearm_l','forearm_r'),
esperado enum(...)". Esses testes apagam a tabela de controle e rodam o runner
de novo sobre um schema já convergido; a 047 reexecuta e recusa o ENUM ampliado
pela 075. Em produção a 047 nunca reexecuta (registrada; checksum conferido),
então a correção é só do apoio de testes: a 075 entra em `RESHAPING_MIGRATIONS`
e `undo_reshaping_migrations` (chamado por `fk023.reset_to_pre_023`, pelo
`_reset` de `test_migrate_dataapi.py` e pelo runner da 026) roda o `down` dela,
que volta o ENUM ao da 047 ou recusa se alguma linha usa os locais novos.
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from tests.support import fk023
from tests.support import migrations as support

M047 = m.DEFAULT_MIGRATIONS_DIR / "047_body_measurements.py"
M075 = m.DEFAULT_MIGRATIONS_DIR / "075_body_measurements_sites.py"


@functools.lru_cache(maxsize=None)
def _globals(path) -> dict:
    return m.load_python_migration(path).__globals__


class FakeConn:
    """Conexão crua mínima: `information_schema` de `body_measurements` (ENUM
    de `site`) e de `progression_tracks` (068) e os comandos recebidos."""

    def __init__(self, *, site_type=None, rows_in_new_sites=0, tracks=False):
        self.site_type = site_type  # None = tabela ausente
        self.rows_in_new_sites = rows_in_new_sites
        self.tracks = tracks
        self.executed: list[str] = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


class FakeCursor:
    def __init__(self, conn: FakeConn):
        self.conn = conn
        self._rows: list[dict] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        conn = self.conn
        if q.startswith("SELECT TABLE_NAME FROM information_schema.TABLES"):
            table = params[0]
            present = (table == "body_measurements" and conn.site_type is not None) or (
                table == "progression_tracks" and conn.tracks
            )
            self._rows = [{"TABLE_NAME": table}] if present else []
        elif "FROM information_schema.COLUMNS" in q and params[0] == "body_measurements":
            self._rows = [{"COLUMN_NAME": "site", "COLUMN_TYPE": conn.site_type, "IS_NULLABLE": "NO"}]
        elif q.startswith("SELECT COUNT(*) AS n FROM body_measurements"):
            self._rows = [{"n": conn.rows_in_new_sites}]
        elif q.startswith(("SELECT", "TRUNCATE", "SET")) or "information_schema" in q:
            self._rows = []
        else:
            conn.executed.append(q)
            self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_a_075_esta_registrada_porque_a_047_confere_o_enum_por_igualdade():
    g047, g075 = _globals(M047), _globals(M075)
    assert g047["EXPECTED_SITE_TYPE"] == g075["EXPECTED_TYPE_BEFORE"], "a 075 parte do ENUM da 047"
    assert g075["EXPECTED_TYPE_AFTER"] != g047["EXPECTED_SITE_TYPE"], "e o muda: a 047 recusaria ao reexecutar"
    assert M075.name in support.RESHAPING_MIGRATIONS
    for name in support.RESHAPING_MIGRATIONS:
        assert (support.MIGRATIONS_DIR / name).is_file(), name
        assert callable(m.load_python_migration(support.MIGRATIONS_DIR / name).__globals__.get("down")), name


def test_desfaz_o_enum_da_075_e_deixa_a_047_reexecutar_sem_erro():
    g047, g075 = _globals(M047), _globals(M075)
    conn = FakeConn(site_type=g075["EXPECTED_TYPE_AFTER"])
    statements = support.undo_reshaping_migrations(conn)
    assert statements == [" ".join(g075["REVERT_SQL"].split())]
    assert conn.executed == statements and conn.commits == 1
    # Depois do `down`, o schema está na forma que a 047 aceita.
    assert g075["REVERT_SQL"].endswith(f"site ENUM({g047['_ENUM_VALUES_SQL']}) NOT NULL")


@pytest.mark.parametrize("site_type", [None, "047"], ids=["sem_tabela", "ja_no_enum_da_047"])
def test_sem_tabela_ou_ja_na_forma_da_047_nao_faz_nada(site_type):
    g075 = _globals(M075)
    conn = FakeConn(site_type=None if site_type is None else g075["EXPECTED_TYPE_BEFORE"])
    assert support.undo_reshaping_migrations(conn) == []
    assert conn.executed == []


def test_linha_com_local_novo_falha_com_erro_claro_sem_alterar_nada():
    g075 = _globals(M075)
    conn = FakeConn(site_type=g075["EXPECTED_TYPE_AFTER"], rows_in_new_sites=2)
    with pytest.raises(Exception, match=r"075: 2 linha\(s\) usam neck, forearm_l, forearm_r") as exc:
        support.undo_reshaping_migrations(conn)
    assert type(exc.value).__name__ == "BodyMeasurementSitesError"
    assert conn.executed == []


def test_reset_to_pre_023_desfaz_as_remodelagens_antes_de_apagar_o_controle(monkeypatch):
    ordem: list[str] = []
    monkeypatch.setattr(support, "undo_reshaping_migrations", lambda conn: ordem.append("undo") or [])
    monkeypatch.setattr(fk023, "truncate_seed_tables", lambda conn: ordem.append("truncate"))
    monkeypatch.setattr(fk023, "restore_legacy_mapping_fks", lambda conn: ordem.append("fks"))
    conn = FakeConn()
    fk023.reset_to_pre_023(conn, (m.CONTROL_TABLE, m.LOCK_TABLE))
    assert ordem == ["undo", "truncate", "fks"]
    assert conn.executed == [f"DROP TABLE IF EXISTS `{m.CONTROL_TABLE}`", f"DROP TABLE IF EXISTS `{m.LOCK_TABLE}`"]


def test_reset_to_pre_023_desfaz_a_075_de_verdade():
    g075 = _globals(M075)
    conn = FakeConn(site_type=g075["EXPECTED_TYPE_AFTER"])
    fk023.reset_to_pre_023(conn, (m.CONTROL_TABLE,))
    assert conn.executed[0] == " ".join(g075["REVERT_SQL"].split())
    assert f"DROP TABLE IF EXISTS `{m.CONTROL_TABLE}`" in conn.executed
