"""Plano da migração 059 (vínculo da série com a técnica e com a âncora -
Fase 5, PR 2) contra o `information_schema` REAL da Aurora de produção.

Recorte composto, sem leitura nova da AWS:
- tabelas: `aurora_schema_2026_09_17_032_033.json` (lista completa, com
  `exercise_sets` e `advanced_techniques`);
- colunas/índices/FKs de `exercise_sets`: `aurora_schema_2026_09_14_026.json`
  (as colunas acrescentadas depois - `rpe_target_effective`, `safety_event` -
  não interferem no plano da 059);
- colunas de `advanced_techniques` (com collation): recorte 032_033.

Produção hoje NÃO tem as colunas -> 2 ADD COLUMN + 2 ADD KEY + 1 FK.
Estado convergido -> 0 statements. `down` -> o inverso.

Rodar: cd api && python -m pytest tests/unit/test_migration_059_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_059 = m.DEFAULT_MIGRATIONS_DIR / "059_exercise_sets_tecnica_e_ancora.py"
DATA = Path(__file__).resolve().parents[1] / "unit" / "data"

ADD_TECHNIQUE = (
    "ALTER TABLE exercise_sets ADD COLUMN technique_code VARCHAR(20) "
    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL"
)
ADD_ANCHOR = "ALTER TABLE exercise_sets ADD COLUMN anchor_set_number INT NULL"
ADD_IDX_TECHNIQUE = "ALTER TABLE exercise_sets ADD KEY idx_sets_technique_code (technique_code)"
ADD_IDX_ANCHOR = "ALTER TABLE exercise_sets ADD KEY idx_sets_exercise_anchor (exercise_id, anchor_set_number)"
ADD_FK = (
    "ALTER TABLE exercise_sets ADD CONSTRAINT fk_sets_technique FOREIGN KEY (technique_code) "
    "REFERENCES advanced_techniques(code)"
)
FULL_PLAN = [(s, None) for s in (ADD_TECHNIQUE, ADD_ANCHOR, ADD_IDX_TECHNIQUE, ADD_IDX_ANCHOR, ADD_FK)]


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _snapshot() -> dict:
    base = _load("aurora_schema_2026_09_17_032_033.json")
    sets = _load("aurora_schema_2026_09_14_026.json")
    snap = copy.deepcopy(base)
    snap["columns"] = [c for c in snap["columns"] if c["t"] != "exercise_sets"] + [
        dict(c, coll=c.get("coll")) for c in sets["columns"] if c["t"] == "exercise_sets"
    ]
    snap["indexes"] = [i for i in snap["indexes"] if i["t"] != "exercise_sets"] + [
        i for i in sets["indexes"] if i["t"] == "exercise_sets"
    ]
    snap["foreign_keys"] = [f for f in snap["foreign_keys"] if f["t"] != "exercise_sets"] + [
        f for f in sets["foreign_keys"] if f["t"] == "exercise_sets"
    ]
    return snap


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION_059).__globals__


class _RecordingDB(SafetySnapshotDB):
    def __init__(self, snapshot, dry_run: bool = False):
        super().__init__(snapshot, dry_run=dry_run)
        self.executed: list[tuple[str, tuple | None]] = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))


def _plan(snapshot, fn="plan", dry_run: bool = False):
    db = _RecordingDB(snapshot, dry_run=dry_run)
    statements = _module()[fn](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def _column(col: str, tipo: str, nulo: str = "YES", coll: str | None = None) -> dict:
    return {"t": "exercise_sets", "pos": 99, "col": col, "type": tipo, "nullable": nulo,
            "dflt": None, "ckey": "", "extra": "", "coll": coll}


def _convergido() -> dict:
    snap = _snapshot()
    snap["columns"] += [
        _column("technique_code", "varchar(20)", coll="utf8mb4_0900_ai_ci"),
        _column("anchor_set_number", "int(11)"),
    ]
    snap["indexes"] += [
        {"t": "exercise_sets", "idx": "idx_sets_technique_code", "seq": 1, "col": "technique_code", "non_unique": 1},
        {"t": "exercise_sets", "idx": "idx_sets_exercise_anchor", "seq": 1, "col": "exercise_id", "non_unique": 1},
        {"t": "exercise_sets", "idx": "idx_sets_exercise_anchor", "seq": 2, "col": "anchor_set_number", "non_unique": 1},
    ]
    snap["foreign_keys"].append(
        {"t": "exercise_sets", "fk": "fk_sets_technique", "col": "technique_code", "ref_t": "advanced_techniques",
         "ref_col": "code", "on_delete": "NO ACTION", "on_update": "NO ACTION"}
    )
    return snap


def test_o_recorte_tem_as_tabelas_e_advanced_techniques_code_e_pk_varchar20():
    snap = _snapshot()
    tabelas = {t["table_name"] for t in snap["tables"]}
    assert {"exercise_sets", "advanced_techniques"} <= tabelas
    code = next(c for c in snap["columns"] if c["t"] == "advanced_techniques" and c["col"] == "code")
    assert (code["type"], code["ckey"], code["coll"]) == ("varchar(20)", "PRI", "utf8mb4_0900_ai_ci")
    assert not any(c["t"] == "exercise_sets" and c["col"] in ("technique_code", "anchor_set_number")
                   for c in snap["columns"])


@pytest.mark.parametrize("dry_run", [False, True])
def test_producao_hoje_planeja_ddl_completo_somente_lendo_information_schema(dry_run):
    statements, db = _plan(_snapshot(), dry_run=dry_run)
    assert statements == FULL_PLAN
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed, "plan() é somente leitura"


def test_migracao_e_so_ddl_aditivo():
    sqls = " ".join(v for k, v in _module().items() if k.endswith("_SQL") and k.startswith("ADD")).upper()
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "PREPARE", "SET @", "DEFAULT", "NOT NULL", "AFTER"):
        assert proibido not in sqls


def test_reexecucao_no_estado_convergido_nao_escreve_nada():
    statements, db = _plan(_convergido())
    assert statements == []
    assert not db.executed


def test_estado_parcial_so_planeja_o_que_falta():
    snap = _convergido()
    snap["foreign_keys"] = [f for f in snap["foreign_keys"] if f["fk"] != "fk_sets_technique"]
    statements, _ = _plan(snap)
    assert statements == [(ADD_FK, None)]


def test_up_executa_exatamente_o_plano():
    db = _RecordingDB(_snapshot())
    _module()["up"](db)
    assert db.executed == FULL_PLAN


def test_down_e_o_inverso_real():
    statements, _ = _plan(_convergido(), fn="plan_down")
    assert [s for s, _ in statements] == [
        "ALTER TABLE exercise_sets DROP FOREIGN KEY fk_sets_technique",
        "ALTER TABLE exercise_sets DROP KEY idx_sets_technique_code",
        "ALTER TABLE exercise_sets DROP KEY idx_sets_exercise_anchor",
        "ALTER TABLE exercise_sets DROP COLUMN technique_code",
        "ALTER TABLE exercise_sets DROP COLUMN anchor_set_number",
    ]
    statements, _ = _plan(_snapshot(), fn="plan_down")
    assert statements == []


@pytest.mark.parametrize(
    "col, tipo, nulo, coll, trecho",
    [
        ("technique_code", "varchar(40)", "YES", "utf8mb4_0900_ai_ci", "esperado varchar(20)"),
        ("technique_code", "varchar(20)", "NO", "utf8mb4_0900_ai_ci", "IS_NULLABLE"),
        ("technique_code", "varchar(20)", "YES", "utf8mb4_unicode_ci", "collation"),
        ("anchor_set_number", "bigint", "YES", None, "esperado int"),
    ],
)
def test_coluna_em_outro_formato_falha_antes_de_escrever(col, tipo, nulo, coll, trecho):
    Erro = _module()["TechniqueLinkError"]
    snap = _snapshot()
    snap["columns"].append(_column(col, tipo, nulo, coll))
    db = _RecordingDB(snap)
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert trecho in str(exc.value)
    assert not db.executed


def _sem_catalogo() -> dict:
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != "advanced_techniques"]
    snap["columns"] = [c for c in snap["columns"] if c["t"] != "advanced_techniques"]
    return snap


def test_catalogo_ausente_falha_na_execucao_real_e_e_aceito_no_dry_run():
    Erro = _module()["TechniqueLinkError"]
    with pytest.raises(Erro, match="031"):
        _module()["plan"](_RecordingDB(_sem_catalogo()))
    statements, _ = _plan(_sem_catalogo(), dry_run=True)
    assert statements == FULL_PLAN
