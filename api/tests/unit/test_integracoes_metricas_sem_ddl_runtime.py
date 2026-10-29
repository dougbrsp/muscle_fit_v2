"""Grupo B (integracoes, metricas, relatorios): guarda estatica + plano da 027
contra o schema REAL da Aurora de producao (snapshot de information_schema,
2026-09-14). A prova comportamental, nos dois backends, esta em
tests/integration/dataapi/test_integracoes_metricas_sem_ddl_paridade.py e
tests/integration/dataapi/test_migration_027_integracoes_metricas_schema.py."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from app import migrate as m
from app.schema_guards import SCHEMA_GUARD_MODULES
from tests.support import grupo_b_schema as gb

APP_DIR = Path(__file__).resolve().parents[2] / "app"
GRUPO_B_MODULES = (
    "devices",
    "exercise_mapping_catalog",
    "gyms",
    "intervals_integration",
    "effort_metrics",
    "reports",
    "sleep_metrics",
)
DDL_RE = re.compile(
    r"\b(CREATE\s+(TABLE|INDEX|UNIQUE)|ALTER\s+TABLE|DROP\s+(TABLE|INDEX|COLUMN|FOREIGN|PRIMARY))\b", re.IGNORECASE
)


@pytest.mark.parametrize("module", GRUPO_B_MODULES)
def test_modulo_do_grupo_b_sem_ddl_nem_guard(module):
    source = (APP_DIR / f"{module}.py").read_text(encoding="utf-8")
    assert DDL_RE.findall(source) == []
    assert "information_schema" not in source.lower()
    assert "_ensure_schema" not in source
    assert "_schema_done" not in source and "_schema_ready" not in source


@pytest.mark.parametrize("module", GRUPO_B_MODULES)
def test_modulo_do_grupo_b_fora_dos_guards_de_dev(module):
    assert module not in SCHEMA_GUARD_MODULES


def test_migracao_027_versionada_e_sem_comandos_de_sessao():
    names = [mig.filename for mig in m.discover_migrations(m.DEFAULT_MIGRATIONS_DIR)]
    assert gb.MIGRATION_FILENAME in names
    source = gb.MIGRATION_PATH.read_text(encoding="utf-8")
    assert not re.search(r"[\"']\s*(PREPARE|EXECUTE|DEALLOCATE|DELIMITER|USE\s|SET\s+@)", source, re.IGNORECASE)
    # Uma migracao nao remove dado sem decisao explicita: sem DROP COLUMN/TABLE.
    assert not re.search(r"DROP\s+(COLUMN|TABLE)", source, re.IGNORECASE)


def test_plano_da_027_na_aurora_e_zero_statements():
    db = gb.SnapshotDB()
    assert gb.load_module().plan(db) == []
    # So information_schema: nem contagem de linhas nem busca de admin.
    assert db.queries and all("information_schema" in q for q in db.queries)


def test_snapshot_da_aurora_nao_tem_gyms_color_palette():
    """O DROP COLUMN do antigo guard de gyms ja aconteceu na Aurora: nada a remover."""
    snap = gb.aurora_snapshot()
    assert any(c["t"] == "gyms" for c in snap["columns"])
    assert not any(c["t"] == "gyms" and c["col"] == "color_palette" for c in snap["columns"])


def _without(snap, table, column):
    snap = copy.deepcopy(snap)
    snap["columns"] = [c for c in snap["columns"] if not (c["t"] == table and c["col"] == column)]
    return snap


def test_plano_sobre_snapshot_sem_colunas_late_de_wellness_so_adiciona_as_colunas():
    snap = _without(_without(gb.aurora_snapshot(), "intervals_wellness", "readiness_score"), "intervals_wellness", "daily_effort_load")
    assert gb.load_module().plan(gb.SnapshotDB(snap)) == [
        ("ALTER TABLE intervals_wellness ADD COLUMN readiness_score DECIMAL(6,2) NULL", None),
        ("ALTER TABLE intervals_wellness ADD COLUMN daily_effort_load INT NULL", None),
    ]


def test_plano_sobre_snapshot_sem_tabela_reports_cria_so_ela():
    snap = copy.deepcopy(gb.aurora_snapshot())
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != "reports"]
    planned = gb.load_module().plan(gb.SnapshotDB(snap))
    assert len(planned) == 1 and planned[0][0].strip().startswith("CREATE TABLE IF NOT EXISTS reports (")


def test_plano_sobre_snapshot_incompativel_falha():
    snap = copy.deepcopy(gb.aurora_snapshot())
    for column in snap["columns"]:
        if column["t"] == "reports" and column["col"] == "status":
            column["type"] = "varchar(20)"
    module = gb.load_module()
    with pytest.raises(module.IntegracoesMetricasSchemaError, match=r"reports\.status e varchar\(20\).*Nenhum statement"):
        module.plan(gb.SnapshotDB(snap))
