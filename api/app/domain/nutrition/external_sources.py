"""Fontes externas do catálogo de alimentos: USDA FoodData Central e Open Food
Facts — mapeamento para os campos do catálogo, código de barras e limites.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §49; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão
`fontes_externas_alimentos`). Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`
§4, §8.1, §13 e §15 (R4, R7). Nasceu no Lote L3b: os ids de nutriente do USDA,
as chaves do OFF, as conversões de unidade e a escolha da energia são regra e
moram aqui; a rota e o cliente HTTP (`api/app/external_food_sources.py`) só
consomem. Payloads reais gravados em 2026-10-17 conferem o mapeamento
(`api/tests/unit/data/nutrition_external/`).

USDA FoodData Central (CC0 1.0):
- Só Foundation e SR Legacy (`USDA_DATA_TYPES`): dados analíticos por 100 g
  (base g). Branded/Survey ficam fora (422 na importação).
- Nutrientes pelo id do FDC, com a unidade esperada (`USDA_NUTRIENT_IDS`;
  unidade do campo: `_g` → g, `_mg` → mg). Só ids vistos nos payloads reais:
  proteína 1003, gordura total 1004, carboidrato por diferença 1005, fibra
  1079, sódio 1093 (mg), açúcares totais 2000, saturada 1258, trans 1257.
  Açúcares adicionados não existem nesses tipos: ficam None.
- Energia (`USDA_ENERGY_CANDIDATES`, em ordem): 1008 (kcal publicada) >
  2048 (Atwater com fatores específicos do alimento) > 2047 (Atwater geral) >
  1062 (kJ → kcal por `meal_math.kj_to_kcal`). Os Foundation mais novos não
  têm 1008, só 2047/2048 (ex.: fdcId 2710825); o específico é o mais exato.
- Linha de grupo (sem valor) e unidade diferente da esperada são ignoradas.

Open Food Facts (ODbL 1.0; conteúdo DbCL 1.0), API v3.6:
- Lê `nutrition.aggregated_set` (o conjunto que o OFF agrega e normaliza a
  partir das fontes do produto). Só `preparation` = `as_sold` e `per` = 100g
  ou 100ml (`OFF_PER_BASIS`: base g ou ml). Outro preparo/base → sem valores.
  A v3.6 não devolve mais as chaves planas `*_100g` (`nutriments`): conferido
  no payload real; a guarda recusa essas chaves fora daqui.
- Nutrientes por chave (`OFF_NUTRIENT_KEYS`) com a unidade normalizada pelo OFF
  (g); sódio g → mg (`catalog.MG_PER_G`). Valor de origem `estimate`
  (estimado pelo OFF a partir dos ingredientes, `OFF_IGNORED_NUTRIENT_SOURCES`)
  é ignorado: o catálogo guarda o que o rótulo/fonte declara.
- Energia (`OFF_ENERGY_CANDIDATES`): `energy-kcal` declarada > `energy-kj` →
  kcal > `energy` (kJ) → kcal. O `value_computed` do OFF (kcal calculadas a
  partir dos macros) NUNCA é usado: a conferência é o Atwater do motor, que
  vira aviso (`catalog.WARN_ATWATER`).
- Nome: `OFF_NAME_FIELDS` em ordem (português primeiro); sem nome →
  `OFF_UNNAMED_TEMPLATE`. Porção do rótulo: `serving_quantity` (na base) se
  passar na regra da porção (`catalog.validate_portion_amount`); o texto
  `serving_size` vira a medida caseira.

Comum: nutriente ausente vira None, nunca 0; a validação é a do catálogo
(`catalog.validate_food_values`) com `catalog.EXTERNAL_REQUIRED_PER_100_FIELDS`
(só a energia: a fonte pode não informar um macro). Erro → a importação é
recusada (422); aviso (Atwater, subtotal) segue na resposta.

Código de barras (`normalize_gtin`): GTIN-8, 12 (UPC-A), 13 (EAN) ou 14, só
dígitos (espaço e hífen são removidos), dígito verificador módulo 10 com pesos
3/1 a partir da direita (GS1). Forma canônica: UPC-A ganha um 0 à esquerda
(EAN-13) e GTIN-14 com 0 à esquerda perde o 0 — a mesma chave para o mesmo
produto.

Limite (`OFF_LOOKUPS_PER_USER_DAY`, default de desenho do plano §8.1): consultas
NOVAS ao OFF por usuário e dia (o dia é o do fuso do usuário). Quem conta é a
rota, pelas linhas `source='off'` que o usuário criou no dia: consulta que
achou o produto no cache, que não achou o produto no OFF ou que foi recusada
pela validação NÃO conta (limitação registrada no mapa §49).

Módulo PURO: sem banco, sem FastAPI, sem I/O e sem relógio.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Final, Iterator, Mapping, Optional

from . import catalog, food_text, meal_math
from .catalog import FoodSource, FoodValidation
from .meal_math import Unit

# ---------------------------------------------------------------------------
# Código de barras (GTIN/EAN)
# ---------------------------------------------------------------------------

GTIN_LENGTHS: Final[tuple[int, ...]] = (8, 12, 13, 14)
_UPC_A_LENGTH: Final[int] = 12
_GTIN_14_LENGTH: Final[int] = 14
_GTIN_SEPARATORS = re.compile(r"[\s-]+")
_DIGITS = re.compile(r"[0-9]+")

ERR_INVALID_BARCODE = "invalid_barcode"
ERR_OFF_DAILY_LIMIT = "off_daily_limit"
ERR_USDA_DATA_TYPE = "usda_data_type_not_supported"

# Dígito verificador GS1: pesos alternados a partir do dígito à esquerda do verificador, módulo 10.
GS1_CHECK_WEIGHTS: Final[tuple[int, int]] = (3, 1)
GS1_MODULUS: Final[int] = 10

# Consultas NOVAS ao Open Food Facts por usuário e dia (plano §8.1; default de desenho).
OFF_LOOKUPS_PER_USER_DAY: Final[int] = 30
# O que conta como consulta nova: uma linha desta fonte criada (`created_by`) pelo
# usuário desde o início do dia DELE. Cache achado, 404 do OFF, recusa do motor
# (422) e erro externo não gravam linha e, por isso, não contam (limitação, §49).
OFF_LOOKUP_COUNTED_SOURCE: Final[FoodSource] = FoodSource.OFF
# Resultados por busca no USDA (default de desenho; a tela mostra uma página).
USDA_SEARCH_PAGE_SIZE: Final[int] = 25


def gtin_check_digit_ok(digits: str) -> bool:
    """Dígito verificador GS1 (`GS1_CHECK_WEIGHTS` a partir da direita, `GS1_MODULUS`)."""
    corpo, verificador = digits[:-1], int(digits[-1])
    soma = sum(int(d) * GS1_CHECK_WEIGHTS[i % 2] for i, d in enumerate(reversed(corpo)))
    return (GS1_MODULUS - soma % GS1_MODULUS) % GS1_MODULUS == verificador


def normalize_gtin(raw: Any) -> Optional[str]:
    """Código canônico (8, 13 ou 14 dígitos) ou None se não for um GTIN válido."""
    if raw is None:
        return None
    texto = _GTIN_SEPARATORS.sub("", str(raw))
    if not _DIGITS.fullmatch(texto) or len(texto) not in GTIN_LENGTHS or not gtin_check_digit_ok(texto):
        return None
    if len(texto) == _UPC_A_LENGTH:
        return f"0{texto}"
    if len(texto) == _GTIN_14_LENGTH and texto.startswith("0"):
        return texto[1:]
    return texto


def off_lookup_allowed(lookups_today: int) -> bool:
    """Ainda cabe uma consulta nova ao OFF hoje?"""
    return int(lookups_today) < OFF_LOOKUPS_PER_USER_DAY


# ---------------------------------------------------------------------------
# Resultado comum
# ---------------------------------------------------------------------------


class EnergyOrigin(str, Enum):
    """De onde veio a energia gravada (transparência; nunca calculada dos macros)."""

    SOURCE_KCAL = "source_kcal"
    ATWATER_SPECIFIC = "atwater_specific"
    ATWATER_GENERAL = "atwater_general"
    FROM_KJ = "from_kj"


@dataclass(frozen=True)
class ExternalFood:
    """Alimento de uma fonte externa já nos campos do catálogo (por 100 g/ml),
    com citação/licença da linha e a validação do catálogo."""

    source: FoodSource
    source_id: str
    source_version: Optional[str]
    barcode: Optional[str]
    name: str
    name_normalized: str
    brand: Optional[str]
    category: Optional[str]
    basis: Unit
    household_measure: Optional[str]
    citation: str
    license: str
    energy_origin: Optional[EnergyOrigin]
    validation: FoodValidation
    extra_errors: tuple[str, ...] = ()

    @property
    def values(self) -> Mapping[str, Optional[float]]:
        return self.validation.values

    @property
    def errors(self) -> tuple[str, ...]:
        return (*self.extra_errors, *self.validation.errors)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "barcode": self.barcode,
            "name": self.name,
            "name_normalized": self.name_normalized,
            "brand": self.brand,
            "category": self.category,
            "basis": self.basis.value,
            "household_measure": self.household_measure,
            "values": dict(self.values),
            "citation": self.citation,
            "license": self.license,
            "energy_origin": self.energy_origin.value if self.energy_origin else None,
            "errors": list(self.errors),
            "warnings": list(self.validation.warnings),
        }


def _number(value: Any) -> Optional[float]:
    """Número finito (sem decidir sinal: negativo segue para a validação do catálogo)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    texto = " ".join(str(value).split())
    return texto or None


def _unit_of(field: str) -> str:
    return "mg" if field in catalog.MILLIGRAM_FIELDS else "g"


def _energy_kcal(amount: Optional[float], origin: EnergyOrigin) -> Optional[float]:
    if amount is None:
        return None
    return meal_math.kj_to_kcal(amount) if origin == EnergyOrigin.FROM_KJ else amount


def _validate(values: Mapping[str, Optional[float]], basis: Unit) -> FoodValidation:
    return catalog.validate_food_values(values, basis, required=catalog.EXTERNAL_REQUIRED_PER_100_FIELDS)


# ---------------------------------------------------------------------------
# USDA FoodData Central
# ---------------------------------------------------------------------------


class UsdaDataType(str, Enum):
    """`dataType` do FDC (o valor é o que a API usa)."""

    FOUNDATION = "Foundation"
    SR_LEGACY = "SR Legacy"


USDA_DATA_TYPES: Final[tuple[UsdaDataType, ...]] = (UsdaDataType.FOUNDATION, UsdaDataType.SR_LEGACY)
_USDA_VERSION_SLUG: Final[Mapping[UsdaDataType, str]] = {
    UsdaDataType.FOUNDATION: "foundation",
    UsdaDataType.SR_LEGACY: "sr_legacy",
}

# Energia: (id do nutriente, unidade, origem), na ordem de preferência.
USDA_ENERGY_CANDIDATES: Final[tuple[tuple[int, str, EnergyOrigin], ...]] = (
    (1008, "kcal", EnergyOrigin.SOURCE_KCAL),
    (2048, "kcal", EnergyOrigin.ATWATER_SPECIFIC),
    (2047, "kcal", EnergyOrigin.ATWATER_GENERAL),
    (1062, "kj", EnergyOrigin.FROM_KJ),
)
# Campo do catálogo -> ids do FDC em ordem de preferência (unidade pelo campo).
USDA_NUTRIENT_IDS: Final[Mapping[str, tuple[int, ...]]] = {
    "carbohydrates_g": (1005,),
    "total_sugars_g": (2000,),
    "protein_g": (1003,),
    "total_fat_g": (1004,),
    "saturated_fat_g": (1258,),
    "trans_fat_g": (1257,),
    "fiber_g": (1079,),
    "sodium_mg": (1093,),
}

_USDA_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def usda_data_type(raw: Any) -> Optional[UsdaDataType]:
    try:
        return UsdaDataType(str(raw).strip())
    except ValueError:
        return None


def _usda_rows(food: Mapping[str, Any]) -> Iterator[tuple[Optional[int], str, Optional[float]]]:
    """(id, unidade minúscula, valor) do detalhe (`nutrient.id`/`amount`) ou da
    busca (`nutrientId`/`value`)."""
    for linha in food.get("foodNutrients") or ():
        if not isinstance(linha, Mapping):
            continue
        nutriente = linha.get("nutrient")
        if isinstance(nutriente, Mapping):
            ident, unidade, valor = nutriente.get("id"), nutriente.get("unitName"), linha.get("amount")
        else:
            ident, unidade, valor = linha.get("nutrientId"), linha.get("unitName"), linha.get("value")
        try:
            ident = int(ident) if ident is not None else None
        except (TypeError, ValueError):
            ident = None
        yield ident, str(unidade or "").strip().lower(), _number(valor)


def usda_values(food: Mapping[str, Any]) -> tuple[dict[str, Optional[float]], Optional[EnergyOrigin]]:
    """Campos por 100 g do catálogo + origem da energia (ausente -> None)."""
    por_id: dict[int, tuple[str, float]] = {}
    for ident, unidade, valor in _usda_rows(food):
        if ident is not None and valor is not None and ident not in por_id:
            por_id[ident] = (unidade, valor)
    valores: dict[str, Optional[float]] = {campo: None for campo in catalog.PER_100_FIELDS}
    origem: Optional[EnergyOrigin] = None
    for ident, unidade, candidata in USDA_ENERGY_CANDIDATES:
        achado = por_id.get(ident)
        if achado is not None and achado[0] == unidade:
            valores["energy_kcal"], origem = _energy_kcal(achado[1], candidata), candidata
            break
    for campo, ids in USDA_NUTRIENT_IDS.items():
        for ident in ids:
            achado = por_id.get(ident)
            if achado is not None and achado[0] == _unit_of(campo):
                valores[campo] = achado[1]
                break
    return valores, origem


def _usda_category(food: Mapping[str, Any]) -> Optional[str]:
    categoria = food.get("foodCategory")
    if isinstance(categoria, Mapping):
        return _text(categoria.get("description"))
    return _text(categoria)


def usda_source_version(data_type: Optional[UsdaDataType], published: Any) -> Optional[str]:
    """`foundation-2020-10-30` (data de publicação do item no FDC, ISO)."""
    if data_type is None:
        return None
    texto = _text(published)
    if texto:
        data = _USDA_DATE.fullmatch(texto)
        if data:
            mes, dia, ano = data.groups()
            texto = f"{ano}-{int(mes):02d}-{int(dia):02d}"
    return f"{_USDA_VERSION_SLUG[data_type]}-{texto}" if texto else _USDA_VERSION_SLUG[data_type]


def usda_citation(fdc_id: Any, data_type: Optional[UsdaDataType]) -> str:
    tipo = f"{data_type.value}, " if data_type else ""
    return f"{catalog.USDA_CITATION} {tipo}FDC ID {fdc_id}."


def map_usda_food(food: Mapping[str, Any], requested_fdc_id: Any = None) -> ExternalFood:
    """Detalhe do FDC (`GET /food/{fdcId}`) -> alimento do catálogo (base g).
    O id é o do payload; sem ele, o pedido."""
    tipo = usda_data_type(food.get("dataType"))
    fdc_id = str(food.get("fdcId") or requested_fdc_id or "").strip()
    nome = _text(food.get("description")) or ""
    valores, origem = usda_values(food)
    erros = [] if tipo is not None else [ERR_USDA_DATA_TYPE]
    normalizado = food_text.normalize_name(nome)
    if not normalizado:
        erros.append(catalog.ERR_INVALID_NAME)
    return ExternalFood(
        source=FoodSource.USDA,
        source_id=fdc_id,
        source_version=usda_source_version(tipo, food.get("publicationDate") or food.get("publishedDate")),
        barcode=None,
        name=nome,
        name_normalized=normalizado,
        brand=None,
        category=_usda_category(food),
        basis=Unit.GRAM,
        household_measure=None,
        citation=usda_citation(fdc_id, tipo),
        license=catalog.USDA_LICENSE,
        energy_origin=origem,
        validation=_validate(valores, Unit.GRAM),
        extra_errors=tuple(erros),
    )


def usda_search_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Resumo de cada resultado da busca (só os tipos aceitos): nome em inglês,
    fdcId, tipo de dado, categoria e os campos por 100 g mapeados."""
    itens: list[dict[str, Any]] = []
    for food in payload.get("foods") or ():
        if not isinstance(food, Mapping):
            continue
        tipo = usda_data_type(food.get("dataType"))
        if tipo is None or food.get("fdcId") is None:
            continue
        valores, origem = usda_values(food)
        itens.append({
            "fdc_id": int(food["fdcId"]),
            "name": _text(food.get("description")),
            "data_type": tipo.value,
            "category": _usda_category(food),
            "basis": Unit.GRAM.value,
            "per_100": valores,
            "energy_origin": origem.value if origem else None,
        })
    return itens


def usda_search_request(query: str) -> dict[str, Any]:
    """Corpo de `POST /foods/search` do FDC: o termo, só os tipos aceitos e uma página."""
    return {
        "query": query,
        "dataType": [tipo.value for tipo in USDA_DATA_TYPES],
        "pageSize": USDA_SEARCH_PAGE_SIZE,
        "pageNumber": 1,
    }


def usda_search_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Resposta da busca para a API: total de resultados no FDC e os itens resumidos."""
    total = payload.get("totalHits")
    return {
        "total_hits": int(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else None,
        "items": usda_search_items(payload),
    }


# ---------------------------------------------------------------------------
# Open Food Facts (API v3.6, `nutrition.aggregated_set`)
# ---------------------------------------------------------------------------

OFF_ACCEPTED_PREPARATION: Final[str] = "as_sold"
OFF_PER_BASIS: Final[Mapping[str, Unit]] = {"100g": Unit.GRAM, "100ml": Unit.MILLILITER}
OFF_IGNORED_NUTRIENT_SOURCES: Final[frozenset[str]] = frozenset({"estimate"})
# Energia: (chave, unidade, origem), na ordem de preferência.
OFF_ENERGY_CANDIDATES: Final[tuple[tuple[str, str, EnergyOrigin], ...]] = (
    ("energy-kcal", "kcal", EnergyOrigin.SOURCE_KCAL),
    ("energy-kj", "kj", EnergyOrigin.FROM_KJ),
    ("energy", "kj", EnergyOrigin.FROM_KJ),
)
# Campo do catálogo -> chave do nutriente no OFF (valor em g; sódio vira mg).
OFF_NUTRIENT_KEYS: Final[Mapping[str, str]] = {
    "carbohydrates_g": "carbohydrates",
    "total_sugars_g": "sugars",
    "added_sugars_g": "added-sugars",
    "protein_g": "proteins",
    "total_fat_g": "fat",
    "saturated_fat_g": "saturated-fat",
    "trans_fat_g": "trans-fat",
    "fiber_g": "fiber",
    "sodium_mg": "sodium",
}
OFF_MASS_UNIT: Final[str] = "g"
OFF_NAME_FIELDS: Final[tuple[str, ...]] = ("product_name_pt", "product_name", "generic_name_pt", "generic_name")
OFF_UNNAMED_TEMPLATE: Final[str] = "Produto {code}"
# Campos que o mapeamento lê (o cliente pede só estes: `fields=`).
OFF_PRODUCT_FIELDS: Final[tuple[str, ...]] = (
    "code", *OFF_NAME_FIELDS, "brands", "serving_size", "serving_quantity", "nutrition", "last_modified_t",
)

OFF_PRODUCT_URL: Final[str] = "https://world.openfoodfacts.org/product/{code}"


def off_citation(code: str) -> str:
    return f"{catalog.OFF_CITATION} Produto {code}: {OFF_PRODUCT_URL.format(code=code)}"


def _off_aggregated(product: Mapping[str, Any]) -> tuple[Optional[Unit], Mapping[str, Any]]:
    """(base, nutrientes) do conjunto agregado, ou (None, {}) se o preparo/base não servem."""
    nutricao = product.get("nutrition")
    agregado = nutricao.get("aggregated_set") if isinstance(nutricao, Mapping) else None
    if not isinstance(agregado, Mapping) or agregado.get("preparation") != OFF_ACCEPTED_PREPARATION:
        return None, {}
    base = OFF_PER_BASIS.get(str(agregado.get("per") or ""))
    nutrientes = agregado.get("nutrients")
    if base is None or not isinstance(nutrientes, Mapping):
        return None, {}
    return base, nutrientes


def _off_nutrient(nutrientes: Mapping[str, Any], chave: str, unidade: str) -> Optional[float]:
    item = nutrientes.get(chave)
    if not isinstance(item, Mapping) or item.get("source") in OFF_IGNORED_NUTRIENT_SOURCES:
        return None
    if str(item.get("unit") or "").strip().lower() != unidade:
        return None
    return _number(item.get("value"))  # `value_computed` nunca: é cálculo do OFF, não o declarado


def off_values(product: Mapping[str, Any]) -> tuple[Optional[Unit], dict[str, Optional[float]], Optional[EnergyOrigin]]:
    base, nutrientes = _off_aggregated(product)
    valores: dict[str, Optional[float]] = {campo: None for campo in catalog.PER_100_FIELDS}
    origem: Optional[EnergyOrigin] = None
    for chave, unidade, candidata in OFF_ENERGY_CANDIDATES:
        valor = _off_nutrient(nutrientes, chave, unidade)
        if valor is not None:
            valores["energy_kcal"], origem = _energy_kcal(valor, candidata), candidata
            break
    for campo, chave in OFF_NUTRIENT_KEYS.items():
        valor = _off_nutrient(nutrientes, chave, OFF_MASS_UNIT)
        if valor is not None and campo in catalog.MILLIGRAM_FIELDS:
            valor *= catalog.MG_PER_G
        valores[campo] = valor
    return base, valores, origem


def _off_version(last_modified: Any) -> Optional[str]:
    """Data (UTC, ISO) da última modificação do produto no OFF."""
    instante = _number(last_modified)
    if instante is None or instante <= 0:
        return None
    return datetime.fromtimestamp(instante, tz=timezone.utc).date().isoformat()


def map_off_product(product: Mapping[str, Any], code: str) -> ExternalFood:
    """Produto do OFF (campos `OFF_PRODUCT_FIELDS`) -> alimento do catálogo.
    `code` é o GTIN canônico da consulta (`normalize_gtin`)."""
    base, valores, origem = off_values(product)
    nome = next((texto for campo in OFF_NAME_FIELDS if (texto := _text(product.get(campo)))), None)
    nome = nome or OFF_UNNAMED_TEMPLATE.format(code=code)
    porcao = _number(product.get("serving_quantity"))
    if porcao is not None and catalog.validate_portion_amount(porcao) is not None:
        porcao = None
    base_efetiva = base or catalog.DEFAULT_FOOD_BASIS
    return ExternalFood(
        source=FoodSource.OFF,
        source_id=code,
        source_version=_off_version(product.get("last_modified_t")),
        barcode=code,
        name=nome,
        name_normalized=food_text.normalize_name(nome),
        brand=_text(product.get("brands")),
        category=None,
        basis=base_efetiva,
        household_measure=_text(product.get("serving_size")),
        citation=off_citation(code),
        license=catalog.OFF_LICENSE,
        energy_origin=origem,
        validation=_validate({**valores, "serving_size": porcao}, base_efetiva),
    )


__all__ = [
    "ERR_INVALID_BARCODE",
    "ERR_OFF_DAILY_LIMIT",
    "ERR_USDA_DATA_TYPE",
    "EnergyOrigin",
    "ExternalFood",
    "GTIN_LENGTHS",
    "OFF_LOOKUPS_PER_USER_DAY",
    "OFF_PRODUCT_FIELDS",
    "USDA_DATA_TYPES",
    "USDA_SEARCH_PAGE_SIZE",
    "UsdaDataType",
    "gtin_check_digit_ok",
    "map_off_product",
    "map_usda_food",
    "normalize_gtin",
    "off_lookup_allowed",
    "usda_search_items",
    "usda_search_request",
    "usda_search_result",
    "usda_values",
]
