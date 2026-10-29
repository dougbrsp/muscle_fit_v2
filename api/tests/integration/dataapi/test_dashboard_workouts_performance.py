"""Custo de `GET /analytics/dashboard/workouts`, `/workouts/{id}` e
`/adherence` (F1.1) no MESMO dataset de 3 anos que mediu o drill-down v2 em
`#188` (`test_dashboard_drilldown_performance.py`): 400 sessoes, 4.000
exercicios, 16.000 series.

Por que existe: a tela v2 estourou o timeout de 29s do API Gateway em
producao porque `VolumeService.get_muscle_group_overview` le o historico
COMPLETO do usuario a cada chamada. O requisito desta F1.1 e **<= 3s** para as
rotas novas, e a unica forma honesta de afirmar isso e medindo no mesmo lugar
- inclusive contra a rota v2 que esta tela deixa de consumir
(`dashboard-workout-sessions`), impressa lado a lado.

O dataset e o gerador vem do arquivo do `#188` (um unico seed, uma unica
definicao de "dataset grande"); este arquivo acrescenta as FICHAS e os
VINCULOS (`activities.workout_plan_id`, `exercises.workout_plan_exercise_id`),
sem os quais as derivacoes do §5 nao teriam o que percorrer - medir o caminho
"sem ficha nenhuma" seria medir o caso facil.

    cd api && python -m pytest -s -m slow \\
        tests/integration/dataapi/test_dashboard_workouts_performance.py
"""

from __future__ import annotations

import json
import os
import time

import pytest

from app import analytics, analytics_dashboard
from app.intelligence.dashboard.adherence_service import (
    DEFAULT_WORKOUTS_LIMIT,
    MAX_DETAIL_SETS,
)
from app.security import Identity
from tests.support.medicao import assert_no_maximo_statements, assert_ordem_de_grandeza
from tests.support.sql_capture import recorded_sql
from tests.support.dataapi_backend import raw_mysql

from .test_dashboard_drilldown_performance import (  # noqa: F401 - fixtures
    big_dataset,
    frozen_clock,
)

pytestmark = pytest.mark.slow

# Teto de aceite deste PR (mais apertado que os 5s da F1.2: aqui nao existe
# bloco opcional caro - tudo o que a tela pede esta no caminho medido).
# O teto de 3,0s que estava aqui era o aceite deste PR, medido numa maquina
# especifica; como criterio de CI so produzia flake (ver
# tests/support/medicao.py). Relogio = rede de ordem de grandeza; a
# protecao de verdade e o teto DETERMINISTICO de statements, porque a
# regressao desta lista e um N+1 por sessao (a pagina traz 200).
#
# Medido no dataset de 3 anos: 8 statements, IDENTICO nos dois backends. O teto
# de 16 dobra isso - cabe consulta nova legitima, e nao cabe nenhuma passada
# por sessao nem por item de ficha.
MAX_STATEMENTS_WORKOUTS = 16


@pytest.fixture
def dataset_com_fichas(big_dataset, mysql_schema) -> dict:  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Uma ficha por sessao, com um item por exercicio executado e o vinculo
    `exercises.workout_plan_exercise_id` preenchido.

    Metade dos itens fica com `sets_count = 4` (o executado tem 4 series, das
    quais 1 e aquecimento -> 3 de trabalho, logo INTERRUPTED) e metade com
    `sets_count = 3` (COMPLETED): o caminho medido e o que tem trabalho de
    verdade a derivar, nao uma lista de vinculos vazios.

    Acompanha `big_dataset`: deixou de ser de escopo de sessao no M30 (o seed
    de outro dataset trunca `workout_plans`), e em vez disso confere um
    sentinela barato - uma ficha por sessao - antes de recriar."""
    user_id = big_dataset["user_id"]
    with raw_mysql(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=mysql_schema,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM workout_plans WHERE user_id = %s", (user_id,))
            ja_tem = int(next(iter((cur.fetchone() or {"n": 0}).values())))
            if ja_tem == big_dataset["activities"]:
                return {**big_dataset, "plans": ja_tem}
            cur.execute(
                "SELECT id, local_date FROM activities WHERE user_id = %s ORDER BY id ASC",
                (user_id,),
            )
            activities = [(row[0] if isinstance(row, (tuple, list)) else row["id"],
                           row[1] if isinstance(row, (tuple, list)) else row["local_date"])
                          for row in cur.fetchall()]

            for index, (activity_id, local_date) in enumerate(activities):
                cur.execute(
                    "INSERT INTO workout_plans (user_id, plan_date, name, plan_type, status) "
                    "VALUES (%s, %s, %s, 'abc', 'planned')",
                    (user_id, local_date, f"Ficha {index + 1}"),
                )
                plan_id = cur.lastrowid
                cur.execute(
                    "SELECT id, sequence, original_name FROM exercises WHERE activity_id = %s "
                    "ORDER BY sequence ASC",
                    (activity_id,),
                )
                exercises = [
                    (row[0], row[1], row[2]) if isinstance(row, (tuple, list))
                    else (row["id"], row["sequence"], row["original_name"])
                    for row in cur.fetchall()
                ]
                prescricao = json.dumps({"schema_version": 1, "sets_count": 3, "reps_target": "8-10"})
                for exercise_row_id, sequence, name in exercises:
                    sets_count = 4 if (exercise_row_id % 2) else 3
                    cur.execute(
                        "INSERT INTO workout_plan_exercises (plan_id, sequence, exercise_name, "
                        "sets_count, reps_target, prescription_json) VALUES (%s, %s, %s, %s, '8-10', %s)",
                        (plan_id, sequence, name, sets_count, prescricao if sets_count == 3 else None),
                    )
                    cur.execute(
                        "UPDATE exercises SET workout_plan_exercise_id = %s WHERE id = %s",
                        (cur.lastrowid, exercise_row_id),
                    )
                cur.execute(
                    "UPDATE activities SET workout_plan_id = %s, session_status = 'done' WHERE id = %s",
                    (plan_id, activity_id),
                )
        conn.commit()
    return {**big_dataset, "plans": len(activities)}


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="admin", source="session")


def _timed(label: str, fn):
    started = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - started
    print(f"[medicao] {label}: {elapsed:.3f}s")
    return result, elapsed


def _workouts(identity, *, window="90d", limit=DEFAULT_WORKOUTS_LIMIT, offset=0):
    """Chamada direta da funcao do endpoint: como nao passa pelo FastAPI, os
    defaults `Query(...)` precisam ser informados explicitamente."""
    return analytics_dashboard.workouts(
        window=window,
        date_from=None,
        date_to=None,
        limit=limit,
        offset=offset,
        identity=identity,
    )


def test_medicao_workouts_detalhe_e_aderencia(
    db_backend, dataset_com_fichas, frozen_clock, monkeypatch, fake_rds, capsys  # noqa: F811 - fixture do pytest importada (falso positivo)
):
    user_id = dataset_com_fichas["user_id"]
    identity = _identity(user_id)

    with capsys.disabled():
        print(
            f"\n[dataset] backend={db_backend} activities={dataset_com_fichas['activities']} "
            f"exercises={dataset_com_fichas['exercises']} sets={dataset_com_fichas['sets']} "
            f"plans={dataset_com_fichas['plans']}"
        )

        # ANTES: a rota v2 que esta tela consumia ate este PR.
        _, t_v2 = _timed(
            f"[v2] dashboard-workout-sessions days=90 backend={db_backend}",
            lambda: analytics.dashboard_workout_sessions(days=90, limit=200, identity=identity),
        )
        _, t_v2_ciclo = _timed(
            f"[v2] dashboard-cycle                 backend={db_backend}",
            lambda: analytics.dashboard_cycle(identity=identity),
        )

        # DEPOIS: as rotas novas.
        with monkeypatch.context() as ctx:
            with recorded_sql(db_backend, ctx, fake_rds) as stmts90:
                lista, t_lista = _timed(
                    f"[v3] dashboard/workouts window=90d   backend={db_backend}",
                    lambda: _workouts(identity, window="90d"),
                )
        print(f"[statements] dashboard/workouts 90d backend={db_backend}: {len(stmts90)}")
        _, t_lista_180 = _timed(
            f"[v3] dashboard/workouts window=180d  backend={db_backend}",
            lambda: _workouts(identity, window="180d"),
        )
        _, t_pagina_2 = _timed(
            f"[v3] dashboard/workouts offset=20    backend={db_backend}",
            lambda: _workouts(identity, window="180d", offset=20),
        )
        detalhe, t_detalhe = _timed(
            f"[v3] dashboard/workouts/{{id}}         backend={db_backend}",
            lambda: analytics_dashboard.workout_detail(
                activity_id=lista.items[0].activity_id,
                window="90d",
                date_from=None,
                date_to=None,
                sets_limit=MAX_DETAIL_SETS,
                identity=identity,
            ),
        )
        aderencia, t_aderencia = _timed(
            f"[v3] dashboard/adherence window=90d  backend={db_backend}",
            lambda: analytics_dashboard.adherence(
                window="90d", date_from=None, date_to=None, identity=identity
            ),
        )

    assert lista.items, "o dataset grande tem sessoes na janela"
    assert detalhe.items, "a sessao medida tem itens de ficha a classificar"
    assert aderencia is not None

    # A pagina inteira precisa caber no teto de 1 MB da RDS Data API (§15).
    tamanho = len(lista.model_dump_json().encode("utf-8"))
    print(f"[medicao] payload da pagina: {tamanho} bytes")
    assert tamanho < 1_000_000

    assert_no_maximo_statements(
        stmts90, teto=MAX_STATEMENTS_WORKOUTS, label="dashboard/workouts 90d", backend=db_backend
    )
    assert_ordem_de_grandeza(
        [
            ("workouts 90d", t_lista),
            ("workouts 180d", t_lista_180),
            ("workouts offset=20", t_pagina_2),
            ("workouts/{id}", t_detalhe),
            ("adherence 90d", t_aderencia),
            # As rotas v2 (o "antes" impresso acima) NAO entram na rede: elas
            # ja custavam ~13s na maquina de quem escreveu o PR, ou seja,
            # encostam na rede por natureza, e nao sao o que este PR protege.
            # Ficam como registro no relatorio.
        ],
        backend=db_backend,
    )
