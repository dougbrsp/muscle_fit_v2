"""Guarda estatica: sessoes de treino e fichas sem DDL nem guard de schema em
runtime (o schema e da migracao 026). A prova comportamental, nos dois
backends, esta em
tests/integration/dataapi/test_sessoes_fichas_sem_ddl_paridade.py."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import migrate as m
from app.schema_guards import SCHEMA_GUARD_MODULES

APP_DIR = Path(__file__).resolve().parents[2] / "app"
MODULES = ("workout_sessions.py", "services.py")
DDL_RE = re.compile(r"\b(CREATE\s+(TABLE|INDEX|UNIQUE)|ALTER\s+TABLE|DROP\s+(TABLE|INDEX|COLUMN|FOREIGN))\b", re.IGNORECASE)
REMOVED_GUARDS = (
    "_ensure_schema",
    "_ensure_set_hr_columns",
    "_ensure_workout_plans_time_column",
    "_ensure_workout_plans_split_day_column",
    "_ensure_plan_link_columns",
)


@pytest.mark.parametrize("relative", MODULES)
def test_modulo_sem_ddl_nem_guard(relative):
    source = (APP_DIR / relative).read_text(encoding="utf-8")
    assert DDL_RE.findall(source) == []
    assert "information_schema" not in source.lower()
    assert [name for name in REMOVED_GUARDS if name in source] == []


def test_workout_sessions_fora_dos_guards_de_dev():
    assert "workout_sessions" not in SCHEMA_GUARD_MODULES


def test_migracao_026_versionada_e_sem_comandos_de_sessao():
    names = [mig.filename for mig in m.discover_migrations(m.DEFAULT_MIGRATIONS_DIR)]
    assert "026_sessoes_fichas_schema.py" in names
    source = (m.DEFAULT_MIGRATIONS_DIR / "026_sessoes_fichas_schema.py").read_text(encoding="utf-8")
    assert not re.search(r"[\"']\s*(PREPARE|EXECUTE|DEALLOCATE|DELIMITER|USE\s|SET\s+@)", source, re.IGNORECASE)
