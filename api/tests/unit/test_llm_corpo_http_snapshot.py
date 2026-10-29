"""Snapshot do corpo HTTP enviado a OpenAI pelas 3 features ao vivo (avaliacao
de serie, resumo de exercicio, resumo de treino).

Lote 1 (IA-04): as 3 copias de `call_openai` foram unificadas em
`openai_client.call_openai`. Nenhuma resposta ao usuario pode mudar, entao o
corpo (`model`, `input`) tem de ser byte a byte o mesmo de antes. A referencia
(`api/privado/tests/fixtures/llm_request_snapshots.json`, repositorio privado
desde o M103 P1) foi gerada ANTES da refatoracao.

Regenerar (so se o PROMPT mudar de proposito): `LLM_SNAPSHOT_WRITE=1 pytest ...`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app import exercise_set_evaluation_ai, exercise_summary_ai, workout_summary_ai

from tests.support import privado


def _fixture() -> Path:
    """Corpo com o `input` (texto do prompt): repositorio privado (M103 P1)."""
    return privado.fixture_privada("llm_request_snapshots.json")

SET_PAYLOAD = {
    "exercise_name": "Supino reto",
    "set_number": 2,
    "sets_count": 4,
    "reps_target": "8-10",
    "load_target": "60kg",
    "rpe_target": 8,
    "reps": 9,
    "weight_kg": 60,
    "rpe": 8.5,
    "previous_sets_this_session": [{"set_number": 1, "reps": 10, "weight_kg": 60, "rpe": 8}],
}

EXERCISE_PAYLOAD = {
    "exercise_name": "Agachamento livre",
    "reps_target": "6-8",
    "load_target": "100kg",
    "rpe_target": 8,
    "sets": [
        {"set_number": 1, "reps": 8, "weight_kg": 100, "rpe": 8},
        {"set_number": 2, "reps": 7, "weight_kg": 100, "rpe": 9, "muscle_pain": 3},
    ],
    "muscle_pain": 3,
    "joint_pain": False,
    "notes": "fadiga no fim",
}

ACTIVITY = {
    "id": 10,
    "name": "Treino A",
    "local_date": "2026-10-01",
    "gym_name": "Academia X",
    "duration_seconds": 3600,
    "total_volume_kg": 5000,
    "avg_rpe": 8,
    "notes": "ok",
    "exercises": [
        {
            "id": 1,
            "original_name": "Supino reto",
            "muscle_primary": "Peitoral",
            "avg_rpe": 8,
            "sets": [{"reps": 10, "weight_kg": 60, "rpe": 8}, {"reps": 9, "weight_kg": 60, "rpe": 9}],
        }
    ],
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


def _capturar(monkeypatch, chamar):
    """Roda `chamar()` com a rede mockada e devolve o que foi enviado."""
    from app import openai_client

    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    # Codigo antigo lia a chave no import; o novo, do ambiente. Cobre os dois.
    for mod in (exercise_set_evaluation_ai, exercise_summary_ai, workout_summary_ai):
        monkeypatch.setattr(mod, "OPENAI_API_KEY", "chave-de-teste", raising=False)
    monkeypatch.setattr(exercise_set_evaluation_ai, "load_progressao_rag_text", lambda: "REGRA RAG FIXA")
    enviados = []

    def _fake(request, timeout=None):
        enviados.append(request)
        # set-eval-v2 (IA-09): saida estruturada; os resumos seguem em texto.
        if "text" in json.loads(request.data.decode("utf-8")):
            return _Resposta(
                {"output_text": '{"category": "HOLD", "note": "Ok.", "next": {"reps": 9, "load_kg": 60, "rpe": 8}}'}
            )
        return _Resposta({"output_text": "Resumo de teste."})

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    chamar()
    assert len(enviados) == 1
    request = enviados[0]
    return {
        "url": request.full_url,
        "method": request.get_method(),
        "body_sha256": hashlib.sha256(request.data).hexdigest(),
        "body": json.loads(request.data.decode("utf-8")),
    }


CASOS = {
    "set_evaluation": lambda: exercise_set_evaluation_ai.evaluate_set(SET_PAYLOAD),
    "exercise_summary": lambda: exercise_summary_ai.summarize_exercise(EXERCISE_PAYLOAD),
    "workout_summary": lambda: workout_summary_ai.generate_workout_summary(ACTIVITY),
}


@pytest.mark.parametrize("nome", sorted(CASOS))
def test_corpo_http_identico_ao_de_antes_da_refatoracao(monkeypatch, nome):
    capturado = _capturar(monkeypatch, CASOS[nome])
    fixture = _fixture()
    if os.environ.get("LLM_SNAPSHOT_WRITE"):
        atual = json.loads(fixture.read_text(encoding="utf-8")) if fixture.exists() else {}
        atual[nome] = capturado
        fixture.parent.mkdir(exist_ok=True)
        fixture.write_text(json.dumps(atual, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    esperado = json.loads(fixture.read_text(encoding="utf-8"))[nome]
    assert capturado["url"] == esperado["url"] == "https://api.openai.com/v1/responses"
    assert capturado["method"] == "POST"
    # Sem `assert body == body`: a falha imprimiria o prompt no log publico do CI.
    privado.comparar_sem_vazar(
        json.dumps(capturado["body"], ensure_ascii=False, indent=2, sort_keys=True),
        json.dumps(esperado["body"], ensure_ascii=False, indent=2, sort_keys=True),
        f"corpo HTTP de {nome}",
    )
    assert capturado["body_sha256"] == esperado["body_sha256"]
