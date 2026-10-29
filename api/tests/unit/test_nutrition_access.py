"""Acesso ao plano nutricional (`app/nutrition_access.py`, decisão D-L): o
próprio usuário passa; terceiro exige admin ou personal trainer, que vê
QUALQUER aluno (sem vínculo formal); aluno inexistente -> 404."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import nutrition_access as access
from app.security import Identity
from tests.support.nutrition_fake import FakeNutritionDB


def _ident(role: str, user_id: int = 10) -> Identity:
    return Identity(user_id=user_id, role=role, source="session")


@pytest.mark.parametrize("role", ["regular", "admin", "personal_trainer"])
def test_o_proprio_usuario_sempre_passa(role):
    assert access.resolve_target_user(_ident(role), None) == 10
    assert access.resolve_target_user(_ident(role), 10) == 10


@pytest.mark.parametrize("role", ["admin", "personal_trainer"])
def test_admin_e_pt_veem_qualquer_aluno(role):
    assert access.resolve_target_user(_ident(role), 99) == 99
    assert access.require_plan_manager(_ident(role)).role == role


def test_aluno_comum_nao_ve_terceiro():
    with pytest.raises(HTTPException) as erro:
        access.resolve_target_user(_ident("regular"), 99)
    assert erro.value.status_code == 403
    with pytest.raises(HTTPException):
        access.require_plan_manager(_ident("regular"))


def test_aluno_inexistente_e_404():
    cur = FakeNutritionDB().conn().cursor()
    assert access.fetch_target_user(cur, 2) == {"id": 2, "username": "aluno", "full_name": "Aluno Teste"}
    with pytest.raises(HTTPException) as erro:
        access.fetch_target_user(cur, 999)
    assert erro.value.status_code == 404


# --- Diário (L4): alimento e porção que entram no diário do aluno ---------------


@pytest.mark.parametrize(
    ("dono", "ativo", "quem", "aluno", "usa"),
    [
        (None, 1, 10, 10, True),  # público
        (10, 1, 10, 10, True),  # do próprio aluno
        (20, 1, 10, 10, False),  # privado de outro usuário
        (10, 0, 10, 10, False),  # inativo
        (99, 1, 99, 10, True),  # admin/PT registrando para o aluno com o próprio cadastro
        (10, 1, 99, 10, True),  # admin/PT usando o cadastro do aluno
        (20, 1, 99, 10, False),  # nem do aluno nem de quem registra
    ],
)
def test_alimento_no_diario(dono, ativo, quem, aluno, usa):
    alimento = {"owner_user_id": dono, "is_active": ativo, "source": "user"}
    assert access.can_log_food(_ident("personal_trainer", quem), aluno, alimento) is usa


@pytest.mark.parametrize(("dono", "usa"), [(None, True), (10, True), (99, True), (20, False)])
def test_porcao_no_diario(dono, usa):
    assert access.can_log_portion(_ident("personal_trainer", 99), 10, {"owner_user_id": dono}) is usa
