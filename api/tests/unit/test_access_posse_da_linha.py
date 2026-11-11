"""Posse da linha (RBAC, ADR 0011): `owns_row_or_all` decide so com as permissoes
ja resolvidas, sem banco e sem consulta de vinculo."""

from __future__ import annotations

import pytest

from app.access import EffectivePermissions, owns_row_or_all, resolve_permissions
from app.access import catalog
from app.security import Identity

RECURSO = "treino.academias"
ACAO = "editar"


def _identity(user_id: int = 7, source: str = "session") -> Identity:
    return Identity(user_id=user_id, role="regular", source=source)


def _perms(scope: str | None) -> EffectivePermissions:
    grants = {RECURSO: {ACAO: scope}} if scope else {}
    return EffectivePermissions(grants=grants)


@pytest.mark.parametrize("scope", [catalog.SCOPE_PROPRIOS, catalog.SCOPE_ALUNOS, catalog.SCOPE_TODOS])
def test_dono_passa_com_qualquer_escopo_concedido(scope):
    assert owns_row_or_all(_identity(7), _perms(scope), RECURSO, ACAO, 7) is True


def test_terceiro_recusado_com_proprios_e_alunos():
    for scope in (catalog.SCOPE_PROPRIOS, catalog.SCOPE_ALUNOS):
        assert owns_row_or_all(_identity(7), _perms(scope), RECURSO, ACAO, 8) is False


def test_terceiro_passa_com_todos():
    assert owns_row_or_all(_identity(7), _perms(catalog.SCOPE_TODOS), RECURSO, ACAO, 8) is True


def test_linha_orfa_so_passa_com_todos():
    assert owns_row_or_all(_identity(7), _perms(catalog.SCOPE_TODOS), RECURSO, ACAO, None) is True
    assert owns_row_or_all(_identity(7), _perms(catalog.SCOPE_PROPRIOS), RECURSO, ACAO, None) is False
    assert owns_row_or_all(_identity(7), _perms(catalog.SCOPE_ALUNOS), RECURSO, ACAO, None) is False


def test_alunos_nao_basta_e_nao_consulta_vinculo(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("nao deve consultar vinculo nem banco")

    monkeypatch.setattr("app.access.service.has_active_link", _boom)
    monkeypatch.setattr("app.access.service._fetch_rows", _boom)
    assert owns_row_or_all(_identity(7), _perms(catalog.SCOPE_ALUNOS), RECURSO, ACAO, 8) is False


def test_sem_concessao_recusa_ate_o_dono():
    assert owns_row_or_all(_identity(7), _perms(None), RECURSO, ACAO, 7) is False
    assert owns_row_or_all(_identity(7), _perms(None), RECURSO, ACAO, None) is False


def test_concessao_de_outra_acao_nao_vale():
    perms = EffectivePermissions(grants={RECURSO: {"ver": catalog.SCOPE_TODOS}})
    assert owns_row_or_all(_identity(7), perms, RECURSO, ACAO, 7) is False


def test_token_mestre_passa_em_qualquer_linha():
    identity = _identity(1, source=catalog.MASTER_TOKEN_SOURCE)
    perms = resolve_permissions(identity)  # sem banco para token mestre
    assert owns_row_or_all(identity, perms, RECURSO, ACAO, 99) is True
    assert owns_row_or_all(identity, perms, RECURSO, ACAO, None) is True


def test_recurso_fora_do_catalogo_e_erro_de_programacao():
    with pytest.raises(ValueError):
        owns_row_or_all(_identity(), _perms(catalog.SCOPE_TODOS), "inexistente.x", ACAO, 7)
