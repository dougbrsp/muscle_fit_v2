"""Uso e cota de IA das features de nutrição: vocabulário, cota diária, job type,
fechamento da linha e custo (tabela `ai_usage_log`, migração 085).

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §54; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `cota_ia_nutricao`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §5 (E7, E8), §6.2, §8.3 e
§13. Nasceu no Lote L5a, a base que o L5b (texto da refeição) e o L6 (foto do
rótulo) consomem; o acesso ao banco é de `api/app/nutrition_ai_quota.py`.

Features (`AiFeature`): `meal_parse` (L5b, texto da refeição → itens) e
`label_photo` (L6, foto do rótulo → RDC 429). A foto da refeição (L10) entra
aqui quando o lote abrir. O L5b/L6 usa o mesmo código como rótulo `feature` do
evento `llm.usage` e como chave de `openai_client.LLM_OUTPUT_OBSERVED_MAX_TOKENS`
(um vocabulário só).

Ciclo da linha (`AiUsageStatus`): a rota que responde 202 abre `pending`; o
worker fecha como `succeeded` ou `failed`. O sweep fecha como `failed` (código
`async_jobs.WORKER_LOST_ERROR_CODE`) a linha `pending` mais velha que o prazo do
worker perdido do M73 para o job type da feature (`FEATURE_JOB_TYPE` →
`async_jobs.worker_lost_deadline_seconds`: timeout da Lambda + folga, fonte
única com paridade com `infra/*.tf`). O relato do worker vence a presunção do
sweep: o `failed` por prazo pode ser sobrescrito pelo resultado real; nenhum
outro estado terminal é reaberto.

Cota (`DAILY_QUOTA`, plano §8.3; defaults de desenho, sem fonte científica):
tentativas por usuário, feature e DIA DO USUÁRIO (fuso do perfil). Toda linha
conta, qualquer status — como a periodização (10/dia) e os cards (100/dia): uma
falha também gastou tokens, e contar só sucesso deixaria um laço de falhas sem
teto. O "hoje" e o início do dia no relógio do banco são de `time_windows`
(M76 Lote 2b, §12); quem conta é o serviço.

Custo (`estimate_cost_usd`): tokens × preço por modelo da tabela
`MODEL_PRICES_USD_CENTS_PER_MTOKEN` (centavos de dólar por 1 milhão de tokens,
fonte em `PRICE_SOURCE_URL`, consultada em `PRICES_CHECKED_ON`). Modelo fora da
tabela ou token desconhecido → None (coluna NULL), nunca um preço inventado; os
tokens ficam gravados para a conta posterior. Sem a contagem de tokens em cache,
todo token de entrada paga o preço cheio: o custo é um teto. Snapshot datado
(`gpt-5.4-mini-2026-03-17`) usa o preço do modelo-base. Preço muda: atualizar a
tabela, a fonte e a data juntos.

Modelos Claude (Anthropic, fonte em `ANTHROPIC_PRICE_SOURCE_URL`): a mesma
tabela, mais a faixa de prompt longo do Haiku 5.5 (`MODEL_LONG_PROMPT_PRICES`:
prompt ACIMA do limiar paga outra tabela; o "prompt" conta toda a entrada,
inclusive cache). Escrita de cache (1,25x na Anthropic) entra pelo preço cheio
da entrada: o custo do Claude com cache é uma estimativa por baixo. O dono do
preço por modelo de TODA chamada de texto é este módulo (mapa §74);
`periodization.logs` consome `estimate_cost_usd`.

Módulo PURO: sem banco, sem FastAPI, sem I/O e sem relógio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Mapping, Optional


class AiFeature(str, Enum):
    MEAL_PARSE = "meal_parse"
    LABEL_PHOTO = "label_photo"


class AiUsageStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_STATUSES: Final[frozenset[AiUsageStatus]] = frozenset({AiUsageStatus.SUCCEEDED, AiUsageStatus.FAILED})
# Fechamento: o worker fecha a linha ainda aberta; o sweep presume perdida a
# `pending` vencida e a fecha com este status (e o código de perda do M73).
OPEN_STATUS: Final[AiUsageStatus] = AiUsageStatus.PENDING
SWEEP_PRESUMED_STATUS: Final[AiUsageStatus] = AiUsageStatus.FAILED

ERR_AI_QUOTA_EXCEEDED = "ai_quota_exceeded"

# ---------------------------------------------------------------------------
# Cota diária por feature (plano §8.3; defaults de desenho)
# ---------------------------------------------------------------------------

MEAL_PARSE_DAILY_QUOTA: Final[int] = 30
LABEL_PHOTO_DAILY_QUOTA: Final[int] = 10
DAILY_QUOTA: Final[Mapping[AiFeature, int]] = MappingProxyType(
    {
        AiFeature.MEAL_PARSE: MEAL_PARSE_DAILY_QUOTA,
        AiFeature.LABEL_PHOTO: LABEL_PHOTO_DAILY_QUOTA,
    }
)

# ---------------------------------------------------------------------------
# Job type de cada feature (plano §8.3)
# ---------------------------------------------------------------------------
# Sem anotação de tipo de propósito: `test_m73_async_jobs_worker_lost` resolve o
# job type de `async_jobs.create_job(<CONSTANTE>, ...)` lendo `NOME = "valor"` dos
# módulos de `api/app`. A rota do L5b/L6 passa a constante daqui, e o job type
# entra em `async_jobs.JOB_TYPE_LAMBDA` no mesmo PR (o teste do M73 exige): é
# isso que dá o prazo do sweep da feature (texto na `musclefit-workout-ai`,
# imagem na `musclefit-plan-generator`, decisão E8 do plano).
MEAL_PARSE_JOB_TYPE = "nutrition-meal-parse"
LABEL_PHOTO_JOB_TYPE = "nutrition-label-ocr"
FEATURE_JOB_TYPE: Final[Mapping[AiFeature, str]] = MappingProxyType(
    {
        AiFeature.MEAL_PARSE: MEAL_PARSE_JOB_TYPE,
        AiFeature.LABEL_PHOTO: LABEL_PHOTO_JOB_TYPE,
    }
)

# ---------------------------------------------------------------------------
# Preço por modelo (centavos de dólar por 1 milhão de tokens)
# ---------------------------------------------------------------------------
# Fonte: página oficial de preços da OpenAI, consultada em 2026-10-08 e
# registrada em auditoria de 2026-10-08 (arquivo privado)
# ("Custo estimado"): gpt-5.4-mini US$ 0,75 / 0,075 (cache) / 4,50; gpt-5.4-nano
# US$ 0,20 / 0,02 / 1,25; gpt-5.4 US$ 2,50 / 0,25 / 15; gpt-4o-mini US$ 0,15 /
# 0,075 / 0,60 — por 1 milhão de tokens de entrada / entrada em cache / saída.
# Em centavos para a tabela ficar em números exatos. Os tokens de imagem (L6)
# já vêm somados em `input_tokens` e os de raciocínio em `output_tokens`.
PRICE_SOURCE_URL: Final[str] = "https://developers.openai.com/api/docs/pricing"
PRICES_CHECKED_ON: Final[str] = "2026-10-08"
TOKENS_PER_PRICE_UNIT: Final[int] = 1_000_000
CENTS_PER_USD: Final[int] = 100
# Casas do `cost_usd` (coluna DECIMAL(10,6) da 085): uma leitura de refeição custa
# frações de centavo.
COST_USD_DECIMAL_PLACES: Final[int] = 6


@dataclass(frozen=True)
class ModelPrice:
    """Centavos de dólar por 1 milhão de tokens."""

    input_cents: Decimal
    cached_input_cents: Decimal
    output_cents: Decimal

    def to_dict(self) -> dict[str, str]:
        return {
            "input_cents": str(self.input_cents),
            "cached_input_cents": str(self.cached_input_cents),
            "output_cents": str(self.output_cents),
        }


# Anthropic (Claude): página oficial de preços consultada em 2026-10-30.
# claude-sonnet-5-5 US$ 2 / 0,10 (acerto de cache, 0,05x da entrada) / 10;
# claude-haiku-5-5 US$ 0,10 / 0,01 / 0,50 para prompt de até 100.000 tokens e
# US$ 0,50 / 0,05 / 2,50 acima disso — por 1 milhão de tokens de entrada /
# entrada lida do cache / saída. Os tokens de raciocínio (thinking) vêm somados
# em `output_tokens`.
ANTHROPIC_PRICE_SOURCE_URL: Final[str] = "https://platform.claude.com/docs/en/about-claude/pricing"
ANTHROPIC_PRICES_CHECKED_ON: Final[str] = "2026-10-30"
HAIKU_LONG_PROMPT_THRESHOLD_TOKENS: Final[int] = 100_000

MODEL_PRICES_USD_CENTS_PER_MTOKEN: Final[Mapping[str, ModelPrice]] = MappingProxyType(
    {
        "gpt-5.4-mini": ModelPrice(Decimal(75), Decimal("7.5"), Decimal(450)),
        "gpt-5.4-nano": ModelPrice(Decimal(20), Decimal(2), Decimal(125)),
        "gpt-5.4": ModelPrice(Decimal(250), Decimal(25), Decimal(1500)),
        "gpt-4o-mini": ModelPrice(Decimal(15), Decimal("7.5"), Decimal(60)),
        "claude-sonnet-5-5": ModelPrice(Decimal(200), Decimal(10), Decimal(1000)),
        "claude-haiku-5-5": ModelPrice(Decimal(10), Decimal(1), Decimal(50)),
    }
)

# Faixa de prompt longo: (limiar em tokens de entrada, preço ACIMA do limiar).
MODEL_LONG_PROMPT_PRICES: Final[Mapping[str, tuple[int, ModelPrice]]] = MappingProxyType(
    {
        "claude-haiku-5-5": (
            HAIKU_LONG_PROMPT_THRESHOLD_TOKENS,
            ModelPrice(Decimal(50), Decimal(5), Decimal(250)),
        ),
    }
)

# Sufixo de snapshot datado que o provedor devolve em `model`: OpenAI
# `gpt-4o-mini-2024-07-18`; Anthropic `claude-...-20251001`.
_SNAPSHOT_SUFFIX = re.compile(r"-\d{4}-?\d{2}-?\d{2}$")


# ---------------------------------------------------------------------------
# Vocabulário
# ---------------------------------------------------------------------------


def normalize_feature(value: Any) -> AiFeature:
    """`AiFeature` do código (a feature vem do código, nunca do usuário):
    desconhecida é erro de programação (`ValueError`)."""
    try:
        return AiFeature(value.value if isinstance(value, Enum) else str(value))
    except ValueError:
        raise ValueError(f"feature de IA desconhecida: {value!r}") from None


def normalize_terminal_status(value: Any) -> AiUsageStatus:
    """Status com que o worker fecha a linha: só `succeeded` ou `failed`."""
    try:
        status = AiUsageStatus(value.value if isinstance(value, Enum) else str(value))
    except ValueError:
        raise ValueError(f"status de uso de IA desconhecido: {value!r}") from None
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"o worker fecha só com {sorted(s.value for s in TERMINAL_STATUSES)}: {status.value!r}")
    return status


def worker_may_close(status: Any, error_code: Any, *, presumed_lost_code: str) -> bool:
    """O relato do worker pode fechar a linha? Sim se ela ainda está aberta
    (`OPEN_STATUS`) ou se foi o sweep que a presumiu perdida
    (`SWEEP_PRESUMED_STATUS` + `presumed_lost_code`, o `worker_lost` do M73): o
    resultado real vence a presunção. Nenhum outro estado terminal reabre."""
    valor = status.value if isinstance(status, Enum) else status
    if valor == OPEN_STATUS.value:
        return True
    return valor == SWEEP_PRESUMED_STATUS.value and error_code == presumed_lost_code


def job_type_for(feature: Any) -> str:
    return FEATURE_JOB_TYPE[normalize_feature(feature)]


def feature_for_job_type(job_type: Optional[str]) -> Optional[AiFeature]:
    """Feature do job type (para o `GET /jobs` fechar a linha da feature); None fora delas."""
    for feature, tipo in FEATURE_JOB_TYPE.items():
        if tipo == job_type:
            return feature
    return None


def count_or_none(value: Any) -> Optional[int]:
    """Contagem (tokens, imagens) inteira e >= 0; qualquer outra coisa é desconhecida (None)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return int(value)


# ---------------------------------------------------------------------------
# Cota
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotaStatus:
    """Uso da feature no dia do usuário: `used` tentativas de `limit`."""

    feature: AiFeature
    limit: int
    used: int

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    @property
    def allowed(self) -> bool:
        """Ainda cabe uma tentativa nova hoje?"""
        return self.used < self.limit

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature.value,
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "allowed": self.allowed,
        }


def daily_quota(feature: Any) -> int:
    return DAILY_QUOTA[normalize_feature(feature)]


def quota_status(feature: Any, used_today: Any) -> QuotaStatus:
    """Decisão da cota a partir das tentativas do dia (qualquer status)."""
    codigo = normalize_feature(feature)
    usadas = count_or_none(used_today)
    if usadas is None:
        raise ValueError(f"contagem de uso inválida: {used_today!r}")
    return QuotaStatus(feature=codigo, limit=DAILY_QUOTA[codigo], used=usadas)


# ---------------------------------------------------------------------------
# Custo
# ---------------------------------------------------------------------------


def _model_key(model: Any) -> Optional[str]:
    """Nome do modelo na tabela (o snapshot datado vira o modelo-base)."""
    if not isinstance(model, str) or not model.strip():
        return None
    nome = model.strip().lower()
    if nome in MODEL_PRICES_USD_CENTS_PER_MTOKEN:
        return nome
    base = _SNAPSHOT_SUFFIX.sub("", nome)
    return base if base in MODEL_PRICES_USD_CENTS_PER_MTOKEN else None


def price_for_model(model: Any, prompt_tokens: Any = None) -> Optional[ModelPrice]:
    """Preço do modelo (o snapshot datado usa o do modelo-base); fora da tabela → None.
    `prompt_tokens` (toda a entrada) escolhe a faixa de prompt longo, quando o
    modelo tem uma (`MODEL_LONG_PROMPT_PRICES`); sem ele, a faixa padrão."""
    chave = _model_key(model)
    if chave is None:
        return None
    faixa = MODEL_LONG_PROMPT_PRICES.get(chave)
    entrada = count_or_none(prompt_tokens)
    if faixa is not None and entrada is not None and entrada > faixa[0]:
        return faixa[1]
    return MODEL_PRICES_USD_CENTS_PER_MTOKEN[chave]


def estimate_cost_usd(
    model: Any,
    tokens_in: Any,
    tokens_out: Any,
    cached_tokens_in: Any = None,
) -> Optional[Decimal]:
    """Custo em USD de uma chamada; None se o modelo não tem preço ou falta um dos
    tokens. `cached_tokens_in` (parte de `tokens_in` servida do cache) é opcional:
    sem ele o custo é o teto (entrada toda a preço cheio)."""
    entrada, saida = count_or_none(tokens_in), count_or_none(tokens_out)
    preco = price_for_model(model, entrada)
    if preco is None or entrada is None or saida is None:
        return None
    em_cache = min(count_or_none(cached_tokens_in) or 0, entrada)
    centavos = (
        (entrada - em_cache) * preco.input_cents
        + em_cache * preco.cached_input_cents
        + saida * preco.output_cents
    )
    usd = centavos / Decimal(TOKENS_PER_PRICE_UNIT * CENTS_PER_USD)
    return usd.quantize(Decimal(1).scaleb(-COST_USD_DECIMAL_PLACES), rounding=ROUND_HALF_UP)


__all__ = [
    "ANTHROPIC_PRICES_CHECKED_ON",
    "ANTHROPIC_PRICE_SOURCE_URL",
    "CENTS_PER_USD",
    "COST_USD_DECIMAL_PLACES",
    "DAILY_QUOTA",
    "ERR_AI_QUOTA_EXCEEDED",
    "FEATURE_JOB_TYPE",
    "LABEL_PHOTO_DAILY_QUOTA",
    "LABEL_PHOTO_JOB_TYPE",
    "MEAL_PARSE_DAILY_QUOTA",
    "MEAL_PARSE_JOB_TYPE",
    "HAIKU_LONG_PROMPT_THRESHOLD_TOKENS",
    "MODEL_LONG_PROMPT_PRICES",
    "MODEL_PRICES_USD_CENTS_PER_MTOKEN",
    "OPEN_STATUS",
    "PRICES_CHECKED_ON",
    "PRICE_SOURCE_URL",
    "SWEEP_PRESUMED_STATUS",
    "TERMINAL_STATUSES",
    "TOKENS_PER_PRICE_UNIT",
    "AiFeature",
    "AiUsageStatus",
    "ModelPrice",
    "QuotaStatus",
    "count_or_none",
    "daily_quota",
    "estimate_cost_usd",
    "feature_for_job_type",
    "job_type_for",
    "normalize_feature",
    "normalize_terminal_status",
    "price_for_model",
    "quota_status",
    "worker_may_close",
]
