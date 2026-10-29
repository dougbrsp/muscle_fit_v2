"""Rotas da foto do rótulo (`app/nutrition_label_photo.py`, L6) sem banco, S3,
DynamoDB nem Lambda reais: corpo JSON com a foto em base64 sem padding, limite,
alfabeto e tipo do motor ANTES da cota (413/400/415 não gastam tentativa),
reserva da cota (429), ordem do despacho (S3 -> job -> vínculo -> invoke da
plan-generator) e o fechamento como `failed` quando a gravação ou o despacho
falham. O router é conferido no `app.main.app` real."""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import async_jobs, nutrition_label_photo as rota, nutrition_label_storage as storage
from app.domain.nutrition import ai_usage, label_photo
from app.domain.nutrition import labels as lb
from app.domain.nutrition.ai_usage import LABEL_PHOTO_JOB_TYPE, AiFeature, AiUsageStatus, QuotaStatus
from app.nutrition_ai_quota import quota_exceeded_error
from app.nutrition_label_ai import PROMPT_VERSION
from app.security import Identity, get_current_identity

USUARIO = 7
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 2048
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 2048
CHAVE = label_photo.label_photo_key(USUARIO, "0123456789abcdef0123456789abcdef")


class _Despacho:
    def __init__(self, monkeypatch, *, cota: bool = True, falha_s3: bool = False, falha_invoke: bool = False):
        self.eventos: list[tuple] = []
        self.cota = cota
        self.falha_s3 = falha_s3
        self.falha_invoke = falha_invoke
        monkeypatch.setattr(rota.nutrition_ai_quota, "open_usage", self.open_usage)
        monkeypatch.setattr(rota.nutrition_ai_quota, "finish_usage", self.finish_usage)
        monkeypatch.setattr(rota.nutrition_ai_quota, "link_job", self.link_job)
        monkeypatch.setattr(storage, "put_label_photo", self.put)
        monkeypatch.setattr(async_jobs, "create_job", self.create_job)
        monkeypatch.setattr(async_jobs, "invoke_async", self.invoke)
        monkeypatch.setattr(async_jobs, "mark_failed", self.mark_failed)

    def open_usage(self, **kwargs):
        self.eventos.append(("open_usage", kwargs))
        if not self.cota:
            limite = ai_usage.daily_quota(AiFeature.LABEL_PHOTO)
            raise quota_exceeded_error(QuotaStatus(feature=AiFeature.LABEL_PHOTO, limit=limite, used=limite))
        return 55

    def finish_usage(self, usage_id, **kwargs):
        self.eventos.append(("finish_usage", usage_id, kwargs))
        return True

    def link_job(self, usage_id, job_id):
        self.eventos.append(("link_job", usage_id, job_id))
        return True

    def put(self, user_id, data):
        self.eventos.append(("put", user_id, len(data)))
        if self.falha_s3:
            raise storage.LabelPhotoStorageError("s3 fora")
        return CHAVE

    def create_job(self, job_type, request=None, *, owner_user_id):
        self.eventos.append(("create_job", job_type, request, owner_user_id))
        return "job-1"

    def invoke(self, env, payload):
        self.eventos.append(("invoke", env, payload))
        if self.falha_invoke:
            raise RuntimeError("lambda fora")

    def mark_failed(self, job_id, error):
        self.eventos.append(("mark_failed", job_id, error))
        return True

    def nomes(self):
        return [evento[0] for evento in self.eventos]


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(rota.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=USUARIO, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _b64(dados: bytes) -> str:
    return base64.b64encode(dados).decode("ascii").rstrip("=")


def _enviar(cliente, dados: bytes):
    return cliente.post("/nutrition/foods/label-photo", json={"image_base64": _b64(dados)})


def test_o_router_esta_no_app_real_da_api_core():
    from app.main import app

    caminhos = app.openapi()["paths"]
    assert "/nutrition/foods/label-photo" in caminhos and "/nutrition/foods/label-photo/usage" in caminhos
    resposta = TestClient(app, raise_server_exceptions=False).get("/nutrition/foods/label-photo/usage")
    assert resposta.status_code in (401, 403)  # existe e exige identidade (não 404)


def test_upload_despacha_na_ordem_do_contrato_e_responde_202(monkeypatch):
    despacho = _Despacho(monkeypatch)
    resposta = _enviar(_client(), JPEG)
    assert resposta.status_code == 202, resposta.text
    assert resposta.json() == {
        "job_id": "job-1",
        "status": "pending",
        "poll_timeout_seconds": async_jobs.worker_lost_deadline_seconds(LABEL_PHOTO_JOB_TYPE),
    }
    assert despacho.nomes() == ["open_usage", "put", "create_job", "link_job", "invoke"]
    _, reserva = despacho.eventos[0]
    assert reserva == {"user_id": USUARIO, "feature": AiFeature.LABEL_PHOTO, "prompt_version": PROMPT_VERSION,
                       "image_count": label_photo.LABEL_PHOTO_IMAGES_PER_READING}
    assert despacho.eventos[1] == ("put", USUARIO, len(JPEG))
    _, tipo, pedido, dono = despacho.eventos[2]
    assert tipo == LABEL_PHOTO_JOB_TYPE and pedido == {"s3_key": CHAVE, "ai_usage_id": 55} and dono == USUARIO
    assert despacho.eventos[3] == ("link_job", 55, "job-1")
    _, env, evento = despacho.eventos[4]
    assert env == "PLAN_GENERATOR_FUNCTION_NAME"
    assert evento == {"job_id": "job-1", "job_kind": LABEL_PHOTO_JOB_TYPE, "user_id": USUARIO, "ai_usage_id": 55,
                      "s3_key": CHAVE}


@pytest.mark.parametrize(
    ("dados", "status", "codigo"),
    [
        (b"\xff\xd8\xff" + b"0" * label_photo.LABEL_PHOTO_MAX_BYTES, 413, label_photo.ERR_LABEL_PHOTO_TOO_LARGE),
        (PNG, 415, label_photo.ERR_LABEL_PHOTO_UNSUPPORTED_TYPE),
        (b"GIF89a" + b"g" * 64, 415, label_photo.ERR_LABEL_PHOTO_UNSUPPORTED_TYPE),
        (b"<svg onload=alert(1)>", 415, label_photo.ERR_LABEL_PHOTO_UNSUPPORTED_TYPE),
        (b"", 400, label_photo.ERR_LABEL_PHOTO_EMPTY),
    ],
    ids=["grande", "png", "gif", "svg", "vazio"],
)
def test_tamanho_e_tipo_do_motor_antes_da_cota(monkeypatch, dados, status, codigo):
    despacho = _Despacho(monkeypatch)
    # O tipo declarado pelo cliente é ignorado: vale a assinatura dos bytes.
    resposta = _enviar(_client(), dados)
    assert resposta.status_code == status
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == codigo and detalhe["message"] == lb.limitation_label(codigo)
    assert detalhe["max_bytes"] == label_photo.LABEL_PHOTO_MAX_BYTES
    assert despacho.eventos == []  # nem reserva a cota


@pytest.mark.parametrize(
    ("corpo", "status", "codigo"),
    [
        ({"image_base64": "não é base64!"}, 400, label_photo.ERR_LABEL_PHOTO_INVALID_ENCODING),
        ({"image_base64": "A" * (label_photo.LABEL_PHOTO_MAX_BASE64_CHARS + 1)}, 413, label_photo.ERR_LABEL_PHOTO_TOO_LARGE),
    ],
    ids=["alfabeto", "texto_grande"],
)
def test_base64_invalido_ou_grande_antes_de_decodificar(monkeypatch, corpo, status, codigo):
    despacho = _Despacho(monkeypatch)
    resposta = _client().post("/nutrition/foods/label-photo", json=corpo)
    assert resposta.status_code == status and resposta.json()["detail"]["error"] == codigo
    assert despacho.eventos == []


def test_padding_e_aceito_e_contrato_estrito(monkeypatch):
    despacho = _Despacho(monkeypatch)
    cliente = _client()
    com_padding = base64.b64encode(JPEG).decode("ascii")
    assert cliente.post("/nutrition/foods/label-photo", json={"image_base64": com_padding}).status_code == 202
    assert despacho.eventos[1] == ("put", USUARIO, len(JPEG))
    assert cliente.post("/nutrition/foods/label-photo", json={"image_base64": _b64(JPEG), "nome": "x"}).status_code == 422


def test_cota_estourada_responde_429_sem_gravar_nada(monkeypatch):
    despacho = _Despacho(monkeypatch, cota=False)
    resposta = _enviar(_client(), JPEG)
    assert resposta.status_code == 429
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == ai_usage.ERR_AI_QUOTA_EXCEEDED
    assert detalhe["limit"] == ai_usage.LABEL_PHOTO_DAILY_QUOTA
    assert despacho.nomes() == ["open_usage"]


def test_falha_no_s3_fecha_a_linha_e_responde_503(monkeypatch):
    despacho = _Despacho(monkeypatch, falha_s3=True)
    resposta = _enviar(_client(), JPEG)
    assert resposta.status_code == 503
    assert resposta.json()["detail"]["error"] == label_photo.ERR_LABEL_PHOTO_STORAGE_FAILED
    assert despacho.nomes() == ["open_usage", "put", "finish_usage"]
    _, usage_id, fechamento = despacho.eventos[-1]
    assert usage_id == 55 and fechamento == {"status": AiUsageStatus.FAILED,
                                             "error_code": label_photo.ERR_LABEL_PHOTO_STORAGE_FAILED}


def test_falha_no_invoke_fecha_a_linha_e_o_job(monkeypatch):
    despacho = _Despacho(monkeypatch, falha_invoke=True)
    resposta = _enviar(_client(), JPEG)
    assert resposta.status_code == 503
    assert resposta.json()["detail"]["error"] == label_photo.ERR_LABEL_PHOTO_DISPATCH_FAILED
    assert despacho.nomes() == ["open_usage", "put", "create_job", "link_job", "invoke", "finish_usage", "mark_failed"]
    assert despacho.eventos[5][2]["error_code"] == label_photo.ERR_LABEL_PHOTO_DISPATCH_FAILED
    assert despacho.eventos[6][1] == "job-1"


def test_uso_do_dia_vem_do_servico_da_cota(monkeypatch):
    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    pedidos = []

    def _uso(conn, *, user_id, feature):
        pedidos.append((user_id, feature))
        return QuotaStatus(feature=AiFeature.LABEL_PHOTO, limit=ai_usage.LABEL_PHOTO_DAILY_QUOTA, used=3)

    monkeypatch.setattr(rota, "get_conn", lambda: _Conn())
    monkeypatch.setattr(rota.nutrition_ai_quota, "usage_today", _uso)
    resposta = _client().get("/nutrition/foods/label-photo/usage")
    assert resposta.status_code == 200
    assert resposta.json() == {"feature": "label_photo", "limit": ai_usage.LABEL_PHOTO_DAILY_QUOTA, "used": 3,
                               "remaining": ai_usage.LABEL_PHOTO_DAILY_QUOTA - 3, "allowed": True}
    assert pedidos == [(USUARIO, AiFeature.LABEL_PHOTO)]


def test_armazenamento_grava_na_chave_do_usuario_sem_cache():
    gravados = []

    class _S3:
        def put_object(self, **kwargs):
            gravados.append(kwargs)

    chave = storage.put_label_photo(USUARIO, JPEG, client=_S3(), bucket="musclefit-uploads")
    assert label_photo.is_label_photo_key_of(chave, USUARIO)
    [gravado] = gravados
    assert gravado["Key"] == chave and gravado["Bucket"] == "musclefit-uploads" and gravado["Body"] == JPEG
    assert gravado["ContentType"] == label_photo.LABEL_PHOTO_CONTENT_TYPE
    assert gravado["CacheControl"] == storage.CACHE_CONTROL


def test_armazenamento_traduz_objeto_ausente_para_o_codigo_do_motor():
    from botocore.exceptions import ClientError

    class _S3:
        def get_object(self, **_kw):
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")

    with pytest.raises(storage.LabelPhotoStorageError) as exc:
        storage.read_label_photo(CHAVE, client=_S3(), bucket="b")
    assert exc.value.code == label_photo.ERR_LABEL_PHOTO_NOT_FOUND

    class _S3Fora:
        def get_object(self, **_kw):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "GetObject")

    with pytest.raises(storage.LabelPhotoStorageError) as exc:
        storage.read_label_photo(CHAVE, client=_S3Fora(), bucket="b")
    assert exc.value.code == label_photo.ERR_LABEL_PHOTO_STORAGE_FAILED
