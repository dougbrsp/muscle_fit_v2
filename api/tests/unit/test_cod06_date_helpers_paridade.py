"""COD-06: paridade das copias de utilitarios de data unificadas.

Cada `_ref_*` abaixo e o corpo ORIGINAL (copiado antes da unificacao) da
funcao que virou alias de `app/date_helpers.py` ou de um dono de motor. A
tabela de entradas cobre os tipos que os dois backends devolvem (objeto do
PyMySQL, string ISO do shim da RDS Data API) e os casos de borda que separam
as variantes (`None`, `""`, espaco, `time`, formato BR, lixo). Se alguma
saida divergir, a unificacao mudou comportamento.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from app import date_helpers, equipment_exercise_preferences, progression, template_assignments
from app.domain import progression_engine, progression_evaluator
from app.intelligence import wellness_freshness
from app.intelligence.dashboard import coercion, time_windows

ENTRADAS = [
    None,
    "",
    "   ",
    "2026-10-01",
    " 2026-10-01",
    "2026-10-01 ",
    "2026-10-01T23:30:00",
    "2026-10-01 23:30:00",
    "2026-10-01T23:30:00+00:00",
    "01/10/2026",
    "2026-13-01",
    "abc",
    date(2026, 10, 1),
    datetime(2026, 10, 1, 23, 30),
    datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc),
    time(7, 15),
    20261001,
    Decimal("1"),
    0,
]


def _ref_to_date(value):
    """`coercion.to_date` e `template_assignments._as_date` (identicas)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _ref_wellness_as_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _ref_iso(value):
    """`_iso` de `progression` e `equipment_exercise_preferences` (identicas)."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _ref_progression_as_date(value):
    """`_as_date` de `progression_engine` e `progression_evaluator` (identicas)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


@pytest.mark.parametrize("valor", ENTRADAS, ids=repr)
def test_to_date_igual_as_duas_copias_originais(valor):
    esperado = _ref_to_date(valor)
    assert date_helpers.to_date(valor) == esperado
    assert coercion.to_date(valor) == esperado
    assert template_assignments._as_date(valor) == esperado


@pytest.mark.parametrize("valor", ENTRADAS, ids=repr)
def test_wellness_mantem_o_strip_que_so_ela_tinha(valor):
    assert wellness_freshness._as_date(valor) == _ref_wellness_as_date(valor)


def test_divergencia_registrada_wellness_le_texto_com_espaco_e_to_date_nao():
    assert wellness_freshness._as_date(" 2026-10-01") == date(2026, 10, 1)
    assert date_helpers.to_date(" 2026-10-01") is None


@pytest.mark.parametrize("valor", ENTRADAS, ids=repr)
def test_iso_igual_a_copia_original(valor):
    esperado = _ref_iso(valor)
    assert date_helpers.isoformat_or_value(valor) == esperado
    assert progression._iso(valor) == esperado
    assert equipment_exercise_preferences._iso(valor) == esperado


@pytest.mark.parametrize("valor", ENTRADAS, ids=repr)
def test_as_date_da_progressao_vem_do_motor_dono(valor):
    assert progression_evaluator._as_date is progression_engine._as_date
    assert progression_engine._as_date(valor) == _ref_progression_as_date(valor)


class _Cursor:
    def __init__(self, timezone_name):
        self.timezone_name = timezone_name

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return {"timezone": self.timezone_name}


class _Conn:
    def __init__(self, timezone_name):
        self.timezone_name = timezone_name

    def cursor(self):
        return _Cursor(self.timezone_name)


@pytest.mark.parametrize("fuso", ["America/Sao_Paulo", "Asia/Tokyo", "Fuso/Invalido", None])
def test_user_today_for_e_a_composicao_que_os_chamadores_repetiam(fuso):
    conn = _Conn(fuso)
    esperado = time_windows.user_today(time_windows.user_timezone(conn, 1))
    assert time_windows.user_today_for(conn, 1) == esperado


def test_user_today_for_segue_o_monkeypatch_do_modulo(monkeypatch):
    """Quem congela `time_windows.user_today`/`user_timezone` (ex.:
    test_progressao_tela_mesma_regra) continua valendo para os chamadores."""
    monkeypatch.setattr(time_windows, "user_timezone", lambda conn, user_id: "UTC")
    monkeypatch.setattr(time_windows, "user_today", lambda tz: date(2030, 1, 2) if tz == "UTC" else None)
    assert time_windows.user_today_for(object(), 1) == date(2030, 1, 2)
