"""Persistencia da descricao de execucao do exercicio (migracao 039).

Operacoes de escrita/varredura, nenhuma regra de prompt (essa mora em
`app/exercise_description_ai.py`) e nenhuma rota (essas moram em
`app/exercise_catalog_admin.py`):

    generate_and_store(...)  gera pela OpenAI e grava UM exercicio
    store_manual_text(...)   grava o texto revisado/escrito por um admin
    pending_exercise_ids(..) quem ainda nao tem descricao (ou tem de um prompt
                             antigo) - base da geracao em lote
    claim_generation(...)    RESERVA um exercicio para geracao (migracao 042)
    release_generation_claim(...) devolve a reserva

Regra que nao pode ser quebrada: **uma revisao humana nunca e sobrescrita por
uma geracao automatica sem `force` explicito**. O admin que editou o texto em
/dominio-exercicios e a autoridade; a IA e o rascunho. Por isso
`generate_and_store` recusa (devolve `skipped`) quando a linha ja tem
`execution_description` e `force` e falso, e a varredura em lote so lista
linhas sem descricao (ou de outra versao de prompt, quando pedido).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Sequence

from .exercise_description_ai import (
    EXEC_DESC_PROMPT_VERSION,
    ExerciseDescriptionError,
    generate_execution_description,
)

logger = logging.getLogger(__name__)

STATUS_GENERATED = "generated"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

# Teto de itens por chamada da geracao em lote. A rota roda dentro da Lambda
# `musclefit-api-core` (timeout de API Gateway), e cada item e uma chamada de
# LLM de alguns segundos. O chamador pagina (o proprio retorno diz quantos
# sobraram). Este teto e um limite de CUSTO por request; o limite de TEMPO
# real e `BATCH_TIME_BUDGET_SECONDS`, abaixo.
MAX_BATCH = 10

# Orcamento de TEMPO de um lote, em segundos. O API Gateway REST corta a
# resposta em 29 s (`infra/lambda_api_core.tf`, `timeout = 29`), e cada item e
# uma chamada de LLM que costuma levar de 2 a 6 s - mas pode levar bem mais
# num dia ruim da OpenAI. Dez itens sequenciais passavam dos 29 s, o gateway
# devolvia 504 (sem CORS, que o browser mostra como "nao foi possivel
# conectar"), a Lambda morria com a transacao aberta e NADA do lote era
# gravado; cada tentativa seguinte pegava os mesmos dez ids e estourava de
# novo. Por isso o lote agora para de puxar itens novos quando o tempo gasto
# passa deste orcamento, devolvendo `time_budget_exhausted=True` e
# `remaining`; o chamador simplesmente chama de novo.
#
# 20 s deixam ~9 s de folga para o item em voo terminar quando o orcamento
# acabar no meio dele, mais o COUNT final e a serializacao. E um teto sobre
# quando COMECAR um item, nao uma interrupcao: um unico item mais lento que a
# folga ainda pode estourar - por isso o chamador tambem trata o 504.
BATCH_TIME_BUDGET_SECONDS = 20.0

# Por quanto tempo uma RESERVA de geracao (migracao 042) segura o exercicio.
# Teto folgado sobre o pior caso real: a Lambda que gera tem timeout de 120 s
# (infra/lambda_ai_generators.tf) e o `invoke` assincrono do Lambda ainda pode
# tentar de novo duas vezes. 15 minutos cobrem isso com sobra e, mais
# importante, GARANTEM que uma reserva orfa (Lambda morta, deploy no meio)
# nunca prende o exercicio para sempre: a proxima abertura do briefing, 15
# minutos depois, tenta de novo.
GENERATION_LOCK_MINUTES = 15


def _exercise_row(cursor, exercise_id: int) -> Optional[dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, name, body_position, movement, trajectory, biomechanical_pattern, primary_joint,
               resistance_vector, is_unilateral, is_compound, execution_description,
               execution_description_prompt_version, execution_description_reviewed_by
        FROM exercise_catalog WHERE id = %s
        """,
        (int(exercise_id),),
    )
    return cursor.fetchone()


def _muscle_rows(cursor, exercise_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT emc.role, mg.name AS muscle_group_name
        FROM exercise_muscle_contributions emc
        JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
        WHERE emc.exercise_id = %s AND emc.is_active = 1
        ORDER BY FIELD(emc.role, 'PRIMARY', 'SECONDARY', 'STABILIZER'), mg.name
        """,
        (int(exercise_id),),
    )
    return list(cursor.fetchall() or [])


def _equipment_names(cursor, exercise_id: int) -> list[str]:
    cursor.execute(
        """
        SELECT eq.name
        FROM equipment_exercises eqex
        JOIN equipment eq ON eq.id = eqex.equipment_id
        WHERE eqex.exercise_id = %s AND eqex.is_active = 1
        ORDER BY eqex.is_preferred DESC, eq.name
        LIMIT 5
        """,
        (int(exercise_id),),
    )
    return [str(row["name"]) for row in (cursor.fetchall() or []) if row.get("name")]


def _claim_cutoff(now: Optional[datetime] = None) -> datetime:
    """Antes deste instante, uma reserva de geracao esta VENCIDA."""
    return (now or datetime.now()).replace(microsecond=0) - timedelta(minutes=GENERATION_LOCK_MINUTES)


def claim_generation(cursor, exercise_id: int, *, now: Optional[datetime] = None) -> bool:
    """RESERVA este exercicio para geracao automatica. `True` = a reserva e
    sua, pode disparar a geracao; `False` = nao ha o que gerar, ou outro
    processo ja esta gerando.

    Um unico `UPDATE` condicional, e nao `SELECT` seguido de `UPDATE`: o
    briefing roda em varias instancias concorrentes da Lambda `api-core`, e
    entre um SELECT e um UPDATE cabem dois disparos do mesmo exercicio. O
    MySQL serializa o UPDATE na linha, entao de N tentativas simultaneas
    exatamente UMA ve `rowcount = 1`.

    As condicoes sao as mesmas regras de sempre, agora escritas em SQL para
    serem atomicas: so reserva quem NAO tem texto (nunca sobrescreve) e nao
    foi revisado por humano, e so rouba uma reserva ja VENCIDA.

    `rowcount` aqui e confiavel nos dois backends: o valor gravado
    (`now`) e sempre diferente do que estava la (a condicao exige NULL ou
    um carimbo de 15 minutos atras), entao "linhas encontradas" (pymysql, com
    CLIENT.FOUND_ROWS) e "linhas alteradas" (Data API) coincidem.
    """
    stamp = (now or datetime.now()).replace(microsecond=0)
    cursor.execute(
        """
        UPDATE exercise_catalog
        SET execution_description_generation_started_at = %s
        WHERE id = %s
          AND (execution_description IS NULL OR execution_description = '')
          AND execution_description_reviewed_at IS NULL
          AND (execution_description_generation_started_at IS NULL
               OR execution_description_generation_started_at < %s)
        """,
        (stamp, int(exercise_id), _claim_cutoff(now)),
    )
    return int(cursor.rowcount or 0) > 0


def release_generation_claim(cursor, exercise_id: int) -> None:
    """Devolve a reserva, liberando o exercicio para a proxima tentativa.

    Chamado quando o disparo NAO aconteceu (falha ao enfileirar) ou quando a
    geracao terminou sem deixar texto por um motivo que nao se repetira. Uma
    FALHA de geracao de proposito NAO libera: a reserva vence sozinha em
    `GENERATION_LOCK_MINUTES` e serve de back-off - senao um exercicio que
    falha sempre viraria uma chamada de LLM por abertura de tela.
    """
    cursor.execute(
        "UPDATE exercise_catalog SET execution_description_generation_started_at = NULL WHERE id = %s",
        (int(exercise_id),),
    )


def pending_exercise_ids(
    cursor,
    *,
    limit: int,
    prompt_version: Optional[str] = None,
    active_only: bool = True,
    skip_in_progress: bool = True,
    now: Optional[datetime] = None,
) -> list[int]:
    """Ids que ainda precisam de descricao, do menor para o maior.

    Sem `prompt_version`: so quem nao tem descricao nenhuma. Com
    `prompt_version`: tambem quem tem descricao gerada por uma versao
    DIFERENTE de prompt - e nunca quem foi revisado por um humano
    (`execution_description_reviewed_by IS NOT NULL`), que e justamente o
    texto que nao se regera sozinho.

    `skip_in_progress` (migracao 042) deixa de fora quem ja tem uma reserva
    de geracao VALIDA: sem isso, um backfill em lote rodando enquanto um
    usuario abre o briefing de um exercicio novo pagaria a MESMA chamada de
    LLM duas vezes. Com `force`, o admin passa por cima da reserva de
    proposito (ele esta pedindo para regerar).
    """
    where = ["(execution_description IS NULL OR execution_description = '')"]
    params: list[Any] = []
    if prompt_version:
        where = [
            "((execution_description IS NULL OR execution_description = '')"
            " OR (execution_description_reviewed_by IS NULL"
            "     AND COALESCE(execution_description_prompt_version, '') <> %s))"
        ]
        params.append(str(prompt_version))
    if skip_in_progress:
        where.append(
            "(execution_description_generation_started_at IS NULL"
            " OR execution_description_generation_started_at < %s)"
        )
        params.append(_claim_cutoff(now))
    if active_only:
        where.append("is_active = 1")
    cursor.execute(
        f"SELECT id FROM exercise_catalog WHERE {' AND '.join(where)} ORDER BY id LIMIT %s",
        tuple(params + [max(1, int(limit))]),
    )
    return [int(row["id"]) for row in (cursor.fetchall() or [])]


def pending_count(cursor, *, prompt_version: Optional[str] = None, active_only: bool = True) -> int:
    """Quantos exercicios ainda faltam (para o chamador saber se pagina)."""
    where = ["(execution_description IS NULL OR execution_description = '')"]
    params: list[Any] = []
    if prompt_version:
        where = [
            "((execution_description IS NULL OR execution_description = '')"
            " OR (execution_description_reviewed_by IS NULL"
            "     AND COALESCE(execution_description_prompt_version, '') <> %s))"
        ]
        params.append(str(prompt_version))
    if active_only:
        where.append("is_active = 1")
    cursor.execute(f"SELECT COUNT(*) AS n FROM exercise_catalog WHERE {' AND '.join(where)}", tuple(params))
    row = cursor.fetchone() or {}
    return int(row.get("n") or 0)


def store_manual_text(cursor, exercise_id: int, text: Optional[str], *, reviewer_user_id: Optional[int]) -> None:
    """Grava o texto REVISADO por um humano.

    Marca `reviewed_by`/`reviewed_at` e ZERA `model`/`prompt_version`: o texto
    deixou de ser o que a IA produziu, e manter a etiqueta do modelo mentiria
    sobre a origem. Texto vazio/None apaga a descricao e a auditoria junto
    (volta ao estado "ainda nao gerado").

    Nos dois casos a reserva de geracao (042) e zerada: ou o admin acabou de
    escrever o texto (nao ha mais o que gerar), ou acabou de apaga-lo, e ai
    a proxima abertura do briefing deve poder tentar de novo na hora, sem
    esperar a reserva vencer.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        cursor.execute(
            """
            UPDATE exercise_catalog
            SET execution_description = NULL, execution_description_model = NULL,
                execution_description_prompt_version = NULL, execution_description_generated_at = NULL,
                execution_description_reviewed_by = NULL, execution_description_reviewed_at = NULL,
                execution_description_generation_started_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (int(exercise_id),),
        )
        return
    cursor.execute(
        """
        UPDATE exercise_catalog
        SET execution_description = %s, execution_description_model = NULL,
            execution_description_prompt_version = NULL,
            execution_description_reviewed_by = %s, execution_description_reviewed_at = %s,
            execution_description_generation_started_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (cleaned, reviewer_user_id, datetime.now().replace(microsecond=0), int(exercise_id)),
    )


def generate_and_store(
    cursor,
    exercise_id: int,
    *,
    force: bool = False,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Gera e grava a descricao de UM exercicio.

    Devolve `{"exercise_id", "status", ...}` - nunca levanta por falha de
    geracao, para que um item ruim nao derrube um lote inteiro. `status`:
    `generated` | `skipped` (ja tinha e `force` nao foi pedido, ou exercicio
    inexistente) | `failed` (a OpenAI falhou; `error` explica).
    """
    row = _exercise_row(cursor, exercise_id)
    if not row:
        return {"exercise_id": int(exercise_id), "status": STATUS_SKIPPED, "reason": "not_found"}
    if (row.get("execution_description") or "").strip() and not force:
        return {"exercise_id": int(exercise_id), "status": STATUS_SKIPPED, "reason": "already_generated"}

    payload = dict(row)
    payload["equipment_names"] = _equipment_names(cursor, exercise_id)
    try:
        generated = generate_execution_description(payload, _muscle_rows(cursor, exercise_id), model=model)
    except ExerciseDescriptionError as exc:
        logger.warning("Falha ao gerar descricao de execucao exercise_id=%s: %s", exercise_id, exc)
        return {"exercise_id": int(exercise_id), "status": STATUS_FAILED, "error": str(exc)}

    cursor.execute(
        """
        UPDATE exercise_catalog
        SET execution_description = %s, execution_description_model = %s,
            execution_description_prompt_version = %s, execution_description_generated_at = %s,
            execution_description_reviewed_by = NULL, execution_description_reviewed_at = NULL,
            execution_description_generation_started_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (
            generated.text,
            generated.model,
            generated.prompt_version,
            datetime.now().replace(microsecond=0),
            int(exercise_id),
        ),
    )
    return {
        "exercise_id": int(exercise_id),
        "status": STATUS_GENERATED,
        "model": generated.model,
        "prompt_version": generated.prompt_version,
        "tokens_in": generated.tokens_in,
        "tokens_out": generated.tokens_out,
    }


def generate_batch(
    cursor,
    *,
    limit: int = MAX_BATCH,
    exercise_ids: Optional[Sequence[int]] = None,
    force: bool = False,
    prompt_version: Optional[str] = None,
    model: Optional[str] = None,
    commit: Optional[Callable[[], None]] = None,
    time_budget_seconds: float = BATCH_TIME_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Gera um LOTE: no maximo `MAX_BATCH` itens E no maximo
    `time_budget_seconds` de relogio. Ids explicitos tem prioridade sobre a
    varredura do que falta.

    Duas garantias que resolvem o "trava no item 51" de producao:

    - **Cada item gerado fica gravado na hora.** `commit` (a `Connection.commit`
      do chamador) e chamado logo apos o UPDATE de cada item. Se o request
      morrer no item seguinte (Lambda cortada pelo API Gateway), o que ja foi
      gerado - e pago - esta no banco. Sem isso, `with conn:` so commitaria no
      fim, e o 504 descartava o lote inteiro.
    - **O tempo e o limite real, nao a contagem.** Antes de comecar cada item
      (a partir do segundo), o lote confere quanto ja gastou; passou de
      `time_budget_seconds`, para e devolve `time_budget_exhausted=True` com
      os ids que nao chegou a tentar em `deferred`. O primeiro item sempre
      roda: um lote que nao tenta nada faria o chamador desistir ("stalled").

    `clock` e injetavel (segundos monotonicos) para os testes provarem o
    orcamento sem dormir. `requested` e o numero de itens EFETIVAMENTE
    tentados (gerados + pulados + falhos), que e o que o chamador soma como
    progresso.
    """
    size = max(1, min(int(limit), MAX_BATCH))
    if exercise_ids:
        alvos = [int(value) for value in exercise_ids]
    else:
        # `skip_in_progress=not force`: o backfill nao repete a chamada de LLM
        # de um exercicio que a geracao automatica do briefing ja reservou -
        # mas `force` e um pedido explicito do admin e passa por cima.
        alvos = pending_exercise_ids(
            cursor, limit=size, prompt_version=prompt_version, skip_in_progress=not force
        )
    # O corte e aqui, DEPOIS de escolher os alvos, e nao so no `LIMIT` do SQL:
    # o teto e sobre quantas chamadas de LLM este request faz, e ele precisa
    # valer tanto para a varredura quanto para a lista de ids explicitos.
    alvos = alvos[:size]

    started = clock()
    results: list[dict[str, Any]] = []
    deferred: list[int] = []
    for index, exercise_id in enumerate(alvos):
        elapsed = clock() - started
        if index > 0 and elapsed >= time_budget_seconds:
            deferred = alvos[index:]
            logger.info(
                "generate_batch: orcamento de tempo esgotado (%.1fs >= %.1fs) apos %d item(ns); %d adiado(s)",
                elapsed,
                time_budget_seconds,
                index,
                len(deferred),
            )
            break
        result = generate_and_store(cursor, exercise_id, force=force, model=model)
        results.append(result)
        if result["status"] == STATUS_GENERATED and commit is not None:
            commit()

    return {
        "requested": len(results),
        "generated": sum(1 for item in results if item["status"] == STATUS_GENERATED),
        "skipped": sum(1 for item in results if item["status"] == STATUS_SKIPPED),
        "failed": sum(1 for item in results if item["status"] == STATUS_FAILED),
        "remaining": pending_count(cursor, prompt_version=prompt_version),
        "time_budget_exhausted": bool(deferred),
        "deferred": len(deferred),
        "elapsed_seconds": round(max(0.0, clock() - started), 2),
        "time_budget_seconds": time_budget_seconds,
        "prompt_version": EXEC_DESC_PROMPT_VERSION,
        "items": results,
    }
