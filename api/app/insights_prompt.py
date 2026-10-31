"""O prompt de `POST /insights/generate` - fonte unica e versionada (F3.2).

Antes da F3.2 o prompt vivia em `insights_ai.build_prompt` e recebia duas
coisas que ele nao controlava: um `context` de texto livre montado pelo
FRONTEND (JSON de quatro endpoints v1/v2, concatenado) e o dump COMPLETO de
`rag/markdown`. As duas pontas soltas produziam o mesmo defeito por caminhos
diferentes - o modelo recebia numeros sem `nature`, sem versao de formula e
sem qualidade declarada, junto de centenas de milhares de caracteres de
metodologia sem relacao com a pergunta, e preenchia o vazio recalculando e
inferindo causa.

Agora ele recebe exatamente duas coisas, as duas montadas no servidor:

- o `intelligence_package` (contrato fechado, <= 40 KB), e
- o RAG FILTRADO pelos grupos musculares presentes nesse pacote
  (`rag_chunking.select_rag_text`).

As tres regras do plano (§17 F3.2) sao literais aqui e nao sao negociaveis:
nao recalcular, nao inventar, nao afirmar causalidade. Elas nao sao educacao
do prompt - sao a unica defesa que existe entre um agregado e uma afirmacao
falsa com aparencia de laudo.

`PROMPT_VERSION` muda SEMPRE que o texto muda. A avaliacao de respostas
(`tests/eval/`) registra a versao com que cada expectativa foi escrita: sem
isso, uma piora de qualidade depois de uma edicao de prompt seria
indistinguivel de variacao do modelo.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping

from . import prompt_store
from .goal_prompt_context import SCOPE_WINDOW, focus_text, prompt_section
from .goal_prompt_context import prompt_version as goal_prompt_version
from .prompt_user_data import (
    USER_NOTES_MAX_CHARS,
    delimit_user_fields,
    sanitize_user_text,
    user_data_block,
    user_data_inline,
    user_data_rule,
)

# v4 (IA-02/IA-05): ordem fixa "instrucoes -> RAG -> dados variaveis" (prefixo
# cacheavel pelo prompt caching automatico da OpenAI) e janela/qualidade como
# texto legivel (valor do enum, datas ISO) - a v3 vazava o repr Python de
# `WindowInfo` e de `DataQuality` no prompt.
# v5 (pos-eval do #442): regra 2b "variacao exige dois pontos". Com o RAG
# cortado, o modelo passou a afirmar "Sim, a cintura diminuiu" citando
# "de 99.5 para 99.5" (uma medicao so, sem baseline). A regra e de logica, nao
# numero de dominio; medida no eval: cintura 6/6, insights completo 10/10.
# v6 (IA-10, prompt injection): a pergunta e as `notes` de `context_events`
# (texto digitado pelo usuario) vao delimitadas por `<dados_do_usuario>`, e a
# regra 7 (`user_data_rule()`) diz que o conteudo delimitado e dado.
# M97 F5: objetivo nao-default treinado na janela (foco da persona + bloco de
# regras interpolado dos donos) e deload na janela (bloco proprio, escopo de
# janela) entram so nos ramos novos, versionados por sufixo
# (`effective_prompt_version`). Hipertrofia sem deload: texto e versao de sempre.
PROMPT_VERSION = "insights-prompt-v6"


def effective_prompt_version(training_goals: Any = None, deload: bool = False) -> str:
    """`PROMPT_VERSION` com o sufixo dos ramos novos (objetivo/deload)."""
    return goal_prompt_version(PROMPT_VERSION, training_goals, deload)

# Teto da pergunta: o mesmo do contrato de entrada (`InsightGenerateIn.question`).
QUESTION_MAX_CHARS = 2000

# M103 P2: a PROSA do prompt (papel, regras, formato) mora no repositorio
# privado, `api/privado/prompts/insights/<PROMPT_VERSION>.md` (`prompt_store`).
# Aqui ficam a ordem dos dados, os valores e a versao.
PROMPT_FEATURE_DIR = "insights"


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        # Modelo Pydantic (ex.: `WindowInfo`): sem isto caia no `str()` e o
        # prompt recebia o repr Python (`kind=<WindowKind...> date_from=
        # datetime.date(...)`) - v3.
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _plain(value: Any) -> Any:
    """Valor escalar legivel no texto do prompt: enum vira o seu valor, data
    vira ISO, `None` fica `None`. Nunca o repr do objeto."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def render_package(package: Mapping[str, Any]) -> str:
    """O pacote como JSON legivel dentro do prompt.

    `indent=2` aqui de proposito, ao contrario de
    `intelligence_package.measure_bytes` (que mede compacto): o teto de 40 KB
    e sobre o TRANSPORTE, e no prompt a indentacao paga por si em precisao de
    leitura do modelo. A diferenca e de poucos KB contra as centenas de
    milhares de caracteres que o RAG filtrado economizou."""
    return json.dumps(_delimit_user_text(package), default=_json_default, ensure_ascii=False, indent=2, sort_keys=True)


def _delimit_user_text(package: Mapping[str, Any]) -> Any:
    """Copia do pacote com o texto livre do usuario delimitado (IA-10): a
    `question` de topo e as `notes` de `context_events` (ja cortadas em
    `MAX_NOTES_CHARS` pelo pacote). Os outros textos do pacote (inclusive as
    `question` fixas dos cards) sao do sistema e ficam como estao."""
    delimited = dict(package)
    if delimited.get("context_events"):
        delimited["context_events"] = delimit_user_fields(
            delimited["context_events"], {"notes": USER_NOTES_MAX_CHARS}
        )
    if isinstance(delimited.get("question"), str) and delimited["question"].strip():
        delimited["question"] = user_data_inline(delimited["question"], QUESTION_MAX_CHARS)
    return delimited


def _values(
    package: Mapping[str, Any],
    rag_text: str,
    *,
    question: str | None,
    training_goals: Any,
    deload: bool,
    technique_names: Mapping[str, str] | None,
) -> dict[str, str]:
    """Valores do template (a montagem de sempre de `build_insight_prompt`)."""
    asked = sanitize_user_text(question or package.get("question"), QUESTION_MAX_CHARS)
    window = package.get("window") or {}
    if hasattr(window, "model_dump"):
        window = window.model_dump(mode="json")
    periodo = (
        f"{_plain(window.get('date_from'))} a {_plain(window.get('date_to'))} ({_plain(window.get('kind'))})"
    )

    # Ordem fixa (IA-05): o que nao muda entre chamadas vem primeiro (papel,
    # regras, formato), depois o RAG (igual para o mesmo conjunto de grupos) e
    # so entao o que varia por pedido (janela, qualidade, pacote, pergunta). O
    # prompt caching automatico da OpenAI so reaproveita PREFIXO identico.
    goal_text = prompt_section(
        training_goals, deload, deload_scope=SCOPE_WINDOW, technique_names=technique_names
    )
    return {
        "foco": focus_text(training_goals),
        "regra_dados_usuario": user_data_rule(),
        "bloco_objetivo": goal_text,
        "rag": rag_text,
        "periodo": periodo,
        "fuso": f"{_plain(package.get('timezone'))}",
        "qualidade": f"{_plain(package.get('data_quality'))}",
        "versao_prompt": effective_prompt_version(training_goals, deload),
        "versao_pacote": f"{_plain(package.get('package_version'))}",
        "pacote": render_package(package),
        "pergunta": user_data_block(asked),
    }


def build_insight_prompt(
    package: Mapping[str, Any],
    rag_text: str,
    *,
    question: str | None = None,
    training_goals: Any = None,
    deload: bool = False,
    technique_names: Mapping[str, str] | None = None,
) -> str:
    """Prompt completo de `/insights/generate`.

    `training_goals`/`deload` (M97 F5): objetivos treinados e deload na janela,
    lidos pelo servidor (`insights_core.load_window_goal_context`). Ausentes =
    hipertrofia sem deload, o prompt de sempre. `technique_names` (M95c):
    `{code: nome}` do catalogo `advanced_techniques`, para o bloco do objetivo
    citar as tecnicas vetadas pelo nome (nunca o codigo cru).

    `question` explicito sobrepoe `package["question"]` apenas para o caso de
    quem monta o pacote sem pergunta (o endpoint do Personal Trainer) querer
    reaproveita-lo - o normal e a pergunta ja estar dentro do pacote."""
    values = _values(
        package, rag_text, question=question, training_goals=training_goals, deload=deload,
        technique_names=technique_names,
    )
    return prompt_store.render(PROMPT_FEATURE_DIR, PROMPT_VERSION, values)


# Placeholders que mudam a cada pedido: o prefixo cacheavel termina no
# primeiro deles (`prompt_store.render_prefix`).
REQUEST_PLACEHOLDERS = ("periodo", "fuso", "qualidade", "versao_pacote", "pacote", "pergunta")


def insight_cache_prefix(
    package: Mapping[str, Any],
    rag_text: str,
    *,
    question: str | None = None,
    training_goals: Any = None,
    deload: bool = False,
    technique_names: Mapping[str, str] | None = None,
) -> str:
    """Comeco estavel de `build_insight_prompt` (instrucoes + RAG), para o
    `cache_control` do provedor Anthropic. Nao muda o prompt."""
    values = _values(
        package, rag_text, question=question, training_goals=training_goals, deload=deload,
        technique_names=technique_names,
    )
    return prompt_store.render_prefix(PROMPT_FEATURE_DIR, PROMPT_VERSION, values, REQUEST_PLACEHOLDERS)


__all__ = [
    "PROMPT_VERSION",
    "REQUEST_PLACEHOLDERS",
    "build_insight_prompt",
    "effective_prompt_version",
    "insight_cache_prefix",
    "render_package",
]
