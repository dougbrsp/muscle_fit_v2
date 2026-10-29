"""Plano da migracao 053 (aposentadoria de `exercise_zepp_mappings`, M13)
contra o `information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_19_038.json`
(SELECT somente leitura; lista COMPLETA das 53 tabelas do schema). Nenhuma
leitura nova da AWS foi feita - a 053 so pergunta quais tabelas existem e
conta as linhas do legado sem representacao canonica.

Dois fatos que este arquivo guarda, e que sao a JUSTIFICATIVA do M13:

1. **Producao nao tem nenhuma FK apontando para `exercise_zepp_mappings`.**
   Medido no unico recorte com as 57 FKs do schema INTEIRO
   (`aurora_schema_2026_09_14_028.json`). Por isso o M13 nao precisa criar FK
   nova - e por isso a restricao da 028 (lista fechada de FKs para
   `exercise_catalog`, que recusa qualquer outra) nao o impede.
2. A 053 **nao escreve em `exercise_catalog`**: o `SnapshotDB` recusa
   qualquer escrita, e o plano so devolve `CREATE TABLE`/`INSERT` na tabela
   de auditoria.

Rodar: cd api && python -m pytest tests/unit/test_migration_053_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.paridade_028 import SnapshotDB
from tests.support.paridade_028 import SNAPSHOT_PATH as SNAPSHOT_028_PATH

MIGRATION_053 = m.DEFAULT_MIGRATIONS_DIR / "053_aposentadoria_exercise_zepp_mappings.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_19_038.json"

TABLE = "exercise_zepp_mappings_retirement_audit"
LEGACY = "exercise_zepp_mappings"
DROP = f"DROP TABLE IF EXISTS {TABLE}"

# tipos como o information_schema do MySQL 8 os devolve.
AUDIT_COLUMNS = {
    "legacy_id": ("bigint unsigned", "NO"),
    "exercise_name": ("varchar(255)", "NO"),
    "amazfit_name": ("varchar(255)", "NO"),
    "legacy_source": ("varchar(160)", "NO"),
    "legacy_is_active": ("tinyint(1)", "NO"),
    "recorded_at": ("datetime", "NO"),
}


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION_053).__globals__


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _plan(snapshot, *, counts=None, fn="plan"):
    db = SnapshotDB(snapshot, counts=counts)
    statements = _module()[fn](db)
    return [(_norm(sql), params) for sql, params in statements], db


def _com_a_tabela(snapshot: dict, **overrides) -> dict:
    snap = copy.deepcopy(snapshot)
    snap["tables"].append({"table_name": TABLE, "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
    tipos = dict(AUDIT_COLUMNS)
    tipos.update(overrides)
    for pos, (col, (tipo, nulo)) in enumerate(tipos.items(), start=1):
        snap["columns"].append(
            {
                "t": TABLE,
                "pos": pos,
                "col": col,
                "type": tipo,
                "nullable": nulo,
                "dflt": None,
                "ckey": "PRI" if col == "legacy_id" else "",
                "extra": "",
                "coll": None,
            }
        )
    return snap


def _sem_tabelas(snapshot: dict, *nomes: str) -> dict:
    snap = copy.deepcopy(snapshot)
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] not in nomes]
    snap["columns"] = [c for c in snap["columns"] if c["t"] not in nomes]
    return snap


# ---------------------------------------------------------------------------
# O fato que sustenta o M13
# ---------------------------------------------------------------------------


def test_producao_nao_tem_nenhuma_fk_apontando_para_o_legado():
    """A unica leitura de producao com TODAS as FKs do schema (a da 028).

    Se um dia isto falhar, o `DROP TABLE` da etapa final do M13 deixou de ser
    seguro e alguem reintroduziu dependencia no legado."""
    snap = json.loads(SNAPSHOT_028_PATH.read_text(encoding="utf-8"))
    assert len(snap["foreign_keys"]) == 57
    apontam = [f for f in snap["foreign_keys"] if f["ref_t"] == LEGACY]
    assert apontam == []
    # As unicas FKs da tabela sao as que SAEM dela (nao prendem ninguem).
    saem = sorted(f["fk"] for f in snap["foreign_keys"] if f["t"] == LEGACY)
    assert saem == ["fk_zm_primary_group", "fk_zm_secondary_group"]


def test_o_recorte_e_da_aurora_tem_o_legado_e_nao_tem_a_auditoria():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    tabelas = {t["table_name"] for t in snap["tables"]}
    assert {LEGACY, "exercise_catalog", "exercise_legacy_id_map", "exercise_aliases"} <= tabelas
    assert TABLE not in tabelas


# ---------------------------------------------------------------------------
# Plano
# ---------------------------------------------------------------------------


def test_producao_sem_orfao_executa_um_unico_create_table():
    statements, db = _plan(_snapshot(), counts={LEGACY: 0})
    create = _norm(_module()["CREATE_SQL"])
    assert statements == [(create, None)]
    # A contagem foi mesmo feita (a 053 MEDE; nao presume).
    assert any(q.startswith("SELECT COUNT(*)") for q in db.queries)


def test_producao_com_orfao_cria_a_tabela_e_registra_numa_unica_passada():
    statements, _db = _plan(_snapshot(), counts={LEGACY: 14})
    assert [sql for sql, _ in statements] == [
        _norm(_module()["CREATE_SQL"]),
        _norm(_module()["INSERT_SQL"]),
    ]
    assert all(params is None for _sql, params in statements)


def test_o_registro_nunca_toca_o_catalogo_canonico():
    """A etapa 1 do M13 MEDE. Reinserir linha legada em `exercise_catalog`
    (10 FKs filhas, alimenta a classificacao de todo treino) e decisao humana,
    porque parte da diferenca 93/79 e consolidacao deliberada de duplicata."""
    statements, _db = _plan(_snapshot(), counts={LEGACY: 14})
    alvos = " ".join(sql for sql, _ in statements)
    assert "INSERT INTO exercise_catalog" not in alvos
    assert "UPDATE exercise_catalog" not in alvos
    assert f"INSERT INTO {TABLE}" in alvos
    # Nenhum DDL em tabela que nao seja a de auditoria.
    assert "ALTER TABLE" not in alvos
    assert "FOREIGN KEY" not in alvos


def test_o_predicado_de_representada_usa_os_quatro_caminhos():
    insert = _norm(_module()["INSERT_SQL"])
    assert "exercise_catalog c WHERE c.id = z.id" in insert
    assert "exercise_legacy_id_map m WHERE m.old_exercise_id = z.id" in insert
    assert "exercise_catalog cn WHERE cn.name = z.exercise_name" in insert
    assert "exercise_aliases a WHERE a.alias = z.exercise_name" in insert
    # Reexecutavel: nao registra duas vezes a mesma linha.
    assert f"{TABLE} t WHERE t.legacy_id = z.id" in insert


def test_convergido_nao_executa_nada():
    statements, _db = _plan(_com_a_tabela(_snapshot()), counts={LEGACY: 0})
    assert statements == []


def test_convergido_com_linha_nova_no_legado_registra_so_o_que_falta():
    statements, _db = _plan(_com_a_tabela(_snapshot()), counts={LEGACY: 1})
    assert statements == [(_norm(_module()["INSERT_SQL"]), None)]


def test_sem_o_legado_so_cria_a_auditoria_e_nao_conta_nada():
    """Estado DEPOIS do PR que remover a tabela (e de um banco em dry-run, que
    planeja a 053 sobre um schema onde a 001 ainda nao rodou). Sem `counts`, o
    `SnapshotDB` transforma qualquer contagem em erro - a ausencia de erro e a
    prova de que nenhuma leitura de dado aconteceu."""
    statements, db = _plan(_sem_tabelas(_snapshot(), LEGACY))
    assert statements == [(_norm(_module()["CREATE_SQL"]), None)]
    assert not any(q.startswith("SELECT COUNT(*)") for q in db.queries)


@pytest.mark.parametrize("ausente", ["exercise_catalog", "exercise_legacy_id_map", "exercise_aliases"])
def test_faltando_qualquer_fonte_do_predicado_nao_mede(ausente):
    statements, db = _plan(_sem_tabelas(_snapshot(), ausente))
    assert statements == [(_norm(_module()["CREATE_SQL"]), None)]
    assert not any(q.startswith("SELECT COUNT(*)") for q in db.queries)


# ---------------------------------------------------------------------------
# Recusa e reversao
# ---------------------------------------------------------------------------


def test_coluna_com_tipo_diferente_falha_antes_de_qualquer_statement():
    snap = _com_a_tabela(_snapshot(), legacy_id=("bigint", "NO"))
    erro = _module()["ZeppRetirementAuditError"]
    with pytest.raises(erro) as exc:
        _plan(snap, counts={LEGACY: 0})
    assert "legacy_id" in str(exc.value)
    assert "Nenhum statement executado" in str(exc.value)


def test_coluna_ausente_falha():
    snap = _com_a_tabela(_snapshot())
    snap["columns"] = [c for c in snap["columns"] if not (c["t"] == TABLE and c["col"] == "recorded_at")]
    with pytest.raises(_module()["ZeppRetirementAuditError"]):
        _plan(snap, counts={LEGACY: 0})


def test_engine_diferente_de_innodb_falha():
    snap = _com_a_tabela(_snapshot())
    for t in snap["tables"]:
        if t["table_name"] == TABLE:
            t["engine"] = "MyISAM"
    with pytest.raises(_module()["ZeppRetirementAuditError"]):
        _plan(snap, counts={LEGACY: 0})


def test_down_derruba_a_tabela_quando_ela_existe():
    statements, _db = _plan(_com_a_tabela(_snapshot()), fn="plan_down")
    assert statements == [(DROP, None)]


def test_down_em_base_sem_a_tabela_nao_faz_nada():
    statements, _db = _plan(_snapshot(), fn="plan_down")
    assert statements == []
