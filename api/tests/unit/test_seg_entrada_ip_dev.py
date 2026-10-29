"""SEG-11 / SEG-14 / SEG-19 / SEG-06 (parte pura): sem banco.

Rodar: cd api && python -m pytest tests/unit/test_seg_entrada_ip_dev.py
"""

from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from starlette.requests import Request

from app import gyms
from app.client_ip import client_ip
from app.models import InsightsGenerateIn
from app.request_models import (
    BodyCompositionUpdateIn,
    GymCreateIn,
    SleepMetricsUpdateIn,
    UserCreateIn,
    UserProfileUpdateIn,
    UserUpdateIn,
)
from app.secrets_bootstrap import InsecureDefaultSecretError, assert_no_default_secrets
from app.security import _is_must_change_exempt


def _request(peer: str | None, xff: str | None = None, path: str = "/", method: str = "GET") -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "headers": headers,
        "client": (peer, 1234) if peer is not None else None,
    }
    return Request(scope)


# --------------------------------------------------------------------------- SEG-14


def test_xff_forjado_pega_o_ultimo_valor_atras_do_adapter():
    assert client_ip(_request("127.0.0.1", "spoof, real")) == "real"
    assert client_ip(_request("127.0.0.1", "1.1.1.1, 2.2.2.2, 203.0.113.9")) == "203.0.113.9"


def test_sem_header_usa_o_peer():
    assert client_ip(_request("127.0.0.1")) == "127.0.0.1"
    assert client_ip(_request("198.51.100.4")) == "198.51.100.4"


def test_peer_publico_ignora_xff_forjado():
    assert client_ip(_request("198.51.100.4", "spoof")) == "198.51.100.4"


def test_sem_cliente_e_sem_header_vira_unknown():
    assert client_ip(_request(None)) == "unknown"


# --------------------------------------------------------------------------- SEG-19


def test_api_recusa_token_padrao_fora_do_dev(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "dev-local-token")
    monkeypatch.setenv("DB_BACKEND", "data-api")
    with pytest.raises(InsecureDefaultSecretError):
        assert_no_default_secrets()


def test_token_padrao_continua_valendo_no_dev_pymysql(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "dev-local-token")
    monkeypatch.setenv("DB_BACKEND", "pymysql")
    assert_no_default_secrets()


def test_token_forte_fora_do_dev_passa(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "x" * 43)
    monkeypatch.setenv("DB_BACKEND", "data-api")
    assert_no_default_secrets()


# --------------------------------------------------------------------------- SEG-06 (isento)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/auth/me"),
        ("POST", "/auth/change-password"),
        ("POST", "/auth/logout"),
        ("GET", "/users/me"),
        ("GET", "/health"),
    ],
)
def test_rotas_isentas_da_troca_obrigatoria(method, path):
    assert _is_must_change_exempt(_request("1.2.3.4", path=path, method=method))


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/workout-plans"), ("PUT", "/users/me"), ("POST", "/auth/login-x"), ("GET", "/users")],
)
def test_demais_rotas_nao_sao_isentas(method, path):
    assert not _is_must_change_exempt(_request("1.2.3.4", path=path, method=method))


# --------------------------------------------------------------------------- SEG-11 modelos


def test_modelos_rejeitam_campo_desconhecido():
    for model, data in [
        (UserCreateIn, {"username": "a", "password": "12345678", "is_admin": True}),
        (UserProfileUpdateIn, {"password_hash": "x"}),
        (UserUpdateIn, {"id": 1}),
        (GymCreateIn, {"name": "g", "owner_user_id": 2}),
        (BodyCompositionUpdateIn, {"user_id": 2}),
        (SleepMetricsUpdateIn, {"user_id": 2}),
    ]:
        with pytest.raises(ValidationError):
            model(**data)


def test_modelos_rejeitam_texto_acima_do_teto():
    with pytest.raises(ValidationError):
        UserProfileUpdateIn(email="a" * 161)
    with pytest.raises(ValidationError):
        UserProfileUpdateIn(full_name="n" * 161)
    with pytest.raises(ValidationError):
        SleepMetricsUpdateIn(notes="x" * 2001)
    with pytest.raises(ValidationError):
        BodyCompositionUpdateIn(notes="x" * 2001)
    with pytest.raises(ValidationError):
        GymCreateIn(name="g" * 121)
    with pytest.raises(ValidationError):
        InsightsGenerateIn(question="q" * 2001)
    with pytest.raises(ValidationError):
        InsightsGenerateIn(question="q", context="c" * 8001)


def test_payloads_atuais_do_frontend_passam():
    UserCreateIn(username="maria", password="Senha12345", role="regular", full_name="Maria", email="m@x.com")
    UserUpdateIn(full_name="A", email=None, birth_date="1990-01-01", sex="F", role="admin", is_active=True)
    UserProfileUpdateIn(
        full_name=None,
        email=None,
        birth_date=None,
        sex=None,
        intervals_athlete_id="i123456",
        preferred_weight_unit="kg",
        timezone="America/Sao_Paulo",
        intervals_api_key=None,
    )
    GymCreateIn(name="Academia", usual_weekdays=[1, 3], is_recurring=True)
    sleep = SleepMetricsUpdateIn(
        local_date="2026-10-08", duration_hours=7.5, regularity_percent=80, awake_count=2, notes="ok"
    )
    assert sleep.model_dump(exclude_unset=True)["duration_hours"] == 7.5
    body = BodyCompositionUpdateIn(local_date="2026-10-08", weight_kg=80.2, metabolic_age=30, notes=None)
    assert body.model_dump(exclude_unset=True)["notes"] is None
    assert "bmi" not in body.model_dump(exclude_unset=True)


# --------------------------------------------------------------------------- SEG-11 upload

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16


@pytest.mark.parametrize(
    "data,expected",
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp"), (b"<script>", None)],
)
def test_assinatura_define_o_tipo(data, expected):
    assert gyms.sniff_image_content_type(data[:16]) == expected


def _upload(data: bytes, content_type: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename="f", headers={"content-type": content_type})


class _FakeS3:
    def __init__(self):
        self.put = None

    def list_objects_v2(self, **_kw):
        return {}

    def delete_object(self, **_kw):
        pass

    def put_object(self, **kw):
        self.put = kw


@pytest.fixture
def fake_s3(monkeypatch):
    s3 = _FakeS3()
    monkeypatch.setattr(gyms, "_s3_client", lambda: s3)
    monkeypatch.setattr(gyms, "_s3_bucket", lambda: "bucket")
    monkeypatch.setattr(gyms, "_invalidate_cloudfront_paths", lambda _keys: None)
    return s3


def test_upload_png_e_jpeg_reais_ok_e_extensao_vem_da_assinatura(fake_s3):
    # content-type declarado errado: a assinatura manda.
    assert gyms._save_photo("gyms", 1, "gyms", _upload(PNG, "image/jpeg")) == "gyms/1.png"
    assert fake_s3.put["ContentType"] == "image/png"
    assert gyms._save_photo("gyms", 2, "gyms", _upload(JPEG, "image/png")) == "gyms/2.jpg"


def test_texto_rotulado_como_png_e_400(fake_s3):
    with pytest.raises(HTTPException) as exc:
        gyms._save_photo("gyms", 1, "gyms", _upload(b"isto nao e uma imagem", "image/png"))
    assert exc.value.status_code == 400
    assert fake_s3.put is None


def test_upload_acima_de_8mb_e_413(fake_s3):
    with pytest.raises(HTTPException) as exc:
        gyms._save_photo("gyms", 1, "gyms", _upload(PNG + b"\x00" * gyms.MAX_PHOTO_BYTES, "image/png"))
    assert exc.value.status_code == 413
    assert fake_s3.put is None


def test_upload_de_exatamente_8mb_passa(fake_s3):
    data = PNG + b"\x00" * (gyms.MAX_PHOTO_BYTES - len(PNG))
    assert len(data) == gyms.MAX_PHOTO_BYTES
    assert gyms._save_photo("gyms", 1, "gyms", _upload(data, "image/png")) == "gyms/1.png"
