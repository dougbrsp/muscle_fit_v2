"""Registro de uso do eval PAGO (`-m llm_eval`): tokens, latencia e resultado
por caso, para comparar provedores/modelos sem ler o log a mao.

Com `LLM_EVAL_USAGE_JSONL=<arquivo>`, cada evento `llm.usage`, `llm.error` e
`llm.output_incomplete` emitido pelo cliente unico (`app.openai_client` /
`app.llm_anthropic`) durante um caso vira uma linha JSON no arquivo, com o
`nodeid` do caso; ao fim de cada caso entra uma linha `eval.case` com o
resultado (`passed`/`failed`/`skipped`). Sem a env, nada muda.

Os eventos so levam metadados (provedor, modelo, tokens, latencia, status,
codigo do erro e diagnostico ja mascarado): nunca o prompt nem a resposta.
O resumo por feature (aprovacao, falhas, p50/p95, custo) sai de
`python -m tests.eval.resumo_uso_eval <arquivo>`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import pytest

ENV_USAGE_JSONL = "LLM_EVAL_USAGE_JSONL"
LOGGER_EVENTOS = "musclefit.events"  # `app.observability.logger`
EVENTOS_REGISTRADOS = frozenset({"llm.usage", "llm.error", "llm.output_incomplete"})

_estado: dict[str, Optional[str]] = {"nodeid": None}


def _destino() -> Optional[Path]:
    valor = (os.environ.get(ENV_USAGE_JSONL) or "").strip()
    return Path(valor) if valor else None


def _gravar(linha: dict) -> None:
    destino = _destino()
    if destino is None:
        return
    with destino.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")


class _HandlerUso(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = json.loads(record.getMessage())
        except (TypeError, ValueError):
            return
        if isinstance(payload, dict) and payload.get("event") in EVENTOS_REGISTRADOS:
            _gravar({"nodeid": _estado["nodeid"], **payload})


@pytest.fixture(autouse=True)
def _registro_de_uso(request):
    if _destino() is None:
        yield
        return
    handler = _HandlerUso(level=logging.INFO)
    raiz = logging.getLogger(LOGGER_EVENTOS)
    nivel_anterior = raiz.level
    raiz.addHandler(handler)
    raiz.setLevel(logging.INFO)
    _estado["nodeid"] = request.node.nodeid
    try:
        yield
    finally:
        raiz.removeHandler(handler)
        raiz.setLevel(nivel_anterior)
        _estado["nodeid"] = None


def pytest_runtest_logreport(report):
    if _destino() is None:
        return
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        _gravar({"event": "eval.case", "nodeid": report.nodeid, "outcome": report.outcome})
