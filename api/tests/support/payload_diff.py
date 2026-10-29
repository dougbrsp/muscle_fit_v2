"""Comparacao de payload CAMPO A CAMPO - a metade "equivalencia" de toda
otimizacao de desempenho deste projeto.

Morava em `test_dashboard_drilldown_performance.py` (que roda so sob demanda)
e passou a viver aqui no M30, porque as mesmas comparacoes agora rodam tambem
no dataset pequeno, em todo PR. Uma implementacao so: a mensagem de falha
("divergencias") e o tratamento de float sao iguais em toda a suite.
"""

from __future__ import annotations

from typing import Any


def diferencas(antes: Any, depois: Any, caminho: str = "", tolerancia: float = 1e-9) -> list[str]:
    """Lista as divergencias entre dois payloads.

    Numeros de ponto flutuante admitem `tolerancia` (uma soma feita em outra
    ordem nao da bit a bit o mesmo resultado - soma de float nao e
    associativa); todo o resto - status, textos de `explanation`,
    recomendacoes, datas, inteiros - precisa ser IGUAL.
    """
    if isinstance(antes, dict) and isinstance(depois, dict):
        problemas: list[str] = []
        for chave in sorted(set(antes) | set(depois)):
            if chave not in antes or chave not in depois:
                problemas.append(f"{caminho}.{chave}: presente em apenas um dos payloads")
                continue
            problemas.extend(diferencas(antes[chave], depois[chave], f"{caminho}.{chave}", tolerancia))
        return problemas
    if isinstance(antes, list) and isinstance(depois, list):
        if len(antes) != len(depois):
            return [f"{caminho}: tamanhos {len(antes)} != {len(depois)}"]
        problemas = []
        for indice, (a, d) in enumerate(zip(antes, depois)):
            problemas.extend(diferencas(a, d, f"{caminho}[{indice}]", tolerancia))
        return problemas
    if isinstance(antes, bool) or isinstance(depois, bool):
        return [] if antes == depois else [f"{caminho}: {antes!r} != {depois!r}"]
    if isinstance(antes, (int, float)) and isinstance(depois, (int, float)):
        if abs(float(antes) - float(depois)) <= tolerancia:
            return []
        return [f"{caminho}: {antes!r} != {depois!r}"]
    return [] if antes == depois else [f"{caminho}: {antes!r} != {depois!r}"]


__all__ = ["diferencas"]
