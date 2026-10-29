"""Guarda estatica: nenhum modulo do caminho de requisicao de cardio tem DDL
nem guard de schema em runtime (o schema e da migracao 025). A prova
comportamental, nos dois backends, esta em
tests/integration/dataapi/test_cardio_sem_ddl_paridade.py."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import migrate as m
from app.schema_guards import SCHEMA_GUARD_MODULES

APP_DIR = Path(__file__).resolve().parents[2] / "app"
CARDIO_REQUEST_PATH = (
    "cardio_recommendations.py",
    "cardio_generation_core.py",
    "cardio_agent_ai.py",
    "lambda_cardio_agent.py",
    "intelligence/cardio_context_service.py",
    # M98 C3a (`cardio-agent-v3`): contexto compacto e contrato estruturado.
    "intelligence/cardio_context_v3.py",
    "cardio_recommendation_v3.py",
)
DDL_RE = re.compile(r"\b(CREATE\s+(TABLE|INDEX|UNIQUE)|ALTER\s+TABLE|DROP\s+(TABLE|INDEX|COLUMN|FOREIGN))\b", re.IGNORECASE)


@pytest.mark.parametrize("relative", CARDIO_REQUEST_PATH)
def test_modulo_de_cardio_sem_ddl_nem_guard(relative):
    source = (APP_DIR / relative).read_text(encoding="utf-8")
    assert DDL_RE.findall(source) == []
    assert "_ensure_schema" not in source
    assert "_schema_done" not in source


def test_cardio_fora_dos_guards_de_dev():
    assert "cardio_recommendations" not in SCHEMA_GUARD_MODULES


def test_migracao_025_versionada_e_sem_comandos_de_sessao():
    names = [mig.filename for mig in m.discover_migrations(m.DEFAULT_MIGRATIONS_DIR)]
    assert "025_cardio_recommendations_schema.py" in names
    source = (m.DEFAULT_MIGRATIONS_DIR / "025_cardio_recommendations_schema.py").read_text(encoding="utf-8")
    # SQL literal comecando por comando de sessao (a docstring pode citar os nomes).
    assert not re.search(r"[\"']\s*(PREPARE|EXECUTE|DEALLOCATE|DELIMITER|USE\s|SET\s+@)", source, re.IGNORECASE)
