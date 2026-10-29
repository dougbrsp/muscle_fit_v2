"""`GET /auth/me` expõe o perfil antropométrico (nutrição L0, migração 074) só
leitura: `sex` (desde o L2a no código canônico do motor, normalizado também na
leitura: 'M' antigo -> 'male', desconhecido -> null), `birth_date` (ISO),
`height_cm` (número) e `activity_level` (código do motor); linha anterior à
migração (colunas ausentes) -> `null`, sem quebrar. E o CRUD de usuários aceita
`personal_trainer` (plano §5 D1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import users
from app.auth import _public_user

_BASE = {
    "id": 1,
    "username": "doug",
    "full_name": "Doug",
    "role": "admin",
    "must_change_password": 0,
    "intervals_athlete_id": None,
    "intervals_api_key_encrypted": None,
    "timezone": "America/Sao_Paulo",
}


def test_auth_me_devolve_os_quatro_campos_prontos_para_json():
    row = {
        **_BASE,
        "sex": "M",
        "birth_date": date(1990, 5, 1),
        "height_cm": Decimal("178.5"),
        "activity_level": "moderate",
    }
    out = _public_user(row)
    assert out["sex"] == "male", "valor antigo sai no código canônico do motor (L2a)"
    assert out["birth_date"] == "1990-05-01"
    assert out["height_cm"] == 178.5 and isinstance(out["height_cm"], float)
    assert out["activity_level"] == "moderate"
    # Nada do que já existia mudou.
    assert out["id"] == 1 and out["role"] == "admin" and out["timezone"] == "America/Sao_Paulo"
    assert "password_hash" not in out and "intervals_api_key_encrypted" not in out


def test_auth_me_normaliza_sexo_e_nivel_pelo_motor():
    for gravado, esperado in (("male", "male"), ("F", "female"), ("Outro", "other"), ("abc", None)):
        assert _public_user({**_BASE, "sex": gravado})["sex"] == esperado
    assert _public_user({**_BASE, "activity_level": "Muito ativo"})["activity_level"] == "very_active"
    assert _public_user({**_BASE, "activity_level": "xyz"})["activity_level"] is None


def test_auth_me_antes_da_074_ou_sem_preencher_devolve_null():
    out = _public_user(dict(_BASE))
    assert out["sex"] is None
    assert out["birth_date"] is None
    assert out["height_cm"] is None
    assert out["activity_level"] is None


def test_personal_trainer_e_papel_atribuivel_e_a_mensagem_lista_todos():
    assert users._ALLOWED_ROLES == {"admin", "regular", "personal_trainer"}
    for role in sorted(users._ALLOWED_ROLES):
        assert f"'{role}'" in users._ROLE_ERROR
    assert all(len(role) <= 16 for role in users._ALLOWED_ROLES), "cabe no max_length=16 de UserCreateIn.role"
