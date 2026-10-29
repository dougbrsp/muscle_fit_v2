"""M98 C4 - acompanhamento do cardio no hub (`/dashboard/cardio`).

O que este arquivo protege:

1. **Nenhuma regra fora do dono.** Minutos por zona saem de
   `hr_zones.app_zone_seconds` (mapeamento 7 -> 5), a sessao intensa de
   `cardio_envelope.is_high_intensity`, o status da aderencia de
   `cardio_adherence`, a meta de PAI de `cardio_policy`, o TSB ausente de
   `intervals_store.derive_form_tsb`. Os testes comparam com o dono, nao com
   numero escrito aqui.
2. **Ausencia nao vira zero nem classe.** Sessao sem tempo em zona nao e
   "continua" por eliminacao; semana sem sessao com zona nao tem
   `zone_minutes`.
3. **Bloco acessorio degrada sozinho.** Falha de leitura vira ressalva, nunca
   500 na tela.
4. **Uma definicao de cardio** em `analytics.py` (dono
   `intervals_activity_kind`) e **dia do usuario** no contexto v2.
5. **Casamento por dia local** (proposta da C4) mora em `intervals_hr_match`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from app import analytics, cardio_recommendation_v3, intervals_hr_match, intervals_store
from app.domain import cardio_adherence, cardio_envelope, cardio_load, cardio_policy, cardio_prescription, hr_zones
from app.intelligence.dashboard import cardio_service as cs
from app.intelligence.dashboard.contracts import CardioResponse, WindowInfo, WindowKind

DATE_TO = date(2026, 9, 22)  # terca


def _window(days: int = 28) -> WindowInfo:
    return WindowInfo(
        kind=WindowKind.ROLLING_28D,
        date_from=DATE_TO - timedelta(days=days - 1),
        date_to=DATE_TO,
        timezone="America/Sao_Paulo",
        days=days,
    )


def _atividade(**over):
    linha = {
        "id": 1,
        "external_id": "i-1",
        "activity_date": date(2026, 9, 20),
        "start_time": datetime(2026, 9, 20, 6, 30),
        "name": "Corrida",
        "sport_type": "Run",
        "moving_time_seconds": 1800,
        "calories_kcal": "300.00",
        "avg_hr_bpm": "140.00",
        "hr_zone_seconds": None,
    }
    linha.update(over)
    return linha


# Intervals com 7 zonas: Z5a/Z5b/Z5c somam na Z5 do app.
ZONAS_7_CONTINUO = [300, 1200, 240, 60, 0, 0, 0]
ZONAS_7_INTENSO = [120, 300, 180, 400, 100, 50, 10]


# ---------------------------------------------------------------------------
# 1) Normalizacao da sessao
# ---------------------------------------------------------------------------


def test_sessao_com_tempo_em_zona_usa_o_mapeamento_e_a_regra_do_dono():
    item = cs.normalize_intervals_row(_atividade(hr_zone_seconds=json.dumps(ZONAS_7_INTENSO)))
    esperado = hr_zones.app_zone_seconds(ZONAS_7_INTENSO)
    assert item["zone_seconds"] == list(esperado)
    assert item["zone_minutes"] == {
        hr_zones.zone_label(z): cardio_prescription.minutes_from_seconds(s)
        for z, s in zip(hr_zones.HR_ZONE_NUMBERS, esperado)
    }
    assert item["high_intensity"] is cardio_envelope.is_high_intensity(esperado) is True


def test_sessao_sem_tempo_em_zona_nao_tem_classe():
    item = cs.normalize_intervals_row(_atividade())
    assert item["zone_seconds"] is None and item["zone_minutes"] is None
    assert item["high_intensity"] is None


# ---------------------------------------------------------------------------
# 2) Semanas
# ---------------------------------------------------------------------------


def test_semanas_cobrem_a_janela_inteira_e_somam_segundos_antes_de_converter():
    window = _window()
    sessoes = [
        cs.normalize_intervals_row(_atividade(id=1, hr_zone_seconds=ZONAS_7_CONTINUO)),
        cs.normalize_intervals_row(
            _atividade(id=2, external_id="i-2", activity_date=date(2026, 9, 21), hr_zone_seconds=ZONAS_7_INTENSO)
        ),
        cs.normalize_intervals_row(_atividade(id=3, external_id="i-3", activity_date=date(2026, 9, 21))),
    ]
    semanas = cs.summarize_weeks(sessoes, date_from=window.date_from, date_to=window.date_to)
    inicios = [s["week_start"] for s in semanas]
    assert inicios[0] == cardio_adherence.week_start(window.date_from)
    assert inicios[-1] == cardio_adherence.week_start(window.date_to)
    assert all(b - a == timedelta(days=7) for a, b in zip(inicios, inicios[1:]))

    # 20/09 (domingo) e 21/09 (segunda) caem em semanas diferentes.
    dom = next(s for s in semanas if s["week_start"] == date(2026, 9, 14))
    seg = next(s for s in semanas if s["week_start"] == date(2026, 9, 21))
    assert dom["continuous_sessions"] == 1 and dom["high_intensity_sessions"] == 0
    assert seg["high_intensity_sessions"] == 1 and seg["unclassified_sessions"] == 1
    assert seg["sessions"] == 2 and seg["sessions_with_zones"] == 1
    assert seg["zone_minutes"] == cs._zone_minutes(hr_zones.app_zone_seconds(ZONAS_7_INTENSO))
    assert seg["calories_kcal"] == 600

    vazia = semanas[0]
    assert vazia["sessions"] == 0 and vazia["zone_minutes"] is None and vazia["calories_kcal"] is None


def test_sem_intervals_configurado_nao_ha_semanas():
    payload = _build({"FROM users": [{"intervals_athlete_id": None, "intervals_api_key_encrypted": None}]})
    assert payload["weeks"] == []
    assert payload["adherence"] is None  # sem realizado, aderencia mentiria


# ---------------------------------------------------------------------------
# 3) Aderencia, wellness, PAI, proxima sessao
# ---------------------------------------------------------------------------


def test_aderencia_so_rotula_e_converte_minutos():
    data = {
        "sessions": [
            {
                "activity_id": 10,
                "local_date": "2026-09-20",
                "adherence": {
                    "mode": "continuous",
                    "prescription_text": "30 min Z2",
                    "planned_seconds": 1800,
                    "realized_seconds": 1650,
                    "time_completion_pct": 91.7,
                    "has_zone_data": True,
                    "realized": {"match_status": "MATCHED"},
                    "zones": {"target_zone_completion_pct": 75.0},
                    "status": cardio_adherence.STATUS_ADHERENT,
                },
            }
        ],
        "weeks": [
            {
                "week_start": "2026-09-14",
                "planned_sessions": 1,
                "done_sessions": 1,
                "planned_seconds": 1800,
                "realized_seconds": 1650,
                "time_completion_pct": 91.7,
                "by_status": {cardio_adherence.STATUS_ADHERENT: 1},
            }
        ],
        "thresholds": {"adherent_time_min_pct": cardio_adherence.ADHERENT_TIME_MIN_PCT},
    }
    view = cs.adherence_view(data)
    sessao = view["sessions"][0]
    assert sessao["status_label"] == cardio_adherence.STATUS_LABELS[cardio_adherence.STATUS_ADHERENT]
    assert sessao["planned_minutes"] == cardio_prescription.minutes_from_seconds(1800)
    assert sessao["realized_minutes"] == cardio_prescription.minutes_from_seconds(1650)
    assert sessao["zone_completion_pct"] == 75.0 and sessao["time_completion_pct"] == 91.7
    assert view["anchor"] == cardio_adherence.ANCHOR_STRENGTH_END
    assert view["weeks"][0]["realized_minutes"] == cardio_prescription.minutes_from_seconds(1650)


def test_aderencia_do_dia_de_descanso_sai_com_a_ficha_e_a_ancora_e_fecha_no_contrato():
    """27/10/2026: ficha de descanso com cardio, casada pelo dia local inteiro."""
    from app.intelligence.dashboard.contracts import CardioAdherenceOut

    data = {
        "sessions": [
            {
                "activity_id": None,
                "workout_plan_id": 700,
                "anchor": cardio_adherence.ANCHOR_LOCAL_DAY,
                "local_date": "2026-09-21",
                "adherence": {"mode": "continuous", "prescription_text": "20 min", "planned_seconds": 1200,
                              "realized_seconds": None, "has_zone_data": False,
                              "status": cardio_adherence.STATUS_NOT_DONE},
            }
        ],
        "weeks": [],
        "thresholds": {},
    }
    view = cs.adherence_view(data)
    sessao = view["sessions"][0]
    assert sessao["activity_id"] is None and sessao["workout_plan_id"] == 700
    assert sessao["anchor"] == cardio_adherence.ANCHOR_LOCAL_DAY
    assert CardioAdherenceOut.model_validate(view).sessions[0].anchor == cardio_adherence.ANCHOR_LOCAL_DAY


def test_wellness_deriva_tsb_pelo_dono_e_ultimo_valor_por_metrica():
    linhas = [
        {"wellness_date": date(2026, 9, 20), "fitness_ctl": 40, "fatigue_atl": 50, "form_tsb": None,
         "hrv_ms": 45, "resting_hr_bpm": 52},
        {"wellness_date": date(2026, 9, 21), "fitness_ctl": 41, "fatigue_atl": 45, "form_tsb": None,
         "hrv_ms": None, "resting_hr_bpm": 0},
    ]
    w = cs.summarize_wellness(linhas)
    assert w["points"][0]["tsb"] == intervals_store.derive_form_tsb(40.0, 50.0, None)
    assert w["points"][1]["resting_hr_bpm"] is None  # 0 do Intervals nao e medida
    assert w["latest"]["ctl"] == 41.0 and w["latest"]["ctl_date"] == date(2026, 9, 21)
    assert w["latest"]["hrv_ms"] == 45.0 and w["latest"]["hrv_ms_date"] == date(2026, 9, 20)


def test_pai_compara_o_ultimo_dia_estimado_com_a_meta_do_dono():
    abaixo = cardio_policy.PAI_TARGET_MIN - 1
    pai = cs.summarize_pai([
        {"local_date": "2026-09-20", "pai_7d_estimated": cardio_policy.PAI_TARGET_MIN + 5},
        {"local_date": "2026-09-21", "pai_7d_estimated": abaixo},
        {"local_date": "2026-09-22", "pai_7d_estimated": None},  # sem tempo em zona na janela
    ])
    assert pai["target_min"] == cardio_policy.PAI_TARGET_MIN
    assert pai["latest"] == abaixo and pai["latest_below_target"] is True
    assert pai["latest_date"] == date(2026, 9, 21) and len(pai["points"]) == 2
    assert pai["estimated"] is True and pai["model_version"] == cardio_load.CARDIO_LOAD_MODEL_VERSION
    vazio = cs.summarize_pai([])
    assert vazio["latest"] is None and vazio["latest_below_target"] is None


def _recomendacao(*datas):
    sessoes = []
    for dia in datas:
        sessoes.append(
            {
                "date": dia,
                "prescription_text": f"Sessao {dia}",
                "prescription_short": "Z2",
                "total_seconds": 1800,
                "rationale": "porque sim",
                "signals": [],
                "plan_link": None,
                "adjustments": [],
            }
        )
    return {"schema_version": 1, "sessions": sessoes}


def test_proxima_sessao_e_a_primeira_de_hoje_em_diante():
    rec = _recomendacao("2026-09-20", "2026-09-24", "2026-09-22")
    sessao = cardio_recommendation_v3.next_session(rec, DATE_TO)
    assert sessao["date"] == "2026-09-22"
    assert sessao["total_minutes_label"] == cardio_prescription.minutes_label(1800)
    assert "applicable" not in sessao
    assert cardio_recommendation_v3.next_session(_recomendacao("2026-09-20"), DATE_TO) is None
    assert cardio_recommendation_v3.next_session(None, DATE_TO) is None


# ---------------------------------------------------------------------------
# 4) build_cardio de ponta a ponta
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, respostas, falhas):
        self._respostas, self._falhas = respostas, falhas
        self._linhas: list = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        for chave in self._falhas:
            if chave in sql:
                raise RuntimeError("banco fora")
        for chave, linhas in self._respostas.items():
            if chave in sql:
                self._linhas = linhas
                return
        self._linhas = []

    def fetchall(self):
        return list(self._linhas)

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class _Conn:
    def __init__(self, respostas, falhas=()):
        self._respostas, self._falhas = respostas, falhas

    def cursor(self):
        return _Cursor(self._respostas, self._falhas)


CONFIGURADO = [{"intervals_athlete_id": "i1", "intervals_api_key_encrypted": "x"}]


def _build(respostas, falhas=()):
    base = {
        "JOIN workout_plans": [],
        "FROM intervals_wellness": [],
        "hr_zone_seconds IS NOT NULL": [],
        "FROM cardio_recommendations": [],
        "FROM intervals_athlete_hr_zones": [],
        "FROM activities": [],
    }
    return cs.build_cardio(_Conn({**base, **respostas}, falhas), user_id=1, window=_window(), today=DATE_TO)


def test_payload_c4_completo_fecha_no_contrato():
    payload = _build(
        {
            "FROM cardio_recommendations": [
                {"id": 9, "generated_at": datetime(2026, 9, 21, 8, 0), "prompt_version": "cardio-agent-v3",
                 "recommendation_json": json.dumps(_recomendacao("2026-09-23"))}
            ],
            "FROM users": CONFIGURADO,
            "FROM intervals_activities\nWHERE user_id = %s AND activity_date": [
                _atividade(hr_zone_seconds=json.dumps(ZONAS_7_CONTINUO)),
                _atividade(id=2, external_id="i-2"),
            ],
            "MAX(synced_at)": [{"last_synced_at": datetime(2026, 9, 22, 5, 0)}],
        }
    )
    resposta = CardioResponse.model_validate(payload)
    assert resposta.next_session is not None and resposta.next_session.date == "2026-09-23"
    assert resposta.next_session.recommendation_id == 9
    assert resposta.zones is not None and resposta.zones.source == hr_zones.ZoneSource.NONE.value
    assert resposta.pai.target_min == cardio_policy.PAI_TARGET_MIN
    assert resposta.adherence is not None and resposta.adherence.sessions == []
    assert cs.NO_PLANNED_CARDIO_LIMITATION in resposta.limitations
    assert cs.NO_ZONES_LIMITATION in resposta.limitations
    assert cs.CARDIO_WITHOUT_ZONES_LIMITATION_TEMPLATE.format(n=1, total=2) in resposta.limitations
    assert sum(w.sessions for w in resposta.weeks) == 2


def test_bloco_acessorio_que_falha_vira_ressalva_e_o_resto_continua():
    payload = _build(
        {"FROM users": CONFIGURADO, "FROM intervals_activities\nWHERE user_id = %s AND activity_date": [_atividade()]},
        falhas=("FROM intervals_wellness", "FROM cardio_recommendations"),
    )
    CardioResponse.model_validate(payload)
    assert payload["totals"]["sessions"] == 1
    assert payload["wellness"]["points"] == [] and payload["next_session"] is None
    for bloco in ("wellness", "next_session"):
        assert cs.BLOCK_UNAVAILABLE_TEMPLATE.format(bloco=cs.BLOCK_LABELS[bloco]) in payload["limitations"]


def test_relatorio_pula_os_blocos_c4_e_as_consultas_deles():
    executados = []

    class _Registra(_Conn):
        def cursor(self):
            cursor = super().cursor()
            original = cursor.execute

            def execute(sql, params=None):
                executados.append(sql)
                return original(sql, params)

            cursor.execute = execute
            return cursor

    base = {"FROM users": CONFIGURADO, "FROM intervals_activities\nWHERE user_id = %s AND activity_date": [_atividade()]}
    payload = cs.build_cardio(_Registra(base), user_id=1, window=_window(), today=DATE_TO, tracking=False)
    assert payload["weeks"] == [] and payload["adherence"] is None and payload["zones"] is None
    assert payload["totals"]["sessions"] == 1
    for tabela in ("intervals_wellness", "manual_effort_metrics", "cardio_recommendations", "workout_plans"):
        assert not any(tabela in sql for sql in executados), tabela


# ---------------------------------------------------------------------------
# 5) Casamento por dia local (proposta C4, `intervals_hr_match`)
# ---------------------------------------------------------------------------


def _candidato(id_, inicio, segundos):
    return intervals_hr_match.IntervalsCandidate(
        intervals_activity_id=id_, external_id=f"i-{id_}", start_time=inicio, moving_time_seconds=segundos
    )


def test_casamento_por_dia_escolhe_a_mais_longa_e_marca_ambiguidade():
    dia = date(2026, 9, 20)
    candidatos = [
        _candidato(1, datetime(2026, 9, 20, 7, 0), 1200),
        _candidato(2, datetime(2026, 9, 20, 18, 0), 2400),
        _candidato(3, datetime(2026, 9, 21, 7, 0), 3600),  # outro dia
    ]
    r = intervals_hr_match.match_local_day(dia, candidatos)
    assert r.status == intervals_hr_match.STATUS_MATCHED_AMBIGUOUS
    assert r.candidate.intervals_activity_id == 2 and r.candidates_count == 2 and r.delta_seconds is None
    so_um = intervals_hr_match.match_local_day("2026-09-21", candidatos)
    assert so_um.status == intervals_hr_match.STATUS_MATCHED and so_um.candidate.intervals_activity_id == 3
    assert intervals_hr_match.match_local_day(date(2026, 9, 22), candidatos).status == intervals_hr_match.STATUS_NO_CANDIDATE
    assert intervals_hr_match.match_local_day("lixo", candidatos).status == intervals_hr_match.STATUS_NO_START_TIME


# ---------------------------------------------------------------------------
# 6) analytics.py: uma definicao de cardio
# ---------------------------------------------------------------------------


def test_analytics_conta_cardio_pelo_dono():
    # Linhas ja agregadas por (tipo, nome) no SQL.
    linhas = [
        {"activity_type": "musculacao", "name": "Treino A", "activities": 10},
        {"activity_type": "cardio", "name": "Esteira", "activities": 1},
        {"activity_type": "Run", "name": "Corrida", "activities": 3},
        # Forca vence (o SQL antigo contava esta como cardio pelo nome).
        {"activity_type": "musculacao", "name": "Treino + caminhada", "activities": 2},
    ]
    assert analytics.count_cardio_activities(linhas) == 4


def _grupo(period, tipo, nome, inicio, fim, n, dur, kcal, hr_sum, hr_n, hr_max):
    return {"period": period, "activity_type": tipo, "name": nome, "period_start": inicio, "period_end": fim,
            "activities": n, "duration_seconds": dur, "calories_kcal": kcal, "avg_hr_sum": hr_sum,
            "avg_hr_count": hr_n, "max_hr_bpm": hr_max}


def test_analytics_cardio_por_periodo_tem_a_forma_do_sql_antigo():
    linhas = [
        _grupo("2026-W38", "Walk", "Caminhada", "2026-09-14", "2026-09-15", 2, 3600, 300.0, 230.0, 2, 130.0),
        _grupo("2026-W38", "Ride", "Bike", "2026-09-16", "2026-09-16", 1, 0, 0, None, 0, 150.0),
        _grupo("2026-W38", "musculacao", "Treino A", "2026-09-13", "2026-09-16", 3, 9000, 900.0, 360.0, 3, 160.0),
        _grupo("2026-W39", "Run", "Corrida", "2026-09-21", "2026-09-21", 1, 1200, 200.0, 150.0, 1, 170.0),
    ]
    semanas = analytics.summarize_cardio_periods(linhas)
    assert [s["period"] for s in semanas] == ["2026-W38", "2026-W39"]
    assert semanas[0] == {
        "period": "2026-W38",
        "period_start": "2026-09-14",  # a musculacao (13/09) nao conta
        "period_end": "2026-09-16",
        "activities": 3,
        "duration_seconds": 3600.0,
        "calories_kcal": 300.0,
        "avg_hr_bpm": 115.0,  # media das FC medidas: (230) / 2
        "max_hr_bpm": 150.0,
    }


def test_analytics_cardio_nao_usa_like_em_sql(monkeypatch):
    capturado = []

    def fake_fetch_all(sql, params=None):
        capturado.append(sql)
        return []

    monkeypatch.setattr(analytics, "_fetch_all", fake_fetch_all)
    monkeypatch.setattr(analytics, "_days_ago", lambda uid, days: DATE_TO - timedelta(days=days))

    class _Id:
        user_id = 1

    out = analytics.cardio_summary(period="week", days=30, identity=_Id())
    assert out == {"period": "week", "days": 30, "items": []}
    assert capturado and all("LIKE" not in sql.upper() for sql in capturado)


# ---------------------------------------------------------------------------
# 7) Contexto v2 no dia do usuario
# ---------------------------------------------------------------------------


def test_contexto_v2_usa_o_dia_do_usuario_e_nao_curdate(monkeypatch):
    from app.intelligence.cardio_context_service import CardioAgentContextService

    service = CardioAgentContextService()
    capturado = []

    def fake_fetch_all(sql, params=None):
        capturado.append((sql, params))
        return []

    monkeypatch.setattr(service, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(service, "_user_today", lambda user_id: DATE_TO)
    inicio = (DATE_TO - timedelta(days=14)).isoformat()

    service._get_recent_workouts(days=14, user_id=7)
    service._get_intervals_wellness(days=14, user_id=7)
    service._get_effort_estimates(days=14, user_id=7)
    service._get_sleep_metrics(days=14, user_id=7)
    service._get_intervals_wellness(days=14, user_id=None)

    assert all("CURDATE" not in sql for sql, _ in capturado)
    assert capturado[0][1] == (inicio, 7)
    assert capturado[1][1] == (7, inicio)
    # M99: o esforço estimado lê a janela que o motor pede (a de 7 dias do 1º dia).
    leitura = cardio_load.activity_read_start(DATE_TO - timedelta(days=14)).isoformat()
    assert capturado[2][1] == (7, leitura, DATE_TO.isoformat())
    assert capturado[-1][1] == (inicio,)
