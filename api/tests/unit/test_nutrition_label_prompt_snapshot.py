"""Snapshot do PEDIDO da leitura da foto do rótulo (nutrição L6; plano §9 e §14).

Roda `nutrition_label_ai.read_label` com a rede mockada e compara com a
referência commitada em `fixtures/nutrition_prompts/`:
- `label_photo.prompt.txt`: o prompt renderizado (texto inteiro), no
  repositório PRIVADO (`api/privado/tests/fixtures/nutrition_prompts/`, M103 P1);
- `label_photo.body.json`: o corpo HTTP sem o texto do prompt e com a imagem
  trocada por um marcador (modelo, `detail`, schema strict, teto de saída,
  `store`) + `prompt_version` e o sha256 do prompt.

Qualquer mudança de prompt, schema, modelo ou parâmetro quebra este teste.
Mudou de propósito? Suba o `PROMPT_VERSION` de `nutrition_label_ai`, regenere e
rode o eval pago (`tests/eval/test_nutrition_label_eval.py`):

    cd api && NUTRITION_PROMPT_SNAPSHOT_WRITE=1 python -m pytest tests/unit/test_nutrition_label_prompt_snapshot.py

Arquivo próprio (e não o snapshot do IA-11): a entrada tem imagem, que o
esqueleto de `test_llm_prompt_renderizado_snapshot.py` não representa.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app import nutrition_label_ai as ai, openai_client

from tests.support import privado

# Corpo (sem o texto do prompt): público. Prompt renderizado: repositório
# privado (M103 P1), comparado sem imprimir o texto (log do CI é público).
FIXTURES = Path(__file__).parent / "fixtures" / "nutrition_prompts"
CORPO = FIXTURES / "label_photo.body.json"
IMAGEM = b"\xff\xd8\xff\xe0snapshot"


class _Resposta:
    def read(self):
        return json.dumps({"output_text": json.dumps({"legibility": "ok"}), "model": "modelo-falso"}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capturar(monkeypatch) -> dict:
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.delenv(ai.MODEL_ENV_VAR, raising=False)
    enviados = []

    def _fake(request, timeout=None):
        enviados.append(json.loads(request.data.decode("utf-8")))
        return _Resposta()

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    ai.read_label(IMAGEM)
    [corpo] = enviados
    return corpo


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _resumo(corpo: dict) -> tuple[str, dict]:
    [mensagem] = corpo["input"]
    texto, imagem = mensagem["content"]
    prompt = texto["text"]
    resumo = json.loads(json.dumps(corpo))
    resumo["input"][0]["content"][0]["text"] = f"<<PROMPT {len(prompt)} chars, sha256 {_sha(prompt)[:16]}>>"
    resumo["input"][0]["content"][1]["image_url"] = f"<<IMAGEM data URL, sha256 {_sha(imagem['image_url'])[:16]}>>"
    resumo["_prompt_version"] = ai.PROMPT_VERSION
    return prompt, resumo


def test_pedido_da_leitura_do_rotulo_igual_ao_snapshot(monkeypatch):
    prompt, resumo = _resumo(_capturar(monkeypatch))
    texto_resumo = json.dumps(resumo, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    referencia = privado.fixture_privada("nutrition_prompts", "label_photo.prompt.txt")
    if os.environ.get("NUTRITION_PROMPT_SNAPSHOT_WRITE"):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        referencia.parent.mkdir(parents=True, exist_ok=True)
        referencia.write_text(prompt + "\n", encoding="utf-8", newline="\n")
        CORPO.write_text(texto_resumo, encoding="utf-8", newline="\n")
    assert CORPO.exists(), "sem snapshot: gere com NUTRITION_PROMPT_SNAPSHOT_WRITE=1"
    privado.comparar_arquivo_sem_vazar(
        prompt + "\n", referencia, "prompt da leitura do rótulo", regerar="NUTRITION_PROMPT_SNAPSHOT_WRITE"
    )
    assert CORPO.read_text(encoding="utf-8") == texto_resumo, "corpo da leitura do rótulo mudou"
