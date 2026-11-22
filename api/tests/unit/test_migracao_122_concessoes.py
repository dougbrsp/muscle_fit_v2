"""Migracao 122 (#246): concessoes de `treino.academias.equipamentos` copiadas de
`treino.academias` (mesma acao, mesmo escopo), com banco falso que projeta colunas.

Rodar: cd api && python -m pytest tests/unit/test_migracao_122_concessoes.py
"""

from __future__ import annotations

import functools

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "122_access_grants_academias_equipamentos.py"
ORIGEM = "treino.academias"
NOVO = "treino.academias.equipamentos"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, tables=("access_groups", "access_group_grants"), grants=None):
        self.tables = set(tables)
        self.grants = dict(grants or {})  # (group_id, resource, action) -> scope
        self.executed: list[tuple[str, tuple | None]] = []

    def query(self, sql, params=None):
        s = " ".join(sql.split())
        if "information_schema.TABLES" in s:
            return [{"TABLE_NAME": t} for t in params if t in self.tables]
        if s.startswith("SELECT group_id, action, scope FROM access_group_grants WHERE resource = %s"):
            return [
                {"group_id": g, "action": a, "scope": sc}
                for (g, r, a), sc in self.grants.items()
                if r == params[0]
            ]
        raise AssertionError(f"leitura inesperada: {s}")

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        s = " ".join(sql.split())
        if s.startswith("INSERT IGNORE INTO access_group_grants"):
            group_id, resource, action, scope = params
            self.grants.setdefault((group_id, resource, action), scope)
        elif s == "DELETE FROM access_group_grants WHERE resource = %s":
            for k in [k for k in self.grants if k[1] == params[0]]:
                del self.grants[k]
        else:
            raise AssertionError(f"statement inesperado: {s[:80]}")


def _up(db):
    _mod()["up"](db)


def _grupos(editar="proprios"):
    return {(g, ORIGEM, a): editar for g in (2, 3) for a in ("ver", "editar", "excluir")}


def test_sem_as_tabelas_da_111_plano_vazio_e_sem_escrita():
    for tabelas in ((), ("access_groups",), ("access_group_grants",)):
        db = FakeDB(tables=tabelas, grants=_grupos())
        assert _mod()["plan"](db) == []
        _up(db)
        assert db.executed == []


def test_semeia_pelo_escopo_existente_e_plan_so_le():
    db = FakeDB(grants={**_grupos(), (7, ORIGEM, "editar"): "todos", (7, ORIGEM, "ver"): "todos"})
    plano = _mod()["plan"](db)
    assert db.executed == [] and len(plano) == 8
    _up(db)
    assert db.grants[(7, NOVO, "editar")] == "todos"  # grupo personalizado com `todos`
    assert db.grants[(2, NOVO, "editar")] == db.grants[(3, NOVO, "excluir")] == "proprios"
    assert plano == db.executed


def test_grupo_sem_treino_academias_nao_recebe():
    db = FakeDB(grants={**_grupos(), (4, "cardio", "editar"): "proprios", (5, ORIGEM, "ver"): "proprios"})
    _up(db)
    assert {g for (g, r, _) in db.grants if r == NOVO} == {2, 3, 5}
    assert (5, NOVO, "editar") not in db.grants


def test_nao_sobrescreve_concessao_existente_do_recurso_novo():
    db = FakeDB(grants={**_grupos(), (3, NOVO, "editar"): "todos"})
    plano = _mod()["plan"](db)
    assert all(p[:3] != (3, NOVO, "editar") for _, p in plano)
    _up(db)
    assert db.grants[(3, NOVO, "editar")] == "todos"


def test_idempotente():
    db = FakeDB(grants=_grupos())
    _up(db)
    n = len(db.executed)
    assert _mod()["plan"](db) == []
    _up(db)
    assert len(db.executed) == n


def test_down_remove_so_o_recurso_novo_e_e_idempotente():
    db = FakeDB(grants=_grupos())
    _up(db)
    _mod()["down"](db)
    assert db.grants == _grupos()
    db.executed.clear()
    _mod()["down"](db)
    assert db.executed == []
    sem = FakeDB(tables=())
    _mod()["down"](sem)
    assert sem.executed == []
