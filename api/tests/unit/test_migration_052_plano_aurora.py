"""Plano da migracao 052 (vigencia das Metas de Ativacao - M12) contra um
recorte REAL do `information_schema`.

Recorte: `tests/unit/data/schema_dev_2026_09_26_052.json` (ver `_origem`),
lido do MySQL 8.0.46 do DEV em 2026-09-26. O recorte veio do DEV e nao da
Aurora porque nenhuma leitura nova da AWS foi feita (nem podia ser:
infraestrutura so por PR) e porque `training_type_muscle_group_targets` nasce
do baseline `010_training_types.sql` (`schema_migrations` 33), aplicado
identico nos dois ambientes e nunca tocado por migracao `.py` posterior - o
teste `test_o_recorte_descreve_a_tabela_do_baseline_010` amarra as duas
pontas comparando o recorte com o DDL do arquivo. Mesmo precedente de
`schema_dev_2026_09_24_049.json` (migracao 049).

Producao hoje: sem as colunas de vigencia, com o UNIQUE antigo -> 4
statements. Estado convergido -> 0. E, diferente da 049, NENHUM deles escreve
dado: a 052 nao tem backfill, porque `NULL` ja e a resposta certa para as
linhas que existem.

Rodar: cd api && python -m pytest tests/unit/test_migration_052_plano_aurora.py
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

MIGRATION_052 = m.DEFAULT_MIGRATIONS_DIR / "052_training_type_target_versioning.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "schema_dev_2026_09_26_052.json"
BASELINE_010 = m.DEFAULT_MIGRATIONS_DIR / "010_training_types.sql"

TABELA = "training_type_muscle_group_targets"

ADD_COLUMNS = (
    "ALTER TABLE training_type_muscle_group_targets "
    "ADD COLUMN effective_from DATETIME NULL, "
    "ADD COLUMN superseded_at DATETIME NULL, "
    "ADD KEY idx_ttmgt_type (training_type_id)"
)
DROP_UNIQUE_ANTIGO = "ALTER TABLE training_type_muscle_group_targets DROP INDEX uq_ttmgt_type_group"
ADD_CURRENT_TYPE = (
    "ALTER TABLE training_type_muscle_group_targets ADD COLUMN current_training_type_id BIGINT "
    "GENERATED ALWAYS AS (IF(superseded_at IS NULL, training_type_id, NULL)) VIRTUAL"
)
ADD_CURRENT_UNIQUE = (
    "ALTER TABLE training_type_muscle_group_targets "
    "ADD UNIQUE KEY uq_ttmgt_current (current_training_type_id, muscle_group_id)"
)
RESTORE_UNIQUE_ANTIGO = (
    "ALTER TABLE training_type_muscle_group_targets "
    "ADD UNIQUE KEY uq_ttmgt_type_group (training_type_id, muscle_group_id)"
)
DROP_COLUMNS = (
    "ALTER TABLE training_type_muscle_group_targets "
    "DROP COLUMN superseded_at, DROP COLUMN effective_from, DROP INDEX idx_ttmgt_type"
)


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria uma classe de erro nova a cada
    # chamada e `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_052).__globals__


class _DB052(SnapshotDB):
    """`SnapshotDB` + os tres campos que a 052 le e a base nao serve:
    `GENERATION_EXPRESSION` (a coluna gerada), `SEQ_IN_INDEX` (ordem das
    colunas do UNIQUE composto) e as duplicatas de `plan_down`."""

    def __init__(self, snapshot, duplicadas=None):
        super().__init__(snapshot)
        self.duplicadas = duplicadas or []
        self.dry_run = False

    def query(self, sql, params=None):
        if "GROUP BY training_type_id, muscle_group_id" in sql:
            # Consulta de DADOS, so do `plan_down`. O `plan` nunca chega aqui -
            # e `test_o_plano_so_le_information_schema` prova isso.
            return list(self.duplicadas)
        rows = super().query(sql, params)
        if "information_schema.COLUMNS" in sql:
            geradas = {(c["t"], c["col"]): c.get("gen", "") for c in self.snap["columns"]}
            for row in rows:
                row["GENERATION_EXPRESSION"] = geradas.get((row["TABLE_NAME"], row["COLUMN_NAME"]), "")
        if "information_schema.STATISTICS" in sql:
            seqs = {(i["t"], i["idx"], i["col"]): i["seq"] for i in self.snap["indexes"]}
            for row in rows:
                row["SEQ_IN_INDEX"] = seqs[(row["TABLE_NAME"], row["INDEX_NAME"], row["COLUMN_NAME"])]
        return rows

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no snapshot: {sql}")


def _plan(snapshot, **kwargs):
    db = _DB052(snapshot, **kwargs)
    return [(" ".join(sql.split()), params) for sql, params in _module()["plan"](db)], db


def _plan_down(snapshot, **kwargs):
    db = _DB052(snapshot, **kwargs)
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
    """Recorte com a 052 JA aplicada (estado pos-deploy)."""
    snap = copy.deepcopy(snapshot)
    _com_coluna(snap, "effective_from", "datetime")
    _com_coluna(snap, "superseded_at", "datetime")
    _com_coluna(
        snap, "current_training_type_id", "bigint",
        gen="if(`superseded_at` is null,`training_type_id`,NULL)",
    )
    _sem_indice(snap, "uq_ttmgt_type_group")
    _com_indice(snap, "idx_ttmgt_type", ["training_type_id"], non_unique=1)
    _com_indice(
        snap, "uq_ttmgt_current", ["current_training_type_id", "muscle_group_id"], non_unique=0
    )
    return snap


# ---------------------------------------------------------------------------
# O recorte
# ---------------------------------------------------------------------------


def test_o_recorte_e_do_dev_na_versao_49_e_a_tabela_ainda_nao_tem_vigencia():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    assert snap["versao"][0]["schema_migration_files"] == 49
    engines = {t["table_name"]: t["engine"] for t in snap["tables"]}
    assert engines[TABELA] == "InnoDB"
    colunas = {c["col"] for c in snap["columns"] if c["t"] == TABELA}
    assert colunas == {
        "id", "training_type_id", "muscle_group_id",
        "min_weekly_sets", "target_weekly_sets", "max_weekly_sets",
    }
    indices = {i["idx"] for i in snap["indexes"] if i["t"] == TABELA}
    assert "uq_ttmgt_type_group" in indices


def test_o_recorte_descreve_a_tabela_do_baseline_010():
    """A ponte entre "li o DEV" e "isto tambem e producao": o recorte tem de
    bater com o `CREATE TABLE` do baseline, o unico lugar em que esta tabela
    foi definida e que rodou igual nos dois ambientes."""
    ddl = BASELINE_010.read_text(encoding="utf-8")
    bloco = re.search(rf"CREATE TABLE IF NOT EXISTS {TABELA} \((.*?)\n\) ENGINE=InnoDB", ddl, re.DOTALL)
    assert bloco, "o baseline 010 precisa continuar definindo a tabela"
    corpo = bloco.group(1)
    for coluna in ("id", "training_type_id", "muscle_group_id", "min_weekly_sets",
                   "target_weekly_sets", "max_weekly_sets"):
        assert re.search(rf"^\s*{coluna}\s", corpo, re.MULTILINE), coluna
    assert "UNIQUE KEY uq_ttmgt_type_group (training_type_id, muscle_group_id)" in corpo
    # E o inverso: nada de vigencia no baseline, senao a 052 seria redundante.
    assert "superseded_at" not in corpo


def test_o_unique_antigo_era_o_unico_indice_que_apoiava_a_fk_do_tipo():
    """Por que `idx_ttmgt_type` entra ANTES do DROP: sem ele, o InnoDB recusa
    remover o ultimo indice que atende `fk_ttmgt_type` (errno 150) e a
    migracao morreria no meio. O UNIQUE NOVO nao serve de apoio - ele comeca
    por `current_training_type_id`, que e outra coluna."""
    snap = _snapshot()
    fks = {f["fk"]: f for f in snap["foreign_keys"] if f["t"] == TABELA}
    assert fks["fk_ttmgt_type"]["col"] == "training_type_id"
    com_tipo_na_frente = {
        i["idx"] for i in snap["indexes"]
        if i["t"] == TABELA and i["seq"] == 1 and i["col"] == "training_type_id"
    }
    assert com_tipo_na_frente == {"uq_ttmgt_type_group"}


# ---------------------------------------------------------------------------
# O plano
# ---------------------------------------------------------------------------


def test_producao_hoje_executa_quatro_statements_nesta_ordem():
    sqls = [sql for sql, _ in _plan(_snapshot())[0]]
    assert sqls == [ADD_COLUMNS, DROP_UNIQUE_ANTIGO, ADD_CURRENT_TYPE, ADD_CURRENT_UNIQUE]


def test_a_052_nao_tem_backfill_nenhum():
    """A decisao central do M12: `NULL` ja e a resposta certa para as linhas
    que existem (vigentes, desde sempre). Um UPDATE aqui estaria INVENTANDO
    uma data de inicio - e tudo que fosse anterior a ela ficaria sem meta."""
    sqls = [sql for sql, _ in _plan(_snapshot())[0]]
    assert all(sql.startswith("ALTER TABLE") for sql in sqls)
    assert not any(sql.startswith(("UPDATE", "INSERT", "DELETE")) for sql in sqls)


def test_o_unique_novo_entra_depois_da_coluna_gerada_de_que_ele_depende():
    sqls = [sql for sql, _ in _plan(_snapshot())[0]]
    assert sqls.index(ADD_CURRENT_UNIQUE) > sqls.index(ADD_CURRENT_TYPE)


def test_o_indice_de_apoio_da_fk_entra_antes_do_drop_do_unique_antigo():
    sqls = [sql for sql, _ in _plan(_snapshot())[0]]
    assert sqls.index(ADD_COLUMNS) < sqls.index(DROP_UNIQUE_ANTIGO)
    assert "ADD KEY idx_ttmgt_type (training_type_id)" in ADD_COLUMNS


def test_o_plano_so_le_information_schema():
    _, db = _plan(_snapshot())
    assert db.queries
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)


def test_o_plano_so_olha_a_tabela_de_metas():
    """A 052 nao adiciona FK nenhuma, entao nao depende de `training_types`
    nem de `muscle_groups` - e por isso nao depende da ordem em relacao a
    nenhuma outra migracao do lote (053/054 rodam em paralelo no mesmo
    deploy)."""
    _, db = _plan(_snapshot())
    assert db.queries
    assert all(TABELA in q or "TABLE_NAME IN (%s)" in q for q in db.queries)


def test_estado_convergido_nao_executa_nada():
    assert _plan(_convergido(_snapshot()))[0] == []


def test_reexecucao_apos_aplicar_e_zero_statements():
    snap = _snapshot()
    assert len(_plan(snap)[0]) == 4
    assert _plan(_convergido(snap))[0] == []


def test_estado_parcial_executa_so_o_que_falta():
    """Aplicacao interrompida depois do DROP do UNIQUE antigo: as colunas ja
    existem e o indice de apoio tambem. O plano tem de pedir SO os dois
    ultimos."""
    snap = copy.deepcopy(_snapshot())
    _com_coluna(snap, "effective_from", "datetime")
    _com_coluna(snap, "superseded_at", "datetime")
    _com_indice(snap, "idx_ttmgt_type", ["training_type_id"], non_unique=1)
    _sem_indice(snap, "uq_ttmgt_type_group")
    assert [sql for sql, _ in _plan(snap)[0]] == [ADD_CURRENT_TYPE, ADD_CURRENT_UNIQUE]


def test_estado_parcial_com_so_uma_das_colunas_pede_a_outra_isoladamente():
    snap = copy.deepcopy(_snapshot())
    _com_coluna(snap, "effective_from", "datetime")
    sqls = [sql for sql, _ in _plan(snap)[0]]
    assert "ALTER TABLE training_type_muscle_group_targets ADD COLUMN superseded_at DATETIME NULL" in sqls
    assert "ALTER TABLE training_type_muscle_group_targets ADD KEY idx_ttmgt_type (training_type_id)" in sqls
    assert ADD_COLUMNS not in sqls


# ---------------------------------------------------------------------------
# Recusas
# ---------------------------------------------------------------------------


def test_tabela_ausente_ou_com_engine_errada_falha():
    erro = _module()["TrainingTypeTargetVersioningError"]

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
        ("effective_from", "date", "YES"),
        ("effective_from", "datetime", "NO"),
        ("superseded_at", "timestamp", "YES"),
        ("superseded_at", "datetime", "NO"),
    ],
)
def test_coluna_de_vigencia_divergente_falha_antes_de_qualquer_statement(col, tipo, nullable):
    erro = _module()["TrainingTypeTargetVersioningError"]
    snap = _com_coluna(copy.deepcopy(_snapshot()), col, tipo, nullable=nullable)
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert "Nenhum statement executado" in str(exc.value)


def test_coluna_gerada_com_outra_expressao_falha():
    """A coluna gerada E a invariante. Se alguem a recriou com outra regra, o
    UNIQUE passa a garantir outra coisa - e a migracao tem de recusar, nao
    convergir por cima."""
    erro = _module()["TrainingTypeTargetVersioningError"]
    snap = copy.deepcopy(_snapshot())
    _com_coluna(snap, "effective_from", "datetime")
    _com_coluna(snap, "superseded_at", "datetime")
    _com_coluna(snap, "current_training_type_id", "bigint", gen="`training_type_id`")
    with pytest.raises(erro):
        _plan(snap)


def test_coluna_gerada_que_nao_e_gerada_falha():
    erro = _module()["TrainingTypeTargetVersioningError"]
    snap = copy.deepcopy(_snapshot())
    _com_coluna(snap, "effective_from", "datetime")
    _com_coluna(snap, "superseded_at", "datetime")
    _com_coluna(snap, "current_training_type_id", "bigint", gen="")
    with pytest.raises(erro):
        _plan(snap)


def test_a_expressao_gerada_e_comparada_ignorando_a_reescrita_do_mysql():
    """O MySQL devolve a expressao com crases e sem espacos - o mesmo sentido,
    outra string. Compara-la crua faria o estado convergido parecer
    divergente e a migracao falharia em toda reexecucao."""
    assert _plan(_convergido(_snapshot()))[0] == []


def test_coluna_gerada_sem_a_coluna_de_que_deriva_falha():
    erro = _module()["TrainingTypeTargetVersioningError"]
    snap = copy.deepcopy(_snapshot())
    _com_coluna(snap, "current_training_type_id", "bigint", gen="if(`superseded_at` is null,`training_type_id`,NULL)")
    with pytest.raises(erro):
        _plan(snap)


def test_unique_novo_cobrindo_outras_colunas_falha():
    erro = _module()["TrainingTypeTargetVersioningError"]
    snap = _convergido(_snapshot())
    _sem_indice(snap, "uq_ttmgt_current")
    _com_indice(snap, "uq_ttmgt_current", ["current_training_type_id"], non_unique=0)
    with pytest.raises(erro):
        _plan(snap)


# ---------------------------------------------------------------------------
# `down`
# ---------------------------------------------------------------------------


def test_down_desfaz_os_quatro_statements_na_ordem_inversa():
    sqls = [sql for sql, _ in _plan_down(_convergido(_snapshot()))[0]]
    assert sqls == [
        "ALTER TABLE training_type_muscle_group_targets DROP INDEX uq_ttmgt_current",
        "ALTER TABLE training_type_muscle_group_targets DROP COLUMN current_training_type_id",
        RESTORE_UNIQUE_ANTIGO,
        DROP_COLUMNS,
    ]


def test_down_sobre_um_banco_que_nunca_aplicou_a_052_nao_faz_nada():
    assert _plan_down(_snapshot())[0] == []


def test_down_para_antes_do_primeiro_statement_se_ja_houver_historico():
    """Reverter e possivel enquanto NENHUMA versao nova tiver sido escrita: com
    o mesmo par (tipo, grupo) duas vezes, recriar `uq_ttmgt_type_group` falha -
    e deve falhar, porque nao existe estado antigo equivalente para aquele
    historico."""
    erro = _module()["TrainingTypeTargetVersioningError"]
    with pytest.raises(erro) as exc:
        _plan_down(
            _convergido(_snapshot()),
            duplicadas=[{"training_type_id": 2, "muscle_group_id": 7, "n": 2}],
        )
    assert "tipo 2" in str(exc.value) and "grupo 7" in str(exc.value)
    assert "Nenhum statement executado" in str(exc.value)


def test_up_e_down_voltam_ao_mesmo_conjunto_de_indices():
    """Equivalencia de FORMA: o que o `plan` cria, o `plan_down` remove, e o
    que ele remove, o `plan_down` recria."""
    criados = {sql for sql, _ in _plan(_snapshot())[0]}
    removidos = " ".join(sql for sql, _ in _plan_down(_convergido(_snapshot()))[0])
    for nome in ("idx_ttmgt_type", "uq_ttmgt_current", "current_training_type_id",
                 "effective_from", "superseded_at"):
        assert any(nome in sql for sql in criados), nome
        assert nome in removidos, nome
    assert "uq_ttmgt_type_group" in removidos
