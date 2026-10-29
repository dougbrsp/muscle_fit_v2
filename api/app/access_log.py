"""Log de acesso da API: UMA linha JSON por requisicao HTTP (M56).

Pergunta que este modulo responde: "que status a API devolveu para esta
chamada?". Ate o M56 os logs da `musclefit-api-core` nao diziam - no
incidente do loop React #185 em `/grupos-musculares` nao deu para confirmar se
o usuario foi para o login por um 401 de verdade ou por um erro de banco
convertido em 401 (`app/security.py`).

Conteudo (deliberadamente minimo, sem PII):
- `method`;
- `route`: o TEMPLATE da rota (`/workout-plans/{plan_id}`), nunca o path cru
  nem a query string. Requisicao que nao casou com nenhuma rota (404,
  preflight CORS respondido pelo middleware) sai como `<unmatched>`;
- `status`: o status enviado. Excecao nao tratada (que o
  `ServerErrorMiddleware` do Starlette, por fora deste, transforma em 500)
  sai como 500;
- `duration_ms`.
Nunca: corpo, headers (inclusive `X-API-Token`/`Cookie`), IP, user agent.

Middleware ASGI puro (nao `BaseHTTPMiddleware`): nao bufferiza o corpo e nao
interfere em streaming/excecoes. O template vem de `scope["route"]`, que o
`APIRoute.matches` do FastAPI preenche no MESMO dict de scope que passa por
aqui - por isso ele so e lido depois que a aplicacao respondeu.

Saida: handler proprio em
stdout com a linha JSON crua e `propagate=False`. A api-core roda `uvicorn`
atras do AWS Lambda Web Adapter, cujo logger raiz NAO tem handler: um INFO
propagado cairia no `logging.lastResort` (so WARNING+) e sumiria. stdout do
processo vai para o CloudWatch Logs da Lambda (em dev, para o log do
container) - mesmo caminho nos dois ambientes.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Optional

ACCESS_EVENT = "http_access"
UNMATCHED_ROUTE = "<unmatched>"

logger = logging.getLogger("fitness.access")
if not any(getattr(h, "_access_log", False) for h in logger.handlers):
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _handler._access_log = True  # type: ignore[attr-defined]
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def route_template(scope: dict[str, Any]) -> str:
    """Template da rota que atendeu a requisicao, ou `<unmatched>`."""
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return template if isinstance(template, str) and template else UNMATCHED_ROUTE


def build_access_event(method: str, route: str, status: Optional[int], duration_ms: float) -> dict[str, Any]:
    event: dict[str, Any] = {"event": ACCESS_EVENT, "method": method, "route": route}
    if status is not None:
        event["status"] = status
    event["duration_ms"] = round(duration_ms, 1)
    return event


def _emit(event: dict[str, Any]) -> None:
    # Log nunca derruba a requisicao.
    try:
        logger.info(json.dumps(event, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


class AccessLogMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status: Optional[int] = None

        async def send_and_capture(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = message.get("status")
            await send(message)

        try:
            await self.app(scope, receive, send_and_capture)
        except Exception:
            if status is None:
                status = 500
            raise
        finally:
            _emit(
                build_access_event(
                    str(scope.get("method", "")),
                    route_template(scope),
                    status,
                    (time.perf_counter() - started) * 1000.0,
                )
            )
