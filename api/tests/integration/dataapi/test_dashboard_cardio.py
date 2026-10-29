"""`GET /analytics/dashboard/cardio` (F2.3) contra MySQL real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova, em ordem de risco:

- **a FC da sessao vem de `activity_intervals_link`** (migracao 041, M2) e
  nao de um casamento refeito na leitura: a linha do link e quem manda,
  inclusive quando ela diz `MATCHED_AMBIGUOUS`;
- **e a FC MEDIDA na propria sessao vence a associada**: gravar
  `activities.avg_hr_bpm` (o que o import do relogio faz) muda a resposta e a
  origem declarada, com o casamento continuando visivel mas fora da conta;
- **o payload e IDENTICO nos dois backends** e fecha no `response_model`;
- **quatro estados, quatro respostas**: sem Intervals configurado, configurado
  e sem sincronizacao, sincronizado sem cardio na janela, e com cardio;
- **IDOR**: o usuario B nao ve nada do A - nem o cardio, nem a FC das sessoes;
- **contagem de statements travada**: o endpoint emite um numero FIXO de
  consultas, que nao cresce com o numero de sessoes. E a protecao
  deterministica contra o N+1 que ja derrubou endpoints deste hub em
  producao (`tests/support/medicao.py`).

Nenhuma data sai do relogio do runner: `hoje_do_usuario()` da a data no fuso
do usuario, como o resto da suite v3.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importado no topo de proposito: `use_backend` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import analytics_dashboard  # noqa: F401
from app.intelligence.dashboard import cardio_service  # noqa: F401
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.grupo_b_schema import ensure_grupo_b_tables
from tests.support.isolamento_v3 import truncar
from tests.support.medicao import assert_no_maximo_statements
from tests.support.relogio_usuario import hoje_do_usuario
from tests.support.sql_capture import recorded_sql

HOJE = hoje_do_usuario()

# Teto de idas ao banco do endpoint. Sao 4 consultas fixas (perfil, cache do
# Intervals na janela, ultima sincronizacao, sessoes com o link) mais o SELECT
# do fuso do usuario que a rota faz antes de resolver a janela. Uma 5a
# consulta fixa (`LINKED_INTERVALS_IDS_SQL`, M37) so e emitida quando a janela
# tem atividade de FORCA vinda do Intervals - o seed deste arquivo nao tem
# nenhuma, entao ela nao aparece aqui; mesmo com ela o total (6) fica abaixo
# do teto. A folga de 2 deixa uma consulta nova legitima passar; um N+1 por
# sessao (aqui, 4 sessoes) nao passa.
# M98 C4: +6 fixas dos blocos de acompanhamento (aderencia 1 sem sessao com
# cardio no Modelo, wellness 1, PAI 1, ultima recomendacao 1, zonas 2).
# 27/10/2026: +1 fixa da aderencia (fichas de descanso com cardio) = 12.
TETO_STATEMENTS = 14


@pytest.fixture
def seed(raw):
    """Usuario A COM Intervals configurado: 4 sessoes de treino (duas com FC
    associada - uma delas AMBIGUA -, duas sem) e 3 atividades no cache (uma
    corrida, um pedal, um yoga que NAO pode virar cardio). Usuario B: nada."""
    ensure_grupo_b_tables(raw)
    truncar(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role, intervals_athlete_id, intervals_api_key_encrypted) "
            "VALUES ('dono', 'x', 'admin', 'i123', 'chave-cifrada')"
        )
        ids["dono"] = cur.lastrowid
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('outro', 'x', 'regular')")
        ids["outro"] = cur.lastrowid

        # Cache do Intervals: corrida, pedal (sem FC) e yoga.
        atividades = (
            ("ext-run", 2, "Corrida matinal", "Run", 1800, 5000, "148.00", "171.00"),
            ("ext-ride", 4, "Pedal", "Ride", 3600, 20000, None, None),
            ("ext-yoga", 5, "Yoga", "Yoga", 2700, None, "95.00", "110.00"),
        )
        for external_id, dias, nome, sport, segundos, metros, avg_hr, max_hr in atividades:
            dia = HOJE - _dt.timedelta(days=dias)
            cur.execute(
                "INSERT INTO intervals_activities "
                "(user_id, external_id, activity_date, start_time, name, sport_type, moving_time_seconds, "
                " distance_meters, calories_kcal, avg_hr_bpm, max_hr_bpm, training_load) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    ids["dono"],
                    external_id,
                    dia,
                    _dt.datetime.combine(dia, _dt.time(6, 30)),
                    nome,
                    sport,
                    segundos,
                    None if metros is None else f"{metros:.2f}",
                    "300.00",
                    avg_hr,
                    max_hr,
                    "40.00",
                ),
            )

        # A atividade WeightTraining que o job casou com a sessao de hoje.
        cur.execute(
            "INSERT INTO intervals_activities "
            "(user_id, external_id, activity_date, start_time, name, sport_type, moving_time_seconds, avg_hr_bpm, max_hr_bpm) "
            "VALUES (%s, 'ext-wt', %s, %s, 'Musculação', 'WeightTraining', 3600, '118.00', '155.00')",
            (ids["dono"], HOJE, _dt.datetime.combine(HOJE, _dt.time(19, 5))),
        )
        intervals_wt = cur.lastrowid

        # 4 sessoes de treino: hoje, ontem, 3 e 6 dias atras.
        sessoes: list[int] = []
        for dias in (0, 1, 3, 6):
            dia = HOJE - _dt.timedelta(days=dias)
            cur.execute(
                "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status) "
                "VALUES (%s, %s, %s, 'Treino A', 'musculacao', 'done')",
                (ids["dono"], dia, _dt.datetime.combine(dia, _dt.time(19, 0))),
            )
            sessoes.append(cur.lastrowid)
        ids["sessao_hoje"], ids["sessao_ontem"] = sessoes[0], sessoes[1]

        # Hoje: casamento limpo. Ontem: AMBIGUO (dois candidatos na janela).
        for activity_id, status, candidatos, delta in (
            (sessoes[0], "MATCHED", 1, 300),
            (sessoes[1], "MATCHED_AMBIGUOUS", 2, 900),
        ):
            cur.execute(
                "INSERT INTO activity_intervals_link "
                "(user_id, activity_id, intervals_activity_id, intervals_external_id, match_status, "
                " match_delta_seconds, candidates_count, moving_time_seconds, avg_hr_bpm, max_hr_bpm, calories_kcal, training_load) "
                "VALUES (%s, %s, %s, 'ext-wt', %s, %s, %s, 3600, %s, '155.00', '410.00', '38.00')",
                (
                    ids["dono"],
                    activity_id,
                    intervals_wt,
                    status,
                    delta,
                    candidatos,
                    "120.00" if status == "MATCHED" else "116.00",
                ),
            )
    raw.commit()
    return ids


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(analytics_dashboard.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=user_id, role="regular", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _cardio(user_id: int, **params):
    return _client(user_id).get("/analytics/dashboard/cardio", params=params)


def _desconfigurar_intervals(raw, user_id: int) -> None:
    with raw.cursor() as cur:
        cur.execute(
            "UPDATE users SET intervals_athlete_id = NULL, intervals_api_key_encrypted = NULL WHERE id = %s",
            (user_id,),
        )
    raw.commit()


# ---------------------------------------------------------------------------


def test_payload_identico_nos_dois_backends(seed, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _cardio(seed["dono"], window="28d")
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]
    corpo = resultados["pymysql"]
    assert corpo["window"]["kind"] == "28d"
    assert corpo["metric_versions"]["cardio_sessions"] == "cardio-sessions-v1"
    assert corpo["metric_versions"]["session_heart_rate"] == "session-hr-v1"
    assert corpo["intervals_configured"] is True


def test_cardio_agrega_so_o_que_e_cardio(seed, db_backend):
    corpo = _cardio(seed["dono"], window="28d").json()
    totais = corpo["totals"]
    assert totais["sessions"] == 2, db_backend  # corrida + pedal
    assert totais["minutes"] == 90
    assert totais["distance_km"] == 25.0
    # O pedal veio sem FC: entra nos minutos e fica FORA da media.
    assert totais["sessions_with_hr"] == 1
    assert totais["avg_hr_bpm"] == 148.0
    # Yoga nao vira cardio por eliminacao; a musculacao do relogio tambem nao.
    assert corpo["other_activities_total"] == 1
    assert corpo["strength_activities_total"] == 1
    assert [item["sport_type"] for item in corpo["by_sport"]] == ["Ride", "Run"]


def test_fc_por_sessao_vem_do_link_e_a_ambiguidade_sobe(seed, db_backend):
    bloco = _cardio(seed["dono"], window="28d").json()["strength_heart_rate"]
    assert bloco["sessions_total"] == 4, db_backend
    assert bloco["sessions_with_hr"] == 2
    assert bloco["coverage_pct"] == 50.0
    assert bloco["ambiguous_total"] == 1
    assert bloco["avg_hr_bpm"] == 118.0  # media de 120 e 116

    assert bloco["from_link_total"] == 2  # nenhuma sessao trouxe FC propria
    assert bloco["from_activity_total"] == 0

    por_sessao = {item["activity_id"]: item for item in bloco["items"]}
    hoje = por_sessao[seed["sessao_hoje"]]
    assert hoje["hr_source"] == "intervals_link"
    assert hoje["match_status"] == "MATCHED"
    assert hoje["match_delta_seconds"] == 300
    assert hoje["has_heart_rate"] is True

    ontem = por_sessao[seed["sessao_ontem"]]
    assert ontem["match_status"] == "MATCHED_AMBIGUOUS"
    assert ontem["candidates_count"] == 2

    # Sessao sem link: FC ausente, e isso NAO e erro nem zero.
    sem_link = [item for item in bloco["items"] if item["match_status"] is None]
    assert len(sem_link) == 2
    assert all(item["avg_hr_bpm"] is None and item["has_heart_rate"] is False for item in sem_link)

    # A duvida vence a cobertura: com casamento ambiguo a resposta e LOW.
    assert _cardio(seed["dono"], window="28d").json()["data_quality"] == "LOW"


def test_fc_gravada_na_propria_sessao_vence_a_do_link(seed, raw, db_backend):
    """A sessao de hoje esta casada com uma atividade do Intervals (FC 120).
    Gravar a FC do proprio treino (o import do relogio) muda a resposta: a
    MEDIDA vence a ASSOCIADA, e a origem passa a dizer isso."""
    with raw.cursor() as cur:
        cur.execute(
            "UPDATE activities SET avg_hr_bpm = '134.00', max_hr_bpm = '170.00' WHERE id = %s",
            (seed["sessao_hoje"],),
        )
    raw.commit()

    bloco = _cardio(seed["dono"], window="28d").json()["strength_heart_rate"]
    por_sessao = {item["activity_id"]: item for item in bloco["items"]}
    hoje = por_sessao[seed["sessao_hoje"]]
    assert hoje["hr_source"] == "activity", db_backend
    assert hoje["avg_hr_bpm"] == 134.0
    assert hoje["max_hr_bpm"] == 170.0
    # O casamento continua visivel (o link nao sumiu), so deixou de ser a fonte.
    assert hoje["match_status"] == "MATCHED"
    assert bloco["from_activity_total"] == 1 and bloco["from_link_total"] == 1


def test_estados_sem_intervals_tem_frases_proprias(seed, raw, db_backend):
    _desconfigurar_intervals(raw, seed["dono"])
    corpo = _cardio(seed["dono"], window="28d").json()
    assert corpo["intervals_configured"] is False, db_backend
    assert corpo["totals"]["sessions"] == 0
    assert cardio_service.NOT_CONFIGURED_LIMITATION in corpo["limitations"]
    # O link e HISTORICO: revogar a chave nao apaga a FC dos treinos passados.
    assert corpo["strength_heart_rate"]["sessions_with_hr"] == 2


def test_janela_sem_cardio_nao_diz_que_faltou_sincronizacao(seed, db_backend):
    """Janela de 7 dias terminando ANTES das atividades: nao ha cardio, mas
    houve sincronizacao - e as duas frases sao diferentes."""
    inicio = (HOJE - _dt.timedelta(days=60)).isoformat()
    fim = (HOJE - _dt.timedelta(days=54)).isoformat()
    corpo = _cardio(seed["dono"], window="custom", date_from=inicio, date_to=fim).json()
    assert corpo["totals"]["sessions"] == 0, db_backend
    assert cardio_service.NO_CARDIO_IN_WINDOW_LIMITATION in corpo["limitations"]
    assert cardio_service.NEVER_SYNCED_LIMITATION not in corpo["limitations"]
    assert corpo["last_synced_at"] is not None


def test_usuario_b_nao_ve_nada_do_usuario_a(seed, db_backend):
    corpo = _cardio(seed["outro"], window="28d").json()
    assert corpo["intervals_configured"] is False, db_backend
    assert corpo["totals"]["sessions"] == 0
    assert corpo["strength_heart_rate"]["sessions_total"] == 0
    assert corpo["strength_heart_rate"]["coverage_pct"] is None  # sem denominador, nunca 0%
    assert corpo["data_quality"] == "INSUFFICIENT"


def test_contagem_de_statements_nao_cresce_com_as_sessoes(seed, db_backend, monkeypatch, fake_rds):
    """Protecao DETERMINISTICA (politica de `tests/support/medicao.py`): o que
    derrubou endpoints deste hub em producao foi contagem de idas ao banco em
    laco, nao constante de CPU. Aqui nao ha consulta por sessao - e este teto
    e o que impede uma de aparecer."""
    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        resposta = _cardio(seed["dono"], window="28d")
    assert resposta.status_code == 200, resposta.text
    assert_no_maximo_statements(
        statements,
        teto=TETO_STATEMENTS,
        label="GET /analytics/dashboard/cardio (4 sessoes, 4 atividades)",
        backend=db_backend,
    )


# ---------------------------------------------------------------------------
# M98 C4 - acompanhamento: semanas por zona, tendencia, PAI, proxima sessao
# ---------------------------------------------------------------------------


def test_acompanhamento_c4_le_cache_pai_estimado_e_recomendacao(seed, raw, db_backend):
    import json

    from app.domain import cardio_policy, hr_zones

    dono = seed["dono"]
    amanha = HOJE + _dt.timedelta(days=1)
    with raw.cursor() as cur:
        # Tempo em zona do Intervals (7 zonas) na corrida: Z5a-c somam na Z5.
        cur.execute(
            "UPDATE intervals_activities SET hr_zone_seconds = %s WHERE user_id = %s AND external_id = 'ext-run'",
            (json.dumps([300, 900, 300, 200, 60, 30, 10]), dono),
        )
        cur.execute(
            "INSERT INTO intervals_wellness (user_id, wellness_date, fitness_ctl, fatigue_atl, hrv_ms, resting_hr_bpm) "
            "VALUES (%s, %s, '40.0', '48.0', '45.0', '52.0')",
            (dono, HOJE - _dt.timedelta(days=1)),
        )
        recomendacao = {
            "schema_version": 1,
            "sessions": [
                {"date": (HOJE - _dt.timedelta(days=2)).isoformat(), "prescription_text": "passada",
                 "total_seconds": 1200, "signals": [], "adjustments": [], "plan_link": None},
                {"date": amanha.isoformat(), "prescription_text": "30 min Z2", "prescription_short": "Z2",
                 "total_seconds": 1800, "signals": [], "adjustments": [], "plan_link": None},
            ],
        }
        cur.execute(
            "INSERT INTO cardio_recommendations (user_id, reference_date, model, recommendation_markdown, "
            " prompt_version, recommendation_json) VALUES (%s, %s, 'gpt', '# x', 'cardio-agent-v3', %s)",
            (dono, HOJE, json.dumps(recomendacao)),
        )
    raw.commit()

    corpo = _cardio(dono, window="28d").json()
    assert corpo["metric_versions"]["cardio_weekly_zones"] == "cardio-weekly-zones-v1", db_backend
    semanas_com_zona = [w for w in corpo["weeks"] if w["zone_minutes"] is not None]
    assert len(semanas_com_zona) == 1
    assert semanas_com_zona[0]["zone_minutes"][hr_zones.zone_label(5)] == 2  # 60+30+10 s
    assert sum(w["sessions"] for w in corpo["weeks"]) == corpo["totals"]["sessions"]

    assert corpo["wellness"]["latest"]["tsb"] == -8.0  # CTL - ATL (dono do cache)
    assert corpo["wellness"]["latest"]["hrv_ms"] == 45.0
    # M99: PAI de 7 dias ESTIMADO do tempo em zona (só a corrida tem zonas: ~21 < meta).
    assert corpo["pai"]["estimated"] is True and corpo["pai"]["points"]
    assert corpo["pai"]["latest_below_target"] is True
    assert corpo["pai"]["target_min"] == cardio_policy.PAI_TARGET_MIN

    assert corpo["next_session"]["date"] == amanha.isoformat()
    assert corpo["next_session"]["prescription_text"] == "30 min Z2"
    assert corpo["adherence"] is not None and corpo["adherence"]["sessions"] == []
    assert corpo["zones"]["source"] == "none"
    assert not any("Não foi possível ler" in texto for texto in corpo["limitations"])


def test_aderencia_do_dia_de_descanso_casa_pelo_dia_local(seed, raw, db_backend):
    """27/10/2026 (decisao do usuario): ficha de DESCANSO salva para a data,
    com cardio no slot e sem sessao de forca no dia, casa com o cardio do dia
    local inteiro (`intervals_hr_match.match_local_day`). Sem atividade de
    cardio no dia = `not_done`; ficha de amanha nao entra."""
    from tests.support.template_versions import versao_v1

    from app.domain import cardio_adherence

    dono = seed["dono"]
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, template_type, is_active, created_by_user_id) "
            "VALUES ('Modelo cardio', 'abc', 1, %s)",
            (dono,),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
            "VALUES (%s, %s, 1, 'Descanso ativo', 'descanso')",
            (versao_v1(cur, template_id), template_id),
        )
        slot_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slot_items (slot_id, item_order, item_kind, cardio_mode, "
            " cardio_duration_minutes) VALUES (%s, 1, 'cardio', 'continuous', 20)",
            (slot_id,),
        )
        # Dia 2: corrida do seed (ext-run, 06:30) e nenhuma sessao de forca.
        # Dia 5: so yoga (nao e cardio). Amanha: dia que ainda nao chegou.
        for dias in (2, 5, -1):
            cur.execute(
                "INSERT INTO workout_plans (user_id, plan_date, name, plan_type, status, template_id, "
                "template_slot_sequence) VALUES (%s, %s, 'Descanso', 'abc', 'planned', %s, 1)",
                (dono, HOJE - _dt.timedelta(days=dias), template_id),
            )
    raw.commit()

    corpo = _cardio(dono, window="28d").json()
    sessoes = corpo["adherence"]["sessions"]
    assert [s["local_date"] for s in sessoes] == [
        (HOJE - _dt.timedelta(days=5)).isoformat(),
        (HOJE - _dt.timedelta(days=2)).isoformat(),
    ], db_backend
    nao_feito, feito = sessoes
    assert all(s["anchor"] == cardio_adherence.ANCHOR_LOCAL_DAY and s["activity_id"] is None for s in sessoes)
    assert nao_feito["status"] == cardio_adherence.STATUS_NOT_DONE
    assert feito["status"] == cardio_adherence.STATUS_ADHERENT and feito["match_status"] == "MATCHED"
    assert feito["realized_minutes"] == 30 and feito["planned_minutes"] == 20
    assert corpo["metric_versions"]["cardio_adherence"] == "cardio-adherence-v2"
