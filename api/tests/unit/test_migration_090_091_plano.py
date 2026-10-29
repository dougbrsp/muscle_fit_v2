"""Planos das migracoes 090 (coluna de linhagem `source_template_id`) e 091
(junta o Modelo 36 ao 34, escrita autorizada pelo usuario em 2026-10-24) contra
um banco sintetico:

- 090: banco atual -> ADD COLUMN INSTANT + ADD INDEX online; convergido -> 0;
  forma divergente -> erro antes do 1o statement; DRY-RUN sem tabela aceito;
  `plan_down` inverso; sem FK, sem ':' em literal, sem DML;
- 091: so escreve com as 5 guardas (existem, mesmo usuario, 36 sem origem, sem
  ciclo, mesma grade vigente); senao 0 statements (DEV/banco novo inofensivo);
  idempotente; o UPDATE repete as guardas no SQL; `down` so desfaz a propria.

Rodar: cd api && python -m pytest tests/unit/test_migration_090_091_plano.py -q
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

M090 = m.DEFAULT_MIGRATIONS_DIR / "090_template_lineage.py"
M091 = m.DEFAULT_MIGRATIONS_DIR / "091_linhagem_modelo_36_do_34.py"


@functools.lru_cache(maxsize=None)
def _mod(path) -> dict:
    return m.load_python_migration(path).__globals__


# ---------------------------------------------------------------------------
# 090
# ---------------------------------------------------------------------------


class Fake090:
    def __init__(self, *, tabela=True, coluna=None, indice=(), dry_run=False):
        self.tabela = tabela
        self.coluna = coluna  # (COLUMN_TYPE, IS_NULLABLE) ou None
        self.indice = list(indice)
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            assert params == ("workout_plan_templates",)
            return [{"TABLE_NAME": "workout_plan_templates"}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            assert params == ("workout_plan_templates", "source_template_id")
            if not self.tabela or self.coluna is None:
                return []
            return [{"COLUMN_NAME": "source_template_id", "COLUMN_TYPE": self.coluna[0], "IS_NULLABLE": self.coluna[1]}]
        if "information_schema.STATISTICS" in sql:
            assert params == ("workout_plan_templates", "idx_wpt_source_template")
            return [{"COLUMN_NAME": c} for c in self.indice] if self.tabela else []
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append(sql)


def _convergido_090(**kw):
    return Fake090(coluna=("bigint", "YES"), indice=("source_template_id",), **kw)


def test_090_banco_atual_recebe_coluna_e_indice():
    db = Fake090()
    _mod(M090)["up"](db)
    assert db.executed == [
        "ALTER TABLE workout_plan_templates ADD COLUMN source_template_id BIGINT NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE workout_plan_templates ADD INDEX idx_wpt_source_template (source_template_id), "
        "ALGORITHM=INPLACE, LOCK=NONE",
    ]


def test_090_convergido_e_meio_caminho():
    assert _mod(M090)["plan"](_convergido_090()) == []
    assert _mod(M090)["plan"](Fake090(coluna=("bigint(20)", "yes"), indice=("source_template_id",))) == []
    so_coluna = Fake090(coluna=("bigint", "YES"))
    assert [s for s, _ in _mod(M090)["plan"](so_coluna)] == [_mod(M090)["ADD_INDEX_SQL"]]


@pytest.mark.parametrize(
    "db",
    [
        Fake090(coluna=("int", "YES")),
        Fake090(coluna=("bigint", "NO")),
        Fake090(coluna=("bigint", "YES"), indice=("name",)),
        Fake090(coluna=("bigint", "YES"), indice=("source_template_id", "id")),
    ],
)
def test_090_forma_divergente_falha_antes_do_primeiro_statement(db):
    with pytest.raises(_mod(M090)["TemplateLineageError"], match="Nenhum statement executado"):
        _mod(M090)["up"](db)
    assert db.executed == []


def test_090_tabela_ausente_falha_e_dry_run_planeja():
    with pytest.raises(_mod(M090)["TemplateLineageError"], match="nao existe"):
        _mod(M090)["plan"](Fake090(tabela=False))
    assert len(_mod(M090)["plan"](Fake090(tabela=False, dry_run=True))) == 2


def test_090_down_inverso_e_idempotente():
    assert [s for s, _ in _mod(M090)["plan_down"](_convergido_090())] == [
        "ALTER TABLE workout_plan_templates DROP INDEX idx_wpt_source_template",
        "ALTER TABLE workout_plan_templates DROP COLUMN source_template_id",
    ]
    assert _mod(M090)["plan_down"](Fake090()) == []
    assert _mod(M090)["plan_down"](Fake090(tabela=False)) == []


def test_090_sem_fk_sem_dml_e_sem_dois_pontos():
    fonte = M090.read_text(encoding="utf-8")
    sqls = [s for s, _ in _mod(M090)["plan"](Fake090())] + [s for s, _ in _mod(M090)["plan_down"](_convergido_090())]
    for sql in sqls:
        assert "FOREIGN KEY" not in sql.upper() and "REFERENCES" not in sql.upper()
        assert ":" not in sql and ";" not in sql
    codigo = fonte.split('"""', 2)[2].upper()
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "PREPARE ", "SET @", "IMPORT APP", "FROM APP", "EXERCISE_CATALOG"):
        assert proibido not in codigo


# ---------------------------------------------------------------------------
# 091
# ---------------------------------------------------------------------------


class Fake091:
    """Modelos `{id: (created_by_user_id, source_template_id[, created_at])}` e
    sequencias da versao vigente `{template_id: [sequence, ...]}`."""

    CRIADO = {34: "2026-10-12 11:47:05", 36: "2026-10-17 10:06:27"}

    def __init__(self, *, coluna=True, modelos=None, grades=None, dry_run=False):
        self.coluna = coluna
        self.modelos = {36: (1, None), 34: (1, None)} if modelos is None else modelos
        seq = list(range(1, 8))
        self.grades = {36: seq, 34: seq} if grades is None else grades
        self.dry_run = dry_run
        self.executed: list[tuple[str, tuple]] = []

    def query(self, sql, params=None):
        if "information_schema.COLUMNS" in sql:
            assert params == ("workout_plan_templates", "source_template_id")
            return [{"COLUMN_NAME": "source_template_id"}] if self.coluna else []
        assert self.coluna, "nao le a coluna antes de saber que ela existe"
        if sql == _mod(M091)["TEMPLATES_SQL"]:
            assert params == (36, 34)
            return [
                {"id": tid, "created_by_user_id": dados[0], "source_template_id": dados[1],
                 "created_at": dados[2] if len(dados) > 2 else self.CRIADO.get(tid)}
                for tid, dados in self.modelos.items()
                if tid in params
            ]
        if sql == _mod(M091)["CURRENT_SEQUENCES_SQL"]:
            assert params == (36, 34)
            return [{"template_id": tid, "sequence": s} for tid, seqs in self.grades.items() if tid in params for s in seqs]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


def test_091_par_compativel_grava_a_origem_uma_vez():
    db = Fake091()
    _mod(M091)["up"](db)
    assert db.executed == [(_mod(M091)["LINK_SQL"], (34, 36))]
    # PyMySQL devolve datetime; a Data API, texto: os dois servem.
    import datetime as dt

    com_datetime = Fake091(modelos={36: (1, None, dt.datetime(2026, 10, 17, 10, 6, 27)),
                                    34: (1, None, dt.datetime(2026, 10, 12, 11, 47, 5))})
    assert len(_mod(M091)["plan"](com_datetime)) == 1


@pytest.mark.parametrize(
    "db",
    [
        Fake091(modelos={34: (1, None)}),  # 36 nao existe (DEV / banco novo)
        Fake091(modelos={36: (1, None)}),  # 34 nao existe
        Fake091(modelos={}),
        Fake091(modelos={36: (1, None), 34: (2, None)}),  # usuarios diferentes
        Fake091(modelos={36: (1, 28), 34: (1, None)}),  # 36 ja tem origem (ou ja aplicada: idempotente)
        Fake091(modelos={36: (1, 34), 34: (1, None)}),
        Fake091(modelos={36: (1, None), 34: (1, 36)}),  # criaria ciclo
        Fake091(modelos={36: (1, None), 34: (1, 9)}),  # 34 nao e raiz (ciclo longo possivel)
        Fake091(modelos={36: (1, None, "2026-11-02 08:00:00"), 34: (1, None)}),  # outro 36 (DEV/CI semeado)
        Fake091(modelos={36: (1, None), 34: (1, None, "2026-09-01 08:00:00")}),  # outro 34
        Fake091(modelos={36: (1, None, None), 34: (1, None)}),
        Fake091(grades={36: [1, 2, 3, 4, 5, 6, 7], 34: [1, 2, 3, 4, 5, 6]}),  # grade diferente
        Fake091(grades={36: [1, 2, 3], 34: [1, 2, 4]}),
        Fake091(grades={36: [], 34: []}),  # sem versao vigente
    ],
)
def test_091_qualquer_guarda_falhando_nao_escreve(db):
    _mod(M091)["up"](db)
    assert db.executed == []


def test_091_o_update_repete_as_guardas_no_sql():
    sql = _mod(M091)["LINK_SQL"]
    assert "c.source_template_id IS NULL" in sql
    assert "c.created_by_user_id = o.created_by_user_id" in sql
    assert "o.source_template_id IS NULL" in sql
    assert sql.count("%s") == 2 and ":" not in sql and ";" not in sql
    assert (_mod(M091)["COPY_TEMPLATE_ID"], _mod(M091)["SOURCE_TEMPLATE_ID"]) == (36, 34)


def test_091_dry_run_antes_da_090_planeja_e_execucao_real_sem_coluna_falha():
    assert _mod(M091)["plan"](Fake091(coluna=False, dry_run=True)) == [(_mod(M091)["LINK_SQL"], (34, 36))]
    with pytest.raises(RuntimeError, match="090 nao aplicada"):
        _mod(M091)["plan"](Fake091(coluna=False))


def test_091_down_so_desfaz_a_propria_origem():
    db = Fake091()
    _mod(M091)["down"](db)
    assert db.executed == [(_mod(M091)["UNLINK_SQL"], (36, 34))]
    assert "source_template_id = %s" in _mod(M091)["UNLINK_SQL"].split("WHERE", 1)[1]
    assert _mod(M091)["plan_down"](Fake091(coluna=False)) == []


def test_091_autocontida_e_sem_ddl():
    codigo = M091.read_text(encoding="utf-8").split('"""', 2)[2].upper()
    for proibido in ("ALTER ", "CREATE ", "DROP ", "DELETE ", "INSERT ", "PREPARE ", "SET @", "IMPORT APP", "FROM APP"):
        assert proibido not in codigo
