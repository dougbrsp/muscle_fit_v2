"""Sync AUTOMATICO do Intervals.icu (melhoria M2).

Substitui os botoes "Atualizar"/"Sincronizar" da tela `/intervals-icu`: em vez
de depender de alguem clicar, um job agendado (EventBridge -> Lambda
`musclefit-intervals-sync`, `app/lambda_intervals_sync.py`) busca as
atividades e o wellness de cada usuario QUE TEM A API DO INTERVALS
CONFIGURADA, grava no cache (`intervals_activities`/`intervals_wellness`) e
associa a FC das atividades `WeightTraining` aos treinos realizados
(`activities`) pelo horario, gravando em `activity_intervals_link`
(migracao 041).

Garantias que o job precisa ter, e como cada uma e obtida:

1. **So quem configurou.** A lista de usuarios vem de
   `intervals_store.users_with_intervals_configured` - `users` ATIVOS com
   `intervals_athlete_id` E `intervals_api_key_encrypted` preenchidos. Sem
   varredura da base.

2. **Falha de um usuario nao derruba os outros.** Cada usuario roda em seu
   proprio `try/except` e na PROPRIA transacao: erro da API do Intervals
   (401/429/timeout), chave que nao descriptografa ou erro de banco daquele
   usuario viram uma entrada em `failures` + evento
   `intervals_sync.user_failed`, e o loop continua. O job so falha inteiro se
   nao conseguir nem LISTAR os usuarios.

3. **Idempotencia.** Os tres caminhos de escrita sao upsert por chave natural
   (`intervals_activities(user_id, external_id)`,
   `intervals_wellness(user_id, wellness_date)`,
   `activity_intervals_link(activity_id)`), e o casamento por horario tem
   desempate total e deterministico (ver `app/intervals_hr_match.py`). Rodar
   duas vezes sobre o mesmo dado escreve os MESMOS valores.

4. **Nunca sobrescrever com lixo.** Se o Intervals nao devolve nenhum
   candidato para um treino que ja tinha link, o link EXISTENTE e mantido -
   este job nunca apaga linha de `activity_intervals_link`. Ausencia de
   candidato e ausencia de informacao, nao informacao nova.

5. **Segredo.** A chave de API e descriptografada, usada na chamada HTTP e
   descartada. Nao aparece em log, em evento, no relatorio nem em excecao.

Observabilidade (secao 14 do plano de refatoracao, mesmo formato do PR #145):
eventos `intervals_sync.started`, `intervals_sync.user_done`,
`intervals_sync.user_failed` e `intervals_sync.finished`, via
`app/observability.log_event` - so id numerico de usuario, contagens e
`reason_code`.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional, Sequence

from . import intervals_client, intervals_hr_match, intervals_store
from .intervals_client import IntervalsClientError
from .intervals_store import IntervalsCredentialsError
from .observability import log_event

logger = logging.getLogger("musclefit.intervals_sync")

ConnFactory = Callable[[], Any]

EVENT_STARTED = "intervals_sync.started"
EVENT_USER_DONE = "intervals_sync.user_done"
EVENT_USER_FAILED = "intervals_sync.user_failed"
EVENT_FINISHED = "intervals_sync.finished"

# Janela default do sync agendado, em dias. Curta de proposito: o job roda
# todo dia, e reprocessar 30 dias a cada execucao ja cobre correcao
# retroativa (o usuario editar/renomear uma atividade antiga no Intervals)
# sem gastar quota da API deles com 3 anos de historico.
DEFAULT_SYNC_DAYS = 30
SYNC_DAYS_ENV = "INTERVALS_SYNC_DAYS"
MAX_SYNC_DAYS = 3650

# Margem de dias ao ler `intervals_activities` para o casamento: um treino que
# comeca 23:40 pode casar com uma atividade gravada no dia seguinte (ou o
# contrario). Um dia de folga de cada lado cobre qualquer janela de tolerancia
# razoavel.
LINK_DATE_MARGIN_DAYS = 1

# Mesmo filtro textual de "treino de forca" ja usado em `analytics.py` e em
# `intelligence/intervals_correlation_service.py` - nao existe enum fechado
# para `activities.activity_type`, so o default 'musculacao' e variantes.
_STRENGTH_ACTIVITY_TYPE_FILTER = """
    LOWER(a.activity_type) LIKE '%%muscul%%'
    OR LOWER(a.activity_type) LIKE '%%força%%'
    OR LOWER(a.activity_type) LIKE '%%forca%%'
    OR LOWER(a.activity_type) LIKE '%%strength%%'
"""

# Normaliza `sport_type` no SQL igual a `intervals_hr_match.normalize_sport_type`
# (minusculo, sem espaco/-/_), para nao trazer atividade de cardio a toa.
_WEIGHT_TRAINING_SQL_EXPR = (
    "LOWER(REPLACE(REPLACE(REPLACE(sport_type, ' ', ''), '-', ''), '_', ''))"
)

_UPSERT_LINK_SQL = """
    INSERT INTO activity_intervals_link (
        user_id,
        activity_id,
        intervals_activity_id,
        intervals_external_id,
        match_status,
        match_delta_seconds,
        candidates_count,
        moving_time_seconds,
        avg_hr_bpm,
        max_hr_bpm,
        calories_kcal,
        training_load
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        intervals_activity_id = VALUES(intervals_activity_id),
        intervals_external_id = VALUES(intervals_external_id),
        match_status = VALUES(match_status),
        match_delta_seconds = VALUES(match_delta_seconds),
        candidates_count = VALUES(candidates_count),
        moving_time_seconds = VALUES(moving_time_seconds),
        avg_hr_bpm = VALUES(avg_hr_bpm),
        max_hr_bpm = VALUES(max_hr_bpm),
        calories_kcal = VALUES(calories_kcal),
        training_load = VALUES(training_load)
"""


# Preenche na PROPRIA `activities` a FC que veio do Intervals - e so onde a
# coluna esta vazia (`COALESCE` mantem o que ja existe). Assim o numero
# aparece nas telas de treino que ja leem `activities.avg_hr_bpm`, sem nenhuma
# mudanca de frontend, e um valor informado a mao ou importado do relogio
# NUNCA e sobrescrito.
_FILL_ACTIVITY_HR_SQL = """
    UPDATE activities
    SET avg_hr_bpm = COALESCE(avg_hr_bpm, %s),
        max_hr_bpm = COALESCE(max_hr_bpm, %s),
        calories_kcal = COALESCE(calories_kcal, %s)
    WHERE id = %s AND user_id = %s
"""


class IntervalsSyncError(Exception):
    """Falha que impede o job inteiro (ex.: nao conseguir listar usuarios)."""


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------


@dataclass
class UserSyncResult:
    user_id: int
    activities_received: int = 0
    activities_upserted: int = 0
    wellness_received: int = 0
    wellness_upserted: int = 0
    hr_matched: int = 0
    hr_ambiguous: int = 0
    hr_unmatched: int = 0
    hr_no_start_time: int = 0
    activities_hr_filled: int = 0
    # M98 C2: zonas de FC do atleta (`GET /athlete/{id}`), uma chamada por usuário.
    athlete_zones_upserted: int = 0
    athlete_zones_failed: bool = False
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UserSyncFailure:
    user_id: int
    reason_code: str
    status_code: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SyncReport:
    days: int
    dry_run: bool
    oldest: str
    newest: str
    users_eligible: int = 0
    results: list[UserSyncResult] = field(default_factory=list)
    failures: list[UserSyncFailure] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def totals(self) -> dict[str, int]:
        return {
            "users_processed": len(self.results),
            "users_failed": len(self.failures),
            "activities_upserted": sum(item.activities_upserted for item in self.results),
            "wellness_upserted": sum(item.wellness_upserted for item in self.results),
            "hr_matched": sum(item.hr_matched for item in self.results),
            "hr_ambiguous": sum(item.hr_ambiguous for item in self.results),
            "hr_unmatched": sum(item.hr_unmatched for item in self.results),
            "hr_no_start_time": sum(item.hr_no_start_time for item in self.results),
            "activities_hr_filled": sum(item.activities_hr_filled for item in self.results),
            "athlete_zones_upserted": sum(item.athlete_zones_upserted for item in self.results),
            "athlete_zones_failed": sum(1 for item in self.results if item.athlete_zones_failed),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "days": self.days,
            "dry_run": self.dry_run,
            "range": {"oldest": self.oldest, "newest": self.newest},
            "users_eligible": self.users_eligible,
            "totals": self.totals,
            "users": [item.to_dict() for item in self.results],
            "failures": [item.to_dict() for item in self.failures],
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# Parametros
# ---------------------------------------------------------------------------


def sync_days(requested: Optional[int] = None) -> int:
    """Janela do sync. Valor invalido em `INTERVALS_SYNC_DAYS` nao quebra o
    job: cai no default."""
    if requested is not None:
        value = int(requested)
    else:
        try:
            value = int(str(os.environ.get(SYNC_DAYS_ENV, DEFAULT_SYNC_DAYS)))
        except (TypeError, ValueError):
            return DEFAULT_SYNC_DAYS
    if value < 1:
        return DEFAULT_SYNC_DAYS
    return min(value, MAX_SYNC_DAYS)


def _date_range(days: int) -> tuple[date, date]:
    newest = date.today()
    return newest - timedelta(days=days), newest


def _default_conn_factory():
    from .db import get_conn

    return get_conn()


# ---------------------------------------------------------------------------
# Associacao de FC com os treinos realizados
# ---------------------------------------------------------------------------


def _fetch_strength_sessions(conn, user_id: int, oldest: date, newest: date) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT a.id, a.started_at, a.avg_hr_bpm, a.max_hr_bpm, a.calories_kcal
            FROM activities a
            WHERE a.user_id = %s
              AND a.local_date BETWEEN %s AND %s
              AND ({_STRENGTH_ACTIVITY_TYPE_FILTER})
            ORDER BY a.id ASC
            """,
            (user_id, oldest.isoformat(), newest.isoformat()),
        )
        return list(cursor.fetchall())


def _fetch_weight_training(conn, user_id: int, oldest: date, newest: date) -> list[dict[str, Any]]:
    margin = timedelta(days=LINK_DATE_MARGIN_DAYS)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, external_id, start_time, sport_type, moving_time_seconds,
                   avg_hr_bpm, max_hr_bpm, calories_kcal, training_load
            FROM intervals_activities
            WHERE user_id = %s
              AND activity_date BETWEEN %s AND %s
              AND {_WEIGHT_TRAINING_SQL_EXPR} = %s
            ORDER BY start_time ASC, external_id ASC
            """,
            (
                user_id,
                (oldest - margin).isoformat(),
                (newest + margin).isoformat(),
                intervals_hr_match.WEIGHT_TRAINING_SPORT_TYPE,
            ),
        )
        return list(cursor.fetchall())


def _needs_hr_fill(session: dict[str, Any], candidate: intervals_hr_match.IntervalsCandidate) -> bool:
    """So vale um UPDATE se alguma coluna de FC/calorias do treino esta vazia
    E o Intervals tem o valor correspondente."""
    return any(
        session.get(column) is None and value is not None
        for column, value in (
            ("avg_hr_bpm", candidate.avg_hr_bpm),
            ("max_hr_bpm", candidate.max_hr_bpm),
            ("calories_kcal", candidate.calories_kcal),
        )
    )


def link_heart_rate(conn, user_id: int, oldest: date, newest: date, result: UserSyncResult) -> None:
    """Casa cada treino de forca do periodo com uma atividade `WeightTraining`
    e grava/atualiza `activity_intervals_link`. Nunca apaga link existente."""
    sessions = _fetch_strength_sessions(conn, user_id, oldest, newest)
    if not sessions:
        return

    candidates = intervals_hr_match.eligible_candidates(_fetch_weight_training(conn, user_id, oldest, newest))
    rows: list[tuple] = []
    fills: list[tuple[dict[str, Any], intervals_hr_match.IntervalsCandidate]] = []

    for session in sessions:
        match = intervals_hr_match.match_session(session.get("started_at"), candidates)

        if match.status == intervals_hr_match.STATUS_MATCHED:
            result.hr_matched += 1
        elif match.status == intervals_hr_match.STATUS_MATCHED_AMBIGUOUS:
            result.hr_ambiguous += 1
        elif match.status == intervals_hr_match.STATUS_NO_START_TIME:
            result.hr_no_start_time += 1
        else:
            result.hr_unmatched += 1

        if not match.persisted or match.candidate is None:
            continue

        candidate = match.candidate
        rows.append(
            (
                user_id,
                int(session["id"]),
                candidate.intervals_activity_id,
                candidate.external_id,
                match.status,
                match.delta_seconds,
                match.candidates_count,
                candidate.moving_time_seconds,
                candidate.avg_hr_bpm,
                candidate.max_hr_bpm,
                candidate.calories_kcal,
                candidate.training_load,
            )
        )

        if _needs_hr_fill(session, candidate):
            fills.append((session, candidate))

    if not rows:
        return

    with conn.cursor() as cursor:
        # COD-12: vinculos em lote, na ordem das sessoes.
        cursor.executemany(_UPSERT_LINK_SQL, rows)
        for session, candidate in fills:
            cursor.execute(
                _FILL_ACTIVITY_HR_SQL,
                (
                    candidate.avg_hr_bpm,
                    candidate.max_hr_bpm,
                    candidate.calories_kcal,
                    int(session["id"]),
                    user_id,
                ),
            )
            result.activities_hr_filled += 1


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


def _sync_one_user(conn, user_id: int, oldest: date, newest: date) -> UserSyncResult:
    result = UserSyncResult(user_id=user_id)
    started = time.monotonic()

    athlete_id, api_key = intervals_store.get_user_intervals_credentials(conn, user_id)
    athlete: Optional[dict[str, Any]] = None
    try:
        activities, wellness = intervals_client.fetch_activities_and_wellness(athlete_id, api_key, oldest, newest)
        # M98 C2: zonas de FC do atleta. Falha aqui NAO derruba o sync das
        # atividades: as zonas gravadas antes ficam (ausencia nao apaga dado).
        try:
            athlete = intervals_client.fetch_athlete(athlete_id, api_key)
        except Exception as exc:  # noqa: BLE001 - zonas sao acessorias: nada aqui derruba o usuario
            result.athlete_zones_failed = True
            logger.warning(
                "zonas do Intervals.icu indisponiveis para user_id=%s (%s)", user_id, _reason_code(exc)
            )
    finally:
        # A chave so existe pelo tempo da chamada HTTP.
        del api_key

    result.activities_received = len(activities)
    result.wellness_received = len(wellness)
    result.activities_upserted = intervals_store.upsert_activities(conn, user_id, activities)["upserted"]
    result.wellness_upserted = intervals_store.upsert_wellness(conn, user_id, wellness)["upserted"]
    if athlete is not None:
        result.athlete_zones_upserted = intervals_store.upsert_athlete_hr_zones(conn, user_id, athlete)["upserted"]

    link_heart_rate(conn, user_id, oldest, newest, result)

    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def sync_users(
    user_ids: Optional[Sequence[int]] = None,
    *,
    days: Optional[int] = None,
    dry_run: bool = False,
    conn_factory: Optional[ConnFactory] = None,
) -> SyncReport:
    """Roda o sync para todos os usuarios com Intervals.icu configurado (ou so
    para `user_ids`, quando informado - usado em troubleshooting).

    `dry_run=True` faz TODO o trabalho e da `rollback` no fim: util para medir
    o efeito de uma mudanca de janela sem escrever nada."""
    factory = conn_factory or _default_conn_factory
    window = sync_days(days)
    oldest, newest = _date_range(window)
    report = SyncReport(days=window, dry_run=dry_run, oldest=oldest.isoformat(), newest=newest.isoformat())
    job_started = time.monotonic()

    try:
        conn = factory()
        try:
            eligible = intervals_store.users_with_intervals_configured(
                conn, [int(value) for value in user_ids] if user_ids else None
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - sem a lista nao ha job
        raise IntervalsSyncError(f"Nao foi possivel listar os usuarios com Intervals.icu configurado: {exc}") from exc

    report.users_eligible = len(eligible)
    log_event(EVENT_STARTED, users_eligible=len(eligible), days=window, dry_run=dry_run)

    for user_id in eligible:
        conn = None
        try:
            conn = factory()
            result = _sync_one_user(conn, user_id, oldest, newest)
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
            report.results.append(result)
            log_event(
                EVENT_USER_DONE,
                user_id=user_id,
                activities_upserted=result.activities_upserted,
                wellness_upserted=result.wellness_upserted,
                hr_matched=result.hr_matched,
                hr_ambiguous=result.hr_ambiguous,
                hr_unmatched=result.hr_unmatched,
                hr_no_start_time=result.hr_no_start_time,
                duration_ms=result.duration_ms,
            )
        except Exception as exc:  # noqa: BLE001 - garantia 2: um usuario nao derruba o job
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    logger.warning("rollback falhou para user_id=%s", user_id)
            failure = UserSyncFailure(
                user_id=user_id,
                reason_code=_reason_code(exc),
                status_code=getattr(exc, "status_code", None),
            )
            report.failures.append(failure)
            # `exc_info` NAO e usado: a mensagem do IntervalsClientError pode
            # conter o corpo da resposta do Intervals. Só o reason_code vai pro log.
            logger.warning("sync do Intervals.icu falhou para user_id=%s (%s)", user_id, failure.reason_code)
            log_event(EVENT_USER_FAILED, user_id=user_id, reason_code=failure.reason_code, status_code=failure.status_code)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    report.duration_ms = int((time.monotonic() - job_started) * 1000)
    log_event(EVENT_FINISHED, days=window, dry_run=dry_run, duration_ms=report.duration_ms, **report.totals)
    return report


def _reason_code(exc: BaseException) -> str:
    """Codigo estavel para o log/metric filter. NUNCA a mensagem crua: ela
    pode carregar o corpo da resposta do Intervals.icu."""
    if isinstance(exc, IntervalsCredentialsError):
        return exc.reason_code
    if isinstance(exc, IntervalsClientError):
        if exc.status_code == 401:
            return "intervals_unauthorized"
        if exc.status_code == 429:
            return "intervals_rate_limited"
        if exc.status_code is None:
            return "intervals_network_error"
        return "intervals_http_error"
    return "database_error" if type(exc).__name__ == "DataApiError" else "unexpected_error"
