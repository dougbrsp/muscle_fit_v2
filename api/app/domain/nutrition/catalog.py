"""Catálogo de alimentos: fontes, citação e licença, base da tabela, campos por
100 g/ml e da porção, e as regras de entrada de um alimento ou porção.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §48; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`). Plano:
`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §4, §6.2, §8.1 e §15 (D-F, D-G,
R4, R5). Nasceu no Lote L3a: até ali o vocabulário do catálogo não tinha dono.

- Fontes (`FoodSource`): códigos gravados em `food_items.source`. A TACO 4ª ed.
  (NEPA/UNICAMP, 2011) entra por migração (D-F, 081) e é SOMENTE LEITURA pela
  API (`READ_ONLY_SOURCES`): é uma tabela publicada, citada por linha (R4); um
  valor diferente vira alimento novo, nunca edição da TACO. USDA e Open Food
  Facts (L3b) entram pela importação (`external_sources`) e também são somente
  leitura: a linha é cópia da fonte, citada; a TBCA não é embutida (D3).
- Citação e licença por fonte (`SOURCE_CITATIONS`, `SOURCE_LICENSES`): o texto
  vai em cada linha (`food_items.citation`/`license`) e no rodapé da tela. A
  citação da TACO segue a ficha catalográfica da publicação; a licença é a da
  página de créditos ("É permitida a reprodução parcial ou total desta obra,
  desde que citada a fonte"). USDA: citação sugerida pelo FDC, CC0. OFF: o
  aviso de atribuição da ODbL (seção 4.3), por linha (com o link do produto,
  `external_sources.off_citation`) e no rodapé.
- Base: a tabela é por 100 g ou por 100 ml (`FOOD_BASES`, os códigos de
  `meal_math.Unit`). Porção do alimento = quantidade NA BASE (g ou ml).
- Campos por 100 (`PER_100_FIELDS`) e da porção (`SERVING_FIELDS`) usam os
  códigos da RDC 429/2020 (`labels.RDC429_FIELDS`, que dá rótulo e unidade).
  Obrigatórios no cadastro do usuário (`REQUIRED_PER_100_FIELDS`): energia e
  os três macros, o mínimo para o diário (L4) calcular.
- Regras de entrada (`validate_food_values`): número finito e >= 0; obrigatório
  presente; nenhum componente em gramas passa da própria base (100 g de
  alimento não têm mais de 100 g de proteína; sódio em mg idem, ÷ 1.000) —
  na base ml a massa máxima de 100 ml usa `ML_BASIS_MAX_DENSITY_G_PER_ML`;
  energia até 9 kcal por grama da base (Atwater da gordura, `macros`). Avisos,
  que NÃO bloqueiam: Atwater fora da tolerância (`meal_math.atwater_check`),
  subtotal maior que o total (açúcares > carboidratos, saturada > gordura...)
  e proteína + carboidrato + gordura acima da base. Porção do rótulo
  (`serving_size`) segue a regra da porção; porções por embalagem > 0.
- Porção (`validate_portion_amount`): positiva e até `PORTION_AMOUNT_MAX` na
  base do alimento.
- Proveniência por campo (`FieldSource`, `parse_field_sources`,
  `field_sources_json`; M85, migração 084): valor da linha que veio de OUTRA
  fonte que não a da linha, com a fonte, o id, a versão, o nome na fonte, a
  data da consulta, o motivo e a citação/licença. Hoje só os campos `*` ("em
  reavaliação") da TACO 4ª ed., preenchidos com o equivalente do USDA FoodData
  Central por decisão do usuário (2026-10-17, `FieldSourceReason`); `NA` e
  célula vazia continuam NULL. Coluna `food_items.field_sources_json` NULL =
  todos os valores são da fonte da linha.

Defaults de desenho, SEM fonte bibliográfica: `ML_BASIS_MAX_DENSITY_G_PER_ML`
(nenhum alimento líquido comum passa de 2 g/ml; mel ≈ 1,4) e
`PORTION_AMOUNT_MAX` (2 kg ou 2 L numa porção é erro de digitação).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping, Optional, Sequence

from .macros import KCAL_PER_G_FAT, Macros
from .meal_math import PER_100G_REFERENCE, AtwaterCheck, Unit, atwater_check


class FoodSource(str, Enum):
    TACO = "taco"
    USDA = "usda"
    OFF = "off"
    TBCA_MANUAL = "tbca_manual"
    USER = "user"
    LABEL_PHOTO = "label_photo"
    AI_ESTIMATE = "ai_estimate"


# TACO 4ª ed. (seed da migração 081; a migração embute o MESMO texto, teste de paridade).
TACO_SOURCE_VERSION: Final[str] = "4ed-2011"
TACO_CITATION: Final[str] = (
    "Núcleo de Estudos e Pesquisas em Alimentação (NEPA-UNICAMP). Tabela Brasileira de Composição "
    "de Alimentos – TACO. 4. ed. rev. e ampl. Campinas: NEPA-UNICAMP, 2011. 161 p."
)
TACO_LICENSE: Final[str] = "Reprodução total ou parcial permitida desde que citada a fonte (NEPA-UNICAMP)."

# USDA FoodData Central (L3b): citação sugerida pelo FDC (https://fdc.nal.usda.gov/api-guide/); CC0.
USDA_CITATION: Final[str] = (
    "U.S. Department of Agriculture, Agricultural Research Service. FoodData Central, 2019. fdc.nal.usda.gov."
)
USDA_LICENSE: Final[str] = "CC0 1.0 Universal (domínio público)."
# Open Food Facts (L3b): aviso de atribuição da ODbL (seção 4.3) — por linha e no rodapé (R4).
OFF_CITATION: Final[str] = (
    "Contém informações do Open Food Facts (https://world.openfoodfacts.org), disponibilizadas sob a "
    "Open Database License (ODbL)."
)
OFF_LICENSE: Final[str] = (
    "Open Database License (ODbL) v1.0; conteúdo individual sob a Database Contents License (DbCL) v1.0."
)

SOURCE_CITATIONS: Final[Mapping[FoodSource, str]] = {
    FoodSource.TACO: TACO_CITATION,
    FoodSource.USDA: USDA_CITATION,
    FoodSource.OFF: OFF_CITATION,
}
SOURCE_LICENSES: Final[Mapping[FoodSource, str]] = {
    FoodSource.TACO: TACO_LICENSE,
    FoodSource.USDA: USDA_LICENSE,
    FoodSource.OFF: OFF_LICENSE,
}

# Fontes que a API não edita (tabela publicada ou cópia de base externa; a
# correção é um alimento novo). USDA e OFF desde o L3b.
READ_ONLY_SOURCES: Final[frozenset[FoodSource]] = frozenset({FoodSource.TACO, FoodSource.USDA, FoodSource.OFF})
# Fonte gravada pelo `POST /nutrition/foods` (cadastro manual, D-G: nasce privado).
USER_ENTRY_SOURCE: Final[FoodSource] = FoodSource.USER
# Fontes que o cadastro do usuário aceita (L6): o manual e o formulário pré-preenchido
# pela leitura da foto do rótulo (`label_photo`), conferido pelo usuário antes de salvar.
USER_ENTRY_SOURCES: Final[frozenset[FoodSource]] = frozenset({FoodSource.USER, FoodSource.LABEL_PHOTO})

FOOD_BASES: Final[tuple[Unit, ...]] = (Unit.GRAM, Unit.MILLILITER)
DEFAULT_FOOD_BASIS: Final[Unit] = Unit.GRAM

# Campos por 100 g/ml, na ordem da RDC 429 (códigos de `labels.RDC429_FIELDS`).
PER_100_FIELDS: Final[tuple[str, ...]] = (
    "energy_kcal",
    "carbohydrates_g",
    "total_sugars_g",
    "added_sugars_g",
    "protein_g",
    "total_fat_g",
    "saturated_fat_g",
    "trans_fat_g",
    "fiber_g",
    "sodium_mg",
)
SERVING_FIELDS: Final[tuple[str, ...]] = ("serving_size", "household_measure", "servings_per_package")
REQUIRED_PER_100_FIELDS: Final[tuple[str, ...]] = ("energy_kcal", "carbohydrates_g", "protein_g", "total_fat_g")
# Importação de fonte externa (USDA/OFF, L3b): a fonte pode não informar um
# macro (NULL, nunca 0); sem energia o item não serve ao diário e é recusado.
EXTERNAL_REQUIRED_PER_100_FIELDS: Final[tuple[str, ...]] = ("energy_kcal",)

GRAM_FIELDS: Final[tuple[str, ...]] = tuple(campo for campo in PER_100_FIELDS if campo.endswith("_g"))
MILLIGRAM_FIELDS: Final[tuple[str, ...]] = ("sodium_mg",)
MG_PER_G: Final[float] = 1000.0
# Subtotal -> total que o contém (rótulo RDC 429).
SUBTOTAL_OF: Final[Mapping[str, str]] = {
    "total_sugars_g": "carbohydrates_g",
    "added_sugars_g": "total_sugars_g",
    "saturated_fat_g": "total_fat_g",
    "trans_fat_g": "total_fat_g",
}
MACRO_MASS_FIELDS: Final[tuple[str, ...]] = ("protein_g", "carbohydrates_g", "total_fat_g")

# Defaults de desenho (ver docstring).
ML_BASIS_MAX_DENSITY_G_PER_ML: Final[float] = 2.0
PORTION_AMOUNT_MAX: Final[float] = 2000.0

ERR_INVALID_BASIS = "invalid_basis"
ERR_INVALID_SOURCE = "invalid_food_source"
ERR_INVALID_NAME = "invalid_food_name"
ERR_MISSING_FIELD = "missing_field"  # `missing_field:<campo>`
ERR_INVALID_VALUE = "invalid_value"  # `invalid_value:<campo>`
ERR_ABOVE_BASIS = "above_basis"  # `above_basis:<campo>`
ERR_INVALID_PORTION_AMOUNT = "invalid_portion_amount"
ERR_SOURCE_READ_ONLY = "food_source_read_only"
WARN_ATWATER = "atwater_inconsistent"
WARN_SUBTOTAL_ABOVE_TOTAL = "subtotal_above_total"  # `subtotal_above_total:<campo>`
WARN_MACROS_ABOVE_BASIS = "macros_above_basis"


def normalize_source(raw: Any) -> Optional[FoodSource]:
    if isinstance(raw, FoodSource):
        return raw
    try:
        return FoodSource(str(raw).strip().lower())
    except ValueError:
        return None


def user_entry_source(raw: Any) -> Optional[FoodSource]:
    """Fonte do cadastro do usuário: ausente -> `USER_ENTRY_SOURCE`; uma de
    `USER_ENTRY_SOURCES` -> ela; qualquer outra (TACO, USDA, OFF...) -> None."""
    if raw is None:
        return USER_ENTRY_SOURCE
    fonte = normalize_source(raw)
    return fonte if fonte in USER_ENTRY_SOURCES else None


def normalize_basis(raw: Any) -> Optional[Unit]:
    if raw is None:
        return DEFAULT_FOOD_BASIS
    if isinstance(raw, Unit):
        return raw if raw in FOOD_BASES else None
    texto = str(raw).strip().lower()
    return next((base for base in FOOD_BASES if base.value == texto), None)


def is_read_only(source: Any) -> bool:
    fonte = normalize_source(source)
    return fonte is not None and fonte in READ_ONLY_SOURCES


def max_basis_mass_g(basis: Unit) -> float:
    """Massa máxima, em g, de 100 unidades da base (100 g; 100 ml × densidade máxima)."""
    if basis == Unit.MILLILITER:
        return PER_100G_REFERENCE * ML_BASIS_MAX_DENSITY_G_PER_ML
    return PER_100G_REFERENCE


def _number(value: Any) -> tuple[Optional[float], bool]:
    """(valor, válido). Ausente -> (None, True); inválido/negativo -> (None, False)."""
    if value is None:
        return None, True
    if isinstance(value, bool):
        return None, False
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None, False
    if not math.isfinite(numero) or numero < 0:
        return None, False
    return numero, True


@dataclass(frozen=True)
class FoodValidation:
    """Valores numéricos normalizados (float/None: os por 100 e os da porção do
    rótulo), erros que bloqueiam e avisos."""

    basis: Optional[Unit]
    values: Mapping[str, Optional[float]]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    atwater: Optional[AtwaterCheck]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis.value if self.basis else None,
            "values": dict(self.values),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "atwater": self.atwater.to_dict() if self.atwater else None,
        }


def validate_food_values(
    values: Mapping[str, Any],
    basis: Any = None,
    *,
    required: tuple[str, ...] = REQUIRED_PER_100_FIELDS,
) -> FoodValidation:
    """Regras de entrada de um alimento (por 100 g/ml). Campos fora de
    `PER_100_FIELDS` são ignorados. `required`: obrigatórios do cadastro
    (`REQUIRED_PER_100_FIELDS`) ou da importação externa
    (`EXTERNAL_REQUIRED_PER_100_FIELDS`); o Atwater só roda com os quatro."""
    base = normalize_basis(basis)
    erros: list[str] = []
    avisos: list[str] = []
    numeros: dict[str, Optional[float]] = {}
    for campo in PER_100_FIELDS:
        numero, valido = _number(values.get(campo))
        numeros[campo] = numero
        if not valido:
            erros.append(f"{ERR_INVALID_VALUE}:{campo}")
        elif numero is None and campo in required:
            erros.append(f"{ERR_MISSING_FIELD}:{campo}")
    if base is None:
        return FoodValidation(None, numeros, (ERR_INVALID_BASIS, *erros), (), None)

    massa_max = max_basis_mass_g(base)
    tetos = {campo: massa_max for campo in GRAM_FIELDS}
    tetos.update({campo: massa_max * MG_PER_G for campo in MILLIGRAM_FIELDS})
    tetos["energy_kcal"] = massa_max * KCAL_PER_G_FAT
    for campo, teto in tetos.items():
        numero = numeros.get(campo)
        if numero is not None and numero > teto:
            erros.append(f"{ERR_ABOVE_BASIS}:{campo}")

    for parte, total in SUBTOTAL_OF.items():
        if numeros.get(parte) is not None and numeros.get(total) is not None and numeros[parte] > numeros[total]:  # type: ignore[operator]
            avisos.append(f"{WARN_SUBTOTAL_ABOVE_TOTAL}:{parte}")
    massas = [numeros.get(campo) for campo in MACRO_MASS_FIELDS]
    if all(m is not None for m in massas) and sum(massas) > massa_max:  # type: ignore[arg-type]
        avisos.append(WARN_MACROS_ABOVE_BASIS)

    # Porção do rótulo: mesma faixa da porção do alimento; porções por embalagem > 0.
    porcao, porcao_valida = _number(values.get("serving_size"))
    if not porcao_valida or (porcao is not None and validate_portion_amount(porcao) is not None):
        erros.append(f"{ERR_INVALID_VALUE}:serving_size")
    embalagem, embalagem_valida = _number(values.get("servings_per_package"))
    if not embalagem_valida or embalagem == 0:
        erros.append(f"{ERR_INVALID_VALUE}:servings_per_package")
    numeros["serving_size"] = porcao
    numeros["servings_per_package"] = embalagem

    checagem: Optional[AtwaterCheck] = None
    if all(numeros.get(campo) is not None for campo in REQUIRED_PER_100_FIELDS):
        checagem = atwater_check(
            Macros(
                kcal=numeros["energy_kcal"],  # type: ignore[arg-type]
                protein_g=numeros["protein_g"],  # type: ignore[arg-type]
                carb_g=numeros["carbohydrates_g"],  # type: ignore[arg-type]
                fat_g=numeros["total_fat_g"],  # type: ignore[arg-type]
                fiber_g=numeros.get("fiber_g"),
            )
        )
        if checagem is not None and not checagem.consistent:
            avisos.append(WARN_ATWATER)
    return FoodValidation(base, numeros, tuple(erros), tuple(avisos), checagem)


def validate_portion_amount(amount: Any) -> Optional[str]:
    """None se a porção vale (positiva, até `PORTION_AMOUNT_MAX` na base); senão o código."""
    numero, valido = _number(amount)
    if not valido or numero is None or numero <= 0 or numero > PORTION_AMOUNT_MAX:
        return ERR_INVALID_PORTION_AMOUNT
    return None


def citation_for(source: Any) -> Optional[str]:
    fonte = normalize_source(source)
    return SOURCE_CITATIONS.get(fonte) if fonte else None


def license_for(source: Any) -> Optional[str]:
    fonte = normalize_source(source)
    return SOURCE_LICENSES.get(fonte) if fonte else None


# ---------------------------------------------------------------------------
# Proveniência por campo (M85, migração 084)
# ---------------------------------------------------------------------------


class FieldSourceReason(str, Enum):
    """Por que um valor da linha veio de outra fonte."""

    # TACO 4ª ed. com `*` ("análise em reavaliação"): o seed (081) grava NULL e
    # a 084 preenche com o equivalente do USDA (decisão do usuário, 2026-10-17).
    TACO_UNDER_REVIEW = "taco_under_review"


@dataclass(frozen=True)
class FieldSource:
    """Origem dos valores `fields` (códigos de `PER_100_FIELDS`) de uma linha
    quando NÃO são da fonte da linha (`food_items.source`)."""

    fields: tuple[str, ...]
    source: FoodSource
    source_id: Optional[str]
    source_version: Optional[str]
    name: Optional[str]
    retrieved_on: Optional[str]
    reason: Optional[FieldSourceReason]
    citation: Optional[str]
    license: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": list(self.fields),
            "source": self.source.value,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "name": self.name,
            "retrieved_on": self.retrieved_on,
            "reason": self.reason.value if self.reason else None,
            "citation": self.citation,
            "license": self.license,
        }


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    texto = " ".join(str(value).split())
    return texto or None


def _field_source(raw: Any, already: set[str]) -> Optional[FieldSource]:
    if not isinstance(raw, Mapping):
        return None
    fonte = normalize_source(raw.get("source"))
    lista = raw.get("fields")
    if fonte is None or not isinstance(lista, (list, tuple)):
        return None
    pedidos = {str(campo) for campo in lista}
    campos = tuple(campo for campo in PER_100_FIELDS if campo in pedidos and campo not in already)
    if not campos:
        return None
    try:
        motivo: Optional[FieldSourceReason] = FieldSourceReason(str(raw.get("reason")))
    except ValueError:
        motivo = None
    return FieldSource(
        fields=campos,
        source=fonte,
        source_id=_optional_text(raw.get("source_id")),
        source_version=_optional_text(raw.get("source_version")),
        name=_optional_text(raw.get("name")),
        retrieved_on=_optional_text(raw.get("retrieved_on")),
        reason=motivo,
        citation=_optional_text(raw.get("citation")),
        license=_optional_text(raw.get("license")),
    )


def parse_field_sources(raw: Any) -> tuple[FieldSource, ...]:
    """`food_items.field_sources_json` (texto/bytes do banco, lista já
    decodificada ou o que o snapshot do diário guardou) -> origens, na ordem
    gravada. Ausente, malformado ou sem campo válido -> `()`: os valores
    continuam os da linha, só a nota de origem some. Campo fora de
    `PER_100_FIELDS`, fonte desconhecida ou campo já atribuído a uma origem
    anterior são descartados (um campo, uma origem)."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return ()
    if not isinstance(raw, (list, tuple)):
        return ()
    origens: list[FieldSource] = []
    atribuidos: set[str] = set()
    for item in raw:
        origem = _field_source(item, atribuidos)
        if origem is not None:
            origens.append(origem)
            atribuidos.update(origem.fields)
    return tuple(origens)


def field_sources_snapshot(raw: Any) -> list[dict[str, Any]]:
    """Origens na forma guardada no snapshot do item do diário (E4): a mesma de
    `FieldSource.to_dict`, relida por `parse_field_sources`."""
    return [origem.to_dict() for origem in parse_field_sources(raw)]


def field_sources_json(sources: Sequence[FieldSource]) -> Optional[str]:
    """Texto gravado em `food_items.field_sources_json` (chaves ordenadas, sem
    espaço; acentos como estão). Sem origem -> `None` (NULL)."""
    if not sources:
        return None
    return json.dumps([origem.to_dict() for origem in sources], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
