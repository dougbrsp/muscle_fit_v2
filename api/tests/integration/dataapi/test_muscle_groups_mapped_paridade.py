"""Classificacao CANONICA de grupos musculares (`app/muscle_groups_mapped.py`)
no caminho de PRODUCAO (shim RDS Data API + dublê) e paridade com PyMySQL.

Cobre o agregado usado pelas telas Grupos musculares, Dashboard (tabela por
grupo: series, volume, RPE medio, cobertura/nao mapeados) e Insights, que
deixaram de usar as rotas de regex no nome do exercicio.

Limite de 1 MiB do resultado da Data API: `load_rows` agrega no SQL por
(dia, exercicio, grupos) e le a janela em blocos de `LOAD_ROWS_CHUNK_DAYS`.
Com volume alto, a MESMA consulta em janela unica estoura so no Data API.
"""

from __future__ import annotations

import pytest

# Importado no topo: `use_dataapi` so troca o backend de modulos `app.*` ja
# importados (ver tests/support/dataapi_backend.py).
from app import muscle_groups_mapped as mgm
from app.security import Identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.muscle_groups_mapped import (
    expected_summary,
    seed_canonical_history,
    seed_heavy_history,
    table_summary,
)


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="regular", source="session")


def _run_in_both(monkeypatch, fake_rds, mysql_schema, fn):
    results = {}
    for backend in BACKENDS:  # pymysql primeiro; data-api troca ate o teardown
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        results[backend] = fn()
    assert fake_rds.calls_of("ExecuteStatement"), "o caminho data-api nao passou pelo dublê"
    return results


def _size_errors(fake_rds):
    return [call for call in fake_rds.calls if call.error_code == "UnsupportedResultException"]


@pytest.fixture
def history(raw):
    return seed_canonical_history(raw)


@pytest.mark.parametrize("include_secondary", [False, True], ids=["so-primario", "com-secundario"])
@pytest.mark.parametrize("days", [30, 90])
def test_aggregate_identico_e_correto_nos_dois_backends(history, monkeypatch, fake_rds, mysql_schema, days, include_secondary):
    results = _run_in_both(
        monkeypatch,
        fake_rds,
        mysql_schema,
        lambda: mgm.aggregate("weekly", "volume", days, include_secondary, user_id=history["user"]),
    )
    # Tipo + valor, sem normalizar (inclui trend_rows, meta e nao mapeados).
    assert results["data-api"] == results["pymysql"]
    assert table_summary(results["data-api"]) == expected_summary(days, include_secondary)


def test_rota_usada_pelo_dashboard_identica_nos_dois_backends(history, monkeypatch, fake_rds, mysql_schema):
    """Mesma chamada do Dashboard/Insights (`mappedTablePath`)."""
    results = _run_in_both(
        monkeypatch,
        fake_rds,
        mysql_schema,
        lambda: mgm.table(
            period="yearly",
            metric="volume",
            days=30,
            include_secondary=False,
            identity=_identity(history["user"]),
        ),
    )
    assert results["data-api"] == results["pymysql"]
    payload = results["data-api"]
    assert table_summary(payload) == expected_summary(30, False)
    for row in payload["table_rows"]:
        assert isinstance(row["volume_kg"], float) and isinstance(row["avg_rpe"], float), row
        assert isinstance(row["sets"], int) and isinstance(row["rpe_sets"], int), row
    assert _size_errors(fake_rds) == []


# "Outros": exercicio sem contribuicao cadastrada (e o sem vinculo) - mesma
# politica de `VolumeService` (M76 Lote 3c).
@pytest.mark.parametrize(
    "metric,expected",
    [
        ("sets", {"Peitoral": 3.0, "Costas": 4.0, "Outros": 3.0}),
        ("reps", {"Peitoral": 27.0, "Costas": 40.0, "Outros": 30.0}),
        ("load", {"Peitoral": 180.0, "Costas": 118.0, "Outros": 24.0}),
    ],
)
def test_metricas_identicas_nos_dois_backends(history, monkeypatch, fake_rds, mysql_schema, metric, expected):
    results = _run_in_both(
        monkeypatch,
        fake_rds,
        mysql_schema,
        lambda: mgm.aggregate("monthly", metric, 30, False, user_id=history["user"]),
    )
    assert results["data-api"] == results["pymysql"]
    assert {row["group"]: row["value"] for row in results["data-api"]["table_rows"]} == expected


def test_health_identico_nos_dois_backends(history, monkeypatch, fake_rds, mysql_schema):
    results = _run_in_both(
        monkeypatch, fake_rds, mysql_schema, lambda: mgm.health(days=30, identity=_identity(history["user"]))
    )
    assert results["data-api"] == results["pymysql"]
    assert results["data-api"]["set_count"] == 10
    assert results["data-api"]["groups"] == ["Costas", "Peitoral"]


# -- limite de 1 MiB -----------------------------------------------------------


@pytest.fixture
def heavy_history(raw):
    return seed_heavy_history(raw)


def test_janela_unica_estouraria_1_mib_so_no_data_api(heavy_history, monkeypatch, fake_rds, mysql_schema):
    """Prova que o limite e real para esta consulta: 1 ano de volume alto em
    UMA consulta passa no PyMySQL (dev) e falha no Data API (producao)."""
    monkeypatch.setattr(mgm, "LOAD_ROWS_CHUNK_DAYS", 100_000)
    user_id = heavy_history["user"]

    ok = mgm.aggregate("yearly", "volume", 365, False, user_id=user_id)
    assert ok["total_sets"] == heavy_history["sets"]

    use_backend("data-api", monkeypatch, fake_rds, mysql_schema)
    with pytest.raises(Exception) as exc_info:
        mgm.aggregate("yearly", "volume", 365, False, user_id=user_id)
    chain, current = [], exc_info.value
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    assert any("UnsupportedResultException" in f"{type(item).__name__} {item}" for item in chain), chain
    assert _size_errors(fake_rds)


def test_blocos_de_dias_cabem_no_limite_e_sao_identicos(heavy_history, monkeypatch, fake_rds, mysql_schema):
    user_id = heavy_history["user"]
    results = _run_in_both(
        monkeypatch,
        fake_rds,
        mysql_schema,
        lambda: mgm.aggregate("weekly", "volume", 365, True, user_id=user_id),
    )
    assert _size_errors(fake_rds) == []
    assert results["data-api"] == results["pymysql"]
    assert results["data-api"]["total_sets"] == heavy_history["sets"]
    assert results["data-api"]["mapped_ratio"] == 1.0
