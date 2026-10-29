"""O replay das trilhas como OPERACAO executavel pelo pipeline
(`app/replay_progression.py` + acao `replay-progression` de
`app/migrate.py`), nos DOIS backends: PyMySQL (DEV) e o caminho de PRODUCAO
(shim `app/db_dataapi.py` + dublê da RDS Data API).

O que esta coberto aqui, e por que cada coisa:

- **`dry_run` nao escreve NADA.** E o modo em que o operador confere o plano
  antes de gravar em producao; se ele escrevesse, a conferencia seria o
  proprio risco.
- **Idempotencia.** Rodar duas vezes seguidas devolve o MESMO `fingerprint`,
  as MESMAS contagens e nao acumula linha nenhuma nas tres tabelas.
- **Leitura paginada == leitura unica.** A paginacao existe porque a resposta
  da RDS Data API tem teto de 1 MB e a query do historico devolve uma linha
  por serie. Se a paginacao mudasse a ordem das sessoes, o replay daria
  resultado diferente em producao e em DEV - exatamente a divergencia que nao
  pode existir. Aqui as duas formas sao comparadas sessao a sessao, e o teto
  de 1 MB e forcado (dublê com `max_result_bytes` pequeno) para exercitar a
  reducao automatica de pagina.
- **Orcamento de tempo estourado nao deixa escrita parcial.**
- **A acao da Lambda pelo caminho Data API**, com a guarda de backend ligada
  (`AWS_LAMBDA_FUNCTION_NAME`), que e literalmente o que roda em producao.
- **Independencia de flag**: `PROGRESSION_ENGINE` fica em `v1` (ausente) em
  todos os testes deste arquivo. O replay e pre-requisito das flags, entao ele
  tem que funcionar antes delas.

Schema proprio (`*_p14_test`), apagado ao final. O dominio de teste (academia,
aluno, ficha, sessoes) e o mesmo do PR-10, reaproveitado por import - nao ha
segundo seed para manter em paridade.
"""

from __future__ import annotations

import os

import pytest

from app import migrate as m
from app import progression_state, replay_progression
from app.domain.training_history import TrainingHistoryRepository
from tests.integration.dataapi.test_pr10_progressao_shadow import (  # noqa: F401 - fixtures reusadas
    EX_CRUCIFIXO,
    EX_SUPINO,
    MASTER,
    _database,
    _kwargs,
    _pymysql_factory,
    _qualifying_rows,
    _record_session,
    _state,
    domain,
    sraw,
)
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations


@pytest.fixture(scope="module")
def schema():
    """Banco novo com TODAS as migracoes aplicadas pelo runner (inclui a 036).
    Tag propria (`_p14_test`) para nao disputar o schema do PR-10."""
    database = _database("p14")
    recreate_schema_with_migrations(database=database, **_kwargs())
    m.migrate(conn_factory=_pymysql_factory(database))
    yield database
    with raw_mysql(database="information_schema", **_kwargs()) as conn:
        with conn.cursor() as cur:
            assert database.endswith("_test")
            cur.execute(f"DROP DATABASE IF EXISTS `{database}`")


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch, fake_rds, schema) -> str:
    """Igual ao do PR-10, com uma diferenca deliberada: `PROGRESSION_ENGINE`
    e REMOVIDA. O replay e o passo que roda ANTES de qualquer flag."""
    monkeypatch.setenv("API_TOKEN", MASTER)
    monkeypatch.delenv(progression_state.ENGINE_FLAG_ENV, raising=False)
    if request.param == "data-api":
        use_dataapi(monkeypatch, fake_rds, schema)
    else:
        monkeypatch.setenv("MYSQL_DATABASE", schema)
    yield request.param
    if request.param == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


@pytest.fixture
def historico(sraw, domain):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Cinco sessoes qualificantes em dois pares (exercicio, equipamento)."""
    for day in ("01", "05", "09", "13", "17"):
        _record_session(
            sraw,
            domain,
            when=f"2026-09-{day} 18:00:00",
            sets_spec=[
                ("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows()),
                ("ge_polia", EX_CRUCIFIXO, "wpe_crucifixo", _qualifying_rows(weight=25)),
            ],
        )
    return domain


def _counts(sraw, ids) -> tuple[int, int, int]:  # noqa: F811 - fixture do pytest importada (falso positivo)
    groups, tracks, decisions = _state(sraw, ids)
    return len(groups), len(tracks), len(decisions)


def _replay(**kwargs):
    """Sempre pela camada da aplicacao (`app.db`, trocada pelo backend em
    teste), como a Lambda faz."""
    return replay_progression.replay(**kwargs)


# ===========================================================================
# O replay nao depende de flag
# ===========================================================================


def test_replay_roda_com_a_flag_desligada(backend, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    assert progression_state.engine_mode() == progression_state.ENGINE_V1
    report = _replay(dry_run=False)
    assert report.status == "ok" and report.totals["tracks"] == 2
    assert _counts(sraw, historico)[1] == 2


# ===========================================================================
# dry_run
# ===========================================================================


def test_dry_run_calcula_o_plano_e_nao_escreve_nada(backend, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    report = _replay(dry_run=True)
    assert report.dry_run is True
    # o plano e real: contou trilhas, grupos e decisoes sobre o banco de verdade
    assert report.totals["sessions"] == 5
    assert report.totals["tracks"] == 2 and report.totals["groups"] == 1
    assert report.totals["decisions"] > 0
    assert report.totals["tracks_before"] == 0  # producao: as tabelas estao vazias
    # e nao gravou nada
    assert _counts(sraw, historico) == (0, 0, 0)


def test_dry_run_depois_do_replay_real_nao_altera_o_estado(backend, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    real = _replay(dry_run=False)
    antes = _counts(sraw, historico)
    plano = _replay(dry_run=True)
    assert _counts(sraw, historico) == antes
    assert [u.fingerprint for u in plano.users] == [u.fingerprint for u in real.users]
    # agora o replay ESTA substituindo estado, e o relatorio diz isso
    assert plano.totals["tracks_before"] == real.totals["tracks"]


# ===========================================================================
# Idempotencia
# ===========================================================================


def test_duas_execucoes_mesmo_estado_e_nada_duplicado(backend, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    primeiro = _replay(dry_run=False)
    depois_do_primeiro = _counts(sraw, historico)
    segundo = _replay(dry_run=False)

    assert [u.fingerprint for u in primeiro.users] == [u.fingerprint for u in segundo.users]
    assert primeiro.totals["tracks"] == segundo.totals["tracks"]
    assert primeiro.totals["groups"] == segundo.totals["groups"]
    assert primeiro.totals["decisions"] == segundo.totals["decisions"]
    assert primeiro.totals["sessions"] == segundo.totals["sessions"] == 5
    # nenhuma linha acumulada nas tres tabelas
    assert _counts(sraw, historico) == depois_do_primeiro
    assert depois_do_primeiro == (
        segundo.totals["groups"],
        segundo.totals["tracks"],
        segundo.totals["decisions"],
    )


def test_replay_grava_cada_decisao_com_a_sessao_que_a_gerou(backend, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Regressao: o replay gravava `progression_decisions.activity_id` NULL.
    Agora toda decisao EVALUATION aponta para uma sessao concluida do aluno,
    e todas as cinco sessoes do historico aparecem."""
    _replay(dry_run=False)
    _, _, decisions = _state(sraw, historico)
    assert decisions
    assert {row["kind"] for row in decisions} == {"EVALUATION"}
    assert all(row["activity_id"] is not None for row in decisions)
    with sraw.cursor() as cur:
        cur.execute(
            "SELECT id FROM activities WHERE user_id = %s AND session_status = 'done'",
            (historico["user_a"],),
        )
        done = {int(row["id"]) for row in cur.fetchall() or []}
    origem = {int(row["activity_id"]) for row in decisions}
    assert origem <= done and len(origem) == 5


# ===========================================================================
# Leitura paginada do historico
# ===========================================================================


def _activity_ids(sessions) -> list[int]:
    return [session.activity_id for session in sessions]


def test_leitura_paginada_e_identica_a_leitura_unica(backend, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    import app.db as db

    conn = db.get_conn()
    try:
        unica = TrainingHistoryRepository(conn).fetch_last_completed_sessions(historico["user_a"], 10_000)
        paginada, pages = replay_progression.fetch_sessions_paged(conn, historico["user_a"], page_size=2)
    finally:
        conn.close()
    assert pages == 3  # 2 + 2 + 1
    assert _activity_ids(paginada) == _activity_ids(unica)
    assert paginada == unica  # sessao a sessao, series inclusive


def test_pagina_de_uma_sessao_tambem_devolve_a_ordem_completa(backend, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    import app.db as db

    conn = db.get_conn()
    try:
        unica = TrainingHistoryRepository(conn).fetch_last_completed_sessions(historico["user_a"], 10_000)
        paginada, pages = replay_progression.fetch_sessions_paged(conn, historico["user_a"], page_size=1)
    finally:
        conn.close()
    assert pages == 6  # 5 paginas cheias + a vazia que encerra
    assert _activity_ids(paginada) == _activity_ids(unica)


def test_replay_paginado_ou_nao_da_o_mesmo_fingerprint(backend, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    grande = _replay(dry_run=False, page_size=1000)
    miudo = _replay(dry_run=False, page_size=1)
    assert [u.fingerprint for u in grande.users] == [u.fingerprint for u in miudo.users]
    assert grande.totals["decisions"] == miudo.totals["decisions"]


def test_teto_de_1mb_da_data_api_reduz_a_pagina_e_conclui(backend, sraw, historico, fake_rds):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Teto real do servico (`UnsupportedResultException`), forcado pequeno no
    dublê: a pagina e refeita pela metade ate passar, sem falhar o replay e sem
    mudar o resultado."""
    if backend != "data-api":
        pytest.skip("o teto de 1 MB e da RDS Data API")
    esperado = _replay(dry_run=False, page_size=8)
    fake_rds.max_result_bytes = 20_000  # ~1 sessao de historico por resposta
    reduzido = _replay(dry_run=False, page_size=8)
    assert [u.fingerprint for u in reduzido.users] == [u.fingerprint for u in esperado.users]
    assert reduzido.totals["decisions"] == esperado.totals["decisions"]


# ===========================================================================
# Orcamento de tempo
# ===========================================================================


def test_orcamento_estourado_falha_sem_escrita_parcial(backend, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    with pytest.raises(replay_progression.ReplayTimeBudgetError, match="NADA foi gravado"):
        _replay(dry_run=False, time_budget_seconds=-1.0)
    assert _counts(sraw, historico) == (0, 0, 0)


# ===========================================================================
# A acao da Lambda (caminho de producao)
# ===========================================================================


@pytest.fixture
def lambda_env(backend, monkeypatch):
    """Como a Lambda `musclefit-migrations` roda: dentro de uma Lambda e com o
    backend Data API (a guarda de `app.migrate` recusa qualquer outro)."""
    if backend != "data-api":
        pytest.skip("a acao da Lambda so roda sobre o Data API (guarda de app.migrate)")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "musclefit-migrations")
    assert os.environ.get("DB_BACKEND") == "data-api"


def test_handler_replay_dry_run_nao_escreve(lambda_env, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    result = m.handler({"action": "replay-progression", "dry_run": True}, None)
    assert result["status"] == "ok" and result["dry_run"] is True
    assert result["totals"]["tracks"] == 2
    assert _counts(sraw, historico) == (0, 0, 0)


def test_handler_replay_grava_e_repete_o_fingerprint(lambda_env, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    primeiro = m.handler({"action": "replay-progression", "dry_run": False}, None)
    assert primeiro["dry_run"] is False
    groups, tracks, decisions = _counts(sraw, historico)
    assert (groups, tracks) == (primeiro["totals"]["groups"], primeiro["totals"]["tracks"])
    assert decisions == primeiro["totals"]["decisions"] > 0
    segundo = m.handler({"action": "replay-progression", "dry_run": False}, None)
    assert [u["fingerprint"] for u in segundo["users"]] == [u["fingerprint"] for u in primeiro["users"]]
    assert _counts(sraw, historico) == (groups, tracks, decisions)


def test_handler_replay_aceita_user_ids(lambda_env, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    result = m.handler(
        {"action": "replay-progression", "dry_run": True, "user_ids": [historico["user_a"]]}, None
    )
    assert [u["user_id"] for u in result["users"]] == [historico["user_a"]]


def test_handler_usa_o_tempo_restante_da_lambda_como_orcamento(lambda_env, sraw, historico):  # noqa: F811 - fixture do pytest importada (falso positivo)
    class Context:
        @staticmethod
        def get_remaining_time_in_millis() -> int:
            return 300_000

    result = m.handler({"action": "replay-progression", "dry_run": True}, Context())
    assert result["time_budget_seconds"] == pytest.approx(300 - m.REPLAY_TIME_RESERVE_SECONDS)


def test_handler_recusa_dry_run_invalido(lambda_env):
    with pytest.raises(m.MigrationError, match="dry_run invalido"):
        m.handler({"action": "replay-progression", "dry_run": "true"}, None)
