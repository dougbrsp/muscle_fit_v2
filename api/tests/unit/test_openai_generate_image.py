"""`openai_client.generate_image` (POST /v1/images/generations) e a
classificacao de erro de `OpenAIGenerationError`.

Sem rede: `urllib.request.urlopen` e substituido por um duble que devolve uma
fila de respostas/erros; o relogio (`_monotonic`) e a espera (`_sleep`) sao
falsos, entao o teste de retry nao dorme.

Rodar: cd api && python -m pytest tests/unit/test_openai_generate_image.py
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
from decimal import Decimal

import pytest

from app import openai_client as oc
from app.exercise_description_ai import ExerciseDescriptionError

PNG = b"\x89PNG\r\n\x1a\n" + b"conteudo-de-teste" * 4
PROMPT = "PROMPT-SECRETO-DO-CARD"
KEY = "sk-chave-de-teste-nao-vaza"


class _Resposta:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok(image: bytes = PNG, **extra) -> dict:
    return {"created": 1, "data": [{"b64_json": base64.b64encode(image).decode()}], **extra}


def _http_error(code: int, body: dict | str = "") -> urllib.error.HTTPError:
    raw = json.dumps(body).encode() if isinstance(body, dict) else body.encode()
    return urllib.error.HTTPError(oc._IMAGES_URL, code, "erro", {}, io.BytesIO(raw))


class _Relogio:
    def __init__(self):
        self.agora = 1000.0
        self.esperas: list[float] = []

    def monotonic(self):
        return self.agora

    def sleep(self, segundos):
        self.esperas.append(segundos)
        self.agora += segundos


@pytest.fixture
def openai(monkeypatch):
    """Fila de respostas (dict = 200 com esse JSON; bytes = 200 cru;
    excecao = levantada). `duracao` = segundos que cada chamada "leva"."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    for name in ("OPENAI_IMAGE_MODEL", "OPENAI_IMAGE_QUALITY", "OPENAI_IMAGE_SIZE", "OPENAI_IMAGE_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    relogio = _Relogio()
    monkeypatch.setattr(oc, "_monotonic", relogio.monotonic)
    monkeypatch.setattr(oc, "_sleep", relogio.sleep)

    class Duble:
        fila: list = []
        chamadas: list[dict] = []
        duracao = 5.0

    def _fake(request, timeout=None):
        Duble.chamadas.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data.decode("utf-8")),
                "headers": dict(request.header_items()),
                "timeout": timeout,
            }
        )
        relogio.agora += Duble.duracao
        item = Duble.fila.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _Resposta(item if isinstance(item, bytes) else json.dumps(item).encode())

    Duble.fila = []
    Duble.chamadas = []
    Duble.relogio = relogio
    monkeypatch.setattr(oc.urllib.request, "urlopen", _fake)
    return Duble


# ---------------------------------------------------------------------------
# Pedido
# ---------------------------------------------------------------------------


def test_corpo_enviado_com_os_defaults_e_sem_response_format(openai):
    openai.fila = [_ok()]
    oc.generate_image(PROMPT)
    (chamada,) = openai.chamadas
    assert chamada["url"] == "https://api.openai.com/v1/images/generations"
    assert chamada["body"] == {
        "model": "gpt-image-1.5",
        "prompt": PROMPT,
        "size": "1024x1536",
        "quality": "medium",
        "n": 1,
        "output_format": "png",
    }
    assert "response_format" not in chamada["body"]
    assert "moderation" not in chamada["body"]
    assert chamada["timeout"] == 150
    assert chamada["headers"]["Authorization"] == f"Bearer {KEY}"


def test_moderation_so_quando_informada(openai):
    openai.fila = [_ok()]
    oc.generate_image(PROMPT, moderation="low")
    assert openai.chamadas[0]["body"]["moderation"] == "low"


@pytest.mark.parametrize("fundo", ["transparent", "opaque", "auto"])
def test_background_so_quando_informado(openai, fundo):
    openai.fila = [_ok(), _ok()]
    oc.generate_image(PROMPT, background=fundo)
    assert openai.chamadas[0]["body"]["background"] == fundo
    assert openai.chamadas[0]["body"]["output_format"] == "png"  # transparente exige PNG/WebP
    oc.generate_image(PROMPT)
    assert "background" not in openai.chamadas[1]["body"]


def test_background_invalido_nem_chama(openai):
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT, background="branco")
    assert exc.value.code == oc.OPENAI_INVALID_REQUEST
    assert openai.chamadas == []
    assert oc.IMAGE_BACKGROUNDS == ("transparent", "opaque", "auto")


def test_configuracao_pelo_ambiente(openai, monkeypatch):
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-1-mini")
    monkeypatch.setenv("OPENAI_IMAGE_QUALITY", "low")
    monkeypatch.setenv("OPENAI_IMAGE_SIZE", "1024x1024")
    monkeypatch.setenv("OPENAI_IMAGE_TIMEOUT_SECONDS", "90")
    openai.fila = [_ok()]
    oc.generate_image(PROMPT)
    body = openai.chamadas[0]["body"]
    assert (body["model"], body["quality"], body["size"]) == ("gpt-image-1-mini", "low", "1024x1024")
    assert openai.chamadas[0]["timeout"] == 90


@pytest.mark.parametrize("valor", ["abc", "0", "-5", ""])
def test_timeout_invalido_no_ambiente_volta_ao_padrao(monkeypatch, valor):
    monkeypatch.setenv("OPENAI_IMAGE_TIMEOUT_SECONDS", valor)
    assert oc.image_settings().timeout_seconds == 150


def test_parametros_explicitos_vencem_o_ambiente(openai, monkeypatch):
    monkeypatch.setenv("OPENAI_IMAGE_QUALITY", "low")
    openai.fila = [_ok()]
    oc.generate_image(PROMPT, model="gpt-image-1.5", quality="high", size="1024x1536", timeout=60)
    assert openai.chamadas[0]["body"]["quality"] == "high"
    assert openai.chamadas[0]["timeout"] == 60


def test_defaults_sao_o_contrato_de_envs_do_pr1():
    assert (oc.DEFAULT_IMAGE_MODEL, oc.DEFAULT_IMAGE_QUALITY, oc.DEFAULT_IMAGE_SIZE) == (
        "gpt-image-1.5", "medium", "1024x1536")
    assert oc.DEFAULT_IMAGE_TIMEOUT_SECONDS == 150


# ---------------------------------------------------------------------------
# Resposta
# ---------------------------------------------------------------------------


def test_decodifica_b64_json_e_devolve_usage_e_custo(openai):
    openai.fila = [_ok(usage={"input_tokens": 1234, "output_tokens": 4160, "total_tokens": 5394},
                       quality="medium", size="1024x1536")]
    result = oc.generate_image(PROMPT)
    assert result.image == PNG
    assert (result.model, result.quality, result.size) == ("gpt-image-1.5", "medium", "1024x1536")
    assert (result.tokens_in, result.tokens_out) == (1234, 4160)
    assert result.cost_usd == Decimal("0.05")
    assert result.attempts == 1
    assert result.elapsed_seconds == 5.0


def test_sem_usage_tokens_ficam_none(openai):
    openai.fila = [_ok()]
    result = oc.generate_image(PROMPT)
    assert (result.tokens_in, result.tokens_out) == (None, None)


@pytest.mark.parametrize(
    "modelo, qualidade, tamanho, custo",
    [
        ("gpt-image-1.5", "low", "1024x1536", Decimal("0.013")),
        ("gpt-image-1.5", "medium", "1024x1536", Decimal("0.05")),
        ("gpt-image-1.5", "high", "1024x1536", Decimal("0.20")),
        ("gpt-image-1.5", "medium", "1024x1024", None),
        ("gpt-image-9", "medium", "1024x1536", None),
    ],
)
def test_tabela_de_custo(modelo, qualidade, tamanho, custo):
    assert oc.image_cost_usd(modelo, qualidade, tamanho) == custo


def test_custo_desconhecido_e_none_no_resultado(openai):
    openai.fila = [_ok(size="1024x1024")]
    assert oc.generate_image(PROMPT, size="1024x1024").cost_usd is None


@pytest.mark.parametrize(
    "resposta",
    [
        {"data": []},
        {"data": [{"url": "https://exemplo"}]},
        {"data": [{"b64_json": "%%%nao-e-base64%%%"}]},
        {"data": [{"b64_json": ""}]},
        b"<html>nao e json</html>",
        b"[1, 2]",
    ],
    ids=["sem_itens", "sem_b64", "b64_invalido", "b64_vazio", "nao_json", "json_lista"],
)
def test_resposta_fora_do_contrato_e_generation_failed(openai, resposta):
    openai.fila = [resposta]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT)
    assert exc.value.code == oc.OPENAI_GENERATION_FAILED
    assert len(openai.chamadas) == 1


# ---------------------------------------------------------------------------
# Erros e retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_4xx_e_invalid_request_sem_retry(openai, status):
    openai.fila = [_http_error(status, {"error": {"code": "moderation_blocked", "message": "recusado"}})]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT)
    assert exc.value.code == oc.OPENAI_INVALID_REQUEST
    assert exc.value.status == status
    # SEG-12: o motivo da OpenAI vai para o diagnostico (log), nunca para a mensagem.
    assert "moderation_blocked" in exc.value.diagnostic
    assert "moderation_blocked" not in str(exc.value)
    assert str(exc.value) == oc.USER_MESSAGE_UNAVAILABLE
    assert len(openai.chamadas) == 1
    assert openai.relogio.esperas == []


def test_429_tenta_de_novo_e_sucede(openai):
    openai.fila = [_http_error(429, {"error": {"message": "rate"}}), _ok()]
    result = oc.generate_image(PROMPT)
    assert result.attempts == 2
    assert len(openai.chamadas) == 2
    assert openai.relogio.esperas == [1.0]


def test_5xx_com_backoff_exponencial(openai):
    openai.fila = [_http_error(500), _http_error(502), _ok()]
    result = oc.generate_image(PROMPT)
    assert result.attempts == 3
    assert openai.relogio.esperas == [1.0, 2.0]


def test_5xx_persistente_desiste_no_teto_de_tentativas(openai):
    openai.fila = [_http_error(503)] * 5
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT)
    assert (exc.value.code, exc.value.status) == (oc.OPENAI_GENERATION_FAILED, 503)
    assert len(openai.chamadas) == oc.IMAGE_MAX_ATTEMPTS == 3


def test_429_esgotado_e_rate_limit(openai):
    openai.fila = [_http_error(429)] * 3
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT)
    assert exc.value.code == oc.OPENAI_RATE_LIMIT


def test_sem_retry_depois_de_20_segundos(openai):
    """Erro transitorio que chega depois de 20 s: a proxima tentativa poderia
    passar do orcamento da Lambda (e cada tentativa que gera e cobrada)."""
    openai.duracao = 21.0
    openai.fila = [_http_error(500), _ok()]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT)
    assert exc.value.status == 500
    assert len(openai.chamadas) == 1


def test_retry_para_quando_a_janela_fecha_no_meio(openai):
    openai.duracao = 12.0  # 1a falha aos 12 s (retry), 2a aos 12+1+12=25 s (para)
    openai.fila = [_http_error(500), _http_error(500), _ok()]
    with pytest.raises(oc.OpenAIGenerationError):
        oc.generate_image(PROMPT)
    assert len(openai.chamadas) == 2


@pytest.mark.parametrize(
    "erro",
    [TimeoutError("read timed out"), urllib.error.URLError(TimeoutError("connect timed out"))],
    ids=["leitura", "conexao"],
)
def test_timeout_nunca_tenta_de_novo(openai, erro):
    openai.fila = [erro, _ok()]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT)
    assert exc.value.code == oc.OPENAI_TIMEOUT
    assert exc.value.status is None
    assert len(openai.chamadas) == 1
    assert "150" in exc.value.diagnostic
    assert str(exc.value) == oc.USER_MESSAGE_TIMEOUT


def test_erro_de_rede_e_conexao_cortada_nao_tentam_de_novo(openai):
    openai.fila = [urllib.error.URLError("nome nao resolvido"), _ok()]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT)
    assert exc.value.code == oc.OPENAI_GENERATION_FAILED
    openai.fila = [ConnectionResetError("reset"), _ok()]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT)
    assert exc.value.code == oc.OPENAI_GENERATION_FAILED
    assert len(openai.chamadas) == 2


def test_prompt_vazio_nem_chama(openai):
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image("   ")
    assert exc.value.code == oc.OPENAI_INVALID_REQUEST
    assert openai.chamadas == []


def test_sem_chave_nem_chama(openai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT)
    assert "OPENAI_API_KEY nao configurada" in exc.value.diagnostic
    assert str(exc.value) == oc.USER_MESSAGE_UNAVAILABLE
    assert openai.chamadas == []


def test_mensagens_de_erro_nao_vazam_prompt_nem_chave(openai):
    openai.fila = [_http_error(400, {"error": {"code": "invalid_value", "message": "x" * 1000}})]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT)
    texto = str(exc.value)
    assert PROMPT not in texto and KEY not in texto
    assert len(texto) < 400


def test_orcamento_de_tempo_cabe_na_lambda_de_180s():
    pior_inicio = oc.IMAGE_RETRY_WINDOW_SECONDS + max(oc.IMAGE_RETRY_BACKOFF_SECONDS)
    assert pior_inicio + oc.DEFAULT_IMAGE_TIMEOUT_SECONDS < 180


# ---------------------------------------------------------------------------
# Compatibilidade de OpenAIGenerationError
# ---------------------------------------------------------------------------


def test_erro_antigo_continua_construido_so_com_mensagem():
    exc = oc.OpenAIGenerationError("falhou")
    assert str(exc) == "falhou"
    assert exc.status is None
    assert exc.code == oc.OPENAI_GENERATION_FAILED
    assert not exc.retryable
    sub = ExerciseDescriptionError("descricao invalida")
    assert isinstance(sub, oc.OpenAIGenerationError)
    assert str(sub) == "descricao invalida" and sub.code == oc.OPENAI_GENERATION_FAILED


@pytest.mark.parametrize(
    "status, code",
    [(None, "OPENAI_GENERATION_FAILED"), (400, "OPENAI_INVALID_REQUEST"), (403, "OPENAI_INVALID_REQUEST"),
     (408, "OPENAI_TIMEOUT"), (429, "OPENAI_RATE_LIMIT"), (500, "OPENAI_GENERATION_FAILED"),
     (503, "OPENAI_GENERATION_FAILED")],
)
def test_classificacao_por_status(status, code):
    assert oc.classify_http_status(status) == code
    assert oc.OpenAIGenerationError("x", status=status).code == code


def test_call_openai_status_no_diagnostico_e_mensagem_generica(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)

    def _falha(request, timeout=None):
        raise urllib.error.HTTPError(oc._RESPONSES_URL, 429, "rate", {}, io.BytesIO(b"limite"))

    monkeypatch.setattr(oc.urllib.request, "urlopen", _falha)
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.call_openai("p", "m")
    assert str(exc.value) == oc.USER_MESSAGE_RATE_LIMIT
    assert exc.value.diagnostic == "OpenAI respondeu 429: limite"
    assert (exc.value.status, exc.value.code) == (429, oc.OPENAI_RATE_LIMIT)


def test_call_openai_timeout_classificado(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)

    def _estoura(request, timeout=None):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(oc.urllib.request, "urlopen", _estoura)
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.call_openai("p", "m", timeout=90)
    assert str(exc.value) == oc.USER_MESSAGE_TIMEOUT
    assert exc.value.diagnostic == "Timeout de 90s ao chamar OpenAI."
    assert exc.value.code == oc.OPENAI_TIMEOUT
