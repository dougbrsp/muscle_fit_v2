"""Sync automatico do Intervals.icu (melhoria M2) contra um MySQL REAL:
migracao 041 aplicada pelo runner + o job `app/intervals_sync.py` gravando de
verdade em `intervals_activities`, `intervals_wellness`,
`activity_intervals_link` e `activities`.

O que so um banco real prova (e por isso este arquivo existe ao lado dos
unitarios):

- a 041 aplica num schema criado do zero pelo runner (baseline + todas as
  versionadas) e, rodada de novo, nao tem mais nada a fazer - 0 statements;
- o `ON DUPLICATE KEY UPDATE` das tres tabelas de fato NAO duplica: o job
  roda duas vezes e o banco fica byte a byte igual - com a unica excecao do
  carimbo `synced_at`, que e a "ultima sincronizacao" do usuario e por
  contrato anda a cada passada (ver `_sem_carimbo`) - incluindo o `id` da
  linha de link, que seria outro se houvesse INSERT novo;
- a FK/UNIQUE da 041 aceita exatamente o que o job escreve.

Unica coisa dublada: a chamada HTTP ao Intervals.icu (e a descriptografia da
chave). Nada de rede no teste.

Schema proprio (`*_m2_test`), apagado no fim.

Rodar: cd api && python -m pytest tests/integration/test_intervals_sync_m2.py
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta

import pytest

from app import intervals_sync as sync
from app import migrate as m
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support.dataapi_backend import raw_mysql
from tests.support.migrations import recreate_schema_with_migrations

MIGRATION_041 = m.DEFAULT_MIGRATIONS_DIR / "041_intervals_hr_link.py"

HOJE = date.today()
INICIO_TREINO = datetime.combine(HOJE, time(19, 0))


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


def _database() -> str:
    base = os.environ["MYSQL_DATABASE"]
    return f"{base[: -len('_test')]}_m2_test"


def _factory(database: str):
    kw = _kwargs()
    return lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database)


@pytest.fixture(scope="module")
def schema():
    database = _database()
    recreate_schema_with_migrations(database=database, **_kwargs())
    m.migrate(conn_factory=_factory(database))
    yield database
    with raw_mysql(database="information_schema", **_kwargs()) as conn:
        with conn.cursor() as cur:
            assert database.endswith("_test")
            cur.execute(f"DROP DATABASE IF EXISTS `{database}`")


@pytest.fixture
def raw(schema):
    with raw_mysql(database=schema, **_kwargs()) as conn:
        yield conn


@pytest.fixture
def env(raw, schema):
    tag = os.urandom(4).hex()
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role, is_active, "
            "intervals_athlete_id, intervals_api_key_encrypted) "
            "VALUES (%s, 'x', 'admin', 1, 'i000', 'cifrada')",
            (f"m2-{tag}",),
        )
        user_id = cur.lastrowid
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, activity_type, name) "
            "VALUES (%s, %s, %s, 'musculacao', %s)",
            (user_id, HOJE.isoformat(), INICIO_TREINO, f"Treino A {tag}"),
        )
        activity_id = cur.lastrowid
        # Usuario SEM Intervals configurado: nao pode ser tocado pelo job.
        cur.execute(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, 'x', 'regular', 1)",
            (f"m2-sem-{tag}",),
        )
        outro_user = cur.lastrowid
    raw.commit()

    yield {"user": user_id, "activity": activity_id, "outro_user": outro_user, "schema": schema}

    with raw.cursor() as cur:
        cur.execute("DELETE FROM activity_intervals_link WHERE user_id IN (%s, %s)", (user_id, outro_user))
        cur.execute("DELETE FROM intervals_activities WHERE user_id IN (%s, %s)", (user_id, outro_user))
        cur.execute("DELETE FROM intervals_wellness WHERE user_id IN (%s, %s)", (user_id, outro_user))
        cur.execute("DELETE FROM activities WHERE user_id IN (%s, %s)", (user_id, outro_user))
        cur.execute("DELETE FROM users WHERE id IN (%s, %s)", (user_id, outro_user))
    raw.commit()


def _payload(external_id: str, start: datetime, sport_type: str = "WeightTraining", avg_hr: float = 142.0):
    return {
        "id": external_id,
        "start_date_local": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "name": "Sessao",
        "type": sport_type,
        "moving_time": 3900,
        "calories": 455,
        "average_heartrate": avg_hr,
        "max_heartrate": 176,
        "icu_training_load": 61,
    }


@pytest.fixture
def intervals_api(monkeypatch):
    state = {"activities": [], "wellness": []}
    monkeypatch.setattr(sync.intervals_store, "decrypt_secret", lambda encrypted: "chave-falsa")
    monkeypatch.setattr(
        sync.intervals_client,
        "fetch_activities_and_wellness",
        lambda *a, **k: (list(state["activities"]), list(state["wellness"])),
    )
    # M98 C2: o sync também lê as zonas do atleta; sem rede no teste.
    monkeypatch.setattr(sync.intervals_client, "fetch_athlete", lambda *a, **k: {})
    return state


def _rows(raw, sql, params=()):
    with raw.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# `synced_at` das tabelas de cache do Intervals e um CARIMBO DE SINCRONIZACAO,
# nao um dado: `intervals_store` escreve `synced_at = CURRENT_TIMESTAMP` no
# ON DUPLICATE KEY UPDATE de proposito, porque
# `intervals_integration.get_summary` publica `MAX(synced_at)` como a "ultima
# sincronizacao" do usuario. Ou seja: reexecutar o job DEVE mexer nesse campo.
#
# Comparar a linha inteira duas vezes transformava isso em flake de 1 em N: o
# DATETIME tem resolucao de 1 segundo, entao as duas passadas so batem quando
# caem dentro do MESMO segundo. Foi exatamente o que quebrou o CI da run
# 36047117636 (`synced_at` 19:18:37 -> 19:18:38, e so na linha de wellness).
# Reproduzido localmente pondo 1,3 s entre as duas chamadas: divergem `cache`
# e `wellness`, nunca `links`/`activities` (que nao tem carimbo).
#
# A idempotencia continua provada byte a byte em TODO o resto - inclusive o
# `id`, que seria outro se a segunda passada tivesse feito INSERT - e o
# carimbo ganha asserts proprios: mesma quantidade de linhas e tempo que so
# anda para frente.
CARIMBO = "synced_at"


def _sem_carimbo(rows):
    return [{k: v for k, v in row.items() if k != CARIMBO} for row in rows]


def _carimbos(rows):
    return [row[CARIMBO] for row in rows]


# ---------------------------------------------------------------------------
# Migracao
# ---------------------------------------------------------------------------


def test_041_aplicada_pelo_runner_e_convergida(schema):
    """O schema deste modulo foi criado do zero por `m.migrate`; se a 041
    tivesse falhado, o fixture nem existiria. Aqui o que se prova e que ela
    NAO tem mais nada a fazer - reaplicar e no-op."""
    conn = _factory(schema)()
    try:
        db = m.MigrationDB(conn)
        assert m.load_python_migration(MIGRATION_041).__globals__["plan"](db) == []
    finally:
        conn.close()


def test_tabela_de_link_existe_com_a_unique_por_treino(raw):
    indexes = _rows(
        raw,
        "SELECT INDEX_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_intervals_link' "
        "AND INDEX_NAME = 'uk_ail_activity'",
    )
    assert [int(row["NON_UNIQUE"]) for row in indexes] == [0]


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


def test_sync_grava_cache_associa_fc_e_e_idempotente(env, raw, intervals_api, schema):
    intervals_api["activities"] = [
        _payload("wt-1", INICIO_TREINO + timedelta(minutes=8)),
        _payload("run-1", INICIO_TREINO + timedelta(minutes=3), sport_type="Run", avg_hr=155.0),
    ]
    intervals_api["wellness"] = [{"id": HOJE.isoformat(), "ctl": 42.5, "atl": 33.0, "restingHR": 51}]

    primeiro = sync.sync_users(conn_factory=_factory(schema))

    assert primeiro.totals["users_processed"] == 1
    assert primeiro.totals["users_failed"] == 0
    assert primeiro.totals["hr_matched"] == 1
    assert primeiro.totals["activities_hr_filled"] == 1

    links = _rows(raw, "SELECT * FROM activity_intervals_link WHERE user_id = %s", (env["user"],))
    assert len(links) == 1
    link = links[0]
    assert link["activity_id"] == env["activity"]
    assert link["match_status"] == "MATCHED"
    assert link["match_delta_seconds"] == 8 * 60
    assert link["intervals_external_id"] == "wt-1"
    assert float(link["avg_hr_bpm"]) == 142.0
    assert link["candidates_count"] == 1

    # A corrida entrou no cache, mas NAO virou link.
    assert len(_rows(raw, "SELECT id FROM intervals_activities WHERE user_id = %s", (env["user"],))) == 2

    atividade = _rows(raw, "SELECT avg_hr_bpm, max_hr_bpm, calories_kcal FROM activities WHERE id = %s", (env["activity"],))[0]
    assert float(atividade["avg_hr_bpm"]) == 142.0
    assert float(atividade["max_hr_bpm"]) == 176.0

    # --- segunda execucao: nada pode mudar -------------------------------
    antes = {
        "links": _rows(raw, "SELECT * FROM activity_intervals_link WHERE user_id = %s", (env["user"],)),
        "cache": _rows(raw, "SELECT * FROM intervals_activities WHERE user_id = %s ORDER BY id", (env["user"],)),
        "wellness": _rows(raw, "SELECT * FROM intervals_wellness WHERE user_id = %s", (env["user"],)),
        "activity": _rows(raw, "SELECT * FROM activities WHERE id = %s", (env["activity"],)),
    }

    segundo = sync.sync_users(conn_factory=_factory(schema))

    depois = {
        "links": _rows(raw, "SELECT * FROM activity_intervals_link WHERE user_id = %s", (env["user"],)),
        "cache": _rows(raw, "SELECT * FROM intervals_activities WHERE user_id = %s ORDER BY id", (env["user"],)),
        "wellness": _rows(raw, "SELECT * FROM intervals_wellness WHERE user_id = %s", (env["user"],)),
        "activity": _rows(raw, "SELECT * FROM activities WHERE id = %s", (env["activity"],)),
    }
    for chave in ("links", "activity"):
        # Sem carimbo de sincronizacao: tem de ficar igual byte a byte.
        assert depois[chave] == antes[chave], chave

    for chave in ("cache", "wellness"):
        # Todo o resto da linha imutavel (o `id` inclusive: INSERT novo daria outro).
        assert _sem_carimbo(depois[chave]) == _sem_carimbo(antes[chave]), chave
        # E o carimbo: mesma quantidade de linhas, tempo so andando para frente.
        assert len(depois[chave]) == len(antes[chave]), chave
        for depois_ts, antes_ts in zip(_carimbos(depois[chave]), _carimbos(antes[chave])):
            assert depois_ts >= antes_ts, chave

    assert segundo.totals["hr_matched"] == 1
    # Nada a preencher na 2a volta: a FC ja estava na `activities`.
    assert segundo.totals["activities_hr_filled"] == 0


def test_usuario_sem_intervals_configurado_nao_entra_no_job(env, raw, intervals_api, schema):
    report = sync.sync_users(conn_factory=_factory(schema))
    assert env["outro_user"] not in [item.user_id for item in report.results]
    assert _rows(raw, "SELECT id FROM intervals_activities WHERE user_id = %s", (env["outro_user"],)) == ()


def test_nenhum_candidato_na_janela_nao_grava_link(env, raw, intervals_api, schema):
    intervals_api["activities"] = [_payload("wt-longe", INICIO_TREINO - timedelta(hours=6))]
    report = sync.sync_users(conn_factory=_factory(schema))

    assert report.totals["hr_unmatched"] == 1
    assert _rows(raw, "SELECT id FROM activity_intervals_link WHERE user_id = %s", (env["user"],)) == ()
    atividade = _rows(raw, "SELECT avg_hr_bpm FROM activities WHERE id = %s", (env["activity"],))[0]
    assert atividade["avg_hr_bpm"] is None


def test_falha_da_api_nao_deixa_escrita_parcial(env, raw, intervals_api, schema, monkeypatch):
    """A excecao acontece ANTES de qualquer escrita, e a transacao do usuario
    e revertida: o banco fica exatamente como estava."""
    monkeypatch.setattr(
        sync.intervals_client,
        "fetch_activities_and_wellness",
        lambda *a, **k: (_ for _ in ()).throw(sync.IntervalsClientError("quota estourada", status_code=429)),
    )
    report = sync.sync_users(conn_factory=_factory(schema))

    assert [item.reason_code for item in report.failures] == ["intervals_rate_limited"]
    assert report.results == []
    assert _rows(raw, "SELECT id FROM intervals_activities WHERE user_id = %s", (env["user"],)) == ()
    assert _rows(raw, "SELECT id FROM activity_intervals_link WHERE user_id = %s", (env["user"],)) == ()
