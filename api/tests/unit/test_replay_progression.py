"""Testes unitarios da operacao de replay (`app/replay_progression.py`) e da
acao `replay-progression` do handler (`app/migrate.py`), sem banco.

O comportamento sobre banco real (idempotencia, `dry_run` que nao escreve,
caminho Data API) esta em
tests/integration/dataapi/test_pr14_replay_operacao.py. Aqui ficam a mecanica
da paginacao (incluindo a reducao de pagina no teto de 1 MB da RDS Data API) e
a validacao do evento da Lambda.

Rodar: cd api && python -m pytest tests/unit/test_replay_progression.py
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app import migrate as m
from app import replay_progression as rp


# ---------------------------------------------------------------------------
# Paginacao: repositorio de historico dublado
# ---------------------------------------------------------------------------


class FakeSession:
    """Basta ter `activity_id`: `fetch_sessions_paged` so ordena e deduplica."""

    def __init__(self, activity_id: int):
        self.activity_id = activity_id

    def __repr__(self) -> str:  # pragma: no cover - diagnostico
        return f"S{self.activity_id}"


class _TooLarge(Exception):
    def __init__(self) -> None:
        super().__init__("UnsupportedResultException: The result exceeds the size limit 1 MB.")


class FakeRepo:
    """Historico em ordem DECRESCENTE (como a subconsulta do SQL), fatiado por
    LIMIT/OFFSET e devolvido em ordem crescente (como o ORDER BY externo).

    `max_per_page` simula o teto de 1 MB: pagina maior que isso levanta o
    mesmo erro do servico.
    """

    def __init__(self, newest_first: list[int], *, max_per_page: Optional[int] = None):
        self.newest_first = newest_first
        self.max_per_page = max_per_page
        self.calls: list[tuple[int, int]] = []

    def fetch_last_completed_sessions(self, user_id: int, n: int, *, offset: int = 0) -> list[FakeSession]:
        self.calls.append((n, offset))
        if self.max_per_page is not None and n > self.max_per_page:
            raise _TooLarge()
        chunk = self.newest_first[offset : offset + n]
        return [FakeSession(activity_id) for activity_id in reversed(chunk)]


@pytest.fixture
def repo_factory(monkeypatch):
    def install(newest_first: list[int], **kwargs: Any) -> FakeRepo:
        repo = FakeRepo(newest_first, **kwargs)
        monkeypatch.setattr(rp, "TrainingHistoryRepository", lambda conn: repo)
        return repo

    return install


def _ids(sessions) -> list[int]:
    return [session.activity_id for session in sessions]


def test_paginacao_devolve_a_ordem_crescente_de_uma_leitura_unica(repo_factory):
    repo = repo_factory([50, 40, 30, 20, 10])  # do mais novo para o mais antigo
    sessions, pages = rp.fetch_sessions_paged(object(), 1, page_size=2)
    assert _ids(sessions) == [10, 20, 30, 40, 50]
    assert pages == 3
    assert repo.calls == [(2, 0), (2, 2), (2, 4)]


def test_pagina_maior_que_o_historico_faz_uma_leitura_so(repo_factory):
    repo = repo_factory([30, 20, 10])
    sessions, pages = rp.fetch_sessions_paged(object(), 1, page_size=100)
    assert _ids(sessions) == [10, 20, 30] and pages == 1
    assert repo.calls == [(100, 0)]


def test_historico_vazio(repo_factory):
    repo_factory([])
    sessions, pages = rp.fetch_sessions_paged(object(), 1, page_size=10)
    assert sessions == [] and pages == 1


def test_max_sessions_limita_o_total_lido(repo_factory):
    repo_factory([90, 80, 70, 60, 50])
    sessions, _ = rp.fetch_sessions_paged(object(), 1, page_size=2, max_sessions=3)
    # le so as 3 MAIS RECENTES, na ordem crescente entre elas
    assert _ids(sessions) == [70, 80, 90]


def test_teto_de_1mb_reduz_a_pagina_pela_metade_e_continua_reduzida(repo_factory):
    repo = repo_factory([50, 40, 30, 20, 10], max_per_page=2)
    sessions, pages = rp.fetch_sessions_paged(object(), 1, page_size=8)
    assert _ids(sessions) == [10, 20, 30, 40, 50]
    # 8 -> 4 -> 2 na primeira pagina; as seguintes ja nascem com 2
    assert repo.calls[:3] == [(8, 0), (4, 0), (2, 0)]
    assert repo.calls[3:] == [(2, 2), (2, 4)]
    assert pages == 3


def test_uma_sessao_acima_do_teto_falha_com_erro_explicito(repo_factory):
    repo_factory([10, 20], max_per_page=0)
    with pytest.raises(rp.ReplayResultTooLargeError, match="1 MB"):
        rp.fetch_sessions_paged(object(), 7, page_size=4)


def test_erro_que_nao_e_o_teto_de_1mb_sobe_sem_reducao(repo_factory):
    class Boom(Exception):
        pass

    class Explode(FakeRepo):
        def fetch_last_completed_sessions(self, user_id, n, *, offset=0):
            raise Boom("conexao morreu")

    repo = Explode([10])
    import app.replay_progression as module

    original = module.TrainingHistoryRepository
    module.TrainingHistoryRepository = lambda conn: repo  # type: ignore[assignment]
    try:
        with pytest.raises(Boom):
            rp.fetch_sessions_paged(object(), 1, page_size=4)
    finally:
        module.TrainingHistoryRepository = original  # type: ignore[assignment]


def test_sessao_repetida_entre_paginas_nao_duplica(repo_factory):
    # escrita concorrente deslocaria a janela do OFFSET e repetiria uma sessao
    repo_factory([40, 30, 30, 20])
    sessions, _ = rp.fetch_sessions_paged(object(), 1, page_size=2)
    assert _ids(sessions) == [20, 30, 40]


def test_page_size_vem_da_env_quando_nao_passado(monkeypatch):
    monkeypatch.delenv(rp.PAGE_SIZE_ENV, raising=False)
    assert rp._page_size() == rp.DEFAULT_PAGE_SIZE
    monkeypatch.setenv(rp.PAGE_SIZE_ENV, "3")
    assert rp._page_size() == 3
    assert rp._page_size(7) == 7  # argumento explicito ganha
    monkeypatch.setenv(rp.PAGE_SIZE_ENV, "zero")
    assert rp._page_size() == rp.DEFAULT_PAGE_SIZE
    assert rp._page_size(0) == rp.MIN_PAGE_SIZE


# ---------------------------------------------------------------------------
# Transacao unica: orcamento estourado nao commita
# ---------------------------------------------------------------------------


class FakeConn:
    """Conexao minima: responde `SELECT 1` e "nenhum usuario com historico"."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> "FakeConn":
        return self

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> "FakeConn":
        # cuidado: "SELECT 1" tambem aparece no EXISTS de users_with_history
        self._rows = [{"n": 1}] if "SELECT 1 AS n" in sql else []
        return self

    def fetchone(self) -> Optional[dict[str, Any]]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_orcamento_estourado_nao_commita_e_faz_rollback():
    conn = FakeConn()
    with pytest.raises(rp.ReplayTimeBudgetError, match="NADA foi gravado"):
        rp.replay(dry_run=False, conn_factory=lambda: conn, time_budget_seconds=-1.0)
    assert (conn.commits, conn.rollbacks, conn.closed) == (0, 1, True)


def test_dry_run_sempre_faz_rollback_e_nunca_commit():
    conn = FakeConn()
    report = rp.replay(dry_run=True, conn_factory=lambda: conn)
    assert report.dry_run is True and report.status == "ok"
    assert (conn.commits, conn.rollbacks) == (0, 1)


def test_execucao_real_commita_uma_vez():
    conn = FakeConn()
    report = rp.replay(dry_run=False, conn_factory=lambda: conn)
    assert report.totals == {
        "users": 0,
        "sessions": 0,
        "groups": 0,
        "tracks": 0,
        "decisions": 0,
        "groups_before": 0,
        "tracks_before": 0,
        "decisions_before": 0,
    }
    assert (conn.commits, conn.rollbacks) == (1, 0)


# ---------------------------------------------------------------------------
# A acao no handler da Lambda
# ---------------------------------------------------------------------------


@pytest.fixture
def in_lambda(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "musclefit-migrations")
    monkeypatch.delenv("DB_BACKEND", raising=False)


@pytest.fixture
def spy_replay(monkeypatch):
    chamadas: list[dict[str, Any]] = []

    def fake_replay(user_ids=None, **kwargs: Any) -> rp.ReplayReport:
        chamadas.append({"user_ids": user_ids, **kwargs})
        return rp.ReplayReport(dry_run=bool(kwargs.get("dry_run", True)))

    monkeypatch.setattr(rp, "replay", fake_replay)
    return chamadas


def test_handler_default_e_dry_run(in_lambda, spy_replay):
    result = m.handler({"action": "replay-progression"}, None)
    assert result["status"] == "ok" and result["dry_run"] is True
    assert spy_replay[0]["dry_run"] is True and spy_replay[0]["user_ids"] is None


def test_handler_repassa_dry_run_false_e_user_ids(in_lambda, spy_replay):
    m.handler({"action": "replay-progression", "dry_run": False, "user_ids": [7, "9"]}, None)
    assert spy_replay[0]["dry_run"] is False
    assert spy_replay[0]["user_ids"] == [7, 9]


@pytest.mark.parametrize("dry_run", ["true", 1, None])
def test_handler_recusa_dry_run_que_nao_e_booleano(in_lambda, spy_replay, dry_run):
    with pytest.raises(m.MigrationError, match="dry_run invalido"):
        m.handler({"action": "replay-progression", "dry_run": dry_run}, None)
    assert spy_replay == []


@pytest.mark.parametrize("user_ids", [[], "1", [1, "x"], {}])
def test_handler_recusa_user_ids_invalido(in_lambda, spy_replay, user_ids):
    with pytest.raises(m.MigrationError, match="user_ids invalido"):
        m.handler({"action": "replay-progression", "user_ids": user_ids}, None)
    assert spy_replay == []


def test_handler_recusa_pymysql_antes_de_qualquer_replay(monkeypatch, spy_replay):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "musclefit-migrations")
    monkeypatch.setenv("DB_BACKEND", "pymysql")
    with pytest.raises(m.MigrationError, match="data-api"):
        m.handler({"action": "replay-progression", "dry_run": False}, None)
    assert spy_replay == []


def test_orcamento_do_handler_desconta_a_reserva_do_tempo_restante():
    class Context:
        @staticmethod
        def get_remaining_time_in_millis() -> int:
            return 300_000

    assert m._replay_time_budget(Context()) == pytest.approx(300.0 - m.REPLAY_TIME_RESERVE_SECONDS)
    # sem context (CLI/teste): o default do modulo
    assert m._replay_time_budget(None) == rp.DEFAULT_TIME_BUDGET_SECONDS
    # tempo restante ja curto: nunca negativo
    class Quase:
        @staticmethod
        def get_remaining_time_in_millis() -> int:
            return 1_000

    assert m._replay_time_budget(Quase()) == 5.0


def test_action_desconhecida_continua_recusada(in_lambda):
    with pytest.raises(m.MigrationError, match="action invalida"):
        m.handler({"action": "replay"}, None)
