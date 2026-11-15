"""Paridade dos escopos: SCOPE_ORDER do frontend == SCOPES do catalogo (RBAC, ADR 0011)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.access import catalog

_RAIZ = Path(__file__).resolve().parents[3]
_FRONTEND = _RAIZ / "frontend"
_ARQUIVO = _FRONTEND / "src" / "lib" / "permissions.ts"


def test_scope_order_do_frontend_espelha_scopes_do_catalogo() -> None:
    if not _FRONTEND.is_dir():
        pytest.skip("diretorio frontend ausente (ex.: imagem so da api)")
    assert _ARQUIVO.is_file(), (
        f"{_ARQUIVO} nao existe: o frontend deve exportar SCOPE_ORDER em "
        "frontend/src/lib/permissions.ts"
    )
    texto = _ARQUIVO.read_text(encoding="utf-8")
    achado = re.search(r"export\s+const\s+SCOPE_ORDER\s*=\s*\[([^\]]*)\]", texto)
    assert achado, "SCOPE_ORDER nao encontrado em frontend/src/lib/permissions.ts"
    escopos = tuple(re.findall(r"""['"]([^'"]+)['"]""", achado.group(1)))
    assert escopos == tuple(catalog.SCOPES), (
        f"SCOPE_ORDER do frontend {escopos} diverge de catalog.SCOPES {catalog.SCOPES}"
    )
