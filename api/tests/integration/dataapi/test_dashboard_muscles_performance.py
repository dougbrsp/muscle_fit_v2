"""Custo de `GET /analytics/dashboard/muscles` e `/muscles/{id}` (F1.2) no
MESMO dataset de 3 anos que mediu o drill-down v2 em `#188`
(`test_dashboard_drilldown_performance.py`): 400 sessoes, 4.000 exercicios,
16.000 series.

Por que existe: a tela v2 estourou o timeout de 29s do API Gateway em
producao porque `VolumeService.get_muscle_group_overview` le o historico
COMPLETO do usuario a cada chamada. O `#188` derrubou o drill-down para
13,4s no shim da Data API; o requisito deste PR e **<= 5s** para as rotas
novas, e a unica forma honesta de afirmar isso e medindo no mesmo lugar.

O dataset e o gerador sao importados do arquivo do `#188` - um unico seed,
uma unica definicao de "dataset grande".

    cd api && python -m pytest -s -m slow \\
        tests/integration/dataapi/test_dashboard_muscles_performance.py
"""

from __future__ import annotations

import json
import time

import pytest

from app import analytics_dashboard
from app.security import Identity
from tests.support.dataset_medicao import criar_modelo_por_equipamento, remover_modelo
from tests.support.medicao import assert_no_maximo_statements, assert_ordem_de_grandeza
from tests.support.sql_capture import recorded_sql

from .test_dashboard_drilldown_performance import (  # noqa: F401 - fixtures
    big_dataset,
    frozen_clock,
)

pytestmark = pytest.mark.slow

# O teto de 5,0s que estava aqui era o aceite da F1.2, medido numa maquina
# especifica; como criterio de CI so produzia flake (ver
# tests/support/medicao.py). O relogio virou rede de ordem de grandeza e a
# protecao de verdade passou a ser o teto DETERMINISTICO de statements: a
# regressao que estes endpoints sofrem e N+1 por grupo muscular, e um N+1
# multiplica a contagem em qualquer maquina.
#
# Medido no dataset de 3 anos: 11 statements, IDENTICO nos dois backends. O
# teto de 20 quase dobra isso (cabe consulta nova legitima), mas NAO cabe uma
# passada por grupo: a janela tem 12 grupos, entao o N+1 mais barato possivel
# levaria a >= 23.
MAX_STATEMENTS_MUSCLES = 20

# M34 acrescentou o bloco POR SESSAO (`include_comparatives=true`): comparativo
# T-0..T-3, comparativo por equipamento e mapa de calor saem de UMA consulta so
# (`SESSION_EQUIPMENT_SQL`), porque os tres sao recortes da MESMA agregacao
# (grupo x sessao x equipamento). Logo o teto com o bloco e o de sem o bloco
# MAIS UM - e esse "+1" e a trava: se alguem trocar a montagem em memoria por
# uma consulta por grupo (ou por uma por sessao para o mapa de calor), a
# contagem estoura aqui antes de chegar em producao.
MAX_STATEMENTS_MUSCLES_COM_COMPARATIVOS = MAX_STATEMENTS_MUSCLES + 1


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="admin", source="session")


def _muscles(identity, *, window="28d", include_readiness=False, include_comparatives=False):
    """Chamada direta da funcao do endpoint: como nao passa pelo FastAPI, os
    defaults `Query(...)` precisam ser informados explicitamente."""
    return analytics_dashboard.muscles(
        window=window,
        date_from=None,
        date_to=None,
        training_type_id=None,
        include_readiness=include_readiness,
        include_comparatives=include_comparatives,
        identity=identity,
    )


def _drilldown(identity, muscle_group_id, *, window="90d"):
    return analytics_dashboard.muscle_drilldown(
        muscle_group_id=muscle_group_id,
        window=window,
        date_from=None,
        date_to=None,
        training_type_id=None,
        include_readiness=False,
        sets_limit=analytics_dashboard.MAX_DRILLDOWN_SETS,
        identity=identity,
    )


def _timed(label: str, fn):
    started = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - started
    print(f"[medicao] {label}: {elapsed:.3f}s")
    return result, elapsed


def test_medicao_muscles_e_drilldown(
    db_backend, big_dataset, frozen_clock, monkeypatch, fake_rds, capsys  # noqa: F811 - fixture do pytest importada (falso positivo)
):
    user_id = big_dataset["user_id"]
    identity = _identity(user_id)
    muscle_group_id = big_dataset["muscle_group_id"]

    with capsys.disabled():
        print(
            f"\n[dataset] backend={db_backend} activities={big_dataset['activities']} "
            f"exercises={big_dataset['exercises']} sets={big_dataset['sets']}"
        )

        with monkeypatch.context() as ctx:
            with recorded_sql(db_backend, ctx, fake_rds) as stmts28:
                lista, t_lista = _timed(
                    f"dashboard/muscles window=28d      backend={db_backend}",
                    lambda: _muscles(identity, window="28d"),
                )
        print(f"[statements] dashboard/muscles 28d backend={db_backend}: {len(stmts28)}")
        _, t_lista_90 = _timed(
            f"dashboard/muscles window=90d      backend={db_backend}",
            lambda: _muscles(identity, window="90d"),
        )
        # Bloco OPT-IN: medido e impresso, mas FORA do teto de 5s - o custo e
        # do modelo de recuperacao existente (dois datasets inteiros por
        # chamada), nao das consultas desta F1.2. A tela o pede numa consulta
        # separada, para a tabela pintar sem esperar por ele.
        _, t_com_readiness = _timed(
            f"dashboard/muscles COM prontidao   backend={db_backend}",
            lambda: _muscles(identity, window="90d", include_readiness=True),
        )
        detalhe, t_detalhe = _timed(
            f"dashboard/muscles/{{id}} 90d       backend={db_backend}",
            lambda: _drilldown(identity, muscle_group_id, window="90d"),
        )

    assert lista.items, "o dataset grande tem series em todos os grupos"
    assert detalhe.sessions, "o drill-down do grupo semeado tem sessoes"

    assert_no_maximo_statements(
        stmts28, teto=MAX_STATEMENTS_MUSCLES, label="dashboard/muscles 28d", backend=db_backend
    )
    assert_ordem_de_grandeza(
        [
            ("muscles 28d", t_lista),
            ("muscles 90d", t_lista_90),
            ("muscles/{id} 90d", t_detalhe),
            # O bloco OPT-IN entra na MESMA rede: o custo e do modelo de
            # recuperacao existente (dois datasets inteiros por chamada), e o
            # que se precisa travar e justamente que ele continue longe dos
            # 29s do API Gateway que derrubaram a tela v2 em producao.
            ("muscles COM prontidao", t_com_readiness),
        ],
        backend=db_backend,
    )


def test_bloco_por_sessao_custa_exatamente_um_statement(
    db_backend, big_dataset, frozen_clock, monkeypatch, fake_rds, capsys  # noqa: F811 - fixture do pytest importada (falso positivo)
):
    """M34: comparativo por sessao + por equipamento + mapa de calor saem de
    UMA consulta.

    O risco real aqui nao e latencia, e cardinalidade: o jeito "obvio" de
    escrever esses tres blocos e uma consulta por grupo (12 grupos) ou uma por
    sessao do mapa de calor (8 sessoes). Qualquer um dos dois estoura o teto.
    """
    identity = _identity(big_dataset["user_id"])
    # M58: o bloco le `equipment_exercises.load_convention`, cuja existencia e
    # detectada UMA vez por schema e processo (`load_conventions`, cache do
    # positivo). Aquece a deteccao antes de contar: o que se mede aqui e o
    # regime (o caso permanente em producao), nao a 1a chamada do container.
    _muscles(identity, window="90d", include_comparatives=True)

    with capsys.disabled():
        with monkeypatch.context() as ctx:
            with recorded_sql(db_backend, ctx, fake_rds) as sem_bloco:
                _muscles(identity, window="90d")
        with monkeypatch.context() as ctx:
            with recorded_sql(db_backend, ctx, fake_rds) as com_bloco:
                payload = _muscles(identity, window="90d", include_comparatives=True)
        print(
            f"[statements] dashboard/muscles 90d backend={db_backend}: "
            f"{len(sem_bloco)} sem bloco, {len(com_bloco)} com bloco"
        )

    assert_no_maximo_statements(
        com_bloco,
        teto=MAX_STATEMENTS_MUSCLES_COM_COMPARATIVOS,
        label="dashboard/muscles 90d + comparativos",
        backend=db_backend,
    )
    assert len(com_bloco) == len(sem_bloco) + 1, (
        "o bloco por sessao tem que custar exatamente UMA consulta a mais; "
        f"custou {len(com_bloco) - len(sem_bloco)}"
    )

    bloco = payload.comparatives
    assert bloco is not None
    # A unidade e contrato: um bloco por JANELA entrando aqui por engano seria
    # invisivel para quem le a tela.
    assert bloco.unit == "session"
    assert bloco.by_session, "o dataset grande tem sessoes em todos os grupos"
    assert bloco.heatmap, "o mapa de calor cobre as sessoes mais recentes"
    for grupo in bloco.by_session:
        assert len(grupo.points) <= 4
        assert [p.offset for p in grupo.points] == list(range(len(grupo.points)))
    for sessao in bloco.heatmap:
        # Cor nunca e o unico sinal: toda celula chega com faixa NOMEADA.
        assert all(cell.band for cell in sessao.cells)
        assert all((cell.share_of_max or 0) <= 1.0 for cell in sessao.cells)


def test_bloco_por_sessao_nao_vem_quando_nao_e_pedido(db_backend, big_dataset, frozen_clock):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """`None` (nao lista vazia): "nao foi pedido" e diferente de "nao ha"."""
    identity = _identity(big_dataset["user_id"])
    assert _muscles(identity, window="28d").comparatives is None


def test_payload_do_drilldown_cabe_no_limite_da_data_api(db_backend, big_dataset, frozen_clock):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """A RDS Data API recusa RESULTADO acima de 1 MB e o API Gateway tem
    limite de resposta: o drill-down corta pelas series mais recentes e
    DECLARA o corte."""
    identity = _identity(big_dataset["user_id"])
    payload = _drilldown(identity, big_dataset["muscle_group_id"], window="180d")

    serialized = json.dumps(payload.model_dump(mode="json"))
    assert len(serialized.encode("utf-8")) < 1_000_000, (
        f"payload de {len(serialized)} bytes - o teto de 1 MB da Data API existe "
        "justamente para nao voltar ao UnsupportedResultException de producao."
    )
    total_sets = sum(len(exposure.sets) for s in payload.sessions for exposure in s.exposures)
    assert total_sets <= payload.sets_limit
    if payload.sets_truncated:
        assert any("mais antigas" in limitation for limitation in payload.limitations)


def test_status_classifica_so_o_realizado(db_backend, big_dataset, frozen_clock):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Regressao do defeito que este PR corrige: a tela v2 classificava
    `realizado + projetado`. Aqui `status` tem que bater com o que
    `classify_weekly_volume_status` devolve para o realizado SOZINHO."""
    from app.intelligence.volume_service import classify_weekly_volume_status

    identity = _identity(big_dataset["user_id"])
    payload = _muscles(identity, window="28d")

    for item in payload.items:
        esperado = classify_weekly_volume_status(
            item.realized.weekly_effective_sets.value or 0.0,
            item.targets.min.value or 0.0,
            item.targets.target.value or 0.0,
            item.targets.max.value or 0.0,
        )
        assert item.status == esperado, item.muscle_group
        assert item.status_basis == "realized_only"


# ---------------------------------------------------------------------------
# Meta vinda do PROPRIO Modelo por equipamento (`target_source = template_plan`)
# ---------------------------------------------------------------------------

# Modelo realista: 4 slots de treino x 6 equipamentos, 4 series cada. A
# construcao mora em `tests/support/dataset_medicao.py` desde o M30, porque o
# dataset PEQUENO precisa do MESMO Modelo para afirmar, em todo PR, que a meta
# e resolvida uma unica vez por requisicao.


@pytest.fixture
def modelo_por_equipamento(raw, big_dataset):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Modelo `equipment_series` atribuido ao atleta do dataset grande, com
    itens apontando para o catalogo semeado. Removido ao fim (o dataset
    sobrevive ao teste; os outros testes medem SEM Modelo, como antes)."""
    template_id = criar_modelo_por_equipamento(raw, user_id=big_dataset["user_id"])
    yield template_id
    remover_modelo(raw, template_id)


def test_medicao_muscles_com_meta_do_modelo_por_equipamento(
    db_backend, big_dataset, frozen_clock, modelo_por_equipamento, capsys  # noqa: F811 - fixture do pytest importada (falso positivo)
):
    """As duas consultas a mais (cabecalho do Modelo + ativacao agregada por
    grupo) tem que caber no MESMO teto da rota; e a meta tem que vir do
    Modelo, nao do fallback generico."""
    identity = _identity(big_dataset["user_id"])

    with capsys.disabled():
        lista, t_28 = _timed(
            f"dashboard/muscles 28d COM Modelo por equipamento backend={db_backend}",
            lambda: _muscles(identity, window="28d"),
        )
        _, t_90 = _timed(
            f"dashboard/muscles 90d COM Modelo por equipamento backend={db_backend}",
            lambda: _muscles(identity, window="90d"),
        )
        detalhe, t_detalhe = _timed(
            f"dashboard/muscles/{{id}} 90d COM Modelo por equipamento backend={db_backend}",
            lambda: _drilldown(identity, big_dataset["muscle_group_id"], window="90d"),
        )

    assert lista.target_source.value == "template_plan"
    assert lista.template_id == modelo_por_equipamento
    com_meta = [item for item in lista.items if (item.targets.target.value or 0.0) > 0.0]
    assert com_meta, "o Modelo trabalha grupos do dataset"
    for item in lista.items:
        assert item.targets.source.value == "template_plan"
        assert item.targets.min.value is None and item.targets.max.value is None
        assert item.status in ("BELOW_TARGET", "ON_TARGET", "ABOVE_TARGET"), item.muscle_group
    assert detalhe.target_source.value == "template_plan"

    assert_ordem_de_grandeza(
        [
            ("muscles 28d com Modelo", t_28),
            ("muscles 90d com Modelo", t_90),
            ("muscles/{id} 90d com Modelo", t_detalhe),
        ],
        backend=db_backend,
    )
