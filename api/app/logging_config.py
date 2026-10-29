"""Configuracao UNICA de logging da aplicacao (api-core, engines, Lambdas
awslambdaric e docker local).

Pergunta que este modulo responde: por que um `log_event(...)` (logger
`musclefit.events`, `app/observability.py`) aparece no CloudWatch?

Ate este modulo, nao aparecia. Nenhum entrypoint configurava o logging da
aplicacao:
- api-core/engines rodam `uvicorn` atras do Lambda Web Adapter. O uvicorn
  configura so os loggers `uvicorn*` (com `propagate=False`) e deixa o raiz
  SEM handler e em WARNING. Um INFO de `musclefit.events` era descartado ja no
  `logger.info` (nivel efetivo herdado do raiz = WARNING) - e mesmo um WARNING
  so saia pelo `logging.lastResort`;
- as Lambdas awslambdaric (plan-generator, cardio-agent, workout-ai,
  intervals-sync, migrations) tem um handler no raiz instalado pelo runtime,
  mas o raiz fica em WARNING: o INFO morria do mesmo jeito (so intervals-sync
  e migrations subiam o raiz para INFO).
So `fitness.access` (M56) saia, porque instala o proprio handler em stdout
com `propagate=False`.

O que `configure_logging()` faz (idempotente, sem nada condicionado a
ambiente):
1. sobe para INFO os namespaces da aplicacao (`musclefit`, `fitness`) - so
   eles; bibliotecas de terceiros continuam no WARNING herdado do raiz;
2. se o raiz NAO tem handler nenhum (uvicorn/LWA, docker local), instala UM
   handler em stdout com a mensagem crua (formato so da mensagem): a linha inteira e o
   JSON do evento, e o Logs Insights descobre os campos sozinho. Se o raiz ja
   tem handler (o do runtime awslambdaric, o do pytest), nao instala outro -
   e isso que impede a linha duplicada.

Sem duplicacao com `fitness.access`: ele tem `propagate=False`, entao o
registro dele nunca chega ao handler do raiz.
Os demais loggers da aplicacao continuam com `propagate=True` (o `caplog` dos
testes depende disso).
"""

from __future__ import annotations

import logging
import sys
from typing import Optional, TextIO

APP_LOGGER_NAMESPACES = ("musclefit", "fitness")

_HANDLER_MARK = "_musclefit_root_stdout"


def configure_logging(stream: Optional[TextIO] = None) -> None:
    """Deixa os loggers da aplicacao emitirem INFO, uma linha por registro.

    Chamado no startup de TODO entrypoint: `app/main.py`, `app/main_engines.py`
    (import do modulo pelo uvicorn) e no inicio do `handler` de cada Lambda
    awslambdaric (quando o runtime ja instalou o handler dele no raiz).
    `stream` existe so para teste; o padrao e `sys.stdout`."""
    for name in APP_LOGGER_NAMESPACES:
        logging.getLogger(name).setLevel(logging.INFO)

    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
