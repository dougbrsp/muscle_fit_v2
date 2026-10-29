"""Uso e cota de IA da nutrição: acesso ao `ai_usage_log` (migração 085; plano
§5 E7, §6.2 e §8.3; Lote L5a). É a base que o L5b (texto da refeição) e o L6
(foto do rótulo) consomem; este módulo não tem rota.

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §3.7 e §54): features,
status, cota por feature, job type de cada feature, quem pode fechar a linha e
custo são de `domain/nutrition/ai_usage`; o início do "hoje" do usuário no
relógio do banco, de `time_windows` (`user_day_start_db_for`, fuso do perfil);
o prazo do worker perdido (timeout da Lambda + folga) e o código `worker_lost`,
de `async_jobs` (M73, paridade com `infra/*.tf`). Esta camada só monta o SQL,
igual nos dois backends (PyMySQL no DEV, RDS Data API na produção), e devolve o
que o motor decidiu.

Contrato para o L5b e o L6 (a rota "checa a cota -> abre pending -> 202"):

    from .domain.nutrition.ai_usage import AiFeature, AiUsageStatus, MEAL_PARSE_JOB_TYPE

    # rota (api-core)
    usage_id = open_usage(user_id=uid, feature=AiFeature.MEAL_PARSE,
                          prompt_version=PROMPT_VERSION, model=modelo, image_count=0)
    #   uma transação curta: trava a linha do usuário (1º statement) -> conta as
    #   tentativas do dia -> 429 `ai_quota_exceeded` (cota estourada) ou abre
    #   `pending`; 503 `ai_usage_table_missing` antes da 085
    job_id = create_job(MEAL_PARSE_JOB_TYPE, request=..., owner_user_id=uid)   # async_jobs
    link_job(usage_id, job_id)            # best-effort, nunca levanta
    invoke_async("WORKOUT_AI_FUNCTION_NAME", {..., "ai_usage_id": usage_id})  # async_jobs
    # falha ao despachar: finish_usage(usage_id, status=AiUsageStatus.FAILED, error_code=...)
    # (o exemplo não escreve `async_jobs.` antes das duas chamadas porque o teste do
    # M73 lê o texto dos módulos; no código da rota elas levam o prefixo, com o nome
    # do job type importado do dono, sem `ai_usage.` na frente, e o job type entra
    # em `async_jobs.JOB_TYPE_LAMBDA` no mesmo PR, com a chave `ai_usage.<NOME>`)

    # worker (Lambda): sucesso, falha, recusa
    finish_usage(usage_id, status=AiUsageStatus.SUCCEEDED, model=result.model,
                 tokens_in=result.tokens_in, tokens_out=result.tokens_out)
    finish_usage(usage_id, status=AiUsageStatus.FAILED, error_code=exc.code)

    # tela ("x de 30 hoje"), com a conexão da rota
    usage_today(conn, user_id=uid, feature=AiFeature.MEAL_PARSE).to_dict()

As funções com `conn` (`reserve_usage`, `count_usage_today`, `usage_today`,
`attach_job`, `sweep_stale_pending`) usam a conexão/transação do chamador; as
de cima (`open_usage`, `link_job`, `finish_usage`) abrem a sua.

Concorrência: `reserve_usage` começa travando a linha do usuário (`SELECT ...
FOR UPDATE`, como a trava por exercício dos cards) e só depois conta: a
contagem é a primeira leitura consistente da transação, então vê o commit da
reserva que soltou a trava, e duas reservas simultâneas do mesmo usuário se
enfileiram. Provado no PyMySQL e no dublê da Data API (que abre a transação
com o mesmo `BEGIN`); a AWS não documenta se o `BeginTransaction` real já abre
o retrato — se abrir, duas reservas simultâneas do mesmo usuário podem passar
1 cada da cota (risco residual baixo, registrado no mapa §54). Contar com
`FOR SHARE` foi descartado: as travas de gap da faixa de um usuário são as do
começo da faixa do próximo, e duas reservas de usuários diferentes (que a trava
de `users` não serializa) entrariam em deadlock ao inserir no mesmo gap.
A trava segura, enquanto a reserva dura, toda escrita do mesmo usuário que
dependa da linha dele (INSERT em tabela filha, `UPDATE users`): por isso a
reserva roda SEMPRE na transação curta de `open_usage` — nunca dentro de uma
transação longa da rota, nunca em volta de uma chamada à OpenAI ou ao DynamoDB.

Sweep: sem agendamento nem infra. Roda no worker (best-effort, transação
própria, depois do commit do fechamento) a cada `finish_usage` sem `conn`, fora
do caminho do usuário, e é global (todos os
usuários, índice `(status, started_at)`). Como a cota conta toda tentativa, o
sweep não muda nenhuma decisão de cota: só fecha a auditoria. O L5b/L6 pode
chamá-lo também quando o `GET /jobs` fechar um job da feature como
`worker_lost` (`ai_usage.feature_for_job_type`), como a periodização faz.
Feature cujo job type ainda não está em `async_jobs.JOB_TYPE_LAMBDA` (antes do
L5b/L6) não tem prazo e fica de fora: sem rota, não há linha dela.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException

from . import async_jobs
from .db import get_conn, transaction
from .db_helpers import connect_or_503
from .domain.nutrition import ai_usage
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.ai_usage import AiFeature, AiUsageStatus, QuotaStatus
from .intelligence.dashboard.time_windows import user_day_start_db_for
from .nutrition_schema import AI_USAGE_TABLE, require_ai_usage_table

logger = logging.getLogger("musclefit.nutrition.ai_usage")

TABLE = AI_USAGE_TABLE

LOCK_USER_SQL = "SELECT id FROM users WHERE id = %s FOR UPDATE"
COUNT_SINCE_SQL = f"SELECT COUNT(*) AS n FROM {TABLE} WHERE user_id = %s AND feature = %s AND started_at >= %s"
INSERT_PENDING_SQL = (
    f"INSERT INTO {TABLE} (user_id, feature, status, prompt_version, model, image_count) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)
ATTACH_JOB_SQL = f"UPDATE {TABLE} SET job_id = %s WHERE id = %s AND job_id IS NULL"
SELECT_FOR_FINISH_SQL = f"SELECT model, status, error_code FROM {TABLE} WHERE id = %s"
# O WHERE repete, na escrita, a regra do dono (`ai_usage.worker_may_close`), com os
# valores dele: entre a leitura e a escrita a linha pode ter mudado.
FINISH_SQL = (
    f"UPDATE {TABLE} SET status = %s, model = %s, tokens_in = %s, tokens_out = %s, cost_usd = %s, "
    "error_code = %s, finished_at = NOW() "
    "WHERE id = %s AND (status = %s OR (status = %s AND error_code = %s))"
)
# A comparação de tempo fica toda no banco (`started_at` e `NOW()` no mesmo
# relógio), igual nos dois backends, como o sweep da periodização (M73).
SWEEP_SQL = (
    f"UPDATE {TABLE} SET status = %s, error_code = %s, finished_at = NOW() "
    "WHERE status = %s AND feature = %s AND started_at < DATE_SUB(NOW(), INTERVAL %s SECOND)"
)

# Tamanho das colunas de texto (espelho do DDL da 085): o valor é cortado.
_TEXT_COLUMN_MAX: dict[str, int] = {"prompt_version": 40, "model": 80, "error_code": 64, "job_id": 64}


def _clip(column: str, value: Any) -> Optional[str]:
    if value is None:
        return None
    texto = str(value).strip()
    return texto[: _TEXT_COLUMN_MAX[column]] or None


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message, **extra})


def _connect():
    return connect_or_503(get_conn)


def quota_exceeded_error(status: QuotaStatus) -> HTTPException:
    """429 com o código e o texto do dono (número interpolado da cota)."""
    return _error(
        429,
        ai_usage.ERR_AI_QUOTA_EXCEEDED,
        nutrition_labels.ai_quota_exceeded_label(status.feature),
        **status.to_dict(),
    )


# ---------------------------------------------------------------------------
# Cota (conexão do chamador)
# ---------------------------------------------------------------------------


def count_usage_today(conn: Any, *, user_id: int, feature: Any, now_utc: Optional[datetime] = None) -> int:
    """Tentativas da feature no dia do usuário (qualquer status: tentativa conta).
    Usa o índice `(user_id, feature, started_at)`. 503 sem a 085 (a tela do
    L5b pode ler o uso antes da migração rodar). `now_utc` só para teste."""
    codigo = ai_usage.normalize_feature(feature)
    with conn.cursor() as cur:
        require_ai_usage_table(cur)
        cur.execute(COUNT_SINCE_SQL, (int(user_id), codigo.value, user_day_start_db_for(conn, int(user_id), now_utc)))
        linha = cur.fetchone() or {}
    return int(linha.get("n") or 0)


def usage_today(conn: Any, *, user_id: int, feature: Any, now_utc: Optional[datetime] = None) -> QuotaStatus:
    """Uso e limite da feature hoje, pela decisão do motor (`ai_usage.quota_status`)."""
    return ai_usage.quota_status(feature, count_usage_today(conn, user_id=user_id, feature=feature, now_utc=now_utc))


def reserve_usage(
    conn: Any,
    *,
    user_id: int,
    feature: Any,
    prompt_version: Optional[str] = None,
    model: Optional[str] = None,
    image_count: int = 0,
    now_utc: Optional[datetime] = None,
) -> int:
    """Checa a cota e abre a linha `pending` na MESMA transação (do chamador),
    que deve ser curta, só desta reserva e começar por ela — as rotas usam
    `open_usage`. A trava do usuário é o PRIMEIRO statement: a contagem é a
    primeira leitura consistente depois dela, então o retrato da transação já
    inclui a reserva que acabou de soltar a trava (ressalva da Data API no
    docstring do módulo).

    404 sem o usuário; 503 sem a 085; 429 `ai_quota_exceeded` com
    `{feature, limit, used, remaining, allowed}` quando a cota do dia acabou.
    Devolve o id da linha (o `ai_usage_id` do payload do worker)."""
    codigo = ai_usage.normalize_feature(feature)
    imagens = ai_usage.count_or_none(image_count)
    if imagens is None:
        raise ValueError(f"image_count inválido: {image_count!r}")
    with conn.cursor() as cur:
        cur.execute(LOCK_USER_SQL, (int(user_id),))
        if cur.fetchone() is None:
            raise _error(404, "user_not_found", "Usuário não encontrado.")
    status = usage_today(conn, user_id=user_id, feature=codigo, now_utc=now_utc)
    if not status.allowed:
        raise quota_exceeded_error(status)
    with conn.cursor() as cur:
        cur.execute(
            INSERT_PENDING_SQL,
            (
                int(user_id),
                codigo.value,
                ai_usage.OPEN_STATUS.value,
                _clip("prompt_version", prompt_version),
                _clip("model", model),
                imagens,
            ),
        )
        return int(cur.lastrowid)


def attach_job(conn: Any, usage_id: int, job_id: str) -> bool:
    """Grava o `job_id` do DynamoDB na linha (uma vez). True se gravou."""
    with conn.cursor() as cur:
        cur.execute(ATTACH_JOB_SQL, (_clip("job_id", job_id), int(usage_id)))
        return int(cur.rowcount or 0) > 0


# ---------------------------------------------------------------------------
# Sweep (conexão do chamador)
# ---------------------------------------------------------------------------


def stale_after_seconds() -> dict[AiFeature, int]:
    """Prazo do sweep por feature: o do worker perdido do M73 para o job type da
    feature (timeout da Lambda + folga). Feature sem job type registrado em
    `async_jobs.JOB_TYPE_LAMBDA` fica de fora (ainda não tem rota)."""
    prazos: dict[AiFeature, int] = {}
    for feature, job_type in ai_usage.FEATURE_JOB_TYPE.items():
        prazo = async_jobs.worker_lost_deadline_seconds(job_type)
        if prazo is not None:
            prazos[feature] = int(prazo)
    return prazos


def sweep_stale_pending(conn: Any) -> int:
    """Fecha como perdidas (`SWEEP_PRESUMED_STATUS` + `worker_lost`) as linhas
    ainda abertas mais velhas que o prazo da feature (todos os usuários). Só toca
    a linha aberta: o fechamento de um worker nunca é sobrescrito. Devolve
    quantas linhas fechou."""
    fechadas = 0
    with conn.cursor() as cur:
        for feature, segundos in stale_after_seconds().items():
            cur.execute(
                SWEEP_SQL,
                (
                    ai_usage.SWEEP_PRESUMED_STATUS.value,
                    async_jobs.WORKER_LOST_ERROR_CODE,
                    ai_usage.OPEN_STATUS.value,
                    feature.value,
                    segundos,
                ),
            )
            fechadas += max(0, int(cur.rowcount or 0))
    return fechadas


# ---------------------------------------------------------------------------
# Entradas de alto nível (abrem a própria transação)
# ---------------------------------------------------------------------------


def _sweep_best_effort() -> None:
    try:
        with transaction() as conn:
            sweep_stale_pending(conn)
    except Exception:  # noqa: BLE001 - limpeza de auditoria nunca derruba o worker
        logger.exception("Falha no sweep de ai_usage_log pendentes")


def open_usage(
    *,
    user_id: int,
    feature: Any,
    prompt_version: Optional[str] = None,
    model: Optional[str] = None,
    image_count: int = 0,
    now_utc: Optional[datetime] = None,
) -> int:
    """O passo da rota antes do 202: `reserve_usage` numa transação curta e só
    dela (503 de banco indisponível, 503 sem a 085, 404, 429 de cota)."""
    with _connect() as conn:
        return reserve_usage(
            conn,
            user_id=user_id,
            feature=feature,
            prompt_version=prompt_version,
            model=model,
            image_count=image_count,
            now_utc=now_utc,
        )


def link_job(usage_id: Optional[int], job_id: Optional[str]) -> bool:
    """`attach_job` numa transação própria. Nunca levanta: sem o vínculo a linha
    continua valendo para a cota e a auditoria."""
    if not usage_id or not job_id:
        return False
    try:
        with transaction() as conn:
            return attach_job(conn, int(usage_id), str(job_id))
    except Exception:  # noqa: BLE001 - auditoria nunca derruba o despacho
        logger.exception("Falha ao gravar job_id em ai_usage_log id=%s", usage_id)
        return False


def finish_usage(
    usage_id: Optional[int],
    *,
    status: Any,
    model: Optional[str] = None,
    tokens_in: Any = None,
    tokens_out: Any = None,
    cached_tokens_in: Any = None,
    error_code: Optional[str] = None,
    conn: Any = None,
) -> bool:
    """Fecha a linha com o relato do worker: status terminal, modelo efetivo
    (sem ele, o da reserva), tokens e custo do motor (`ai_usage.estimate_cost_usd`;
    `cached_tokens_in` só entra na conta). Fecha só o que o dono permite
    (`ai_usage.worker_may_close`: a linha aberta ou a que o sweep presumiu
    perdida); outro fechamento terminal fica como está (False).

    Sem `conn` (o caso do worker), abre a própria transação e, DEPOIS do commit,
    roda o sweep (best-effort, outra transação). Com `conn`, só fecha: o sweep
    numa segunda conexão esperaria a trava da linha que esta transação ainda
    não commitou (o UPDATE por faixa do sweep passa por ela) até o lock wait
    timeout — quem passa `conn` chama `sweep_stale_pending` depois de commitar.

    Nunca levanta: a auditoria não pode transformar um resultado bom em job
    falho."""
    if not usage_id:
        return False
    try:
        final = ai_usage.normalize_terminal_status(status)
        if conn is not None:
            return _finish(conn, int(usage_id), final, model, tokens_in, tokens_out, cached_tokens_in, error_code)
        with transaction() as nova:
            fechou = _finish(nova, int(usage_id), final, model, tokens_in, tokens_out, cached_tokens_in, error_code)
    except Exception:  # noqa: BLE001 - auditoria nunca derruba o job
        logger.exception("Falha ao fechar ai_usage_log id=%s", usage_id)
        return False
    _sweep_best_effort()
    return fechou


def _finish(
    conn: Any,
    usage_id: int,
    status: AiUsageStatus,
    model: Optional[str],
    tokens_in: Any,
    tokens_out: Any,
    cached_tokens_in: Any,
    error_code: Optional[str],
) -> bool:
    perdida = async_jobs.WORKER_LOST_ERROR_CODE
    with conn.cursor() as cur:
        cur.execute(SELECT_FOR_FINISH_SQL, (usage_id,))
        linha = cur.fetchone()
        if linha is None:
            logger.warning("ai_usage_log id=%s não existe; nada a fechar", usage_id)
            return False
        if not ai_usage.worker_may_close(linha.get("status"), linha.get("error_code"), presumed_lost_code=perdida):
            logger.warning(
                "ai_usage_log id=%s já fechado (status=%s, error_code=%s); relato do worker descartado",
                usage_id,
                linha.get("status"),
                linha.get("error_code"),
            )
            return False
        modelo = _clip("model", model) or _clip("model", linha.get("model"))
        entrada, saida = ai_usage.count_or_none(tokens_in), ai_usage.count_or_none(tokens_out)
        cur.execute(
            FINISH_SQL,
            (
                status.value,
                modelo,
                entrada,
                saida,
                ai_usage.estimate_cost_usd(modelo, entrada, saida, cached_tokens_in),
                _clip("error_code", error_code),
                usage_id,
                ai_usage.OPEN_STATUS.value,
                ai_usage.SWEEP_PRESUMED_STATUS.value,
                perdida,
            ),
        )
        return int(cur.rowcount or 0) > 0


__all__ = [
    "attach_job",
    "count_usage_today",
    "finish_usage",
    "link_job",
    "open_usage",
    "quota_exceeded_error",
    "reserve_usage",
    "stale_after_seconds",
    "sweep_stale_pending",
    "usage_today",
]
