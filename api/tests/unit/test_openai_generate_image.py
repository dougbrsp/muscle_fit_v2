"""`openai_client.generate_image` (POST /v1/images/generations sem
referencias; POST /v1/images/edits multipart com referencias) e a
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


def _parse_multipart(raw: bytes, content_type: str) -> tuple[dict, list[tuple[str, str, bytes]]]:
    """Parser independente do corpo multipart: split pelo boundary do header,
    cada parte conferida (CRLF, cabecalhos, terminador final)."""
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=", 1)[1].encode()
    dash = b"--" + boundary
    fim = dash + b"--\r\n"
    assert raw.endswith(fim)
    pedacos = raw[: -len(fim)].split(dash + b"\r\n")
    assert pedacos[0] == b""
    campos: dict = {}
    imagens: list[tuple[str, str, bytes]] = []
    for parte in pedacos[1:]:
        cabecalho, corpo = parte.split(b"\r\n\r\n", 1)
        assert corpo.endswith(b"\r\n")
        corpo = corpo[:-2]
        linhas = cabecalho.decode().split("\r\n")
        disposicao = linhas[0]
        assert disposicao.startswith("Content-Disposition: form-data; name=")
        nome = disposicao.split('name="', 1)[1].split('"', 1)[0]
        if nome == "image[]":
            arquivo = disposicao.split('filename="', 1)[1].split('"', 1)[0]
            extensao = arquivo.rsplit(".", 1)[1]
            tipo = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[extensao]
            assert linhas[1:] == [f"Content-Type: {tipo}"]
            imagens.append((nome, arquivo, corpo))
        else:
            assert len(linhas) == 1 and nome not in campos
            campos[nome] = corpo.decode("utf-8")
    return campos, imagens


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
        headers = dict(request.header_items())
        content_type = headers.get("Content-type", "")
        chamada = {"url": request.full_url, "headers": headers, "timeout": timeout, "raw": request.data}
        if content_type == "application/json":
            chamada["body"] = json.loads(request.data.decode("utf-8"))
        else:
            chamada["body"], chamada["images"] = _parse_multipart(request.data, content_type)
        Duble.chamadas.append(chamada)
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
        "model": "gpt-image-2.5-sunburst",
        "prompt": PROMPT,
        "size": "1024x1536",
        "quality": "high",
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


def test_defaults_sao_o_contrato_de_envs_v3():
    assert (oc.DEFAULT_IMAGE_MODEL, oc.DEFAULT_IMAGE_QUALITY, oc.DEFAULT_IMAGE_SIZE) == (
        "gpt-image-2.5-sunburst", "high", "1024x1536")
    assert oc.DEFAULT_IMAGE_TIMEOUT_SECONDS == 150
    assert oc.IMAGE_MAX_REFERENCE_IMAGES == 16


# ---------------------------------------------------------------------------
# Resposta
# ---------------------------------------------------------------------------


def test_decodifica_b64_json_e_devolve_usage_e_custo(openai):
    openai.fila = [_ok(usage={"input_tokens": 1234, "output_tokens": 4160, "total_tokens": 5394},
                       quality="medium", size="1024x1536")]
    result = oc.generate_image(PROMPT, model="gpt-image-1.5", quality="medium")
    assert result.image == PNG
    assert (result.model, result.quality, result.size) == ("gpt-image-1.5", "medium", "1024x1536")
    assert (result.tokens_in, result.tokens_out) == (1234, 4160)
    assert result.cost_usd == Decimal("0.05")
    assert result.attempts == 1
    assert result.elapsed_seconds == 5.0
    assert result.endpoint == oc.IMAGE_ENDPOINT_GENERATIONS == "generations"


USAGE_SUNBURST = {
    "input_tokens": 1500,
    "input_tokens_details": {"text_tokens": 500, "image_tokens": 1000},
    "output_tokens": 6000,
    "output_tokens_details": {"image_tokens": 6000, "text_tokens": 0},
    "total_tokens": 7500,
}


def test_custo_do_sunburst_sai_do_usage_pelas_taxas_por_token(openai):
    openai.fila = [_ok(usage=USAGE_SUNBURST)]
    result = oc.generate_image(PROMPT)
    precos = oc.IMAGE_TOKEN_PRICES_USD_PER_MILLION["gpt-image-2.5-sunburst"]
    assert (precos.text_input, precos.image_input, precos.image_output) == (
        Decimal("5.00"), Decimal("8.00"), Decimal("30.00"))
    # 500 x 5 + 1000 x 8 + 6000 x 30 = 190500 por 1M
    assert result.cost_usd == Decimal("0.1905")
    assert (result.tokens_in, result.tokens_out) == (1500, 6000)


def test_custo_do_sunburst_sem_usage_e_none(openai):
    openai.fila = [_ok()]
    assert oc.generate_image(PROMPT).cost_usd is None


def test_custo_do_sunburst_sem_detalhe_de_entrada(openai):
    """Sem `input_tokens_details`: no generations toda a entrada e texto; no
    edits nao da para separar texto de imagem -> None (sem chute)."""
    usage = {"input_tokens": 400, "output_tokens": 1000}
    openai.fila = [_ok(usage=usage), _ok(usage=usage)]
    assert oc.generate_image(PROMPT).cost_usd == Decimal("0.032")  # 400 x 5 + 1000 x 30
    assert oc.generate_image(PROMPT, reference_images=[PNG]).cost_usd is None


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
        ("gpt-image-1-mini", "high", "1024x1536", Decimal("0.052")),
        ("gpt-image-1.5", "medium", "1024x1024", None),
        ("gpt-image-9", "medium", "1024x1536", None),
        ("gpt-image-2.5-sunburst", "high", "1024x1536", None),  # por token: sem usage, sem custo
    ],
)
def test_tabela_de_custo(modelo, qualidade, tamanho, custo):
    assert oc.image_cost_usd(modelo, qualidade, tamanho) == custo


def test_sunburst_nao_tem_preco_fixo_por_imagem():
    assert not any(modelo.startswith("gpt-image-2.5") for modelo, _, _ in oc.IMAGE_PRICES_USD)


def test_custo_desconhecido_e_none_no_resultado(openai):
    openai.fila = [_ok(size="1024x1024")]
    assert oc.generate_image(PROMPT, model="gpt-image-1.5", size="1024x1024").cost_usd is None


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


def test_pior_caso_de_uma_chamada():
    assert oc.image_call_worst_case_seconds(150) == 20 + 1 + 2 + 150
    assert oc.image_call_worst_case_seconds(60.0) == 83.0


def test_duas_chamadas_no_pior_caso_cabem_na_lambda_de_420s():
    # V3: 2 chamadas sequenciais por card (cena 1 e cena 2) no worker.
    assert 2 * oc.image_call_worst_case_seconds(oc.DEFAULT_IMAGE_TIMEOUT_SECONDS) < 420


# ---------------------------------------------------------------------------
# Edits (multipart) com imagens de referencia
# ---------------------------------------------------------------------------

# REF_A contem CRLF e algo parecido com boundary: o parser so separa pelo boundary real.
REF_A = b"\x89PNG\r\n\x1a\n" + b"referencia-a\r\n--nao-e-boundary" * 3
REF_B = b"\x89PNG\r\n\x1a\n" + b"referencia-b" * 5


def test_uma_referencia_vai_para_o_edits_em_multipart(openai):
    openai.fila = [_ok(usage=USAGE_SUNBURST)]
    result = oc.generate_image(PROMPT, reference_images=[REF_A])
    (chamada,) = openai.chamadas
    assert chamada["url"] == "https://api.openai.com/v1/images/edits" == oc._IMAGE_EDITS_URL
    assert chamada["headers"]["Authorization"] == f"Bearer {KEY}"
    assert chamada["body"] == {
        "model": "gpt-image-2.5-sunburst",
        "prompt": PROMPT,
        "size": "1024x1536",
        "quality": "high",
        "n": "1",
        "output_format": "png",
    }
    assert chamada["images"] == [("image[]", "ref0.png", REF_A)]
    assert result.endpoint == oc.IMAGE_ENDPOINT_EDITS == "edits"
    assert result.cost_usd == Decimal("0.1905")


def test_n_referencias_na_ordem_e_campos_opcionais(openai):
    openai.fila = [_ok()]
    oc.generate_image(PROMPT, reference_images=[REF_A, REF_B, REF_A], moderation="low", background="opaque")
    (chamada,) = openai.chamadas
    assert chamada["images"] == [
        ("image[]", "ref0.png", REF_A), ("image[]", "ref1.png", REF_B), ("image[]", "ref2.png", REF_A)]
    assert chamada["body"]["moderation"] == "low"
    assert chamada["body"]["background"] == "opaque"
    for proibido in ("input_fidelity", "response_format", "image", "images", "mask"):
        assert proibido not in chamada["body"]
    assert b"input_fidelity" not in chamada["raw"] and b"response_format" not in chamada["raw"]


def test_boundary_e_unico_por_pedido(openai):
    openai.fila = [_ok(), _ok()]
    oc.generate_image(PROMPT, reference_images=[REF_B])
    oc.generate_image(PROMPT, reference_images=[REF_B])
    tipos = [c["headers"]["Content-type"] for c in openai.chamadas]
    assert tipos[0] != tipos[1]


def test_limite_de_referencias_recusado_sem_rede(openai):
    openai.fila = [_ok()]
    oc.generate_image(PROMPT, reference_images=[REF_B] * oc.IMAGE_MAX_REFERENCE_IMAGES)
    assert len(openai.chamadas[0]["images"]) == 16
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT, reference_images=[REF_B] * (oc.IMAGE_MAX_REFERENCE_IMAGES + 1))
    assert exc.value.code == oc.OPENAI_INVALID_REQUEST
    assert len(openai.chamadas) == 1


REF_JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-de-teste" * 4
REF_WEBP = b"RIFF\x10\x00\x00\x00WEBP" + b"webp-de-teste" * 2


def test_cada_imagem_declara_o_proprio_tipo(openai):
    """A cena 1 vai em JPEG e as fotos do admin tambem: o content-type e o
    filename de cada parte vem da assinatura dos bytes."""
    openai.fila = [_ok()]
    oc.generate_image(PROMPT, reference_images=[REF_JPEG, REF_A, REF_WEBP])
    assert openai.chamadas[0]["images"] == [
        ("image[]", "ref0.jpg", REF_JPEG), ("image[]", "ref1.png", REF_A), ("image[]", "ref2.webp", REF_WEBP)]


@pytest.mark.parametrize(
    "referencias",
    [[b""], [REF_A, b""], ["texto"], [None], [b"GIF89a-nao-aceito"], [REF_A, b"nao e imagem"]],
    ids=["vazia", "uma_vazia", "str", "none", "gif", "texto_cru"],
)
def test_referencia_vazia_ou_invalida_nem_chama(openai, referencias):
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT, reference_images=referencias)
    assert exc.value.code == oc.OPENAI_INVALID_REQUEST
    assert openai.chamadas == []


def test_edits_retry_429_5xx_igual_ao_generations(openai):
    openai.fila = [_http_error(429), _http_error(502), _ok()]
    result = oc.generate_image(PROMPT, reference_images=[REF_A])
    assert result.attempts == 3 and result.endpoint == "edits"
    assert openai.relogio.esperas == [1.0, 2.0]
    assert all(c["url"] == oc._IMAGE_EDITS_URL for c in openai.chamadas)
    assert openai.chamadas[0]["raw"] == openai.chamadas[2]["raw"]  # mesmo corpo reenviado


def test_edits_sem_retry_depois_da_janela(openai):
    openai.duracao = 21.0
    openai.fila = [_http_error(500), _ok()]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT, reference_images=[REF_A])
    assert exc.value.status == 500
    assert len(openai.chamadas) == 1


def test_edits_timeout_nunca_tenta_de_novo(openai):
    openai.fila = [TimeoutError("read timed out"), _ok()]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT, reference_images=[REF_A], timeout=150)
    assert exc.value.code == oc.OPENAI_TIMEOUT
    assert len(openai.chamadas) == 1
    assert openai.chamadas[0]["timeout"] == 150


@pytest.mark.parametrize("status", [400, 413, 422])
def test_edits_4xx_e_invalid_request_sem_retry(openai, status):
    openai.fila = [_http_error(status, {"error": {"code": "invalid_image", "message": "recusado"}})]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT, reference_images=[REF_A])
    assert (exc.value.code, exc.value.status) == (oc.OPENAI_INVALID_REQUEST, status)
    assert str(exc.value) == oc.USER_MESSAGE_UNAVAILABLE
    assert len(openai.chamadas) == 1


def test_edits_resposta_sem_imagem_e_generation_failed(openai):
    openai.fila = [{"data": []}]
    with pytest.raises(oc.OpenAIGenerationError) as exc:
        oc.generate_image(PROMPT, reference_images=[REF_A])
    assert exc.value.code == oc.OPENAI_GENERATION_FAILED


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
