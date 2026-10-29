"""M52 (wellness velho ignorado) e M53 (atividades ao vivo gravadas no cache)
contra MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API
de producao).

M52 - o que este arquivo prova:
- com wellness FRESCO, o overview de recuperacao e IDENTICO ao do modelo
  vigente (`fullbody-no-reps-v1`) antes do M52 - mesma leitura, mesmo TSB,
  mesmos numeros - e o fator de fato estica a recuperacao (o teste nao passa
  "por vacuo");
- com wellness VELHO (> 3 dias), o overview e identico ao de "sem
  Intervals.icu" (fator neutro), e o ajuste exposto diz `stale` com a data;
- o limite e inclusivo (3 dias ainda vale) e o "hoje" e o do FUSO DO USUARIO;
- IDOR: o wellness de um usuario nunca decide o do outro;
- o prompt de geracao omite a secao com dado velho;
- payload identico nos dois backends.

M53 - o que este arquivo prova:
- abrir `/integrations/intervals/dashboard` grava as atividades em
  `intervals_activities` e associa a FC ao treino realizado
  (`activity_intervals_link` + `activities.avg_hr_bpm`), como o job;
- idempotente: duas gravacoes seguidas nao duplicam nada (nem o link);
- IDOR: o cache, o link e a FC de outro usuario - inclusive com o MESMO
  `external_id` e um treino no MESMO horario - ficam intocados;
- falha de escrita nao quebra a pagina nem deixa transacao Data API aberta;
- custo em statements medido e com teto.

O Intervals.icu e sempre dublado (nenhuma rede). Os dados sao semeados por
conexao NEUTRA (PyMySQL direto).
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importados no topo de proposito: `use_backend` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import (  # noqa: F401
    analytics_dashboard,
    dashboard_modules,
    intervals_client,
    intervals_integration,
    intervals_store,
    intervals_sync,
    workout_plan_ai,
)
from app.intelligence import muscle_recovery_service, recovery_read_model, volume_service  # noqa: F401
from app.intelligence.base import IntelligenceServiceBase  # noqa: F401
from app.intelligence.dashboard import recovery_signals_service  # noqa: F401
from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.grupo_b_schema import ensure_grupo_b_tables
from tests.support.isolamento_v3 import congelar_relogio, truncar
from tests.support.relogio_usuario import hoje_do_usuario
from tests.support.sql_capture import recorded_sql
from tests.support.privado import exigir

HOJE = hoje_do_usuario()  # fuso do usuario, nunca o do runner/MySQL
SUPINO = 2101


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


@pytest.fixture
def seed(raw):
    """Dois usuarios. "dono" treinou supino ontem (a recuperacao tem numero);
    "outro" tambem, para o IDOR. Wellness e semeado por cada teste - e o
    que cada cenario varia."""
    ensure_grupo_b_tables(raw)
    truncar(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome, papel in (("dono", "admin"), ("outro", "regular")):
            cur.execute(
                "INSERT INTO users (username, password_hash, role, is_active, "
                "intervals_athlete_id, intervals_api_key_encrypted) VALUES (%s, 'x', %s, 1, %s, 'cifrada')",
                (nome, papel, f"i-{nome}"),
            )
            ids[nome] = cur.lastrowid

        cur.execute("INSERT INTO muscle_groups (name) VALUES ('Peito')")
        peito = cur.lastrowid
        cur.execute(
            "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) "
            "VALUES (%s, 'Supino reto', 'Supino reto')",
            (SUPINO,),
        )
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, 'Supino reto')", (SUPINO,))
        cur.execute(
            "INSERT INTO exercise_muscle_contributions "
            "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight, is_active) "
            "VALUES (%s, %s, 'PRIMARY', 1.00, 1.00, 1)",
            (SUPINO, peito),
        )

        ontem = HOJE - _dt.timedelta(days=1)
        for nome in ("dono", "outro"):
            cur.execute(
                "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status) "
                "VALUES (%s, %s, %s, 'Treino', 'musculacao', 'done')",
                (ids[nome], ontem, _dt.datetime.combine(ontem, _dt.time(19, 0))),
            )
            atividade = cur.lastrowid
            cur.execute(
                "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
                "VALUES (%s, 1, 'Supino reto', %s, 4)",
                (atividade, SUPINO),
            )
            exercicio = cur.lastrowid
            for numero in (1, 2, 3, 4):
                cur.execute(
                    "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
                    "VALUES (%s, %s, 'working', 10, '60.00', '9.00')",
                    (exercicio, numero),
                )
    raw.commit()
    ids["raw"] = raw  # type: ignore[assignment]
    return ids


def _wellness(raw, user_id: int, dia: _dt.date, tsb: str | None) -> None:
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO intervals_wellness (user_id, wellness_date, fitness_ctl, fatigue_atl, form_tsb) "
            "VALUES (%s, %s, '50.00', '65.00', %s)",
            (user_id, dia, tsb),
        )
    raw.commit()


def _timezone(raw, user_id: int, tz: str) -> None:
    with raw.cursor() as cur:
        cur.execute("UPDATE users SET timezone = %s WHERE id = %s", (tz, user_id))
    raw.commit()


def _client(user_id: int, *routers) -> TestClient:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=user_id, role="regular", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _legacy_global_tsb(self, user_id=None):
    """`_get_global_tsb` do modelo vigente ANTES do M52, literal: a ultima
    linha de `intervals_wellness`, sem olhar a data."""
    wellness = self.get_latest_intervals_wellness(user_id=user_id)
    tsb = wellness.get("form_tsb") if wellness else None
    return (float(tsb) if tsb is not None else None), wellness


def _overview(user_id: int) -> list[dict]:
    return MuscleRecoveryService().get_recovery_overview(days=14, limit=24, user_id=user_id)


def _overview_legacy(monkeypatch, user_id: int) -> list[dict]:
    with monkeypatch.context() as patch:
        patch.setattr(MuscleRecoveryService, "_get_global_tsb", _legacy_global_tsb)
        return _overview(user_id)


def _overview_sem_intervals(monkeypatch, user_id: int) -> list[dict]:
    with monkeypatch.context() as patch:
        patch.setattr(MuscleRecoveryService, "_get_global_tsb", lambda self, user_id=None: (None, None))
        return _overview(user_id)


def _peito(items: list[dict]) -> dict:
    return next(item for item in items if item["muscle_group"] == "Peito")


# ---------------------------------------------------------------------------
# M52
# ---------------------------------------------------------------------------


def test_m52_fresco_numero_identico_ao_modelo_vigente(seed, monkeypatch, db_backend):
    congelar_relogio(monkeypatch)
    _wellness(seed["raw"], seed["dono"], HOJE - _dt.timedelta(days=1), "-15.00")

    novo = _overview(seed["dono"])
    assert novo == _overview_legacy(monkeypatch, seed["dono"]), db_backend
    # Nao passa por vacuo: o TSB fresco de fato estica a recuperacao.
    sem = _overview_sem_intervals(monkeypatch, seed["dono"])
    assert _peito(novo)["estimated_recovery_hours"] > _peito(sem)["estimated_recovery_hours"], db_backend

    corpo = _client(seed["dono"], dashboard_modules.router).get("/analytics/muscle-recovery").json()
    peito_rota, peito_novo = _peito(corpo["items"]), _peito(novo)
    for campo in ("estimated_recovery_hours", "remaining_hours", "recovery_pct", "readiness_status"):
        assert peito_rota[campo] == peito_novo[campo], (db_backend, campo)
    assert corpo["global_readiness_adjustment"] == {
        "form_tsb": -15.0,
        "factor": 1.1,
        "wellness_date": (HOJE - _dt.timedelta(days=1)).isoformat(),
        "stale": False,
        "max_age_days": 3,
    }, db_backend


def test_m52_limite_de_3_dias_ainda_aplica(seed, monkeypatch, db_backend):
    congelar_relogio(monkeypatch)
    _wellness(seed["raw"], seed["dono"], HOJE - _dt.timedelta(days=3), "-15.00")

    assert _overview(seed["dono"]) == _overview_legacy(monkeypatch, seed["dono"]), db_backend
    ajuste = MuscleRecoveryService().get_global_readiness_adjustment(user_id=seed["dono"])
    assert ajuste["stale"] is False and ajuste["factor"] == pytest.approx(1.1), db_backend


def test_m52_velho_vira_fator_neutro_e_avisa(seed, monkeypatch, db_backend):
    congelar_relogio(monkeypatch)
    velho = HOJE - _dt.timedelta(days=4)
    _wellness(seed["raw"], seed["dono"], velho, "-15.00")

    assert _overview(seed["dono"]) == _overview_sem_intervals(monkeypatch, seed["dono"]), db_backend
    assert _overview(seed["dono"]) != _overview_legacy(monkeypatch, seed["dono"]), db_backend

    corpo = _client(seed["dono"], dashboard_modules.router).get("/analytics/muscle-recovery").json()
    assert corpo["global_readiness_adjustment"] == {
        "form_tsb": -15.0,
        "factor": 1.0,
        "wellness_date": velho.isoformat(),
        "stale": True,
        "max_age_days": 3,
    }, db_backend

    sinais = _client(seed["dono"], analytics_dashboard.router).get("/analytics/dashboard/recovery-signals")
    assert sinais.status_code == 200, sinais.text
    ajuste = sinais.json()["global_readiness_adjustment"]
    assert ajuste["stale"] is True and ajuste["factor"] == 1.0, db_backend
    assert ajuste["wellness_date"] == velho.isoformat(), db_backend
    assert ajuste["max_age_days"] == 3, db_backend


def test_m52_hoje_e_o_do_fuso_do_usuario(seed, monkeypatch, db_backend):
    # 22:00 em Sao Paulo = 10:00 do dia seguinte em Tokyo.
    congelar_relogio(monkeypatch, _dt.datetime.combine(HOJE, _dt.time(22, 0)))
    tres_dias = HOJE - _dt.timedelta(days=3)
    _wellness(seed["raw"], seed["dono"], tres_dias, "-15.00")
    _wellness(seed["raw"], seed["outro"], tres_dias, "-15.00")
    _timezone(seed["raw"], seed["outro"], "Asia/Tokyo")

    sp = MuscleRecoveryService().get_global_readiness_adjustment(user_id=seed["dono"])
    tokyo = MuscleRecoveryService().get_global_readiness_adjustment(user_id=seed["outro"])
    assert sp["stale"] is False, db_backend  # 3 dias em Sao Paulo
    assert tokyo["stale"] is True, db_backend  # 4 dias em Tokyo


def test_m52_idor_wellness_de_um_nao_decide_o_do_outro(seed, monkeypatch, db_backend):
    congelar_relogio(monkeypatch)
    _wellness(seed["raw"], seed["dono"], HOJE - _dt.timedelta(days=10), "-25.00")
    _wellness(seed["raw"], seed["outro"], HOJE, "30.00")

    dono = MuscleRecoveryService().get_global_readiness_adjustment(user_id=seed["dono"])
    outro = MuscleRecoveryService().get_global_readiness_adjustment(user_id=seed["outro"])
    assert dono["stale"] is True and dono["form_tsb"] == -25.0, db_backend
    assert outro["stale"] is False and outro["form_tsb"] == 30.0, db_backend
    # O dado fresco do outro nao "conserta" o do dono, e vice-versa.
    assert _overview(seed["dono"]) == _overview_sem_intervals(monkeypatch, seed["dono"]), db_backend


def test_m52_prompt_omite_secao_com_dado_velho(seed, monkeypatch, db_backend):
    congelar_relogio(monkeypatch)
    _wellness(seed["raw"], seed["dono"], HOJE - _dt.timedelta(days=5), "-15.00")
    _wellness(seed["raw"], seed["outro"], HOJE, "-8.00")

    velho = workout_plan_ai.get_latest_intervals_wellness(user_id=seed["dono"])
    assert velho is not None and velho["stale"] is True, db_backend
    assert workout_plan_ai.format_intervals_wellness_section(velho) == "", db_backend

    fresco = workout_plan_ai.get_latest_intervals_wellness(user_id=seed["outro"])
    secao = workout_plan_ai.format_intervals_wellness_section(fresco)
    # `exigir`: a falha nao poe a secao (prosa do repositorio privado) no log publico.
    exigir("Forma (TSB): -8.0" in secao, f"forma (TSB) [{db_backend}]")
    exigir(f"wellness_date={HOJE.isoformat()}" in secao, f"data do registro [{db_backend}]")


def test_m52_payload_identico_nos_dois_backends(seed, monkeypatch, fake_rds, mysql_schema):
    congelar_relogio(monkeypatch)
    _wellness(seed["raw"], seed["dono"], HOJE - _dt.timedelta(days=6), "-15.00")

    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        legado = _client(seed["dono"], dashboard_modules.router).get("/analytics/muscle-recovery")
        sinais = _client(seed["dono"], analytics_dashboard.router).get("/analytics/dashboard/recovery-signals")
        assert legado.status_code == 200 and sinais.status_code == 200, backend
        resultados[backend] = (legado.json()["global_readiness_adjustment"], sinais.json())

    assert resultados["pymysql"] == resultados["data-api"]
    assert resultados["pymysql"][0]["stale"] is True
    assert resultados["pymysql"][1]["global_readiness_adjustment"]["stale"] is True


# ---------------------------------------------------------------------------
# M53
# ---------------------------------------------------------------------------

# A rota usa o relogio do PROCESSO para a janela da busca ao vivo (como
# antes do M53); o cenario usa o mesmo, para a atividade cair na janela.
HOJE_PROCESSO = _dt.date.today()
INICIO_TREINO = _dt.datetime.combine(HOJE_PROCESSO, _dt.time(19, 0))


def _payload(external_id: str, inicio: _dt.datetime, sport: str, avg_hr: float | None = None) -> dict:
    return {
        "id": external_id,
        "start_date_local": inicio.strftime("%Y-%m-%dT%H:%M:%S"),
        "name": f"Atividade {external_id}",
        "type": sport,
        "moving_time": 3600,
        "calories": 400,
        "average_heartrate": avg_hr,
        "max_heartrate": 170 if avg_hr else None,
        "icu_training_load": 55,
    }


@pytest.fixture
def m53(seed, monkeypatch):
    """Os dois usuarios com Intervals configurado e um treino de forca HOJE
    as 19:00. O "outro" ja tem no cache uma atividade com o MESMO
    `external_id` que o Intervals do dono vai devolver."""
    raw = seed["raw"]
    with raw.cursor() as cur:
        treinos = {}
        for nome in ("dono", "outro"):
            cur.execute(
                "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status) "
                "VALUES (%s, %s, %s, 'Treino hoje', 'musculacao', 'done')",
                (seed[nome], HOJE_PROCESSO, INICIO_TREINO),
            )
            treinos[nome] = cur.lastrowid
        cur.execute(
            "INSERT INTO intervals_activities (user_id, external_id, activity_date, start_time, name, sport_type) "
            "VALUES (%s, 'forca-1', %s, %s, 'B original', 'WeightTraining')",
            (seed["outro"], HOJE_PROCESSO, INICIO_TREINO),
        )
    raw.commit()

    atividades = [
        _payload("forca-1", INICIO_TREINO + _dt.timedelta(minutes=5), "WeightTraining", avg_hr=142.0),
        _payload("corrida-1", INICIO_TREINO - _dt.timedelta(hours=10), "Run", avg_hr=150.0),
    ]
    monkeypatch.setattr(intervals_store, "decrypt_secret", lambda encrypted: "chave-falsa")
    monkeypatch.setattr(intervals_client, "fetch_activities", lambda *a, **k: [dict(item) for item in atividades])
    monkeypatch.setattr(intervals_client, "fetch_wellness", lambda *a, **k: [])
    return {**seed, "treinos": treinos}


def _dashboard(user_id: int):
    return _client(user_id, intervals_integration.router).get("/integrations/intervals/dashboard?days=30")


def _linhas(raw, sql: str, params) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _estado_do_dono(env) -> dict:
    raw, dono = env["raw"], env["dono"]
    return {
        "cache": _linhas(
            raw,
            "SELECT id, external_id, name, sport_type, avg_hr_bpm FROM intervals_activities "
            "WHERE user_id = %s ORDER BY external_id",
            (dono,),
        ),
        "link": _linhas(
            raw,
            "SELECT id, activity_id, intervals_external_id, match_status, avg_hr_bpm FROM activity_intervals_link "
            "WHERE user_id = %s",
            (dono,),
        ),
        "fc": _linhas(raw, "SELECT avg_hr_bpm FROM activities WHERE id = %s", (env["treinos"]["dono"],)),
    }


def test_m53_grava_associa_fc_e_e_idempotente(m53, monkeypatch, db_backend):
    resposta = _dashboard(m53["dono"])
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["activities_cache"] == "stored", db_backend
    assert resposta.json()["activity_count"] == 2, db_backend

    primeiro = _estado_do_dono(m53)
    assert [linha["external_id"] for linha in primeiro["cache"]] == ["corrida-1", "forca-1"], db_backend
    (link,) = primeiro["link"]
    assert link["activity_id"] == m53["treinos"]["dono"], db_backend
    assert link["intervals_external_id"] == "forca-1", db_backend
    assert link["match_status"] == "MATCHED", db_backend
    assert float(primeiro["fc"][0]["avg_hr_bpm"]) == 142.0, db_backend

    # Segunda abertura logo depois: o throttle segura.
    assert _dashboard(m53["dono"]).json()["activities_cache"] == "throttled", db_backend
    # Sem o throttle, a MESMA gravacao de novo: nada duplica (nem o id do link).
    monkeypatch.setattr(intervals_integration, "_activities_recently_synced", lambda conn, user_id: False)
    assert _dashboard(m53["dono"]).json()["activities_cache"] == "stored", db_backend
    assert _estado_do_dono(m53) == primeiro, db_backend


def test_m53_idor_cache_link_e_fc_do_outro_intocados(m53, db_backend):
    assert _dashboard(m53["dono"]).status_code == 200

    raw, outro = m53["raw"], m53["outro"]
    cache_outro = _linhas(
        raw, "SELECT external_id, name, avg_hr_bpm FROM intervals_activities WHERE user_id = %s", (outro,)
    )
    assert cache_outro == [{"external_id": "forca-1", "name": "B original", "avg_hr_bpm": None}], db_backend
    assert _linhas(raw, "SELECT id FROM activity_intervals_link WHERE user_id = %s", (outro,)) == [], db_backend
    fc_outro = _linhas(raw, "SELECT avg_hr_bpm FROM activities WHERE id = %s", (m53["treinos"]["outro"],))
    assert fc_outro == [{"avg_hr_bpm": None}], db_backend
    # O link do dono aponta para a linha do DONO, nunca para a do outro.
    (link,) = _linhas(
        raw,
        "SELECT ia.user_id FROM activity_intervals_link l JOIN intervals_activities ia "
        "ON ia.id = l.intervals_activity_id WHERE l.user_id = %s",
        (m53["dono"],),
    )
    assert link["user_id"] == m53["dono"], db_backend


def test_m53_falha_de_escrita_nao_quebra_a_pagina(m53, monkeypatch, fake_rds, db_backend):
    def boom(conn, user_id, items):
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        raise RuntimeError("Deadlock found when trying to get lock")

    monkeypatch.setattr(intervals_store, "upsert_activities", boom)
    resposta = _dashboard(m53["dono"])

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["activities_cache"] == "failed", db_backend
    assert corpo["activity_count"] == 2, db_backend  # a tela segue com o dado ao vivo
    assert _estado_do_dono(m53)["cache"] == [], db_backend
    if db_backend == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


# Custo da gravacao, em statements, no cenario acima (2 atividades, 1 treino
# casado): throttle (1) + ids ja no cache (1) + upsert (2) + treinos do periodo
# (1) + candidatos WeightTraining (1) + link (1) + FC no treino (1) = 8.
# Aberturas seguidas dentro de 5 min custam so o throttle (1).
MAX_STATEMENTS_STORE = 8
MAX_STATEMENTS_STORE_THROTTLED = 1


def test_m53_custo_em_statements(m53, monkeypatch, fake_rds, db_backend):
    atividades = intervals_client.fetch_activities()

    with recorded_sql(db_backend, monkeypatch, fake_rds) as primeira:
        assert intervals_integration._store_live_activities(m53["dono"], atividades, HOJE_PROCESSO) == "stored"
    with recorded_sql(db_backend, monkeypatch, fake_rds) as segunda:
        assert intervals_integration._store_live_activities(m53["dono"], atividades, HOJE_PROCESSO) == "throttled"

    print(f"\n[M53 {db_backend}] statements: gravacao={len(primeira)} throttled={len(segunda)}")
    assert len(primeira) <= MAX_STATEMENTS_STORE, (db_backend, primeira)
    assert len(segunda) <= MAX_STATEMENTS_STORE_THROTTLED, (db_backend, segunda)
