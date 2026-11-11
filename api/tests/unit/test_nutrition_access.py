"""Acesso ao plano nutricional (`app/nutrition_access.py`, decisão D-L, RBAC lote 2D-1): o
próprio usuário passa; terceiro exige concessão `todos` (ou `alunos` com vínculo ativo), que o
admin e o PT têm nos grupos de sistema (qualquer aluno); aluno inexistente -> 404."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import nutrition_access as access
from app.access import EffectivePermissions, service
from app.access.catalog import DEFAULT_GRANTS_BY_GROUP
from app.access.membership import ROLE_TO_SYSTEM_GROUP
from app.security import Identity
from tests.support.nutrition_fake import FakeNutritionDB

PLANO = "nutricao.plano"


def _ident(role: str, user_id: int = 10) -> Identity:
    return Identity(user_id=user_id, role=role, source="session")


def _perms(role: str) -> EffectivePermissions:
    grupo = ROLE_TO_SYSTEM_GROUP[role]
    linhas = [
        {"group_key": grupo, "is_system": 1, "resource": r, "action": a, "scope": s}
        for r, a, s in DEFAULT_GRANTS_BY_GROUP[grupo]
    ] or [{"group_key": grupo, "is_system": 1, "resource": None, "action": None, "scope": None}]
    return service.merge_grants(linhas)


def _so(escopo: str, acao: str = "ver") -> EffectivePermissions:
    return service.merge_grants(
        [{"group_key": "g", "is_system": 0, "resource": PLANO, "action": acao, "scope": escopo}]
    )


@pytest.mark.parametrize("role", ["regular", "admin", "personal_trainer"])
def test_o_proprio_usuario_sempre_passa(role):
    perms = _perms(role)
    assert access.resolve_target_user(_ident(role), perms, PLANO, "ver", None) == 10
    assert access.resolve_target_user(_ident(role), perms, PLANO, "ver", 10) == 10


@pytest.mark.parametrize("role", ["admin", "personal_trainer"])
def test_admin_e_pt_veem_qualquer_aluno(role):
    perms = _perms(role)
    assert access.resolve_target_user(_ident(role), perms, PLANO, "editar", 99) == 99
    assert access.resolve_plan_owner(_ident(role), perms, "editar", 99) == 99


def test_aluno_comum_nao_ve_terceiro():
    with pytest.raises(HTTPException) as erro:
        access.resolve_target_user(_ident("regular"), _perms("regular"), PLANO, "ver", 99)
    assert erro.value.status_code == 403
    assert erro.value.detail == service.FORBIDDEN_DETAIL


@pytest.mark.parametrize("alvo", [0, -1, True, "10", 10.0])
def test_alvo_invalido_e_400_invalid_target(alvo):
    with pytest.raises(HTTPException) as erro:
        access.resolve_target_user(_ident("admin"), _perms("admin"), PLANO, "ver", alvo)
    assert erro.value.status_code == 400
    assert erro.value.detail["error"] == "invalid_target"


def test_sem_concessao_nem_o_proprio_passa():
    with pytest.raises(HTTPException) as erro:
        access.resolve_target_user(_ident("regular"), EffectivePermissions(), PLANO, "ver", None)
    assert erro.value.status_code == 403


def test_plano_proprio_passa_e_alheio_e_404_para_usuario_comum():
    perms = _perms("regular")
    assert access.resolve_plan_owner(_ident("regular"), perms, "ver", 10) == 10
    with pytest.raises(HTTPException) as erro:
        access.resolve_plan_owner(_ident("regular"), perms, "ver", 99)
    # 404 igual ao plano inexistente: o id alheio não revela que existe.
    assert erro.value.status_code == 404
    assert erro.value.detail["error"] == "plan_not_found"


def test_plano_sem_concessao_na_acao_e_404_ate_para_o_proprio():
    perms = _so("proprios")
    assert access.resolve_plan_owner(_ident("regular"), perms, "ver", 10) == 10
    with pytest.raises(HTTPException) as erro:
        access.resolve_plan_owner(_ident("regular"), perms, "editar", 10)
    assert erro.value.status_code == 404


def test_plano_de_aluno_vinculado_com_escopo_alunos(monkeypatch):
    monkeypatch.setattr(service, "has_active_link", lambda treinador, aluno: (treinador, aluno) == (10, 50))
    perms = _so("alunos")
    assert access.resolve_plan_owner(_ident("personal_trainer"), perms, "ver", 50) == 50
    with pytest.raises(HTTPException) as erro:
        access.resolve_plan_owner(_ident("personal_trainer"), perms, "ver", 51)
    assert erro.value.status_code == 404


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
