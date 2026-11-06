"""Refresh MANUAL do Intervals (`POST /integrations/intervals/sync`, admin) e o registro de execuções (migração 107).

Cobre: grava `ok` (janela buscada e contagem) e `error` (o erro original segue), `source = manual`, e que falhar ao
registrar (sem a tabela, conexão caindo) nunca derruba o refresh.

Rodar: cd api && python -m pytest tests/unit/test_intervals_sync_manual_registro.py
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from app import intervals_integration as ii

JANELA = (date(2026, 10, 7), date(2026, 11, 6))


class _Admin:
    user_id = 1


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        raise AssertionError("o registro foi trocado por um duble neste teste")


@pytest.fixture
def refresh(monkeypatch):
    chamadas: list[dict] = []
    monkeypatch.setattr(ii, "_fetch_live_activities_and_wellness", lambda uid, days: ([{"id": 1}, {"id": 2}], [], JANELA))
    monkeypatch.setattr(ii, "_upsert_activities", lambda uid, items: {"upserted": len(items)})
    monkeypatch.setattr(ii, "_upsert_wellness", lambda uid, items: {"upserted": 0})
    monkeypatch.setattr(ii, "_sync_athlete_hr_zones", lambda uid, elapsed_seconds=0.0: {"upserted": 0, "failed": False})
    monkeypatch.setattr(ii, "_connect", lambda: _Conn())

    def gravar(conn, user_id, **campos):
        chamadas.append({"user_id": user_id, **campos})
        return True

    monkeypatch.setattr(ii.intervals_store, "record_sync_run", gravar)
    return chamadas


def test_refresh_manual_grava_a_execucao_ok_com_a_janela_buscada(refresh):
    out = ii.intervals_sync(days=30, identity=_Admin())

    assert out["status"] == "ok"
    assert len(refresh) == 1
    linha = refresh[0]
    assert linha["user_id"] == 1 and linha["source"] == "manual" and linha["status"] == "ok"
    assert (linha["window_start"], linha["window_end"]) == JANELA and linha["activities_count"] == 2
    assert linha["started_at"] <= linha["finished_at"] and linha["finished_at"].tzinfo is None


def test_falha_do_intervals_grava_error_e_o_erro_original_segue(refresh, monkeypatch):
    def quebra(uid, days):
        raise HTTPException(status_code=502, detail="Intervals fora do ar")

    monkeypatch.setattr(ii, "_fetch_live_activities_and_wellness", quebra)

    with pytest.raises(HTTPException) as erro:
        ii.intervals_sync(days=30, identity=_Admin())

    assert erro.value.status_code == 502
    assert [(c["status"], c["activities_count"], c["source"]) for c in refresh] == [("error", 0, "manual")]


def test_falha_ao_registrar_nunca_derruba_o_refresh(refresh, monkeypatch):
    def sem_banco():
        raise HTTPException(status_code=503, detail="banco indisponivel")

    monkeypatch.setattr(ii, "_connect", sem_banco)

    out = ii.intervals_sync(days=30, identity=_Admin())

    assert out["status"] == "ok" and refresh == []


def test_falha_do_registro_em_si_tambem_e_tolerada(refresh, monkeypatch):
    def quebra(conn, user_id, **campos):
        raise RuntimeError("1146 sem a tabela")

    monkeypatch.setattr(ii.intervals_store, "record_sync_run", quebra)
    assert ii.intervals_sync(days=30, identity=_Admin())["status"] == "ok"


def test_recusa_de_requisicao_nao_vira_execucao_error(refresh, monkeypatch):
    """400 `not_configured` (quem nao tem Intervals) nao e execucao do sync: nada e gravado e o erro segue."""

    def recusa(uid, days):
        raise HTTPException(status_code=400, detail="not_configured")

    monkeypatch.setattr(ii, "_fetch_live_activities_and_wellness", recusa)
    with pytest.raises(HTTPException) as erro:
        ii.intervals_sync(days=30, identity=_Admin())
    assert erro.value.status_code == 400 and refresh == []


def test_falha_5xx_do_intervals_continua_registrada(refresh, monkeypatch):
    def cai(uid, days):
        raise HTTPException(status_code=503, detail="indisponivel")

    monkeypatch.setattr(ii, "_fetch_live_activities_and_wellness", cai)
    with pytest.raises(HTTPException):
        ii.intervals_sync(days=30, identity=_Admin())
    assert [c["status"] for c in refresh] == ["error"]
