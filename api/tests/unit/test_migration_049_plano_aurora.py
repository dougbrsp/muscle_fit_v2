"""Plano da migracao 049 (uma atribuicao de Modelo vigente por usuario, com
historico - M23) contra um recorte REAL do `information_schema`.

Recorte: `tests/unit/data/schema_dev_2026_09_24_049.json` (ver `_origem`), lido
do MySQL 8.0.46 do DEV em 2026-09-24, na versao 48 de `schema_migration_files`
- a MESMA versao da Aurora de producao, ja que a 048 (`context_events`) foi
aplicada em producao no PR #203. O recorte veio do DEV e nao da Aurora porque
nenhuma leitura nova da AWS foi feita (nem podia ser: infraestrutura so por
PR) e porque `workout_plan_template_assignments` nasce do baseline
`007_workout_plan_templates.sql` (`schema_migrations` 29), aplicado identico
nos dois ambientes e nunca tocado por migracao `.py` posterior - o teste
`test_o_recorte_descreve_a_tabela_do_baseline_007` amarra as duas pontas
comparando o recorte com o DDL do arquivo. Mesmo precedente de
`schema_dev_2026_09_20_039.json` (migracao 039).

Producao hoje: sem as colunas de vigencia, com o UNIQUE antigo -> 5
statements. Estado convergido -> 0.
"""

from __future__ import annotations

import copy
import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.paridade_028 import SnapshotDB

MIGRATION_049 = m.DEFAULT_MIGRATIONS_DIR / "049_template_assignment_history.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "schema_dev_2026_09_24_049.json"
BASELINE_007 = m.DEFAULT_MIGRATIONS_DIR / "007_workout_plan_templates.sql"

TABELA = "workout_plan_template_assignments"

ADD_COLUMNS = (
    "ALTER TABLE workout_plan_template_assignments "
    "ADD COLUMN unassigned_at DATETIME NULL, "
    "ADD COLUMN unassigned_by_user_id BIGINT NULL, "
    "ADD KEY idx_wpta_template (template_id)"
)
DROP_UNIQUE_ANTIGO = "ALTER TABLE workout_plan_template_assignments DROP INDEX uq_wpta_template_user"
ADD_ACTIVE_USER = (
    "ALTER TABLE workout_plan_template_assignments ADD COLUMN active_user_id BIGINT "
    "GENERATED ALWAYS AS (IF(unassigned_at IS NULL, user_id, NULL)) VIRTUAL"
)
ADD_ACTIVE_UNIQUE = (
    "ALTER TABLE workout_plan_template_assignments ADD UNIQUE KEY uq_wpta_active_user (active_user_id)"
)


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria uma classe de erro nova a cada
    # chamada e `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_049).__globals__


class _DB049(SnapshotDB):
    """`SnapshotDB` + os tres campos que a 049 le e a base nao serve:
    `GENERATION_EXPRESSION` (a coluna gerada), `SEQ_IN_INDEX` (ordem das
    colunas de um indice composto) e as contagens de `plan_down`."""

    def __init__(self, snapshot, duplicadas=None):
        super().__init__(snapshot)
        self.duplicadas = duplicadas or []
        self.dry_run = False

    def query(self, sql, params=None):
        if "GROUP BY template_id, user_id" in sql:
            # Consulta de DADOS, so do `plan_down`. O `plan` nunca chega aqui
            # - e `test_o_plano_so_le_information_schema` prova isso.
            return list(self.duplicadas)
        rows = super().query(sql, params)
        if "information_schema.COLUMNS" in sql:
            geradas = {(c["t"], c["col"]): c.get("gen", "") for c in self.snap["columns"]}
            for row in rows:
                row["GENERATION_EXPRESSION"] = geradas.get((row["TABLE_NAME"], row["COLUMN_NAME"]), "")
        if "information_schema.STATISTICS" in sql:
            seqs = {
                (i["t"], i["idx"], i["col"]): i["seq"]
                for i in self.snap["indexes"]
            }
            for row in rows:
                row["SEQ_IN_INDEX"] = seqs[(row["TABLE_NAME"], row["INDEX_NAME"], row["COLUMN_NAME"])]
        return rows

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no snapshot: {sql}")


def _plan(snapshot, **kwargs):
    db = _DB049(snapshot, **kwargs)
    return [(" ".join(sql.split()), params) for sql, params in _module()["plan"](db)], db


def _plan_down(snapshot, **kwargs):
    db = _DB049(snapshot, **kwargs)
    return [(" ".join(sql.split()), params) for sql, params in _module()["plan_down"](db)], db


# ---------------------------------------------------------------------------
# Estados sinteticos
# ---------------------------------------------------------------------------


def _com_coluna(snap: dict, col: str, tipo: str, *, nullable="YES", gen="", **overrides) -> dict:
    ultima = max(c["pos"] for c in snap["columns"] if c["t"] == TABELA)
    nova = {
        "t": TABELA, "pos": ultima + 1, "col": col, "type": tipo, "nullable": nullable,
        "dflt": None, "ckey": "", "extra": "VIRTUAL GENERATED" if gen else "", "coll": None,
        "gen": gen,
    }
    nova.update(overrides)
    snap["columns"].append(nova)
    return snap


def _sem_indice(snap: dict, nome: str) -> dict:
    snap["indexes"] = [i for i in snap["indexes"] if not (i["t"] == TABELA and i["idx"] == nome)]
    return snap


def _com_indice(snap: dict, nome: str, colunas, *, non_unique: int) -> dict:
    for seq, col in enumerate(colunas, 1):
        snap["indexes"].append({"t": TABELA, "idx": nome, "seq": seq, "col": col, "non_unique": non_unique})
    return snap


def _convergido(snapshot: dict) -> dict:
    """Recorte com a 049 JA aplicada (estado pos-deploy)."""
    snap = copy.deepcopy(snapshot)
    _com_coluna(snap, "unassigned_at", "datetime")
    _com_coluna(snap, "unassigned_by_user_id", "bigint")
    _com_coluna(snap, "active_user_id", "bigint", gen="if(`unassigned_at` is null,`user_id`,NULL)")
    _sem_indice(snap, "uq_wpta_template_user")
    _com_indice(snap, "idx_wpta_template", ["template_id"], non_unique=1)
    _com_indice(snap, "uq_wpta_active_user", ["active_user_id"], non_unique=0)
    return snap


# ---------------------------------------------------------------------------
# O recorte
# ---------------------------------------------------------------------------


def test_o_recorte_e_da_versao_48_e_a_tabela_ainda_nao_tem_vigencia():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    assert snap["versao"][0]["schema_migration_files"] == 48
    engines = {t["table_name"]: t["engine"] for t in snap["tables"]}
    assert engines[TABELA] == "InnoDB"
    colunas = {c["col"] for c in snap["columns"] if c["t"] == TABELA}
    assert colunas == {"id", "template_id", "user_id", "assigned_by_user_id", "assigned_at"}
    indices = {i["idx"] for i in snap["indexes"] if i["t"] == TABELA}
    assert "uq_wpta_template_user" in indices


def test_o_recorte_descreve_a_tabela_do_baseline_007():
    """A ponte entre "li o DEV" e "isto tambem e producao": o recorte tem de
    bater com o `CREATE TABLE` do baseline, que e o unico lugar em que esta
    tabela foi definida e que rodou igual nos dois ambientes."""
    ddl = BASELINE_007.read_text(encoding="utf-8")
    bloco = re.search(
        rf"CREATE TABLE IF NOT EXISTS {TABELA} \((.*?)\n\) ENGINE=InnoDB", ddl, re.DOTALL
    )
    assert bloco, "o baseline 007 precisa continuar definindo a tabela"
    corpo = bloco.group(1)
    for coluna in ("id", "template_id", "user_id", "assigned_by_user_id", "assigned_at"):
        assert re.search(rf"^\s*{coluna}\s", corpo, re.MULTILINE), coluna
    assert "UNIQUE KEY uq_wpta_template_user (template_id, user_id)" in corpo
    # E o inverso: nada de vigencia no baseline, senao a 049 seria redundante.
    assert "unassigned_at" not in corpo


def test_o_unique_antigo_era_o_unico_indice_que_apoiava_a_fk_de_template():
    """Por que `idx_wpta_template` entra ANTES do DROP: sem ele, o InnoDB
    recusa remover o ultimo indice que atende `fk_wpta_template` (errno 150) e
    a migracao morreria no meio."""
    snap = _snapshot()
    fks = {f["fk"]: f for f in snap["foreign_keys"] if f["t"] == TABELA}
    assert fks["fk_wpta_template"]["col"] == "template_id"
    com_template_na_frente = {
        i["idx"] for i in snap["indexes"] if i["t"] == TABELA and i["seq"] == 1 and i["col"] == "template_id"
    }
    assert com_template_na_frente == {"uq_wpta_template_user"}


# ---------------------------------------------------------------------------
# O plano
# ---------------------------------------------------------------------------


def test_producao_hoje_executa_cinco_statements_nesta_ordem():
    statements, _ = _plan(_snapshot())
    sqls = [sql for sql, _ in statements]
    assert sqls[0] == ADD_COLUMNS
    assert sqls[1].startswith("UPDATE workout_plan_template_assignments a JOIN (")
    assert sqls[2] == DROP_UNIQUE_ANTIGO
    assert sqls[3] == ADD_ACTIVE_USER
    assert sqls[4] == ADD_ACTIVE_UNIQUE
    assert len(sqls) == 5


def test_o_backfill_roda_antes_do_unique_de_uma_vigente_por_usuario():
    """A ORDEM e a prova de que o indice nunca ve o estado intermediario em
    que todas as atribuicoes estao abertas - que e o unico estado em que ele
    quebraria."""
    sqls = [sql for sql, _ in _plan(_snapshot())[0]]
    assert sqls.index("ALTER TABLE workout_plan_template_assignments ADD UNIQUE KEY "
                      "uq_wpta_active_user (active_user_id)") > next(
        i for i, s in enumerate(sqls) if s.startswith("UPDATE")
    )


def test_o_backfill_desempata_por_id_e_so_fecha_quem_tem_sucessora():
    """Sem o desempate por `id`, duas atribuicoes no MESMO segundo ficariam as
    duas vigentes e o UNIQUE derrubaria a migracao no ultimo statement."""
    update = next(sql for sql, _ in _plan(_snapshot())[0] if sql.startswith("UPDATE"))
    assert "seguinte.assigned_at = atual.assigned_at AND seguinte.id > atual.id" in update
    assert "MIN(seguinte.assigned_at)" in update
    # Quem nao tem sucessora nao entra no JOIN e por isso fica VIGENTE.
    assert "WHERE a.unassigned_at IS NULL" in update


def test_o_plano_so_le_information_schema():
    _, db = _plan(_snapshot())
    assert db.queries
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)


def test_o_plano_so_olha_a_tabela_de_atribuicoes():
    """A 049 nao adiciona FK nenhuma, entao ela nao depende de `users` nem de
    `workout_plan_templates` - e por isso nao depende da ordem em relacao a
    nenhuma outra migracao do lote."""
    _, db = _plan(_snapshot())
    assert db.queries
    assert all(TABELA in q or "TABLE_NAME IN (%s)" in q for q in db.queries)


def test_estado_convergido_nao_executa_nada():
    statements, _ = _plan(_convergido(_snapshot()))
    assert statements == []


def test_reexecucao_apos_aplicar_e_zero_statements():
    snap = _snapshot()
    assert len(_plan(snap)[0]) == 5
    assert _plan(_convergido(snap))[0] == []


def test_estado_parcial_executa_so_o_que_falta_sem_repetir_o_backfill():
    """Aplicacao interrompida depois do statement 3: as colunas e o backfill ja
    existem, o UNIQUE antigo ja saiu. O plano tem de pedir SO os dois ultimos -
    e, principalmente, NAO reescrever `unassigned_at`, que a essa altura ja e
    dado do usuario."""
    snap = copy.deepcopy(_snapshot())
    _com_coluna(snap, "unassigned_at", "datetime")
    _com_coluna(snap, "unassigned_by_user_id", "bigint")
    _com_indice(snap, "idx_wpta_template", ["template_id"], non_unique=1)
    _sem_indice(snap, "uq_wpta_template_user")

    sqls = [sql for sql, _ in _plan(snap)[0]]
    assert sqls == [ADD_ACTIVE_USER, ADD_ACTIVE_UNIQUE]
    assert not any(s.startswith("UPDATE") for s in sqls)


def test_estado_parcial_com_so_uma_das_colunas_pede_a_outra_isoladamente():
    snap = copy.deepcopy(_snapshot())
    _com_coluna(snap, "unassigned_at", "datetime")
    sqls = [sql for sql, _ in _plan(snap)[0]]
    assert "ALTER TABLE workout_plan_template_assignments ADD COLUMN unassigned_by_user_id BIGINT NULL" in sqls
    assert "ALTER TABLE workout_plan_template_assignments ADD KEY idx_wpta_template (template_id)" in sqls
    assert ADD_COLUMNS not in sqls
    # `unassigned_at` ja existe -> o backfill NAO volta.
    assert not any(s.startswith("UPDATE") for s in sqls)


# ---------------------------------------------------------------------------
# Recusas
# ---------------------------------------------------------------------------


def test_tabela_ausente_ou_com_engine_errada_falha():
    erro = _module()["TemplateAssignmentHistoryError"]

    sem = copy.deepcopy(_snapshot())
    sem["tables"] = [t for t in sem["tables"] if t["table_name"] != TABELA]
    with pytest.raises(erro):
        _plan(sem)

    myisam = copy.deepcopy(_snapshot())
    for t in myisam["tables"]:
        if t["table_name"] == TABELA:
            t["engine"] = "MyISAM"
    with pytest.raises(erro):
        _plan(myisam)


@pytest.mark.parametrize(
    "col,tipo,nullable",
    [
        ("unassigned_at", "date", "YES"),
        ("unassigned_at", "datetime", "NO"),
        ("unassigned_by_user_id", "int", "YES"),
    ],
)
def test_coluna_de_vigencia_divergente_falha_antes_de_qualquer_statement(col, tipo, nullable):
    erro = _module()["TemplateAssignmentHistoryError"]
    snap = _com_coluna(copy.deepcopy(_snapshot()), col, tipo, nullable=nullable)
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert "Nenhum statement executado" in str(exc.value)


def test_active_user_id_com_outra_expressao_falha():
    """A coluna gerada E a invariante. Se alguem a recriou com outra regra (por
    exemplo sem o `IF`), o UNIQUE passa a garantir outra coisa - e a migracao
    tem de recusar, nao convergir por cima."""
    erro = _module()["TemplateAssignmentHistoryError"]
    snap = copy.deepcopy(_snapshot())
    _com_coluna(snap, "unassigned_at", "datetime")
    _com_coluna(snap, "active_user_id", "bigint", gen="`user_id`")
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert "gerada" in str(exc.value)


def test_active_user_id_que_nao_e_gerada_falha():
    erro = _module()["TemplateAssignmentHistoryError"]
    snap = copy.deepcopy(_snapshot())
    _com_coluna(snap, "unassigned_at", "datetime")
    _com_coluna(snap, "active_user_id", "bigint", gen="")
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert "NAO e coluna gerada" in str(exc.value)


def test_active_user_id_sem_a_coluna_de_que_ela_deriva_falha():
    erro = _module()["TemplateAssignmentHistoryError"]
    snap = _com_coluna(
        copy.deepcopy(_snapshot()), "active_user_id", "bigint", gen="if(`unassigned_at` is null,`user_id`,NULL)"
    )
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert "unassigned_at" in str(exc.value)


def test_indice_com_forma_diferente_falha():
    erro = _module()["TemplateAssignmentHistoryError"]
    snap = _com_indice(copy.deepcopy(_snapshot()), "idx_wpta_template", ["user_id"], non_unique=1)
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert "idx_wpta_template" in str(exc.value)


def test_expressao_reescrita_pelo_mysql_converge_e_nao_falha():
    """O MySQL devolve a expressao com crases e sem os espacos que escrevemos.
    Isso e FORMATACAO, nao divergencia - a normalizacao tem de aceitar."""
    snap = _convergido(_snapshot())
    assert _plan(snap)[0] == []
    outro_formato = copy.deepcopy(snap)
    for c in outro_formato["columns"]:
        if c["col"] == "active_user_id":
            c["gen"] = "IF ( `unassigned_at`  IS  NULL , `user_id` , NULL )"
    assert _plan(outro_formato)[0] == []


# ---------------------------------------------------------------------------
# `down`
# ---------------------------------------------------------------------------


def test_down_desfaz_na_ordem_inversa():
    statements, _ = _plan_down(_convergido(_snapshot()))
    assert [sql for sql, _ in statements] == [
        "ALTER TABLE workout_plan_template_assignments DROP INDEX uq_wpta_active_user",
        "ALTER TABLE workout_plan_template_assignments DROP COLUMN active_user_id",
        "ALTER TABLE workout_plan_template_assignments ADD UNIQUE KEY uq_wpta_template_user (template_id, user_id)",
        "ALTER TABLE workout_plan_template_assignments DROP COLUMN unassigned_by_user_id, "
        "DROP COLUMN unassigned_at, DROP INDEX idx_wpta_template",
    ]


def test_down_sobre_banco_que_nunca_recebeu_a_049_nao_faz_nada():
    statements, _ = _plan_down(_snapshot())
    assert statements == []


def test_down_recusa_quando_o_historico_ja_tem_o_mesmo_par_duas_vezes():
    """O `down` recria `uq_wpta_template_user`. Se o usuario ja voltou para um
    Modelo que tinha largado, esse par aparece duas vezes e o UNIQUE seria
    impossivel - a recusa tem de vir ANTES do primeiro statement, com os pares
    nomeados."""
    erro = _module()["TemplateAssignmentHistoryError"]
    with pytest.raises(erro) as exc:
        _plan_down(_convergido(_snapshot()), duplicadas=[{"template_id": 9, "user_id": 1, "n": 2}])
    assert "template 9" in str(exc.value) and "user 1" in str(exc.value)
    assert "Nenhum statement executado" in str(exc.value)


# ---------------------------------------------------------------------------
# Higiene
# ---------------------------------------------------------------------------


def test_migracao_e_autocontida_e_compativel_com_o_data_api():
    source = MIGRATION_049.read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
    for sql, _params in _plan(_snapshot())[0]:
        assert ";" not in sql
        upper = sql.upper()
        for proibido in ("PREPARE", "EXECUTE", "DEALLOCATE", "DELIMITER", "SET @", "USE "):
            assert proibido not in upper


def test_o_numero_049_e_unico_no_diretorio():
    """O plano do Novo Dashboard (§14) reservava a 049 para `daily_checkins`;
    o M23 chegou antes e a F2.2 desloca para 050/051 - o plano foi atualizado
    junto. O que este teste protege e a unicidade, que o runner exige."""
    migracoes = m.discover_migrations(m.DEFAULT_MIGRATIONS_DIR)
    por_versao = {mig.version: mig.filename for mig in migracoes}
    assert por_versao[49] == MIGRATION_049.name
    assert [mig.version for mig in migracoes if mig.version == 49] == [49]
    assert 49 > m.BASELINE_MAX_VERSION
