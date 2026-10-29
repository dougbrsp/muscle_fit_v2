"""Vocabulário comum de natureza e qualidade (dono `app/domain/data_nature.py`).

Extraído de `intelligence/dashboard/contracts.py` em 2026-10-16 (L1 de
nutrição): os valores não mudam, e `contracts` reexporta o MESMO objeto, então a
API do dashboard e os importadores antigos seguem iguais."""

from __future__ import annotations

import ast
from pathlib import Path

from app.domain import data_nature
from app.domain.data_nature import DataNature, DataQuality
from app.domain.nutrition import energy_expenditure, adaptive_tdee
from app.intelligence.dashboard import contracts

DONO = Path(data_nature.__file__)


def test_valores_iguais_aos_do_contrato_do_dashboard():
    assert [n.value for n in DataNature] == ["OBSERVED", "DERIVED", "PLANNED", "PROJECTED", "ESTIMATED", "INFERRED"]
    assert [q.value for q in DataQuality] == ["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]


def test_contracts_reexporta_o_mesmo_objeto():
    assert contracts.DataNature is DataNature
    assert contracts.DataQuality is DataQuality
    assert {"DataNature", "DataQuality"} <= set(contracts.__all__)


def test_nutricao_usa_o_vocabulario_comum():
    assert energy_expenditure.DataNature is DataNature and energy_expenditure.DataQuality is DataQuality
    assert adaptive_tdee.DataNature is DataNature and adaptive_tdee.DataQuality is DataQuality
    assert not hasattr(energy_expenditure, "Nature") and not hasattr(energy_expenditure, "Quality")


def test_dono_e_puro():
    """Sem Pydantic, sem banco, sem FastAPI: só `enum` e `__future__`."""
    arvore = ast.parse(DONO.read_text(encoding="utf-8"))
    modulos = {
        (no.module if isinstance(no, ast.ImportFrom) else alias.name)
        for no in ast.walk(arvore)
        if isinstance(no, (ast.Import, ast.ImportFrom))
        for alias in no.names
    }
    assert modulos == {"__future__", "enum"}
