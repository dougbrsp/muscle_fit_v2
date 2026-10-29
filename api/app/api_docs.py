"""SEG-12: /docs, /redoc e /openapi.json so existem em DEV.

Producao (Lambdas) nao define API_DOCS_ENABLED, entao a documentacao interativa
(mapa completo das rotas, inclusive admin) nao fica exposta a anonimos.
O docker-compose de DEV define API_DOCS_ENABLED=1.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


def api_docs_enabled() -> bool:
    return os.environ.get("API_DOCS_ENABLED") == "1"


def api_docs_kwargs() -> Dict[str, Optional[Any]]:
    if api_docs_enabled():
        return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}
