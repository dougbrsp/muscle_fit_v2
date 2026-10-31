"""Resumo do registro de uso do eval pago (`tests/eval/resumo_uso_eval.py`):
agrupa por feature/modelo, junta o resultado do caso e usa o preco do dono."""

from __future__ import annotations

from decimal import Decimal

from app.domain.nutrition.ai_usage import estimate_cost_usd
from tests.eval.resumo_uso_eval import percentil, resumir


def _uso(nodeid, *, feature="set_evaluation", provider="anthropic", model="claude-haiku-5-5",
         entrada=1000, saida=200, cache=0, latencia=1000, status="ok"):
    return {"event": "llm.usage", "nodeid": nodeid, "feature": feature, "provider": provider, "model": model,
            "input_tokens": entrada, "output_tokens": saida, "cached_tokens": cache, "latency_ms": latencia,
            "status": status}


def test_percentil_pelo_posto_mais_proximo():
    assert percentil([], 50) is None
    assert percentil([300, 100, 200], 50) == 200
    assert percentil([100, 200, 300, 400], 95) == 400
    assert percentil([100, 200, 300, 400], 50) == 200


def test_resumir_agrupa_casos_custo_e_erros():
    linhas = [
        _uso("t[set_evaluation/a]", latencia=1000),
        {"event": "eval.case", "nodeid": "t[set_evaluation/a]", "outcome": "passed"},
        _uso("t[set_evaluation/b]", latencia=3000, status="incomplete"),
        {"event": "llm.error", "nodeid": "t[set_evaluation/b]", "code": None},
        {"event": "eval.case", "nodeid": "t[set_evaluation/b]", "outcome": "failed"},
        {"event": "eval.case", "nodeid": "t[periodization/c]", "outcome": "failed"},
    ]
    resumo = {(r["feature"], r["model"]): r for r in resumir(linhas)}

    haiku = resumo[("set_evaluation", "claude-haiku-5-5")]
    assert (haiku["aprovados"], haiku["casos"], haiku["chamadas"]) == (1, 2, 2)
    assert (haiku["p50_ms"], haiku["p95_ms"]) == (1000, 3000)
    assert haiku["custo_usd"] == 2 * estimate_cost_usd("claude-haiku-5-5", 1000, 200, 0)
    assert haiku["erros"] == {"status=incomplete": 1, "erro=sem_codigo": 1}

    sem_chamada = resumo[("periodization", "sem chamada")]
    assert (sem_chamada["aprovados"], sem_chamada["casos"], sem_chamada["custo_usd"]) == (0, 1, Decimal(0))


def test_mesmo_caso_em_dois_bracos_fica_em_cada_modelo():
    nodeid = "t[set_evaluation/a]"
    linhas = [
        {**_uso(nodeid, provider="openai", model="gpt-5.4-mini"), "arquivo": "a.jsonl"},
        {"event": "eval.case", "nodeid": nodeid, "outcome": "passed", "arquivo": "a.jsonl"},
        {**_uso(nodeid), "arquivo": "b.jsonl"},
        {"event": "eval.case", "nodeid": nodeid, "outcome": "failed", "arquivo": "b.jsonl"},
    ]
    resumo = {r["model"]: (r["aprovados"], r["casos"]) for r in resumir(linhas)}
    assert resumo == {"gpt-5.4-mini": (1, 1), "claude-haiku-5-5": (0, 1)}


def test_resumir_ignora_erro_com_retentativa_e_conta_pulados():
    linhas = [
        {"event": "llm.error", "nodeid": "t[insights/a]", "code": "OPENAI_RATE_LIMIT", "will_retry": True},
        _uso("t[insights/a]", feature="insights", provider="openai", model="gpt-5.4-mini"),
        {"event": "eval.case", "nodeid": "t[insights/a]", "outcome": "passed"},
        {"event": "eval.case", "nodeid": "t[insights/b]", "outcome": "skipped"},
    ]
    resumo = {(r["feature"], r["model"]): r for r in resumir(linhas)}
    assert resumo[("insights", "gpt-5.4-mini")]["erros"] == {}
    assert resumo[("insights", "sem chamada")]["pulados"] == 1
