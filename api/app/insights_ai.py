"""Chamada a OpenAI da tela `/insights` (pergunta livre).

F3.2 mudou duas coisas estruturais aqui e deixou o resto no lugar:

1. **A chave da API saiu do modulo.** `OPENAI_API_KEY` era lida numa CONSTANTE
   de modulo (`insights_ai.py:11`), o que congelava o valor no import: um
   segredo rotacionado em runtime (ou injetado depois do bootstrap, como
   `secrets_bootstrap.py` faz) so passava a valer no proximo cold start, e um
   teste que setava a variavel de ambiente nao tinha efeito nenhum. A chamada
   agora e `openai_client.call_openai`, que le a chave A CADA chamada - o
   mesmo caminho de todos os outros fluxos de IA do projeto. A copia local de
   `call_openai`/parsing de resposta que existia aqui tambem foi embora.
2. **O prompt saiu daqui.** Ele vive em `insights_prompt.py`, versionado, e
   recebe o `intelligence_package` em vez de um texto livre montado pelo
   cliente. O RAG deixou de ser o dump completo de `rag/markdown` e passou a
   ser o recorte filtrado pelos grupos do pacote.

`InsightsGenerationError` continua existindo e continua sendo o que
`insights_core` captura - agora como SUBCLASSE de `OpenAIGenerationError`,
para que uma falha vinda de dentro do cliente compartilhado (chave ausente,
HTTP, timeout) chegue ao job com a mesma mensagem legivel de antes.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from .insights_prompt import build_insight_prompt, effective_prompt_version, insight_cache_prefix
from .openai_client import LLM_TIMEOUT_LONG_SECONDS, OpenAIGenerationError, call_openai_text

OPENAI_MODEL = os.environ.get("OPENAI_INSIGHTS_MODEL", "gpt-5.4-mini")


class InsightsGenerationError(OpenAIGenerationError):
    """Falha de geracao do insight livre.

    Herda de `OpenAIGenerationError` para que `except InsightsGenerationError`
    continue valendo nos chamadores antigos E para que um erro levantado
    dentro de `openai_client` seja tratado do mesmo jeito (ver
    `insights_core.dispatch_insights`, que captura a classe base)."""


def _model() -> str:
    """Modelo lido a CADA chamada, nao no import - mesma razao da chave."""
    return os.environ.get("OPENAI_INSIGHTS_MODEL", "gpt-5.4-mini")


def generate_insight(
    question: str,
    package: Mapping[str, Any],
    rag_text: str,
    user_id: Any = None,
    *,
    training_goals: Any = None,
    deload: bool = False,
    technique_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Gera o insight livre a partir do pacote JA montado pelo servidor.

    A assinatura mudou na F3.2 (`context: str` -> `package` + `rag_text`) de
    proposito: enquanto o segundo parametro fosse texto livre, nada impedia um
    cliente de voltar a mandar numero solto. O unico chamador e
    `insights_core.dispatch_insights`."""
    if not question:
        raise InsightsGenerationError("question é obrigatório.")

    prompt = build_insight_prompt(
        package,
        rag_text,
        question=question,
        training_goals=training_goals,
        deload=deload,
        technique_names=technique_names,
    )
    version = effective_prompt_version(training_goals, deload)
    model = _model()
    cache_prefix = insight_cache_prefix(
        package,
        rag_text,
        question=question,
        training_goals=training_goals,
        deload=deload,
        technique_names=technique_names,
    )
    try:
        result = call_openai_text(
            prompt,
            model,
            timeout=LLM_TIMEOUT_LONG_SECONDS,
            feature="insights",
            user_id=user_id,
            prompt_version=version,
            cache_prefix=cache_prefix,
        )
    except OpenAIGenerationError as exc:
        raise InsightsGenerationError(str(exc)) from exc

    # Modelo que RESPONDEU (Claude, ou a OpenAI depois do fallback), nao o
    # configurado para a OpenAI.
    return {
        "model": result.model,
        "insight": result.text,
        "prompt_version": version,
        "package_version": str(package.get("package_version") or ""),
        "prompt_chars": len(prompt),
    }


__all__ = ["OPENAI_MODEL", "InsightsGenerationError", "generate_insight"]
