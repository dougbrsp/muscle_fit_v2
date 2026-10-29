"""Persistencia do cache do Intervals.icu (`intervals_activities` /
`intervals_wellness`), sem FastAPI e sem HTTP.

Este modulo nasceu da melhoria M2 (sync automatico agendado): ate aqui o
upsert vivia dentro de `app/intervals_integration.py`, um router FastAPI, e
abria a propria conexao. O job agendado (`app/intervals_sync.py`) precisa do
MESMO upsert - byte a byte, para nao existir "o jeito do job" e "o jeito da
API" - rodando dentro de UMA transacao por usuario, numa Lambda sem request
HTTP nenhum.

Entao aqui as funcoes:
  - recebem a conexao pronta (`conn`), nunca abrem nem fecham nada;
  - nunca levantam `HTTPException` (quem traduz erro para HTTP e o router);
  - nao sabem quem e o usuario autenticado - `user_id` chega por parametro.

`app/intervals_integration.py` reexporta o que usava, entao os call sites
existentes (incluindo os testes de paridade) continuam iguais.

SEGREDO: `get_user_intervals_credentials` devolve a chave ja descriptografada
para ser usada IMEDIATAMENTE na chamada HTTP. Ela nunca e logada, nunca entra
em dict de retorno de rota e nunca e persistida em texto claro.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .crypto_utils import decrypt_secret
from .domain import hr_zones


class IntervalsCredentialsError(Exception):
    """Usuario sem Intervals.icu configurado, ou com chave que nao
    descriptografa. A mensagem NUNCA contem a chave."""

    def __init__(self, message: str, reason_code: str):
        super().__init__(message)
        self.reason_code = reason_code


# ---------------------------------------------------------------------------
# Normalizacao de payload (shape do Intervals.icu -> colunas do cache)
# ---------------------------------------------------------------------------


def pick(source: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in source and source[name] not in (None, ""):
            return source[name]
    return None


# HRV no cache e na tela = rMSSD do Intervals.icu (`hrv`). `hrvSDNN` NAO e
# fallback: e outra metrica, com escala propria, e mistura-las gerava dias com
# "HRV" ~40% abaixo do real quando o rMSSD ainda nao tinha chegado.
HRV_RMSSD_KEYS: tuple[str, ...] = ("hrv", "hrv_ms")


def to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def to_date(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if len(text) >= 10:
        text = text[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except Exception:
        return None


def to_datetime(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    text = str(value).strip().replace("Z", "")
    if "T" in text:
        text = text.replace("T", " ")
    if len(text) >= 19:
        text = text[:19]
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def raw_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def external_activity_id(item: Dict[str, Any]) -> str:
    candidate = pick(
        item,
        "id",
        "activity_id",
        "activityId",
        "file_id",
        "fileId",
        "filename",
        "fit_file",
        "icu_activity_id",
    )
    if candidate not in (None, ""):
        return str(candidate)
    return hashlib.sha256(raw_json(item).encode("utf-8")).hexdigest()


# Chaves de data de uma atividade, na ordem de preferencia. Fonte UNICA: o
# upsert e o filtro do cache ao vivo (M53, `intervals_integration`) leem daqui.
ACTIVITY_DATE_KEYS: tuple[str, ...] = (
    "start_date_local",
    "startDateLocal",
    "start_date",
    "startDate",
    "start_time",
    "startTime",
    "date",
    "day",
)


def activity_date_of(item: Dict[str, Any]) -> Optional[str]:
    """Data (ISO) da atividade como o upsert a grava em `activity_date`;
    `None` = o upsert pula o item."""
    return to_date(pick(item, *ACTIVITY_DATE_KEYS))


def derive_form_tsb(
    fitness_ctl: Optional[float], fatigue_atl: Optional[float], form_tsb: Optional[float]
) -> Optional[float]:
    """Intervals.icu nao retorna "Estado Fisico"/TSB como campo pronto na
    maioria dos payloads; e derivado da formula padrao da industria:
    TSB = CTL - ATL."""
    if form_tsb is not None:
        return form_tsb
    if fitness_ctl is not None and fatigue_atl is not None:
        return fitness_ctl - fatigue_atl
    return None


# ---------------------------------------------------------------------------
# Upserts
#
# Schema: `api/db/migrations/027_integracoes_metricas_schema.py` (chaves
# compostas por usuario). Nenhum DDL no caminho de requisicao nem no job.
# ---------------------------------------------------------------------------

_UPSERT_ACTIVITIES_SQL = """
    INSERT INTO intervals_activities (
        user_id,
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
        raw_json,
        hr_zone_seconds
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), CAST(%s AS JSON))
    ON DUPLICATE KEY UPDATE
        activity_date = VALUES(activity_date),
        start_time = VALUES(start_time),
        name = VALUES(name),
        sport_type = VALUES(sport_type),
        moving_time_seconds = VALUES(moving_time_seconds),
        elapsed_time_seconds = VALUES(elapsed_time_seconds),
        distance_meters = VALUES(distance_meters),
        calories_kcal = VALUES(calories_kcal),
        avg_hr_bpm = VALUES(avg_hr_bpm),
        max_hr_bpm = VALUES(max_hr_bpm),
        training_load = VALUES(training_load),
        intensity = VALUES(intensity),
        raw_json = VALUES(raw_json),
        hr_zone_seconds = COALESCE(VALUES(hr_zone_seconds), hr_zone_seconds),
        synced_at = CURRENT_TIMESTAMP
"""

# Campo do tempo em zona de FC no payload da atividade (M98 C2, migracao 097).
ZONE_TIMES_KEYS = ("icu_hr_zone_times",)


def hr_zone_seconds_json(item: Dict[str, Any]) -> Optional[str]:
    """`icu_hr_zone_times` validado pelo dono (`hr_zones`) e serializado
    para a coluna `hr_zone_seconds`; ausente/invalido -> None (o upsert mantem
    o valor gravado: ausencia nao apaga dado)."""
    seconds = hr_zones.parse_intervals_zone_seconds(pick(item, *ZONE_TIMES_KEYS))
    return None if seconds is None else json.dumps(seconds, separators=(",", ":"))

# ---------------------------------------------------------------------------
# Edicao manual de FC de repouso / HRV (precedencia sobre o sync)
#
# REGRA (pedido do usuario, 2026-10-08): o usuario pode digitar a FC de
# repouso e o HRV de um dia. Quando o Intervals.icu enviar o dado DAQUELE dia
# (valor nao nulo), ele sobrescreve a edicao e a origem volta a 'intervals';
# enquanto o Intervals nao tiver o dado (valor nulo), a edicao fica.
#
# Esta e a UNICA implementacao da regra: o job diario, o refresh do
# /dashboard, POST /sync e POST /wellness passam todos por `upsert_wellness`.
# Consumidores (recuperacao, relatorios, correlacao, cardio) continuam lendo
# `resting_hr_bpm`/`hrv_ms` como sempre - a origem nao muda nenhum calculo.
# ---------------------------------------------------------------------------

SOURCE_INTERVALS = "intervals"
SOURCE_MANUAL = "manual"

# metrica editavel -> coluna de origem (migracao 069)
MANUAL_WELLNESS_SOURCE_COLUMNS: Dict[str, str] = {
    "resting_hr_bpm": "resting_hr_source",
    "hrv_ms": "hrv_source",
}

# Faixas aceitas na edicao manual (validacao de ENTRADA da integracao, nao
# regra de motor): fora delas e erro de digitacao, a API responde 422.
MANUAL_RESTING_HR_BPM_MIN = 20.0
MANUAL_RESTING_HR_BPM_MAX = 250.0
MANUAL_HRV_MS_MIN = 1.0
MANUAL_HRV_MS_MAX = 500.0

MANUAL_WELLNESS_RANGES: Dict[str, tuple[float, float]] = {
    "resting_hr_bpm": (MANUAL_RESTING_HR_BPM_MIN, MANUAL_RESTING_HR_BPM_MAX),
    "hrv_ms": (MANUAL_HRV_MS_MIN, MANUAL_HRV_MS_MAX),
}


def _sync_precedence_assignments(value_column: str, source_column: str) -> str:
    """`ON DUPLICATE KEY UPDATE` de uma metrica editavel.

    - novo valor nao nulo                 -> grava o valor, origem 'intervals';
    - novo valor nulo e origem 'manual'   -> mantem valor e origem;
    - novo valor nulo e origem 'intervals'-> grava o NULL (como antes da 069).

    ARMADILHA: o MySQL avalia as atribuicoes da esquerda para a direita e as
    seguintes ja enxergam a coluna atualizada. As duas expressoes foram
    escritas para NAO depender da ordem:
    - a da origem so le `VALUES(valor)` e a propria origem (nunca o valor
      atual da linha);
    - a do valor so preserva o atual quando `VALUES(valor)` e nulo - e nesse
      caso a expressao da origem nao muda a origem.
    Trocar a ordem das duas atribuicoes da o mesmo resultado (o teste de
    integracao `test_intervals_wellness_edicao_manual.py` roda as duas ordens
    no MySQL)."""
    return (
        f"{value_column} = IF(VALUES({value_column}) IS NULL AND {source_column} = '{SOURCE_MANUAL}', "
        f"{value_column}, VALUES({value_column})), "
        f"{source_column} = IF(VALUES({value_column}) IS NULL, {source_column}, '{SOURCE_INTERVALS}')"
    )


_UPSERT_WELLNESS_SQL = """
    INSERT INTO intervals_wellness (
        wellness_date,
        user_id,
        fitness_ctl,
        fatigue_atl,
        form_tsb,
        ramp_rate,
        weight_kg,
        resting_hr_bpm,
        hrv_ms,
        sleep_seconds,
        readiness_score,
        daily_effort_load,
        raw_json
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON))
    ON DUPLICATE KEY UPDATE
        fitness_ctl = VALUES(fitness_ctl),
        fatigue_atl = VALUES(fatigue_atl),
        form_tsb = VALUES(form_tsb),
        ramp_rate = VALUES(ramp_rate),
        weight_kg = VALUES(weight_kg),
        """ + _sync_precedence_assignments("resting_hr_bpm", "resting_hr_source") + """,
        """ + _sync_precedence_assignments("hrv_ms", "hrv_source") + """,
        sleep_seconds = VALUES(sleep_seconds),
        readiness_score = VALUES(readiness_score),
        daily_effort_load = VALUES(daily_effort_load),
        raw_json = VALUES(raw_json),
        synced_at = CURRENT_TIMESTAMP
"""


def upsert_activities(conn, user_id: int, items: List[Dict[str, Any]]) -> Dict[str, int]:
    """Idempotente pela UNIQUE (user_id, external_id): reexecutar com o mesmo
    payload reescreve os MESMOS valores, nunca duplica linha."""
    upserted = 0
    skipped = 0

    rows: list[tuple[Any, ...]] = []
    with conn.cursor() as cursor:
        for item in items:
            activity_date = activity_date_of(item)
            if not activity_date:
                skipped += 1
                continue

            rows.append(
                (
                    user_id,
                    external_activity_id(item),
                    activity_date,
                    to_datetime(
                        pick(item, "start_date_local", "startDateLocal", "start_date", "startDate", "start_time", "startTime")
                    ),
                    str(pick(item, "name", "title", "description") or "Intervals activity"),
                    str(pick(item, "type", "sport", "sport_type", "sportType", "activity_type", "activityType") or "activity"),
                    to_int(pick(item, "moving_time", "movingTime", "moving_time_seconds", "duration", "duration_seconds")),
                    to_int(pick(item, "elapsed_time", "elapsedTime", "elapsed_time_seconds")),
                    to_float(pick(item, "distance", "distance_meters", "distanceMeters")),
                    to_float(pick(item, "calories", "calories_kcal", "caloriesKcal")),
                    to_float(pick(item, "average_heartrate", "avg_hr", "avgHr", "avg_heartrate", "averageHeartRate")),
                    to_float(pick(item, "max_heartrate", "max_hr", "maxHr", "maxHeartRate")),
                    to_float(pick(item, "icu_training_load", "training_load", "load", "icuTrainingLoad")),
                    to_float(pick(item, "intensity", "icu_intensity", "icuIntensity")),
                    raw_json(item),
                    hr_zone_seconds_json(item),
                ),
            )
            upserted += 1

        # COD-12: um lote (BatchExecuteStatement na Data API), na ordem dos
        # itens e na transacao do chamador - mesmo efeito de N execute().
        cursor.executemany(_UPSERT_ACTIVITIES_SQL, rows)

    return {"received": len(items), "upserted": upserted, "skipped": skipped}


def upsert_wellness(conn, user_id: int, items: List[Dict[str, Any]]) -> Dict[str, int]:
    """Idempotente pela PK (user_id, wellness_date).

    FC de repouso e HRV editados a mao (origem 'manual') so sao sobrescritos
    quando o item traz o valor daquele dia - ver `_sync_precedence_assignments`."""
    upserted = 0
    skipped = 0

    rows: list[tuple[Any, ...]] = []
    with conn.cursor() as cursor:
        for item in items:
            wellness_date = to_date(pick(item, "id", "date", "day", "local_date", "wellness_date"))
            if not wellness_date:
                skipped += 1
                continue

            fitness_ctl = to_float(pick(item, "ctl", "fitness", "fitness_ctl"))
            fatigue_atl = to_float(pick(item, "atl", "fatigue", "fatigue_atl"))

            rows.append(
                (
                    wellness_date,
                    user_id,
                    fitness_ctl,
                    fatigue_atl,
                    derive_form_tsb(fitness_ctl, fatigue_atl, to_float(pick(item, "tsb", "form", "form_tsb"))),
                    to_float(pick(item, "rampRate", "ramp_rate", "ramp")),
                    to_float(pick(item, "weight", "weight_kg", "weightKg")),
                    to_float(pick(item, "restingHR", "resting_hr", "resting_hr_bpm", "restingHeartRate")),
                    # HRV = SOMENTE rMSSD. `hrvSDNN` e outra metrica (sistematicamente
                    # mais baixa); sem rMSSD o dia fica nulo, nunca SDNN no lugar.
                    to_float(pick(item, *HRV_RMSSD_KEYS)),
                    to_int(pick(item, "sleepSecs", "sleep", "sleep_seconds", "sleep_time", "sleepTime")),
                    to_float(pick(item, "readiness", "readiness_score", "readinessScore")),
                    to_int(pick(item, "ctlLoad", "atlLoad", "daily_effort_load")),
                    raw_json(item),
                ),
            )
            upserted += 1

        # COD-12: um lote (BatchExecuteStatement na Data API), na ordem dos
        # itens e na transacao do chamador - mesmo efeito de N execute().
        cursor.executemany(_UPSERT_WELLNESS_SQL, rows)

    return {"received": len(items), "upserted": upserted, "skipped": skipped}


class ManualWellnessError(ValueError):
    """Valor de edicao manual invalido (fora da faixa, metrica desconhecida).
    O router traduz para 422."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def validate_manual_wellness_value(metric: str, value: Any) -> Optional[float]:
    """`None` (limpar a edicao) passa; numero fora de `MANUAL_WELLNESS_RANGES`
    levanta `ManualWellnessError`."""
    if metric not in MANUAL_WELLNESS_RANGES:
        raise ManualWellnessError(metric, f"{metric} nao e editavel")
    if value is None:
        return None
    if isinstance(value, bool):
        raise ManualWellnessError(metric, f"{metric} deve ser numerico")
    number = to_float(value)
    if number is None or not math.isfinite(number):
        raise ManualWellnessError(metric, f"{metric} deve ser numerico")
    low, high = MANUAL_WELLNESS_RANGES[metric]
    if number < low or number > high:
        raise ManualWellnessError(metric, f"{metric} deve estar entre {low:g} e {high:g}")
    return number


_MANUAL_ROW_SQL = """
    SELECT wellness_date, resting_hr_bpm, resting_hr_source, hrv_ms, hrv_source, manual_updated_at
    FROM intervals_wellness
    WHERE user_id = %s AND wellness_date = %s
"""


def set_manual_wellness(
    conn, user_id: int, wellness_date: str, values: Dict[str, Optional[float]]
) -> Optional[Dict[str, Any]]:
    """Grava a edicao manual de FC de repouso / HRV de UM dia do usuario.

    `values` traz SO as metricas enviadas (ja validadas):
    - numero -> grava o valor com origem 'manual';
    - None   -> limpa a edicao: valor NULL, origem 'intervals' (o proximo
      sync preenche, se o Intervals tiver o dado).
    Sem linha naquele dia, cria a linha so com essas metricas (um "limpar"
    sozinho nao cria linha vazia). `synced_at` nao muda numa linha existente:
    a edicao nao e um sync (throttle do refresh e "ultimo sync" continuam
    dizendo a verdade). Devolve a linha resultante (ou None se nao existe)."""
    unknown = sorted(set(values) - set(MANUAL_WELLNESS_SOURCE_COLUMNS))
    if unknown or not values:
        raise ManualWellnessError(",".join(unknown) or "body", "nenhuma metrica editavel informada")

    metrics = [metric for metric in MANUAL_WELLNESS_SOURCE_COLUMNS if metric in values]
    columns: List[str] = []
    params: List[Any] = []
    for metric in metrics:
        value = values[metric]
        columns += [metric, MANUAL_WELLNESS_SOURCE_COLUMNS[metric]]
        params += [value, SOURCE_INTERVALS if value is None else SOURCE_MANUAL]

    with conn.cursor() as cursor:
        if any(values[metric] is not None for metric in metrics):
            placeholders = ", ".join(["%s"] * len(columns))
            updates = ", ".join(f"{column} = VALUES({column})" for column in columns)
            cursor.execute(
                f"INSERT INTO intervals_wellness (wellness_date, user_id, {', '.join(columns)}, manual_updated_at) "
                f"VALUES (%s, %s, {placeholders}, CURRENT_TIMESTAMP) "
                f"ON DUPLICATE KEY UPDATE {updates}, manual_updated_at = CURRENT_TIMESTAMP, synced_at = synced_at",
                (wellness_date, user_id, *params),
            )
        else:
            assignments = ", ".join(f"{column} = %s" for column in columns)
            cursor.execute(
                f"UPDATE intervals_wellness SET {assignments}, manual_updated_at = CURRENT_TIMESTAMP, "
                "synced_at = synced_at WHERE user_id = %s AND wellness_date = %s",
                (*params, user_id, wellness_date),
            )
        cursor.execute(_MANUAL_ROW_SQL, (user_id, wellness_date))
        return cursor.fetchone()


# ---------------------------------------------------------------------------
# Credenciais do usuario
# ---------------------------------------------------------------------------


def users_with_intervals_configured(conn, user_ids: Optional[List[int]] = None) -> List[int]:
    """Ids dos usuarios ATIVOS que configuraram o Intervals.icu.

    E esta query - e nao um `SELECT id FROM users` - que garante o requisito
    "nada de varrer a base inteira": o job so conhece quem tem credencial.
    """
    sql = (
        "SELECT id FROM users "
        "WHERE is_active = 1 "
        "AND intervals_athlete_id IS NOT NULL AND intervals_athlete_id <> '' "
        "AND intervals_api_key_encrypted IS NOT NULL AND intervals_api_key_encrypted <> '' "
    )
    params: tuple = ()
    if user_ids:
        sql += f"AND id IN ({', '.join(['%s'] * len(user_ids))}) "
        params = tuple(int(value) for value in user_ids)
    sql += "ORDER BY id ASC"

    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return [int(row["id"]) for row in rows]


def get_user_intervals_credentials(conn, user_id: int) -> tuple[str, str]:
    """`(athlete_id, api_key_em_claro)`. Levanta `IntervalsCredentialsError`
    com `reason_code` (`not_configured` | `decrypt_failed`) - nunca com a
    chave no texto do erro."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT intervals_athlete_id, intervals_api_key_encrypted FROM users WHERE id = %s",
            (user_id,),
        )
        row = cursor.fetchone()

    athlete_id = (row or {}).get("intervals_athlete_id")
    encrypted_key = (row or {}).get("intervals_api_key_encrypted")

    if not athlete_id or not encrypted_key:
        raise IntervalsCredentialsError(
            "Configure sua chave do Intervals.icu no perfil antes de sincronizar.",
            "not_configured",
        )

    try:
        api_key = decrypt_secret(encrypted_key)
    except ValueError as exc:
        raise IntervalsCredentialsError(
            "Não foi possível descriptografar a chave do Intervals.icu configurada. "
            "Reconfigure-a no seu perfil.",
            "decrypt_failed",
        ) from exc

    return str(athlete_id), api_key


# ---------------------------------------------------------------------------
# Zonas de FC do atleta (M98 C2, migracao 097)
#
# Cache das zonas por esporte do `GET /athlete/{id}` (`sportSettings[]`),
# cruas como o Intervals manda. O mapeamento 7 -> 5 e a escolha do esporte
# sao do dono (`domain/hr_zones.py`), na leitura.
# ---------------------------------------------------------------------------

_UPSERT_ATHLETE_ZONES_SQL = """
    INSERT INTO intervals_athlete_hr_zones (
        user_id, sport_settings_id, sport_types, hr_zones_bpm, hr_zone_names, max_hr_bpm, lthr_bpm, fetched_at
    )
    VALUES (%s, %s, CAST(%s AS JSON), CAST(%s AS JSON), CAST(%s AS JSON), %s, %s, CURRENT_TIMESTAMP)
    ON DUPLICATE KEY UPDATE
        sport_types = VALUES(sport_types),
        hr_zones_bpm = VALUES(hr_zones_bpm),
        hr_zone_names = VALUES(hr_zone_names),
        max_hr_bpm = VALUES(max_hr_bpm),
        lthr_bpm = VALUES(lthr_bpm),
        fetched_at = CURRENT_TIMESTAMP
"""

_ATHLETE_ZONES_SQL = """
    SELECT sport_settings_id, sport_types, hr_zones_bpm, max_hr_bpm, lthr_bpm
    FROM intervals_athlete_hr_zones
    WHERE user_id = %s
    ORDER BY sport_settings_id ASC
"""


def json_list(value: Any) -> Optional[list]:
    """Coluna JSON lida (lista no PyMySQL, string no shim da Data API)."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return list(value) if isinstance(value, (list, tuple)) else None


def parse_sport_settings(athlete: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`sportSettings[]` do atleta -> linhas da tabela; so esportes com `id`
    e zonas de FC em forma valida para o dono (`hr_zones.zones_from_intervals`)."""
    rows: List[Dict[str, Any]] = []
    for item in athlete.get("sportSettings") or []:
        if not isinstance(item, dict) or to_int(item.get("id")) is None:
            continue
        zones = item.get("hr_zones")
        if not hr_zones.zones_from_intervals(zones):
            continue
        names = item.get("hr_zone_names")
        rows.append(
            {
                "sport_settings_id": to_int(item.get("id")),
                "types": [str(t) for t in (item.get("types") or []) if t],
                "hr_zones_bpm": [int(float(v)) for v in zones],
                "hr_zone_names": names if isinstance(names, list) else None,
                "max_hr_bpm": to_int(item.get("max_hr")),
                "lthr_bpm": to_int(item.get("lthr")),
            }
        )
    return rows


def upsert_athlete_hr_zones(conn, user_id: int, athlete: Dict[str, Any]) -> Dict[str, int]:
    """Grava as zonas por esporte. Idempotente pela UNIQUE (user_id,
    sport_settings_id). Payload sem nenhum esporte valido NAO apaga nada
    (ausencia de informacao nao e informacao nova); com esportes validos,
    remove os esportes que sairam do Intervals."""
    received = len(athlete.get("sportSettings") or [])
    rows = parse_sport_settings(athlete)
    if not rows:
        return {"received": received, "upserted": 0}
    params = [
        (
            user_id,
            row["sport_settings_id"],
            json.dumps(row["types"], ensure_ascii=False),
            json.dumps(row["hr_zones_bpm"]),
            None if row["hr_zone_names"] is None else json.dumps(row["hr_zone_names"], ensure_ascii=False),
            row["max_hr_bpm"],
            row["lthr_bpm"],
        )
        for row in rows
    ]
    keep = [row["sport_settings_id"] for row in rows]
    with conn.cursor() as cursor:
        cursor.executemany(_UPSERT_ATHLETE_ZONES_SQL, params)
        cursor.execute(
            "DELETE FROM intervals_athlete_hr_zones WHERE user_id = %s "
            f"AND sport_settings_id NOT IN ({', '.join(['%s'] * len(keep))})",
            (user_id, *keep),
        )
    return {"received": received, "upserted": len(rows)}


def read_athlete_hr_zones(cur, user_id: int) -> List[Dict[str, Any]]:
    """Esportes gravados do usuario (`types`, `hr_zones_bpm`, `max_hr_bpm`,
    `lthr_bpm`), na forma que `hr_zones.pick_reference_sport` espera."""
    cur.execute(_ATHLETE_ZONES_SQL, (int(user_id),))
    return [
        {
            "sport_settings_id": to_int(row.get("sport_settings_id")),
            "types": json_list(row.get("sport_types")) or [],
            "hr_zones_bpm": json_list(row.get("hr_zones_bpm")),
            "max_hr_bpm": to_int(row.get("max_hr_bpm")),
            "lthr_bpm": to_int(row.get("lthr_bpm")),
        }
        for row in (cur.fetchall() or [])
    ]


# M99: tempo em zona por atividade numa janela de dias locais (`activity_date`
# já é o dia local da atividade: `start_date_local`). Só as duas colunas: o
# `raw_json` inteiro estoura o limite de 1 MB da Data API.
ZONE_SECONDS_SQL = """
SELECT activity_date, hr_zone_seconds
FROM intervals_activities
WHERE user_id = %s AND hr_zone_seconds IS NOT NULL AND activity_date BETWEEN %s AND %s
ORDER BY activity_date ASC, id ASC
"""


def zone_seconds_rows(rows: List[Dict[str, Any]]) -> List[tuple[date, Optional[list]]]:
    """Linhas de `ZONE_SECONDS_SQL` -> (dia local, segundos por zona do Intervals)."""
    out: List[tuple[date, Optional[list]]] = []
    for row in rows:
        day = to_date(row.get("activity_date"))
        if day:
            out.append((date.fromisoformat(day), json_list(row.get("hr_zone_seconds"))))
    return out
