"""`template_generation_logs` (migracao 038) e o limite de geracoes por dia
(PLANO_20260912.md secao 7.3).

Por que a linha de log nasce no ENDPOINT (status `pending`) e nao na Lambda:
o limite de 10 geracoes por usuario/dia precisa contar tentativas, nao sucessos.
Se o log so aparecesse no fim da geracao, dez cliques em sequencia passariam
todos pela checagem antes da primeira linha existir. Com a linha criada antes de
invocar a Lambda, a contagem e sempre coerente - e o `generation_log_id` ja vai
no payload do job, sem round-trip extra.

Custo: `tokens_in`/`tokens_out` vem da resposta da OpenAI e sao SEMPRE gravados.
`cost_usd` depende de tabela de preco, que muda com o tempo e nao deve ser
chumbada (nem inventada) no codigo: sai de `OPENAI_PRICE_IN_PER_MTOKEN`/
`OPENAI_PRICE_OUT_PER_MTOKEN` (USD por milhao de tokens). Sem as env vars,
`cost_usd` fica NULL e o custo se calcula depois a partir dos tokens, que estao
gravados.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

from ..db import transaction

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
                        estimate_cost_usd(tokens_in, tokens_out),
                        generation_log_id,
                    ),
                )
    except Exception:  # noqa: BLE001 - auditoria nunca derruba o job
        logger.exception("Falha ao fechar template_generation_logs id=%s", generation_log_id)


def estimate_cost_usd(tokens_in: Optional[int], tokens_out: Optional[int]) -> Optional[float]:
    """Custo em USD a partir dos precos em env var (USD por MILHAO de tokens).
    Sem preco configurado devolve None (coluna NULL) - melhor um custo ausente
    que um custo inventado; os tokens ficam gravados de qualquer forma."""
    price_in = _price("OPENAI_PRICE_IN_PER_MTOKEN")
    price_out = _price("OPENAI_PRICE_OUT_PER_MTOKEN")
    if price_in is None and price_out is None:
        return None
    total = ((tokens_in or 0) * (price_in or 0.0) + (tokens_out or 0) * (price_out or 0.0)) / 1_000_000
    return round(total, 4)


def _price(env_var: str) -> Optional[float]:
    raw = (os.environ.get(env_var) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r nao e numero; custo da periodizacao fica sem esse componente.", env_var, raw)
        return None
