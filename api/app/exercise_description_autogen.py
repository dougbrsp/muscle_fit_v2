"""Rede de seguranca da descricao de execucao (M4): quando o briefing abre um
exercicio que AINDA nao tem texto, enfileira a geracao em SEGUNDO PLANO e
responde `null` na hora, como sempre. O texto aparece na proxima abertura.

Para que serve: o backfill em lote (`/dominio-exercicios`) cobre o catalogo de
hoje. Um exercicio cadastrado DEPOIS do backfill ficaria sem descricao para
sempre ate alguem lembrar de rodar o lote de novo. Este modulo e quem lembra.

    O que este modulo NAO faz: fazer o usuario esperar.

Nenhuma chamada a OpenAI acontece aqui. `dispatch_if_missing` faz, no maximo,
UM `UPDATE` de reserva e UM `lambda:Invoke` assincrono (fire-and-forget) - na
ordem de milissegundos - e engole qualquer excecao. O briefing e a tela que o
usuario abre entre a lista e a primeira serie; ele nunca pode ficar mais lento
nem responder 500 por causa de uma descricao que, afinal, e opcional.

Por que enfileirar em outra Lambda em vez de rodar em background aqui
--------------------------------------------------------------------
`musclefit-api-core` roda em Lambda. Depois que o handler retorna a resposta
HTTP, o runtime CONGELA o processo: uma thread, uma task de asyncio ou um
`BackgroundTasks` do FastAPI iniciados no caminho do request simplesmente
param no meio, e so voltam a rodar (se voltarem) quando aquele mesmo ambiente
atender outro request - minutos depois, ou nunca. Uma geracao de ~10 s feita
assim funcionaria perfeitamente em DEV (uvicorn, processo vivo) e falharia
calada em producao. Por isso o trabalho vai para OUTRO processo.

O padrao ja existe no projeto e e reaproveitado inteiro, sem infra nova:
`app/async_jobs.py` (job no DynamoDB + `invoke_async` com
`InvocationType="Event"`) disparando `musclefit-plan-generator`, que desde o
PR-16 ja despacha por `job_kind` (`app/lambda_plan_generator.py`). Este e um
`job_kind` novo - `exercise-description` - na MESMA funcao, mesma imagem,
mesmo timeout de 120 s, mesma IAM. A `api-core` ja tem
`lambda:InvokeFunction` nessa funcao e `dynamodb:PutItem` na tabela de jobs
(infra/lambda_ai_generators.tf), entao **nenhuma mudanca de Terraform e
necessaria**.

Sem geracao duplicada
---------------------
A reserva e uma linha do banco (`execution_description_generation_started_at`,
migracao 042) tomada com um UPDATE condicional atomico
(`exercise_description_service.claim_generation`). Dois usuarios abrindo o
mesmo exercicio no mesmo segundo caem em duas instancias diferentes da
Lambda; exatamente uma ganha o UPDATE e dispara. A outra nao faz nada. A
reserva EXPIRA sozinha (15 min), entao uma Lambda morta no meio nao prende o
exercicio para sempre.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from . import async_jobs, exercise_description_service as descriptions
from .db import transaction
from .observability import (
    EVENT_EXEC_DESC_DISPATCH_FAILED,
    EVENT_EXEC_DESC_DISPATCHED,
    EVENT_EXEC_DESC_FINISHED,
    log_event,
)

logger = logging.getLogger(__name__)

JOB_TYPE = "exercise-description"
JOB_KIND = "exercise-description"

# Nome da env var da `api-core` que guarda o nome real da Lambda de IA (a
# mesma de `POST /workout-plans/generate`) - ver async_jobs.invoke_async.
GENERATOR_FUNCTION_ENV_VAR = "PLAN_GENERATOR_FUNCTION_NAME"


def is_enabled() -> bool:
    """A rede de seguranca so existe onde ha para onde enfileirar.

    Ambiente sem `PLAN_GENERATOR_FUNCTION_NAME` (um teste, um script, um
    uvicorn solto) nao dispara nada e nao reclama - e a mesma env var que os
    outros caminhos assincronos ja exigem, entao DEV e producao se comportam
    igual: os dois a tem configurada.
    """
    return bool(os.environ.get(GENERATOR_FUNCTION_ENV_VAR))


def dispatch_if_missing(exercise_id: Optional[int], *, owner_user_id: int, reason: str = "briefing") -> bool:
    """Reserva e enfileira a geracao da descricao deste exercicio.

    Devolve `True` se ESTA chamada enfileirou a geracao; `False` em todo o
    resto (ja tem texto, outro processo ja esta gerando, ambiente sem a
    Lambda, ou o disparo falhou). **Nunca levanta**: o chamador e o briefing.
    """
    if not exercise_id or not is_enabled():
        return False

    claimed = False
    try:
        with transaction() as conn:
            with conn.cursor() as cursor:
                claimed = descriptions.claim_generation(cursor, int(exercise_id))
        # A reserva so vale depois do COMMIT (fim do `with` acima): disparar
        # antes abriria a janela em que a Lambda le a linha sem enxergar a
        # marca e um segundo request tambem reserva.
        if not claimed:
            return False

        # M65: dono = usuario cujo briefing disparou a geracao (ninguem faz
        # polling deste job; o texto aparece no catalogo).
        job_id = async_jobs.create_job(
            JOB_TYPE,
            request={"exercise_id": int(exercise_id), "reason": reason},
            owner_user_id=owner_user_id,
        )
        async_jobs.invoke_async(
            GENERATOR_FUNCTION_ENV_VAR,
            {"job_id": job_id, "job_kind": JOB_KIND, "exercise_id": int(exercise_id)},
        )
    except Exception as exc:  # noqa: BLE001 - o briefing responde de qualquer jeito
        logger.warning("Falha ao enfileirar a descricao de execucao exercise_id=%s: %s", exercise_id, exc)
        log_event(
            EVENT_EXEC_DESC_DISPATCH_FAILED,
            exercise_id=int(exercise_id),
            reason_code=type(exc).__name__,
            source=reason,
        )
        if claimed:
            _release_quietly(int(exercise_id))
        return False

    log_event(EVENT_EXEC_DESC_DISPATCHED, exercise_id=int(exercise_id), job_id=job_id, source=reason)
    return True


def run_generation_job(exercise_id: int, *, model: Optional[str] = None) -> dict:
    """O outro lado do disparo: roda DENTRO da Lambda de IA
    (`app/lambda_plan_generator.py`, `job_kind="exercise-description"`).

    `force=False` fixo e sem parametro para mudar isso: este caminho e
    automatico, nao tem humano confirmando nada, e por definicao nunca pode
    sobrescrever texto existente nem revisao humana. Quem regera de proposito
    sao as duas rotas de admin.

    A reserva e devolvida quando o resultado e final (`generated` ja zera a
    marca junto com a escrita; `skipped` significa que alguem chegou antes).
    Em `failed`, NAO: a reserva vence sozinha e serve de back-off contra um
    exercicio que falha a cada abertura da tela.
    """
    if not exercise_id:
        raise ValueError("Evento de descricao de execucao sem exercise_id.")

    with transaction() as conn:
        with conn.cursor() as cursor:
            result = descriptions.generate_and_store(cursor, int(exercise_id), force=False, model=model)
            if result["status"] == descriptions.STATUS_SKIPPED:
                descriptions.release_generation_claim(cursor, int(exercise_id))

    log_event(
        EVENT_EXEC_DESC_FINISHED,
        exercise_id=int(exercise_id),
        status=result.get("status"),
        model=result.get("model"),
        prompt_version=result.get("prompt_version"),
        reason_code=result.get("reason"),
        tokens_in=result.get("tokens_in"),
        tokens_out=result.get("tokens_out"),
    )
    return result


def _release_quietly(exercise_id: int) -> None:
    """Devolve a reserva depois de um disparo que nao saiu, para a proxima
    abertura da tela tentar de novo em vez de esperar a reserva vencer."""
    try:
        with transaction() as conn:
            with conn.cursor() as cursor:
                descriptions.release_generation_claim(cursor, exercise_id)
    except Exception:  # noqa: BLE001 - a reserva vence sozinha; isto e so cortesia
        logger.warning("Falha ao liberar a reserva da descricao exercise_id=%s", exercise_id)
