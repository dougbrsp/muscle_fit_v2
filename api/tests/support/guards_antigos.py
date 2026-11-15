"""Copias CONGELADAS dos guards de papel removidos de `app/security.py` no lote 2F-3 (ADR 0011).

`require_admin`, `require_admin_or_pt`, `is_admin_or_pt` e `ADMIN_OR_PT_ROLES` foram
copiados fielmente de `origin/develop:api/app/security.py` antes do lote 2F-3, quando a
ultima rota que os usava passou a declarar `require_permission`.

Uso exclusivo dos testes:
- `tests/support/access_retrato.py` (classificar a arvore de dependencias como o retrato
  congelado `tests/unit/data/access_retrato_guards_antigos.json` classificava);
- os testes de paridade e da matriz (`test_access_paridade_guards.py`,
  `test_access_catalogo_e_resolucao.py`, `test_access_retrato_guards.py`), que comparam a
  matriz de permissoes com a regra de papel de antes.

NAO EDITE a regra: ela e a referencia historica. NAO IMPORTE este modulo a partir de
`api/app`; o teste `test_access_retrato_guards.py` garante que nenhum modulo de `app`
define nem importa esses nomes.
"""

from fastapi import Depends, HTTPException, status

from app.security import Identity, get_current_identity


def require_admin(identity: Identity = Depends(get_current_identity)) -> Identity:
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a administradores.")
    return identity


ADMIN_OR_PT_ROLES = ("admin", "personal_trainer")


def is_admin_or_pt(identity: Identity) -> bool:
    """Mesma regra de `require_admin_or_pt`, para quem so precisa SABER."""
    return identity.role in ADMIN_OR_PT_ROLES


def require_admin_or_pt(identity: Identity = Depends(get_current_identity)) -> Identity:
    if not is_admin_or_pt(identity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores e personal trainers.",
        )
    return identity
