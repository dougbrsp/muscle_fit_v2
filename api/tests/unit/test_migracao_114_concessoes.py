"""Migracao 114 (RBAC lote 2C-1): concessoes de `modelos.tipos_treino` com banco falso.

Banco falso com estado que PROJETA colunas (devolve so as colunas que o SELECT
pede). Cobre: sem tabelas (plano vazio), grupo de sistema ausente (erro sem
escrita), primeira execucao (3 linhas so no PT, escopo todos), segunda execucao
(zero escrita), grupo nao-sistema com a mesma chave intacto, concessao existente
(nao sobrescrita), releitura ao fim de `up`, dry-run e `down`.

Rodar: cd api && python -m pytest tests/unit/test_migracao_114_concessoes.py
"""

from __future__ import annotations

import functools
import re

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "114_access_grants_modelos_tipos_treino.py"
RECURSO = "modelos.tipos_treino"
ACOES = ("ver", "editar", "excluir")
PT = 2


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _erro():
    return _mod()["AccessGrantsModelosTiposTreinoError"]


class FakeDB:
    def __init__(
        self,
        tables=("access_groups", "access_group_grants"),
        groups=None,
        grants=None,
        engole_insert=False,
        dry_run=False,
    ):
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
        self.engole_insert = engole_insert  # simula INSERT IGNORE que nao grava
        self.dry_run = dry_run

    def query(self, sql, params=None):
        s = " ".join(sql.split())
        if "information_schema.TABLES" in s:
            return [{"TABLE_NAME": t} for t in params if t in self.tables]
        if s.startswith("SELECT id, group_key FROM access_groups WHERE is_system = 1"):
            return [{"id": i, "group_key": k} for i, (k, sis) in self.groups.items() if sis == 1]
        if s.startswith("SELECT group_id, action FROM access_group_grants WHERE resource = %s"):
            return [{"group_id": g, "action": a} for (g, r, a) in self.grants if r == params[0]]
        raise AssertionError(f"leitura inesperada: {s}")

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.dry_run:
            return 0
        s = " ".join(sql.split())
        if s.startswith("INSERT IGNORE INTO access_group_grants"):
            group_id, resource, action, scope = params
            assert group_id in self.groups
            if not self.engole_insert:
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
    assert any("114" in r.getMessage() for r in caplog.records)


def test_grupo_de_sistema_personal_trainer_ausente_levanta_erro_proprio_sem_escrita():
    grupos = {i: (k, s) for i, (k, s) in FakeDB().groups.items() if k != "personal_trainer"}
    db = FakeDB(groups=grupos)
    with pytest.raises(_erro()):
        _up(db)
    assert db.executed == [] and db.grants == {}


def test_grupo_com_a_chave_mas_fora_do_sistema_conta_como_ausente():
    db = FakeDB(groups={1: ("usuario", 1), 2: ("personal_trainer", 0), 4: ("free", 0)})
    with pytest.raises(_erro()):
        _up(db)
    assert db.executed == []


def test_primeira_execucao_insere_tres_linhas_so_no_personal_trainer_com_escopo_todos():
    db = FakeDB()
    plano = _mod()["plan"](db)
    assert len(plano) == 3
    assert db.executed == [], "plan so le"
    _up(db)
    assert db.grants == {(PT, RECURSO, a): "todos" for a in ACOES}
    sqls = {" ".join(sql.split()) for sql, _ in db.executed}
    assert sqls == {
        "INSERT IGNORE INTO access_group_grants (group_id, resource, action, scope) VALUES (%s, %s, %s, %s)"
    }
    assert plano == db.executed


def test_usuario_admin_e_free_nao_recebem_nada():
    db = FakeDB()
    _up(db)
    assert {g for (g, _, _) in db.grants} == {PT}


def test_segunda_execucao_nao_emite_escrita():
    db = FakeDB()
    _up(db)
    n = len(db.executed)
    assert _mod()["plan"](db) == []
    _up(db)
    assert len(db.executed) == n


def test_grupo_nao_sistema_com_a_mesma_chave_fica_intacto():
    grupos = {1: ("admin", 1), 2: ("personal_trainer", 1), 3: ("usuario", 1), 4: ("free", 0), 5: ("personal_trainer", 0)}
    db = FakeDB(groups=grupos)
    _up(db)
    assert {g for (g, _, _) in db.grants} == {PT}


def test_concessao_existente_nao_e_tocada_mesmo_com_escopo_editado():
    db = FakeDB(grants={(PT, RECURSO, "ver"): "alunos", (PT, RECURSO, "editar"): "proprios"})
    plano = _mod()["plan"](db)
    assert [p for _, p in plano] == [(PT, RECURSO, "excluir", "todos")]
    _up(db)
    assert db.grants[(PT, RECURSO, "ver")] == "alunos"
    assert db.grants[(PT, RECURSO, "editar")] == "proprios"
    assert db.grants[(PT, RECURSO, "excluir")] == "todos"


def test_outras_concessoes_nao_sao_tocadas():
    outras = {(3, "modelos.treino", "ver"): "proprios", (1, RECURSO, "ver"): "todos", (4, RECURSO, "ver"): "proprios"}
    db = FakeDB(grants=outras)
    _up(db)
    for chave, escopo in outras.items():
        assert db.grants[chave] == escopo


def test_releitura_ao_fim_de_up_levanta_erro_se_o_insert_ignore_nao_gravou():
    db = FakeDB(engole_insert=True)
    with pytest.raises(_erro()):
        _up(db)
    assert len(db.executed) == 3, "os inserts foram tentados antes da releitura"


def test_releitura_detecta_gravacao_parcial():
    class ParcialDB(FakeDB):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if params and params[2] == "excluir":
                self.grants.pop((params[0], params[1], params[2]), None)

    db = ParcialDB()
    with pytest.raises(_erro()) as exc:
        _up(db)
    assert "excluir" in str(exc.value)


def test_dry_run_nao_dispara_a_releitura():
    db = FakeDB(dry_run=True)
    _up(db)
    assert len(db.executed) == 3 and db.grants == {}


def test_so_dml_e_um_statement_por_chamada_sem_fk_nem_set():
    db = FakeDB()
    _up(db)
    for sql, _ in db.executed:
        assert ";" not in sql and "exercise_catalog" not in sql
        assert not re.search(r"\b(CREATE|ALTER|DROP|PREPARE|SET|UPDATE)\b", sql)


def test_retrato_congelado_proprio_sem_importar_app_access():
    fonte = MIGRATION.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+app", fonte, re.MULTILINE)
    assert _mod()["GRANTS_BY_GROUP"] == {
        "personal_trainer": tuple((RECURSO, a, "todos") for a in ACOES)
    }


def test_down_remove_so_o_recurso_do_personal_trainer_e_e_idempotente():
    outras = {(3, "modelos.treino", "ver"): "proprios", (PT, "modelos.treino", "ver"): "todos", (4, RECURSO, "ver"): "proprios"}
    db = FakeDB(grants=outras)
    _up(db)
    db.executed.clear()
    _mod()["down"](db)
    assert db.grants == outras
    db.executed.clear()
    _mod()["down"](db)
    assert db.executed == []


def test_down_sem_tabelas_e_no_op():
    db = FakeDB(tables=())
    _mod()["down"](db)
    assert db.executed == []
