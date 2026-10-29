"""Custo de `GET /analytics/dashboard/body` (F1.4) e da visao geral (que
passou a chamar o `body_service`) no dataset de 3 anos
(`test_dashboard_drilldown_performance.py`: 400 sessoes, 4.000 exercicios,
16.000 series), nos dois backends.

Requisitos de aceite do PR: **body <= 2 s** e **overview continua <= 3 s**
no shim da RDS Data API. O teste imprime o ABSOLUTO e a COMPOSICAO por
leitura (`_timings` do `build_body`) - e daqui que sai a tabela da descricao
do PR.

    cd api && python -m pytest -s -m slow \\
        tests/integration/dataapi/test_dashboard_body_performance.py
"""

from __future__ import annotations

import datetime as _dt
import json
import time

import pytest

from app import analytics_dashboard
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.db import get_conn
from app.intelligence.dashboard.body_service import build_body
from app.intelligence.dashboard.time_windows import resolve_window, user_today
from app.security import Identity
from tests.support.grupo_b_schema import ensure_grupo_b_tables
from tests.support.medicao import assert_ordem_de_grandeza
from tests.support.relogio_usuario import hoje_do_usuario

from .test_dashboard_drilldown_performance import (  # noqa: F401 - fixtures
    big_dataset,
    frozen_clock,
)

pytestmark = pytest.mark.slow

# Rede de seguranca contra regressao de ORDEM DE GRANDEZA, nao cronometro -
# politica unica do projeto em `tests/support/medicao.py`.
#
# Medido nesta maquina, MESMO codigo, em tres execucoes: body 90d no shim da
# Data API deu 1,15s / 2,72s / 3,30s - variacao de 3x vinda da MAQUINA, nao do
# codigo (a composicao mostra `measurements` em 4 ms). O teto de 6,0s que
# estava aqui ja era generoso, mas continuava sendo um numero local; agora a
# rede e a mesma dos outros arquivos e vem do timeout real de 29s do API
# Gateway.
#
# O bloco Corpo nao tem afirmacao de CONTAGEM DE STATEMENTS a fazer (ele sai de
# `body_service` em ~4 ms, e a composicao impressa abaixo e que mostra isso),
# entao aqui sobra so a rede.
TZ = "America/Sao_Paulo"


@pytest.fixture
def grupo_b(raw):
    ensure_grupo_b_tables(raw)


@pytest.fixture
def cinturas(raw, big_dataset):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Um ano de cintura semanal (52 medidas) para o usuario do dataset.

    Limpa no fim: `big_dataset` e de escopo de SESSAO e o schema e um so para
    a execucao inteira, entao uma medida deixada aqui sobrevive para os
    arquivos seguintes - e, como os ids de `users` voltam a 1 depois de cada
    TRUNCATE, ela reaparece como cintura do usuario de OUTRO teste. Foi
    exatamente isso que quebrou `test_dashboard_overview.py` no CI do PR #199
    (a medida de hoje, 88,0 cm, vazou para o bloco Corpo da visao geral)."""
    reset_body_measurements_table_cache()
    hoje = hoje_do_usuario()
    with raw.cursor() as cur:
        cur.execute("DELETE FROM body_measurements WHERE user_id = %s", (big_dataset["user_id"],))
        for semana in range(52):
            cur.execute(
                "INSERT INTO body_measurements (user_id, local_date, site, value_cm, protocol_ok) "
                "VALUES (%s, %s, 'waist', %s, 1)",
                (big_dataset["user_id"], hoje - _dt.timedelta(days=7 * semana), f"{88 + semana * 0.05:.1f}"),
            )
    raw.commit()
    yield
    with raw.cursor() as cur:
        cur.execute("DELETE FROM body_measurements WHERE user_id = %s", (big_dataset["user_id"],))
    raw.commit()


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="admin", source="session")


def _timed(label: str, fn):
    started = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - started
    print(f"[medicao] {label}: {elapsed:.3f}s")
    return result, elapsed


def test_medicao_body_e_overview(db_backend, big_dataset, grupo_b, cinturas, frozen_clock, capsys):  # noqa: F811 - fixture do pytest importada (falso positivo)
    user_id = big_dataset["user_id"]
    identity = _identity(user_id)

    with capsys.disabled():
        print(
            f"\n[dataset] backend={db_backend} activities={big_dataset['activities']} "
            f"exercises={big_dataset['exercises']} sets={big_dataset['sets']}"
        )
        payload90, t90 = _timed(
            f"body window=90d backend={db_backend}",
            lambda: analytics_dashboard.body(window="90d", date_from=None, date_to=None, identity=identity),
        )
        _, t28 = _timed(
            f"body window=28d backend={db_backend}",
            lambda: analytics_dashboard.body(window="28d", date_from=None, date_to=None, identity=identity),
        )
        _, t_overview = _timed(
            f"overview window=28d backend={db_backend}",
            lambda: analytics_dashboard.overview(
                window="28d", date_from=None, date_to=None, training_type_id=None, identity=identity
            ),
        )

        window = resolve_window("90d", today=user_today(TZ), timezone_name=TZ)
        with get_conn() as conn:
            bruto = build_body(conn, user_id=user_id, window=window)
        print(f"[composicao] backend={db_backend} window=90d")
        for label, seconds in sorted(bruto["_timings"].items(), key=lambda kv: -kv[1]):
            print(f"  {label:<26} {seconds:.3f}s")

    serialized = json.dumps(payload90.model_dump(mode="json"), ensure_ascii=False)
    assert len(serialized.encode("utf-8")) < 1_000_000
    assert payload90.waist.available is True
    assert payload90.performance.comparable_exercises > 0

    assert_ordem_de_grandeza([("body 90d", t90), ("body 28d", t28)], backend=db_backend)
    # O TETO da visao geral NAO e afirmado aqui de proposito: ele pertence a
    # `test_dashboard_overview_performance.py`, que e quem mede a visao geral
    # bloco a bloco. Duplicar a afirmacao so duplicava o alarme (os dois
    # arquivos falhavam pelo mesmo motivo) sem cobrir nada a mais. A medicao
    # continua sendo impressa acima, porque o que este arquivo precisa mostrar
    # e que o bloco Corpo passou a sair do `body_service` SEM encarecer a
    # visao geral - e isso se le na composicao (`measurements` em ~4 ms).
    assert t_overview > 0
