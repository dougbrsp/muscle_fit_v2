"""Guarda anti-vazamento dev/prod: fonte unica do criterio de "Lambda real".

Dentro de uma Lambda real o backend de banco TEM que ser o Data API. Config de
dev (`DB_BACKEND=pymysql`) vazada para la significaria falar TCP com um MySQL
inexistente (ou pior, com outro banco). O criterio vive so aqui; `db.py`,
`migrate.py` e `lambda_intervals_sync.py` o reutilizam.

Criterio: `AWS_LAMBDA_FUNCTION_NAME` definido e nao vazio (o runtime da AWS
sempre o define). O emulador local de Lambdas do dev
(`dev/lambda-local/lambda_invoke_emulator.py`) NAO o define, entao o dev com
`DB_BACKEND=pymysql` continua funcionando.
"""

from __future__ import annotations

import os
from typing import Callable

BACKEND_PADRAO = "data-api"
MENSAGEM_RECUSA = "DB_BACKEND diferente de data-api dentro de uma Lambda; recusando executar."


def em_lambda_real() -> bool:
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def backend_vazado_para_lambda() -> bool:
    """True se estamos numa Lambda real com DB_BACKEND diferente de data-api."""
    return em_lambda_real() and os.environ.get("DB_BACKEND", BACKEND_PADRAO) != BACKEND_PADRAO


def exigir_data_api_em_lambda(erro: Callable[[str], Exception] = RuntimeError) -> None:
    """Levanta `erro(MENSAGEM_RECUSA)` se o backend de dev vazou para uma Lambda."""
    if backend_vazado_para_lambda():
        raise erro(MENSAGEM_RECUSA)
