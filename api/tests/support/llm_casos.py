"""Casos congelados das features de LLM (IA-11) e o executor UNICO usado
pelas duas metades:

- snapshot do prompt renderizado (`tests/unit/test_llm_prompt_renderizado_snapshot.py`,
  CI, sem chamada paga): `rag="fixo"` e a rede mockada pelo teste;
- eval pago manual (`tests/eval/test_llm_features_eval.py`, `-m llm_eval`):
  `rag="real"` e a chamada de verdade.

Os casos vivem em `tests/eval/casos/<feature>.json`, gerados por
`tests/eval/coletar_casos_llm.py` e trocados por valores sinteticos (M103 L2). O que e
congelado e a ENTRADA de cada feature; o executor chama a funcao REAL da
feature (a mesma que a Lambda chama), revivendo antes os poucos objetos que o
prompt formata por f-string (decisao do motor, teto composto, `WindowInfo`,
`DataQuality`) - assim o texto renderizado e o da producao.

Datas fixas: o cardio le `date.today()`/`datetime.now()`; o executor os
substitui pela data/hora da coleta gravadas no caso.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

CASOS_DIR = Path(__file__).resolve().parents[1] / "eval" / "casos"
RAG_DIR = Path(__file__).resolve().parents[3] / "api" / "privado" / "rag" / "markdown"

# No snapshot o RAG entra como este marcador: o RAG e conteudo versionado em
# `rag/markdown` (muda por PR proprio) e a selecao dele e medida a parte
# (`rag_chars` no log). O snapshot trava o TEMPLATE e os dados.
RAG_FIXO = "<<RAG: marcador fixo do snapshot - o eval injeta o RAG real>>"

FEATURES = (
    "set_evaluation",
    "exercise_summary",
    "workout_summary",
    "insights",
    "workouts_analysis",
    "cardio_agent",
    "periodization",
    "exercise_description",
    "meal_parse",  # nutrição L5b (`tests/support/meal_parse_eval.py`)
)

# Resposta falsa por feature no snapshot: so precisa deixar o fluxo terminar.
RESPOSTA_FALSA = {
    "set_evaluation": '{"category": "HOLD", "note": "Ok.", "next": {"reps": 10, "load_kg": 20, "rpe": 9}}',
    "periodization": '{"slots": [], "rationale": "", "source_citations": []}',
    "cardio_agent": '{"summary": "Ok.", "objective": "Ok.", "confidence": "medium", "confidence_reason": "Ok.", "sessions": []}',
    "exercise_description": '{"setup": [], "execution": ["Empurre."], "cues": []}',
    "meal_parse": (
        '{"items": [{"name": "arroz branco", "quantity": 100, "unit": "g", "grams_estimate": 100, '
        '"preparation": "cozido", "confidence": 0.9, "estimated_per_100g": null}], "notes": null}'
    ),
}
RESPOSTA_FALSA_PADRAO = "Resumo de teste."

# Default de modelo das features de texto (`OPENAI_*_MODEL` ausente).
MODELO_PADRAO = "gpt-5.4-mini"


def _resolver(value: Any, compartilhado: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$compartilhado"}:
            return _resolver(compartilhado[value["$compartilhado"]], compartilhado)
        return {k: _resolver(v, compartilhado) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolver(v, compartilhado) for v in value]
    return value


@lru_cache(maxsize=None)
def _documento(feature: str) -> str:
    return (CASOS_DIR / f"{feature}.json").read_text(encoding="utf-8")


def carregar(feature: str) -> list[dict[str, Any]]:
    """Casos da feature, com as referencias `$compartilhado` resolvidas.
    Copia nova a cada chamada (o executor pode mutar a entrada)."""
    documento = json.loads(_documento(feature))
    compartilhado = documento.get("compartilhado") or {}
    return [{**caso, "input": _resolver(caso["input"], compartilhado)} for caso in documento["cases"]]


def todos_os_casos() -> list[tuple[str, dict[str, Any]]]:
    return [(feature, caso) for feature in FEATURES for caso in carregar(feature)]


# ---------------------------------------------------------------------------
# Execucao (a funcao real de cada feature)
# ---------------------------------------------------------------------------


def _rag_real(monkeypatch) -> None:
    from app import exercise_set_evaluation_ai, rag_chunking

    monkeypatch.delenv("RAG_S3_BUCKET", raising=False)
    monkeypatch.setattr(rag_chunking, "RAG_MARKDOWN_DIR", RAG_DIR)
    monkeypatch.setattr(exercise_set_evaluation_ai, "RAG_MARKDOWN_DIR", RAG_DIR)


def leitura_da_serie(payload: dict[str, Any], composition: Any, engine: Any = None) -> Any:
    """Status da serie PELO MOTOR (set-eval-v4, RPE 10 Lote 6/P9) a partir do
    teto/alvo congelados no caso - a mesma funcao que o salvamento da serie e
    `dispatch_evaluate_set` usam - e, desde a v5, as reps contra a faixa
    (`evaluation_reps_range`: decisao do motor, senao `reps_target`). Nenhum
    status escrito a mao no caso."""
    from app.domain.effective_intensity import EffectiveIntensity, set_intensity_reading
    from app.engine_decision_context import evaluation_reps_range

    intensity = EffectiveIntensity(
        rpe_target_effective=composition.target,
        ceiling=composition.ceiling,
        failure_allowed=composition.failure_allowed,
        failure_denied_by=composition.failure_denied_by,
    )
    return set_intensity_reading(
        payload.get("set_type") or "working",
        payload.get("rpe"),
        intensity,
        reps=payload.get("reps"),
        reps_range=evaluation_reps_range(engine, payload.get("reps_target")),
    )


def _set_evaluation(entrada: dict[str, Any], monkeypatch, rag: str) -> dict[str, Any]:
    from app import exercise_set_evaluation_ai as mod
    from app.domain.training_safety import IntensityComposition
    from app.engine_decision_context import EngineDecision, PairEngineContext, next_set_load_for

    if rag == "fixo":
        monkeypatch.setattr(mod, "load_progressao_rag_text", lambda: RAG_FIXO)
    decision = entrada["engine"]["decision"]
    engine = PairEngineContext(
        step_kg=Decimal(entrada["engine"]["step_kg"]),
        decision=EngineDecision(**decision) if decision else None,
        load_convention=entrada["engine"].get("load_convention"),
    )
    composition = IntensityComposition(**entrada["composition"])
    payload = entrada["payload"]
    reading = leitura_da_serie(payload, composition, engine)
    # set-eval-v6: a regra da carga da proxima serie (a mesma de
    # `dispatch_evaluate_set`), calculada do caso - nenhuma direcao escrita a mao.
    next_set = next_set_load_for(payload, engine)
    # M95c: nomes do catalogo coletados com o caso (a producao le o catalogo
    # em `dispatch_evaluate_set` quando o objetivo e nao-default).
    bruto = mod.evaluate_set(
        payload,
        engine=engine,
        composition=composition,
        set_reading=reading,
        next_set=next_set,
        technique_names=entrada.get("technique_names"),
    )
    final = mod.apply_engine_load_bound(
        mod.apply_safety_clamp(bruto, composition), engine, executed_kg=payload.get("weight_kg"), next_set=next_set
    )
    return {"bruto": bruto, "final": final, "engine": engine, "composition": composition, "next_set": next_set}


def _exercise_summary(entrada: dict[str, Any], monkeypatch, rag: str) -> dict[str, Any]:
    from app import exercise_summary_ai as mod
    from app.engine_decision_context import EngineDecision

    payload = dict(entrada["payload"])
    if payload.get("engine_decision"):
        payload["engine_decision"] = EngineDecision(**payload["engine_decision"])
    return mod.summarize_exercise(payload)


def _workout_summary(entrada: dict[str, Any], monkeypatch, rag: str) -> dict[str, Any]:
    from app import workout_summary_ai as mod
    from app.engine_decision_context import EngineDecision, GroupDecision

    decisions = entrada["engine_decisions"]
    revividas = {
        "exercises": {int(k): EngineDecision(**v) for k, v in decisions["exercises"].items()},
        "groups": [GroupDecision(**g) for g in decisions["groups"]],
    }
    # M95: nomes do catalogo `advanced_techniques` coletados com o caso (a
    # producao le o catalogo em `dispatch_activity_summary`).
    return mod.generate_workout_summary(
        entrada["activity"], engine_decisions=revividas, technique_names=entrada.get("technique_names")
    )


def reviver_pacote(pacote: dict[str, Any]) -> dict[str, Any]:
    """`window` volta a ser `WindowInfo` e `data_quality` a ser `DataQuality`,
    como saem de `build_intelligence_package` (o prompt formata os dois)."""
    from app.intelligence.dashboard.contracts import DataQuality, WindowInfo

    pacote = dict(pacote)
    pacote["window"] = WindowInfo.model_validate(pacote["window"])
    if pacote.get("data_quality") is not None:
        pacote["data_quality"] = DataQuality(pacote["data_quality"])
    return pacote


def _insights(entrada: dict[str, Any], monkeypatch, rag: str) -> dict[str, Any]:
    from app import insights_ai
    from app.insights_core import select_insights_rag

    pacote = reviver_pacote(entrada["package"])
    # Mesmo recorte e teto da producao (`insights_core.dispatch_insights`).
    rag_text = RAG_FIXO if rag == "fixo" else select_insights_rag(entrada["rag_groups"])[1]
    resultado = insights_ai.generate_insight(
        entrada["question"], pacote, rag_text, technique_names=entrada.get("technique_names")
    )
    return {**resultado, "package": pacote}


def _workouts_analysis(entrada: dict[str, Any], monkeypatch, rag: str) -> dict[str, Any]:
    from app import report_ai
    from app.intelligence.report_data_service import ReportDataService

    training = entrada["training"]
    rag_context = RAG_FIXO if rag == "fixo" else ReportDataService().build_rag_context(training)
    texto = report_ai.analyze_training_evolution(training, days=entrada["days"], rag_context=rag_context)
    return {"insight": texto, "training": training}


def _cardio_agent(entrada: dict[str, Any], monkeypatch, rag: str) -> dict[str, Any]:
    """`cardio-agent-v3` (M98 C3a): o contexto v3 congelado entra no lugar de
    `build_context_v3`; envelope, prompt, validacao e markdown rodam de verdade.
    RAG: marcador fixo no snapshot, `CARDIO.md` real no eval."""
    from app import cardio_agent_ai as mod

    contexto = entrada["context"]
    hoje = date.fromisoformat(entrada["today"])
    agora = datetime.fromisoformat(f"{entrada['today']}T{entrada['now_time']}:00")

    class _Contexto:
        def build_context_v3(self, days: int = 14, user_id=None, **_kwargs):
            assert days == entrada["days"], "o caso foi coletado com outra janela"
            return copy.deepcopy(contexto)

    class _Date(date):
        @classmethod
        def today(cls):
            return hoje

    class _Datetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return agora

    monkeypatch.setattr(mod, "CardioAgentContextV3Service", _Contexto)
    monkeypatch.setattr(mod, "date", _Date)
    monkeypatch.setattr(mod, "datetime", _Datetime)
    if rag == "fixo":
        monkeypatch.setattr(mod, "load_rag_text", lambda: RAG_FIXO)
    return mod.generate_cardio_recommendation(
        days=entrada["days"],
        proposed_workout=entrada.get("proposed_workout"),
        reference_date=entrada.get("reference_date"),
        reference_time=entrada.get("reference_time"),
    )


def _periodization(entrada: dict[str, Any], monkeypatch, rag: str) -> dict[str, Any]:
    """`run_periodization_draft` de verdade, sem banco: o contexto congelado
    entra no lugar de `build_context`, e o validador (que precisa do banco
    para checar ids) e trocado por uma identidade - o eval julga a SAIDA
    CRUA do modelo, antes de qualquer auto-fix."""
    from app.periodization import core
    from app.periodization.context import load_rag_text

    contexto = copy.deepcopy(entrada["context"])
    contexto["rag_text"] = (
        RAG_FIXO if rag == "fixo" else load_rag_text(list(entrada["request"].get("rag_sources") or []), entrada["rag_target_groups"])
    )
    if contexto.get("cardio"):
        # M98 C5: o recorte do RAG de cardio segue a mesma regra (fixo no snapshot).
        from app.rag_chunking import select_periodization_cardio_rag_text

        contexto["cardio"]["rag_text"] = RAG_FIXO if rag == "fixo" else select_periodization_cardio_rag_text()
    # Snapshot: modelo fixo (o corpo HTTP inclui o modelo). Eval: o da env,
    # ou o mesmo default das outras features de texto.
    if rag == "fixo" or not (os.environ.get(core.MODEL_ENV_VAR) or "").strip():
        monkeypatch.setenv(core.MODEL_ENV_VAR, MODELO_PADRAO)
    monkeypatch.setattr(core, "transaction", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(core, "build_context", lambda conn, request, user_id: contexto)
    monkeypatch.setattr(core, "finish_log", lambda *a, **k: None)
    monkeypatch.setattr(
        core,
        "validate_draft",
        lambda conn, request, data, **_kw: (
            data,
            {"errors": [], "warnings": []},
            {"rationale": data.get("rationale"), "source_citations": data.get("source_citations")},
        ),
    )
    resultado = core.run_periodization_draft(entrada["request"], user_id=None)
    return {**resultado, "context": contexto}


def _exercise_description(entrada: dict[str, Any], monkeypatch, rag: str) -> dict[str, Any]:
    from app import exercise_description_ai as mod

    gerada = mod.generate_execution_description(entrada["exercise"], entrada["muscles"])
    return {"text": gerada.text, "model": gerada.model, "prompt_version": gerada.prompt_version}


def _meal_parse(entrada: dict[str, Any], monkeypatch, rag: str) -> dict[str, Any]:
    from tests.support import meal_parse_eval

    return meal_parse_eval.executar(entrada, monkeypatch, rag, MODELO_PADRAO)


EXECUTORES: dict[str, Callable[[dict[str, Any], Any, str], Any]] = {
    "set_evaluation": _set_evaluation,
    "exercise_summary": _exercise_summary,
    "workout_summary": _workout_summary,
    "insights": _insights,
    "workouts_analysis": _workouts_analysis,
    "cardio_agent": _cardio_agent,
    "periodization": _periodization,
    "exercise_description": _exercise_description,
    "meal_parse": _meal_parse,
}


def executar(feature: str, caso: dict[str, Any], monkeypatch, *, rag: str) -> Any:
    """Roda a feature com a entrada congelada do caso. `rag`: "fixo"
    (snapshot) ou "real" (eval). A rede NAO e tocada aqui: o snapshot mocka
    `urlopen`; o eval chama a OpenAI de verdade."""
    if rag not in ("fixo", "real"):
        raise ValueError(rag)
    if rag == "real":
        _rag_real(monkeypatch)
    return EXECUTORES[feature](copy.deepcopy(caso["input"]), monkeypatch, rag)


__all__ = ["CASOS_DIR", "FEATURES", "RAG_FIXO", "RESPOSTA_FALSA", "carregar", "executar", "todos_os_casos"]
