"""Comportamento HTTP da FastAPI 0.115.x preservado na FastAPI 0.141 + Starlette 1.x.

A troca de versao (CVEs da Starlette 0.41.3) nao pode mudar o que os clientes
recebem. Duas mudancas de padrao da FastAPI afetariam este app e sao
desligadas aqui, nas duas apps (`main.py` e `main_engines.py`):

- `strict_content_type=False` (padrao novo desde a 0.132.0: True). Com True, um
  POST/PUT com corpo JSON SEM cabecalho `Content-Type` deixa de ser lido como
  JSON e vira 422. Na 0.115.x o corpo era lido como JSON; clientes que nao
  mandam o cabecalho (relogio Zepp, scripts, MCP) continuam funcionando.
- `default_response_class=JSONResponse` explicito. Desde a 0.130.0, rota com
  `response_model` e sem `response_class` serializa direto pelo Pydantic (Rust)
  em vez de `JSONResponse` (`json.dumps`). O JSON e equivalente, mas os bytes
  podem diferir (ex.: float `1e16` sai `1e16` no Pydantic e `1e+16` no
  `json.dumps`). Declarar a classe padrao mantem o caminho antigo, byte a byte.

Os dois ajustes sao travados por `tests/unit/test_fastapi_compat.py`.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi.responses import JSONResponse


def fastapi_compat_kwargs() -> Dict[str, Any]:
    return {"strict_content_type": False, "default_response_class": JSONResponse}
