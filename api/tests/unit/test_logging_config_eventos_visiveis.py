"""Eventos `log_event` (musclefit.events) visiveis no CloudWatch.

Bug de producao (2026-10-03): nenhum evento de `musclefit.events` saia nos
logs da `musclefit-api-core` - o raiz do processo uvicorn/LWA nao tem handler
e fica em WARNING, entao o `logger.info` do `log_event` era descartado. Ver
`app/logging_config.py`.

Cada teste roda com o logging global isolado: o raiz comeca SEM handler (o
estado real do processo uvicorn atras do Lambda Web Adapter) e tudo e
restaurado no fim, para nao vazar para os demais testes (o `caplog` depende
do raiz)."""

from __future__ import annotations

import io
import json
import logging
from contextlib import contextmanager
from pathlib import Path

import pytest

from app import access_log
from app.logging_config import APP_LOGGER_NAMESPACES, configure_logging
from app.observability import log_event

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@contextmanager
def _raiz_isolado():
    """O pytest pendura o handler de captura no raiz durante a FASE de
    chamada do teste (depois do setup das fixtures) - por isso o isolamento
    e um `with` dentro do corpo do teste."""
    root = logging.getLogger()
    saved_level, saved_handlers = root.level, list(root.handlers)
    saved_levels = {name: logging.getLogger(name).level for name in APP_LOGGER_NAMESPACES}
    root.handlers = []
    root.setLevel(logging.WARNING)
    for name in APP_LOGGER_NAMESPACES:
        logging.getLogger(name).setLevel(logging.NOTSET)
    try:
        yield root
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)


def _linhas(buf: io.StringIO) -> list[str]:
    return [linha for linha in buf.getvalue().splitlines() if linha.strip()]


def test_sem_configuracao_o_info_do_log_event_some():
    """Reproduz o bug: raiz sem handler e em WARNING descarta o INFO."""
    with _raiz_isolado():
        assert not logging.getLogger("musclefit.events").isEnabledFor(logging.INFO)


def test_log_event_sai_em_exatamente_uma_linha_json():
    with _raiz_isolado():
        buf = io.StringIO()
        configure_logging(stream=buf)

        log_event("x.y", a=1)

        linhas = _linhas(buf)
        assert len(linhas) == 1
        assert json.loads(linhas[0]) == {"event": "x.y", "a": 1}


def test_configure_logging_e_idempotente():
    with _raiz_isolado() as logging_isolado:
        buf = io.StringIO()
        configure_logging(stream=buf)
        configure_logging(stream=buf)
        configure_logging()

        log_event("x.y", a=1)

        assert len(logging_isolado.handlers) == 1
        assert len(_linhas(buf)) == 1


def test_http_access_continua_saindo_uma_vez_so():
    """O `fitness.access` tem handler proprio e `propagate=False`: o handler
    novo do raiz nao pode gerar a segunda linha. Os dois handlers escrevem no
    MESMO buffer, entao qualquer duplicacao apareceria aqui."""
    with _raiz_isolado():
        buf = io.StringIO()
        configure_logging(stream=buf)
        (access_handler,) = [h for h in access_log.logger.handlers if getattr(h, "_access_log", False)]
        original = access_handler.setStream(buf)
        try:
            access_log._emit(access_log.build_access_event("GET", "/health", 200, 1.23))
            log_event("x.y", a=1)
        finally:
            access_handler.setStream(original)

        linhas = [json.loads(linha) for linha in _linhas(buf)]
        assert [linha["event"] for linha in linhas] == ["http_access", "x.y"]
        assert linhas[0] == {"event": "http_access", "method": "GET", "route": "/health", "status": 200, "duration_ms": 1.2}


def test_raiz_com_handler_do_runtime_nao_ganha_segundo_handler():
    """Lambdas awslambdaric: o runtime ja pos um handler no raiz. Instalar
    outro duplicaria cada linha; o evento sai uma vez, pelo handler dele."""
    with _raiz_isolado() as logging_isolado:
        buf_runtime = io.StringIO()
        runtime_handler = logging.StreamHandler(buf_runtime)
        runtime_handler.setFormatter(logging.Formatter("%(message)s"))
        logging_isolado.addHandler(runtime_handler)

        buf_nosso = io.StringIO()
        configure_logging(stream=buf_nosso)
        log_event("x.y", a=1)

        assert logging_isolado.handlers == [runtime_handler]
        assert buf_nosso.getvalue() == ""
        linhas = _linhas(buf_runtime)
        assert len(linhas) == 1
        assert json.loads(linhas[0]) == {"event": "x.y", "a": 1}


def test_bibliotecas_de_terceiros_continuam_em_warning():
    with _raiz_isolado():
        configure_logging(stream=io.StringIO())
        assert not logging.getLogger("botocore").isEnabledFor(logging.INFO)
        assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)


@pytest.mark.parametrize(
    "arquivo",
    [
        "main.py",
        "main_engines.py",
        "lambda_plan_generator.py",
        "lambda_cardio_agent.py",
        "lambda_workout_ai.py",
        "lambda_intervals_sync.py",
        "migrate.py",
    ],
)
def test_todo_entrypoint_chama_configure_logging(arquivo):
    """Todo entrypoint de Lambda (e o app local, que e o mesmo main.py)
    passa pela configuracao central."""
    assert "configure_logging()" in (APP_DIR / arquivo).read_text(encoding="utf-8")
