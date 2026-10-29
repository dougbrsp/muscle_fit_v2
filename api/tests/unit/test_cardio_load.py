"""Motor do EPOC/PAI de 7 dias estimado (M99, `domain/cardio_load.py`),
leitura (`effort_estimates`, `intervals_store.zone_seconds_rows`) e a rota
`GET /effort-metrics`."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import effort_estimates, effort_metrics, intervals_store
from app.domain import cardio_load
from app.security import Identity, get_current_identity

FIXTURE = Path(__file__).parent / "fixtures" / "cardio_load" / "tres_dias_sinteticos.json"
DIA = date(2026, 8, 10)
MINUTO = cardio_load.SECONDS_PER_MINUTE


def _zonas(**minutos_por_posicao: int) -> list[int]:
    out = [0] * cardio_load.INTERVALS_ZONE_COUNT
    for chave, minutos in minutos_por_posicao.items():
        out[int(chave[1:]) - 1] = minutos * MINUTO
    return out


# --- motor ------------------------------------------------------------------


def test_pesos_nao_negativos_e_nao_decrescentes():
    for pesos in (cardio_load.EPOC_ZONE_WEIGHTS, cardio_load.PAI_ZONE_WEIGHTS):
        assert len(pesos) == cardio_load.INTERVALS_ZONE_COUNT
        assert all(p >= 0 for p in pesos)
        assert all(a <= b for a, b in zip(pesos, pesos[1:]))


def test_janela_sem_tempo_em_zona_e_none():
    [e] = cardio_load.estimate_days([], DIA, DIA)
    assert e.epoc_7d_estimated is None and e.pai_7d_estimated is None
    # Forma inválida, outra quantidade de zonas ou tempo zero também não contam.
    lixo = [(DIA, None), (DIA, [60, 60, 60, 60, 60]), (DIA, [0] * 7), (DIA, "x")]
    [e] = cardio_load.estimate_days(lixo, DIA, DIA)
    assert e.epoc_7d_estimated is None and e.pai_7d_estimated is None


def test_so_z1_da_pai_perto_de_zero():
    [e] = cardio_load.estimate_days([(DIA, _zonas(z1=60))], DIA, DIA)
    assert e.pai_7d_estimated is not None and e.pai_7d_estimated <= 2
    assert e.epoc_7d_estimated is not None and e.epoc_7d_estimated > 0


def test_mesmo_tempo_em_zona_mais_alta_nunca_vale_menos():
    anteriores = (-1, -1)
    for posicao in range(1, cardio_load.INTERVALS_ZONE_COUNT + 1):
        [e] = cardio_load.estimate_days([(DIA, _zonas(**{f"z{posicao}": 30}))], DIA, DIA)
        assert e.epoc_7d_estimated >= anteriores[0] and e.pai_7d_estimated >= anteriores[1]
        anteriores = (e.epoc_7d_estimated, e.pai_7d_estimated)


def test_janela_de_7_dias_inclui_o_dia_e_os_6_anteriores():
    janela = cardio_load.ROLLING_WINDOW_DAYS
    dentro = DIA - timedelta(days=janela - 1)
    fora = DIA - timedelta(days=janela)
    assert cardio_load.activity_read_start(DIA) == dentro
    atividades = [(dentro, _zonas(z4=10)), (fora, _zonas(z4=100))]
    [e] = cardio_load.estimate_days(atividades, DIA, DIA)
    [so_dentro] = cardio_load.estimate_days([(dentro, _zonas(z4=10))], DIA, DIA)
    assert e == so_dentro
    # No dia seguinte a atividade de `dentro` já saiu da janela.
    [depois] = cardio_load.estimate_days(atividades, DIA + timedelta(days=1), DIA + timedelta(days=1))
    assert depois.epoc_7d_estimated is None


def test_arredonda_so_no_fim_e_soma_atividades_do_dia():
    um_minuto_z2 = _zonas(z2=1)
    [e] = cardio_load.estimate_days([(DIA, um_minuto_z2), (DIA, um_minuto_z2)], DIA, DIA)
    assert e.epoc_7d_estimated == round(2 * cardio_load.EPOC_ZONE_WEIGHTS[1])
    assert e.model_version == cardio_load.CARDIO_LOAD_MODEL_VERSION


def test_tres_dias_sinteticos_batem_com_o_congelado_e_ficam_perto_do_relogio():
    # Fixture sintetica (repositorio publico): o "relogio" e o estimado com
    # ruido; a validacao contra o relogio real esta no PR do M99.
    fx =json.loads(FIXTURE.read_text(encoding="utf-8"))
    atividades = intervals_store.zone_seconds_rows(fx["atividades"])
    primeiro = date.fromisoformat(fx["relogio"][0]["local_date"])
    ultimo = date.fromisoformat(fx["relogio"][-1]["local_date"])
    assert min(d for d, _ in atividades) == cardio_load.activity_read_start(primeiro)
    estimado = [e.as_dict() for e in cardio_load.estimate_days(atividades, primeiro, ultimo)]
    congelado = fx["estimado_cardio_load_v1_7d"]
    assert [
        {k: e[k] for k in ("local_date", "epoc_7d_estimated", "pai_7d_estimated")} for e in estimado
    ] == congelado
    for e, real in zip(estimado, fx["relogio"]):
        assert abs(e["epoc_7d_estimated"] - real["epoc"]) / real["epoc"] < 0.15
        assert abs(e["pai_7d_estimated"] - real["pai"]) / real["pai"] < 0.15


# --- leitura ----------------------------------------------------------------


def test_zone_seconds_rows_aceita_json_em_texto_e_data_em_datetime():
    linhas = [
        {"activity_date": "2026-08-10", "hr_zone_seconds": "[60, 0, 0, 0, 0, 0, 0]"},
        {"activity_date": date(2026, 8, 11), "hr_zone_seconds": [1, 2, 3, 4, 5, 6, 7]},
        {"activity_date": None, "hr_zone_seconds": "[1]"},
    ]
    assert intervals_store.zone_seconds_rows(linhas) == [
        (date(2026, 8, 10), [60, 0, 0, 0, 0, 0, 0]),
        (date(2026, 8, 11), [1, 2, 3, 4, 5, 6, 7]),
    ]


def test_load_recent_pede_a_janela_do_motor_e_devolve_so_dias_com_valor():
    chamadas = []

    def fetch(sql, params):
        chamadas.append((sql, params))
        return [{"activity_date": "2026-08-04", "hr_zone_seconds": json.dumps(_zonas(z2=10))}]

    itens = effort_estimates.load_recent(fetch, 1, DIA, 10)
    primeiro = DIA - timedelta(days=9)
    assert chamadas == [
        (intervals_store.ZONE_SECONDS_SQL, (1, cardio_load.activity_read_start(primeiro).isoformat(), DIA.isoformat()))
    ]
    # A atividade de 04/08 vale de 04/08 a 10/08; do mais recente para o mais antigo.
    assert [i["local_date"] for i in itens] == [(DIA - timedelta(days=k)).isoformat() for k in range(7)]
    assert {i["model_version"] for i in itens} == {cardio_load.CARDIO_LOAD_MODEL_VERSION}


# --- rota -------------------------------------------------------------------


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._linhas: list = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))
        if "FROM users" in sql:
            self._linhas = [{"timezone": "America/Sao_Paulo"}]
        else:
            self._linhas = [{"activity_date": DIA.isoformat(), "hr_zone_seconds": json.dumps(_zonas(z3=20))}]

    def fetchall(self):
        return self._linhas

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class _Conn:
    def __init__(self):
        self.statements: list = []
        self.closed = False

    def cursor(self):
        return _Cursor(self)

    def close(self):
        self.closed = True


@pytest.fixture()
def cliente(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(effort_metrics, "get_conn", lambda: conn)
    monkeypatch.setattr(effort_metrics, "user_today_for", lambda _conn, _uid: DIA)
    app = FastAPI()
    app.include_router(effort_metrics.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=7, role="regular", source="session")
    return TestClient(app), conn


def test_rota_devolve_estimativa_pronta_do_usuario(cliente):
    client, conn = cliente
    resp = client.get("/effort-metrics", params={"days": 3})
    assert resp.status_code == 200, resp.text
    itens = resp.json()["items"]
    [esperado] = cardio_load.estimate_days([(DIA, _zonas(z3=20))], DIA, DIA)
    assert itens[0] == esperado.as_dict()
    assert [i["local_date"] for i in itens] == ["2026-08-10"]  # só os dias com valor
    sql, params = conn.statements[-1]
    assert sql == intervals_store.ZONE_SECONDS_SQL and params[0] == 7
    assert conn.closed


def test_rota_sem_escrita_e_valida_days(cliente):
    client, _ = cliente
    assert client.post("/effort-metrics", json={"local_date": "2026-08-10"}).status_code == 405
    assert client.get("/effort-metrics", params={"days": 0}).status_code == 422
    assert client.get("/effort-metrics", params={"days": effort_metrics.MAX_DAYS + 1}).status_code == 422


# --- consumidores -------------------------------------------------------------


def _fetch_uma_atividade(sql, params=None):
    if sql == intervals_store.ZONE_SECONDS_SQL:
        return [{"activity_date": DIA.isoformat(), "hr_zone_seconds": json.dumps(_zonas(z2=30))}]
    return []


def test_contexto_v3_traz_o_estimado_em_ordem_crescente_sem_a_versao():
    from app.intelligence.cardio_context_v3 import CardioAgentContextV3Service

    service = CardioAgentContextV3Service()
    service.fetch_all = _fetch_uma_atividade
    itens = service._effort_estimates(7, DIA - timedelta(days=2), DIA + timedelta(days=1))
    [esperado] = cardio_load.estimate_days([(DIA, _zonas(z2=30))], DIA, DIA)
    assert [i["local_date"] for i in itens] == [DIA.isoformat(), (DIA + timedelta(days=1)).isoformat()]
    assert itens[0] == {
        "local_date": DIA.isoformat(),
        "epoc_7d_estimated": esperado.epoc_7d_estimated,
        "pai_7d_estimated": esperado.pai_7d_estimated,
    }


def test_contexto_v1_e_relatorio_trocam_o_manual_pelo_estimado(monkeypatch):
    from app.intelligence.cardio_context_service import CardioAgentContextService

    service = CardioAgentContextService()
    monkeypatch.setattr(service, "fetch_all", _fetch_uma_atividade)
    monkeypatch.setattr(service, "_user_today", lambda _uid: DIA)
    itens = service._get_effort_estimates(days=3, user_id=7)
    assert itens[0]["local_date"] == DIA.isoformat()
    assert itens[0]["model_version"] == cardio_load.CARDIO_LOAD_MODEL_VERSION
    assert service._get_effort_estimates(days=3, user_id=None) == []
