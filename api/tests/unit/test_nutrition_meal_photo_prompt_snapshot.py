"""Snapshot do PEDIDO da foto do prato (nutrição L10), no padrão do rótulo
(`test_nutrition_label_prompt_snapshot.py`).

Roda `nutrition_meal_photo_ai.read_meal_photo` com a rede mockada e compara com a
referência commitada em `fixtures/nutrition_prompts/`:
- `meal_photo.prompt.txt`: o prompt renderizado (texto inteiro), no repositório PRIVADO
  (`api/privado/tests/fixtures/nutrition_prompts/`);
- `meal_photo.body.json`: o corpo HTTP sem o texto do prompt e com a imagem trocada por
  um marcador, mais `prompt_version` e o sha256 do prompt.

Mudou de propósito? Suba o `PROMPT_VERSION` de `nutrition_meal_photo_ai` e regenere:

    cd api && NUTRITION_PROMPT_SNAPSHOT_WRITE=1 python -m pytest tests/unit/test_nutrition_meal_photo_prompt_snapshot.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app import nutrition_meal_photo_ai as ai, openai_client

from tests.support import privado

FIXTURES = Path(__file__).parent / "fixtures" / "nutrition_prompts"
CORPO = FIXTURES / "meal_photo.body.json"
IMAGEM = b"\xff\xd8\xff\xe0snapshot"


class _Resposta:
    def read(self):
        saida = {"plate_status": "no_food", "items": []}
        return json.dumps({"output_text": json.dumps(saida), "model": "modelo-falso"}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capturar(monkeypatch) -> dict:
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.delenv(ai.nutrition_label_ai.MODEL_ENV_VAR, raising=False)
    monkeypatch.delenv("LLM_PROVIDER_MEAL_PHOTO", raising=False)
    enviados = []

    def _fake(request, timeout=None):
        enviados.append(json.loads(request.data.decode("utf-8")))
        return _Resposta()

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    ai.read_meal_photo(IMAGEM)
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


def test_pedido_da_foto_do_prato_igual_ao_snapshot(monkeypatch):
    prompt, resumo = _resumo(_capturar(monkeypatch))
    texto_resumo = json.dumps(resumo, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    referencia = privado.fixture_privada("nutrition_prompts", "meal_photo.prompt.txt")
    if os.environ.get("NUTRITION_PROMPT_SNAPSHOT_WRITE"):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        referencia.parent.mkdir(parents=True, exist_ok=True)
        referencia.write_text(prompt + "\n", encoding="utf-8", newline="\n")
        CORPO.write_text(texto_resumo, encoding="utf-8", newline="\n")
    assert CORPO.exists(), "sem snapshot: gere com NUTRITION_PROMPT_SNAPSHOT_WRITE=1"
    privado.comparar_arquivo_sem_vazar(
        prompt + "\n", referencia, "prompt da foto do prato", regerar="NUTRITION_PROMPT_SNAPSHOT_WRITE"
    )
    assert CORPO.read_text(encoding="utf-8") == texto_resumo, "corpo da foto do prato mudou"
