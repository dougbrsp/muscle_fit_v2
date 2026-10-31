"""Resumo do registro de uso do eval PAGO (`LLM_EVAL_USAGE_JSONL`, ver
`tests/eval/conftest.py`): por feature e modelo, casos aprovados, erros do
provedor, latencia p50/p95, tokens e custo.

    cd api && python -m tests.eval.resumo_uso_eval uso_openai.jsonl uso_claude.jsonl

O custo usa a tabela de precos do dono (`app.domain.nutrition.ai_usage`,
`estimate_cost_usd`: entrada, entrada lida do cache e saida). A escrita no
cache da Anthropic (`cache_write_tokens`, cobrada a mais que a entrada comum)
nao esta na tabela do dono: o resumo a soma ao preco de entrada e mostra os
tokens escritos a parte, para quem ler saber que o custo dessas linhas e um
piso. Nao chama a rede.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.domain.nutrition.ai_usage import estimate_cost_usd  # noqa: E402

# Arquivo do caso -> feature, para os casos que falharam antes de chamar o LLM.
_FEATURE_POR_ARQUIVO = {
    "test_nutrition_label_eval.py": "label_photo",
    "test_llm_insights_eval.py": "insights",
}


def _feature_do_nodeid(nodeid: str) -> Optional[str]:
    if "[" in nodeid:
        parametro = nodeid.split("[", 1)[1]
        if "/" in parametro:
            return parametro.split("/", 1)[0]
    for arquivo, feature in _FEATURE_POR_ARQUIVO.items():
        if arquivo in nodeid:
            return feature
    return None


def percentil(valores: list[int], p: float) -> Optional[int]:
    """Percentil pelo posto mais proximo (sem interpolar); None sem valores."""
    if not valores:
        return None
    ordenados = sorted(valores)
    posto = max(1, math.ceil(p / 100 * len(ordenados)))
    return ordenados[posto - 1]


def ler(caminhos: Iterable[Path]) -> list[dict[str, Any]]:
    linhas: list[dict[str, Any]] = []
    for caminho in caminhos:
        for texto in caminho.read_text(encoding="utf-8").splitlines():
            if texto.strip():
                # O mesmo caso aparece em cada braco: o arquivo separa os nodeids.
                linhas.append({**json.loads(texto), "arquivo": str(caminho)})
    return linhas


def _caso(linha: dict[str, Any]) -> tuple[str, str]:
    return (str(linha.get("arquivo")), str(linha.get("nodeid")))


def custo_da_chamada(evento: dict[str, Any]) -> Optional[Decimal]:
    custo = estimate_cost_usd(
        evento.get("model"), evento.get("input_tokens"), evento.get("output_tokens"), evento.get("cached_tokens")
    )
    return custo


def resumir(linhas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Uma linha por (feature, provedor, modelo)."""
    grupo_do_caso: dict[str, tuple[str, str, str]] = {}
    grupos: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"casos": 0, "aprovados": 0, "pulados": 0, "chamadas": 0, "latencias": [], "entrada": 0,
                 "cache_lido": 0, "cache_escrito": 0, "saida": 0, "raciocinio": 0, "custo": Decimal(0),
                 "sem_preco": 0, "erros": defaultdict(int)}
    )
    for linha in linhas:
        if linha.get("event") != "llm.usage":
            continue
        modelo = str(linha.get("model") or "?")
        chave = (str(linha.get("feature") or "?"), str(linha.get("provider") or "openai"), modelo)
        grupo_do_caso.setdefault(_caso(linha), chave)
        g = grupos[chave]
        g["chamadas"] += 1
        if linha.get("latency_ms") is not None:
            g["latencias"].append(int(linha["latency_ms"]))
        status = linha.get("status") or "ok"
        if status != "ok":
            g["erros"][f"status={status}"] += 1
        for campo, destino in (("input_tokens", "entrada"), ("cached_tokens", "cache_lido"),
                               ("cache_write_tokens", "cache_escrito"), ("output_tokens", "saida"),
                               ("reasoning_tokens", "raciocinio")):
            g[destino] += int(linha.get(campo) or 0)
        custo = custo_da_chamada(linha)
        if custo is None:
            if linha.get("input_tokens") is not None:
                g["sem_preco"] += 1
        else:
            g["custo"] += custo
    for linha in linhas:
        nodeid = str(linha.get("nodeid"))
        if linha.get("event") == "llm.error" and not linha.get("will_retry"):
            chave = grupo_do_caso.get(_caso(linha))
            if chave is not None:
                grupos[chave]["erros"][f"erro={linha.get('code') or linha.get('error_code') or 'sem_codigo'}"] += 1
        if linha.get("event") != "eval.case":
            continue
        chave = grupo_do_caso.get(_caso(linha))
        if chave is None:
            chave = (_feature_do_nodeid(nodeid) or "?", "?", "sem chamada")
        g = grupos[chave]
        if linha.get("outcome") == "skipped":
            g["pulados"] += 1
            continue
        g["casos"] += 1
        g["aprovados"] += linha.get("outcome") == "passed"
    saida = []
    for (feature, provedor, modelo), g in sorted(grupos.items()):
        saida.append({
            "feature": feature, "provider": provedor, "model": modelo,
            "aprovados": g["aprovados"], "casos": g["casos"], "pulados": g["pulados"], "chamadas": g["chamadas"],
            "p50_ms": percentil(g["latencias"], 50), "p95_ms": percentil(g["latencias"], 95),
            "entrada": g["entrada"], "cache_lido": g["cache_lido"], "cache_escrito": g["cache_escrito"],
            "saida": g["saida"], "raciocinio": g["raciocinio"], "custo_usd": g["custo"],
            "custo_por_chamada_usd": (g["custo"] / g["chamadas"]) if g["chamadas"] else None,
            "sem_preco": g["sem_preco"], "erros": dict(g["erros"]),
        })
    return saida


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Resumo do registro de uso do eval pago.")
    parser.add_argument("arquivos", nargs="+", type=Path)
    args = parser.parse_args(argv)
    total = Decimal(0)
    print("| feature | provedor | modelo | aprovados | p50 ms | p95 ms | entrada | cache lido | cache escrito "
          "| saída | US$ total | US$/chamada | erros |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in resumir(ler(args.arquivos)):
        total += r["custo_usd"]
        por_chamada = r["custo_por_chamada_usd"]
        erros = ", ".join(f"{k}×{v}" for k, v in sorted(r["erros"].items())) or "-"
        if r["sem_preco"]:
            erros += f" (sem preço ×{r['sem_preco']})"
        print(f"| {r['feature']} | {r['provider']} | {r['model']} | {r['aprovados']}/{r['casos']} "
              f"| {r['p50_ms']} | {r['p95_ms']} | {r['entrada']} | {r['cache_lido']} | {r['cache_escrito']} "
              f"| {r['saida']} | {r['custo_usd']:.4f} | {'-' if por_chamada is None else f'{por_chamada:.5f}'} "
              f"| {erros} |")
    print(f"\nTotal: US$ {total:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
