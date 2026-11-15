"""`can_access_target` (access/service.py): a decisao de `authorize_target` sem levantar."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.access import service
from app.security import Identity

RECURSO, ACAO = "nutricao.plano", "ver"


def _ident(user_id: int = 10, source: str = "session") -> Identity:
    return Identity(user_id=user_id, role="regular", source=source)


def _perms(escopo: str | None) -> service.EffectivePermissions:
    if escopo is None:
        return service.EffectivePermissions()
    return service.merge_grants(
        [{"group_key": "g", "is_system": 0, "resource": RECURSO, "action": ACAO, "scope": escopo}]
    )


@pytest.fixture
def vinculos(monkeypatch):
    """`has_active_link` falso: registra as chamadas e so libera (treinador 10, aluno 50)."""
    chamadas: list[tuple[int, int]] = []

    def _fake(treinador, aluno):
        chamadas.append((treinador, aluno))
        return (treinador, aluno) == (10, 50)

    monkeypatch.setattr(service, "has_active_link", _fake)
    return chamadas


@pytest.mark.parametrize("escopo", ["proprios", "alunos", "todos"])
def test_dono_e_o_proprio_usuario_passa_com_qualquer_escopo_sem_consultar_vinculo(escopo, vinculos):
    assert service.can_access_target(_ident(), _perms(escopo), RECURSO, ACAO, 10) is True
    assert vinculos == []


def test_terceiro_com_proprios_nao_passa(vinculos):
    assert service.can_access_target(_ident(), _perms("proprios"), RECURSO, ACAO, 50) is False
    assert vinculos == []


def test_escopo_todos_passa_para_terceiro_sem_consultar_vinculo(vinculos):
    assert service.can_access_target(_ident(), _perms("todos"), RECURSO, ACAO, 99) is True
    assert vinculos == []


def test_escopo_alunos_exige_vinculo_ativo(vinculos):
    perms = _perms("alunos")
    assert service.can_access_target(_ident(), perms, RECURSO, ACAO, 50) is True
    assert service.can_access_target(_ident(), perms, RECURSO, ACAO, 51) is False
    assert vinculos == [(10, 50), (10, 51)]


@pytest.mark.parametrize("dono", [10, 50])
def test_sem_concessao_nega_ate_para_o_proprio(dono, vinculos):
    assert service.can_access_target(_ident(), _perms(None), RECURSO, ACAO, dono) is False
    assert vinculos == []


def test_concessao_de_outra_acao_nao_vale(vinculos):
    assert service.can_access_target(_ident(), _perms("todos"), RECURSO, "editar", 10) is False


def test_token_mestre_tem_acesso_total_sem_banco(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("sem banco")

    monkeypatch.setattr(service._db, "fetch_rows", _boom)
    mestre = _ident(user_id=1, source="master_token")
    perms = service.resolve_permissions(mestre)
    assert service.can_access_target(mestre, perms, RECURSO, "excluir", 99) is True


def test_nao_levanta_e_authorize_target_decide_pelo_helper(vinculos):
    perms = _perms("proprios")
    assert service.can_access_target(_ident(), perms, RECURSO, ACAO, 99) is False
    with pytest.raises(HTTPException) as exc:
        service.authorize_target(_ident(), RECURSO, ACAO, 99, permissoes=perms)
    assert exc.value.status_code == 403
    assert exc.value.detail == service.FORBIDDEN_DETAIL
    assert service.authorize_target(_ident(), RECURSO, ACAO, None, permissoes=perms) == 10


def test_authorize_target_400_antes_de_qualquer_consulta(vinculos):
    with pytest.raises(HTTPException) as exc:
        service.authorize_target(_ident(), RECURSO, ACAO, 0, permissoes=_perms("alunos"))
    assert exc.value.status_code == 400
    assert vinculos == []
