"""Dispositivos pareados por usuário (app Zepp OS / Amazfit) - M40.

Cada usuário gera, na tela de perfil, tokens de dispositivo revogáveis. O
token cru é devolvido uma única vez no pareamento; só o hash SHA-256 é
persistido. Com ele o dispositivo age em nome do DONO (ver
`security._device_identity`), nos demais endpoints da API.

Gestão (parear, listar, revogar) exige uma credencial "humana" - sessão do
usuário, ou o token mestre (que resolve para o admin e, como qualquer
credencial, só vê e revoga os dispositivos do próprio usuário): um
dispositivo não cria, lista nem revoga outro. `GET /devices/me` é o inverso: só responde a token de
dispositivo (botão "Testar conexão" do app Zepp).

O path `POST /devices/pair` é mantido: a regra de limite por IP do WAF
(`infra/api_gateway_borda.tf`, "limite-auth-por-ip") casa por ele.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from .db import transaction
from .security import Identity, get_current_identity

router = APIRouter(tags=["devices"])

# Prefixo do token de dispositivo: identifica a credencial em logs de suporte
# e no app do relógio sem revelar nada do segredo.
DEVICE_TOKEN_PREFIX = "mfd_"
DEVICE_TOKEN_RANDOM_BYTES = 32

# Tamanhos das colunas `devices.device_name` / `devices.platform` (migração 027).
DEVICE_NAME_MAX_LENGTH = 160
DEVICE_PLATFORM_MAX_LENGTH = 80
DEFAULT_DEVICE_PLATFORM = "zepp"

DEVICE_MANAGEMENT_FORBIDDEN_DETAIL = "Um dispositivo não pode gerenciar dispositivos. Use o app MuscleFit."
DEVICE_ONLY_DETAIL = "Rota exclusiva para token de dispositivo."
DEVICE_NOT_FOUND_DETAIL = "Dispositivo não encontrado ou já revogado."


class DevicePairIn(BaseModel):
    device_name: str = Field(..., max_length=DEVICE_NAME_MAX_LENGTH)
    platform: str = Field(default=DEFAULT_DEVICE_PLATFORM, max_length=DEVICE_PLATFORM_MAX_LENGTH)

    @field_validator("device_name")
    @classmethod
    def _device_name_obrigatorio(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Informe o nome do dispositivo.")
        return value

    @field_validator("platform")
    @classmethod
    def _platform_padrao(cls, value: str) -> str:
        return value.strip() or DEFAULT_DEVICE_PLATFORM


def require_user_credential(identity: Identity = Depends(get_current_identity)) -> Identity:
    """Gestão de dispositivos: qualquer credencial, menos token de dispositivo."""
    if identity.source == "device":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=DEVICE_MANAGEMENT_FORBIDDEN_DETAIL)
    return identity


def require_device_credential(identity: Identity = Depends(get_current_identity)) -> Identity:
    if identity.source != "device" or identity.device_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=DEVICE_ONLY_DETAIL)
    return identity


def _new_device_token() -> str:
    return DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(DEVICE_TOKEN_RANDOM_BYTES)


def _device_status(row: dict) -> str:
    return "active" if row.get("is_active") and row.get("revoked_at") is None else "revoked"


@router.post("/devices/pair")
def pair_device(payload: DevicePairIn, identity: Identity = Depends(require_user_credential)):
    raw_token = _new_device_token()
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO devices (user_id, device_name, platform, token_hash) VALUES (%s, %s, %s, %s)",
                (identity.user_id, payload.device_name, payload.platform, token_hash),
            )
            device_id = cur.lastrowid

    return {
        "status": "ok",
        "device_id": device_id,
        "device_name": payload.device_name,
        "platform": payload.platform,
        # Único momento em que o token cru é exposto: o cliente deve guardá-lo.
        "device_token": raw_token,
    }


@router.get("/devices")
def list_devices(include_revoked: bool = False, identity: Identity = Depends(require_user_credential)):
    filtro_ativos = "" if include_revoked else "AND is_active = 1 AND revoked_at IS NULL"
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, device_name, platform, paired_at, last_seen_at, revoked_at, is_active
                FROM devices
                WHERE user_id = %s {filtro_ativos}
                ORDER BY paired_at DESC, id DESC
                """,
                (identity.user_id,),
            )
            rows = cur.fetchall() or []

    return {"items": [{**row, "status": _device_status(row)} for row in rows]}


@router.get("/devices/me")
def current_device(identity: Identity = Depends(require_device_credential)):
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id AS device_id, d.device_name, d.platform, d.last_seen_at,
                       u.full_name, u.username
                FROM devices d
                JOIN users u ON u.id = d.user_id
                WHERE d.id = %s AND d.user_id = %s
                LIMIT 1
                """,
                (identity.device_id, identity.user_id),
            )
            row = cur.fetchone()

    if not row:
        # A identidade acabou de ser resolvida por este dispositivo; sumir aqui
        # só acontece numa corrida com a revogação.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DEVICE_NOT_FOUND_DETAIL)

    return {
        "device_id": row["device_id"],
        "device_name": row["device_name"],
        "platform": row["platform"],
        "user_display_name": (row.get("full_name") or "").strip() or row["username"],
        "last_seen_at": row["last_seen_at"],
    }


@router.delete("/devices/{device_id}")
def revoke_device(device_id: int, identity: Identity = Depends(require_user_credential)):
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE devices
                SET is_active = 0, revoked_at = CURRENT_TIMESTAMP
                WHERE id = %s AND user_id = %s AND is_active = 1
                """,
                (device_id, identity.user_id),
            )
            if cur.rowcount == 0:
                # Dispositivo de outro usuário responde igual a inexistente:
                # não revela que o id existe.
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DEVICE_NOT_FOUND_DETAIL)

    return {"status": "ok", "device_id": device_id, "revoked": True}
