"""Dataset GRANDE (3 anos de treino) para medir e travar o custo de
`GET /analytics/dashboard-muscle-drilldown` - o endpoint que estourou o
timeout de 29s do API Gateway em producao (`{"message": "Endpoint request
timed out"}` mesmo com `days=7`), enquanto no DEV local (49 activities,
1.228 series) respondia em menos de 1s.

O que este arquivo cobre:

1. `test_payload_nao_muda_*`: EQUIVALENCIA. A correcao e de desempenho,
   entao o payload nao pode mudar. Prova em processo, com o relogio
   congelado:
   - `get_recovery_overview(overview_rows=...)` (caminho novo do drill-down,
     sem recomputar o overview) == `get_recovery_overview()` (caminho antigo);
   - `_fetch_window_sets`/`_fetch_full_history_sets` (queries novas, sem as
     subconsultas correlacionadas por SERIE) == as queries ANTIGAS, copiadas
     literalmente em `tests/support/legacy_volume_queries.py`;
   - o cache de instancia devolve exatamente o mesmo overview da 1a chamada.

1b. M20 (leitura agregada da recuperacao): `test_payload_nao_muda_com_
   leitura_agregada` prova, campo a campo, que `get_recovery_overview` com a
   leitura empacotada (3 statements) devolve EXATAMENTE o mesmo payload da
   leitura serie a serie (~16 statements) - mesma dose, mesma prontidao,
   mesmo `activity_dose_history`.

2. `test_medicao_*`: MEDICAO. Imprime o tempo de cada endpoint no dataset
   grande (rode com `-s` para ver). Nao afirma nada sobre milissegundos
   absolutos (a maquina de CI varia); o teto e generoso e serve so de rede
   de seguranca contra regressao de ordem de grandeza.

Roda apenas sob demanda (marcador `slow`, desmarcado por padrao em
`pytest.ini`):

    cd api && python -m pytest -s -m slow \\
        tests/integration/dataapi/test_dashboard_drilldown_performance.py

Os dois backends (PyMySQL e o shim da RDS Data API) rodam via a fixture
`db_backend` de `tests/integration/dataapi/conftest.py`.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from typing import Any

import pytest

from app import analytics
from app.intelligence.dashboard.time_windows import trained_at_sql, utc_offset_minutes
from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.intelligence.volume_service import VolumeService
from app.security import Identity
from app.set_types import is_preparatory_set
from app.timezones import DEFAULT_TIMEZONE
from tests.support import legacy_volume_queries
from tests.support.isolamento_v3 import congelar_relogio
from tests.support.dataapi_backend import raw_mysql
from tests.support.dataset_medicao import FROZEN_NOW, GRANDE, garantir
from tests.support.payload_diff import diferencas
from tests.support.medicao import (
    assert_no_maximo_statements,
    assert_ordem_de_grandeza,
    assert_reduziu_statements,
)
from tests.support.sql_capture import recorded_sql

pytestmark = pytest.mark.slow

# Teto DETERMINISTICO de idas ao banco da leitura agregada (M20). Medido no
# dataset de 3 anos, IDENTICO nos dois backends: 4 statements na agregada
# (referencia + historico empacotado + series da janela + o TSB global) contra
# 18 da leitura serie a serie. A folga cobre uma consulta nova legitima; um
# N+1 por grupo/serie nao cabe.
MAX_STATEMENTS_RECOVERY_OVERVIEW = 8

_REAL_DATETIME = _dt.datetime

# Dimensoes e seed do dataset moraram aqui ate o M30. Agora vivem em
# `tests/support/dataset_medicao.py` (spec `GRANDE`), lado a lado com a spec
# `PEQUENO` que sustenta as MESMAS afirmacoes de contagem em todo PR: uma
# definicao so de "dataset de medicao", em duas dimensoes.


@pytest.fixture
def frozen_clock(monkeypatch):
    """Sem relogio congelado, `hours_since_trained`/`dose_last_72h` mudam
    entre duas chamadas e a comparacao de payload viraria ruido."""
    # Tambem congela `time_windows.user_now`: o `VolumeService` mede o tempo
    # na hora LOCAL do usuario desde a correcao do relogio UTC.
    congelar_relogio(monkeypatch, FROZEN_NOW)


@pytest.fixture
def big_dataset(mysql_schema) -> dict[str, Any]:
    """Dataset GRANDE, semeado sob demanda e reaproveitado entre testes.

    Nao e mais de escopo de sessao: `garantir` confere um sentinela no banco e
    so ressemeia quando o dataset nao esta la. E o que deixa o dataset PEQUENO
    (M30, caminho critico do CI) e este conviverem na MESMA execucao - o seed
    de um trunca as tabelas do outro, e escopo de sessao devolveria um dataset
    ja apagado."""
    with raw_mysql(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=mysql_schema,
    ) as conn:
        return garantir(conn, GRANDE)


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="admin", source="session")


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _timed(label: str, fn):
    started = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - started
    print(f"[medicao] {label}: {elapsed:.3f}s")
    return result, elapsed


# ---------------------------------------------------------------------------
# 1) Equivalencia: o payload nao pode mudar
# ---------------------------------------------------------------------------


def test_payload_nao_muda_recovery_overview_com_rows_prontas(db_backend, big_dataset, frozen_clock):
    """`get_recovery_overview(overview_rows=...)` (caminho novo do drill-down)
    devolve EXATAMENTE o mesmo que recomputar o overview por dentro."""
    user_id = big_dataset["user_id"]
    antes = MuscleRecoveryService().get_recovery_overview(days=28, limit=100, user_id=user_id)

    rows = VolumeService().get_muscle_group_overview(days=28, limit=100, user_id=user_id)
    depois = MuscleRecoveryService().get_recovery_overview(
        days=28, limit=100, user_id=user_id, overview_rows=rows
    )
    assert _canonical(depois) == _canonical(antes)


# Fase 5 (PR 4): as leituras serie a serie deixaram de trazer aquecimento/
# ajuste e ganharam as colunas da tecnica (`technique_code`,
# `anchor_set_number`) e a unidade de serie equivalente (`set_units`). O
# oraculo continua sendo a query ANTIGA: a comparacao tira dele as series
# preparatorias e da leitura nova as colunas da Fase 5 - o resto tem de ser
# identico, linha a linha e na mesma ordem.
#
# `equipment_id`/`load_convention` (historico completo): chave de
# comparabilidade do sinal "Desempenho recente" (`exercise_performance`),
# aditivas - a query antiga nao as tinha. `training_goal`/`deload_source`
# (M97 F2/D/F4): objetivo da execucao e origem do deload, tambem aditivas.
_COLUNAS_FASE5 = (
    "technique_code",
    "anchor_set_number",
    "set_units",
    "equipment_id",
    "load_convention",
    "training_goal",
    "deload_source",
)


def _sem_colunas_fase5(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in row.items() if key not in _COLUNAS_FASE5} for row in rows]


def _sem_preparatorias(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not is_preparatory_set(row)]


def test_payload_nao_muda_window_sets_vs_query_legada(db_backend, big_dataset):
    """Query nova de `_fetch_window_sets` (sem subconsulta correlacionada por
    serie) == query ANTIGA, linha a linha (sem as preparatorias - Fase 5)."""
    service = VolumeService()
    user_id = big_dataset["user_id"]
    nova = service._fetch_window_sets(90, user_id=user_id)
    legada = legacy_volume_queries.fetch_window_sets_legacy(service, 90, user_id=user_id)
    assert _sem_colunas_fase5(nova) == _sem_preparatorias(legada)
    assert len(_sem_preparatorias(legada)) < len(legada), "o dataset tem aquecimento a excluir"
    assert all(row["set_units"] == 1.0 for row in nova), "dataset sem tecnica: toda serie vale 1"
    assert nova, "cenario vazio nao provaria nada"


def test_payload_nao_muda_full_history_vs_query_legada(big_dataset):
    """So no PyMySQL, de proposito: a query ANTIGA do historico completo NAO
    roda neste dataset no backend de producao - a RDS Data API recusa a
    resposta com `UnsupportedResultException` ("The result exceeds the size
    limit 1 MB"), porque ela repetia em cada uma das 16.000 series as colunas
    que sao constantes por atividade/exercicio. E o mesmo teto documentado em
    `app/db_dataapi.py`. A leitura nova, paginada e sem as colunas
    repetidas, passa nos dois backends (ver os demais testes deste arquivo).
    O SQL comparado aqui e identico nos dois backends, entao provar a
    equivalencia no PyMySQL basta."""
    service = VolumeService()
    user_id = big_dataset["user_id"]
    nova = service._fetch_full_history_sets(user_id=user_id)
    legada = legacy_volume_queries.fetch_full_history_sets_legacy(service, user_id=user_id)
    assert _sem_colunas_fase5(nova) == _sem_preparatorias(legada)
    assert nova, "cenario vazio nao provaria nada"


def test_cache_de_instancia_nao_muda_o_overview(db_backend, big_dataset, frozen_clock):
    """Segunda chamada no MESMO `VolumeService` (que agora reaproveita as
    linhas ja buscadas) devolve o mesmo overview da primeira - e uma
    instancia nova, sem cache, chega no mesmo resultado."""
    user_id = big_dataset["user_id"]
    service = VolumeService()
    primeira = service.get_muscle_group_overview(days=28, limit=100, user_id=user_id)
    segunda = service.get_muscle_group_overview(days=28, limit=100, user_id=user_id)
    assert _canonical(segunda) == _canonical(primeira)

    terceira = VolumeService().get_muscle_group_overview(days=28, limit=100, user_id=user_id)
    assert _canonical(terceira) == _canonical(primeira)


def test_limit_menor_devolve_prefixo_do_limit_maior(db_backend, big_dataset, frozen_clock):
    """O cache guarda a lista COMPLETA ordenada e fatia por `limit` - o que so
    e equivalente porque `limit` sempre foi um truncamento do mesmo
    ranking."""
    user_id = big_dataset["user_id"]
    service = VolumeService()
    completo = service.get_muscle_group_overview(days=28, limit=100, user_id=user_id)
    curto = service.get_muscle_group_overview(days=28, limit=3, user_id=user_id)
    assert _canonical(curto) == _canonical(completo[:3])


# ---------------------------------------------------------------------------
# 2) Medicao no dataset grande
# ---------------------------------------------------------------------------


def test_medicao_drilldown_e_endpoints_irmaos(db_backend, big_dataset, frozen_clock, capsys):
    user_id = big_dataset["user_id"]
    identity = _identity(user_id)
    muscle_group_id = big_dataset["muscle_group_id"]

    with capsys.disabled():
        print(
            f"\n[dataset] backend={db_backend} activities={big_dataset['activities']} "
            f"exercises={big_dataset['exercises']} sets={big_dataset['sets']}"
        )
        payload, drilldown_s = _timed(
            f"dashboard-muscle-drilldown days=7  backend={db_backend}",
            lambda: analytics.dashboard_muscle_drilldown(
                muscle_group_id=muscle_group_id, days=7, identity=identity
            ),
        )
        _timed(
            f"dashboard-muscle-drilldown days=28 backend={db_backend}",
            lambda: analytics.dashboard_muscle_drilldown(
                muscle_group_id=muscle_group_id, days=28, identity=identity
            ),
        )
        _timed(
            f"dashboard-muscle-status   days=14 backend={db_backend}",
            lambda: analytics.dashboard_muscle_status(days=14, training_type_id=None, identity=identity),
        )
        _timed(
            f"dashboard-recovery        days=14 backend={db_backend}",
            lambda: analytics.dashboard_recovery(days=14, identity=identity),
        )

    assert payload["overview"] is not None
    assert payload["readiness"] is not None
    # Este teste ja nascia com a forma CERTA: rede de ordem de grandeza, nao
    # cronometro. So passou a usar o helper comum, para a mensagem de falha ser
    # a mesma em toda a suite e o numero da rede morar num lugar so.
    assert_ordem_de_grandeza([("dashboard-muscle-drilldown days=7", drilldown_s)], backend=db_backend)


def test_medicao_partes_do_drilldown(db_backend, big_dataset, frozen_clock, capsys):
    """Quebra o custo do drill-down por chamada, para saber ONDE o tempo
    esta - e nao otimizar no escuro."""
    from app import services
    from app.intelligence.volume_service import VolumeService as _VS

    user_id = big_dataset["user_id"]
    muscle_group_id = big_dataset["muscle_group_id"]
    with capsys.disabled():
        print(f"\n[partes] backend={db_backend}")
        service = _VS()
        rows, _ = _timed("  get_muscle_group_overview(days=7)", lambda: service.get_muscle_group_overview(days=7, limit=100, user_id=user_id))
        _timed("  get_recovery_overview(rows prontas)", lambda: MuscleRecoveryService().get_recovery_overview(days=7, limit=100, user_id=user_id, overview_rows=rows))
        top, _ = _timed("  get_top_exercises_for_muscle_group", lambda: service.get_top_exercises_for_muscle_group(muscle_group_id=muscle_group_id, days=7, user_id=user_id, limit=5))
        if top and top[0].get("mapping_id"):
            _timed("  suggested_progression_for_mapping", lambda: services.suggested_progression_for_mapping(mapping_id=top[0]["mapping_id"], user_id=user_id))
        _timed("  get_rolling_weekly_trend_by_muscle_group", lambda: service.get_rolling_weekly_trend_by_muscle_group(days=7, user_id=user_id))


def test_explain_das_consultas_mais_caras(big_dataset, capsys):
    """`EXPLAIN` das consultas de serie - a antiga e as novas - no dataset
    grande. Roda so no PyMySQL (a query antiga nem completa na Data API, ver
    o teste de equivalencia acima) e serve de evidencia para o PR: e daqui
    que sai a resposta sobre precisar ou nao de indice novo."""
    service = VolumeService()
    user_id = big_dataset["user_id"]
    historico = service._fetch_full_history_sets(user_id=user_id)
    catalog_ids = sorted({int(row["mapping_id"]) for row in historico if row.get("mapping_id")})
    activity_ids = sorted({int(row["activity_id"]) for row in historico})[:100]

    def _in(values):
        return ",".join(["%s"] * len(values))

    consultas = [
        (
            "ANTIGA - series do historico completo (subconsultas correlacionadas por serie)",
            legacy_volume_queries.FULL_HISTORY_SETS_SQL.format(user_filter="WHERE a.user_id = %s"),
            (user_id,),
        ),
        (
            "NOVA 1 - atividades do usuario",
            "SELECT a.id AS id FROM activities a WHERE a.user_id = %s ORDER BY a.id ASC",
            (user_id,),
        ),
        (
            "NOVA 2 - exercicios de uma pagina de atividades",
            "SELECT "
            + VolumeService._FULL_HISTORY_EXERCISE_SELECT.format(
                trained_at=trained_at_sql("a", offset_minutes=utc_offset_minutes(DEFAULT_TIMEZONE))
            )
            + VolumeService._EXERCISE_SCOPE_FROM
            + " WHERE a.id IN (" + _in(activity_ids) + ")",
            tuple(activity_ids),
        ),
        (
            "NOVA 3 - series de uma pagina de atividades",
            "SELECT es.id AS set_id, es.exercise_id, es.set_number, es.reps, es.weight_kg, es.rpe, es.set_type "
            "FROM exercises e JOIN exercise_sets es ON es.exercise_id = e.id "
            "WHERE e.activity_id IN (" + _in(activity_ids) + ")",
            tuple(activity_ids),
        ),
        (
            "NOVA 4 - atributos de grupo por exercicio do catalogo",
            "SELECT emc.exercise_id, emc.role, emc.stimulus_weight, mg.id, mg.name "
            "FROM exercise_muscle_contributions emc LEFT JOIN muscle_groups mg ON mg.id = emc.muscle_group_id "
            "WHERE emc.exercise_id IN (" + _in(catalog_ids) + ") AND emc.is_active = 1 "
            "AND emc.role IN ('PRIMARY','SECONDARY') ORDER BY emc.id ASC",
            tuple(catalog_ids),
        ),
    ]

    with capsys.disabled():
        print("\n[EXPLAIN] dataset: 400 atividades / 4.000 exercicios / 16.000 series")
        for titulo, sql, params in consultas:
            print("\n--- " + titulo)
            for row in service.fetch_all("EXPLAIN " + sql, params):
                print(
                    f"    type={row.get('type')} table={row.get('table')} key={row.get('key')} "
                    f"rows={row.get('rows')} extra={row.get('Extra')}"
                )


# ---------------------------------------------------------------------------
# 3) M20 - leitura agregada da recuperacao: equivalencia e medicao
# ---------------------------------------------------------------------------


# `_diferencas` mora em `tests/support/payload_diff.py` desde o M30: as mesmas
# comparacoes campo a campo passaram a rodar tambem no dataset pequeno, em todo
# PR. O nome local continua valendo para quem ja o importava daqui.
_diferencas = diferencas


def test_payload_nao_muda_com_leitura_agregada(db_backend, big_dataset, frozen_clock):
    """M20: `get_recovery_overview` com a leitura AGREGADA (3 statements)
    devolve o MESMO payload da leitura antiga (~16 statements) - incluindo
    `explanation[]`, `factors`, `recommendation`, `blocking_signals` e
    `activity_dose_history`."""
    user_id = big_dataset["user_id"]
    antes = MuscleRecoveryService().get_recovery_overview(
        days=14, limit=100, user_id=user_id, aggregated_read=False
    )
    depois = MuscleRecoveryService().get_recovery_overview(
        days=14, limit=100, user_id=user_id, aggregated_read=True
    )

    assert antes, "cenario vazio nao provaria nada"
    problemas = _diferencas(antes, depois)
    assert not problemas, f"{len(problemas)} divergencias:\n" + "\n".join(problemas[:40])

    # O payload EXIBIDO (ja arredondado) tem de ser identico ate o ultimo
    # digito - a tolerancia acima so cobre os numeros crus intermediarios.
    campos = (
        "recovery_pct",
        "estimated_recovery_hours",
        "remaining_hours",
        "session_dose",
        "dose_last_72h",
    )
    for item_antes, item_depois in zip(antes, depois):
        for campo in campos:
            assert item_antes[campo] == item_depois[campo], campo
        assert item_antes["explanation"] == item_depois["explanation"]
        assert _canonical(item_antes["activity_dose_history"]) == _canonical(
            item_depois["activity_dose_history"]
        )


def test_leitura_agregada_respeita_ownership(db_backend, big_dataset, frozen_clock):
    """A leitura agregada EXIGE identidade e nunca ve dado de outro usuario."""
    from app.intelligence import recovery_read_model

    user_id = big_dataset["user_id"]
    with pytest.raises(ValueError):
        recovery_read_model.fetch_overview_datasets(VolumeService(), 14, None)

    datasets = recovery_read_model.fetch_overview_datasets(VolumeService(), 14, user_id + 9999)
    assert datasets["history_items"] == []
    assert datasets["window_sets"] == []


def test_medicao_statements_da_leitura_agregada(db_backend, big_dataset, frozen_clock, capsys):
    """Custo de CADA statement da leitura agregada - e de um `SELECT 1` para
    isolar o custo fixo por ida ao banco no shim da Data API."""
    from app.intelligence import recovery_read_model as rrm

    user_id = big_dataset["user_id"]
    service = VolumeService()
    with capsys.disabled():
        print(f"\n[statements] backend={db_backend}")
        _timed("  SELECT 1 (custo fixo por statement)", lambda: service.fetch_all("SELECT 1 AS um"))
        _timed(
            "  referencia (contribuicoes + grupos + coeficientes)",
            lambda: service.fetch_all(rrm.REFERENCE_DATA_SQL, (user_id, user_id)),
        )
        historico, _ = _timed(
            "  historico empacotado (uma linha por sessao)",
            lambda: service.fetch_all(
                rrm.HISTORY_PACKED_SQL.format(history_filter="", trained_at=service._trained_at_sql(user_id)),
                (user_id,),
            ),
        )
        janela, _ = _timed(
            "  series da janela (14d)",
            lambda: service.fetch_all(
                rrm.WINDOW_SETS_SQL.format(trained_at=service._trained_at_sql(user_id)),
                (user_id, service._days_ago(user_id, 14)),
            ),
        )
        print(f"  linhas: historico={len(historico)} janela={len(janela)}")


def test_medicao_recovery_overview_antes_e_depois(
    db_backend, big_dataset, frozen_clock, monkeypatch, fake_rds, capsys
):
    """M20 no dataset de 3 anos: leitura serie a serie x leitura agregada.

    O que este teste existe para travar e que a leitura agregada continue
    AGREGANDO - isto e, que custe MENOS IDAS AO BANCO que a antiga. Isso e
    afirmado pela CONTAGEM DE STATEMENTS, que e deterministica.

    O `assert depois_s < antes_s` que existia aqui comparava duas leituras de
    relogio consecutivas numa maquina compartilhada, sem folga nenhuma. Ele
    reprovou DOIS PRs no mesmo dia (2026-09-25), nenhum dos dois tocando o
    codigo medido, e os dois passaram no rerun do MESMO commit:

        #213 (so frontend)            assert 1.0939 < 0.6520
        #217 (so Terraform/compose)   assert 1.4341 < 0.5554

    Olhe a escala: o "depois" mediu 1,09s e 1,43s em ocasioes diferentes e o
    "antes" 0,65s e 0,56s - a variacao ENTRE EXECUCOES e maior que a diferenca
    que o teste tentava provar. Medicao sem poder discriminatorio.

    A contagem de statements tem o poder que o relogio nao tinha: 18 -> 4,
    identico nos dois backends e em qualquer maquina. O tempo continua sendo
    medido e impresso - e o numero que vai para a descricao do PR - mas quem
    reprova e a contagem, mais a rede de ordem de grandeza.
    """
    user_id = big_dataset["user_id"]

    def _chamar(*, agregada: bool):
        return MuscleRecoveryService().get_recovery_overview(
            days=14, limit=100, user_id=user_id, aggregated_read=agregada
        )

    with capsys.disabled():
        print(f"\n[M20] backend={db_backend}")
        with monkeypatch.context() as ctx:
            with recorded_sql(db_backend, ctx, fake_rds) as stmts_antes:
                _, antes_s = _timed(
                    f"  get_recovery_overview ANTES (serie a serie) backend={db_backend}",
                    lambda: _chamar(agregada=False),
                )
        with monkeypatch.context() as ctx:
            with recorded_sql(db_backend, ctx, fake_rds) as stmts_depois:
                _, depois_s = _timed(
                    f"  get_recovery_overview DEPOIS (agregada)     backend={db_backend}",
                    lambda: _chamar(agregada=True),
                )
        print(
            f"[M20] statements: {len(stmts_antes)} -> {len(stmts_depois)} | "
            f"tempo: {antes_s:.3f}s -> {depois_s:.3f}s "
            f"({antes_s - depois_s:+.3f}s, {antes_s / max(depois_s, 1e-6):.1f}x)"
        )

    assert_reduziu_statements(
        stmts_antes,
        stmts_depois,
        label="get_recovery_overview (leitura agregada do M20)",
        backend=db_backend,
    )
    assert_no_maximo_statements(
        stmts_depois,
        teto=MAX_STATEMENTS_RECOVERY_OVERVIEW,
        label="get_recovery_overview (leitura agregada do M20)",
        backend=db_backend,
    )
    assert_ordem_de_grandeza([("get_recovery_overview agregada", depois_s)], backend=db_backend)
