"""Worker da leitura da foto do rótulo (`app/nutrition_label_ai.py`, L6) e a
entrada de imagem do cliente (`openai_client.call_openai_structured_vision`).
Rede, S3 e `ai_usage_log` sempre dublados; nenhuma chamada paga.

- prompt: só interpola valores do dono (nenhum dígito escrito à mão) e o schema
  sai dos campos da RDC 429 do motor;
- corpo HTTP: `input_text` + `input_image` (data URL, `detail` do dono), schema
  strict, `store=false`, teto de saída da feature `label_photo`; alternativa
  `json_object` da decisão D-E;
- job: fecha a linha do `ai_usage_log` em todos os caminhos (sucesso, chave de
  outro usuário, foto expirada, falha da OpenAI, forma inválida);
- paridade: feature × teto do cliente, modelo × Terraform, timeout × Lambda,
  job type × Lambda da decisão E8.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

import pytest

from app import async_jobs, nutrition_label_ai as ai, nutrition_label_storage as storage, openai_client
from app.domain.nutrition import catalog, label_photo
from app.domain.nutrition import labels as lb
from app.domain.nutrition.ai_usage import LABEL_PHOTO_JOB_TYPE, AiFeature, AiUsageStatus
from app.openai_client import ImageInput, OpenAIGenerationError

INFRA = Path(__file__).resolve().parents[3] / "infra"
UUID = "0123456789abcdef0123456789abcdef"
USUARIO = 7
CHAVE = label_photo.label_photo_key(USUARIO, UUID)
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64

LEITURA = {
    "product_name": "Biscoito de aveia", "brand": None, "barcode": None, "basis": "g", "serving_size": 30,
    "household_measure": "3 unidades", "servings_per_package": 6,
    "per_100": {"energy_kcal": 450, "energy_kj": 1884, "carbohydrates_g": 65, "total_sugars_g": 20,
                "added_sugars_g": 15, "protein_g": 8, "total_fat_g": 18, "saturated_fat_g": 6, "trans_fat_g": 0,
                "fiber_g": 4, "sodium_mg": 300},
    "per_serving": {"energy_kcal": 135, "energy_kj": 565.2, "carbohydrates_g": 19.5, "total_sugars_g": 6,
                    "added_sugars_g": 4.5, "protein_g": 2.4, "total_fat_g": 5.4, "saturated_fat_g": 1.8,
                    "trans_fat_g": 0, "fiber_g": 1.2, "sodium_mg": 90},
    "legibility": "ok",
    "warnings": ["Código de barras fora da foto."],
}


class _Resposta:
    def __init__(self, data):
        self._data = data

    def read(self):
        return json.dumps(self._data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(openai_client, "_sleep", lambda s: None)
    monkeypatch.delenv(ai.MODEL_ENV_VAR, raising=False)


def _mock_openai(monkeypatch, saida, usage=None):
    enviados = []

    def _fake(request, timeout=None):
        enviados.append({"body": json.loads(request.data.decode("utf-8")), "timeout": timeout})
        texto = saida if isinstance(saida, str) else json.dumps(saida)
        return _Resposta({"output_text": texto, "model": "gpt-5.4-mini-2026-03-17",
                          "usage": usage or {"input_tokens": 1800, "output_tokens": 420}})

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    return enviados


class _Uso:
    """Dublê do `nutrition_ai_quota.finish_usage` (o worker sempre fecha a linha)."""

    def __init__(self, monkeypatch):
        self.fechamentos: list[dict] = []
        monkeypatch.setattr(ai.nutrition_ai_quota, "finish_usage", self._finish)

    def _finish(self, usage_id, **kwargs):
        self.fechamentos.append({"usage_id": usage_id, **kwargs})
        return True


def _foto(monkeypatch, dados=JPEG, erro=None):
    lidas = []

    def _ler(key, **_kw):
        lidas.append(key)
        if erro:
            raise erro
        return dados

    monkeypatch.setattr(storage, "read_label_photo", _ler)
    return lidas


def _evento(**extra):
    return {"job_id": "j1", "job_kind": LABEL_PHOTO_JOB_TYPE, "user_id": USUARIO, "ai_usage_id": 55, "s3_key": CHAVE,
            **extra}


# ---------------------------------------------------------------------------
# Prompt e schema
# ---------------------------------------------------------------------------


# M103 P1: `test_prompt_so_tem_numero_interpolado_do_dono` (zero dígito à mão,
# norma e campos no texto) mora em `api/privado/tests/unit/test_nutrition_label_prompt.py`.
def test_prompt_interpola_todo_valor_do_dono():
    """Sem citar o texto: cada valor do dono entra como marcador e nenhum
    placeholder sobra (o resultado vai para um booleano: log público)."""
    marcados = {chave: f"<<{chave}>>" for chave in ai.prompt_values()}
    texto = ai.build_prompt(marcados)
    faltando = sorted(chave for chave, marcador in marcados.items() if marcador not in texto)
    assert not faltando, faltando
    sobrou_placeholder = "{{" in texto or "{{" in ai.build_prompt()
    assert not sobrou_placeholder
    assert lb.LABEL_REGULATION_REFERENCE == ai.prompt_values()["regulation"]
    codigos_presentes = all(f"- {codigo}:" in texto for codigo in label_photo.LABEL_NUTRIENT_FIELDS)
    assert codigos_presentes


def test_schema_sai_dos_campos_do_motor_e_e_strict():
    schema = ai.output_schema()
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
    for coluna in ("per_100", "per_serving"):
        bloco = schema["properties"][coluna]
        assert bloco["required"] == list(label_photo.LABEL_NUTRIENT_FIELDS)
        assert bloco["additionalProperties"] is False
    assert schema["properties"]["basis"]["enum"] == [*(b.value for b in catalog.FOOD_BASES), None]
    assert schema["properties"]["legibility"]["enum"] == [item.value for item in label_photo.LabelLegibility]
    assert schema["properties"]["not_significant"]["items"]["enum"] == list(catalog.PER_100_FIELDS)


# ---------------------------------------------------------------------------
# Cliente: entrada de imagem
# ---------------------------------------------------------------------------


def test_corpo_da_visao_tem_texto_imagem_schema_strict_e_teto(monkeypatch, caplog):
    enviados = _mock_openai(monkeypatch, LEITURA)
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        leitura = ai.read_label(JPEG, user_id=USUARIO)
    [envio] = enviados
    corpo = envio["body"]
    assert envio["timeout"] == ai.LLM_TIMEOUT_SECONDS
    assert corpo["model"] == ai.DEFAULT_MODEL and corpo["store"] is False
    assert corpo["max_output_tokens"] == openai_client.LLM_MAX_OUTPUT_TOKENS[AiFeature.LABEL_PHOTO.value]
    assert "prompt_cache_key" not in corpo
    [mensagem] = corpo["input"]
    texto, imagem = mensagem["content"]
    # Sem `texto == {...}`: a falha imprimiria o prompt no log público do CI.
    mesmo_prompt = texto["text"] == ai.build_prompt()
    assert mensagem["role"] == "user" and texto["type"] == "input_text" and set(texto) == {"type", "text"}
    assert mesmo_prompt
    assert imagem["type"] == "input_image" and imagem["detail"] == label_photo.LABEL_IMAGE_DETAIL
    prefixo = f"data:{label_photo.LABEL_PHOTO_CONTENT_TYPE};base64,"
    assert imagem["image_url"].startswith(prefixo)
    assert base64.b64decode(imagem["image_url"][len(prefixo):]) == JPEG
    formato = corpo["text"]["format"]
    assert formato["type"] == "json_schema" and formato["strict"] is True and formato["name"] == ai.SCHEMA_NAME
    assert formato["schema"] == ai.output_schema()
    assert leitura.model == "gpt-5.4-mini-2026-03-17" and (leitura.tokens_in, leitura.tokens_out) == (1800, 420)
    assert leitura.data["per_100"]["energy_kcal"] == 450
    # `llm.usage` com a feature do dono e a versão do prompt; nunca a imagem nem o prompt.
    eventos = [json.loads(r.getMessage()) for r in caplog.records if r.name == "musclefit.events"]
    [uso] = [e for e in eventos if e.get("event") == "llm.usage"]
    assert uso["feature"] == AiFeature.LABEL_PHOTO.value and uso["prompt_version"] == ai.PROMPT_VERSION
    prompt_no_evento = ai.build_prompt()[:60] in json.dumps(eventos, ensure_ascii=False)
    assert "base64" not in json.dumps(eventos) and not prompt_no_evento


def test_modelo_da_variavel_de_ambiente(monkeypatch):
    enviados = _mock_openai(monkeypatch, LEITURA)
    monkeypatch.setenv(ai.MODEL_ENV_VAR, "gpt-5.4")
    ai.read_label(JPEG)
    assert enviados[0]["body"]["model"] == "gpt-5.4"


def test_alternativa_json_object_nao_manda_schema(monkeypatch):
    enviados = _mock_openai(monkeypatch, LEITURA)
    ai.read_label(JPEG, response_format=openai_client.STRUCTURED_FORMAT_JSON_OBJECT)
    assert enviados[0]["body"]["text"]["format"] == {"type": "json_object"}


def test_visao_recusa_entrada_invalida():
    with pytest.raises(ValueError):
        openai_client.call_openai_structured_vision("p", [], "m", {}, schema_name="s")
    with pytest.raises(ValueError):
        openai_client.call_openai_structured_vision("p", [ImageInput(JPEG, "image/jpeg", "max")], "m", {}, schema_name="s")
    with pytest.raises(ValueError):
        openai_client.call_openai_structured_vision(
            "p", [ImageInput(JPEG, "image/jpeg", "high")], "m", {}, schema_name="s", response_format="xml"
        )


def test_forma_invalida_vira_erro_com_os_tokens(monkeypatch):
    _mock_openai(monkeypatch, {"per_100": "nada"})
    with pytest.raises(ai.LabelPhotoJobError) as exc:
        ai.read_label(JPEG)
    assert exc.value.code == label_photo.ERR_LABEL_READING_INVALID
    assert (exc.value.tokens_in, exc.value.tokens_out) == (1800, 420)
    assert str(exc.value) == lb.limitation_label(label_photo.ERR_LABEL_READING_INVALID)


def test_json_invalido_ou_recusa_e_falha_do_cliente(monkeypatch):
    _mock_openai(monkeypatch, "isto não é json")
    with pytest.raises(OpenAIGenerationError):
        ai.read_label(JPEG)


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


def test_job_com_sucesso_fecha_a_linha_e_devolve_o_formulario(monkeypatch):
    uso = _Uso(monkeypatch)
    lidas = _foto(monkeypatch)
    _mock_openai(monkeypatch, LEITURA)
    resultado = ai.run_label_job(_evento())
    assert lidas == [CHAVE]
    assert uso.fechamentos == [{"usage_id": 55, "status": AiUsageStatus.SUCCEEDED, "model": "gpt-5.4-mini-2026-03-17",
                                "tokens_in": 1800, "tokens_out": 420}]
    assert resultado["readable"] is True and resultado["legibility"] == "ok"
    assert resultado["food"]["name"] == "Biscoito de aveia" and resultado["food"]["energy_kcal"] == 450
    assert resultado["errors"] == [] and resultado["warnings"] == []
    assert resultado["ai_notes"] == ["Código de barras fora da foto."]
    assert resultado["prompt_version"] == ai.PROMPT_VERSION
    assert len(json.dumps(resultado, ensure_ascii=False).encode("utf-8")) < 8_000  # DynamoDB: muito abaixo de 400 KB


def test_job_corta_texto_no_tamanho_do_cadastro_e_limita_as_notas(monkeypatch):
    _Uso(monkeypatch)
    _foto(monkeypatch)
    _mock_openai(monkeypatch, {**LEITURA, "product_name": "N" * 400, "warnings": [f"nota {i}" for i in range(9)]})
    resultado = ai.run_label_job(_evento())
    assert len(resultado["food"]["name"]) == 255
    assert len(resultado["ai_notes"]) == label_photo.AI_NOTES_MAX


@pytest.mark.parametrize(
    ("evento", "erro_s3", "codigo"),
    [
        ({"user_id": 8}, None, label_photo.ERR_LABEL_PHOTO_KEY_INVALID),
        ({"s3_key": "exercise-cards/1/v1/card.png"}, None, label_photo.ERR_LABEL_PHOTO_KEY_INVALID),
        ({}, storage.LabelPhotoStorageError("x", code=label_photo.ERR_LABEL_PHOTO_NOT_FOUND),
         label_photo.ERR_LABEL_PHOTO_NOT_FOUND),
        ({}, storage.LabelPhotoStorageError("x"), label_photo.ERR_LABEL_PHOTO_STORAGE_FAILED),
    ],
)
def test_job_sem_a_foto_do_usuario_falha_fechando_a_linha(monkeypatch, evento, erro_s3, codigo):
    uso = _Uso(monkeypatch)
    lidas = _foto(monkeypatch, erro=erro_s3)
    enviados = _mock_openai(monkeypatch, LEITURA)
    with pytest.raises(ai.LabelPhotoJobError) as exc:
        ai.run_label_job(_evento(**evento))
    assert exc.value.code == codigo and str(exc.value) == lb.limitation_label(codigo)
    assert enviados == []  # nenhuma chamada paga
    if codigo == label_photo.ERR_LABEL_PHOTO_KEY_INVALID:
        assert lidas == []  # nem lê a foto de outro usuário
    [fechamento] = uso.fechamentos
    assert fechamento["status"] == AiUsageStatus.FAILED and fechamento["error_code"] == codigo


def test_job_com_foto_grande_demais_nao_chama_a_ia(monkeypatch):
    uso = _Uso(monkeypatch)
    _foto(monkeypatch, dados=b"\xff\xd8\xff" + b"0" * label_photo.LABEL_PHOTO_MAX_BYTES)
    enviados = _mock_openai(monkeypatch, LEITURA)
    with pytest.raises(ai.LabelPhotoJobError):
        ai.run_label_job(_evento())
    assert enviados == [] and uso.fechamentos[0]["error_code"] == label_photo.ERR_LABEL_PHOTO_TOO_LARGE


def test_job_com_falha_da_openai_fecha_com_o_codigo_dela(monkeypatch):
    uso = _Uso(monkeypatch)
    _foto(monkeypatch)

    def _falha(request, timeout=None):
        raise TimeoutError()

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _falha)
    with pytest.raises(OpenAIGenerationError):
        ai.run_label_job(_evento())
    [fechamento] = uso.fechamentos
    assert fechamento["status"] == AiUsageStatus.FAILED and fechamento["error_code"] == openai_client.OPENAI_TIMEOUT


def test_job_com_forma_invalida_fecha_com_os_tokens_gastos(monkeypatch):
    uso = _Uso(monkeypatch)
    _foto(monkeypatch)
    _mock_openai(monkeypatch, {"legibility": ["x"]})
    with pytest.raises(ai.LabelPhotoJobError):
        ai.run_label_job(_evento())
    [fechamento] = uso.fechamentos
    assert fechamento["error_code"] == label_photo.ERR_LABEL_READING_INVALID
    assert (fechamento["tokens_in"], fechamento["tokens_out"]) == (1800, 420)


def test_job_ilegivel_e_sucesso_sem_pre_preencher(monkeypatch):
    uso = _Uso(monkeypatch)
    _foto(monkeypatch)
    _mock_openai(monkeypatch, {**LEITURA, "legibility": "unreadable"})
    resultado = ai.run_label_job(_evento())
    assert resultado["readable"] is False
    assert [e["code"] for e in resultado["errors"]] == [label_photo.ERR_LABEL_UNREADABLE]
    assert uso.fechamentos[0]["status"] == AiUsageStatus.SUCCEEDED  # a IA respondeu: a tentativa custou tokens


def test_handler_da_plan_generator_despacha_o_job_do_rotulo(monkeypatch):
    from app import lambda_plan_generator as handler

    chamados = []
    monkeypatch.setattr(ai, "run_label_job", lambda event: chamados.append(event) or {"ok": True})
    assert handler._run(_evento()) == {"ok": True}
    assert chamados[0]["s3_key"] == CHAVE
    assert handler.JOB_KIND_LABEL_PHOTO == LABEL_PHOTO_JOB_TYPE


# ---------------------------------------------------------------------------
# Paridades
# ---------------------------------------------------------------------------


def test_feature_do_dono_tem_teto_de_saida_no_cliente():
    assert ai.FEATURE == AiFeature.LABEL_PHOTO.value
    assert AiFeature.LABEL_PHOTO.value in openai_client.LLM_OUTPUT_OBSERVED_MAX_TOKENS
    assert openai_client.LLM_MAX_OUTPUT_TOKENS[AiFeature.LABEL_PHOTO.value] >= openai_client.LLM_OUTPUT_CAP_FLOOR_TOKENS


def test_job_type_roda_na_lambda_de_imagem_e_o_timeout_do_cliente_cabe_nela():
    funcao = async_jobs.JOB_TYPE_LAMBDA[LABEL_PHOTO_JOB_TYPE]
    assert funcao == "musclefit-plan-generator"
    assert ai.LLM_TIMEOUT_SECONDS < async_jobs.LAMBDA_TIMEOUT_SECONDS[funcao]


def test_modelo_padrao_e_o_default_do_terraform_e_a_env_chega_na_plan_generator():
    texto = (INFRA / "lambda_ai_generators.tf").read_text(encoding="utf-8")
    variavel = texto[texto.index('variable "openai_model_nutrition_vision"'):]
    variavel = variavel[: variavel.index("\n}\n")]
    assert re.search(r'default\s*=\s*"([^"]+)"', variavel).group(1) == ai.DEFAULT_MODEL
    funcao = texto[texto.index('resource "aws_lambda_function" "musclefit_plan_generator"'):]
    funcao = funcao[: funcao.index('resource "aws_lambda_function" "musclefit_cardio_agent"')]
    assert re.search(rf"{ai.MODEL_ENV_VAR}\s*=\s*var\.openai_model_nutrition_vision", funcao)


# ---------------------------------------------------------------------------
# Critério do eval pago (`tests/eval/criterios_llm.avaliar_rotulo`), sem LLM
# ---------------------------------------------------------------------------


def test_criterio_do_eval_aprova_a_leitura_certa_e_aponta_cada_erro():
    from tests.eval.criterios_llm import avaliar_rotulo

    revisao = label_photo.review_reading(LEITURA)
    resultado = ai.job_result(revisao, ai.LabelReading(LEITURA, "m", 1, 1))
    caso = {"expect": {"legibilidade_aceita": ["ok"], "food": {"energy_kcal": 450, "basis": "g", "serving_size": 30},
                       "per_serving": {"energy_kcal": 135}}}
    assert avaliar_rotulo({"bruto": LEITURA, "resultado": resultado}, caso) == []
    errado = {"expect": {**caso["expect"], "food": {"energy_kcal": 451}, "barcode": "7891234567895",
                         "nome_contem": "queijo", "avisos_obrigatorios": ["label_basis_assumed"]}}
    problemas = avaliar_rotulo({"bruto": LEITURA, "resultado": resultado}, errado)
    assert len(problemas) == 4, problemas
    borrado = {"desenho": {"tabela": {"porcao": "30 g", "linhas": [["Valor energético (kcal)", [450, 135, 7], 0]]}},
               "expect": {"legibilidade_aceita": ["partial", "unreadable"], "sem_valor_inventado": True}}
    inventado = {**LEITURA, "per_100": {"energy_kcal": 999}, "per_serving": {}}
    problemas = avaliar_rotulo({"bruto": inventado, "resultado": resultado}, borrado)
    assert any("999" in p for p in problemas) and any("legibilidade" in p for p in problemas)
