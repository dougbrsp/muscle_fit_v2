"""Migracao 111 (RBAC, ADR 0011, lote 1) com banco falso com estado.

- Cria as cinco tabelas; segunda execucao e no-op (sem duplicar seed nem
  sobrescrever concessoes editadas).
- `free` sem concessoes nem membros; `admin` sem linhas de concessao.
- Membros derivados de `users.role` para os tres papeis.
- `down` remove as cinco tabelas; nenhuma FK para `exercise_catalog`; `users`
  nao e alterada; um statement por chamada (Data API).

Rodar: cd api && python -m pytest tests/unit/test_migracao_111_access_groups.py
"""

from __future__ import annotations

import functools
import re

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "111_access_groups_rbac.py"
CINCO = ["access_groups", "access_group_grants", "user_access_groups", "trainer_students", "access_audit_log"]


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _up():
    return _mod()["up"]


def _down():
    return _mod()["down"]


class FakeDB:
    """Estado minimo: tabelas existentes, grupos, concessoes, membros e `users`."""

    def __init__(self, users=None, engine="InnoDB", broken_column=None):
        self.users = dict(users if users is not None else {1: "admin", 2: "personal_trainer", 3: "regular", 4: "regular"})
        self.tables: set[str] = set()
        self.groups: dict[str, dict] = {}  # group_key -> {name, description, is_system}
        self.grants: dict[tuple[str, str, str], str] = {}  # (group_key, resource, action) -> scope
        self.members: set[tuple[int, str]] = set()
        self.engine = engine
        self.broken_column = broken_column
        self.executed: list[tuple[str, tuple | None]] = []

    # --- leitura ---
    def query(self, sql, params=None):
        g = _mod()
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t, "ENGINE": self.engine} for t in params if t in self.tables]
        if "information_schema.COLUMNS" in sql:
            rows = []
            for name, (tipo, nulo) in g["EXPECTED_COLUMNS"][params[0]].items():
                if self.broken_column == (params[0], name):
                    tipo = "text"
                rows.append({"COLUMN_NAME": name, "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo})
            return rows
        if "information_schema.STATISTICS" in sql:
            rows = []
            for index, cols in g["EXPECTED_UNIQUES"][params[0]].items():
                for seq, col in enumerate(cols, start=1):
                    rows.append({"INDEX_NAME": index, "SEQ_IN_INDEX": seq, "COLUMN_NAME": col, "NON_UNIQUE": 0})
            return rows
        mt = re.match(r"SELECT COUNT\(\*\) AS n FROM (\w+)", sql)
        if mt:
            tabela = mt.group(1)
            n = {
                "access_groups": len(self.groups),
                "access_group_grants": len(self.grants),
                "user_access_groups": len(self.members),
            }[tabela]
            return [{"n": n}]
        raise AssertionError(f"leitura inesperada: {sql}")

    # --- escrita (aplica no estado) ---
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        s = " ".join(sql.split())
        if s.startswith("CREATE TABLE IF NOT EXISTS"):
            self.tables.add(s.split()[5])
        elif s.startswith("DROP TABLE IF EXISTS"):
            tabela = s.split()[4]
            self.tables.discard(tabela)
            if tabela == "access_groups":
                self.groups.clear()
            if tabela == "access_group_grants":
                self.grants.clear()
            if tabela == "user_access_groups":
                self.members.clear()
        elif s.startswith("INSERT IGNORE INTO access_groups"):
            assert "access_groups" in self.tables
            for i in range(0, len(params), 4):
                key, name, desc, is_system = params[i : i + 4]
                self.groups.setdefault(key, {"name": name, "description": desc, "is_system": is_system})
        elif s.startswith("INSERT IGNORE INTO access_group_grants"):
            assert "access_group_grants" in self.tables
            for i in range(0, len(params), 4):
                key, resource, action, scope = params[i : i + 4]
                assert key in self.groups, "grupo precisa existir antes da concessao"
                self.grants.setdefault((key, resource, action), scope)  # UNIQUE (group, resource, action)
        elif s.startswith("INSERT IGNORE INTO user_access_groups"):
            assert "user_access_groups" in self.tables
            # UM statement para todos os papeis: o mapeamento papel->grupo vem dos
            # parametros do CASE (pares WHEN %s THEN %s), sem depender de ordem.
            assert "CASE u.role" in s and s.endswith("END")
            assert s.count("WHEN %s THEN %s") * 2 == len(params) and len(params) % 2 == 0
            mapa = dict(zip(params[0::2], params[1::2]))
            for uid, role in self.users.items():
                grupo = mapa.get(role)  # papel fora do CASE = NULL = nao casa o JOIN
                if grupo is not None and grupo in self.groups:
                    self.members.add((uid, grupo))
        else:
            raise AssertionError(f"statement inesperado: {s[:80]}")

    def grants_of(self, group: str) -> set[tuple[str, str, str]]:
        return {(r, a, sc) for (g, r, a), sc in self.grants.items() if g == group}


def _aplicar(db: FakeDB) -> FakeDB:
    _up()(db)
    return db


# ---------------------------------------------------------------------------
# Criacao e seed
# ---------------------------------------------------------------------------


def test_cria_as_cinco_tabelas_em_ordem_de_dependencia():
    db = _aplicar(FakeDB())
    assert db.tables == set(CINCO)
    criadas = [s.split()[5] for s, _ in db.executed if s.strip().startswith("CREATE TABLE")]
    assert criadas == CINCO
    assert criadas.index("access_groups") < criadas.index("access_group_grants")
    assert criadas.index("access_groups") < criadas.index("user_access_groups")


def test_semeia_os_quatro_grupos_com_free_fora_do_sistema():
    db = _aplicar(FakeDB())
    assert set(db.groups) == {"admin", "personal_trainer", "usuario", "free"}
    assert [db.groups[k]["is_system"] for k in ("admin", "personal_trainer", "usuario")] == [1, 1, 1]
    assert db.groups["free"]["is_system"] == 0


def test_free_sem_concessoes_nem_membros_e_admin_sem_linhas_de_concessao():
    db = _aplicar(FakeDB())
    assert db.grants_of("free") == set() and db.grants_of("admin") == set()
    assert not [1 for _, g in db.members if g == "free"]
    assert not [1 for g, _, _ in db.grants if g in ("free", "admin")]


def test_concessoes_semeadas_sao_a_matriz_da_migracao():
    db = _aplicar(FakeDB())
    for grupo, linhas in _mod()["GRANTS_BY_GROUP"].items():
        assert db.grants_of(grupo) == set(linhas), grupo
    assert db.grants_of("usuario") and db.grants_of("personal_trainer")


def test_membros_derivados_de_users_role_para_os_tres_papeis():
    db = _aplicar(FakeDB())
    assert db.members == {(1, "admin"), (2, "personal_trainer"), (3, "usuario"), (4, "usuario")}


def test_papel_desconhecido_nao_vira_membro_de_grupo():
    db = _aplicar(FakeDB(users={1: "admin", 2: "visitante"}))
    assert db.members == {(1, "admin")}


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


def test_segunda_execucao_e_no_op():
    db = _aplicar(FakeDB())
    antes = (dict(db.groups), dict(db.grants), set(db.members), len(db.executed))
    assert _mod()["plan"](db) == []
    _aplicar(db)
    assert (dict(db.groups), dict(db.grants), set(db.members), len(db.executed)) == antes


def test_segunda_execucao_nao_sobrescreve_concessoes_editadas_nem_membros_novos():
    db = _aplicar(FakeDB())
    # o admin edita pela tela de grupos: rebaixa uma concessao, remove outra, move um usuario, cria grupo
    db.grants[("usuario", "cardio", "ver")] = "proprios"
    chave_removida = ("usuario", "relatorios", "excluir")
    del db.grants[chave_removida]
    db.grants[("usuario", "cardio", "editar")] = "alunos"
    db.members.discard((3, "usuario"))
    db.members.add((3, "free"))
    db.groups["custom"] = {"name": "Custom", "description": None, "is_system": 0}
    antes = (dict(db.groups), dict(db.grants), set(db.members))
    executados = len(db.executed)
    _aplicar(db)
    assert (dict(db.groups), dict(db.grants), set(db.members)) == antes
    assert chave_removida not in db.grants
    assert len(db.executed) == executados, "nenhum statement na reexecucao"


def test_novo_usuario_depois_da_migracao_nao_e_membro_na_reexecucao():
    db = _aplicar(FakeDB())
    db.users[9] = "regular"
    _aplicar(db)
    assert (9, "usuario") not in db.members


def _estado(db: FakeDB):
    return (
        frozenset(db.tables),
        {k: dict(v) for k, v in db.groups.items()},
        dict(db.grants),
        frozenset(db.members),
    )


def verificar_convergencia(plan_fn, db_factory=FakeDB) -> None:
    """Para CADA ponto de corte N: aplica os N primeiros statements do plano do
    zero, chama `plan_fn(db)` DE NOVO sobre o estado parcial, aplica o que ele
    devolver e exige estado final igual ao de uma execucao completa; um terceiro
    `plan_fn(db)` tem de devolver vazio."""
    completo = db_factory()
    for sql, params in plan_fn(completo):
        completo.execute(sql, params)
    esperado = _estado(completo)
    tamanho = len(plan_fn(db_factory()))
    assert tamanho > 0
    for corte in range(tamanho + 1):
        db = db_factory()
        for sql, params in plan_fn(db)[:corte]:
            db.execute(sql, params)
        for sql, params in plan_fn(db):  # replanejado sobre o estado parcial
            db.execute(sql, params)
        assert _estado(db) == esperado, f"corte {corte}: nao convergiu"
        assert plan_fn(db) == [], f"corte {corte}: plano nao esvaziou"


def test_plano_do_zero_tem_oito_statements_com_um_unico_seed_por_tabela():
    plano = _mod()["plan"](FakeDB())
    assert len(plano) == 8
    sqls = [" ".join(sql.split()) for sql, _ in plano]
    assert [s.split()[5] for s in sqls[:5]] == CINCO
    assert sum(s.startswith("INSERT IGNORE INTO access_groups") for s in sqls) == 1
    assert sum(s.startswith("INSERT IGNORE INTO access_group_grants") for s in sqls) == 1
    assert sum(s.startswith("INSERT IGNORE INTO user_access_groups") for s in sqls) == 1
    membros = plano[7]
    assert "JOIN access_groups g ON g.group_key = CASE u.role" in " ".join(membros[0].split())
    assert len(membros[1]) == 6
    assert tuple(zip(membros[1][0::2], membros[1][1::2])) == tuple(_mod()["ROLE_TO_GROUP"])


def test_membros_nao_dependem_da_ordem_dos_pares_do_case():
    g = _mod()
    db = FakeDB()
    db.tables = set(CINCO)
    db.execute(g["INSERT_GROUPS_SQL"], tuple(v for k, n, d, i in g["SEED_GROUPS"] for v in (k, n, d, i)))
    invertidos = tuple(v for par in reversed(g["ROLE_TO_GROUP"]) for v in par)
    db.execute(g["BACKFILL_MEMBERS_SQL"], invertidos)
    assert db.members == {(1, "admin"), (2, "personal_trainer"), (3, "usuario"), (4, "usuario")}


def test_execucao_interrompida_em_qualquer_ponto_converge_para_a_execucao_completa():
    verificar_convergencia(_mod()["plan"])


def test_convergencia_tambem_com_usuarios_de_papel_desconhecido():
    verificar_convergencia(_mod()["plan"], lambda: FakeDB(users={1: "admin", 2: "visitante", 3: "regular"}))


def test_estado_completo_com_concessao_removida_nao_e_resemeado():
    db = _aplicar(FakeDB())
    del db.grants[("usuario", "relatorios", "excluir")]
    antes = _estado(db)
    assert _mod()["plan"](db) == []
    _aplicar(db)
    assert _estado(db) == antes and ("usuario", "relatorios", "excluir") not in db.grants


def test_estado_completo_com_membro_removido_nao_e_resemeado():
    db = _aplicar(FakeDB())
    db.members.discard((4, "usuario"))
    antes = _estado(db)
    assert _mod()["plan"](db) == []
    _aplicar(db)
    assert _estado(db) == antes and (4, "usuario") not in db.members


def test_reexecucao_apos_cortes_nao_duplica_statement_de_seed_ja_aplicado():
    db = FakeDB()
    plano = _mod()["plan"](db)
    for sql, params in plano[:7]:  # tudo menos os membros
        db.execute(sql, params)
    restante = _mod()["plan"](db)
    assert len(restante) == 1 and " ".join(restante[0][0].split()).startswith("INSERT IGNORE INTO user_access_groups")


def test_so_ddl_e_insert_ignore_na_aplicacao():
    db = _aplicar(FakeDB())
    for sql, _ in db.executed:
        s = " ".join(sql.split())
        assert s.startswith(("CREATE TABLE IF NOT EXISTS", "INSERT IGNORE INTO")), s[:60]


# ---------------------------------------------------------------------------
# Forma divergente: erro ANTES do primeiro statement
# ---------------------------------------------------------------------------


def test_tabela_existente_com_forma_divergente_falha_antes_de_qualquer_statement():
    db = FakeDB(broken_column=("access_group_grants", "scope"))
    db.tables = {"access_group_grants"}
    with pytest.raises(_mod()["AccessGroupsRbacError"]):
        _up()(db)
    assert db.executed == []


def test_tabela_existente_com_engine_errado_falha_antes_de_qualquer_statement():
    db = FakeDB(engine="MyISAM")
    db.tables = {"access_groups"}
    with pytest.raises(_mod()["AccessGroupsRbacError"]):
        _up()(db)
    assert db.executed == []


# ---------------------------------------------------------------------------
# down
# ---------------------------------------------------------------------------


def test_down_remove_as_cinco_tabelas_em_ordem_inversa():
    db = _aplicar(FakeDB())
    db.executed.clear()
    _down()(db)
    assert db.tables == set()
    dropadas = [s.split()[4] for s, _ in db.executed]
    assert dropadas == list(reversed(CINCO))
    assert dropadas.index("access_group_grants") < dropadas.index("access_groups")


def test_down_sem_tabelas_e_no_op_e_up_depois_do_down_recria_o_seed():
    db = FakeDB()
    _down()(db)
    assert db.executed == []
    _aplicar(db)
    _down()(db)
    _aplicar(db)
    assert db.tables == set(CINCO) and len(db.groups) == 4 and db.members


# ---------------------------------------------------------------------------
# Restricoes de DDL
# ---------------------------------------------------------------------------


def _todo_o_sql() -> str:
    db = _aplicar(FakeDB())
    return "\n".join(sql for sql, _ in db.executed)


def test_nenhuma_fk_para_exercise_catalog():
    g = _mod()
    ddl = "\n".join(g["CREATE_SQL"].values())
    assert "exercise_catalog" not in ddl and "exercise_catalog" not in _todo_o_sql()


def test_fks_so_apontam_para_users_e_access_groups():
    destinos = set(re.findall(r"REFERENCES\s+(\w+)", "\n".join(_mod()["CREATE_SQL"].values())))
    assert destinos == {"users", "access_groups"}


def test_migracao_nao_altera_users():
    sql = _todo_o_sql()
    sem_default = sql.replace("ON UPDATE CURRENT_TIMESTAMP", "").replace("ON DELETE CASCADE", "")  # clausulas de DDL, nao statements
    assert not re.search(r"\b(ALTER|UPDATE|DELETE|TRUNCATE|DROP)\b", sem_default, re.I)
    assert not re.search(r"INSERT\s+(IGNORE\s+)?INTO\s+users\b", sql, re.I)
    # `users` so aparece como destino de FK e na origem do backfill de membros
    for linha in sql.splitlines():
        if re.search(r"\busers\b", linha):
            assert "REFERENCES users" in linha or "FROM users u" in linha, linha


def test_um_statement_por_chamada_sem_set_nem_prepare():
    for sql in list(_mod()["CREATE_SQL"].values()) + [_mod()["INSERT_GROUPS_SQL"], _mod()["BACKFILL_MEMBERS_SQL"]]:
        assert ";" not in sql
        assert not re.search(r"\b(PREPARE|EXECUTE|SET)\s", sql)


def test_unicidades_garantem_idempotencia_do_seed_e_vinculo_ativo_unico():
    g = _mod()
    ddl = g["CREATE_SQL"]
    assert "UNIQUE KEY uq_agg_group_resource_action (group_id, resource, action)" in ddl["access_group_grants"]
    assert "UNIQUE KEY uq_uag_user_group (user_id, group_id)" in ddl["user_access_groups"]
    assert "UNIQUE KEY uq_access_groups_key (group_key)" in ddl["access_groups"]
    assert "active_flag" in ddl["trainer_students"] and "IF(status = 'ativo', 1, NULL)" in ddl["trainer_students"]
