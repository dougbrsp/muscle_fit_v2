"""Permissões do RBAC para os e2e mobile (#234), geradas do catálogo real.

A sessão simulada dos testes de ponta a ponta (`frontend/e2e/mobile-layout`)
precisa do payload de `GET /auth/me`. Em vez de escrevê-lo à mão, o espelho
`frontend/e2e/mobile-layout/access-grants.generated.json` é montado AQUI com
`merge_grants` (a mesma função que o serviço usa) sobre `DEFAULT_GRANTS_BY_GROUP`,
e com `serialize_permissions` (o payload do `/auth/me`).

Mudou o catálogo? Regenere:

    cd api && python tests/unit/test_access_espelho_e2e_permissoes.py --gravar
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[3]
if __name__ == "__main__":  # execução direta (--gravar): `app` importável sem pytest
    sys.path.insert(0, str(RAIZ / "api"))

from app.access import catalog  # noqa: E402
from app.access.service import merge_grants, serialize_permissions  # noqa: E402

ARQUIVO = RAIZ / "frontend" / "e2e" / "mobile-layout" / "access-grants.generated.json"


def _papel(grupo: str, *, is_system: int = 1) -> dict[str, Any]:
    linhas = [
        {"group_key": grupo, "is_system": is_system, "resource": r, "action": a, "scope": s}
        for r, a, s in catalog.DEFAULT_GRANTS_BY_GROUP[grupo]
    ] or [{"group_key": grupo, "is_system": is_system}]  # admin: sem linhas, acesso total
    return serialize_permissions(merge_grants(linhas))


def gerado() -> dict[str, Any]:
    return {
        "admin": _papel(catalog.GROUP_ADMIN),
        "personal_trainer": _papel(catalog.GROUP_PERSONAL_TRAINER),
        "user": _papel(catalog.GROUP_USUARIO),
    }


def _texto() -> str:
    return json.dumps(gerado(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_espelho_e2e_e_o_do_catalogo():
    assert ARQUIVO.read_text(encoding="utf-8") == _texto(), (
        f"{ARQUIVO.name} divergiu do catálogo: rode "
        "`python tests/unit/test_access_espelho_e2e_permissoes.py --gravar`"
    )


def test_admin_tem_todos_os_recursos_com_todos():
    perms = gerado()["admin"]["permissions"]
    assert set(perms) == set(catalog.RESOURCE_KEYS)
    assert all(
        acoes == {a: catalog.SCOPE_TODOS for a in catalog.ACTIONS} for acoes in perms.values()
    )


def test_user_nao_tem_recurso_exclusivo_de_admin():
    dados = gerado()
    assert "admin.grupos" not in dados["user"]["permissions"]
    assert dados["user"]["groups"] == ["usuario"]
    assert dados["personal_trainer"]["groups"] == ["personal_trainer"]


if __name__ == "__main__":
    if "--gravar" not in sys.argv:
        raise SystemExit("uso: python tests/unit/test_access_espelho_e2e_permissoes.py --gravar")
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO.write_text(_texto(), encoding="utf-8")
    print(f"gravado: {ARQUIVO}")
