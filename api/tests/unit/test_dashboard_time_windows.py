"""Janelas de tempo do Novo Dashboard no fuso do usuario (F0.1, plano §9).

Sem banco e sem relogio do sistema: toda data entra por parametro.
Rodar: cd api && python -m pytest tests/unit/test_dashboard_time_windows.py
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from app.intelligence.dashboard import time_windows as tw
from app.intelligence.dashboard.contracts import WindowKind

HOJE = date(2026, 9, 22)


# ---------------------------------------------------------------------------
# resolve_window
# ---------------------------------------------------------------------------


def test_7d_e_rolling_de_7_dias_terminando_hoje_inclusive():
    window = tw.resolve_window("7d", today=HOJE)
    assert window.kind is WindowKind.ROLLING_7D
    assert window.date_from == date(2026, 9, 16)
    assert window.date_to == HOJE
    assert window.days == 7
    assert (window.date_to - window.date_from).days + 1 == 7


@pytest.mark.parametrize(
    "kind, days, esperado_from",
    [
        ("28d", 28, date(2026, 8, 26)),
        ("90d", 90, date(2026, 6, 25)),
        ("180d", 180, date(2026, 3, 27)),
        (WindowKind.ROLLING_7D, 7, date(2026, 9, 16)),  # aceita o enum tambem
    ],
)
def test_rolling_windows(kind, days, esperado_from):
    window = tw.resolve_window(kind, today=HOJE)
    assert window.days == days
    assert window.date_from == esperado_from
    assert window.date_to == HOJE


def test_timezone_e_carregado_no_window_info():
    window = tw.resolve_window("7d", today=HOJE, timezone_name="America/Manaus")
    assert window.timezone == "America/Manaus"
    assert json.loads(window.model_dump_json())["kind"] == "7d"


def test_custom_exige_as_duas_datas():
    with pytest.raises(ValueError, match="date_from e date_to"):
        tw.resolve_window("custom", today=HOJE, date_from=date(2026, 9, 1))
    with pytest.raises(ValueError, match="date_from e date_to"):
        tw.resolve_window("custom", today=HOJE, date_to=date(2026, 9, 1))


def test_custom_inclusivo_e_ate_365_dias():
    window = tw.resolve_window("custom", today=HOJE, date_from=date(2026, 9, 1), date_to=date(2026, 9, 10))
    assert window.days == 10
    um_dia = tw.resolve_window("custom", today=HOJE, date_from=HOJE, date_to=HOJE)
    assert um_dia.days == 1
    limite = tw.resolve_window("custom", today=HOJE, date_from=date(2025, 9, 23), date_to=HOJE)
    assert limite.days == 365


def test_custom_maior_que_365_dias_falha():
    with pytest.raises(ValueError, match="365"):
        tw.resolve_window("custom", today=HOJE, date_from=date(2025, 9, 22), date_to=HOJE)


def test_custom_com_datas_invertidas_falha():
    with pytest.raises(ValueError, match="date_from"):
        tw.resolve_window("custom", today=HOJE, date_from=HOJE, date_to=date(2026, 9, 1))


def test_cycle_exige_tamanho_do_ciclo():
    with pytest.raises(ValueError, match="cycle_length_days"):
        tw.resolve_window("cycle", today=HOJE)


@pytest.mark.parametrize("ruim", [0, -3, 366, 2.5, True, "8"])
def test_cycle_rejeita_tamanho_invalido(ruim):
    with pytest.raises(ValueError, match="cycle_length_days"):
        tw.resolve_window("cycle", today=HOJE, cycle_length_days=ruim)


def test_cycle_de_8_dias_termina_hoje():
    window = tw.resolve_window("cycle", today=HOJE, cycle_length_days=8)
    assert window.kind is WindowKind.CYCLE
    assert window.days == 8
    assert window.date_from == date(2026, 9, 15)
    assert window.date_to == HOJE


def test_kind_desconhecida_falha_com_lista_de_opcoes():
    with pytest.raises(ValueError, match="window invalida") as exc:
        tw.resolve_window("fortnight", today=HOJE)
    assert "7d" in str(exc.value) and "custom" in str(exc.value)


def test_modulo_nao_consulta_o_relogio_nem_curdate():
    # so o CODIGO (depois do docstring do modulo, que explica o porque)
    source = open(tw.__file__, encoding="utf-8").read().split('"""', 2)[2]
    assert "CURDATE" not in source
    assert "date.today()" not in source
    # so `user_today` chama datetime.now, e apenas como default de now_utc
    assert source.count("datetime.now(") == 1


# ---------------------------------------------------------------------------
# user_today
# ---------------------------------------------------------------------------


def test_user_today_respeita_o_fuso_do_usuario():
    instante = datetime(2026, 9, 22, 2, 0, tzinfo=timezone.utc)
    assert tw.user_today("America/Sao_Paulo", instante) == date(2026, 9, 21)
    assert tw.user_today("UTC", instante) == date(2026, 9, 22)
    assert tw.user_today("Asia/Tokyo", instante) == date(2026, 9, 22)


def test_user_today_entre_21h_e_meia_noite_em_brasilia_ainda_e_o_dia_anterior():
    """Contrato do flake do CI (PR #197, runner em UTC as 00:15): para o
    usuario em Sao Paulo ainda sao 21:15 do dia anterior. Teste e servico
    tem de pegar "hoje" do MESMO helper (`tests/support/relogio_usuario`)."""
    instante = datetime(2026, 9, 24, 0, 15, tzinfo=timezone.utc)
    assert tw.user_today("America/Sao_Paulo", instante) == date(2026, 9, 23)
    assert tw.user_now("America/Sao_Paulo", instante) == datetime(2026, 9, 23, 21, 15)


def test_user_today_naive_e_interpretado_como_utc():
    naive = datetime(2026, 9, 22, 2, 0)
    assert tw.user_today("America/Sao_Paulo", naive) == date(2026, 9, 21)


def test_user_today_sem_now_usa_o_relogio_atual():
    hoje = tw.user_today("UTC")
    assert isinstance(hoje, date)
    assert abs((hoje - datetime.now(timezone.utc).date()).days) <= 1


def test_user_today_com_fuso_invalido_cai_em_sao_paulo_e_loga(monkeypatch):
    eventos: list[tuple[str, dict]] = []
    monkeypatch.setattr(tw, "log_event", lambda event, **fields: eventos.append((event, fields)))

    instante = datetime(2026, 9, 22, 2, 0, tzinfo=timezone.utc)
    assert tw.user_today("Marte/Olympus", instante) == date(2026, 9, 21)
    assert eventos == [
        ("dashboard.timezone_invalid", {"timezone": "Marte/Olympus", "fallback": "America/Sao_Paulo"})
    ]


@pytest.mark.parametrize("ruim", ["", None, "   "])
def test_user_today_com_nome_vazio_tambem_cai_no_default(monkeypatch, ruim):
    monkeypatch.setattr(tw, "log_event", lambda *a, **k: None)
    instante = datetime(2026, 9, 22, 2, 0, tzinfo=timezone.utc)
    assert tw.user_today(ruim, instante) == date(2026, 9, 21)
