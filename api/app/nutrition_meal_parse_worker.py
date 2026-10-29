"""Worker da leitura da refeição por IA (nutrição, Lote L5b; plano §8.3 e §9 a).

Roda na Lambda `musclefit-workout-ai` (decisão E8: texto na Lambda de 60 s), pelo
despacho de `workout_ai_generation_core` para o job type
`ai_usage.MEAL_PARSE_JOB_TYPE`. A rota (`nutrition_meal_parse.py`, api-core) já
reservou a cota e abriu a linha `pending` do `ai_usage_log`; o evento traz o
`ai_usage_id`.

Passos:
1. a IA interpreta o texto (`nutrition_meal_parse_ai.parse_meal_text`, uma
   chamada estruturada);
2. o motor limpa os itens (`meal_parse.normalize_ai_items`);
3. para cada busca do item (`AiItem.match_queries`: com e sem o preparo), a
   MESMA busca da tela (`food_text`: FULLTEXT de `name_normalized`, prefixo
   para termo curto, lote `SEARCH_CANDIDATE_POOL`) sobre os alimentos que o
   diário do aluno pode usar (`nutrition_access.diary_foods_visibility`),
   ordenada pela nota do motor (`food_text.rank_candidates`); vale a melhor
   (`meal_parse.best_ranking`);
4. o motor decide cada item (`meal_parse.build_result`).

Fecha a linha do `ai_usage_log` (`nutrition_ai_quota.finish_usage`) em TODOS os
caminhos: sucesso (modelo e tokens), falha da IA (o modelo pedido) e falha da
busca depois da IA (com os tokens já gastos). O `finish_usage` nunca levanta.
Nenhuma regra aqui: só leitura do catálogo e montagem do resultado.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import HTTPException

from . import nutrition_ai_quota as quota
from . import nutrition_foods, nutrition_meal_parse_ai
from .db import transaction
from .domain.nutrition import ENGINE_VERSION, food_text, meal_parse
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.ai_usage import AiUsageStatus
from .nutrition_access import diary_foods_visibility
from .nutrition_schema import require_food_tables
from .openai_client import OpenAIGenerationError

logger = logging.getLogger("musclefit.nutrition.meal_parse")


class MealParseError(RuntimeError):
    """Falha com a mensagem pronta para o job `failed` (texto do dono, sem
    detalhe técnico: o diagnóstico vai para o log)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.detail = message


def search_candidates(
    cur: Any, query: str, *, registrant_user_id: int, diary_user_id: int
) -> tuple[tuple[food_text.Match, ...], dict[int, dict[str, Any]]]:
    """Candidatos do catálogo para um item lido, na ordem da nota do motor (só
    os acima de `MATCH_MIN_SCORE`), e as linhas por id."""
    expressao = food_text.fulltext_boolean_query(query)
    fulltext = expressao is not None
    if expressao is None:
        expressao = food_text.prefix_pattern(query)
    if expressao is None:
        return (), {}
    visibilidade, params_visibilidade = diary_foods_visibility(registrant_user_id, diary_user_id)
    params: list[Any] = [expressao, *params_visibilidade]
    if fulltext:
        params.append(expressao)
    params.append(food_text.SEARCH_CANDIDATE_POOL)
    cur.execute(nutrition_foods.search_sql(fulltext=fulltext, visibility=visibilidade, with_source=False), tuple(params))
    por_id = {int(linha["id"]): linha for linha in cur.fetchall() or []}
    ranking = food_text.rank_candidates(query, [(food_id, linha["name"]) for food_id, linha in por_id.items()])
    return ranking, por_id


def _candidate_public(match: dict[str, Any], row: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Candidato para a tela: a decisão do motor + o que o editor mostra."""
    linha = row or {}
    return {
        **match,
        "food_id": match.get("candidate_id"),
        "basis": linha.get("basis"),
        "source": linha.get("source"),
        "brand": linha.get("brand"),
    }


def _public_message(code: str) -> str:
    return nutrition_labels.limitation_label(code)


def dispatch_meal_parse(payload: dict[str, Any], user_id: Any, ai_usage_id: Optional[int] = None) -> dict[str, Any]:
    """Resultado do job (`GET /jobs/{id}`): itens com a decisão do motor, os
    candidatos e a proposta de cada um, mais as limitações da leitura."""
    texto, erros = meal_parse.validate_text(payload.get("text"))
    if erros:
        quota.finish_usage(ai_usage_id, status=AiUsageStatus.FAILED, error_code=erros[0])
        raise HTTPException(status_code=400, detail=_public_message(erros[0]))
    solicitante = int(user_id)
    aluno = int(payload.get("diary_user_id") or solicitante)
    slot = payload.get("meal_slot")

    try:
        resposta = nutrition_meal_parse_ai.parse_meal_text(texto, slot, user_id=solicitante)
    except Exception as exc:
        codigo = exc.code if isinstance(exc, OpenAIGenerationError) else meal_parse.ERR_PARSE_FAILED
        quota.finish_usage(
            ai_usage_id, status=AiUsageStatus.FAILED, model=nutrition_meal_parse_ai.resolve_model(), error_code=codigo
        )
        if isinstance(exc, OpenAIGenerationError):
            raise MealParseError(str(exc)) from exc
        logger.exception("Falha inesperada na leitura da refeição por IA")
        raise MealParseError(_public_message(meal_parse.ERR_PARSE_FAILED)) from exc

    uso = {"model": resposta.model, "tokens_in": resposta.tokens_in, "tokens_out": resposta.tokens_out}
    try:
        itens, limitacoes = meal_parse.normalize_ai_items((resposta.data or {}).get("items"))
        rankings: list[tuple[food_text.Match, ...]] = []
        linhas: dict[int, dict[str, Any]] = {}
        if itens:
            with transaction() as conn:
                with conn.cursor() as cur:
                    require_food_tables(cur)
                    for item in itens:
                        buscas = []
                        for consulta in item.match_queries:
                            ranking, por_id = search_candidates(
                                cur, consulta, registrant_user_id=solicitante, diary_user_id=aluno
                            )
                            buscas.append(ranking)
                            linhas.update(por_id)
                        rankings.append(meal_parse.best_ranking(buscas))
        resultado = meal_parse.build_result(itens, rankings, limitacoes, (resposta.data or {}).get("notes"))
    except Exception as exc:
        quota.finish_usage(ai_usage_id, status=AiUsageStatus.FAILED, error_code=meal_parse.ERR_MATCH_FAILED, **uso)
        logger.exception("Falha na busca do catálogo depois da leitura por IA")
        raise MealParseError(_public_message(meal_parse.ERR_MATCH_FAILED)) from exc

    quota.finish_usage(ai_usage_id, status=AiUsageStatus.SUCCEEDED, **uso)
    corpo = resultado.to_dict()
    for item in corpo["items"]:
        item["candidates"] = [_candidate_public(m, linhas.get(int(m["candidate_id"]))) for m in item["candidates"]]
    corpo["limitations"] = nutrition_labels.limitation_items(corpo["limitations"])
    for item in corpo["items"]:
        item["limitations"] = nutrition_labels.limitation_items(item["limitations"])
    return {
        **corpo,
        "job_id": payload.get("job_id"),
        "user_id": aluno,
        "local_date": payload.get("local_date"),
        "meal_slot": slot,
        "model": resposta.model,
        "prompt_version": nutrition_meal_parse_ai.PROMPT_VERSION,
        "engine_version": ENGINE_VERSION,
    }


__all__ = ["MealParseError", "dispatch_meal_parse", "search_candidates"]
