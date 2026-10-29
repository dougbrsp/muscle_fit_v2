"""`GET /reports/markdown` sem banco: autenticacao, headers do anexo e
validacao da janela (custom de 7 a 180 dias, sem data futura).

O coletor e trocado por um dublê que registra a janela recebida; os numeros
contra MySQL estao em `tests/integration/dataapi/test_reports_markdown.py`.

Rodar: cd api && python -m pytest tests/unit/test_reports_markdown_endpoint.py
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import reports
from app.security import Identity, get_current_identity
from tests.support.fake_db import NullContext

AGORA = datetime(2026, 10, 1, 14, 30)


@pytest.fixture
def chamadas(monkeypatch):
    registro: list[dict] = []

    def _collect(conn, *, user_id, window, now_local):
        registro.append({"user_id": user_id, "window": window, "now_local": now_local})
        return {
            "generated_at": now_local,
            "timezone": window.timezone,
            "window": {"date_from": window.date_from, "date_to": window.date_to, "days": window.days},
        }

    monkeypatch.setattr(reports, "_connect", lambda: NullContext())
    monkeypatch.setattr(reports, "user_timezone", lambda conn, user_id: "America/Sao_Paulo")
    monkeypatch.setattr(reports, "user_now", lambda tz: AGORA)
    monkeypatch.setattr(reports, "collect_report_data", _collect)
    return registro


def _client(autenticado: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(reports.router)
    if autenticado:
        app.dependency_overrides[get_current_identity] = lambda: Identity(
            user_id=7, role="regular", source="session"
        )
    return TestClient(app, raise_server_exceptions=False)


def test_sem_sessao_responde_401(chamadas):
    resposta = _client(autenticado=False).get("/reports/markdown")
    assert resposta.status_code == 401
    assert chamadas == []


def test_padrao_90_dias_com_headers_de_anexo_markdown(chamadas):
    resposta = _client().get("/reports/markdown")

    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"] == "text/markdown; charset=utf-8"
    assert (
        resposta.headers["content-disposition"]
        == 'attachment; filename="relatorio-musclefit-2026-07-04_2026-10-01.md"'
    )
    assert resposta.headers["cache-control"] == "no-store"
    assert resposta.text.startswith("# Relatório MuscleFit")
    assert "## 1. Treino por grupo muscular" in resposta.text

    janela = chamadas[0]["window"]
    assert chamadas[0]["user_id"] == 7
    assert (janela.date_from, janela.date_to, janela.days) == (date(2026, 7, 4), date(2026, 10, 1), 90)
    assert janela.timezone == "America/Sao_Paulo"


@pytest.mark.parametrize(
    "window, esperado",
    [("30d", (date(2026, 9, 2), 30)), ("180d", (date(2026, 4, 5), 180))],
)
def test_janelas_rolling(chamadas, window, esperado):
    resposta = _client().get("/reports/markdown", params={"window": window})
    assert resposta.status_code == 200, resposta.text
    janela = chamadas[0]["window"]
    assert (janela.date_from, janela.days) == esperado
    assert janela.date_to == date(2026, 10, 1)


@pytest.mark.parametrize("dias", [7, 180])
def test_custom_aceita_os_limites(chamadas, dias):
    inicio = date(2026, 10, 1).toordinal() - dias + 1
    resposta = _client().get(
        "/reports/markdown",
        params={"window": "custom", "date_from": date.fromordinal(inicio).isoformat(), "date_to": "2026-10-01"},
    )
    assert resposta.status_code == 200, resposta.text
    assert chamadas[0]["window"].days == dias


@pytest.mark.parametrize(
    "params, trecho",
    [
        ({"window": "custom", "date_from": "2026-09-26", "date_to": "2026-10-01"}, "entre 7 e 180 dias"),
        ({"window": "custom", "date_from": "2026-04-03", "date_to": "2026-10-01"}, "entre 7 e 180 dias"),
        ({"window": "custom", "date_from": "2026-09-01"}, "data inicial e data final"),
        ({"window": "custom", "date_from": "2026-09-20", "date_to": "2026-09-01"}, "depois da data final"),
        ({"window": "custom", "date_from": "2026-09-20", "date_to": "2026-10-02"}, "futuro"),
    ],
)
def test_custom_fora_das_regras_responde_422(chamadas, params, trecho):
    resposta = _client().get("/reports/markdown", params=params)
    assert resposta.status_code == 422
    assert trecho in resposta.json()["detail"]
    assert chamadas == []


@pytest.mark.parametrize("params", [{"window": "7d"}, {"window": "cycle"}, {"window": "custom", "date_from": "ontem"}])
def test_parametro_invalido_responde_422(chamadas, params):
    assert _client().get("/reports/markdown", params=params).status_code == 422
    assert chamadas == []


def test_rotas_antigas_do_pdf_nao_existem_mais():
    caminhos = {getattr(r, "path", "") for r in reports.router.routes}
    assert caminhos == {"/reports/markdown"}
