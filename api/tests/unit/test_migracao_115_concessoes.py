"""Migracao 115 (RBAC lote 5A): nutricao de terceiros do PT de `todos` para `alunos`, banco falso.

Banco falso que PROJETA colunas. Cobre: sem tabelas (plano vazio), grupo de sistema
ausente (erro sem escrita), primeira execucao (12 linhas do PT viram alunos), segunda
execucao (zero escrita), escopo editado pelo admin intocado, outros recursos/grupos
intactos (inclusive grupo nao-sistema com a mesma chave), releitura, dry-run e `down`.

Rodar: cd api && python -m pytest tests/unit/test_migracao_115_concessoes.py
"""

from __future__ import annotations

import functools
import re

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "115_access_grants_pt_nutricao_alunos.py"
RECURSOS = ("nutricao.diario", "nutricao.plano", "nutricao.checkin", "ia.nutricao")
ACOES = ("ver", "editar", "excluir")
PT = 2


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _erro():
    return _mod()["AccessGrantsPtNutricaoAlunosError"]


def _estado_inicial() -> dict:
    """PT como na matriz 111: nutricao de terceiros em todos; modelos em todos; resto proprios."""
    g = {(PT, r, a): "todos" for r in RECURSOS for a in ACOES}
    for r in ("modelos.treino", "modelos.metas_ativacao", "modelos.tipos_treino", "ia.modelo_periodizacao"):
        for a in ACOES:
            g[(PT, r, a)] = "todos"
    g[(PT, "nutricao.alimentos", "ver")] = "proprios"
    for r in RECURSOS:
        for a in ACOES:
            g[(3, r, a)] = "proprios"
    return g


class FakeDB:
    def __init__(
        self,
        tables=("access_groups", "access_group_grants"),
        groups=None,
        grants=None,
        dry_run=False,
        ignora_update=False,
    ):
        self.tables = set(tables)
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
        if s.startswith("UPDATE access_group_grants SET scope = %s WHERE group_id = %s AND resource IN"):
            novo, gid, *resto = params
            antigo = resto[-1]
            recursos = resto[:-1]
            assert s.endswith("AND scope = %s")
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


def test_sem_as_tabelas_da_111_plano_vazio_e_sem_escrita(caplog):
    for tabelas in ((), ("access_groups",), ("access_group_grants",)):
        db = FakeDB(tables=tabelas)
        with caplog.at_level("INFO"):
            assert _mod()["plan"](db) == []
            _up(db)
        assert db.executed == []
    assert any("115" in r.getMessage() for r in caplog.records)


def test_grupo_de_sistema_personal_trainer_ausente_levanta_erro_proprio_sem_escrita():
    grupos = {i: (k, s) for i, (k, s) in FakeDB().groups.items() if k != "personal_trainer"}
    db = FakeDB(groups=grupos)
    antes = dict(db.grants)
    with pytest.raises(_erro()):
        _up(db)
    assert db.executed == [] and db.grants == antes


def test_grupo_com_a_chave_mas_fora_do_sistema_conta_como_ausente():
    db = FakeDB(groups={1: ("usuario", 1), 2: ("personal_trainer", 0), 4: ("free", 0)})
    with pytest.raises(_erro()):
        _up(db)
    assert db.executed == []


def test_primeira_execucao_converte_so_as_doze_linhas_de_nutricao_do_pt():
    db = FakeDB()
    plano = _mod()["plan"](db)
    assert len(plano) == 1 and db.executed == [], "plan so le e emite 1 UPDATE"
    _up(db)
    assert _alunos(db) == {(PT, r, a) for r in RECURSOS for a in ACOES}
    assert plano == db.executed
    sql = " ".join(db.executed[0][0].split())
    assert sql == (
        "UPDATE access_group_grants SET scope = %s WHERE group_id = %s "
        "AND resource IN (%s, %s, %s, %s) AND scope = %s"
    )
    assert db.executed[0][1] == ("alunos", PT, *RECURSOS, "todos")


def test_modelos_e_demais_recursos_do_pt_seguem_inalterados_e_usuario_intacto():
    antes = _estado_inicial()
    db = FakeDB()
    _up(db)
    for chave, escopo in antes.items():
        if chave[0] == PT and chave[1] in RECURSOS:
            continue
        assert db.grants[chave] == escopo, chave
    for r in ("modelos.treino", "modelos.metas_ativacao", "modelos.tipos_treino", "ia.modelo_periodizacao"):
        assert all(db.grants[(PT, r, a)] == "todos" for a in ACOES)


def test_segunda_execucao_nao_emite_escrita():
    db = FakeDB()
    _up(db)
    n = len(db.executed)
    assert _mod()["plan"](db) == []
    _up(db)
    assert len(db.executed) == n


def test_grupo_nao_sistema_com_a_mesma_chave_fica_intacto():
    grupos = {1: ("admin", 1), 2: ("personal_trainer", 1), 3: ("usuario", 1), 4: ("free", 0), 5: ("personal_trainer", 0)}
    grants = _estado_inicial()
    for r in RECURSOS:
        grants[(5, r, "ver")] = "todos"
    db = FakeDB(groups=grupos, grants=grants)
    _up(db)
    assert all(db.grants[(5, r, "ver")] == "todos" for r in RECURSOS)
    assert all(db.grants[(PT, r, "ver")] == "alunos" for r in RECURSOS)


def test_escopo_editado_pelo_admin_nao_e_tocado():
    grants = _estado_inicial()
    grants[(PT, "nutricao.diario", "ver")] = "proprios"
    grants[(PT, "nutricao.plano", "ver")] = "alunos"
    db = FakeDB(grants=grants)
    _up(db)
    assert db.grants[(PT, "nutricao.diario", "ver")] == "proprios"
    assert db.grants[(PT, "nutricao.plano", "ver")] == "alunos"
    assert db.grants[(PT, "nutricao.diario", "editar")] == "alunos"


def test_estado_ja_convergido_ou_sem_linhas_gera_plano_vazio():
    assert _mod()["plan"](FakeDB(grants={})) == []
    convergido = {k: ("alunos" if k[1] in RECURSOS and k[0] == PT else v) for k, v in _estado_inicial().items()}
    assert _mod()["plan"](FakeDB(grants=convergido)) == []


def test_releitura_ao_fim_de_up_levanta_erro_se_o_update_nao_gravou():
    db = FakeDB(ignora_update=True)
    with pytest.raises(_erro()):
        _up(db)
    assert len(db.executed) == 1


def test_dry_run_nao_dispara_a_releitura():
    db = FakeDB(dry_run=True)
    _up(db)
    assert len(db.executed) == 1 and _alunos(db) == set()


def test_so_dml_um_statement_por_chamada_sem_fk_nem_set():
    db = FakeDB()
    _up(db)
    for sql, _ in db.executed:
        assert ";" not in sql and "exercise_catalog" not in sql
        assert not re.search(r"\b(CREATE|ALTER|DROP|PREPARE|SET @|INSERT|DELETE)\b", sql)


def test_retrato_congelado_proprio_sem_importar_app_access():
    fonte = MIGRATION.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+app", fonte, re.MULTILINE)
    assert _mod()["GRANTS_BY_GROUP"] == {
        "personal_trainer": tuple((r, a, "alunos") for r in RECURSOS for a in ACOES)
    }


def test_down_volta_para_todos_so_o_pt_e_e_idempotente():
    db = FakeDB()
    _up(db)
    db.executed.clear()
    _mod()["down"](db)
    assert db.grants == _estado_inicial()
    db.executed.clear()
    _mod()["down"](db)
    assert db.executed == []


def test_down_sem_tabelas_e_no_op():
    db = FakeDB(tables=())
    _mod()["down"](db)
    assert db.executed == []
