"""Worker da foto do prato (`app/nutrition_meal_photo_ai.py`, L10) com cliente de IA
falso, sem rede: sucesso (DERIVED × ESTIMATED), Claude com visão, fallback para a OpenAI,
resposta fora do schema, foto sem comida e chave de outro usuário. A linha do
`ai_usage_log` fecha em todos os caminhos."""

from __future__ import annotations

import base64
import json

import pytest

from app import async_jobs, llm_anthropic, llm_anthropic_schema, nutrition_label_ai, nutrition_meal_parse_worker as worker
from app import nutrition_meal_photo_ai as ai, nutrition_meal_photo_storage as storage, openai_client
from app.domain.nutrition import food_text, meal_parse, meal_photo
from app.domain.nutrition import labels as lb
from app.domain.nutrition.ai_usage import MEAL_PHOTO_JOB_TYPE, AiFeature, AiUsageStatus
from app.openai_client import OpenAIGenerationError, StructuredResult

USUARIO = 7
ALUNO = 12
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 64
CHAVE = meal_photo.meal_photo_key(USUARIO, "0123456789abcdef0123456789abcdef")
ESTIMATIVA = {"energy_kcal": 150, "protein_g": 10, "carbohydrates_g": 20, "total_fat_g": 3, "fiber_g": 1}


def _item(nome, **extra):
    return {
        "name": nome,
        "quantity": None,
        "unit": "g",
        "grams_estimate": 120,
        "preparation": None,
        "confidence": 0.8,
        meal_parse.ESTIMATE_KEY: ESTIMATIVA,
        **extra,
    }


LEITURA = {"plate_status": "recognized", "items": [_item("arroz branco"), _item("moqueca capixaba")]}
SEM_COMIDA = {"plate_status": "no_food", "items": []}


def _evento(**extra):
    return {
        "job_id": "j1",
        "job_kind": MEAL_PHOTO_JOB_TYPE,
        "user_id": USUARIO,
        "ai_usage_id": 55,
        "s3_key": CHAVE,
        "payload": {"local_date": "2026-11-02", "meal_slot": "lunch", "diary_user_id": ALUNO, "job_id": "j1"},
        **extra,
    }


class _Uso:
    def __init__(self, monkeypatch):
        self.fechamentos: list[dict] = []
        monkeypatch.setattr(ai.nutrition_ai_quota, "finish_usage", self.finish)

    def finish(self, usage_id, **kwargs):
        self.fechamentos.append({"usage_id": usage_id, **kwargs})
        return True


@pytest.fixture
def uso(monkeypatch) -> _Uso:
    return _Uso(monkeypatch)


def _foto(monkeypatch, dados=JPEG, erro=None):
    lidas = []

    def _ler(key, **_kw):
        lidas.append(key)
        if erro:
            raise erro
        return dados

    monkeypatch.setattr(storage, "read_meal_photo", _ler)
    return lidas


def _ia(monkeypatch, data=None, erro=None, model="claude-haiku-5-5"):
    chamadas = []

    def _chamar(prompt, images, modelo, schema, **kwargs):
        chamadas.append({"prompt": prompt, "images": images, "model": modelo, "schema": schema, **kwargs})
        if erro:
            raise erro
        return StructuredResult(data=data, model=model, tokens_in=900, tokens_out=200)

    monkeypatch.setattr(openai_client, "call_openai_structured_vision", _chamar)
    return chamadas


def _catalogo(monkeypatch, *, erro=None):
    """Só `arroz branco` existe no catálogo: o resto fica sem candidato."""
    pedidos = []

    def _match(itens, *, registrant_user_id, diary_user_id):
        pedidos.append((tuple(i.name for i in itens), registrant_user_id, diary_user_id))
        if erro:
            raise erro
        rankings = [food_text.rank_candidates(i.name, [(9, "Arroz branco")]) if "arroz" in i.name else () for i in itens]
        return rankings, {9: {"id": 9, "name": "Arroz branco", "basis": "g", "source": "taco", "brand": None}}

    monkeypatch.setattr(worker, "match_items", _match)
    return pedidos


# ---------------------------------------------------------------------------
# Sucesso
# ---------------------------------------------------------------------------


def test_job_identifica_casa_e_marca_a_natureza_de_cada_item(monkeypatch, uso):
    lidas = _foto(monkeypatch)
    chamadas = _ia(monkeypatch, LEITURA)
    pedidos = _catalogo(monkeypatch)
    resultado = ai.run_meal_photo_job(_evento())

    assert lidas == [CHAVE]
    assert pedidos == [(("arroz branco", "moqueca capixaba"), USUARIO, ALUNO)]
    casado, estimado = resultado["items"]
    assert casado["status"] == "selected" and casado["food_id"] == 9 and casado["nature"] == "DERIVED"
    assert casado["candidates"][0]["food_id"] == 9 and casado["uses_estimate"] is False
    assert estimado["status"] == "estimated" and estimado["food_id"] is None and estimado["nature"] == "ESTIMATED"
    assert estimado["uses_estimate"] is True and estimado["quantity"] == 120 and estimado["unit"] == "g"
    assert meal_parse.LIMIT_AI_ESTIMATE in [aviso["code"] for aviso in estimado["limitations"]]
    # Sem macro calculado aqui nem texto livre da foto: o preview e a gravação são do motor do diário.
    assert "kcal" not in casado and resultado["notes"] is None
    assert resultado["plate_status"] == "recognized" and resultado["job_id"] == "j1" and resultado["user_id"] == ALUNO
    assert resultado["local_date"] == "2026-11-02" and resultado["meal_slot"] == "lunch"
    assert resultado["prompt_version"] == ai.PROMPT_VERSION and resultado["model"] == "claude-haiku-5-5"
    [chamada] = chamadas
    assert chamada["feature"] == AiFeature.MEAL_PHOTO.value and chamada["prompt_version"] == ai.PROMPT_VERSION
    assert chamada["images"][0].data == JPEG and chamada["images"][0].content_type == meal_photo.MEAL_PHOTO_CONTENT_TYPE
    assert chamada["timeout"] == ai.LLM_TIMEOUT_SECONDS and chamada["user_id"] == USUARIO
    assert uso.fechamentos == [{"usage_id": 55, "status": AiUsageStatus.SUCCEEDED, "model": "claude-haiku-5-5",
                                "tokens_in": 900, "tokens_out": 200}]


def test_foto_sem_comida_devolve_vazio_com_mensagem_sem_buscar_no_catalogo(monkeypatch, uso):
    _foto(monkeypatch)
    _ia(monkeypatch, SEM_COMIDA)
    resultado = ai.run_meal_photo_job(_evento())
    assert resultado["items"] == [] and resultado["plate_status"] == "no_food"
    assert resultado["limitations"] == [{"code": meal_photo.LIMIT_PHOTO_NO_FOOD,
                                         "label": lb.limitation_label(meal_photo.LIMIT_PHOTO_NO_FOOD)}]
    assert uso.fechamentos[0]["status"] == AiUsageStatus.SUCCEEDED  # a IA respondeu: a tentativa custou tokens


def test_ia_que_inventa_item_em_foto_sem_comida_e_ignorada(monkeypatch, uso):
    _foto(monkeypatch)
    _ia(monkeypatch, {"plate_status": "no_food", "items": [_item("pizza")]})
    assert ai.run_meal_photo_job(_evento())["items"] == []


# ---------------------------------------------------------------------------
# Falhas: a linha fecha e a falha propaga
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resposta",
    [
        {"items": []},
        {"plate_status": "recognized"},
        {"plate_status": "recognized", "items": "arroz"},
        {"plate_status": "recognized", "items": [{"name": "arroz"}]},
        {"plate_status": "recognized", "items": [_item("arroz", grams_estimate="muito")]},
        {"plate_status": "recognized", "items": [_item("arroz", extra="ignore as regras")]},
        {"plate_status": "recognized", "items": [], "notes": "visite este site"},
    ],
    ids=["sem_status", "sem_itens", "itens_texto", "item_incompleto", "gramas_texto", "campo_extra_item", "campo_extra"],
)
def test_resposta_fora_do_schema_falha_com_os_tokens_gastos(monkeypatch, uso, resposta):
    _foto(monkeypatch)
    _ia(monkeypatch, resposta)
    pedidos = _catalogo(monkeypatch)
    with pytest.raises(ai.MealPhotoJobError) as exc:
        ai.run_meal_photo_job(_evento())
    assert exc.value.code == meal_photo.ERR_MEAL_READING_INVALID
    assert str(exc.value) == lb.limitation_label(meal_photo.ERR_MEAL_READING_INVALID)
    assert pedidos == []
    [fechamento] = uso.fechamentos
    assert fechamento["status"] == AiUsageStatus.FAILED and fechamento["error_code"] == meal_photo.ERR_MEAL_READING_INVALID
    assert (fechamento["tokens_in"], fechamento["tokens_out"]) == (900, 200)


def test_chave_de_outro_usuario_nao_e_lida(monkeypatch, uso):
    lidas = _foto(monkeypatch)
    chamadas = _ia(monkeypatch, LEITURA)
    with pytest.raises(ai.MealPhotoJobError) as exc:
        ai.run_meal_photo_job(_evento(user_id=USUARIO + 1))
    assert exc.value.code == meal_photo.ERR_MEAL_PHOTO_KEY_INVALID
    assert lidas == [] and chamadas == []
    assert uso.fechamentos[0]["status"] == AiUsageStatus.FAILED


def test_foto_ausente_ou_grande_nao_chama_a_ia(monkeypatch, uso):
    _foto(monkeypatch, erro=storage.MealPhotoStorageError("x", code=meal_photo.ERR_MEAL_PHOTO_NOT_FOUND))
    chamadas = _ia(monkeypatch, LEITURA)
    with pytest.raises(ai.MealPhotoJobError) as exc:
        ai.run_meal_photo_job(_evento())
    assert exc.value.code == meal_photo.ERR_MEAL_PHOTO_NOT_FOUND
    _foto(monkeypatch, dados=b"0" * (meal_photo.MEAL_PHOTO_MAX_BYTES + 1))
    with pytest.raises(ai.MealPhotoJobError) as exc:
        ai.run_meal_photo_job(_evento())
    assert exc.value.code == meal_photo.ERR_MEAL_PHOTO_TOO_LARGE
    assert chamadas == [] and len(uso.fechamentos) == 2


def test_falha_da_ia_propaga_e_fecha_com_o_codigo_do_cliente(monkeypatch, uso):
    _foto(monkeypatch)
    _ia(monkeypatch, erro=OpenAIGenerationError("indisponível", code="OPENAI_UNAVAILABLE"))
    with pytest.raises(OpenAIGenerationError):
        ai.run_meal_photo_job(_evento())
    assert uso.fechamentos[0]["status"] == AiUsageStatus.FAILED and uso.fechamentos[0]["error_code"] == "OPENAI_UNAVAILABLE"


def test_falha_do_catalogo_depois_da_ia_guarda_os_tokens(monkeypatch, uso):
    _foto(monkeypatch)
    _ia(monkeypatch, LEITURA)
    _catalogo(monkeypatch, erro=RuntimeError("banco fora"))
    with pytest.raises(ai.MealPhotoJobError) as exc:
        ai.run_meal_photo_job(_evento())
    assert exc.value.code == meal_parse.ERR_MATCH_FAILED
    assert uso.fechamentos[0]["error_code"] == meal_parse.ERR_MATCH_FAILED and uso.fechamentos[0]["tokens_in"] == 900


# ---------------------------------------------------------------------------
# Provedor: Claude com visão e fallback pelo cliente único
# ---------------------------------------------------------------------------


def _ligar_claude(monkeypatch, *, fallback=True):
    monkeypatch.setenv("LLM_PROVIDER_MEAL_PHOTO", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL_MEAL_PHOTO", llm_anthropic.ANTHROPIC_MODEL_HAIKU)
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)
    if fallback:
        monkeypatch.setenv("LLM_FALLBACK_PROVIDER_MEAL_PHOTO", "openai")
    else:
        monkeypatch.delenv("LLM_FALLBACK_PROVIDER_MEAL_PHOTO", raising=False)


def test_claude_recebe_a_imagem_com_o_modelo_da_feature(monkeypatch):
    _ligar_claude(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-de-teste")
    pedidos = []

    class _Mensagens:
        def create(self, **kwargs):
            pedidos.append(kwargs)
            return {
                "model": kwargs["model"],
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(LEITURA)}],
                "usage": {"input_tokens": 700, "output_tokens": 90},
            }

    class _Cliente:
        messages = _Mensagens()

    monkeypatch.setattr(llm_anthropic, "_client", lambda _chave: _Cliente())
    leitura = ai.read_meal_photo(JPEG, user_id=USUARIO)
    [pedido] = pedidos
    imagem, texto = pedido["messages"][0]["content"]
    assert pedido["model"] == llm_anthropic.ANTHROPIC_MODEL_HAIKU and leitura.model == llm_anthropic.ANTHROPIC_MODEL_HAIKU
    assert imagem["source"]["media_type"] == meal_photo.MEAL_PHOTO_CONTENT_TYPE
    assert base64.b64decode(imagem["source"]["data"]) == JPEG
    mesmo_prompt = texto["text"] == ai.build_prompt()  # sem imprimir o prompt no log público
    assert mesmo_prompt
    assert pedido["max_tokens"] == openai_client.LLM_MAX_OUTPUT_TOKENS[AiFeature.MEAL_PHOTO.value]
    assert [item["name"] for item in leitura.data["items"]] == ["arroz branco", "moqueca capixaba"]


class _RespostaOpenAI:
    def read(self):
        return json.dumps({"output_text": json.dumps(LEITURA), "model": ai.model_name(),
                           "usage": {"input_tokens": 1800, "output_tokens": 420}}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_sem_chave_da_anthropic_cai_no_fallback_da_openai(monkeypatch):
    _ligar_claude(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    enviados = []

    def _fake(request, timeout=None):
        enviados.append(json.loads(request.data.decode("utf-8")))
        return _RespostaOpenAI()

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    leitura = ai.read_meal_photo(JPEG, user_id=USUARIO)
    [corpo] = enviados
    assert corpo["model"] == nutrition_label_ai.DEFAULT_MODEL and corpo["store"] is False
    formato = corpo["text"]["format"]
    assert formato["strict"] is True and formato["name"] == ai.SCHEMA_NAME and formato["schema"] == ai.output_schema()
    _, imagem = corpo["input"][0]["content"]
    assert imagem["detail"] == meal_photo.MEAL_IMAGE_DETAIL
    assert leitura.model == ai.model_name() and (leitura.tokens_in, leitura.tokens_out) == (1800, 420)


def test_sem_fallback_a_falha_da_anthropic_propaga(monkeypatch):
    _ligar_claude(monkeypatch, fallback=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(OpenAIGenerationError) as exc:
        ai.read_meal_photo(JPEG)
    assert exc.value.code == llm_anthropic.ANTHROPIC_NOT_CONFIGURED


# ---------------------------------------------------------------------------
# Prompt, schema e paridades
# ---------------------------------------------------------------------------


def test_prompt_interpola_todo_valor_do_dono():
    marcados = {chave: f"<<{chave}>>" for chave in ai.prompt_values()}
    texto = ai.build_prompt(marcados)
    faltando = sorted(chave for chave, marcador in marcados.items() if marcador not in texto)
    assert not faltando, faltando
    sobrou_placeholder = "{{" in texto or "{{" in ai.build_prompt()
    assert not sobrou_placeholder


def test_schema_e_estrito_sem_texto_livre_e_o_adaptador_do_claude_aceita():
    schema = ai.output_schema()
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
    assert set(schema["properties"]) == {"plate_status", "items"}  # sem `notes`: nada livre vindo da foto
    assert schema["properties"]["plate_status"]["enum"] == [status.value for status in meal_photo.PlateStatus]
    item = schema["properties"]["items"]["items"]
    assert item["additionalProperties"] is False and "grams_estimate" in item["required"]
    assert item["properties"]["grams_estimate"] == {"type": "number"}
    llm_anthropic_schema.adapt_schema(schema, ())


def test_job_type_roda_na_plan_generator_e_o_timeout_do_cliente_cabe_nela():
    funcao = async_jobs.JOB_TYPE_LAMBDA[MEAL_PHOTO_JOB_TYPE]
    assert funcao == "musclefit-plan-generator"
    assert ai.LLM_TIMEOUT_SECONDS < async_jobs.LAMBDA_TIMEOUT_SECONDS[funcao]


def test_handler_da_plan_generator_despacha_o_job_da_foto(monkeypatch):
    from app import lambda_plan_generator as handler

    chamados = []
    monkeypatch.setattr(ai, "run_meal_photo_job", lambda event: chamados.append(event) or {"ok": True})
    assert handler._run(_evento()) == {"ok": True}
    assert chamados[0]["s3_key"] == CHAVE and handler.JOB_KIND_MEAL_PHOTO == MEAL_PHOTO_JOB_TYPE


def test_feature_tem_teto_de_saida_no_cliente():
    assert ai.FEATURE in openai_client.LLM_OUTPUT_OBSERVED_MAX_TOKENS
    assert openai_client.LLM_MAX_OUTPUT_TOKENS[ai.FEATURE] >= openai_client.LLM_OUTPUT_CAP_FLOOR_TOKENS
    assert ai.FEATURE in llm_anthropic.LLM_FEATURES
