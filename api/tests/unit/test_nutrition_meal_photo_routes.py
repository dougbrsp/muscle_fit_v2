"""Rotas da foto do prato (`app/nutrition_meal_photo.py`, L10) sem banco, S3, DynamoDB
nem Lambda reais: autenticação, acesso ao aluno, tipo e tamanho do motor ANTES da cota,
cota esgotada (429), ordem do despacho e o fechamento como `failed` quando a gravação ou
o despacho falham. O router é conferido no `app.main.app` real."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import async_jobs, nutrition_meal_photo as rota, nutrition_meal_photo_storage as storage
from app.domain.nutrition import ai_usage, diary, label_photo, meal_photo
from app.domain.nutrition import labels as lb
from app.domain.nutrition.ai_usage import MEAL_PHOTO_JOB_TYPE, AiFeature, AiUsageStatus, QuotaStatus
from app.nutrition_ai_quota import quota_exceeded_error
from app.nutrition_meal_photo_ai import PROMPT_VERSION
from app.security import Identity, get_current_identity

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

USUARIO = 7
ALUNO = 12
URL = "/nutrition/meals/photo"
DIA = "2026-11-02"
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 2048
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 2048
CHAVE = meal_photo.meal_photo_key(USUARIO, "0123456789abcdef0123456789abcdef")


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor()


class _Despacho:
    def __init__(self, monkeypatch, *, cota=True, falha_s3=False, falha_job=False, falha_invoke=False):
        self.eventos: list[tuple] = []
        self.cota = cota
        self.falha_s3 = falha_s3
        self.falha_job = falha_job
        self.falha_invoke = falha_invoke
        monkeypatch.setattr(rota, "get_conn", lambda: _Conn())
        monkeypatch.setattr(rota, "require_food_tables", lambda cur: None)
        monkeypatch.setattr(rota, "require_diary_tables", lambda cur: None)
        monkeypatch.setattr(rota, "fetch_target_user", self.fetch_user)
        monkeypatch.setattr(rota.nutrition_ai_quota, "open_usage", self.open_usage)
        monkeypatch.setattr(rota.nutrition_ai_quota, "finish_usage", self.finish_usage)
        monkeypatch.setattr(rota.nutrition_ai_quota, "link_job", self.link_job)
        monkeypatch.setattr(storage, "put_meal_photo", self.put)
        monkeypatch.setattr(async_jobs, "create_job", self.create_job)
        monkeypatch.setattr(async_jobs, "invoke_async", self.invoke)
        monkeypatch.setattr(async_jobs, "mark_failed", self.mark_failed)

    def fetch_user(self, cur, user_id):
        self.eventos.append(("fetch_user", user_id))
        return {"id": user_id}

    def open_usage(self, **kwargs):
        self.eventos.append(("open_usage", kwargs))
        if not self.cota:
            limite = ai_usage.daily_quota(AiFeature.MEAL_PHOTO)
            raise quota_exceeded_error(QuotaStatus(feature=AiFeature.MEAL_PHOTO, limit=limite, used=limite))
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
            raise storage.MealPhotoStorageError("s3 fora")
        return CHAVE

    def create_job(self, job_type, request=None, *, owner_user_id):
        self.eventos.append(("create_job", job_type, request, owner_user_id))
        if self.falha_job:
            raise RuntimeError("dynamodb fora")
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


def _client(role: str = "regular") -> TestClient:
    app = FastAPI()
    app.include_router(rota.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=USUARIO, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _b64(dados: bytes) -> str:
    return base64.b64encode(dados).decode("ascii").rstrip("=")


def _enviar(cliente, dados: bytes = JPEG, **extra):
    return cliente.post(URL, json={"image_base64": _b64(dados), "local_date": DIA, **extra})


def test_o_router_esta_no_app_real_e_exige_identidade():
    from app.main import app

    caminhos = app.openapi()["paths"]
    assert URL in caminhos and f"{URL}/usage" in caminhos
    cliente = TestClient(app, raise_server_exceptions=False)
    assert cliente.get(f"{URL}/usage").status_code in (401, 403)
    assert cliente.post(URL, json={"image_base64": _b64(JPEG), "local_date": DIA}).status_code in (401, 403)


def test_upload_despacha_na_ordem_do_contrato_e_responde_202(monkeypatch):
    despacho = _Despacho(monkeypatch)
    resposta = _enviar(_client(), meal_slot="lunch")
    assert resposta.status_code == 202, resposta.text
    assert resposta.json() == {
        "job_id": "job-1",
        "status": "pending",
        "poll_timeout_seconds": async_jobs.worker_lost_deadline_seconds(MEAL_PHOTO_JOB_TYPE),
    }
    assert despacho.nomes() == ["open_usage", "put", "create_job", "link_job", "invoke"]
    _, reserva = despacho.eventos[0]
    assert reserva == {"user_id": USUARIO, "feature": AiFeature.MEAL_PHOTO, "prompt_version": PROMPT_VERSION,
                       "image_count": meal_photo.MEAL_PHOTO_IMAGES_PER_READING}
    assert despacho.eventos[1] == ("put", USUARIO, len(JPEG))
    pedido = {"local_date": DIA, "meal_slot": "lunch", "diary_user_id": USUARIO}
    _, tipo, gravado, dono = despacho.eventos[2]
    assert tipo == MEAL_PHOTO_JOB_TYPE and dono == USUARIO
    assert gravado == {**pedido, "s3_key": CHAVE, "ai_usage_id": 55}
    assert "image_base64" not in gravado  # a foto nunca vai para o DynamoDB
    assert despacho.eventos[3] == ("link_job", 55, "job-1")
    _, env, evento = despacho.eventos[4]
    assert env == "PLAN_GENERATOR_FUNCTION_NAME"
    assert evento == {"job_id": "job-1", "job_kind": MEAL_PHOTO_JOB_TYPE, "user_id": USUARIO, "ai_usage_id": 55,
                      "s3_key": CHAVE, "payload": {**pedido, "job_id": "job-1"}}


def test_terceiro_exige_admin_ou_pt_antes_de_qualquer_coisa(monkeypatch):
    despacho = _Despacho(monkeypatch)
    resposta = _enviar(_client("regular"), user_id=ALUNO)
    assert resposta.status_code == 403
    assert despacho.eventos == []


def test_pt_le_o_prato_do_aluno_gastando_a_propria_cota_e_guardando_na_propria_chave(monkeypatch):
    despacho = _Despacho(monkeypatch)
    resposta = _enviar(_client("personal_trainer"), user_id=ALUNO)
    assert resposta.status_code == 202, resposta.text
    assert despacho.eventos[0] == ("fetch_user", ALUNO)
    assert despacho.eventos[1][1]["user_id"] == USUARIO
    assert despacho.eventos[2][1] == USUARIO
    _, _, gravado, dono = despacho.eventos[3]
    assert dono == USUARIO and gravado["diary_user_id"] == ALUNO


@pytest.mark.parametrize(
    ("dados", "status", "codigo"),
    [
        (b"\xff\xd8\xff" + b"0" * meal_photo.MEAL_PHOTO_MAX_BYTES, 413, meal_photo.ERR_MEAL_PHOTO_TOO_LARGE),
        (PNG, 415, meal_photo.ERR_MEAL_PHOTO_UNSUPPORTED_TYPE),
        (b"GIF89a" + b"g" * 64, 415, meal_photo.ERR_MEAL_PHOTO_UNSUPPORTED_TYPE),
        (b"<svg onload=alert(1)>", 415, meal_photo.ERR_MEAL_PHOTO_UNSUPPORTED_TYPE),
        (b"", 400, meal_photo.ERR_MEAL_PHOTO_EMPTY),
    ],
    ids=["grande", "png", "gif", "svg", "vazio"],
)
def test_tamanho_e_tipo_do_motor_antes_da_cota(monkeypatch, dados, status, codigo):
    despacho = _Despacho(monkeypatch)
    resposta = _enviar(_client(), dados)
    assert resposta.status_code == status
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == codigo and detalhe["message"] == lb.limitation_label(codigo)
    assert detalhe["max_bytes"] == meal_photo.MEAL_PHOTO_MAX_BYTES
    assert despacho.eventos == []


@pytest.mark.parametrize(
    ("foto", "status", "codigo"),
    [
        ("não é base64!", 400, meal_photo.ERR_MEAL_PHOTO_INVALID_ENCODING),
        ("A" * (label_photo.LABEL_PHOTO_MAX_BASE64_CHARS + 1), 413, meal_photo.ERR_MEAL_PHOTO_TOO_LARGE),
    ],
    ids=["alfabeto", "texto_grande"],
)
def test_base64_invalido_ou_grande_antes_de_decodificar(monkeypatch, foto, status, codigo):
    despacho = _Despacho(monkeypatch)
    resposta = _client().post(URL, json={"image_base64": foto, "local_date": DIA})
    assert resposta.status_code == status and resposta.json()["detail"]["error"] == codigo
    assert despacho.eventos == []


def test_tipo_declarado_que_nao_bate_com_os_bytes_nao_engana_a_rota(monkeypatch):
    """Vale a assinatura dos bytes: PNG num data URL de JPEG, num pedido com
    `Content-Type` de imagem ou com o tipo declarado no corpo nunca chega ao S3."""
    despacho = _Despacho(monkeypatch)
    cliente = _client()
    data_url = f"data:image/jpeg;base64,{_b64(PNG)}"
    assert cliente.post(URL, json={"image_base64": data_url, "local_date": DIA}).status_code == 400
    assert cliente.post(URL, json={"image_base64": _b64(PNG), "local_date": DIA, "content_type": "image/jpeg"}).status_code == 422
    corpo = json.dumps({"image_base64": _b64(PNG), "local_date": DIA})
    resposta = cliente.post(URL, content=corpo, headers={"Content-Type": "image/jpeg"})
    assert resposta.status_code in (415, 422)
    assert _enviar(cliente, PNG).json()["detail"]["error"] == meal_photo.ERR_MEAL_PHOTO_UNSUPPORTED_TYPE
    assert despacho.eventos == []


def test_refeicao_fora_do_vocabulario_nao_gasta_cota(monkeypatch):
    despacho = _Despacho(monkeypatch)
    resposta = _enviar(_client(), meal_slot="brunch")
    assert resposta.status_code == 400 and resposta.json()["detail"]["error"] == diary.ERR_INVALID_MEAL_SLOT
    assert despacho.eventos == []


def test_contrato_estrito(monkeypatch):
    _Despacho(monkeypatch)
    cliente = _client()
    assert cliente.post(URL, json={"image_base64": _b64(JPEG)}).status_code == 422  # sem o dia
    assert _enviar(cliente, nome="x").status_code == 422


def test_cota_esgotada_responde_429_sem_gravar_nada(monkeypatch):
    despacho = _Despacho(monkeypatch, cota=False)
    resposta = _enviar(_client())
    assert resposta.status_code == 429
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == ai_usage.ERR_AI_QUOTA_EXCEEDED
    assert detalhe["limit"] == ai_usage.MEAL_PHOTO_DAILY_QUOTA
    assert detalhe["message"] == lb.ai_quota_exceeded_label(AiFeature.MEAL_PHOTO)
    assert despacho.nomes() == ["open_usage"]


def test_falha_no_s3_fecha_a_linha_e_responde_503(monkeypatch):
    despacho = _Despacho(monkeypatch, falha_s3=True)
    resposta = _enviar(_client())
    assert resposta.status_code == 503
    assert resposta.json()["detail"]["error"] == meal_photo.ERR_MEAL_PHOTO_STORAGE_FAILED
    assert despacho.nomes() == ["open_usage", "put", "finish_usage"]
    assert despacho.eventos[-1][2] == {"status": AiUsageStatus.FAILED, "error_code": meal_photo.ERR_MEAL_PHOTO_STORAGE_FAILED}


def test_falha_ao_criar_o_job_fecha_a_linha(monkeypatch):
    despacho = _Despacho(monkeypatch, falha_job=True)
    resposta = _enviar(_client())
    assert resposta.status_code == 503
    assert despacho.nomes() == ["open_usage", "put", "create_job", "finish_usage"]


def test_falha_no_invoke_fecha_a_linha_e_o_job(monkeypatch):
    despacho = _Despacho(monkeypatch, falha_invoke=True)
    resposta = _enviar(_client())
    assert resposta.status_code == 503
    assert resposta.json()["detail"]["error"] == meal_photo.ERR_MEAL_PHOTO_DISPATCH_FAILED
    assert despacho.nomes() == ["open_usage", "put", "create_job", "link_job", "invoke", "finish_usage", "mark_failed"]
    assert despacho.eventos[5][2]["error_code"] == meal_photo.ERR_MEAL_PHOTO_DISPATCH_FAILED
    assert despacho.eventos[6][1] == "job-1"


def test_uso_do_dia_vem_do_servico_da_cota(monkeypatch):
    pedidos = []

    def _uso(conn, *, user_id, feature):
        pedidos.append((user_id, feature))
        return QuotaStatus(feature=AiFeature.MEAL_PHOTO, limit=ai_usage.MEAL_PHOTO_DAILY_QUOTA, used=3)

    monkeypatch.setattr(rota, "get_conn", lambda: _Conn())
    monkeypatch.setattr(rota.nutrition_ai_quota, "usage_today", _uso)
    resposta = _client().get(f"{URL}/usage")
    assert resposta.status_code == 200
    assert resposta.json() == {"feature": "meal_photo", "limit": ai_usage.MEAL_PHOTO_DAILY_QUOTA, "used": 3,
                               "remaining": ai_usage.MEAL_PHOTO_DAILY_QUOTA - 3, "allowed": True}
    assert pedidos == [(USUARIO, AiFeature.MEAL_PHOTO)]


def test_armazenamento_grava_na_chave_do_usuario_sem_cache():
    gravados = []

    class _S3:
        def put_object(self, **kwargs):
            gravados.append(kwargs)

    chave = storage.put_meal_photo(USUARIO, JPEG, client=_S3(), bucket="musclefit-uploads")
    assert meal_photo.is_meal_photo_key_of(chave, USUARIO)
    [gravado] = gravados
    assert gravado["Key"] == chave and gravado["Bucket"] == "musclefit-uploads" and gravado["Body"] == JPEG
    assert gravado["ContentType"] == meal_photo.MEAL_PHOTO_CONTENT_TYPE
    assert gravado["CacheControl"] == storage.CACHE_CONTROL


def test_armazenamento_traduz_as_falhas_para_o_codigo_do_motor(monkeypatch):
    from botocore.exceptions import ClientError

    def _s3(codigo):
        class _S3:
            def get_object(self, **_kw):
                raise ClientError({"Error": {"Code": codigo, "Message": "x"}}, "GetObject")

        return _S3()

    with pytest.raises(storage.MealPhotoStorageError) as exc:
        storage.read_meal_photo(CHAVE, client=_s3("NoSuchKey"), bucket="b")
    assert exc.value.code == meal_photo.ERR_MEAL_PHOTO_NOT_FOUND
    with pytest.raises(storage.MealPhotoStorageError) as exc:
        storage.read_meal_photo(CHAVE, client=_s3("AccessDenied"), bucket="b")
    assert exc.value.code == meal_photo.ERR_MEAL_PHOTO_STORAGE_FAILED
    monkeypatch.delenv("EQUIPMENT_PHOTOS_BUCKET", raising=False)
    with pytest.raises(storage.MealPhotoStorageError) as exc:
        storage.put_meal_photo(USUARIO, JPEG, client=_s3("x"))
    assert exc.value.code == meal_photo.ERR_MEAL_PHOTO_STORAGE_FAILED
