"""URL publica de objetos do bucket de uploads servidos pela CDN.

Um unico mecanismo nos dois ambientes; so o VALOR muda:

- producao: `PUBLIC_ASSETS_BASE_URL` ausente (ou = `https://app.musclefit.com.br`)
  -> a CloudFront `musclefit-cdn`, que serve `exercise-cards/*` (e as fotos)
  a partir do bucket `musclefit-uploads` via OAC;
- DEV: o compose aponta para o bucket no LocalStack, no host/porta que o
  NAVEGADOR alcanca (`http://localhost:4566/musclefit-uploads`).

Os literais `app.musclefit.com.br` que ja existem em outros modulos
(`gyms.PUBLIC_ASSETS_DOMAIN`, `services.py`, ...) NAO foram migrados para ca
de proposito: e o item M67 do backlog, num PR proprio.
"""

from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://app.musclefit.com.br"


def base_url() -> str:
    """Base sem barra final, lida do ambiente a cada chamada."""
    value = (os.environ.get("PUBLIC_ASSETS_BASE_URL") or "").strip()
    return (value or DEFAULT_BASE_URL).rstrip("/")


def url_for(key: str) -> str:
    """URL publica da S3 key (gerada pelo proprio codigo, nunca texto livre
    do usuario). Barra inicial e tolerada; key vazia e erro de programacao."""
    clean = str(key or "").strip().lstrip("/")
    if not clean:
        raise ValueError("S3 key vazia nao tem URL publica.")
    return f"{base_url()}/{clean}"
