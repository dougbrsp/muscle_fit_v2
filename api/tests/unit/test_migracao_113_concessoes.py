"""Migracao 113 (RBAC lote 2B): concessoes de `conta.dispositivos` com banco falso.

Banco falso com estado que PROJETA colunas (devolve so as colunas que o SELECT
pede). Cobre: sem tabelas (plano vazio), grupo de sistema ausente (erro sem
escrita), primeira execucao, segunda execucao (zero escrita), grupo nao-sistema
com a mesma chave intacto, concessao parcial (so insere o que falta), `down`.

Rodar: cd api && python -m pytest tests/unit/test_migracao_113_concessoes.py
"""

from __future__ import annotations

import functools
import re

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "113_access_grants_conta_dispositivos.py"
RECURSO = "conta.dispositivos"
ACOES = ("ver", "editar", "excluir")


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, tables=("access_groups", "access_group_grants"), groups=None, grants=None):
        self.tables = set(tables)
        # id -> (group_key, is_system)
        self.groups = dict(
            groups
            if groups is not None
            else {1: ("admin", 1), 2: ("personal_trainer", 1), 3: ("usuario", 1), 4: ("free", 0)}
        )
        # (group_id, resource, action) -> scope
        self.grants = dict(grants or {})
        self.executed: list[tuple[str, tuple | None]] = []

    def query(self, sql, params=None):
        s = " ".join(sql.split())
        if "information_schema.TABLES" in s:
            return [{"TABLE_NAME": t} for t in params if t in self.tables]
        if s.startswith("SELECT id, group_key FROM access_groups WHERE is_system = 1"):
            return [{"id": i, "group_key": k} for i, (k, sis) in self.groups.items() if sis == 1]
        if s.startswith("SELECT group_id, action FROM access_group_grants WHERE resource = %s"):
            return [
                {"group_id": g, "action": a} for (g, r, a) in self.grants if r == params[0]
            ]
        raise AssertionError(f"leitura inesperada: {s}")

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        s = " ".join(sql.split())
        if s.startswith("INSERT IGNORE INTO access_group_grants"):
            group_id, resource, action, scope = params
            assert group_id in self.groups
            self.grants.setdefault((group_id, resource, action), scope)
        elif s.startswith("DELETE FROM access_group_grants WHERE group_id = %s AND resource = %s"):
            group_id, resource = params
            for chave in [k for k in self.grants if k[0] == group_id and k[1] == resource]:
                del self.grants[chave]
        else:
            raise AssertionError(f"statement inesperado: {s[:80]}")


def _up(db):
    _mod()["up"](db)


def test_sem_as_tabelas_da_111_plano_vazio_e_sem_escrita(caplog):
    for tabelas in ((), ("access_groups",), ("access_group_grants",)):
        db = FakeDB(tables=tabelas)
        with caplog.at_level("INFO"):
            assert _mod()["plan"](db) == []
            _up(db)
        assert db.executed == []
    assert any("113" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("ausente", ["usuario", "personal_trainer"])
def test_grupo_de_sistema_ausente_levanta_erro_proprio_sem_escrita(ausente):
    grupos = {i: (k, s) for i, (k, s) in FakeDB().groups.items() if k != ausente}
    db = FakeDB(groups=grupos)
    with pytest.raises(_mod()["AccessGrantsContaDispositivosError"]):
        _up(db)
    assert db.executed == [] and db.grants == {}


def test_grupo_com_a_chave_mas_fora_do_sistema_conta_como_ausente():
    db = FakeDB(groups={1: ("usuario", 0), 2: ("personal_trainer", 1), 4: ("free", 0)})
    with pytest.raises(_mod()["AccessGrantsContaDispositivosError"]):
        _up(db)
    assert db.executed == []


def test_primeira_execucao_insere_seis_linhas_nos_dois_grupos_de_sistema():
    db = FakeDB()
    plano = _mod()["plan"](db)
    assert len(plano) == 6
    assert db.executed == [], "plan so le"
    _up(db)
    esperado = {(g, RECURSO, a): "proprios" for g in (2, 3) for a in ACOES}
    assert db.grants == esperado
    sqls = {" ".join(sql.split()) for sql, _ in db.executed}
    assert sqls == {
        "INSERT IGNORE INTO access_group_grants (group_id, resource, action, scope) VALUES (%s, %s, %s, %s)"
    }
    assert plano == db.executed


def test_segunda_execucao_nao_emite_escrita():
    db = FakeDB()
    _up(db)
    n = len(db.executed)
    assert _mod()["plan"](db) == []
    _up(db)
    assert len(db.executed) == n


def test_grupo_nao_sistema_com_a_mesma_chave_e_free_ficam_intactos():
    grupos = {1: ("admin", 1), 2: ("personal_trainer", 1), 3: ("usuario", 1), 4: ("free", 0), 5: ("usuario", 0)}
    db = FakeDB(groups=grupos)
    _up(db)
    assert {g for (g, _, _) in db.grants} == {2, 3}


def test_concessao_parcial_so_insere_o_que_falta_e_nao_sobrescreve_escopo_editado():
    db = FakeDB(grants={(3, RECURSO, "ver"): "todos", (2, RECURSO, "ver"): "proprios", (2, RECURSO, "editar"): "proprios"})
    plano = _mod()["plan"](db)
    assert sorted(p for _, p in plano) == sorted(
        [(3, RECURSO, "editar", "proprios"), (3, RECURSO, "excluir", "proprios"), (2, RECURSO, "excluir", "proprios")]
    )
    _up(db)
    assert db.grants[(3, RECURSO, "ver")] == "todos"
    assert len(db.grants) == 6


def test_outras_concessoes_nao_sao_tocadas():
    outras = {(3, "cardio", "ver"): "proprios", (1, "cardio", "ver"): "todos", (4, RECURSO, "ver"): "proprios"}
    db = FakeDB(grants=outras)
    _up(db)
    for chave, escopo in outras.items():
        assert db.grants[chave] == escopo
    assert (1, RECURSO, "ver") not in db.grants


def test_so_dml_e_um_statement_por_chamada_sem_fk_nem_set():
    db = FakeDB()
    _up(db)
    for sql, _ in db.executed:
        assert ";" not in sql and "exercise_catalog" not in sql
        assert not re.search(r"\b(CREATE|ALTER|DROP|PREPARE|SET|UPDATE)\b", sql)


def test_down_remove_so_as_concessoes_do_recurso_dos_dois_grupos_e_e_idempotente():
    db = FakeDB(grants={(3, "cardio", "ver"): "proprios"})
    _up(db)
    db.executed.clear()
    _mod()["down"](db)
    assert db.grants == {(3, "cardio", "ver"): "proprios"}
    db.executed.clear()
    _mod()["down"](db)
    assert db.executed == []


def test_down_sem_tabelas_e_no_op():
    db = FakeDB(tables=())
    _mod()["down"](db)
    assert db.executed == []
