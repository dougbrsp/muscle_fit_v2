"""JSON Schema ESTRITO da saida da LLM (PLANO_20260912.md secao 7.2) e o
espelho pydantic que o backend usa para reler essa saida.

Duas travas, de proposito redundantes:

- o schema vai para a OpenAI em `text.format={type:"json_schema",
  strict:true}`: o servidor da OpenAI recusa uma saida que nao case com ele;
- o mesmo contrato e revalidado aqui por pydantic (`PeriodizationOutput`),
  porque o backend nunca confia em validacao que aconteceu do outro lado da
  rede - resposta fora do formato vira erro `SCHEMA_INVALID` no relatorio, nao
  excecao no meio do validador.

Regras do modo `strict` da OpenAI que o schema abaixo respeita:
- TODA propriedade de um objeto entra em `required` (campo "opcional" e
  expresso como tipo anulavel, `["integer", "null"]`);
- todo objeto tem `additionalProperties: false`;
- nada de `minimum`/`maximum`/`pattern`/`minLength` (nao suportados no modo
  estrito). Faixa numerica, portanto, NAO e responsabilidade do schema: quem
  garante 0-100, RPE <= teto e sets_count >= 1 e o validador deterministico
  (`validation.py`) + `_validate_template_payload`. O schema garante FORMA;
  o validador garante SEMANTICA.

`item_order` nao esta no schema de proposito: a ordem e a posicao no array,
preenchida deterministicamente pelo backend. Pedir para a LLM numerar itens so
criaria uma forma extra de a saida estar errada.

`item_kind` tem so `muscle_group` e `equipment` (os dois modos de volume da
secao 6). O cardio (M98 C5) NAO entra em `items`: vai numa lista propria do
slot, `cardio_items`, e so no schema COM cardio (`OUTPUT_SCHEMA_WITH_CARDIO`,
usado quando o Modelo pediu cardio; sem pedido, o schema e o da v12). Motivo:
no modo estrito todo campo e obrigatorio, e os campos de cardio dentro de
`items` custariam ~10 nulos por exercicio na SAIDA (a parte cara e lenta da
geracao). Cada item de cardio tem `cardio_mode` (enum do dono
`cardio_prescription`) e os campos da prescricao com os nomes das colunas de
`workout_plan_template_slot_items`; os do outro modo vem null e a
normalizacao do dono os descarta. Limites nao vao no schema: quem valida e o
dono (`clamp_fields` + `validate`, em `periodization/cardio_plan.py`).
"""

from __future__ import annotations

from typing import Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict

from ..domain import cardio_prescription
from .catalog_refs import CATALOG_REF_FIELD
from ..llm_anthropic_schema import ITEMS as _ITEMS


def _nullable(*types: str) -> dict:
    return {"type": [*types, "null"]}


_ITEM_PROPERTIES = {
    "item_kind": {"type": "string", "enum": ["muscle_group", "equipment"]},
    "muscle_group_id": _nullable("integer"),
    "priority_min_pct": _nullable("integer"),
    "priority_max_pct": _nullable("integer"),
    "prioritize": {"type": "boolean"},
    "mapping_id": _nullable("integer"),
    "equipment_id": _nullable("integer"),
    "sets_count": _nullable("integer"),
    "reps_target": _nullable("string"),
    "rpe_target": _nullable("string"),
    "advanced_technique": _nullable("string"),
    "advanced_technique_applications": _nullable("integer"),
    "advanced_technique_duration_seconds": _nullable("integer"),
    "warmup_sets_count": _nullable("integer"),
    "warmup_reps": _nullable("integer"),
    "warmup_rest_seconds": _nullable("integer"),
    "feeler_set_sets_count": _nullable("integer"),
    "feeler_set_reps": _nullable("integer"),
    "feeler_set_rest_seconds": _nullable("integer"),
    "peak_contraction_seconds": _nullable("integer"),
}
# `rest_seconds` (Fase 7, Frente B) NAO vai mais ao schema da LLM (v15): e
# regra do motor que o descanso do item gerado seja null
# (`validation._FORCED_NULL_ITEM_FIELDS` zera o que vier). Com o campo no
# schema a LLM o preenchia (eval de 2026-10-31, rodada 2) e pagava tokens de
# saida por um valor descartado. O espelho pydantic segue aceitando o campo.

# v15 (escolha guiada pelo motor): no Modo B o item e SEMPRE de equipamento e
# a LLM so escolhe a referencia do catalogo (`catalog_refs`); o trio, o
# `item_kind` e a faixa percentual (so do Modo A) saem do schema. O motor
# monta o trio a partir da referencia (`catalog_refs.resolve_output`) antes de
# qualquer validacao.
_MODE_B_DROPPED_FIELDS = (
    "item_kind",
    "muscle_group_id",
    "priority_min_pct",
    "priority_max_pct",
    "mapping_id",
    "equipment_id",
)


def _mode_b_item_properties(refs: Optional[Sequence[str]]) -> dict:
    ref_schema: dict = {"type": "string"}
    if refs:
        ref_schema["enum"] = list(refs)
    return {
        CATALOG_REF_FIELD: ref_schema,
        **{name: spec for name, spec in _ITEM_PROPERTIES.items() if name not in _MODE_B_DROPPED_FIELDS},
    }

# M98 C5: prescricao de cardio do slot (nomes = colunas do item do Modelo).
_CARDIO_NUMERIC_FIELDS = tuple(
    name for name in cardio_prescription.PRESCRIPTION_FIELDS if name != cardio_prescription.MODE_FIELD
)
_CARDIO_ITEM_PROPERTIES = {
    cardio_prescription.MODE_FIELD: {"type": "string", "enum": list(cardio_prescription.CARDIO_MODE_VALUES)},
    **{name: _nullable("integer") for name in _CARDIO_NUMERIC_FIELDS},
}
CARDIO_ITEMS_FIELD = "cardio_items"

def _items_schema(item_properties: dict) -> dict:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": item_properties,
            "required": list(item_properties),
        },
    }


_SLOT_BASE_PROPERTIES = {
    "sequence": {"type": "integer"},
    "name": {"type": "string"},
    "slot_type": {"type": "string", "enum": ["normal", "descanso"]},
    "weekday": _nullable("integer"),
}


def _slot_schema(with_cardio: bool, item_properties: dict) -> dict:
    properties = {**_SLOT_BASE_PROPERTIES, "items": _items_schema(item_properties)}
    if with_cardio:
        properties[CARDIO_ITEMS_FIELD] = {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": _CARDIO_ITEM_PROPERTIES,
                "required": list(_CARDIO_ITEM_PROPERTIES),
            },
        }
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


def _output_schema(with_cardio: bool, item_properties: dict) -> dict:
    template_properties = {
        "name": {"type": "string"},
        "description": _nullable("string"),
        "volume_mode": {"type": "string", "enum": ["percent_budget", "equipment_series"]},
        "slots": {"type": "array", "items": _slot_schema(with_cardio, item_properties)},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "template": {
                "type": "object",
                "additionalProperties": False,
                "properties": template_properties,
                "required": list(template_properties),
            },
            "rationale": {"type": "string"},
            "source_citations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["template", "rationale", "source_citations"],
    }


SCHEMA_NAME = "periodization_draft"

# Modo A (percentual): o schema da v12/v14 sem `rest_seconds`.
OUTPUT_SCHEMA: dict = _output_schema(False, _ITEM_PROPERTIES)
# Com cardio pedido (M98 C5): o slot ganha `cardio_items`.
OUTPUT_SCHEMA_WITH_CARDIO: dict = _output_schema(True, _ITEM_PROPERTIES)


def output_schema(with_cardio: bool, mode_b: bool = False, catalog_refs: Optional[Sequence[str]] = None) -> dict:
    """Schema da chamada. Modo A: o fixo (`OUTPUT_SCHEMA[_WITH_CARDIO]`).
    Modo B (`mode_b`): o item so com a referencia do catalogo, restrita a
    `catalog_refs` (`catalog_refs.schema_enum`; None = string livre, quando a
    `enum` nao cabe nos limites dos provedores) - um schema por pedido."""
    if not mode_b:
        return OUTPUT_SCHEMA_WITH_CARDIO if with_cardio else OUTPUT_SCHEMA
    return _output_schema(with_cardio, _mode_b_item_properties(catalog_refs))


# ---------------------------------------------------------------------------
# Adaptacao para a Anthropic (so o caminho Claude; o schema acima nao muda)
# ---------------------------------------------------------------------------
#
# O subconjunto da Anthropic aceita no maximo 16 unioes por pedido
# (`llm_anthropic_schema.MAX_UNION_PARAMS`): o adaptador converte o excedente
# de anulaveis em campos OPCIONAIS ("ausente" = null na volta). Desde a v15
# (sem `rest_seconds`; no Modo B tambem sem trio, `item_kind` e faixa
# percentual) sobram: Modo A 4 sem cardio e 13 com cardio; Modo B nenhum sem
# cardio e 8 com cardio (`test_llm_anthropic_schema.py`). Esta lista diz QUAIS converter primeiro: os campos
# do item de forca que o prompt manda deixar null ou que so existem com
# tecnica/aquecimento/serie de ajuste, os de cardio que o dono trata "ausente"
# como 0 (`cardio_prescription.ZERO_WHEN_MISSING_FIELDS`) e a descricao do
# Modelo. Ficam anulaveis OBRIGATORIOS os que o dono `cardio_prescription`
# exige por modo (`REQUIRED_FIELDS`: duracao da sessao continua, series do
# HIIT): no eval de 2026-10-30, convertidos pela ordem do schema, eles foram
# omitidos em 2 de 4 rodadas.
_ITEM_PATH = ("template", "slots", _ITEMS, "items", _ITEMS)
_CARDIO_PATH = ("template", "slots", _ITEMS, CARDIO_ITEMS_FIELD, _ITEMS)
_ITEM_OPTIONAL_FIRST = (
    "peak_contraction_seconds",
    "feeler_set_rest_seconds",
    "feeler_set_reps",
    "feeler_set_sets_count",
    "warmup_rest_seconds",
    "warmup_reps",
    "warmup_sets_count",
    "advanced_technique_duration_seconds",
    "advanced_technique_applications",
    "advanced_technique",
)
ANTHROPIC_OPTIONAL_FIRST: tuple[tuple[str, ...], ...] = (
    *(_ITEM_PATH + (name,) for name in _ITEM_OPTIONAL_FIRST),
    *(
        _CARDIO_PATH + (name,)
        for name in _CARDIO_NUMERIC_FIELDS
        if name in cardio_prescription.ZERO_WHEN_MISSING_FIELDS
    ),
    ("template", "description"),
)


# ---------------------------------------------------------------------------
# Espelho pydantic (revalidacao local do mesmo contrato)
# ---------------------------------------------------------------------------


class PeriodizationItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_kind: Literal["muscle_group", "equipment"]
    muscle_group_id: Optional[int] = None
    priority_min_pct: Optional[int] = None
    priority_max_pct: Optional[int] = None
    prioritize: bool = False
    mapping_id: Optional[int] = None
    equipment_id: Optional[int] = None
    sets_count: Optional[int] = None
    reps_target: Optional[str] = None
    rpe_target: Optional[str] = None
    advanced_technique: Optional[str] = None
    advanced_technique_applications: Optional[int] = None
    advanced_technique_duration_seconds: Optional[int] = None
    warmup_sets_count: Optional[int] = None
    warmup_reps: Optional[int] = None
    warmup_rest_seconds: Optional[int] = None
    feeler_set_sets_count: Optional[int] = None
    feeler_set_reps: Optional[int] = None
    feeler_set_rest_seconds: Optional[int] = None
    peak_contraction_seconds: Optional[int] = None
    rest_seconds: Optional[int] = None


class PeriodizationCardioOut(BaseModel):
    """Prescricao de cardio crua (M98 C5). Numeros como `float` de proposito:
    um numero fracionario nao derruba o rascunho inteiro em `SCHEMA_INVALID` -
    o dono arredonda/limita (`clamp_fields`) e registra."""

    model_config = ConfigDict(extra="forbid")

    cardio_mode: str
    cardio_duration_minutes: Optional[float] = None
    cardio_warmup_minutes: Optional[float] = None
    cardio_cooldown_minutes: Optional[float] = None
    cardio_target_zone: Optional[float] = None
    hiit_low_seconds: Optional[float] = None
    hiit_high_seconds: Optional[float] = None
    hiit_low_zone: Optional[float] = None
    hiit_high_zone: Optional[float] = None
    hiit_rounds: Optional[float] = None


class PeriodizationSlotOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    name: str
    slot_type: Literal["normal", "descanso"] = "normal"
    weekday: Optional[int] = None
    items: list[PeriodizationItemOut] = []
    # M98 C5: ausente na saida sem cardio (schema da v12) = lista vazia.
    cardio_items: list[PeriodizationCardioOut] = []


class PeriodizationTemplateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: Optional[str] = None
    volume_mode: Literal["percent_budget", "equipment_series"]
    slots: list[PeriodizationSlotOut] = []


class PeriodizationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: PeriodizationTemplateOut
    rationale: str = ""
    source_citations: list[str] = []
