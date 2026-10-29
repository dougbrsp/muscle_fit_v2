"""Leitura da refeição por IA (texto livre -> itens do diário): o vocabulário que
a IA usa, a estimativa por 100 g, a decisão do casamento com o catálogo e a
proposta de cada item (Lote L5b).

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §55; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `leitura_refeicao_ia`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §5 (D2), §8.1, §8.3 e §9 (a).

Fluxo (decisão D2: híbrido):
1. a IA só INTERPRETA o texto em itens (nome, quantidade, unidade do motor,
   gramas estimados, preparo, confiança e uma estimativa por 100 g, sempre que
   souber; ela só é usada quando o alimento não casa). O prompt e o schema estrito são
   de `api/app/nutrition_meal_parse_ai.py` e interpolam o vocabulário daqui;
2. `normalize_ai_items` limpa a saída (unidade fora do vocabulário, número
   inválido, estimativa que não passa nas regras de entrada do catálogo) e
   corta no teto de itens da refeição (`diary.MEAL_MAX_ITEMS`);
3. o worker busca os candidatos no catálogo (FULLTEXT de `food_text`), com e
   sem o preparo (`AiItem.match_queries`; vale a melhor, `best_ranking`), e
   `classify_item` decide, pela nota de `food_text.rank_candidates`: o primeiro
   com aceite automático (`MATCH_AUTO_ACCEPT_SCORE` e folga) é PRÉ-SELECIONADO
   (`SELECTED`); com candidato acima de `MATCH_MIN_SCORE` e nenhum aceite, a
   pessoa ESCOLHE (`CHOICE`); sem candidato, vale a estimativa da IA
   (`ESTIMATED`, natureza ESTIMATED no diário) e o item vira candidato a
   cadastro; sem candidato e sem estimativa válida, `UNMATCHED` (buscar à mão).
   A quantidade proposta são os gramas totais que a IA estimou
   (`proposed_quantity`: a medida em volume não vira massa pela densidade);
4. a tela confirma e grava pelo `POST /nutrition/meals` de sempre: o item do
   catálogo pelo motor do diário (`meal_math`, DERIVED) e o estimado pela
   estimativa da IA (`estimated_item_nutrition`, ESTIMATED).

A cota diária, o job type e o custo da chamada são de `ai_usage` (L5a).

Desvios do rascunho do §9 (a), para não converter nome fora do dono: as
unidades são as de `meal_math.Unit` (medidas caseiras de `GENERIC_PORTIONS`,
sem `slice`/`piece`: fatia e pedaço viram `unit` com os gramas estimados), e a
estimativa usa os códigos da RDC 429 do catálogo (`energy_kcal`, `protein_g`,
`carbohydrates_g`, `total_fat_g`, `fiber_g`), não `kcal`/`carbs_g`/`fat_g`. O
sódio fica fora da estimativa, como no plano: no item estimado ele é
desconhecido (total incompleto em sódio, nunca 0 inventado). Os candidatos são
os `CANDIDATES_PER_ITEM` melhores PELA NOTA dentro do lote do FULLTEXT
(`food_text.SEARCH_CANDIDATE_POOL`), a mesma busca da tela calibrada na TACO no
L3a, e não os 5 primeiros do FULLTEXT.

Defaults de desenho, SEM fonte bibliográfica: `TEXT_MAX_CHARS` (uma refeição
descrita cabe com folga), `CANDIDATES_PER_ITEM` (o número do plano) e
`ITEM_LABEL_MAX_CHARS` (a coluna `meal_items.item_label`, VARCHAR(255)).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Hashable, Mapping, Optional, Sequence

from . import catalog, food_text
from .diary import MEAL_MAX_ITEMS, MealSource
from .meal_math import (
    CARB_FIELD,
    ENERGY_FIELD,
    FAT_FIELD,
    FIBER_FIELD,
    PER_100G_REFERENCE,
    PORTION_UNITS,
    PROTEIN_FIELD,
    AmountResolution,
    ItemNutrition,
    MealNature,
    Nutrients,
    Unit,
    item_nutrition,
    normalize_unit,
    nutrients_from_values,
)

# ---------------------------------------------------------------------------
# Entrada e vocabulário da IA
# ---------------------------------------------------------------------------

TEXT_MAX_CHARS: Final[int] = 1000
CANDIDATES_PER_ITEM: Final[int] = 5
ITEM_LABEL_MAX_CHARS: Final[int] = 255

# Unidades que a IA pode usar: as do diário menos a porção do rótulo/catálogo,
# que a IA não conhece. `unit` (1 ovo, 2 fatias) entra porque é como se descreve
# a refeição; sem a porção cadastrada, a proposta usa os gramas estimados.
AI_UNITS: Final[tuple[Unit, ...]] = tuple(unidade for unidade in Unit if unidade is not Unit.PORTION)

# Unidade quando a pessoa não disse a quantidade (o prompt pede esta à IA) e a
# da proposta sem medida que converta: gramas, a base da estimativa.
UNSTATED_QUANTITY_UNIT: Final[Unit] = Unit.GRAM

# Estimativa da IA: por `PER_100G_REFERENCE` g, nos códigos do catálogo.
ESTIMATE_BASIS: Final[Unit] = Unit.GRAM
ESTIMATE_FIELDS: Final[tuple[str, ...]] = (ENERGY_FIELD, PROTEIN_FIELD, CARB_FIELD, FAT_FIELD, FIBER_FIELD)
ESTIMATE_REQUIRED_FIELDS: Final[tuple[str, ...]] = catalog.REQUIRED_PER_100_FIELDS
ESTIMATE_KEY: Final[str] = f"estimated_per_{PER_100G_REFERENCE:g}{ESTIMATE_BASIS.value}"
ESTIMATE_SOURCE: Final[catalog.FoodSource] = catalog.FoodSource.AI_ESTIMATE
# A confiança que a IA declara (só informativa: nenhuma decisão usa o valor).
CONFIDENCE_RANGE: Final[tuple[float, float]] = (0.0, 1.0)


class MatchStatus(str, Enum):
    SELECTED = "selected"  # catálogo pré-selecionado (aceite automático do `food_text`)
    CHOICE = "choice"  # há candidatos, nenhum com aceite automático: a pessoa escolhe
    ESTIMATED = "estimated"  # sem candidato: estimativa da IA + candidato a cadastro
    UNMATCHED = "unmatched"  # sem candidato e sem estimativa válida: buscar à mão


# Estados em que o item não casou com o catálogo (D2: "vira candidato a cadastro").
CATALOG_CANDIDATE_STATUSES: Final[frozenset[MatchStatus]] = frozenset({MatchStatus.ESTIMATED, MatchStatus.UNMATCHED})

# Entrada da rota.
ERR_TEXT_EMPTY = "meal_text_empty"
ERR_TEXT_TOO_LONG = "meal_text_too_long"
# Limitações do item lido.
LIMIT_AI_ESTIMATE = "ai_estimate"
LIMIT_ESTIMATE_INVALID = "ai_estimate_invalid"
LIMIT_QUANTITY_FROM_GRAMS = "quantity_from_grams_estimate"
LIMIT_QUANTITY_MISSING = "ai_quantity_missing"
# Limitação da leitura inteira.
LIMIT_ITEMS_TRUNCATED = "ai_items_truncated"
LIMIT_NO_ITEMS = "ai_no_items"
# `error_code` do `ai_usage_log` quando a leitura falha (a cota já contou a tentativa).
ERR_PARSE_FAILED = "meal_parse_failed"
ERR_MATCH_FAILED = "meal_match_failed"
ERR_DISPATCH_FAILED = "ai_dispatch_failed"
ERR_WORKER_FAILED = "ai_worker_failed"


def validate_text(raw: Any) -> tuple[str, tuple[str, ...]]:
    """Texto da refeição sem espaços sobrando e os erros de entrada (vazio, longo)."""
    texto = " ".join(str(raw or "").split())
    if not texto:
        return "", (ERR_TEXT_EMPTY,)
    if len(texto) > TEXT_MAX_CHARS:
        return texto, (ERR_TEXT_TOO_LONG,)
    return texto, ()


def meal_source(ai_job_id: Any) -> MealSource:
    """Origem da refeição gravada: `ai_text` quando veio de uma leitura por IA."""
    return MealSource.AI_TEXT if str(ai_job_id or "").strip() else MealSource.MANUAL


# ---------------------------------------------------------------------------
# Saída da IA -> itens limpos
# ---------------------------------------------------------------------------


def _positive(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) and numero > 0 else None


def normalize_confidence(value: Any) -> Optional[float]:
    """Confiança/nota em `CONFIDENCE_RANGE` (a da IA e a nota do casamento que a
    tela devolve para `meal_items.match_confidence`); inválida -> None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numero):
        return None
    return min(max(numero, CONFIDENCE_RANGE[0]), CONFIDENCE_RANGE[1])


def _text(value: Any) -> Optional[str]:
    texto = " ".join(str(value or "").split())[:ITEM_LABEL_MAX_CHARS]
    return texto or None


@dataclass(frozen=True)
class EstimateValidation:
    """Estimativa por 100 g validada pelas regras de entrada do catálogo."""

    nutrients: Optional[Nutrients]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def validate_estimate(values: Any) -> EstimateValidation:
    """Estimativa por 100 g (da IA ou devolvida pela tela): só os
    `ESTIMATE_FIELDS`, com as regras de entrada do catálogo
    (`catalog.validate_food_values`: obrigatórios, número >= 0, teto da base).
    Inválida -> `nutrients` None e os códigos do catálogo; Atwater fora da
    tolerância é aviso, não erro."""
    if not isinstance(values, Mapping):
        return EstimateValidation(None, (LIMIT_ESTIMATE_INVALID,), ())
    so_estimativa = {campo: values.get(campo) for campo in ESTIMATE_FIELDS}
    validacao = catalog.validate_food_values(so_estimativa, ESTIMATE_BASIS, required=ESTIMATE_REQUIRED_FIELDS)
    if validacao.errors:
        return EstimateValidation(None, tuple(validacao.errors), tuple(validacao.warnings))
    numeros = {campo: validacao.values.get(campo) for campo in ESTIMATE_FIELDS}
    return EstimateValidation(nutrients_from_values(numeros), (), tuple(validacao.warnings))


@dataclass(frozen=True)
class AiItem:
    """Um item como a IA leu, já limpo. `estimate` = por 100 g, validada."""

    name: str
    quantity: Optional[float]
    unit: Optional[Unit]
    grams_estimate: Optional[float]
    preparation: Optional[str]
    confidence: Optional[float]
    estimate: Optional[Nutrients]
    limitations: tuple[str, ...] = ()

    @property
    def match_queries(self) -> tuple[str, ...]:
        """Buscas no catálogo, na ordem de preferência: o nome com o preparo (se
        o preparo acrescenta algo ao nome) e o nome sozinho. O preparo distingue
        itens da TACO ("cozido" × "cru"), mas derruba a nota de quem não o tem no
        nome ("feijoada cozida" × "Feijoada"): vale a melhor das buscas
        (`best_ranking`)."""
        if self.preparation and not food_text.tokens(self.preparation) <= food_text.tokens(self.name):
            return (f"{self.name} {self.preparation}", self.name)
        return (self.name,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "quantity": self.quantity,
            "unit": self.unit.value if self.unit else None,
            "grams_estimate": self.grams_estimate,
            "preparation": self.preparation,
            "confidence": self.confidence,
        }


def normalize_ai_item(raw: Any) -> Optional[AiItem]:
    """Um item cru da IA -> `AiItem` (None sem nome). Unidade fora de
    `AI_UNITS` some (a proposta cai nos gramas estimados); estimativa inválida
    some com `LIMIT_ESTIMATE_INVALID`."""
    if not isinstance(raw, Mapping):
        return None
    nome = _text(raw.get("name"))
    if nome is None:
        return None
    unidade = normalize_unit(raw.get("unit"))
    if unidade not in AI_UNITS:
        unidade = None
    limitacoes: list[str] = []
    estimativa = None
    bruta = raw.get(ESTIMATE_KEY)
    if bruta is not None:
        validada = validate_estimate(bruta)
        if validada.nutrients is None:
            limitacoes.append(LIMIT_ESTIMATE_INVALID)
        else:
            estimativa = validada.nutrients
            limitacoes.extend(validada.warnings)
    return AiItem(
        name=nome,
        quantity=_positive(raw.get("quantity")),
        unit=unidade,
        grams_estimate=_positive(raw.get("grams_estimate")),
        preparation=_text(raw.get("preparation")),
        confidence=normalize_confidence(raw.get("confidence")),
        estimate=estimativa,
        limitations=tuple(limitacoes),
    )


def normalize_ai_items(raw_items: Any) -> tuple[tuple[AiItem, ...], tuple[str, ...]]:
    """Itens limpos (na ordem da IA, até `MEAL_MAX_ITEMS`) e as limitações da leitura."""
    brutos = raw_items if isinstance(raw_items, (list, tuple)) else ()
    itens = [item for item in (normalize_ai_item(bruto) for bruto in brutos) if item is not None]
    limitacoes: list[str] = []
    if not itens:
        limitacoes.append(LIMIT_NO_ITEMS)
    if len(itens) > MEAL_MAX_ITEMS:
        limitacoes.append(LIMIT_ITEMS_TRUNCATED)
    return tuple(itens[:MEAL_MAX_ITEMS]), tuple(limitacoes)


# ---------------------------------------------------------------------------
# Casamento com o catálogo e proposta do item
# ---------------------------------------------------------------------------


def proposed_quantity(item: AiItem) -> tuple[Optional[float], Unit, tuple[str, ...]]:
    """Quantidade e unidade da proposta: os gramas TOTAIS que a IA estimou
    (`grams_estimate`), com `LIMIT_QUANTITY_FROM_GRAMS` quando a pessoa não os
    disse em gramas. A unidade contável precisa de porção cadastrada, e a medida
    em volume (ml, colher, xícara) só vira massa pela densidade do alimento, que
    a TACO não tem (o diário assumiria `DEFAULT_DENSITY_G_PER_ML`: uma xícara de
    arroz cozido ou de folhas pesaria o mesmo que uma de água); os gramas da IA
    já consideram o alimento (eval do L5b: a medida da IA convertida pelo diário
    errou a alface e o arroz). Sem os gramas, a medida da IA quando converte sem
    porção; sem nenhum dos dois, sem quantidade (a pessoa informa)."""
    if item.grams_estimate is not None:
        dita_em_gramas = item.unit is Unit.GRAM and item.quantity == item.grams_estimate
        return item.grams_estimate, Unit.GRAM, () if dita_em_gramas else (LIMIT_QUANTITY_FROM_GRAMS,)
    if item.quantity is not None and item.unit is not None and item.unit not in PORTION_UNITS:
        return item.quantity, item.unit, ()
    sem_medida = item.unit is None or item.unit in PORTION_UNITS
    return None, UNSTATED_QUANTITY_UNIT if sem_medida else item.unit, (LIMIT_QUANTITY_MISSING,)


@dataclass(frozen=True)
class ParsedItem:
    """Um item lido + a decisão do casamento + a proposta para o diário."""

    index: int
    ai: AiItem
    status: MatchStatus
    candidates: tuple[food_text.Match, ...]
    food_id: Optional[Hashable]
    quantity: Optional[float]
    unit: Unit
    limitations: tuple[str, ...]

    @property
    def candidate_for_catalog(self) -> bool:
        return self.status in CATALOG_CANDIDATE_STATUSES

    @property
    def match_confidence(self) -> Optional[float]:
        """Nota do candidato pré-selecionado (`meal_items.match_confidence`)."""
        if self.food_id is None:
            return None
        return next((m.score for m in self.candidates if m.candidate_id == self.food_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ai": self.ai.to_dict(),
            "status": self.status.value,
            "candidates": [m.to_dict() for m in self.candidates],
            "food_id": self.food_id,
            "match_confidence": self.match_confidence,
            "quantity": self.quantity,
            "unit": self.unit.value,
            # Estimativa por 100 g (só os `ESTIMATE_FIELDS`): a proposta do item
            # `ESTIMATED` e a alternativa que a tela oferece no `CHOICE`.
            "estimate": (
                {"name": self.ai.name, **{campo: self.ai.estimate.get(campo) for campo in ESTIMATE_FIELDS}}
                if self.ai.estimate
                else None
            ),
            "candidate_for_catalog": self.candidate_for_catalog,
            # A proposta do item é a estimativa da IA (só no `ESTIMATED`); a tela não deriva isso.
            "uses_estimate": self.status is MatchStatus.ESTIMATED,
            "limitations": list(self.limitations),
        }


def best_ranking(rankings: Sequence[Sequence[food_text.Match]]) -> tuple[food_text.Match, ...]:
    """A melhor das buscas de um item (`AiItem.match_queries`, na mesma ordem):
    a de maior nota no primeiro candidato; no empate, a primeira (com o
    preparo). Cada ranking vem inteiro de `food_text.rank_candidates`, então o
    aceite automático (nota e folga sobre o segundo) é o do próprio dono."""
    melhor: tuple[food_text.Match, ...] = ()
    for ranking in rankings:
        if ranking and (not melhor or ranking[0].score > melhor[0].score):
            melhor = tuple(ranking)
    return melhor


def classify_item(index: int, item: AiItem, ranked: Sequence[food_text.Match]) -> ParsedItem:
    """Decisão do casamento (ver o módulo). `ranked` = `best_ranking` das buscas
    `match_queries` (cada uma `food_text.rank_candidates` sobre os candidatos do
    catálogo: só os de nota a partir de `MATCH_MIN_SCORE`, na ordem da nota)."""
    candidatos = tuple(ranked[:CANDIDATES_PER_ITEM])
    topo = candidatos[0] if candidatos else None
    alimento: Optional[Hashable] = None
    if topo is not None and topo.auto_accept:
        status, alimento = MatchStatus.SELECTED, topo.candidate_id
    elif candidatos:
        status = MatchStatus.CHOICE
    elif item.estimate is not None:
        status = MatchStatus.ESTIMATED
    else:
        status = MatchStatus.UNMATCHED
    quantidade, unidade, limitacoes_qtd = proposed_quantity(item)
    limitacoes = [*item.limitations, *limitacoes_qtd]
    if status is MatchStatus.ESTIMATED:
        limitacoes.append(LIMIT_AI_ESTIMATE)
    return ParsedItem(
        index=index,
        ai=item,
        status=status,
        candidates=candidatos,
        food_id=alimento,
        quantity=quantidade,
        unit=unidade,
        limitations=tuple(dict.fromkeys(limitacoes)),
    )


@dataclass(frozen=True)
class MealParseResult:
    """Resultado da leitura (o `result` do job): itens com a decisão e as
    limitações da leitura. A tela monta o editor da refeição a partir daqui."""

    items: tuple[ParsedItem, ...]
    limitations: tuple[str, ...]
    notes: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "limitations": list(self.limitations),
            "notes": self.notes,
        }


def build_result(
    items: Sequence[AiItem], ranked_by_item: Sequence[Sequence[food_text.Match]], limitations: Sequence[str] = (),
    notes: Any = None,
) -> MealParseResult:
    """Junta os itens lidos com o casamento de cada um (mesma ordem)."""
    if len(items) != len(ranked_by_item):
        raise ValueError("um casamento por item lido")
    nota = " ".join(str(notes).split()) if notes else ""
    return MealParseResult(
        items=tuple(classify_item(indice, item, ranked) for indice, (item, ranked) in enumerate(zip(items, ranked_by_item))),
        limitations=tuple(limitations),
        notes=nota or None,
    )


# ---------------------------------------------------------------------------
# Item estimado no diário (`POST/PUT /nutrition/meals`)
# ---------------------------------------------------------------------------


def estimate_snapshot(name: Any, estimate: Nutrients) -> dict[str, Any]:
    """`catalog_snapshot_json` do item estimado pela IA (E4): a mesma forma do
    snapshot do catálogo, sem id, com a fonte `ai_estimate` e a base g."""
    return {
        "food": {
            "id": None,
            "source": ESTIMATE_SOURCE.value,
            "name": _text(name),
            "basis": ESTIMATE_BASIS.value,
            "per_100": {campo: estimate.get(campo) for campo in ESTIMATE_FIELDS},
        },
        "portion": None,
    }


def snapshot_is_estimate(food_snapshot: Any) -> bool:
    """O snapshot gravado é de um item estimado pela IA?"""
    return isinstance(food_snapshot, Mapping) and food_snapshot.get("source") == ESTIMATE_SOURCE.value


def estimated_item_nutrition(estimate: Nutrients, resolution: AmountResolution) -> Optional[ItemNutrition]:
    """Nutrientes do item estimado: a mesma conta do diário (`meal_math.item_nutrition`,
    com a regra do macro ausente) sobre a estimativa por 100 g, natureza
    ESTIMATED e a limitação `LIMIT_AI_ESTIMATE`. Sem quantidade -> None."""
    nutricao = item_nutrition(estimate, resolution, nature=MealNature.ESTIMATED)
    if nutricao is None:
        return None
    return dataclasses.replace(nutricao, limitations=tuple(dict.fromkeys((*nutricao.limitations, LIMIT_AI_ESTIMATE))))


__all__ = [
    "AI_UNITS",
    "CANDIDATES_PER_ITEM",
    "CATALOG_CANDIDATE_STATUSES",
    "CONFIDENCE_RANGE",
    "ERR_DISPATCH_FAILED",
    "ERR_MATCH_FAILED",
    "ERR_PARSE_FAILED",
    "ERR_TEXT_EMPTY",
    "ERR_TEXT_TOO_LONG",
    "ERR_WORKER_FAILED",
    "ESTIMATE_BASIS",
    "ESTIMATE_FIELDS",
    "ESTIMATE_KEY",
    "ESTIMATE_REQUIRED_FIELDS",
    "ESTIMATE_SOURCE",
    "ITEM_LABEL_MAX_CHARS",
    "LIMIT_AI_ESTIMATE",
    "LIMIT_ESTIMATE_INVALID",
    "LIMIT_ITEMS_TRUNCATED",
    "LIMIT_NO_ITEMS",
    "LIMIT_QUANTITY_FROM_GRAMS",
    "LIMIT_QUANTITY_MISSING",
    "TEXT_MAX_CHARS",
    "UNSTATED_QUANTITY_UNIT",
    "AiItem",
    "EstimateValidation",
    "MatchStatus",
    "MealParseResult",
    "ParsedItem",
    "best_ranking",
    "build_result",
    "classify_item",
    "estimate_snapshot",
    "estimated_item_nutrition",
    "meal_source",
    "normalize_ai_item",
    "normalize_ai_items",
    "normalize_confidence",
    "proposed_quantity",
    "snapshot_is_estimate",
    "validate_estimate",
    "validate_text",
]
