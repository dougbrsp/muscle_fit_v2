"""Snapshot do PEDIDO da sugestão de metas (nutrição N6), no padrão da foto do prato.

Roda `nutrition_plan_suggestion_ai.call_model` com a rede mockada e compara com a referência commitada:
- `plan_suggestion.prompt.txt`: o prompt renderizado, no repositório PRIVADO
  (`api/privado/tests/fixtures/nutrition_prompts/`);
- `plan_suggestion.body.json`: o corpo HTTP sem o texto do prompt, mais `prompt_version` e o sha256 do prompt.

Mudou de propósito? Suba o `PROMPT_VERSION` de `nutrition_plan_suggestion_ai` e regenere:

    cd api && NUTRITION_PROMPT_SNAPSHOT_WRITE=1 python -m pytest tests/unit/test_nutrition_plan_suggestion_prompt_snapshot.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app import nutrition_plan_suggestion_ai as ai, openai_client

from tests.support import privado

FIXTURES = Path(__file__).parent / "fixtures" / "nutrition_prompts"
CORPO = FIXTURES / "plan_suggestion.body.json"
# Um contexto como o do motor (`plan_suggestion.build_context`), fixo: o snapshot não depende do banco.
CONTEXTO = {
    "profile": {"sex": "male", "age_years": 30, "height_cm": 178.0, "weight_kg": 80.0, "body_fat_percent": 20.0},
    "energy": {"bmr_kcal": 1800.0, "tdee_kcal": 2500.0},
    "history": {"weight_readings": 4, "weekly_weight_change_share": -0.002},
    "current_form": {"strategy": None},
}


class _Resposta:
    def read(self):
        return json.dumps({"output_text": "{}", "model": "modelo-falso"}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capturar(monkeypatch) -> dict:
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.delenv(ai.nutrition_meal_parse_ai.MODEL_ENV_VAR, raising=False)
    monkeypatch.delenv("LLM_PROVIDER_MEAL_PARSE", raising=False)
    enviados = []

    def _fake(request, timeout=None):
        enviados.append(json.loads(request.data.decode("utf-8")))
        return _Resposta()

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    ai.call_model(CONTEXTO)
    [corpo] = enviados
    return corpo


def _resumo(corpo: dict) -> tuple[str, dict]:
    prompt = corpo["input"]
    resumo = json.loads(json.dumps(corpo))
    resumo["input"] = f"<<PROMPT {len(prompt)} chars, sha256 {hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16]}>>"
    resumo["_prompt_version"] = ai.PROMPT_VERSION
    return prompt, resumo


def test_pedido_da_sugestao_de_metas_igual_ao_snapshot(monkeypatch):
    prompt, resumo = _resumo(_capturar(monkeypatch))
    texto_resumo = json.dumps(resumo, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    referencia = privado.fixture_privada("nutrition_prompts", "plan_suggestion.prompt.txt")
    if os.environ.get("NUTRITION_PROMPT_SNAPSHOT_WRITE"):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        referencia.parent.mkdir(parents=True, exist_ok=True)
        referencia.write_text(prompt + "\n", encoding="utf-8", newline="\n")
        CORPO.write_text(texto_resumo, encoding="utf-8", newline="\n")
    assert CORPO.exists(), "sem snapshot: gere com NUTRITION_PROMPT_SNAPSHOT_WRITE=1"
    privado.comparar_arquivo_sem_vazar(
        prompt + "\n", referencia, "prompt da sugestão de metas", regerar="NUTRITION_PROMPT_SNAPSHOT_WRITE"
    )
    assert CORPO.read_text(encoding="utf-8") == texto_resumo, "corpo da sugestão de metas mudou"
