"""Fixtures opcionais dos testes unitarios (nao ha autouse aqui).

`permissoes_por_papel` (ADR 0011): faz o resolvedor REAL de permissoes
(`require_permission` -> `permissions_for_request` -> `resolve_permissions` ->
`merge_grants`) rodar sobre um banco falso de concessoes, em vez de falhar com 503
por falta de banco. Uso: `pytestmark = pytest.mark.usefixtures("permissoes_por_papel")`.

Como o banco de permissoes so recebe o `user_id`, o papel vem da identidade: a fixture
envolve `app.access.service.resolve_permissions` com um registrador que anota
`user_id -> identity.role` (a identidade que o teste ja injeta por
`dependency_overrides[get_current_identity]`) e DELEGA ao original; nada e substituido.
Papel -> grupo de `DEFAULT_GRANTS_BY_GROUP`: admin -> `admin`; personal_trainer ->
`personal_trainer`; regular -> `usuario`; qualquer outro papel = sem grupo (sem linhas).
"""

from __future__ import annotations

import pytest

from app.access import DEFAULT_GRANTS_BY_GROUP
from app.access import service as access_service

_GRUPO_DO_PAPEL = {"admin": "admin", "personal_trainer": "personal_trainer", "regular": "usuario"}


class BancoPermissoesPorPapel:
    """Banco falso de `app.access.service._db`: concessoes padrao do grupo do papel."""

    def __init__(self) -> None:
        self.papel_por_usuario: dict[int, str] = {}
        self.consultas: list[tuple] = []

    def registrar(self, identity) -> None:
        self.papel_por_usuario[int(identity.user_id)] = identity.role

    def fetch_rows(self, sql, params=()):
        self.consultas.append(tuple(params))
        grupo = _GRUPO_DO_PAPEL.get(self.papel_por_usuario.get(int(params[0])))
        if grupo is None:
            return []
        grants = DEFAULT_GRANTS_BY_GROUP[grupo]
        if not grants:
            return [{"group_key": grupo, "is_system": 1, "resource": None, "action": None, "scope": None}]
        return [
            {"group_key": grupo, "is_system": 1, "resource": r, "action": a, "scope": s} for r, a, s in grants
        ]


@pytest.fixture
def permissoes_por_papel(monkeypatch):
    banco = BancoPermissoesPorPapel()
    original = access_service.resolve_permissions

    def _resolve_registrando_papel(identity):
        banco.registrar(identity)
        return original(identity)

    monkeypatch.setattr(access_service, "_db", banco)
    monkeypatch.setattr(access_service, "resolve_permissions", _resolve_registrando_papel)
    return banco
