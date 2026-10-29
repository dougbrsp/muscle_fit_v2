"""Geracao do insight livre de `/insights` (`POST /insights/generate`).

Extraido de `insights_ai.py` na Fase 3 da migracao AWS para ser reutilizavel
pelo endpoint FastAPI (que so valida payload, cria o job e responde 202) e
pelo handler da Lambda `musclefit-workout-ai` (`lambda_workout_ai.py`, via
`workout_ai_generation_core.dispatch_workout_ai_generation`) - mesmo padrao
202 + job_id + DynamoDB de `workouts_analysis_core.py`/`plan_generation_core.
py`/`cardio_generation_core.py`.

**F3.2 - o contexto passou a ser montado AQUI.** Ate entao o cliente mandava
um campo `context` de texto livre (o frontend concatenava o JSON de quatro
endpoints v1/v2) e o backend injetava o dump COMPLETO de `rag/markdown` no
prompt. Os dois lados do prompt eram, portanto, decididos fora de qualquer
contrato: numeros sem `nature`, sem versao de formula e sem qualidade
declarada, ao lado de centenas de milhares de caracteres de metodologia sem
relacao com a pergunta. Agora:

- o cliente manda so `question` + `window`;
- o servidor monta o `intelligence_package` (contrato fechado, <= 40 KB);
- o RAG vai FILTRADO pelos grupos musculares presentes nesse pacote
  (`rag_chunking.select_rag_text`), nao mais inteiro.

**Janela de compatibilidade.** Um cliente antigo que ainda mande `context`
NAO recebe 422: o campo e ignorado, com um `log_event` que conta quantos
caracteres vieram. A razao e de deploy, nao de gentileza - frontend e API sao
artefatos separados e nao sobem no mesmo segundo; durante a troca, um 422
transformaria uma defasagem de minutos numa tela quebrada. A limpeza do campo
esta declarada na secao "Impacto em producao" do PR e no plano (§17 F3.2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import HTTPException

from .advanced_techniques import technique_display_names
from .db import DataApiError, get_conn
from .goal_prompt_context import non_default_goals
from .insights_ai import InsightsGenerationError, generate_insight
from .intelligence.dashboard.adherence_service import resolve_cycle_length_days
from .intelligence.dashboard.contracts import TargetSource, WindowKind
from .intelligence.dashboard.intelligence_package import (
    build_intelligence_package,
    rag_target_groups,
)
from .intelligence.dashboard.time_windows import resolve_window, user_now, user_timezone, user_today
from .observability import log_event
from .openai_client import OpenAIGenerationError
from .rag_chunking import rag_base_limit, select_rag_text, truncate_rag
from .training_types import resolve_training_type_for_window

logger = logging.getLogger(__name__)

JOB_TYPE_INSIGHTS = "insights"

# Mesma janela default da visao geral: a pergunta livre quase sempre e sobre
# "o mes", e o pacote precisa ser o MESMO recorte que a tela mostra - senao o
# usuario le um numero na home e ouve outro do modelo.
DEFAULT_WINDOW = WindowKind.ROLLING_28D.value

EVENT_LEGACY_CONTEXT = "insights.legacy_context_ignored"


def _connect():
    try:
        return get_conn()
    except DataApiError as exc:
        # SEG-12: o texto da excecao vai para o log, nunca para o corpo.
        logger.exception("Banco indisponivel ao montar o pacote de dados de insights")
        raise HTTPException(
            status_code=503,
            detail="Não foi possível conectar ao banco para montar o pacote de dados.",
        ) from exc


def _as_date(value: Any) -> Any:
    """`date_from`/`date_to` chegam como STRING quando o payload veio do job.

    O endpoint serializa com `model_dump(mode="json")` antes de gravar no
    DynamoDB, entao o que a Lambda le nao e o `date` do Pydantic - e o ISO
    dele. `resolve_window` exige `date`, e sem esta conversao uma janela
    `custom` viraria `TypeError` dentro do job (falha sem mensagem util),
    nao um 400."""
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"data inválida: {value!r}") from exc
    return value


@dataclass(frozen=True)
class WindowGoalContext:
    """Objetivos treinados e deload na janela do pacote (M97 F5): o que o
    prompt de Insights recebe pronto. Default = hipertrofia sem deload."""

    training_goals: tuple[str, ...] = ()
    deload: bool = False


# Objetivos carimbados nas linhas de `exercises` (093) das sessoes da janela.
WINDOW_GOALS_SQL = """
SELECT DISTINCT e.training_goal
FROM exercises e
JOIN activities a ON a.id = e.activity_id
WHERE a.user_id = %s AND a.local_date BETWEEN %s AND %s
"""


def load_window_goal_context(conn: Any, *, user_id: int, window: Any) -> WindowGoalContext:
    """Leitura tolerante (falha = default, o prompt de sempre): os objetivos
    distintos da janela (`training_goal.goals_in`) e se ha deload nela
    (`context_service.load_deload_ranges`, a mesma leitura dos insights de
    regra)."""
    from .domain.training_goal import goals_in
    from .intelligence.dashboard.context_service import load_deload_ranges

    goals: tuple[str, ...] = ()
    try:
        with conn.cursor() as cur:
            cur.execute(WINDOW_GOALS_SQL, (int(user_id), window.date_from, window.date_to))
            goals = goals_in(row.get("training_goal") for row in cur.fetchall() or [])
    except Exception:  # noqa: BLE001 - contexto opcional nunca derruba o insight
        logger.exception("Falha ao ler os objetivos da janela do insight")
    deload = False
    try:
        deload = bool(load_deload_ranges(conn, user_id=user_id, window=window))
    except Exception:  # noqa: BLE001 - contexto opcional nunca derruba o insight
        logger.exception("Falha ao ler o deload da janela do insight")
    return WindowGoalContext(training_goals=goals, deload=deload)


def build_package_for_question(
    *,
    user_id: int,
    question: str,
    window: str = DEFAULT_WINDOW,
    date_from: Any = None,
    date_to: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    """`(pacote, grupos_para_o_rag)` - ver `build_insights_inputs`."""
    package, groups, _ = build_insights_inputs(
        user_id=user_id, question=question, window=window, date_from=date_from, date_to=date_to
    )
    return package, groups


def build_insights_inputs(
    *,
    user_id: int,
    question: str,
    window: str = DEFAULT_WINDOW,
    date_from: Any = None,
    date_to: Any = None,
) -> tuple[dict[str, Any], list[str], WindowGoalContext]:
    """`(pacote, grupos_para_o_rag)` para a pergunta recebida.

    Abre a conexao, resolve fuso e janela do MESMO jeito que
    `/analytics/dashboard/*` (nada de `date.today()`: o relogio e o do
    usuario) e delega a montagem. `window=cycle` sem Modelo atribuido e 400,
    como nos demais endpoints v3 - nao um silencioso cai-pra-28d."""
    cycle_length_days = None
    if window == WindowKind.CYCLE.value:
        cycle_length_days = resolve_cycle_length_days(user_id)
        if cycle_length_days is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "window=cycle exige um Modelo de Ficha atribuído: o tamanho do ciclo vem do "
                    "número de slots do Modelo. Atribua um Modelo ou use 7d|28d|90d|180d|custom."
                ),
            )

    with _connect() as conn:
        timezone_name = user_timezone(conn, user_id)
        try:
            resolved = resolve_window(
                window,
                today=user_today(timezone_name),
                date_from=_as_date(date_from),
                date_to=_as_date(date_to),
                cycle_length_days=cycle_length_days,
                timezone_name=timezone_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # PR3: tipo de treino da versao do Modelo no FIM da janela (flag
        # ligada); desligada, o de hoje. Um statement, como antes.
        training_type_id = resolve_training_type_for_window(user_id, resolved.date_to)
        source = TargetSource.ASSIGNED_TEMPLATE if training_type_id is not None else TargetSource.FALLBACK_GENERIC

        package = build_intelligence_package(
            conn,
            user_id=user_id,
            window=resolved,
            now_local=user_now(timezone_name),
            question=question,
            training_type_id=training_type_id,
            training_type_source=source,
        )
        goal_context = load_window_goal_context(conn, user_id=user_id, window=resolved)

    return package, rag_target_groups(package), goal_context


def select_insights_rag(groups: list[str], training_goals: Any = None) -> tuple[int, str]:
    """RAG do prompt de Insights: (tamanho antes do teto, texto com teto).

    - FILTRADO pelos grupos do pacote. `has_training_type=True` evita o
      fallback recursivo que varre TODAS as subpastas de TIPOS_TREINO - ele
      existe para a montagem de ficha sem tipo de treino definido, e aqui ele
      so traria de volta, por outro caminho, o dump que esta entrega remove.
    - Com TETO (IA-02): o mesmo `MAX_RAG_BASE_CHARS` da base da Periodizacao
      (dono: `rag_chunking`). Sem ele, um pacote com todos os grupos levava
      ~420 mil caracteres de metodologia a cada pergunta.

    Funcao propria para o eval/snapshot (`tests/support/llm_casos.py`) montar
    o RAG exatamente como a producao.

    M97 F5: `training_goals` (objetivos da janela) traz o material proprio do
    objetivo na frente da base, com o teto somado (`rag_base_limit`); sem
    objetivo nao-default, texto e teto de sempre."""
    raw = select_rag_text(groups, has_training_type=True, training_goal=training_goals)
    return len(raw), truncate_rag(raw, rag_base_limit(training_goals), "RAG base")


def dispatch_insights(payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Monta o pacote, filtra o RAG pelos grupos dele e chama a OpenAI.

    `user_id` deixou de ser decorativo: antes da F3.2 esta funcao nao lia
    nada do banco (o contexto vinha pronto do cliente), e o parametro existia
    so por simetria com os demais `dispatch_*`. Agora ele e o dono do pacote -
    e o unico usuario cujo dado pode entrar no prompt."""
    payload = payload or {}
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question é obrigatório.")

    legacy_context = payload.get("context") or ""
    if legacy_context:
        # Janela de compatibilidade (ver o cabecalho do modulo). Ignorado, nao
        # recusado - e contado, para a remocao do campo ser decidida por
        # medida e nao por palpite.
        log_event(
            EVENT_LEGACY_CONTEXT,
            user_id=user_id,
            chars=len(str(legacy_context)),
        )

    package, groups, goal_context = build_insights_inputs(
        user_id=user_id,
        question=question,
        window=str(payload.get("window") or DEFAULT_WINDOW),
        date_from=payload.get("date_from"),
        date_to=payload.get("date_to"),
    )

    rag_full_chars, rag_text = select_insights_rag(groups, goal_context.training_goals)

    try:
        result = generate_insight(
            question,
            package,
            rag_text,
            user_id=user_id,
            training_goals=goal_context.training_goals,
            deload=goal_context.deload,
            # M95c: tecnicas vetadas pelo NOME do catalogo, so quando o bloco
            # do objetivo aparece (objetivo nao-default na janela).
            technique_names=(
                technique_display_names() if non_default_goals(goal_context.training_goals) else None
            ),
        )
    except (InsightsGenerationError, OpenAIGenerationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    log_event(
        "insights.generated",
        user_id=user_id,
        window=str(package["window"].kind.value),
        rag_chars=len(rag_text),
        rag_chars_before_cap=rag_full_chars,
        rag_groups=len(groups),
        package_bytes=package["size_bytes"],
        prompt_chars=result.get("prompt_chars"),
        prompt_version=result.get("prompt_version"),
    )
    return {"model": result["model"], "insight": result["insight"]}


__all__ = [
    "DEFAULT_WINDOW",
    "EVENT_LEGACY_CONTEXT",
    "JOB_TYPE_INSIGHTS",
    "WindowGoalContext",
    "build_insights_inputs",
    "build_package_for_question",
    "dispatch_insights",
    "load_window_goal_context",
    "select_insights_rag",
]
