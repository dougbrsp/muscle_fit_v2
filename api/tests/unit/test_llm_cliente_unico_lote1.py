"""Lote 1 (IA-01/04/07/12/06a): cliente OpenAI unico, `llm.usage`, timeouts,
env do cardio e platô interpolado no prompt da Analise. Rede sempre mockada."""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

from app import (
    cardio_agent_ai,
    exercise_set_evaluation_ai,
    exercise_summary_ai,
    insights_ai,
    openai_client,
    report_ai,
    workout_summary_ai,
)
from app.intelligence.dashboard.metrics_registry import THRESHOLDS
from app.openai_client import USER_MESSAGE_UNAVAILABLE, OpenAIGenerationError


class _Resposta:
    def __init__(self, data):
        self._data = data

    def read(self):
        return json.dumps(self._data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _eventos(caplog):
    saida = []
    for rec in caplog.records:
        if rec.name == "musclefit.events":
            payload = json.loads(rec.getMessage())
            if payload.get("event") == "llm.usage":
                saida.append(payload)
    return saida


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(openai_client, "_sleep", lambda s: None)


# --- IA-01: llm.usage -------------------------------------------------------


def test_usage_emitido_com_todos_os_campos(monkeypatch, caplog):
    resposta = {
        "output_text": "ola",
        "model": "gpt-x-2026",
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens": 30,
            "output_tokens_details": {"reasoning_tokens": 12},
        },
    }
    monkeypatch.setattr(openai_client.urllib.request, "urlopen", lambda r, timeout=None: _Resposta(resposta))
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        texto = openai_client.call_openai("SEGREDO-DO-PROMPT", "gpt-x", feature="insights", user_id=7)
    assert texto == "ola"
    [ev] = _eventos(caplog)
    assert ev["feature"] == "insights" and ev["user_id"] == 7 and ev["status"] == "ok"
    assert ev["model"] == "gpt-x-2026"
    assert (ev["input_tokens"], ev["cached_tokens"]) == (100, 40)
    assert (ev["output_tokens"], ev["reasoning_tokens"]) == (30, 12)
    assert isinstance(ev["latency_ms"], int)
    assert "SEGREDO-DO-PROMPT" not in json.dumps(ev) and "ola" not in json.dumps(ev)


def test_usage_ausente_nao_quebra_e_texto_e_o_mesmo(monkeypatch, caplog):
    monkeypatch.setattr(
        openai_client.urllib.request, "urlopen", lambda r, timeout=None: _Resposta({"output_text": "so texto"})
    )
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        texto = openai_client.call_openai("p", "gpt-x", feature="cardio_agent")
    assert texto == "so texto"
    [ev] = _eventos(caplog)
    # log_event omite campos None: ausentes == None.
    assert ev.get("input_tokens") is None and ev.get("cached_tokens") is None
    assert ev.get("reasoning_tokens") is None and ev.get("user_id") is None
    assert ev["status"] == "ok" and ev["model"] == "gpt-x"


def test_usage_na_saida_estruturada(monkeypatch, caplog):
    resposta = {"output_text": "{}", "usage": {"input_tokens": 5, "output_tokens": 6}}
    monkeypatch.setattr(openai_client.urllib.request, "urlopen", lambda r, timeout=None: _Resposta(resposta))
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        result = openai_client.call_openai_structured("p", "m", {}, schema_name="s", feature="periodization", user_id=3)
    assert (result.tokens_in, result.tokens_out) == (5, 6)
    [ev] = _eventos(caplog)
    assert ev["feature"] == "periodization" and ev["input_tokens"] == 5 and ev["user_id"] == 3


def test_usage_de_erro_e_de_timeout(monkeypatch, caplog):
    def _estoura(r, timeout=None):
        raise TimeoutError("lento")

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _estoura)
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        with pytest.raises(OpenAIGenerationError):
            openai_client.call_openai("p", "m", feature="insights")
    assert [e["status"] for e in _eventos(caplog)] == ["timeout"]

    caplog.clear()

    def _400(r, timeout=None):
        raise urllib.error.HTTPError("u", 400, "x", None, io.BytesIO(b"ruim"))

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _400)
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        with pytest.raises(OpenAIGenerationError):
            openai_client.call_openai("p", "m", feature="insights")
    assert [e["status"] for e in _eventos(caplog)] == ["error"]


# --- IA-04: retry -----------------------------------------------------------


def _http(status):
    return urllib.error.HTTPError("u", status, "x", None, io.BytesIO(b"{}"))


def test_retry_so_em_429_e_5xx_e_um_evento_por_chamada(monkeypatch, caplog):
    chamadas = []

    def _fake(r, timeout=None):
        chamadas.append(timeout)
        if len(chamadas) < 3:
            raise _http(503)
        return _Resposta({"output_text": "ok"})

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        assert openai_client.call_openai("p", "m", timeout=50) == "ok"
    assert len(chamadas) == 3
    assert chamadas[0] == 50 and all(t <= 50 for t in chamadas)
    [ev] = _eventos(caplog)
    assert ev["attempts"] == 3 and ev["status"] == "ok"


def test_sem_retry_em_4xx(monkeypatch):
    chamadas = []

    def _fake(r, timeout=None):
        chamadas.append(1)
        raise _http(400)

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    with pytest.raises(OpenAIGenerationError):
        openai_client.call_openai("p", "m")
    assert len(chamadas) == 1


def test_retry_respeita_orcamento_total(monkeypatch):
    relogio = {"t": 0.0}
    monkeypatch.setattr(openai_client, "_monotonic", lambda: relogio["t"])
    monkeypatch.setattr(openai_client, "_sleep", lambda s: relogio.__setitem__("t", relogio["t"] + s))
    chamadas = []

    def _fake(r, timeout=None):
        chamadas.append(timeout)
        relogio["t"] += 5
        raise _http(429)

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    with pytest.raises(OpenAIGenerationError):
        openai_client.call_openai("p", "m", timeout=50)
    assert 1 < len(chamadas) <= openai_client.TEXT_MAX_ATTEMPTS
    # tempo gasto + o que a ultima tentativa pode usar nunca passa de 50 s
    assert relogio["t"] - 5 + chamadas[-1] <= 50
    # janela estourada: nao repete
    relogio["t"] = 0.0
    chamadas.clear()

    def _lento(r, timeout=None):
        chamadas.append(timeout)
        relogio["t"] += 30
        raise _http(503)

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _lento)
    with pytest.raises(OpenAIGenerationError):
        openai_client.call_openai("p", "m", timeout=50)
    assert len(chamadas) == 1


# --- IA-04 / IA-07: chamadores ----------------------------------------------


def test_constantes_de_timeout_menores_que_a_lambda_de_60s():
    assert openai_client.LLM_TIMEOUT_LIVE_SECONDS < 60
    assert openai_client.LLM_TIMEOUT_LONG_SECONDS < 60


def test_chamadores_usam_as_constantes_e_as_features(monkeypatch):
    vistos = []

    def _fake(r, timeout=None):
        vistos.append(timeout)
        # set-eval-v2 (IA-09): a avaliacao de serie pede saida estruturada.
        return _Resposta({"output_text": '{"category": "MANTER", "note": "ok", "next": {"reps": null, "load_kg": null, "rpe": null}}'})

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    monkeypatch.setattr(exercise_set_evaluation_ai, "load_progressao_rag_text", lambda: "")
    exercise_set_evaluation_ai.call_openai("p")
    exercise_summary_ai.call_openai("p")
    workout_summary_ai.call_openai("p")
    report_ai._call_openai("p")
    monkeypatch.setattr(insights_ai, "build_insight_prompt", lambda *a, **k: "p")
    insights_ai.generate_insight("q", {}, "")
    assert vistos == [openai_client.LLM_TIMEOUT_LIVE_SECONDS] * 3 + [openai_client.LLM_TIMEOUT_LONG_SECONDS] * 2


@pytest.mark.parametrize(
    "funcao, erro",
    [
        (exercise_set_evaluation_ai.call_openai, exercise_set_evaluation_ai.SetEvaluationError),
        (exercise_summary_ai.call_openai, exercise_summary_ai.ExerciseSummaryError),
        (workout_summary_ai.call_openai, workout_summary_ai.WorkoutSummaryError),
    ],
)
def test_erros_proprios_herdam_do_cliente_e_chave_e_lida_na_chamada(monkeypatch, funcao, erro):
    assert issubclass(erro, OpenAIGenerationError)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(erro) as exc:
        funcao("p")
    # SEG-12: o usuario ve a mensagem generica; o motivo fica no diagnostico
    # da excecao original (que vai para o log em `llm.error`).
    assert str(exc.value) == USER_MESSAGE_UNAVAILABLE
    assert "OPENAI_API_KEY nao configurada" in exc.value.__cause__.diagnostic


# --- IA-12: modelo do cardio -------------------------------------------------


def test_modelo_do_cardio_ordem_de_resolucao(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL_CARDIO", raising=False)
    monkeypatch.delenv("OPENAI_CARDIO_AGENT_MODEL", raising=False)
    assert cardio_agent_ai._resolve_model() == "gpt-5.4-mini"
    monkeypatch.setenv("OPENAI_CARDIO_AGENT_MODEL", "antigo")
    assert cardio_agent_ai._resolve_model() == "antigo"
    monkeypatch.setenv("OPENAI_MODEL_CARDIO", "novo")
    assert cardio_agent_ai._resolve_model() == "novo"


# --- IA-06 (a): platô vem do dono -------------------------------------------


def test_prompt_da_analise_interpola_o_platô_do_dono_e_fica_identico(monkeypatch):
    capturado = {}
    monkeypatch.setattr(report_ai, "_call_openai", lambda prompt, *a, **k: capturado.setdefault("p", prompt))
    report_ai.analyze_training_evolution({"days": 90}, days=90)
    prompt = capturado["p"]
    assert report_ai.PLATEAU_MIN_EXPOSURES == THRESHOLDS["plateau_min_exposures"] == 3
    # A frase do platô (prosa do template) e privada (M103 P2,
    # `api/privado/tests/unit/test_workouts_analysis_prompt.py`).
    assert report_ai.prompt_values()["exposicoes_minimas_plato"] == "3"
    assert "{PLATEAU_MIN_EXPOSURES}" not in prompt and "{{" not in prompt
