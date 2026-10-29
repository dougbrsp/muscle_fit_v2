"""SEG-17: configuração única de CORS dos apps FastAPI (api-core e engines).

Antes, `main.py` e `main_engines.py` repetiam o mesmo bloco com
`allow_methods=["*"]` e `allow_headers=["*"]` junto de `allow_credentials=True`
(o Starlette refletia qualquer método/header pedido no preflight). Agora a
lista é explícita e vem do inventário do que os clientes de navegador usam:

- Frontend (`frontend/src/lib/api.ts`, `auth.ts`, `plan-muscle-map.ts`,
  `user-photo.ts`): métodos GET/POST/PUT/PATCH/DELETE; o único header não
  "safelisted" enviado é `Content-Type: application/json` (upload multipart
  deixa o browser definir o Content-Type). Sessão via cookie httpOnly, por
  isso `allow_credentials=True`.
- Settings App do Zepp (webview em `https://zepp-os.zepp.com`, pareamento em
  `/devices/pair`): `X-API-Token` + `Content-Type`.
- Clientes fora do navegador (side service do relógio, MCP, scripts, Lambdas)
  não passam por CORS: a lista não os afeta.

Origens: só as de `CORS_ALLOWED_ORIGINS` (vírgula), sem curinga nem regex.
Produção (`infra/lambda_api_core.tf`, `infra/lambda_engines.tf`):
`https://app.musclefit.com.br,https://zepp-os.zepp.com` (o apex
`musclefit.com.br` só responde 301 para o app na CloudFront Function e não
chama a API). DEV: `.env` (localhost e Tailscale).
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_CORS_ALLOWED_ORIGINS = "https://app.musclefit.com.br"

CORS_ALLOWED_METHODS: tuple[str, ...] = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")

# Accept, Accept-Language e Content-Language já são "safelisted" e o Starlette
# os aceita sempre; Content-Type entra explícito porque `application/json` não
# é valor safelisted.
CORS_ALLOWED_HEADERS: tuple[str, ...] = ("Content-Type", "X-API-Token")


def cors_allowed_origins(raw: str | None = None) -> list[str]:
    """Origens explícitas de `CORS_ALLOWED_ORIGINS`; descarta curinga.

    `*` com credenciais é proibido pelo spec e faria o Starlette aceitar
    qualquer origem; por isso é descartado em vez de aceito em silêncio.
    Barra final é removida porque o header `Origin` nunca a tem.
    """
    value = os.environ.get("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ALLOWED_ORIGINS) if raw is None else raw
    origins: list[str] = []
    for item in value.split(","):
        origin = item.strip().rstrip("/")
        if not origin or "*" in origin or origin in origins:
            continue
        origins.append(origin)
    return origins


def cors_middleware_kwargs() -> dict[str, Any]:
    """Argumentos do `CORSMiddleware` compartilhados pelos apps FastAPI."""
    return {
        "allow_origins": cors_allowed_origins(),
        "allow_credentials": True,
        "allow_methods": list(CORS_ALLOWED_METHODS),
        "allow_headers": list(CORS_ALLOWED_HEADERS),
    }
