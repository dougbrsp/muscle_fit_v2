"""Natureza e qualidade de um valor exibido: vocabulário comum a todos os motores.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.1). Nasceu em
`intelligence/dashboard/contracts.py` (plano do Novo Dashboard §8.4) e foi
extraído para cá em 2026-10-16 (L1 de nutrição): o motor de nutrição é Python
puro e não pode depender do módulo de contratos Pydantic do dashboard. Os
valores são os mesmos, em maiúsculas, e `contracts.py` reexporta os dois enums,
então a API e os importadores do dashboard não mudam.

- `DataNature`: de onde vem o número (observado, derivado, planejado,
  projetado, estimado, inferido).
- `DataQuality`: o quanto confiar nele.

Rótulos pt-BR: ainda escritos à mão no FE (`frontend/src/lib/dashboard-status.ts`,
`DATA_NATURE`), cópia pré-existente registrada no mapa §3.1, fora do L1.

Módulo PURO: sem banco, sem FastAPI, sem Pydantic, sem I/O.
"""

from __future__ import annotations

from enum import Enum


class DataNature(str, Enum):
    """Origem semântica de um valor (plano do Novo Dashboard §8.4)."""

    OBSERVED = "OBSERVED"  # lido do banco como foi registrado (série, pesagem, kcal informada)
    DERIVED = "DERIVED"  # cálculo determinístico sobre observados (séries efetivas, macros do catálogo)
    PLANNED = "PLANNED"  # prescrito na ficha/template/plano
    PROJECTED = "PROJECTED"  # o que ainda falta no ciclo (slots pendentes)
    ESTIMATED = "ESTIMATED"  # modelo com erro conhecido (e1RM, bioimpedância, MET, TDEE observado)
    INFERRED = "INFERRED"  # conclusão heurística (readiness, recomposição)


class DataQuality(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


__all__ = ["DataNature", "DataQuality"]
