"""Plano da migracao 063 (M55: alvo da tecnica da F4 no estado do grupo)
contra o `information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_18_037.json`
(SELECT somente leitura, ja com a 036 aplicada). `progression_group_states`
nao mudou desde a 036 - nenhuma migracao posterior a toca -, entao o recorte
e o formato de producao hoje.

Producao NAO tem as colunas -> 2 `ALTER TABLE ... ADD COLUMN`. Estado
convergido -> 0 statements. `down` -> 2 `DROP COLUMN`. Backfill: nenhum DML
(todas as linhas ficam NULL; regra no docstring da migracao).

Rodar: cd api && python -m pytest tests/unit/test_migration_063_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_063 = m.DEFAULT_MIGRATIONS_DIR / "063_progression_technique_target.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_18_037.json"

TABLE = "progression_group_states"
EXERCISE, EQUIPMENT = "technique_target_exercise_id", "technique_target_equipment_id"
ADD_EXERCISE = f"ALTER TABLE {TABLE} ADD COLUMN {EXERCISE} BIGINT UNSIGNED NULL"
ADD_EQUIPMENT = f"ALTER TABLE {TABLE} ADD COLUMN {EQUIPMENT} BIGINT NULL"
DROP_EXERCISE = f"ALTER TABLE {TABLE} DROP COLUMN {EXERCISE}"
DROP_EQUIPMENT = f"ALTER TABLE {TABLE} DROP COLUMN {EQUIPMENT}"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION_063).__globals__


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


def _com_colunas(snapshot: dict, *colunas: tuple[str, str], nulo: str = "YES") -> dict:
    snap = copy.deepcopy(snapshot)
    for pos, (nome, tipo) in enumerate(colunas, start=98):
        snap["columns"].append(
            {"t": TABLE, "pos": pos, "col": nome, "type": tipo, "nullable": nulo,
             "dflt": None, "ckey": "", "extra": "", "coll": None}
        )
    return snap


CONVERGIDO = ((EXERCISE, "bigint unsigned"), (EQUIPMENT, "bigint"))


def test_o_recorte_e_da_aurora_e_tem_a_tabela_sem_as_colunas():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    assert TABLE in {t["table_name"] for t in snap["tables"]}
    assert not any(c["t"] == TABLE and c["col"] in (EXERCISE, EQUIPMENT) for c in snap["columns"])


def test_tipos_iguais_aos_da_chave_da_trilha_em_producao():
    """O alvo e o par (exercise_id, equipment_id) de `progression_tracks`."""
    tipos = {c["col"]: c["type"] for c in _snapshot()["columns"] if c["t"] == "progression_tracks"}
    esperado = _module()["EXPECTED_TYPES"]
    assert tipos["exercise_id"].replace("(20)", "") == esperado[EXERCISE]
    assert tipos["equipment_id"].replace("(20)", "") == esperado[EQUIPMENT]


@pytest.mark.parametrize("dry_run", [False, True])
def test_producao_hoje_executa_dois_add_column_inclusive_em_dry_run(dry_run):
    statements, db = _plan(_snapshot(), dry_run=dry_run)
    assert statements == [(ADD_EXERCISE, None), (ADD_EQUIPMENT, None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed, "plan() e somente leitura"


def test_backfill_nao_escreve_linhas_nem_cria_fk():
    """Regra do backfill: todas as linhas ficam NULL (alvo desconhecido).
    Sem FK: a 028 recusa FK nova para `exercise_catalog` e a 036 confere as
    FKs de `progression_group_states` por igualdade."""
    sqls = " ".join(v for k, v in _module().items() if k.endswith("_SQL")).upper()
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "PREPARE", "SET @", "USE ", "FOREIGN KEY", "REFERENCES",
                     "DEFAULT", "AFTER", ";"):
        assert proibido not in sqls


def test_reexecucao_no_estado_convergido_nao_escreve_nada():
    statements, db = _plan(_com_colunas(_snapshot(), (EXERCISE, "bigint(20) unsigned"), (EQUIPMENT, "bigint(20)")))
    assert statements == []
    assert not db.executed


def test_estado_parcial_so_adiciona_a_coluna_que_falta():
    statements, _ = _plan(_com_colunas(_snapshot(), (EXERCISE, "bigint unsigned")))
    assert statements == [(ADD_EQUIPMENT, None)]


def test_up_executa_exatamente_o_plano():
    db = _RecordingDB(_snapshot())
    _module()["up"](db)
    assert db.executed == [(ADD_EXERCISE, None), (ADD_EQUIPMENT, None)]


def test_down_e_o_inverso_real():
    statements, _ = _plan(_com_colunas(_snapshot(), *CONVERGIDO), fn="plan_down")
    assert statements == [(DROP_EQUIPMENT, None), (DROP_EXERCISE, None)]
    statements, _ = _plan(_snapshot(), fn="plan_down")
    assert statements == []
    db = _RecordingDB(_com_colunas(_snapshot(), *CONVERGIDO))
    _module()["down"](db)
    assert db.executed == [(DROP_EQUIPMENT, None), (DROP_EXERCISE, None)]


@pytest.mark.parametrize(
    "coluna, tipo, nulo, trecho",
    [
        (EXERCISE, "bigint", "YES", "esperado bigint unsigned"),
        (EQUIPMENT, "int", "YES", "esperado bigint"),
        (EXERCISE, "bigint unsigned", "NO", "IS_NULLABLE"),
    ],
)
def test_coluna_em_outro_formato_falha_antes_de_escrever(coluna, tipo, nulo, trecho):
    Erro = _module()["ProgressionTechniqueTargetError"]
    db = _RecordingDB(_com_colunas(_snapshot(), (coluna, tipo), nulo=nulo))
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert trecho in str(exc.value)
    assert not db.executed


def test_tabela_ausente_falha_antes_do_alter():
    Erro = _module()["ProgressionTechniqueTargetError"]
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != TABLE]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert f"{TABLE} nao existe" in str(exc.value)


def test_dry_run_do_lote_com_a_036_pendente_planeja_os_dois_add_column():
    """Banco em que a 036 ainda esta pendente no mesmo lote: o dry-run mostra
    o plano (como na 041/042/057/059); so a execucao real exige a tabela."""
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != TABLE]
    snap["columns"] = [c for c in snap["columns"] if c["t"] != TABLE]
    statements, db = _plan(snap, dry_run=True)
    assert statements == [(ADD_EXERCISE, None), (ADD_EQUIPMENT, None)]
    assert not db.executed
