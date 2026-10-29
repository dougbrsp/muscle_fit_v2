"""Log estruturado (JSON, uma linha) dos eventos da secao 14 do
PLANO_20260912.md.

Pergunta que este modulo responde: como um evento de negocio vira uma linha
de log pesquisavel no CloudWatch, sempre no MESMO formato, sem cada modulo
reinventar o `json.dumps`.

Regra de conteudo (secao 14): so id numerico de usuario, ids de plano/modelo,
`engine_version` e `reason_code`. **Nunca** nota livre, dor detalhada ou
instrucao do usuario - isso fica no banco.

Campos `None` sao omitidos, para o metric filter nao ter que distinguir
"ausente" de "nulo".
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("musclefit.events")

# Eventos da secao 14 emitidos por este modulo (os de geracao/progressao
# continuam nos seus proprios modulos, com o mesmo formato).
EVENT_SAFETY_CLAMP = "safety.clamp"
EVENT_SAFETY_RPE_ABOVE_CEILING = "safety.rpe_above_ceiling"
EVENT_AI_SET_EVAL_CLAMPED = "ai_set_eval.clamped"
EVENT_PERIODIZATION_VALIDATION_FAILED = "periodization.validation_failed"

# Eventos da secao 14 que faltavam ate a divida de observabilidade (este PR).
# `generation.fallback` tem metrica esperada ZERO em producao: cada linha e um
# caminho alternativo ao pretendido, e so existe para ser vista.
EVENT_GENERATION_FALLBACK = "generation.fallback"
# Transicao de fase F1-F6 do GRUPO muscular (decisao D2: a fase e do grupo,
# nunca da trilha) e o espelho observavel do `blocked_by` ja persistido em
# `progression_decisions`.
EVENT_PROGRESSION_TRANSITION = "progression.transition"
EVENT_PROGRESSION_BLOCKED = "progression.blocked"

# Geracao automatica da descricao de execucao a partir do briefing (M4).
# `dispatched` e a rede de seguranca funcionando (exercicio novo no catalogo);
# `dispatch_failed` tem metrica esperada ZERO - cada linha e um disparo que nao
# saiu (IAM, env var, DynamoDB), sem NENHUM efeito para o usuario, que recebeu
# o briefing normalmente. `finished` fecha o par, ja do lado da Lambda.
EVENT_EXEC_DESC_DISPATCHED = "exec_description.dispatched"
EVENT_EXEC_DESC_DISPATCH_FAILED = "exec_description.dispatch_failed"
EVENT_EXEC_DESC_FINISHED = "exec_description.finished"

# Cards ilustrados de exercicios (`app/exercise_card_service.py`,
# `app/exercise_card_jobs.py`). Campos: ids, status, `error_code`, latencia,
# custo e tamanho - **nunca** o prompt, a instrucao do admin, o motivo da
# rejeicao (texto livre) nem a chave da OpenAI. `failed`/`publish_failed`
# sao a metrica de saude (esperado ~0); `redelivery_skipped` e a reserva
# atomica PENDING -> GENERATING evitando cobranca dupla; `swept` e o sweep
# lazy fechando geracao orfa (Lambda morta, aprovacao interrompida).
EVENT_EXERCISE_CARD_REQUESTED = "exercise_card.requested"
EVENT_EXERCISE_CARD_REGENERATED = "exercise_card.regenerated"
EVENT_EXERCISE_CARD_STARTED = "exercise_card.started"
EVENT_EXERCISE_CARD_GENERATED = "exercise_card.generated"
EVENT_EXERCISE_CARD_FAILED = "exercise_card.failed"
EVENT_EXERCISE_CARD_PUBLISHED = "exercise_card.published"
EVENT_EXERCISE_CARD_PUBLISH_FAILED = "exercise_card.publish_failed"
EVENT_EXERCISE_CARD_REJECTED = "exercise_card.rejected"
EVENT_EXERCISE_CARD_REDELIVERY_SKIPPED = "exercise_card.redelivery_skipped"
EVENT_EXERCISE_CARD_SWEPT = "exercise_card.swept"

# Resumo de fim de treino reaproveitado sem chamar a OpenAI (IA-14): a entrada
# (`workout_summary_ai.input_fingerprint`) e igual a do resumo salvo. Campos:
# ids apenas. Conta o que deixou de ser pago (reentrega, pedido repetido).
EVENT_WORKOUT_SUMMARY_REUSED = "workout_summary.reused"


def log_event(event: str, **fields: Any) -> None:
    """Emite `{"event": <event>, ...}` como UMA linha de log JSON.

    Nunca levanta excecao: observabilidade nao pode derrubar o fluxo do
    treino. Valor nao serializavel cai em `str()`."""
    try:
        payload = {"event": event, **{key: value for key, value in fields.items() if value is not None}}
        logger.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 - log nunca quebra o chamador
        logger.exception("Falha ao emitir evento estruturado %s", event)
