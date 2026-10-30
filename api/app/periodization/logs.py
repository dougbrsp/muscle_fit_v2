"""`template_generation_logs` (migracao 038) e o limite de geracoes por dia
(PLANO_20260912.md secao 7.3).

Por que a linha de log nasce no ENDPOINT (status `pending`) e nao na Lambda:
o limite de 10 geracoes por usuario/dia precisa contar tentativas, nao sucessos.
Se o log so aparecesse no fim da geracao, dez cliques em sequencia passariam
todos pela checagem antes da primeira linha existir. Com a linha criada antes de
invocar a Lambda, a contagem e sempre coerente - e o `generation_log_id` ja vai
no payload do job, sem round-trip extra.

Custo: `tokens_in`/`tokens_out` vem da resposta do provedor e sao SEMPRE
gravados. `cost_usd` sai do DONO do preco por modelo
(`domain/nutrition/ai_usage.estimate_cost_usd`, tabela com fonte e data; mapa
§54 decisao 7 e §74): modelo fora da tabela ou token ausente -> NULL, nunca um
preco inventado. Antes do §74 o preco vinha de `OPENAI_PRICE_*_PER_MTOKEN`,
que a infra nunca publicou (a coluna ficava sempre NULL).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from ..db import transaction
from ..domain.nutrition import ai_usage

logger = logging.getLogger("musclefit.periodization")

# Secao 7.3: constante no backend, sem tabela de configuracao.
DAILY_GENERATION_LIMIT = 10

STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

# M73: codigo gravado em `validation_report.errors[]` quando a linha `pending`
# e fechada por prazo (a tabela nao tem coluna de erro; o relatorio ja e o
# lugar onde a falha de uma geracao e descrita).
WORKER_LOST_CODE = "WORKER_LOST"


def count_generations_today(conn: Any, user_id: int, *, now_utc: Optional[datetime] = None) -> int:
    """Geracoes do usuario no dia corrente DO USUARIO, qualquer status -
    tentativa conta. Usa o indice `idx_tgl_user_created`.

    M76 Lote 2b: era `created_at >= CURDATE()` - o dia UTC do Aurora. Entre
    21h e 24h de Brasilia o contador zerava (o banco ja estava no dia
    seguinte) e as geracoes da noite contavam para o dia seguinte do usuario.
    Agora o inicio do dia do usuario e convertido para o relogio do banco
    (`time_windows.user_day_start_db`) e entra como parametro; `created_at`
    continua sendo o instante do banco. `now_utc` so para teste."""
    from ..intelligence.dashboard.time_windows import user_day_start_db, user_timezone, user_today

    timezone_name = user_timezone(conn, user_id)
    day_start = user_day_start_db(user_today(timezone_name, now_utc), timezone_name)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM template_generation_logs
            WHERE user_id = %s AND created_at >= %s
            """,
            (user_id, day_start),
        )
        row = cur.fetchone()
    return int((row or {}).get("n") or 0)


def sweep_stale_pending_logs(conn: Any) -> int:
    """M73: fecha como `failed` as linhas `pending` mais velhas que o timeout
    da `musclefit-plan-generator` + folga (`async_jobs`, fonte unica). Sem
    esse fechamento, uma Lambda morta (timeout/OOM/crash) deixava a linha
    `pending` para sempre - nao bloqueia nada (o limite diario conta qualquer
    status), mas suja a auditoria.

    Preguicoso (chamado ao criar uma geracao e quando `GET /jobs` fecha um job
    de periodizacao por prazo), sem cron. So toca linhas ainda `pending`: o
    `finish_log` de um worker vivo nunca e sobrescrito. A comparacao de tempo
    fica toda no banco (`created_at` e `NOW()` no mesmo fuso da sessao), igual
    nos dois backends. Devolve quantas linhas fechou."""
    from ..async_jobs import WORKER_LOST_GRACE_SECONDS, LAMBDA_TIMEOUT_SECONDS

    stale_after = LAMBDA_TIMEOUT_SECONDS["musclefit-plan-generator"] + WORKER_LOST_GRACE_SECONDS
    report = {
        "errors": [
            {
                "code": WORKER_LOST_CODE,
                "message": "A geração não respondeu dentro do prazo (worker perdido).",
            }
        ],
        "warnings": [],
        "auto_fixes": [],
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE template_generation_logs
            SET status = %s, validation_report = %s
            WHERE status = %s AND created_at < DATE_SUB(NOW(), INTERVAL %s SECOND)
            """,
            (
                STATUS_FAILED,
                json.dumps(report, ensure_ascii=False),
                STATUS_PENDING,
                int(stale_after),
            ),
        )
        return max(0, int(cur.rowcount or 0))


def create_pending_log(conn: Any, user_id: int, request_payload: dict[str, Any], prompt_version: str) -> int:
    """Linha `pending` da geracao. `request_json` guarda o payload inteiro,
    INCLUSIVE as instrucoes livres do usuario - este e o unico lugar onde esse
    texto e persistido (a secao 14 proibe instrucao do usuario no log
    estruturado do CloudWatch)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO template_generation_logs (
                user_id, request_json, rag_sources, prompt_version, status
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                user_id,
                json.dumps(request_payload, ensure_ascii=False, default=str),
                json.dumps(request_payload.get("rag_sources") or [], ensure_ascii=False),
                prompt_version,
                STATUS_PENDING,
            ),
        )
        return int(cur.lastrowid)


def finish_log(
    generation_log_id: Optional[int],
    *,
    status: str,
    model: Optional[str] = None,
    validation_report: Optional[dict[str, Any]] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
) -> None:
    """Fecha a linha de auditoria. Nunca levanta: auditoria nao pode transformar
    uma geracao boa em job `failed` (a falha, se houver, ja esta no job)."""
    if not generation_log_id:
        return
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE template_generation_logs
                    SET status = %s, model = %s, validation_report = %s,
                        tokens_in = %s, tokens_out = %s, cost_usd = %s
                    WHERE id = %s
                    """,
                    (
                        status,
                        model,
                        json.dumps(validation_report, ensure_ascii=False, default=str)
                        if validation_report is not None
                        else None,
                        tokens_in,
                        tokens_out,
                        estimate_cost_usd(model, tokens_in, tokens_out),
                        generation_log_id,
                    ),
                )
    except Exception:  # noqa: BLE001 - auditoria nunca derruba o job
        logger.exception("Falha ao fechar template_generation_logs id=%s", generation_log_id)


# Escala da coluna `template_generation_logs.cost_usd` (DECIMAL(8,4), migracao 038).
LOG_COST_DECIMAL_PLACES = 4


def estimate_cost_usd(model: Optional[str], tokens_in: Optional[int], tokens_out: Optional[int]) -> Optional[float]:
    """Custo em USD pelo dono (`ai_usage.estimate_cost_usd`), na escala da
    coluna. Modelo sem preco ou token ausente -> None (coluna NULL)."""
    custo = ai_usage.estimate_cost_usd(model, tokens_in, tokens_out)
    if custo is None:
        return None
    return round(float(custo), LOG_COST_DECIMAL_PLACES)
