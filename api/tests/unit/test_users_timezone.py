"""`users.timezone` na API (F0.1, migracao 044): validacao no `PUT /users/me`,
exposicao em `GET /users/me` (`_public_row`) e `GET /auth/me` (`_public_user`).

Sem banco.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app import auth, users
from app.timezones import DEFAULT_TIMEZONE, is_valid_timezone, normalize_timezone


def _row(**overrides) -> dict:
    row = {
        "id": 1,
        "username": "doug",
        "password_hash": "x",
        "full_name": "Doug",
        "role": "admin",
        "photo_path": None,
        "is_advanced_user": 0,
        "must_change_password": 0,
        "intervals_athlete_id": None,
        "intervals_api_key_encrypted": None,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# app/timezones.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["America/Sao_Paulo", "America/Manaus", "UTC", "Europe/Lisbon", " America/Belem "])
def test_nomes_iana_validos(name):
    assert is_valid_timezone(name)


@pytest.mark.parametrize("name", ["", "   ", None, 3, "Marte/Olympus", "america/sao_paulo", "X" * 65])
def test_nomes_invalidos(name):
    assert not is_valid_timezone(name)


def test_normalize_devolve_default_para_ausente_ou_invalido():
    assert normalize_timezone(None) == DEFAULT_TIMEZONE
    assert normalize_timezone("Marte/Olympus") == DEFAULT_TIMEZONE
    assert normalize_timezone("America/Recife") == "America/Recife"
    assert normalize_timezone(" UTC ") == "UTC"


# ---------------------------------------------------------------------------
# PUT /users/me
# ---------------------------------------------------------------------------


def test_validate_timezone_none_ou_vazio_preserva_o_gravado():
    assert users._validate_timezone(None) is None
    assert users._validate_timezone("") is None


def test_validate_timezone_aceita_iana_e_normaliza_espacos():
    assert users._validate_timezone("America/Manaus") == "America/Manaus"
    assert users._validate_timezone("  UTC ") == "UTC"


def test_validate_timezone_invalido_e_400_nunca_default_silencioso():
    with pytest.raises(HTTPException) as exc:
        users._validate_timezone("Marte/Olympus")
    assert exc.value.status_code == 400
    assert "timezone" in exc.value.detail


@pytest.mark.parametrize("endpoint", [users.update_my_profile, users.update_user])
def test_update_grava_timezone_com_coalesce(endpoint):
    """`timezone = COALESCE(%s, timezone)`: ausente no payload nao apaga o valor."""
    source = inspect.getsource(endpoint)
    assert "timezone = COALESCE(%s, timezone)" in source
    assert '_validate_timezone(item.get("timezone"))' in source


# ---------------------------------------------------------------------------
# GET /users/me e GET /auth/me
# ---------------------------------------------------------------------------


def test_public_row_expoe_timezone_sempre_valido():
    assert users._public_row(_row())["timezone"] == DEFAULT_TIMEZONE  # linha pre-044
    assert users._public_row(_row(timezone="America/Manaus"))["timezone"] == "America/Manaus"
    assert users._public_row(_row(timezone="lixo"))["timezone"] == DEFAULT_TIMEZONE


def test_public_user_do_auth_expoe_timezone():
    assert auth._public_user(_row())["timezone"] == DEFAULT_TIMEZONE
    assert auth._public_user(_row(timezone="America/Cuiaba"))["timezone"] == "America/Cuiaba"
