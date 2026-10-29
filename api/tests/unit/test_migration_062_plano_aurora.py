"""Plano da migracao 062 (grade configuravel do editor de Modelo) contra o
`information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_17_035.json`
(SELECT somente leitura). Nenhuma leitura nova da AWS foi feita - a 062 so
pergunta se `workout_plan_templates` existe e se ja tem `grid_rows`/`grid_cols`.

Producao hoje NAO tem as colunas -> 2 `ALTER TABLE ... ADD COLUMN`. Estado
convergido -> 0 statements. `down` -> 2 `DROP COLUMN`.

Rodar: cd api && python -m pytest tests/unit/test_migration_062_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_062 = m.DEFAULT_MIGRATIONS_DIR / "062_template_grid_layout.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"

TABLE = "workout_plan_templates"
ADD_ROWS = f"ALTER TABLE {TABLE} ADD COLUMN grid_rows TINYINT NULL"
ADD_COLS = f"ALTER TABLE {TABLE} ADD COLUMN grid_cols TINYINT NULL"
DROP_ROWS = f"ALTER TABLE {TABLE} DROP COLUMN grid_rows"
DROP_COLS = f"ALTER TABLE {TABLE} DROP COLUMN grid_cols"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION_062).__globals__


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


def _com_colunas(snapshot: dict, *nomes: str, tipo: str = "tinyint", nulo: str = "YES") -> dict:
    snap = copy.deepcopy(snapshot)
    for pos, nome in enumerate(nomes, start=98):
        snap["columns"].append(
            {
                "t": TABLE,
                "pos": pos,
                "col": nome,
                "type": tipo,
                "nullable": nulo,
                "dflt": None,
                "ckey": "",
                "extra": "",
                "coll": None,
            }
        )
    return snap


def test_o_recorte_e_da_aurora_e_tem_a_tabela_sem_as_colunas():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    assert TABLE in {t["table_name"] for t in snap["tables"]}
    assert not any(c["t"] == TABLE and c["col"] in ("grid_rows", "grid_cols") for c in snap["columns"])


@pytest.mark.parametrize("dry_run", [False, True])
def test_producao_hoje_executa_dois_add_column_inclusive_em_dry_run(dry_run):
    statements, db = _plan(_snapshot(), dry_run=dry_run)
    assert statements == [(ADD_ROWS, None), (ADD_COLS, None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed, "plan() e somente leitura"


def test_migracao_e_aditiva_nullable_sem_default_nem_dml():
    """NULL = grade automatica: nenhum Modelo existente muda."""
    sqls = " ".join(v for k, v in _module().items() if k.endswith("_SQL")).upper()
    assert "DEFAULT" not in sqls
    assert "AFTER" not in sqls
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "PREPARE", "SET @", "USE "):
        assert proibido not in sqls


def test_reexecucao_no_estado_convergido_nao_escreve_nada():
    statements, db = _plan(_com_colunas(_snapshot(), "grid_rows", "grid_cols", tipo="tinyint(4)"))
    assert statements == []
    assert not db.executed


def test_estado_parcial_so_adiciona_a_coluna_que_falta():
    statements, _ = _plan(_com_colunas(_snapshot(), "grid_rows"))
    assert statements == [(ADD_COLS, None)]


def test_up_executa_exatamente_o_plano():
    db = _RecordingDB(_snapshot())
    _module()["up"](db)
    assert db.executed == [(ADD_ROWS, None), (ADD_COLS, None)]


def test_down_e_o_inverso_real():
    statements, _ = _plan(_com_colunas(_snapshot(), "grid_rows", "grid_cols"), fn="plan_down")
    assert statements == [(DROP_COLS, None), (DROP_ROWS, None)]
    statements, _ = _plan(_snapshot(), fn="plan_down")
    assert statements == []
    db = _RecordingDB(_com_colunas(_snapshot(), "grid_rows", "grid_cols"))
    _module()["down"](db)
    assert db.executed == [(DROP_COLS, None), (DROP_ROWS, None)]


@pytest.mark.parametrize(
    "tipo, nulo, trecho",
    [("int(11)", "YES", "esperado tinyint"), ("tinyint", "NO", "IS_NULLABLE")],
)
def test_coluna_em_outro_formato_falha_antes_de_escrever(tipo, nulo, trecho):
    Erro = _module()["TemplateGridLayoutError"]
    db = _RecordingDB(_com_colunas(_snapshot(), "grid_rows", tipo=tipo, nulo=nulo))
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert trecho in str(exc.value)
    assert not db.executed


def test_tabela_ausente_falha_antes_do_alter():
    Erro = _module()["TemplateGridLayoutError"]
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != TABLE]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert f"{TABLE} nao existe" in str(exc.value)
