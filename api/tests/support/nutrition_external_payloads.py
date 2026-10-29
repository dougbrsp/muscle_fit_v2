"""Payloads REAIS das fontes externas do catálogo (L3b), gravados em 2026-10-17
dentro do container `api` do DEV pelo próprio cliente (`app/external_food_sources`),
para os testes do motor `domain/nutrition/external_sources` e das rotas rodarem
SEM rede e SEM chave.

`tests/unit/data/nutrition_external/` (nenhum arquivo contém a chave do USDA —
as APIs não a devolvem; conferido na gravação):
- `usda_search_rice.json`: `POST /fdc/v1/foods/search` (query "rice", Foundation
  + SR Legacy, 50 por página), reduzido a 6 itens (2 Foundation, um deles só com
  energia Atwater 2047/2048, e 4 SR Legacy) e às chaves que o motor lê;
- `usda_food_foundation_1104812.json` e `usda_food_sr_legacy_173161.json`:
  `GET /fdc/v1/food/{fdcId}`, sem `foodPortions`/`inputFoods`/fatores (não lidos);
- `off_product_7891000100103.json`: `GET /api/v3.6/product/7891000100103.json`
  (Leite Moça, Nestlé) com os `fields` do motor + `nutriments` (a v3.6 não o
  devolve mais: a chave nem aparece) — produto inteiro, sem corte;
- `off_product_not_found_7899999999999.json`: status HTTP e corpo do OFF para um
  GTIN válido inexistente (404 com `status: failure`).
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parents[1] / "unit" / "data" / "nutrition_external"

USDA_SEARCH = "usda_search_rice.json"
USDA_FOUNDATION = "usda_food_foundation_1104812.json"
USDA_SR_LEGACY = "usda_food_sr_legacy_173161.json"
OFF_MOCA = "off_product_7891000100103.json"
OFF_NOT_FOUND = "off_product_not_found_7899999999999.json"

OFF_MOCA_CODE = "7891000100103"
OFF_NOT_FOUND_CODE = "7899999999999"


@lru_cache(maxsize=None)
def _read(name: str) -> Any:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def load(name: str) -> Any:
    """Cópia nova do payload (os testes podem alterar à vontade)."""
    return copy.deepcopy(_read(name))
