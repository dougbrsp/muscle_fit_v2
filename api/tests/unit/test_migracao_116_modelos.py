"""Migracao 116 (RBAC lote 6A): visibilidade dos Modelos e escopo `alunos` do PT, banco falso.

Cobre: coluna ausente/presente (e forma inesperada), escopo em todos/alunos/editado,
idempotencia, dry-run, grupo ausente, releitura e `down`.

Rodar: cd api && python -m pytest tests/unit/test_migracao_116_modelos.py
"""

from __future__ import annotations

import functools
import re

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "116_modelos_visibilidade_e_escopo_pt.py"
ACOES = ("ver", "editar", "excluir")
PT = 2
OUTROS = ("modelos.metas_ativacao", "modelos.tipos_treino", "ia.modelo_periodizacao")
COL_OK = {"COLUMN_TYPE": "enum('publico','privado')", "IS_NULLABLE": "NO", "COLUMN_DEFAULT": "publico"}


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _erro():
    return _mod()["ModelosVisibilidadeError"]


def _estado_inicial() -> dict:
    g = {(PT, "modelos.treino", a): "todos" for a in ACOES}
    for r in OUTROS:
        for a in ACOES:
            g[(PT, r, a)] = "todos"
    for a in ACOES:
        g[(3, "modelos.treino", a)] = "proprios"
    return g


class FakeDB:
    def __init__(
        self,
        tables=("workout_plan_templates", "access_groups", "access_group_grants"),
        column=None,
        groups=None,
        grants=None,
        dry_run=False,
        ignora_update=False,
    ):
        self.tables = set(tables)
        self.column = column
        self.groups = dict(
            groups
            if groups is not None
            else {1: ("admin", 1), 2: ("personal_trainer", 1), 3: ("usuario", 1), 4: ("free", 0)}
        )
        self.grants = _estado_inicial() if grants is None else dict(grants)
        self.executed: list[tuple[str, tuple | None]] = []
        self.dry_run = dry_run
        self.ignora_update = ignora_update

    def query(self, sql, params=None):
        s = " ".join(sql.split())
        if "information_schema.TABLES" in s:
            return [{"TABLE_NAME": t} for t in params if t in self.tables]
        if "information_schema.COLUMNS" in s:
            assert params == ("workout_plan_templates", "visibility")
            return [self.column] if self.column else []
        if s.startswith("SELECT id, group_key FROM access_groups WHERE is_system = 1"):
            return [{"id": i, "group_key": k} for i, (k, sis) in self.groups.items() if sis == 1]
        if s.startswith("SELECT resource, action FROM access_group_grants WHERE group_id = %s AND scope = %s"):
            gid, scope, *recursos = params
            return [
                {"resource": r, "action": a}
                for (g, r, a), sc in self.grants.items()
                if g == gid and sc == scope and r in recursos
            ]
        raise AssertionError(f"leitura inesperada: {s}")

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.dry_run:
            return 0
        s = " ".join(sql.split())
        if s.startswith("ALTER TABLE workout_plan_templates ADD COLUMN visibility"):
            self.column = dict(COL_OK)
        elif s == "ALTER TABLE workout_plan_templates DROP COLUMN visibility":
            self.column = None
        elif s.startswith("UPDATE access_group_grants SET scope = %s WHERE group_id = %s AND resource IN"):
            novo, gid, *resto = params
            antigo, recursos = resto[-1], resto[:-1]
            if self.ignora_update:
                return 0
            for chave in list(self.grants):
                if chave[0] == gid and chave[1] in recursos and self.grants[chave] == antigo:
                    self.grants[chave] = novo
        else:
            raise AssertionError(f"statement inesperado: {s[:80]}")


def _up(db):
    _mod()["up"](db)


def _alunos(db):
    return {k for k, v in db.grants.items() if v == "alunos"}


def test_coluna_ausente_emite_alter_instant_e_depois_o_update():
    db = FakeDB()
    plano = _mod()["plan"](db)
    assert db.executed == [], "plan so le"
    assert len(plano) == 2
    assert " ".join(plano[0][0].split()) == (
        "ALTER TABLE workout_plan_templates ADD COLUMN visibility ENUM('publico','privado') "
        "NOT NULL DEFAULT 'publico', ALGORITHM=INSTANT"
    )
    assert plano[0][1] is None
    assert plano[1][1] == ("alunos", PT, "modelos.treino", "todos")
    _up(db)
    assert db.executed == plano and db.column == COL_OK
    assert _alunos(db) == {(PT, "modelos.treino", a) for a in ACOES}


def test_coluna_presente_emite_so_o_update():
    db = FakeDB(column=dict(COL_OK))
    plano = _mod()["plan"](db)
    assert len(plano) == 1 and plano[0][0].startswith("UPDATE access_group_grants")


def test_coluna_com_forma_inesperada_levanta_erro_sem_escrita():
    for ruim in (
        {**COL_OK, "COLUMN_TYPE": "varchar(10)"},
        {**COL_OK, "IS_NULLABLE": "YES"},
        {**COL_OK, "COLUMN_DEFAULT": "privado"},
    ):
        db = FakeDB(column=ruim)
        with pytest.raises(_erro()):
            _up(db)
        assert db.executed == []


def test_so_modelos_treino_do_pt_muda_demais_recursos_e_usuario_intactos():
    antes = _estado_inicial()
    db = FakeDB()
    _up(db)
    for chave, escopo in antes.items():
        if chave[0] == PT and chave[1] == "modelos.treino":
            assert db.grants[chave] == "alunos"
        else:
            assert db.grants[chave] == escopo, chave


def test_escopo_editado_pelo_admin_nao_e_tocado():
    grants = _estado_inicial()
    grants[(PT, "modelos.treino", "ver")] = "proprios"
    grants[(PT, "modelos.treino", "editar")] = "alunos"
    db = FakeDB(grants=grants)
    _up(db)
    assert db.grants[(PT, "modelos.treino", "ver")] == "proprios"
    assert db.grants[(PT, "modelos.treino", "editar")] == "alunos"
    assert db.grants[(PT, "modelos.treino", "excluir")] == "alunos"


def test_segunda_execucao_nao_emite_escrita_e_estado_convergido_gera_plano_vazio():
    db = FakeDB()
    _up(db)
    n = len(db.executed)
    assert _mod()["plan"](db) == []
    _up(db)
    assert len(db.executed) == n
    convergido = {k: ("alunos" if k[:2] == (PT, "modelos.treino") else v) for k, v in _estado_inicial().items()}
    assert _mod()["plan"](FakeDB(column=dict(COL_OK), grants=convergido)) == []
    assert _mod()["plan"](FakeDB(column=dict(COL_OK), grants={})) == []


def test_sem_tabelas_da_111_so_ddl_e_log(caplog):
    db = FakeDB(tables=("workout_plan_templates",))
    with caplog.at_level("INFO"):
        plano = _mod()["plan"](db)
    assert [s for s, _ in plano] == [_mod()["ADD_COLUMN_SQL"]]
    assert any("116" in r.getMessage() for r in caplog.records)
    _up(db)
    assert len(db.executed) == 1


def test_tabela_de_modelos_ausente_erro_em_execucao_real_e_ddl_no_dry_run():
    with pytest.raises(_erro()):
        _up(FakeDB(tables=("access_groups", "access_group_grants")))
    db = FakeDB(tables=(), dry_run=True)
    assert [s for s, _ in _mod()["plan"](db)] == [_mod()["ADD_COLUMN_SQL"]]


def test_grupo_de_sistema_personal_trainer_ausente_ou_nao_sistema_levanta_erro_sem_escrita():
    for grupos in (
        {1: ("admin", 1), 3: ("usuario", 1), 4: ("free", 0)},
        {1: ("usuario", 1), 2: ("personal_trainer", 0), 4: ("free", 0)},
    ):
        db = FakeDB(groups=grupos)
        antes = dict(db.grants)
        with pytest.raises(_erro()):
            _up(db)
        assert db.executed == [] and db.grants == antes and db.column is None


def test_grupo_nao_sistema_com_a_mesma_chave_fica_intacto():
    grupos = {1: ("admin", 1), 2: ("personal_trainer", 1), 3: ("usuario", 1), 4: ("free", 0), 5: ("personal_trainer", 0)}
    grants = _estado_inicial()
    grants[(5, "modelos.treino", "ver")] = "todos"
    db = FakeDB(groups=grupos, grants=grants)
    _up(db)
    assert db.grants[(5, "modelos.treino", "ver")] == "todos"
    assert db.grants[(PT, "modelos.treino", "ver")] == "alunos"


def test_releitura_ao_fim_de_up_levanta_erro_se_o_update_nao_gravou():
    db = FakeDB(ignora_update=True)
    with pytest.raises(_erro()):
        _up(db)
    assert len(db.executed) == 2


def test_dry_run_nao_dispara_a_releitura_nem_escreve():
    db = FakeDB(dry_run=True)
    _up(db)
    assert len(db.executed) == 2 and _alunos(db) == set() and db.column is None


def test_um_statement_por_chamada_sem_fk_nem_set_e_retrato_sem_importar_app():
    db = FakeDB()
    _up(db)
    for sql, _ in db.executed:
        assert ";" not in sql and "FOREIGN KEY" not in sql and "REFERENCES" not in sql
        assert not re.search(r"\b(PREPARE|SET @|INSERT|DELETE)\b", sql)
    fonte = MIGRATION.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+app", fonte, re.MULTILINE)
    assert _mod()["GRANTS_BY_GROUP"] == {
        "personal_trainer": tuple(("modelos.treino", a, "alunos") for a in ACOES)
    }


def test_down_volta_para_todos_remove_a_coluna_e_e_idempotente():
    db = FakeDB()
    _up(db)
    db.executed.clear()
    _mod()["down"](db)
    assert db.grants == _estado_inicial() and db.column is None
    db.executed.clear()
    _mod()["down"](db)
    assert db.executed == []


def test_down_sem_tabelas_e_no_op():
    db = FakeDB(tables=())
    _mod()["down"](db)
    assert db.executed == []
