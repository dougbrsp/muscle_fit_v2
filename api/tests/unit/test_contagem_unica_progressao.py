"""Contagem unica de progressao (2026-11-01, C12 de `docs/motores/FONTES_DA_VERDADE.md`).

A home ("Estou evoluindo?") e `/dashboard/progressao` precisam mostrar os MESMOS
numeros para o mesmo periodo: ambas leem `comparability.progression_counts` e
`progression_answer` sobre os itens do MESMO lote
(`get_exercises_progression_batch`). Aqui: paridade entre `progression_block`
e o bloco `exercise_progression` da rota por grupo, contagens e agregacao.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import analytics, security
from app.intelligence.dashboard import comparability
from app.intelligence.dashboard.comparability import (
    GROUP_MIN_COMPARABLE_WEIGHT,
    aggregate_group_progression,
    exercise_progression_summary,
    progression_answer,
    progression_counts,
)
from app.intelligence.dashboard.contracts import WindowInfo, WindowKind
from app.intelligence.dashboard.metrics_registry import ExerciseProgressionStatus as S
from app.intelligence.dashboard.overview_service import progression_block

HOJE = date(2026, 10, 2)


def _items(**por_status):
    """`_items(PROGRESSING=3, ...)` -> itens do lote com ids sequenciais."""
    itens = []
    for status, quantidade in por_status.items():
        for _ in range(quantidade):
            itens.append(
                {
                    "exercise_id": len(itens) + 1,
                    "name": f"Ex {len(itens) + 1}",
                    "status": status,
                    "status_reasons": [],
                    "comparable_exposures": 2,
                    "exposures_in_window": 2,
                    "last_local_date": HOJE,
                }
            )
    return itens


# O caso do usuario: "18 de 40 comparaveis progredindo; 4 platô; 9 regredindo".
CASO_USUARIO = dict(
    PROGRESSING=18, STABLE=9, PLATEAU_SUSPECTED=4, REGRESSING=9, NOT_COMPARABLE=3, INSUFFICIENT_DATA=5
)


def _window(days=28):
    return WindowInfo(kind=WindowKind.ROLLING_28D, date_from=date(2026, 9, 5), date_to=HOJE, timezone="UTC", days=days)


def test_contagens_do_dono():
    counts = progression_counts(_items(**CASO_USUARIO))
    assert counts == {
        "progressing": 18,
        "stable": 9,
        "plateau_suspected": 4,
        "regressing": 9,
        "comparable_total": 40,
        "not_comparable": 3,
        "insufficient_data": 5,
    }
    assert progression_answer(counts) == (
        "18 de 40 exercícios comparáveis progredindo; 4 com suspeita de platô e 9 regredindo."
    )


def test_frases_sem_comparaveis():
    assert progression_answer(progression_counts([])) == "Nenhum exercício com série de trabalho no período."
    so_insuficientes = progression_counts(_items(INSUFFICIENT_DATA=2, NOT_COMPARABLE=1))
    assert progression_answer(so_insuficientes) == (
        "3 exercícios no período, nenhum com duas exposições comparáveis ainda."
    )


@pytest.mark.parametrize(
    "por_status",
    [CASO_USUARIO, dict(PROGRESSING=1), dict(STABLE=2, INSUFFICIENT_DATA=4), {}],
)
def test_home_e_progressao_mostram_os_mesmos_numeros(por_status):
    """`progression_block` (home) e `exercise_progression` (tela por grupo) saem
    dos MESMOS itens: contagem e frase identicas."""
    itens = _items(**por_status)
    home = progression_block(itens, truncated=False, exposures_per_exercise=5, window=_window())
    tela = exercise_progression_summary(
        itens, window_days=28, period_start=date(2026, 9, 5), period_end=HOJE
    )

    assert tela["answer"] == home["answer"]
    assert tela["counts"]["comparable_total"] == home["comparable_total"]
    por_codigo = {bloco["status"]: bloco["count"] for bloco in home["counts"]}
    assert tela["counts"]["progressing"] == por_codigo[S.PROGRESSING.value]
    assert tela["counts"]["stable"] == por_codigo[S.STABLE.value]
    assert tela["counts"]["plateau_suspected"] == por_codigo[S.PLATEAU_SUSPECTED.value]
    assert tela["counts"]["regressing"] == por_codigo[S.REGRESSING.value]
    assert tela["counts"]["not_comparable"] == por_codigo[S.NOT_COMPARABLE.value]
    assert tela["counts"]["insufficient_data"] == por_codigo[S.INSUFFICIENT_DATA.value]
    assert home["exercises_total"] == sum(
        tela["counts"][chave] for chave in ("comparable_total", "not_comparable", "insufficient_data")
    )


def test_home_nao_tem_mais_a_regra_da_frase():
    """A frase da home e a do dono: o texto nao mudou."""
    itens = _items(PROGRESSING=2, REGRESSING=1)
    home = progression_block(itens, truncated=False, exposures_per_exercise=5, window=_window())
    assert home["answer"] == "2 de 3 exercícios comparáveis progredindo; 0 com suspeita de platô e 1 regredindo."
    assert home["data_quality"].value == "HIGH"
    assert comparability.MIN_COMPARABLE_FOR_RELIABLE_ANSWER == 3


# ---------------------------------------------------------------------------
# Agregacao por grupo
# ---------------------------------------------------------------------------


def test_agregacao_ponderada_por_grupo():
    veredito = aggregate_group_progression(
        [(S.PROGRESSING, 1.0), (S.REGRESSING, 0.5), (S.PLATEAU_SUSPECTED, 0.25), (S.NOT_COMPARABLE, 1.0)]
    )
    assert veredito["status"] is S.PROGRESSING
    assert veredito["comparable_weight"] == 1.75
    assert veredito["counts"]["comparable_total"] == 3
    assert veredito["counts"]["not_comparable"] == 1
    assert veredito["weights"] == {"progressing": 1.0, "stable": 0.25, "regressing": 0.5}


def test_agregacao_estavel_agrega_platô_e_empate_e_estavel():
    assert aggregate_group_progression([(S.STABLE, 0.4), (S.PLATEAU_SUSPECTED, 0.4), (S.PROGRESSING, 0.5)])[
        "status"
    ] is S.STABLE
    assert aggregate_group_progression([(S.PROGRESSING, 0.5), (S.REGRESSING, 0.5)])["status"] is S.STABLE


def test_agregacao_peso_minimo_e_sem_comparaveis():
    pouco = aggregate_group_progression([(S.PROGRESSING, GROUP_MIN_COMPARABLE_WEIGHT - 0.01)])
    assert pouco["status"] is S.INSUFFICIENT_DATA and pouco["reason"] == "LOW_ACTIVATION"
    limite = aggregate_group_progression([(S.PROGRESSING, 0.2), (S.PROGRESSING, 0.3)])
    assert limite["status"] is S.PROGRESSING and limite["reason"] is None
    vazio = aggregate_group_progression([(S.NOT_COMPARABLE, 1.0)])
    assert vazio["status"] is S.INSUFFICIENT_DATA and vazio["reason"] == "NO_COMPARABLE_EXERCISE"
    assert aggregate_group_progression([])["status"] is S.INSUFFICIENT_DATA


def test_agregacao_aceita_o_valor_textual_do_status():
    assert aggregate_group_progression([("REGRESSING", 1.0)])["status"] is S.REGRESSING


# ---------------------------------------------------------------------------
# Rota: o bloco `exercise_progression` e o status por grupo vêm do mesmo lote
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    chamadas = {}

    def fake_items(user_id, windows, goal, timezone_name=None):
        chamadas["janela"] = (windows["current_from"], windows["current_to"])
        chamadas["goal"] = goal
        return _items(**CASO_USUARIO), chamadas.get("truncado", False)

    monkeypatch.setattr(analytics, "_user_today", lambda user_id: HOJE)
    monkeypatch.setattr(analytics, "_fetch_group_tonnage_rows", lambda *a, **k: [])
    monkeypatch.setattr(analytics, "_fetch_group_e1rm_rows", lambda *a, **k: [])
    monkeypatch.setattr(analytics, "_load_convention_expr", lambda: "NULL")
    monkeypatch.setattr(analytics, "_group_phases", lambda user_id, *_: {})
    monkeypatch.setattr(analytics, "_exercise_progression_items", fake_items)
    monkeypatch.setattr(
        analytics,
        "_fetch_exercise_group_weights",
        lambda user_id, windows: {4: {1: 1.0, 20: 0.5}, 12: {19: 1.0}},
    )
    app = FastAPI()
    app.include_router(analytics.router)
    app.dependency_overrides[security.get_current_identity] = lambda: security.Identity(
        user_id=7, role="regular", source="session"
    )
    test_client = TestClient(app)
    test_client.chamadas = chamadas
    return test_client


def test_rota_devolve_exercise_progression_igual_a_home(client):
    body = client.get("/analytics/dashboard-progression-by-muscle-group?days=28&goal=hypertrophy").json()
    bloco = body["exercise_progression"]
    home = progression_block(_items(**CASO_USUARIO), truncated=False, exposures_per_exercise=5, window=_window())

    assert bloco["answer"] == home["answer"]
    assert bloco["counts"]["comparable_total"] == home["comparable_total"] == 40
    assert bloco["window_days"] == 28
    # janela ATUAL da rota = 28 dias terminando hoje (a padrao da home)
    assert (bloco["period_start"], bloco["period_end"]) == ("2026-09-05", HOJE.isoformat())
    assert client.chamadas["janela"] == (date(2026, 9, 5), HOJE)
    assert client.chamadas["goal"] == "hypertrophy"
    assert body["performance_status_basis"] == "weighted_exercise_progression_status"


def test_rota_status_do_grupo_deriva_dos_exercicios(client, monkeypatch):
    # grupo 4: ex 1 (PROGRESSING, 1,0) + ex 20 (STABLE, 0,5) -> PROGREDINDO
    # grupo 12: ex 19 (STABLE) com 1,0 -> ESTAVEL
    monkeypatch.setattr(
        analytics,
        "_fetch_group_tonnage_rows",
        lambda *a, **k: [
            {"period": "current", "muscle_group_id": gid, "muscle_group_name": f"G{gid}",
             "tonnage_kg": 1000.0, "sets_count": 8, "sessions_count": 4}
            for gid in (4, 12)
        ],
    )
    body = client.get("/analytics/dashboard-progression-by-muscle-group?days=28").json()
    por_grupo = {item["muscle_group_id"]: item for item in body["items"]}
    assert por_grupo[4]["performance_status"] == "PROGREDINDO"
    assert por_grupo[4]["progression_counts"]["progressing"] == 1
    assert por_grupo[4]["progression_counts"]["stable"] == 1
    assert por_grupo[12]["performance_status"] == "ESTAVEL"


def test_rota_janela_custom_usa_o_periodo_pedido(client):
    body = client.get(
        "/analytics/dashboard-progression-by-muscle-group?date_from=2026-09-04&date_to=2026-10-02"
    ).json()
    assert client.chamadas["janela"] == (date(2026, 9, 4), HOJE)
    assert body["exercise_progression"]["window_days"] == 29


# ---------------------------------------------------------------------------
# QA independente: helper que monta o universo da rota + bordas extras
# ---------------------------------------------------------------------------


def test_helper_da_rota_chama_o_mesmo_lote_da_home(monkeypatch):
    """`_exercise_progression_items` usa `get_exercises_progression_batch` com a
    janela atual (datas e dias) e o objetivo pedido, igual a home."""
    import contextlib

    from app.intelligence.dashboard import exposure_service, time_windows

    capturado = {}

    @contextlib.contextmanager
    def fake_connect():
        yield "CONN"

    def fake_batch(conn, *, user_id, window, training_goal=None, **kw):
        capturado.update(conn=conn, user_id=user_id, window=window, goal=training_goal)
        return [{"status": "PROGRESSING"}], False, 5

    monkeypatch.setattr(analytics, "_connect", fake_connect)
    monkeypatch.setattr(exposure_service, "get_exercises_progression_batch", fake_batch)
    monkeypatch.setattr(time_windows, "user_timezone", lambda conn, uid: "America/Sao_Paulo")

    janelas = {"current_from": date(2026, 9, 5), "current_to": HOJE}
    itens = analytics._exercise_progression_items(7, janelas, "strength")

    assert itens == ([{"status": "PROGRESSING"}], False)
    assert capturado["conn"] == "CONN" and capturado["user_id"] == 7 and capturado["goal"] == "strength"
    janela = capturado["window"]
    assert (janela.date_from, janela.date_to, janela.days) == (date(2026, 9, 5), HOJE, 28)
    assert janela.timezone == "America/Sao_Paulo"


def test_agregacao_empate_a_tres_e_peso_zero():
    # Os tres baldes empatados -> ESTAVEL.
    tres = aggregate_group_progression([(S.PROGRESSING, 0.5), (S.STABLE, 0.5), (S.REGRESSING, 0.5)])
    assert tres["status"] is S.STABLE
    # Empate so no topo entre progredindo e regredindo, com estavel menor.
    assert aggregate_group_progression([(S.PROGRESSING, 0.6), (S.REGRESSING, 0.6), (S.STABLE, 0.1)])[
        "status"
    ] is S.STABLE
    # Peso zero/negativo/None conta como comparavel mas nao soma peso.
    zero = aggregate_group_progression([(S.PROGRESSING, 0.0), (S.REGRESSING, -1.0), (S.STABLE, None)])
    assert zero["counts"]["comparable_total"] == 3
    assert zero["comparable_weight"] == 0.0
    assert zero["status"] is S.INSUFFICIENT_DATA and zero["reason"] == "LOW_ACTIVATION"
    # Platô soma no balde estavel e ganha de progredindo menor.
    assert aggregate_group_progression([(S.PLATEAU_SUSPECTED, 0.7), (S.PROGRESSING, 0.5)])["status"] is S.STABLE
    assert aggregate_group_progression([(S.PLATEAU_SUSPECTED, 0.7)])["counts"]["plateau_suspected"] == 1


def test_contagem_ignora_status_desconhecido_e_frase_total_zero_no_resumo():
    assert progression_counts([{"status": "XYZ"}])["comparable_total"] == 0
    resumo = exercise_progression_summary([], window_days=28, period_start=date(2026, 9, 5), period_end=HOJE)
    assert resumo["answer"] == "Nenhum exercício com série de trabalho no período."
    assert resumo["counts"]["comparable_total"] == 0


# ---------------------------------------------------------------------------
# `window` (como no overview) e degradacao do lote de progressao
# ---------------------------------------------------------------------------


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def janela_do_overview(monkeypatch):
    from app import analytics_dashboard
    from app.intelligence.dashboard import time_windows

    monkeypatch.setattr(analytics, "_connect", lambda: _Conn())
    monkeypatch.setattr(time_windows, "user_timezone", lambda conn, user_id: "UTC")
    monkeypatch.setattr(analytics_dashboard, "user_today", lambda tz: HOJE)
    monkeypatch.setattr(analytics_dashboard, "resolve_cycle_length_days", lambda user_id: 14)
    return analytics_dashboard


def test_window_cycle_resolve_as_mesmas_datas_e_contagens_da_home(client, janela_do_overview):
    # a home resolve a janela por `_resolve` (overview) e conta pelos mesmos itens
    resolvida = janela_do_overview._resolve("cycle", None, None, "UTC", 7)
    assert resolvida.days == 14

    body = client.get("/analytics/dashboard-progression-by-muscle-group?window=cycle&days=28").json()

    assert client.chamadas["janela"] == (resolvida.date_from, resolvida.date_to)
    bloco = body["exercise_progression"]
    assert (bloco["period_start"], bloco["period_end"]) == (resolvida.date_from.isoformat(), resolvida.date_to.isoformat())
    assert bloco["window_days"] == 14 == body["days"]
    # janela anterior: mesma duracao, imediatamente antes
    assert body["previous_to"] == (resolvida.date_from.fromordinal(resolvida.date_from.toordinal() - 1)).isoformat()
    assert body["previous_from"] == resolvida.date_from.fromordinal(resolvida.date_from.toordinal() - 14).isoformat()
    home = progression_block(
        _items(**CASO_USUARIO), truncated=False, exposures_per_exercise=5,
        window=WindowInfo(kind=WindowKind.CYCLE, date_from=resolvida.date_from, date_to=resolvida.date_to,
                          timezone="UTC", days=resolvida.days),
    )
    assert bloco["answer"] == home["answer"]
    assert bloco["counts"]["comparable_total"] == home["comparable_total"]


def test_window_invalida_ou_ciclo_sem_modelo_e_400(client, janela_do_overview, monkeypatch):
    assert client.get("/analytics/dashboard-progression-by-muscle-group?window=xyz").status_code == 400
    assert client.get("/analytics/dashboard-progression-by-muscle-group?window=custom").status_code == 400
    monkeypatch.setattr(janela_do_overview, "resolve_cycle_length_days", lambda user_id: None)
    assert client.get("/analytics/dashboard-progression-by-muscle-group?window=cycle").status_code == 400


def test_window_28d_vence_days(client, janela_do_overview):
    body = client.get("/analytics/dashboard-progression-by-muscle-group?window=28d&days=7").json()
    assert body["days"] == 28 and body["exercise_progression"]["window_days"] == 28


def test_falha_do_lote_de_progressao_nao_derruba_a_tonelagem(client, monkeypatch):
    def quebra(*a, **k):
        raise RuntimeError("data api fora")

    monkeypatch.setattr(analytics, "_exercise_progression_items", quebra)
    monkeypatch.setattr(
        analytics,
        "_fetch_group_tonnage_rows",
        lambda *a, **k: [
            {"period": "current", "muscle_group_id": 4, "muscle_group_name": "Quadriceps",
             "tonnage_kg": 1000.0, "sets_count": 8, "sessions_count": 4}
        ],
    )
    resp = client.get("/analytics/dashboard-progression-by-muscle-group?days=28")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exercise_progression"] is None
    (item,) = body["items"]
    assert item["performance_status"] == "DADOS_INSUFICIENTES"
    assert item["current_tonnage_kg"] == 1000.0


def test_truncated_do_lote_chega_ao_bloco_e_bate_com_a_home(client):
    client.chamadas["truncado"] = True
    bloco = client.get("/analytics/dashboard-progression-by-muscle-group?days=28").json()["exercise_progression"]
    assert bloco["truncated"] is True
    # a home expoe o mesmo campo, vindo do mesmo `truncated` do lote
    home = progression_block(_items(**CASO_USUARIO), truncated=True, exposures_per_exercise=5, window=_window())
    assert home["truncated"] is True
    client.chamadas["truncado"] = False
    assert client.get("/analytics/dashboard-progression-by-muscle-group?days=28").json()["exercise_progression"][
        "truncated"
    ] is False


def test_degradacao_declara_degraded_e_motivo_proprio(client, monkeypatch):
    eventos = []
    monkeypatch.setattr(analytics, "log_event", lambda event, **fields: eventos.append(event))

    def quebra(*a, **k):
        raise RuntimeError("data api fora")

    monkeypatch.setattr(analytics, "_fetch_exercise_group_weights", quebra)
    monkeypatch.setattr(
        analytics,
        "_fetch_group_tonnage_rows",
        lambda *a, **k: [
            {"period": "current", "muscle_group_id": 4, "muscle_group_name": "Quadriceps",
             "tonnage_kg": 1000.0, "sets_count": 8, "sessions_count": 4}
        ],
    )
    body = client.get("/analytics/dashboard-progression-by-muscle-group?days=28").json()
    assert body["degraded"] is True
    assert body["exercise_progression"] is None
    (item,) = body["items"]
    assert item["performance_status"] == "DADOS_INSUFICIENTES"
    assert item["performance_insufficient_reason"] == "PROGRESSION_UNAVAILABLE"
    assert eventos == ["progression_batch_degraded"]


def test_sem_falha_degraded_e_false(client):
    body = client.get("/analytics/dashboard-progression-by-muscle-group?days=28").json()
    assert body["degraded"] is False
