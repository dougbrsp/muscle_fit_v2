"""Cliente HTTP para a API real do Intervals.icu (Parte 2 da migracao
multiusuario).

Replica em Python, no backend, a logica que ate esta entrega vivia no
Next.js (`fitness-frontend/src/lib/intervals-icu.ts`/`intervals-api.ts`):
Basic Auth (`API_KEY:<chave_do_usuario>` em base64), chamadas a
`/athlete/{athlete_id}/activities` e `/athlete/{athlete_id}/wellness`, e o
mesmo fallback de calculo de TSB quando o Intervals.icu nao retorna o campo
pronto (`TSB = CTL - ATL`).

Segue o mesmo padrao ja usado no projeto para chamar servicos externos sem
SDK (ver `app/insights_ai.py`/`app/cardio_agent_ai.py`): `urllib.request`
puro, timeout explicito, erro HTTP tratado e reempacotado numa excecao clara
(nunca deixa o `HTTPError`/`URLError` cru vazar pra cima).

`athlete_id`/`api_key` chegam SEMPRE ja resolvidos (a `api_key` ja
descriptografada) pelo chamador - este modulo nao sabe nada sobre
criptografia nem sobre qual usuario esta autenticado.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional


class IntervalsClientError(Exception):
    """Erro claro ao falar com o Intervals.icu (rede, HTTP, ou payload
    inesperado). `status_code` é `None` para erros de rede/timeout."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _env_first(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def get_base_url() -> str:
    return (_env_first("INTERVALS_ICU_BASE_URL", default="https://intervals.icu/api/v1") or "").rstrip("/")


def get_user_agent() -> str:
    return _env_first("INTERVALS_ICU_USER_AGENT", default="Mozilla/5.0 DougFitnessDashboard/1.0") or ""


def _basic_auth_header(api_key: str) -> str:
    token = base64.b64encode(f"API_KEY:{api_key}".encode("utf-8")).decode("utf-8")
    return f"Basic {token}"


def _request_json(path: str, api_key: str, timeout: float = 30.0) -> Any:
    url = f"{get_base_url()}{path}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": _basic_auth_header(api_key),
            "Accept": "application/json",
            "User-Agent": get_user_agent(),
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        if exc.code == 401:
            raise IntervalsClientError(
                "Chave de API do Intervals.icu inválida ou expirada.", status_code=401
            ) from exc
        raise IntervalsClientError(
            f"Intervals.icu retornou erro HTTP {exc.code}: {raw_body[:500]}", status_code=exc.code
        ) from exc
    except urllib.error.URLError as exc:
        raise IntervalsClientError(f"Falha de rede ao consultar Intervals.icu: {exc.reason}") from exc

    if not raw_body:
        return []

    try:
        return json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise IntervalsClientError("Intervals.icu retornou um payload que não é JSON válido.") from exc


def _normalize_array(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("items", "activities", "wellness", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        if payload:
            return [payload]

    return []


def date_range_params(oldest: date, newest: date) -> str:
    return f"oldest={oldest.isoformat()}&newest={newest.isoformat()}"


def fetch_activities(athlete_id: str, api_key: str, oldest: date, newest: date) -> List[Dict[str, Any]]:
    """Lista bruta de atividades do Intervals.icu (mesmo shape retornado pela
    API deles), no intervalo `[oldest, newest]`."""
    path = f"/athlete/{urllib.parse.quote(str(athlete_id))}/activities?{date_range_params(oldest, newest)}"
    payload = _request_json(path, api_key)
    return _normalize_array(payload)


def fetch_wellness(athlete_id: str, api_key: str, oldest: date, newest: date) -> List[Dict[str, Any]]:
    """Lista bruta de wellness do Intervals.icu (mesmo shape retornado pela
    API deles), no intervalo `[oldest, newest]`."""
    path = f"/athlete/{urllib.parse.quote(str(athlete_id))}/wellness?{date_range_params(oldest, newest)}"
    payload = _request_json(path, api_key)
    return _normalize_array(payload)


def fetch_athlete(athlete_id: str, api_key: str, timeout: float = 30.0) -> Dict[str, Any]:
    """Atleta do Intervals.icu com a configuração por esporte
    (`GET /athlete/{id}` -> `WithSportSettings`: `sportSettings[]` com `id`,
    `types`, `hr_zones`, `hr_zone_names`, `max_hr`, `lthr`). M98 C2: zonas de
    FC do atleta. Payload que não é objeto -> `{}`."""
    payload = _request_json(f"/athlete/{urllib.parse.quote(str(athlete_id))}", api_key, timeout=timeout)
    return payload if isinstance(payload, dict) else {}


def fetch_activities_and_wellness(
    athlete_id: str, api_key: str, oldest: date, newest: date
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Conveniência para buscar as duas listas de uma vez (mesmo padrão de
    `Promise.all` usado hoje no Next.js)."""
    activities = fetch_activities(athlete_id, api_key, oldest, newest)
    wellness = fetch_wellness(athlete_id, api_key, oldest, newest)
    return activities, wellness
