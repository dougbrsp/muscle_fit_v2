from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict

from . import intervals_client, intervals_store
from .db import get_conn
from .db_helpers import connect_or_503, DB_UNAVAILABLE_MYSQL, jsonable as _jsonable
from .intervals_client import IntervalsClientError
from .intervals_store import IntervalsCredentialsError, ManualWellnessError
from .security import Identity, get_current_identity, require_admin

router = APIRouter(prefix="/integrations/intervals", tags=["intervals"])

logger = logging.getLogger(__name__)


def _connect():
    return connect_or_503(get_conn, DB_UNAVAILABLE_MYSQL)


def _rows_jsonable(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{key: _jsonable(value) for key, value in row.items()} for row in rows]


def _fetch_all(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return _rows_jsonable(cursor.fetchall())


def _fetch_one(sql: str, params: Optional[tuple] = None) -> Dict[str, Any]:
    rows = _fetch_all(sql, params)
    return rows[0] if rows else {}


# Normalizacao de payload e upsert vivem em `app/intervals_store.py` desde a
# melhoria M2 (sync automatico): o job agendado precisa exatamente do MESMO
# codigo, sem FastAPI no caminho. Estes aliases mantem os call sites daqui
# (e os testes de paridade) inalterados.
_pick = intervals_store.pick
_to_float = intervals_store.to_float
_to_int = intervals_store.to_int
_to_date = intervals_store.to_date
_to_datetime = intervals_store.to_datetime
_raw_json = intervals_store.raw_json
_external_activity_id = intervals_store.external_activity_id
_derive_form_tsb = intervals_store.derive_form_tsb


def _extract_items(payload: Any, array_keys: List[str]) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in array_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        if payload:
            return [payload]

    return []


# ---------------------------------------------------------------------------
# Schema: intervals_activities/intervals_wellness (user_id NOT NULL, chaves
# compostas por usuario) vem da migracao versionada
# `api/db/migrations/027_integracoes_metricas_schema.py` (`python -m
# app.migrate`), sem DDL no caminho de requisicao.
#
# O upsert em si esta em `app/intervals_store.py` (compartilhado com o job
# agendado `app/intervals_sync.py`); aqui ficam so os wrappers que abrem a
# conexao e traduzem erro de negocio para HTTP.
# ---------------------------------------------------------------------------


def _upsert_activities(user_id: int, items: List[Dict[str, Any]]) -> Dict[str, int]:
    with _connect() as conn:
        return intervals_store.upsert_activities(conn, user_id, items)


def _upsert_wellness(user_id: int, items: List[Dict[str, Any]]) -> Dict[str, int]:
    with _connect() as conn:
        return intervals_store.upsert_wellness(conn, user_id, items)


def _get_user_intervals_credentials(user_id: int) -> tuple[str, str]:
    """Credenciais do Intervals.icu do usuario, com erro ja traduzido para
    HTTP. A chave nunca entra na resposta - nem aqui, nem no detail do erro."""
    try:
        with _connect() as conn:
            return intervals_store.get_user_intervals_credentials(conn, user_id)
    except IntervalsCredentialsError as exc:
        status_code = 400 if exc.reason_code == "not_configured" else 500
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


# SEG-12: 5xx (erro do Intervals.icu repassado ou falha de rede) responde com
# mensagem generica; o texto da excecao (corpo cru do upstream, motivo de rede)
# vai so para o log. 4xx (ex.: chave invalida) mantem a mensagem intencional.
INTERVALS_UPSTREAM_ERROR_DETAIL = "Não foi possível consultar o Intervals.icu agora. Tente novamente mais tarde."


def _intervals_client_http_error(exc: IntervalsClientError) -> HTTPException:
    status_code = exc.status_code or 502
    if status_code < 400:
        status_code = 502
    if status_code >= 500:
        logger.error("Falha ao consultar o Intervals.icu (HTTP %s)", status_code, exc_info=exc)
        return HTTPException(status_code=status_code, detail=INTERVALS_UPSTREAM_ERROR_DETAIL)
    if status_code == 401:
        # 401 do Intervals.icu = chave de API do usuário inválida, não sessão
        # do MuscleFit. Repassado como 401, o `apiFetch` do frontend tratava
        # como sessão expirada e deslogava o usuário (mesmo bug da troca de
        # senha, 2026-10-10). Vira 400 com a mesma mensagem.
        status_code = 400
    return HTTPException(status_code=status_code, detail=str(exc))


def _date_range(days: int) -> tuple[date, date]:
    newest = date.today()
    oldest = newest - timedelta(days=days)
    return oldest, newest


def _fetch_live_activities_and_wellness(user_id: int, days: int) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], tuple[date, date]]:
    athlete_id, api_key = _get_user_intervals_credentials(user_id)
    oldest, newest = _date_range(days)

    try:
        activities, wellness = intervals_client.fetch_activities_and_wellness(athlete_id, api_key, oldest, newest)
    except IntervalsClientError as exc:
        raise _intervals_client_http_error(exc) from exc

    return activities, wellness, (oldest, newest)


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@router.get("/health")
def intervals_integration_health(identity: Identity = Depends(get_current_identity)):
    return {
        "status": "ok",
        "namespace": "/integrations/intervals",
        "database": True,
        "tables": ["intervals_activities", "intervals_wellness"],
        "compatibility": {
            "existing_fitness_api_endpoints": "unchanged",
            "openclaw_mcp": "unchanged",
        },
    }


@router.get("/status")
def intervals_status(identity: Identity = Depends(get_current_identity)):
    """Diz se o usuario autenticado ja configurou o Intervals.icu no perfil,
    sem nunca vazar a chave (nem criptografada) na resposta."""
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT intervals_athlete_id, intervals_api_key_encrypted FROM users WHERE id = %s",
                (identity.user_id,),
            )
            row = cursor.fetchone()

    configured = bool((row or {}).get("intervals_athlete_id")) and bool(
        (row or {}).get("intervals_api_key_encrypted")
    )

    return {
        "configured": configured,
        "message": "Intervals.icu configurado."
        if configured
        else "Configure sua chave do Intervals.icu no seu perfil.",
    }


@router.post("/sync")
def intervals_sync(
    days: int = Query(default=90, ge=1, le=3650),
    identity: Identity = Depends(require_admin),
):
    """Sync MANUAL do proprio usuario - hoje so para troubleshooting (ADMIN).

    Desde a melhoria M2 o caminho normal e o job agendado (EventBridge ->
    `musclefit-intervals-sync`, ver `app/intervals_sync.py`), e o botao
    "Sincronizar" saiu da tela `/intervals-icu`. A rota sobreviveu, agora
    restrita a admin, por um motivo unico: quando um usuario reclama que a
    FC nao apareceu, e preciso reproduzir a chamada ao Intervals.icu e ver o
    erro real (401 de chave revogada, 429 de quota) sem esperar a proxima
    janela do agendador e sem acesso manual a AWS. Ela NAO associa FC a
    treino - isso e do job; aqui so o cache e atualizado.

    Se em algum momento o job agendado ganhar um caminho proprio de
    diagnostico, esta rota pode ser removida sem nada quebrar no frontend."""
    started = time.monotonic()
    activities, wellness, (oldest, newest) = _fetch_live_activities_and_wellness(identity.user_id, days)

    activities_result = _upsert_activities(identity.user_id, activities)
    wellness_result = _upsert_wellness(identity.user_id, wellness)
    athlete_zones = _sync_athlete_hr_zones(identity.user_id, elapsed_seconds=time.monotonic() - started)

    return {
        "status": "ok",
        "range": {"days": days, "oldest": oldest.isoformat(), "newest": newest.isoformat()},
        "intervals": {
            "activities_received": len(activities),
            "wellness_received": len(wellness),
        },
        "fitness_api": {
            "activities": activities_result,
            "wellness": wellness_result,
            "athlete_hr_zones": athlete_zones,
        },
    }


# Orcamento do sync manual (api-core atras do API Gateway, teto de 29 s): a
# chamada das zonas tem timeout curto e e pulada se o sync ja passou do
# orcamento (`skipped`). Operacional, nao e regra de dominio.
MANUAL_SYNC_ZONES_TIMEOUT_SECONDS = 5.0
MANUAL_SYNC_ZONES_BUDGET_SECONDS = 20.0


def _sync_athlete_hr_zones(user_id: int, *, elapsed_seconds: float = 0.0) -> Dict[str, Any]:
    """Zonas de FC do atleta (`GET /athlete/{id}`) no sync manual, com a MESMA
    tolerância do sync diário (`intervals_sync._sync_one_user`, M98 C2):
    qualquer falha só marca `failed` e não derruba o sync das atividades; as
    zonas gravadas antes ficam (ausência não apaga dado). M98 C3a."""
    if elapsed_seconds >= MANUAL_SYNC_ZONES_BUDGET_SECONDS:
        return {"upserted": 0, "failed": False, "skipped": True}
    try:
        athlete_id, api_key = _get_user_intervals_credentials(user_id)
        try:
            athlete = intervals_client.fetch_athlete(athlete_id, api_key, timeout=MANUAL_SYNC_ZONES_TIMEOUT_SECONDS)
        finally:
            del api_key
        with _connect() as conn:
            upserted = intervals_store.upsert_athlete_hr_zones(conn, user_id, athlete)["upserted"]
    except Exception as exc:  # noqa: BLE001 - zonas sao acessorias: nada aqui derruba o sync manual
        logger.warning(
            "zonas do Intervals.icu indisponiveis no sync manual para user_id=%s (%s)", user_id, type(exc).__name__
        )
        return {"upserted": 0, "failed": True}
    return {"upserted": upserted, "failed": False}


@router.post("/activities")
def upsert_intervals_activities(
    payload: Any = Body(...),
    identity: Identity = Depends(get_current_identity),
):
    items = _extract_items(payload, ["items", "activities", "data"])
    result = _upsert_activities(identity.user_id, items)
    return {"status": "ok", **result}


@router.post("/wellness")
def upsert_intervals_wellness(
    payload: Any = Body(...),
    identity: Identity = Depends(get_current_identity),
):
    items = _extract_items(payload, ["items", "wellness", "data"])
    result = _upsert_wellness(identity.user_id, items)
    return {"status": "ok", **result}


class ManualWellnessPatch(BaseModel):
    """So os campos ENVIADOS mudam; `null` explicito limpa a edicao. A faixa
    aceita e do dono (`intervals_store.MANUAL_WELLNESS_RANGES`), nunca do
    cliente."""

    model_config = ConfigDict(extra="forbid")

    resting_hr_bpm: Optional[float] = None
    hrv_ms: Optional[float] = None


def _user_today(conn, user_id: int) -> date:
    """"Hoje" do usuario (`time_windows.user_today_for`, dono). Fica o nome
    local: e o alvo do `monkeypatch` dos testes."""
    from .intelligence.dashboard.time_windows import user_today_for

    return user_today_for(conn, user_id)


@router.patch("/wellness/{wellness_date}")
def patch_intervals_wellness_manual(
    payload: ManualWellnessPatch,
    wellness_date: date = Path(...),
    identity: Identity = Depends(get_current_identity),
):
    """Edicao manual de FC de repouso / HRV de um dia do proprio usuario.

    Precedencia (dona: `intervals_store.upsert_wellness`): o proximo sync
    sobrescreve a edicao se o Intervals.icu tiver o dado daquele dia; senao a
    edicao fica."""
    sent = payload.model_dump(exclude_unset=True)
    if not sent:
        raise HTTPException(status_code=422, detail="Informe resting_hr_bpm e/ou hrv_ms.")
    try:
        values = {metric: intervals_store.validate_manual_wellness_value(metric, value) for metric, value in sent.items()}
    except ManualWellnessError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with _connect() as conn:
        if wellness_date > _user_today(conn, identity.user_id):
            raise HTTPException(status_code=422, detail="Não é possível editar um dia futuro.")
        row = intervals_store.set_manual_wellness(conn, identity.user_id, wellness_date.isoformat(), values)

    row = {key: _jsonable(value) for key, value in (row or {}).items()}
    return {
        "status": "ok",
        "item": {
            "wellness_date": row.get("wellness_date") or wellness_date.isoformat(),
            "resting_hr_bpm": _to_float(row.get("resting_hr_bpm")),
            "resting_hr_source": _wellness_source(row.get("resting_hr_source")),
            "hrv_ms": _to_float(row.get("hrv_ms")),
            "hrv_source": _wellness_source(row.get("hrv_source")),
            "manual_updated_at": row.get("manual_updated_at"),
        },
    }


@router.get("/summary")
def intervals_cached_summary(
    days: int = Query(default=90, ge=1, le=3650),
    identity: Identity = Depends(get_current_identity),
):
    activities = _fetch_one(
        """
        SELECT
            COUNT(*) AS activities,
            COALESCE(SUM(moving_time_seconds), 0) AS moving_time_seconds,
            COALESCE(SUM(calories_kcal), 0) AS calories_kcal,
            COALESCE(SUM(distance_meters), 0) AS distance_meters,
            ROUND(AVG(avg_hr_bpm), 2) AS avg_hr_bpm,
            MAX(max_hr_bpm) AS max_hr_bpm,
            ROUND(SUM(training_load), 2) AS training_load
        FROM intervals_activities
        WHERE user_id = %s AND activity_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
        """,
        (identity.user_id, days),
    )

    wellness = _fetch_one(
        """
        SELECT
            COUNT(*) AS wellness_days,
            ROUND(AVG(fitness_ctl), 2) AS avg_fitness_ctl,
            ROUND(AVG(fatigue_atl), 2) AS avg_fatigue_atl,
            ROUND(AVG(form_tsb), 2) AS avg_form_tsb,
            ROUND(AVG(resting_hr_bpm), 2) AS avg_resting_hr_bpm,
            ROUND(AVG(hrv_ms), 2) AS avg_hrv_ms
        FROM intervals_wellness
        WHERE user_id = %s AND wellness_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
        """,
        (identity.user_id, days),
    )

    latest_sync = _fetch_one(
        """
        SELECT MAX(synced_at) AS latest_synced_at
        FROM (
            SELECT synced_at FROM intervals_activities WHERE user_id = %s
            UNION ALL
            SELECT synced_at FROM intervals_wellness WHERE user_id = %s
        ) syncs
        """,
        (identity.user_id, identity.user_id),
    )

    return {
        "days": days,
        "activities": activities,
        "wellness": wellness,
        "latest_sync": latest_sync,
    }


@router.get("/activities")
def intervals_cached_activities(
    days: int = Query(default=90, ge=1, le=3650),
    limit: int = Query(default=100, ge=1, le=1000),
    identity: Identity = Depends(get_current_identity),
):
    rows = _fetch_all(
        """
        SELECT
            external_id,
            activity_date,
            start_time,
            name,
            sport_type,
            moving_time_seconds,
            elapsed_time_seconds,
            distance_meters,
            calories_kcal,
            avg_hr_bpm,
            max_hr_bpm,
            training_load,
            intensity,
            synced_at
        FROM intervals_activities
        WHERE user_id = %s AND activity_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
        ORDER BY activity_date DESC, start_time DESC
        LIMIT %s
        """,
        (identity.user_id, days, limit),
    )

    return {
        "days": days,
        "limit": limit,
        "items": rows,
    }


@router.get("/wellness")
def intervals_cached_wellness(
    days: int = Query(default=90, ge=1, le=3650),
    limit: int = Query(default=100, ge=1, le=1000),
    identity: Identity = Depends(get_current_identity),
):
    rows = _fetch_all(
        """
        SELECT
            wellness_date,
            fitness_ctl,
            fatigue_atl,
            form_tsb,
            ramp_rate,
            weight_kg,
            resting_hr_bpm,
            resting_hr_source,
            hrv_ms,
            hrv_source,
            manual_updated_at,
            sleep_seconds,
            readiness_score,
            daily_effort_load,
            synced_at
        FROM intervals_wellness
        WHERE user_id = %s AND wellness_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
        ORDER BY wellness_date DESC
        LIMIT %s
        """,
        (identity.user_id, days, limit),
    )

    return {
        "days": days,
        "limit": limit,
        "items": rows,
    }


# ---------------------------------------------------------------------------
# Dashboard "ao vivo" (Parte 2): porta a agregacao antes feita em
# fitness-frontend/src/app/api/intervals/dashboard. Chama o Intervals.icu
# diretamente (nao o cache MySQL) usando as credenciais do usuario
# autenticado. O `/cardio-dashboard` ao vivo saiu na M98 C4: o cardio e lido
# so do cache, por `intelligence/dashboard/cardio_service.py`
# (`GET /analytics/dashboard/cardio`).
# ---------------------------------------------------------------------------


def _pick_number(item: Dict[str, Any], names: List[str]) -> float:
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if parsed == parsed:  # not NaN
                return parsed
    return 0.0


def _pick_string(item: Dict[str, Any], names: List[str], fallback: str = "-") -> str:
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            return str(value)
    return fallback


def _pick_date(item: Dict[str, Any], names: List[str]) -> str:
    value = _pick_string(item, names, "")
    return value[:10] if value else ""


def _daily_activity(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_day: Dict[str, Dict[str, Any]] = {}

    for item in items:
        day = _pick_date(item, ["start_date_local", "startDateLocal", "start_date", "startDate", "date", "day"])
        if not day:
            continue

        bucket = by_day.setdefault(
            day,
            {"date": day, "activities": 0, "minutes": 0, "calories": 0.0, "distanceKm": 0.0, "load": 0.0, "avgHrSum": 0.0, "avgHrCount": 0},
        )

        seconds = _pick_number(item, ["moving_time", "movingTime", "moving_time_seconds", "duration", "duration_seconds"])
        avg_hr = _pick_number(item, ["average_heartrate", "avg_hr", "avgHr", "avg_heartrate", "averageHeartRate"])

        bucket["activities"] += 1
        bucket["minutes"] += round(seconds / 60)
        bucket["calories"] += _pick_number(item, ["calories", "calories_kcal", "caloriesKcal"])
        bucket["distanceKm"] += _pick_number(item, ["distance", "distance_meters", "distanceMeters"]) / 1000
        bucket["load"] += _pick_number(item, ["icu_training_load", "training_load", "load", "icuTrainingLoad"])

        if avg_hr > 0:
            bucket["avgHrSum"] += avg_hr
            bucket["avgHrCount"] += 1

    result = []
    for bucket in sorted(by_day.values(), key=lambda b: b["date"]):
        result.append(
            {
                "date": bucket["date"],
                "activities": bucket["activities"],
                "minutes": bucket["minutes"],
                "calories": round(bucket["calories"]),
                "distanceKm": round(bucket["distanceKm"], 2),
                "load": round(bucket["load"], 1),
                "avgHr": round(bucket["avgHrSum"] / bucket["avgHrCount"], 1) if bucket["avgHrCount"] else None,
            }
        )
    return result


def _recent_activities(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for item in items:
        activity_date = _pick_date(item, ["start_date_local", "startDateLocal", "start_date", "startDate", "date", "day"])
        if not activity_date:
            continue
        result.append(
            {
                "date": activity_date,
                "name": _pick_string(item, ["name", "title", "description"], "Atividade"),
                "sport": _pick_string(item, ["type", "sport", "sport_type", "sportType", "activity_type", "activityType"], "atividade"),
                "minutes": round(_pick_number(item, ["moving_time", "movingTime", "moving_time_seconds", "duration", "duration_seconds"]) / 60),
                "calories": round(_pick_number(item, ["calories", "calories_kcal", "caloriesKcal"])),
                "avgHr": _pick_number(item, ["average_heartrate", "avg_hr", "avgHr", "avg_heartrate", "averageHeartRate"]) or None,
                "load": _pick_number(item, ["icu_training_load", "training_load", "load", "icuTrainingLoad"]) or None,
            }
        )
    return sorted(result, key=lambda r: r["date"], reverse=True)[:30]


# ---------------------------------------------------------------------------
# Wellness do /dashboard: FONTE UNICA = cache `intervals_wellness`
#
# A tabela "Fitness, fadiga e forma" (/intervals-icu) e o "Historico (90
# dias)" (/indicadores-fisicos, via GET /wellness) mostravam HRV de origens
# diferentes: uma ao vivo, outra do cache diario. Agora as duas leem o cache.
# Para o cache nao ficar ate 24h atrasado, abrir o /dashboard faz um refresh
# CURTO do wellness do proprio usuario (ultimos dias), com o MESMO upsert do
# job agendado (`intervals_store.upsert_wellness`) - sem mapeamento duplicado.
#
# O refresh e melhor-esforco: sem credencial, Intervals.icu fora do ar ou
# erro de banco no caminho de escrita, a tela continua servindo o cache e o
# motivo fica no log. Throttle por `synced_at` evita martelar a API a cada
# render/troca de janela.
# ---------------------------------------------------------------------------

WELLNESS_REFRESH_MAX_DAYS = 7
WELLNESS_REFRESH_THROTTLE_MINUTES = 5


def _wellness_recently_synced(conn, user_id: int) -> bool:
    """True se o usuario teve wellness gravado ha menos de
    `WELLNESS_REFRESH_THROTTLE_MINUTES`. A comparacao fica no SQL (NOW() vs
    `synced_at`, mesmo relogio do banco) para nao depender de fuso da Lambda."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS recent
            FROM intervals_wellness
            WHERE user_id = %s AND synced_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
            """,
            (user_id, WELLNESS_REFRESH_THROTTLE_MINUTES),
        )
        row = cursor.fetchone() or {}
    return int(row.get("recent") or 0) > 0


def _refresh_wellness_cache(user_id: int, days: int) -> str:
    """Atualiza `intervals_wellness` com os ultimos dias do Intervals.icu.

    Nunca levanta: devolve o desfecho (`refreshed` | `throttled` |
    `not_configured` | `failed`) para a resposta e o log. A chave do
    Intervals.icu nunca e logada."""
    window = max(1, min(int(days), WELLNESS_REFRESH_MAX_DAYS))
    try:
        with get_conn() as conn:
            if _wellness_recently_synced(conn, user_id):
                return "throttled"
            athlete_id, api_key = intervals_store.get_user_intervals_credentials(conn, user_id)
    except IntervalsCredentialsError as exc:
        logger.info(
            "intervals wellness refresh pulado: sem credencial utilizavel",
            extra={"event": "intervals_wellness_refresh_skipped", "user_id": user_id, "reason": exc.reason_code},
        )
        return "not_configured"
    except Exception as exc:  # noqa: BLE001 - melhor-esforco: a tela serve o cache
        logger.warning(
            "intervals wellness refresh falhou ao preparar (servindo cache): %s",
            type(exc).__name__,
            extra={"event": "intervals_wellness_refresh_failed", "user_id": user_id, "stage": "prepare"},
        )
        return "failed"

    oldest, newest = _date_range(window)
    try:
        try:
            wellness = intervals_client.fetch_wellness(athlete_id, api_key, oldest, newest)
        finally:
            # A chave so existe pelo tempo da chamada HTTP (mesma regra do job).
            del api_key
        with get_conn() as conn:
            result = intervals_store.upsert_wellness(conn, user_id, wellness)
    except IntervalsClientError as exc:
        logger.warning(
            "intervals wellness refresh falhou no Intervals.icu (servindo cache): %s",
            exc,
            extra={
                "event": "intervals_wellness_refresh_failed",
                "user_id": user_id,
                "stage": "fetch",
                "status_code": exc.status_code,
            },
        )
        return "failed"
    except Exception as exc:  # noqa: BLE001 - melhor-esforco: a tela serve o cache
        logger.warning(
            "intervals wellness refresh falhou ao gravar (servindo cache): %s",
            type(exc).__name__,
            extra={"event": "intervals_wellness_refresh_failed", "user_id": user_id, "stage": "upsert"},
        )
        return "failed"

    logger.info(
        "intervals wellness refresh ok",
        extra={"event": "intervals_wellness_refreshed", "user_id": user_id, "days": window, **result},
    )
    return "refreshed"


def _wellness_source(value: Any) -> str:
    """Origem de FC de repouso / HRV (069). Ausente = 'intervals' (o default
    da coluna)."""
    text = value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else value
    return intervals_store.SOURCE_MANUAL if text == intervals_store.SOURCE_MANUAL else intervals_store.SOURCE_INTERVALS


def _cached_wellness_trend(user_id: int, days: int) -> List[Dict[str, Any]]:
    """Tendencia de wellness lida do cache. Ausente = `None` (a
    tela mostra "-"), nunca 0 - mesma semantica do GET /wellness."""
    rows = _fetch_all(
        """
        SELECT
            wellness_date,
            fitness_ctl,
            fatigue_atl,
            form_tsb,
            resting_hr_bpm,
            resting_hr_source,
            hrv_ms,
            hrv_source,
            ramp_rate
        FROM intervals_wellness
        WHERE user_id = %s AND wellness_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
        ORDER BY wellness_date ASC
        """,
        (user_id, days),
    )

    result = []
    for row in rows:
        wellness_date = _to_date(row.get("wellness_date"))
        if not wellness_date:
            continue
        fitness = _to_float(row.get("fitness_ctl"))
        fatigue = _to_float(row.get("fatigue_atl"))
        result.append(
            {
                "date": wellness_date,
                "fitness": fitness,
                "fatigue": fatigue,
                "form": _derive_form_tsb(fitness, fatigue, _to_float(row.get("form_tsb"))),
                "restingHr": _to_float(row.get("resting_hr_bpm")),
                "restingHrSource": _wellness_source(row.get("resting_hr_source")),
                "hrv": _to_float(row.get("hrv_ms")),
                "hrvSource": _wellness_source(row.get("hrv_source")),
                "rampRate": _to_float(row.get("ramp_rate")),
            }
        )
    return sorted(result, key=lambda r: r["date"])


# ---------------------------------------------------------------------------
# M53: atividades buscadas ao vivo pelo /dashboard tambem vao para o cache
#
# Os indicadores (recuperacao, /dashboard/cardio, FC dos treinos) leem so
# `intervals_activities`, ate aqui alimentada apenas pela Lambda diaria - que
# ficou quebrada de 03/09 a 01/10/2026 sem ninguem notar. Agora, abrir
# `/intervals-icu` grava as atividades que a tela ja tinha buscado, com o
# MESMO upsert do job (`intervals_store.upsert_activities`) e a MESMA
# associacao de FC com os treinos realizados (`intervals_sync.link_heart_rate`,
# que o job roda logo depois do upsert, na mesma transacao).
#
# Custo controlado (cada statement e um round-trip na Data API):
# - throttle de `ACTIVITIES_CACHE_THROTTLE_MINUTES` por `synced_at`, como o
#   refresh do wellness;
# - grava so o que FALTA no cache, mais as atividades dos ultimos
#   `ACTIVITIES_CACHE_REWRITE_DAYS` dias (as unicas que ainda costumam mudar:
#   FC processada depois, nome editado). Com o job saudavel isso e ~1
#   semana de atividades; depois de uma pane do job, a primeira abertura
#   repoe o que faltou e as seguintes voltam ao regime normal;
# - nada mais antigo que `ACTIVITIES_CACHE_MAX_DAYS` (a janela da tela vai a
#   3650 dias; reescrever anos de historico numa abertura de pagina nao).
#
# Melhor-esforco, como o wellness: falha de escrita vira log e a tela segue.
# ---------------------------------------------------------------------------

ACTIVITIES_CACHE_MAX_DAYS = 90
ACTIVITIES_CACHE_REWRITE_DAYS = 7
ACTIVITIES_CACHE_THROTTLE_MINUTES = WELLNESS_REFRESH_THROTTLE_MINUTES


def _activities_recently_synced(conn, user_id: int) -> bool:
    """Mesma regra de `_wellness_recently_synced`, em `intervals_activities`."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS recent
            FROM intervals_activities
            WHERE user_id = %s AND synced_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
            """,
            (user_id, ACTIVITIES_CACHE_THROTTLE_MINUTES),
        )
        row = cursor.fetchone() or {}
    return int(row.get("recent") or 0) > 0


def _cached_activity_ids(conn, user_id: int, oldest: date, newest: date) -> set[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT external_id
            FROM intervals_activities
            WHERE user_id = %s AND activity_date BETWEEN %s AND %s
            """,
            (user_id, oldest.isoformat(), newest.isoformat()),
        )
        rows = cursor.fetchall() or []
    return {str(row["external_id"]) for row in rows}


def _activities_to_store(
    activities: List[Dict[str, Any]], cached_ids: set[str], today: date
) -> List[Dict[str, Any]]:
    """Recorte do que vale gravar (ver bloco acima). Data e id saem dos
    MESMOS helpers do upsert - o filtro nunca discorda do que sera gravado."""
    max_oldest = (today - timedelta(days=ACTIVITIES_CACHE_MAX_DAYS)).isoformat()
    rewrite_oldest = (today - timedelta(days=ACTIVITIES_CACHE_REWRITE_DAYS)).isoformat()
    selected = []
    for item in activities:
        activity_date = intervals_store.activity_date_of(item)
        if not activity_date or activity_date < max_oldest:
            continue
        if activity_date >= rewrite_oldest or _external_activity_id(item) not in cached_ids:
            selected.append(item)
    return selected


def _store_live_activities(user_id: int, activities: List[Dict[str, Any]], today: date) -> str:
    """Grava no cache as atividades buscadas ao vivo e associa a FC aos
    treinos do periodo gravado. Nunca levanta: devolve o desfecho
    (`stored` | `unchanged` | `throttled` | `failed`) para a resposta e o
    log. Chamado so DEPOIS da busca ao vivo ter dado certo - ou seja, so para
    quem tem o Intervals.icu configurado."""
    from .intervals_sync import UserSyncResult, link_heart_rate

    if not activities:
        return "unchanged"

    stage = "prepare"
    try:
        with get_conn() as conn:
            if _activities_recently_synced(conn, user_id):
                return "throttled"
            cached_ids = _cached_activity_ids(
                conn, user_id, today - timedelta(days=ACTIVITIES_CACHE_MAX_DAYS), today
            )

        selected = _activities_to_store(activities, cached_ids, today)
        if not selected:
            return "unchanged"

        stage = "upsert"
        dates = sorted(intervals_store.activity_date_of(item) for item in selected)
        link = UserSyncResult(user_id=user_id)
        # Uma transacao, como no job: upsert + associacao entram juntos ou
        # nenhum entra.
        with get_conn() as conn:
            result = intervals_store.upsert_activities(conn, user_id, selected)
            link_heart_rate(conn, user_id, date.fromisoformat(dates[0]), date.fromisoformat(dates[-1]), link)
    except Exception as exc:  # noqa: BLE001 - melhor-esforco: a tela segue com o dado ao vivo
        logger.warning(
            "intervals activities cache falhou (tela segue com o dado ao vivo): %s",
            type(exc).__name__,
            extra={"event": "intervals_activities_cache_failed", "user_id": user_id, "stage": stage},
        )
        return "failed"

    logger.info(
        "intervals activities cache ok",
        extra={
            "event": "intervals_activities_cached",
            "user_id": user_id,
            "hr_matched": link.hr_matched,
            "hr_ambiguous": link.hr_ambiguous,
            "activities_hr_filled": link.activities_hr_filled,
            **result,
        },
    )
    return "stored"


def _fetch_live_activities(user_id: int, days: int) -> tuple[List[Dict[str, Any]], tuple[date, date]]:
    athlete_id, api_key = _get_user_intervals_credentials(user_id)
    oldest, newest = _date_range(days)

    try:
        activities = intervals_client.fetch_activities(athlete_id, api_key, oldest, newest)
    except IntervalsClientError as exc:
        raise _intervals_client_http_error(exc) from exc

    return activities, (oldest, newest)


@router.get("/dashboard")
def intervals_live_dashboard(
    days: int = Query(default=90, ge=1, le=3650),
    identity: Identity = Depends(get_current_identity),
):
    """Atividades ao vivo do Intervals.icu (como antes), agora tambem
    gravadas no cache `intervals_activities` (M53, melhor-esforco); wellness
    do cache `intervals_wellness` (fonte unica com GET /wellness), apos um
    refresh curto e throttled do proprio usuario."""
    wellness_refresh = _refresh_wellness_cache(identity.user_id, days)
    activities, (oldest, newest) = _fetch_live_activities(identity.user_id, days)
    activities_cache = _store_live_activities(identity.user_id, activities, newest)
    wellness_trend = _cached_wellness_trend(identity.user_id, days)

    return {
        "range": {"days": days, "oldest": oldest.isoformat(), "newest": newest.isoformat()},
        "activity_count": len(activities),
        "wellness_count": len(wellness_trend),
        "wellness_source": "cache",
        "wellness_refresh": wellness_refresh,
        "activities_cache": activities_cache,
        "daily_activity": _daily_activity(activities),
        "wellness_trend": wellness_trend,
        "recent_activities": _recent_activities(activities),
    }


@router.get("/cardio-adherence")
def intervals_cardio_adherence(
    window: str = Query(default="28d"),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    """Cardio planejado × realizado do usuário logado (M98 C2), por sessão e
    por semana. Só leitura do cache (`intervals_activities`, mantido pelo
    sync); nenhuma chamada ao Intervals. A janela (`7d`/`28d`/`90d`/`180d` ou
    `custom` com `date_from`/`date_to`) é resolvida no fuso do usuário. Regras:
    `domain/cardio_adherence` (métricas e status), `intervals_hr_match`
    (casamento), `intervals_activity_kind` (o que é cardio), `hr_zones`
    (tempo em zona). O dashboard visual é da C4."""
    from . import cardio_adherence_service
    from .intelligence.dashboard.time_windows import resolve_window, user_timezone, user_today

    with _connect() as conn:
        timezone_name = user_timezone(conn, identity.user_id)
        local_today = user_today(timezone_name)
        try:
            resolved = resolve_window(
                window,
                today=local_today,
                date_from=date_from,
                date_to=date_to,
                timezone_name=timezone_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with conn.cursor() as cursor:
            payload = cardio_adherence_service.build_adherence(
                cursor, identity.user_id, resolved.date_from, resolved.date_to, today=local_today
            )
    return {**payload, "window": resolved.kind.value, "timezone": timezone_name}
