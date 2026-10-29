"""Leitura da aderência do cardio (M98 C2): `cardio_adherence_service` e
`GET /integrations/intervals/cardio-adherence`.

Cenário sintético baseado em caso real: caminhada até a academia antes da
força, força 06:45–08:25, esteira (VirtualRun) às 08:29 e caminhada de volta
às 08:57. O casamento é o de `intervals_hr_match` ancorado no FIM da
força: a esteira vence (MATCHED_AMBIGUOUS, 2 candidatos na janela); a
caminhada de ida fica fora da janela; a força (WeightTraining) e a escalada
não são cardio para `intervals_activity_kind`.

Rodar: cd api && python -m pytest tests/unit/test_cardio_adherence_service.py
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import cardio_adherence_service as svc
from app import intervals_integration
from app.domain import cardio_adherence as ca
from app.security import Identity, get_current_identity

SESSAO = {
    "activity_id": 500,
    "local_date": date(2026, 10, 26),
    "started_at": datetime(2026, 10, 26, 6, 45, 55),
    "ended_at": datetime(2026, 10, 26, 8, 25, 11),
    "template_id": 7,
    "template_slot_sequence": 2,
    "item_id": 900,
    "item_kind": "cardio",
    "cardio_mode": None,  # item antigo (só duração)
    "cardio_duration_minutes": 20,
}


def _atividade(id_, sport, inicio, moving, zonas=None, name="x"):
    return {
        "id": id_,
        "external_id": f"e{id_}",
        "activity_date": "2026-10-26",
        "start_time": inicio,
        "name": name,
        "sport_type": sport,
        "moving_time_seconds": moving,
        "avg_hr_bpm": None,
        "max_hr_bpm": None,
        "calories_kcal": None,
        "training_load": None,
        "hr_zone_seconds": zonas,
    }


ATIVIDADES = [
    _atividade(1, "Walk", "2026-10-26 06:27:39", 800, "[760,40,0,0,0,0,0]"),
    _atividade(2, "WeightTraining", "2026-10-26 06:45:00", 6000, "[100,0,0,0,0,0,0]"),
    _atividade(3, "VirtualRun", "2026-10-26 08:29:31", 1400, "[500,200,180,170,100,130,120]"),
    _atividade(4, "Walk", "2026-10-26 08:57:48", 720, "[720,0,0,0,0,0,0]"),
    _atividade(5, "RockClimbing", "2026-10-26 08:26:00", 600, "[600,0,0,0,0,0,0]"),
]


class _Cursor:
    def __init__(self, sessoes, atividades, descansos=()):
        self.sessoes = sessoes
        self.atividades = atividades
        self.descansos = list(descansos)
        self.executed: list[tuple[str, tuple]] = []
        self._rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.executed.append((flat, params))
        if "FROM workout_plans wp" in flat:
            self._rows = [dict(d) for d in self.descansos]
        elif "FROM activities a" in flat:
            self._rows = [dict(s) for s in self.sessoes]
        elif "FROM intervals_activities" in flat:
            self._rows = [dict(a) for a in self.atividades]
        elif flat.startswith("SELECT timezone FROM users"):
            self._rows = [{"timezone": "America/Sao_Paulo"}]
        else:  # pragma: no cover
            raise AssertionError(flat)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_casa_a_esteira_depois_da_forca_e_avalia():
    cur = _Cursor([SESSAO], ATIVIDADES)
    out = svc.build_adherence(cur, 1, date(2026, 10, 20), date(2026, 10, 26))

    (sessao,) = out["sessions"]
    adesao = sessao["adherence"]
    assert adesao["realized"]["intervals_activity_id"] == 3
    assert adesao["realized"]["match_status"] == "MATCHED_AMBIGUOUS"
    assert adesao["planned_seconds"] == 1200 and adesao["realized_seconds"] == 1400
    assert adesao["realized"]["zone_seconds"] == {"Z1": 500, "Z2": 200, "Z3": 180, "Z4": 170, "Z5": 350}
    assert adesao["status"] == ca.STATUS_ADHERENT  # item antigo: sem zona-alvo, só o tempo
    assert out["weeks"][0]["week_start"] == "2026-10-26"
    # Nada de LIKE '%cardio%' em SQL: o filtro de cardio é do dono, em Python.
    assert all("LIKE" not in sql.upper() for sql, _ in cur.executed)


def test_sem_cardio_realizado_perto_da_forca_e_nao_feito():
    cur = _Cursor([SESSAO], [ATIVIDADES[0], ATIVIDADES[1]])
    out = svc.build_adherence(cur, 1, date(2026, 10, 20), date(2026, 10, 26))
    assert out["sessions"][0]["adherence"]["status"] == ca.STATUS_NOT_DONE


def test_sem_sessao_com_cardio_nao_le_o_intervals():
    cur = _Cursor([], ATIVIDADES)
    out = svc.build_adherence(cur, 1, date(2026, 10, 20), date(2026, 10, 26))
    assert out["sessions"] == [] and out["weeks"] == []
    assert len(cur.executed) == 2  # força + fichas de descanso; o Intervals não


def test_slot_com_dois_cardios_avalia_o_primeiro():
    segundo = {**SESSAO, "item_id": 901, "cardio_duration_minutes": 40}
    cur = _Cursor([SESSAO, segundo], ATIVIDADES)
    out = svc.build_adherence(cur, 1, date(2026, 10, 20), date(2026, 10, 26))
    assert [s["item_id"] for s in out["sessions"]] == [900]


def _client(monkeypatch, cur):
    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return cur

    monkeypatch.setattr(intervals_integration, "_connect", lambda: _Conn())
    app = FastAPI()
    app.include_router(intervals_integration.router)
    identity = Identity(user_id=1, role="regular", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identity
    return TestClient(app)


def test_rota_janela_custom_no_fuso_do_usuario(monkeypatch):
    cur = _Cursor([SESSAO], ATIVIDADES)
    client = _client(monkeypatch, cur)
    resp = client.get(
        "/integrations/intervals/cardio-adherence",
        params={"window": "custom", "date_from": "2026-10-20", "date_to": "2026-10-26"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["timezone"] == "America/Sao_Paulo" and body["window"] == "custom"
    assert body["sessions"][0]["adherence"]["status"] == ca.STATUS_ADHERENT
    sessoes_sql = [p for sql, p in cur.executed if sql.startswith("SELECT a.id AS activity_id")][0]
    assert sessoes_sql == ("cardio", 1, "2026-10-20", "2026-10-26")
    descanso_sql = [p for sql, p in cur.executed if "FROM workout_plans wp" in sql][0]
    assert descanso_sql == ("cardio", 1, "2026-10-20", "2026-10-26", "descanso")


def test_rota_janela_invalida_e_400(monkeypatch):
    client = _client(monkeypatch, _Cursor([], []))
    resp = client.get("/integrations/intervals/cardio-adherence", params={"window": "custom"})
    assert resp.status_code == 400


# --- Dia de descanso com cardio (decisão do usuário de 27/10/2026) ---------

DESCANSO = {
    "workout_plan_id": 700,
    "local_date": date(2026, 10, 25),
    "template_id": 7,
    "template_slot_sequence": 4,
    "item_id": 950,
    "item_kind": "cardio",
    "cardio_mode": "continuous",
    "cardio_duration_minutes": 20,
}


def _no_dia(id_, sport, inicio, moving, zonas=None):
    return {**_atividade(id_, sport, inicio, moving, zonas), "activity_date": inicio[:10]}


def test_descanso_casa_pelo_dia_local_inteiro():
    cur = _Cursor([], [_no_dia(10, "Run", "2026-10-25 17:10:00", 1500, "[100,1300,100,0,0,0,0]")], [DESCANSO])
    out = svc.build_adherence(cur, 1, date(2026, 10, 20), date(2026, 10, 26))
    (sessao,) = out["sessions"]
    assert sessao["activity_id"] is None and sessao["workout_plan_id"] == 700
    assert sessao["anchor"] == ca.ANCHOR_LOCAL_DAY and sessao["local_date"] == "2026-10-25"
    assert sessao["adherence"]["realized"]["intervals_activity_id"] == 10
    assert sessao["adherence"]["realized"]["match_status"] == "MATCHED"
    assert sessao["adherence"]["status"] == ca.STATUS_ADHERENT
    assert out["weeks"][0]["planned_sessions"] == 1


def test_descanso_com_dois_cardios_vale_o_mais_longo():
    atividades = [
        _no_dia(10, "Walk", "2026-10-25 08:00:00", 600),
        _no_dia(11, "Run", "2026-10-25 18:00:00", 1500),
    ]
    out = svc.build_adherence(_Cursor([], atividades, [DESCANSO]), 1, date(2026, 10, 20), date(2026, 10, 26))
    realizado = out["sessions"][0]["adherence"]["realized"]
    assert realizado["intervals_activity_id"] == 11 and realizado["match_status"] == "MATCHED_AMBIGUOUS"


def test_descanso_nao_rouba_o_cardio_casado_com_a_forca_da_vespera():
    forca = {**SESSAO, "activity_id": 501, "local_date": date(2026, 10, 24),
             "started_at": datetime(2026, 10, 24, 22, 0), "ended_at": datetime(2026, 10, 24, 23, 40)}
    madrugada = _no_dia(12, "Run", "2026-10-25 00:05:00", 1200)
    out = svc.build_adherence(_Cursor([forca], [madrugada], [DESCANSO]), 1, date(2026, 10, 20), date(2026, 10, 26))
    por_ancora = {s["anchor"]: s for s in out["sessions"]}
    assert por_ancora[ca.ANCHOR_STRENGTH_END]["adherence"]["realized"]["intervals_activity_id"] == 12
    assert por_ancora[ca.ANCHOR_LOCAL_DAY]["adherence"]["status"] == ca.STATUS_NOT_DONE
    assert [s["local_date"] for s in out["sessions"]] == ["2026-10-24", "2026-10-25"]


def test_descanso_de_hoje_so_conta_depois_de_feito_e_futuro_nunca():
    hoje = {**DESCANSO, "workout_plan_id": 701, "local_date": date(2026, 10, 26)}
    amanha = {**DESCANSO, "workout_plan_id": 702, "local_date": date(2026, 10, 27)}
    cur = _Cursor([], [], [hoje, amanha])
    out = svc.build_adherence(cur, 1, date(2026, 10, 20), date(2026, 10, 27), today=date(2026, 10, 26))
    assert out["sessions"] == []
    feito = _no_dia(13, "Run", "2026-10-26 07:00:00", 1200)
    out = svc.build_adherence(_Cursor([], [feito], [hoje, amanha]), 1, date(2026, 10, 20), date(2026, 10, 27),
                              today=date(2026, 10, 26))
    assert [s["workout_plan_id"] for s in out["sessions"]] == [701]


def test_descanso_sem_today_usa_o_fim_da_janela():
    hoje = {**DESCANSO, "local_date": date(2026, 10, 26)}
    out = svc.build_adherence(_Cursor([], [], [hoje]), 1, date(2026, 10, 20), date(2026, 10, 26))
    assert out["sessions"] == []


def test_sql_do_descanso_le_a_ficha_mais_recente_sem_sessao_de_forca():
    sql = " ".join(svc.REST_DAY_PLANS_WITH_CARDIO_SQL.split())
    assert "s.slot_type = %s" in sql
    assert "wp.id = (SELECT MAX(wl.id) FROM workout_plans wl" in sql
    assert "NOT EXISTS (SELECT 1 FROM activities a WHERE a.user_id = wp.user_id AND a.local_date = wp.plan_date)" in sql
