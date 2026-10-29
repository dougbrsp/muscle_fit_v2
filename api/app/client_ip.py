"""IP do cliente atras do Lambda Web Adapter (SEG-14).

Em producao o trafego chega ao uvicorn via Lambda Web Adapter, entao o peer
TCP (`request.client.host`) e 127.0.0.1. O API Gateway anexa o IP real ao
FINAL do `X-Forwarded-For` (valores anteriores sao enviados pelo cliente e
podem ser forjados), por isso o valor confiavel e o ULTIMO da lista.
Quando o peer ja e um IP real (DEV direto, uvicorn com proxy-headers),
ele e usado como esta e o header e ignorado.
"""
from __future__ import annotations

from fastapi import Request

_LOCAL_PEERS = frozenset({"", "127.0.0.1", "::1", "localhost"})
_MAX_LEN = 45


def client_ip(request: Request) -> str:
    host = (request.client.host if request.client else None) or ""
    if host in _LOCAL_PEERS:
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1][:_MAX_LEN]
    return (host or "unknown")[:_MAX_LEN]
