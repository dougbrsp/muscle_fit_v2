"""Rotas e handler do grupo B (dispositivos, catalogo de mapeamentos,
academias, Intervals.icu, metricas manuais e de sono, relatorios) SEM DDL, nos
dois backends (PyMySQL e shim RDS Data API + dublê).

Antes, essas rotas rodavam `_ensure_schema()` no caminho de requisicao
(`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD/MODIFY/DROP`, consultas a
`information_schema`, backfill) - em `devices`, em toda requisicao. Agora o
schema vem da migracao 027 e:

(a) nenhuma rota/handler emite CREATE/ALTER/DROP/TRUNCATE/RENAME nem consulta
    `information_schema` - capturado no cursor PyMySQL e nas chamadas
    `ExecuteStatement` do dublê (mesma captura dos testes do #89/025);
(b) nenhuma deixa transacao Data API pendurada.

O relatorio PDF assincrono (`POST /reports/generate`, `GET /reports[/{id}]` e
o handler da Lambda `musclefit-reports`) saiu desta lista quando foi
substituido pelo relatorio em Markdown (`GET /reports/markdown`), coberto em
`tests/integration/dataapi/test_reports_markdown.py`.
"""

from __future__ import annotations

import os

import pytest

# Importados no topo: `use_dataapi` so troca o backend de modulos ja importados.
from app.request_models import GymCreateIn, SleepMetricsUpdateIn
from app import (  # noqa: E402
    db_dataapi,  # noqa: F401 - importado no topo de proposito (ver comentario acima)
    db_pymysql,
    devices,
    exercise_mapping_catalog,
    gyms,
    intervals_client,
    intervals_integration,
    intervals_store,
    effort_metrics,
    reports,
    sleep_metrics,
)
from app.security import Identity  # noqa: E402
from tests.support.cardio_schema import ensure_cardio_table  # noqa: E402
from tests.support.grupo_b_schema import ensure_grupo_b_tables  # noqa: E402
from tests.support.sql_capture import ddl, recorded_sql  # noqa: E402


@pytest.fixture
def grupo_b_env(raw):
    """Estado de producao: tabelas pela 027 (e a de cardio pela 025, lida pelo
    relatorio), na conexao neutra e FORA da captura - este fixture precisa vir
    antes de `db_backend` na assinatura do teste."""
    import app.db as db

    assert db.get_conn is db_pymysql.get_conn, "grupo_b_env deve ser instanciado antes da troca de backend"
    ensure_cardio_table(raw)
    ensure_grupo_b_tables(raw)

    tag = os.urandom(4).hex()
    env = {"raw": raw, "tag": tag}
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role, is_active, intervals_athlete_id, intervals_api_key_encrypted) "
            "VALUES (%s, 'x', 'admin', 1, 'i000', 'cifrada')",
            (f"grupob-{tag}",),
        )
        env["user"] = cur.lastrowid
        cur.execute(
            "INSERT INTO devices (user_id, device_name, token_hash) VALUES (%s, %s, %s)",
            (env["user"], f"GB relogio {tag}", os.urandom(32).hex()),
        )
        env["device"] = cur.lastrowid
        cur.execute("INSERT INTO gyms (owner_user_id, name) VALUES (%s, %s)", (env["user"], f"GB academia {tag}"))
        env["gym"] = cur.lastrowid
        cur.execute(
            "INSERT INTO sleep_metrics (user_id, local_date, duration_hours) VALUES (%s, '2026-09-01', 7.25)", (env["user"],)
        )
        env["sleep"] = cur.lastrowid
        cur.execute("INSERT INTO reports (user_id, status, period_days) VALUES (%s, 'done', 30)", (env["user"],))
        env["report"] = cur.lastrowid
        cur.execute("INSERT INTO reports (user_id, status, period_days) VALUES (%s, 'generating', 30)", (env["user"],))
        env["report_generating"] = cur.lastrowid
        # exercise_catalog.id nao e auto_increment (preserva ids legados): id explicito.
        cur.execute("SELECT COALESCE(MAX(id), 0) + 1000 AS n FROM exercise_catalog")
        env["exercise"] = int(cur.fetchone()["n"])
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (env["exercise"], f"GB exercicio {tag}"))
        cur.execute("SELECT COALESCE(MAX(id), 0) + 1000 AS n FROM exercise_zepp_mappings")
        env["zepp_mapping"] = int(cur.fetchone()["n"])
        cur.execute(
            "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
            (env["zepp_mapping"], f"GB zepp {tag}", f"GB zepp {tag}"),
        )
    yield env

    like = f"%{tag}%"
    with raw.cursor() as cur:
        for table in ("sleep_metrics", "intervals_activities", "intervals_wellness", "reports"):
            cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (env["user"],))
        cur.execute("DELETE FROM devices WHERE device_name LIKE %s", (like,))
        cur.execute("DELETE FROM gyms WHERE name LIKE %s", (like,))
        cur.execute("SELECT id FROM exercise_catalog WHERE name LIKE %s", (like,))
        exercise_ids = [row["id"] for row in cur.fetchall()]
        for exercise_id in exercise_ids:
            cur.execute("DELETE FROM exercise_external_mappings WHERE exercise_id = %s", (exercise_id,))
            cur.execute("DELETE FROM exercise_muscle_contributions WHERE exercise_id = %s", (exercise_id,))
            cur.execute("DELETE FROM exercise_catalog WHERE id = %s", (exercise_id,))
        cur.execute("DELETE FROM exercise_zepp_mappings WHERE id = %s", (env["zepp_mapping"],))
        cur.execute("DELETE FROM users WHERE id = %s", (env["user"],))


def _identity(env) -> Identity:
    return Identity(user_id=env["user"], role="admin", source="session")


def _count(env, sql: str, params) -> int:
    with env["raw"].cursor() as cur:
        cur.execute(sql, params)
        return int(next(iter(cur.fetchone().values())))


def _ids(items) -> list[int]:
    return [int(item["id"]) for item in items]


# --------------------------------------------------------------------------- devices


def _devices_pair(env, monkeypatch):
    # M40: o dispositivo nasce do usuario da credencial (aqui, o do fixture).
    out = devices.pair_device(devices.DevicePairIn(device_name=f"GB pareado {env['tag']}"), identity=_identity(env))
    assert out["device_token"].startswith(devices.DEVICE_TOKEN_PREFIX)
    assert _count(env, "SELECT COUNT(*) FROM devices WHERE id = %s AND user_id = %s", (out["device_id"], env["user"])) == 1


def _devices_list(env, monkeypatch):
    assert env["device"] in _ids(devices.list_devices(include_revoked=True, identity=_identity(env))["items"])


def _devices_auth_por_token(env, monkeypatch):
    """M40: o SQL de `_device_identity` (JOIN com users, `needs_touch` booleano,
    UPDATE condicional) nos dois backends - roda em toda chamada do relogio."""
    from fastapi import HTTPException

    from app import security

    out = devices.pair_device(devices.DevicePairIn(device_name=f"GB auth {env['tag']}"), identity=_identity(env))
    token = out["device_token"]

    def _last_seen():
        with env["raw"].cursor() as cur:
            cur.execute("SELECT last_seen_at FROM devices WHERE id = %s", (out["device_id"],))
            return cur.fetchone()["last_seen_at"]

    assert _last_seen() is None
    identity = security.resolve_identity(token)
    assert (identity.user_id, identity.role, identity.source, identity.device_id) == (
        env["user"], "admin", "device", out["device_id"],
    )
    primeiro = _last_seen()
    assert primeiro is not None, "1a chamada grava last_seen_at"
    assert security.resolve_identity(token).user_id == env["user"]
    assert _last_seen() == primeiro, "dentro do throttle nao regrava"

    devices.revoke_device(out["device_id"], identity=_identity(env))
    with pytest.raises(HTTPException) as exc:
        security.resolve_identity(token)
    assert exc.value.status_code == 401


def _devices_revoke(env, monkeypatch):
    assert devices.revoke_device(env["device"], identity=_identity(env))["revoked"] is True
    assert _count(env, "SELECT COUNT(*) FROM devices WHERE id = %s AND is_active = 0", (env["device"],)) == 1


# --------------------------------------------------------------------------- catalogo de mapeamentos


def _as_admin_catalog(env, monkeypatch):
    monkeypatch.setattr(exercise_mapping_catalog, "_require_identity", lambda *a, **k: _identity(env))
    # Reclassificacao pos-escrita e outro fluxo (coberto em seus proprios testes).
    monkeypatch.setattr(exercise_mapping_catalog, "reclassify_after_catalog_change", lambda *a, **k: None)


def _catalog_list(env, monkeypatch):
    _as_admin_catalog(env, monkeypatch)
    out = exercise_mapping_catalog.list_mappings(
        query=env["tag"],
        primary_group="",
        amazfit_name="",
        active_only=True,
        limit=100,
        offset=0,
        x_api_token=None,
        musclefit_session=None,
    )
    assert env["exercise"] in _ids(out["items"])


# POST/PUT /exercise-mapping-catalog: o SQL usava placeholder nomeado + dict
# e falhava nos DOIS backends (corrigido para `%s` posicional). Aqui so importa
# que a escrita funcione sem DDL/information_schema. Este schema (001..021 +
# 026) nao tem a 028, entao `exercise_catalog.id` ainda nao e AUTO_INCREMENT:
# o POST usa o nome de um exercicio existente (upsert por nome). Criacao de
# exercicio NOVO, no formato da Aurora, fica em
# tests/integration/dataapi/test_exercise_mapping_catalog_escrita.py.


def _catalog_create(env, monkeypatch):
    _as_admin_catalog(env, monkeypatch)
    out = exercise_mapping_catalog.create_or_upsert_mapping(
        item={"exercise_name": f"GB exercicio {env['tag']}", "amazfit_name": f"GB amazfit {env['tag']}"},
        x_api_token=None,
        musclefit_session=None,
    )
    assert out["status"] == "ok" and out["item"]["id"] == env["exercise"]
    assert out["item"]["amazfit_name"] == f"GB amazfit {env['tag']}"


def _catalog_update(env, monkeypatch):
    _as_admin_catalog(env, monkeypatch)
    out = exercise_mapping_catalog.update_mapping(
        env["exercise"],
        item={"exercise_name": f"GB editado {env['tag']}", "amazfit_name": f"GB amazfit {env['tag']}"},
        x_api_token=None,
        musclefit_session=None,
    )
    assert out["status"] == "ok" and out["item"]["exercise_name"] == f"GB editado {env['tag']}"
    assert _count(env, "SELECT COUNT(*) FROM exercise_catalog WHERE id = %s AND name = %s", (env["exercise"], f"GB editado {env['tag']}")) == 1


def _catalog_deactivate(env, monkeypatch):
    _as_admin_catalog(env, monkeypatch)
    # DELETE inativa no catalogo canonico (mesma fonte de GET/POST/PUT), nao
    # mais no legado exercise_zepp_mappings, que fica intocado.
    out = exercise_mapping_catalog.deactivate_mapping(env["exercise"], x_api_token=None, musclefit_session=None)
    assert out["is_active"] is False
    assert _count(env, "SELECT COUNT(*) FROM exercise_catalog WHERE id = %s AND is_active = 0", (env["exercise"],)) == 1
    assert _count(env, "SELECT COUNT(*) FROM exercise_zepp_mappings WHERE id = %s AND is_active = 0", (env["zepp_mapping"],)) == 0


# --------------------------------------------------------------------------- academias


def _gyms_list(env, monkeypatch):
    monkeypatch.setattr(gyms, "_require_identity", lambda *a, **k: _identity(env))
    assert env["gym"] in _ids(gyms.list_gyms(active_only=True, x_api_token=None, musclefit_session=None)["items"])


def _gyms_create(env, monkeypatch):
    monkeypatch.setattr(gyms, "_require_identity", lambda *a, **k: _identity(env))
    name = f"GB academia nova {env['tag']}"
    out = gyms.create_gym(payload=GymCreateIn(name=name, notes="nova"), x_api_token=None, musclefit_session=None)
    assert out["item"]["name"] == name


def _gyms_update(env, monkeypatch):
    monkeypatch.setattr(gyms, "_require_identity", lambda *a, **k: _identity(env))
    name = f"GB academia editada {env['tag']}"
    out = gyms.update_gym(env["gym"], item={"name": name, "notes": "editada"}, x_api_token=None, musclefit_session=None)
    assert out["item"]["name"] == name and out["item"]["notes"] == "editada"


# --------------------------------------------------------------------------- Intervals.icu


def _intervals_payloads(env):
    activity = {
        "id": f"gb-{env['tag']}",
        "start_date_local": "2026-09-05T07:00:00",
        "name": "Corrida GB",
        "type": "Run",
        "moving_time": 1800,
        "icu_training_load": 42,
    }
    wellness = {"id": "2026-09-05", "ctl": 40.5, "atl": 35.0, "restingHR": 52, "hrv": 61}
    return activity, wellness


def _mock_intervals_client(env, monkeypatch):
    activity, wellness = _intervals_payloads(env)
    # Desde a melhoria M2 a descriptografia vive em `app/intervals_store.py`
    # (compartilhado com o job agendado `app/intervals_sync.py`).
    monkeypatch.setattr(intervals_store, "decrypt_secret", lambda encrypted: "chave-falsa")
    monkeypatch.setattr(intervals_client, "fetch_activities_and_wellness", lambda *a, **k: ([activity], [wellness]))
    # M98 C3a: o sync manual tambem busca as zonas do atleta (sem rede no teste;
    # payload sem esporte valido nao grava nada).
    monkeypatch.setattr(intervals_client, "fetch_athlete", lambda *a, **k: {"sportSettings": []})


def _intervals_health(env, monkeypatch):
    assert intervals_integration.intervals_integration_health(identity=_identity(env))["status"] == "ok"


def _intervals_status(env, monkeypatch):
    assert intervals_integration.intervals_status(identity=_identity(env))["configured"] is True


def _intervals_sync(env, monkeypatch):
    _mock_intervals_client(env, monkeypatch)
    out = intervals_integration.intervals_sync(days=7, identity=_identity(env))
    assert out["fitness_api"]["activities"]["upserted"] == 1
    assert out["fitness_api"]["wellness"]["upserted"] == 1
    assert _count(env, "SELECT COUNT(*) FROM intervals_activities WHERE user_id = %s", (env["user"],)) == 1
    assert _count(env, "SELECT COUNT(*) FROM intervals_wellness WHERE user_id = %s", (env["user"],)) == 1


def _intervals_dashboard(env, monkeypatch):
    """Wellness do /dashboard vem do cache, apos o refresh curto gravar pelo
    upsert compartilhado - nos dois backends (o api-core de producao escreve
    em `intervals_wellness` via Data API). HRV = somente rMSSD."""
    from datetime import date

    activity, _ = _intervals_payloads(env)
    today = date.today().isoformat()
    # M53: a atividade buscada ao vivo tambem vai para o cache - com data de
    # hoje para cair sempre dentro da janela gravada.
    activity = {**activity, "start_date_local": f"{today}T07:00:00"}
    monkeypatch.setattr(intervals_store, "decrypt_secret", lambda encrypted: "chave-falsa")
    monkeypatch.setattr(intervals_client, "fetch_activities", lambda *a, **k: [activity])
    monkeypatch.setattr(
        intervals_client,
        "fetch_wellness",
        lambda *a, **k: [{"id": today, "ctl": 40.5, "atl": 35.0, "hrv": 61, "hrvSDNN": 24}],
    )
    out = intervals_integration.intervals_live_dashboard(days=30, identity=_identity(env))
    assert out["wellness_refresh"] == "refreshed"
    assert out["wellness_source"] == "cache"
    point = next(item for item in out["wellness_trend"] if item["date"] == today)
    assert point["hrv"] == 61.0 and point["restingHr"] is None
    # Segunda abertura logo em seguida: throttle, sem nova chamada ao Intervals.
    assert intervals_integration._refresh_wellness_cache(env["user"], 30) == "throttled"
    assert out["activities_cache"] == "stored"
    assert _count(env, "SELECT COUNT(*) FROM intervals_activities WHERE user_id = %s", (env["user"],)) == 1


def _intervals_upsert_activities(env, monkeypatch):
    activity, _ = _intervals_payloads(env)
    out = intervals_integration.upsert_intervals_activities(payload={"items": [activity]}, identity=_identity(env))
    assert out["upserted"] == 1


def _intervals_upsert_wellness(env, monkeypatch):
    _, wellness = _intervals_payloads(env)
    out = intervals_integration.upsert_intervals_wellness(payload={"items": [wellness]}, identity=_identity(env))
    assert out["upserted"] == 1


def _intervals_summary(env, monkeypatch):
    assert isinstance(intervals_integration.intervals_cached_summary(days=90, identity=_identity(env)), dict)


# --------------------------------------------------------------------------- metricas manuais e de sono


def _metric_routes(module, table, create, list_, get, update, delete, env_key, field, value):
    def _create(env, monkeypatch):
        out = getattr(module, create)({"local_date": "2026-09-10", field: value}, identity=_identity(env))
        assert _count(env, f"SELECT COUNT(*) FROM {table} WHERE id = %s AND user_id = %s", (out["id"], env["user"])) == 1

    def _list(env, monkeypatch):
        out = getattr(module, list_)(days=None, limit=200, identity=_identity(env))
        assert env[env_key] in _ids(out["items"])

    def _get(env, monkeypatch):
        out = getattr(module, get)(env[env_key], identity=_identity(env))
        assert int(out["id"]) == env[env_key]

    def _update(env, monkeypatch):
        body = {field: value}
        if module is sleep_metrics:  # SEG-11: a rota recebe o modelo estrito
            body = SleepMetricsUpdateIn(**body)
        getattr(module, update)(env[env_key], body, identity=_identity(env))
        assert _count(env, f"SELECT COUNT(*) FROM {table} WHERE id = %s AND {field} = %s", (env[env_key], value)) == 1

    def _delete(env, monkeypatch):
        getattr(module, delete)(env[env_key], identity=_identity(env))
        assert _count(env, f"SELECT COUNT(*) FROM {table} WHERE id = %s", (env[env_key],)) == 0

    return _create, _list, _get, _update, _delete


def _effort_list(env, monkeypatch):
    """M99: o CRUD manual saiu; `GET /effort-metrics` só lê o tempo em zona do
    Intervals e devolve o EPOC/PAI de 7 dias estimado pelo motor."""
    out = effort_metrics.list_effort_metrics(days=30, identity=_identity(env))
    assert out == {"items": []}  # o usuário do teste não tem atividade sincronizada


_SLEEP = _metric_routes(
    sleep_metrics,
    "sleep_metrics",
    "create_or_update_sleep_metrics",
    "list_sleep_metrics",
    "get_sleep_metrics",
    "update_sleep_metrics",
    "delete_sleep_metrics",
    "sleep",
    "awake_count",
    3,
)


ROUTES = {
    "POST /devices/pair": _devices_pair,
    "GET /devices": _devices_list,
    "DELETE /devices/{id}": _devices_revoke,
    "X-API-Token de dispositivo (resolve_identity)": _devices_auth_por_token,
    "GET /exercise-mapping-catalog": _catalog_list,
    "POST /exercise-mapping-catalog": _catalog_create,
    "PUT /exercise-mapping-catalog/{id}": _catalog_update,
    "DELETE /exercise-mapping-catalog/{id}": _catalog_deactivate,
    "GET /gyms": _gyms_list,
    "POST /gyms": _gyms_create,
    "PUT /gyms/{id}": _gyms_update,
    "GET /integrations/intervals/health": _intervals_health,
    "GET /integrations/intervals/status": _intervals_status,
    "POST /integrations/intervals/sync": _intervals_sync,
    "POST /integrations/intervals/activities": _intervals_upsert_activities,
    "POST /integrations/intervals/wellness": _intervals_upsert_wellness,
    "GET /integrations/intervals/summary": _intervals_summary,
    "GET /integrations/intervals/dashboard": _intervals_dashboard,
    "GET /effort-metrics": _effort_list,
    "POST /sleep-metrics": _SLEEP[0],
    "GET /sleep-metrics": _SLEEP[1],
    "GET /sleep-metrics/{id}": _SLEEP[2],
    "PUT /sleep-metrics/{id}": _SLEEP[3],
    "DELETE /sleep-metrics/{id}": _SLEEP[4],
}

# Sem nenhum SQL da aplicacao (antes, so o guard tocava o banco).
NO_APP_SQL = {"GET /integrations/intervals/health"}


def test_instrumentacao_captura_ddl(grupo_b_env, db_backend, monkeypatch, fake_rds):
    """Garante que a captura abaixo nao e vazia por construcao."""
    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        conn = reports._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS reports (id BIGINT PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()
    assert len(ddl(statements)) == 1, statements


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_rota_e_handler_do_grupo_b_nao_emitem_ddl(grupo_b_env, db_backend, monkeypatch, fake_rds, route):
    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        ROUTES[route](grupo_b_env, monkeypatch)

    if route in NO_APP_SQL and db_backend == "data-api":
        assert statements == []
    elif route not in NO_APP_SQL:
        assert statements, "nenhum statement capturado - instrumentacao quebrada"
    assert ddl(statements) == []
    assert [s for s in statements if "information_schema" in s.lower()] == []
    if db_backend == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"
