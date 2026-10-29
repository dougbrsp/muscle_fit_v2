"""Runner (`app.migrate`) aplicando a CADEIA INTEIRA num banco novo, nos dois
backends - PyMySQL e o caminho de PRODUCAO (shim `app/db_dataapi.py` + dublê
da RDS Data API).

Por que este arquivo existe (M38, 2a passada)
---------------------------------------------
Seis arquivos de teste de migracao tinham, cada um, um `test_runner_*`
parametrizado nos 2 backends que fazia EXATAMENTE o mesmo setup:

    banco descartavel proprio -> `recreate_schema_with_migrations` (os `.sql`
    001..021) -> `m.migrate()` SEM limite de versao (a cadeia `.py` inteira)
    -> afere uma fatia diferente do schema resultante -> dropa o banco.

Doze execucoes da cadeia inteira (6 arquivos x 2 backends), a ~3s cada, ~82s
do passo `pytest dataapi` do CI - para aferir seis fatias DISJUNTAS do MESMO
resultado. Aqui a cadeia roda UMA VEZ POR BACKEND (2 execucoes) e as seis
funcoes de assercao, INALTERADAS, leem desse resultado compartilhado.

Mapeamento (nome antigo -> funcao aqui), para o historico do CI continuar
legivel:

    test_migration_029::test_runner_001_029_em_banco_novo_aplica_a_029
        -> test_runner_001_029_em_banco_novo_aplica_a_029
    test_migration_031::test_runner_001_031_em_banco_novo_aplica_a_031
        -> test_runner_001_031_em_banco_novo_aplica_a_031
    test_migration_032_033::test_runner_em_banco_novo_aplica_032_e_033
        -> test_runner_em_banco_novo_aplica_032_e_033
    test_pr08::test_runner_em_banco_novo_aplica_034_e_035_e_so_sobra_a_tabela_por_par
        -> test_runner_em_banco_novo_aplica_034_e_035_e_so_sobra_a_tabela_por_par
    test_migration_037::test_runner_em_banco_novo_aplica_a_037
        -> test_runner_em_banco_novo_aplica_a_037
    test_migration_038::test_runner_em_banco_novo_aplica_a_038
        -> test_runner_em_banco_novo_aplica_a_038

A idempotencia (`m.migrate()` de novo nao aplica nada), que quatro deles
afirmavam separadamente, virou UMA assercao por backend
(`test_segunda_execucao_da_cadeia_nao_aplica_nada`).

A armadilha de isolamento, e por que ela nao morde aqui
-------------------------------------------------------
Fixture de escopo largo convivendo com `monkeypatch`/`fake_rds` de escopo de
funcao foi a mecanica das tres quebras de isolamento dos PRs #199/#200/#231
(ver o cabecalho de `tests/support/isolamento_v3.py`). Duas escolhas tiram
este arquivo dessa classe de problema:

1. **O patch nao sobrevive ao setup.** O `use_dataapi` do backend `data-api`
   entra num `pytest.MonkeyPatch()` proprio da fixture e e DESFEITO antes de
   qualquer teste rodar - a cadeia ja esta aplicada, e as assercoes leem o
   schema por conexao NEUTRA (`raw_mysql`), nao pela camada da aplicacao.
   Ou seja: nenhum estado global de backend vaza deste modulo para os
   seguintes, e `use_dataapi` continua encontrando `app.db` em pymysql (ele
   mesmo checa isso e explode se nao estiver).
2. **O banco e proprio e descartavel.** `<stem>_rcad_<backend>_test`, criado e
   dropado por esta fixture. Nada aqui toca o schema COMPARTILHADO da suite
   (`<stem>_run<token>_test`), que e onde mora o problema de `TRUNCATE` /
   AUTO_INCREMENT / FK que `isolamento_v3` documenta.

Escopo `module` (nao `session`): a cadeia vive e morre dentro deste arquivo.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import pytest

from app import migrate as m
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support import preferences_034
from tests.support.advanced_techniques_031 import CODES, MIGRATION_031, STATEMENTS_031
from tests.support.advanced_techniques_031 import rows as advanced_techniques_rows
from tests.support.aliases_029 import MIGRATION_029, PLAN as PLAN_029
from tests.support.aliases_029 import aliases_indexes, aurora_aliases_indexes
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.fake_rds_data import FakeRdsDataBackend
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.periodizacao_038 import GENERATION_LOG_INDEX, LOGS, TEMPLATES
from tests.support.preferences_035 import EXPECTED_COLUMNS as EXPECTED_COLUMNS_035
from tests.support.preferences_035 import MIGRATION_035, OLD_TABLE, TABLE as TABLE_035
from tests.support.prescricao_037 import DECISION_INDEX, SETS, WPE
from tests.support.safety_032_033 import ALTER_EQUIPMENT, ALTER_EQUIPMENT_EXERCISES, MIGRATION_032, MIGRATION_033

NEW_COLUMNS_037 = {
    WPE: ("prescription_json", "progression_decision_id", "engine_version", "requires_user_choice"),
    SETS: ("rpe_target_effective", "safety_event"),
}
NEW_TEMPLATE_COLUMNS_038 = ("origin", "generation_log_id")


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


def _database(tag: str) -> str:
    base = os.environ["MYSQL_DATABASE"]
    return f"{base[: -len('_test')]}_{tag}_test"


def _drop_database(database: str) -> None:
    assert database.endswith("_test")
    with raw_mysql(database="information_schema", **_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{database}`")


def _pymysql_factory(database: str):
    kw = _kwargs()
    return lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database)


class Cadeia:
    """Resultado de UMA execucao da cadeia inteira num banco novo.

    `applied` e o relatorio do PRIMEIRO `m.migrate()` (filename -> statements),
    `second` o do segundo, e `raw` uma conexao NEUTRA para ler o schema
    resultante. Nenhum teste deste arquivo escreve por `raw` - quem precisa
    alterar o banco depois do `migrate()` continua no seu arquivo proprio, com
    schema proprio.
    """

    def __init__(self, database: str, applied: dict[str, list[str]], second: Any, raw: Any) -> None:
        self.database = database
        self.applied = applied
        self.second = second
        self.raw = raw


@pytest.fixture(scope="module", params=BACKENDS)
def cadeia(request: pytest.FixtureRequest) -> Iterator[Cadeia]:
    backend = request.param
    database = _database(f"rcad_{backend.replace('-', '_')}")
    recreate_schema_with_migrations(database=database, **_kwargs())

    # `pytest.MonkeyPatch()` explicito: a fixture `monkeypatch` do pytest e de
    # escopo de FUNCAO e nao pode ser pedida aqui. Este e desfeito no `finally`,
    # ainda dentro do setup - ver o cabecalho do modulo.
    mp = pytest.MonkeyPatch()
    fake: FakeRdsDataBackend | None = None
    try:
        mp.delenv("MIGRATIONS_DIR", raising=False)  # a cadeia e a do repo, sem limite de versao
        if backend == "data-api":
            fake = FakeRdsDataBackend(**_kwargs())
            use_dataapi(mp, fake, database)
            factory = None  # `m.migrate` cai no `app.db.get_conn`, ja trocado pelo shim
        else:
            factory = _pymysql_factory(database)

        first = m.migrate(conn_factory=factory)
        second = m.migrate(conn_factory=factory)
        if fake is not None:
            assert fake.open_transactions == []
    finally:
        mp.undo()
        if fake is not None:
            fake.close()

    applied = {item.filename: item.statements for item in first.applied}
    with raw_mysql(database=database, **_kwargs()) as raw:
        yield Cadeia(database, applied, second, raw)
    _drop_database(database)


# ---------------------------------------------------------------------------
# Leituras de schema (information_schema) - primitivas, nao asercoes.
# ---------------------------------------------------------------------------


def _columns(raw: Any, table: str) -> dict[str, tuple[str, str, object]]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        return {
            r["COLUMN_NAME"]: (r["COLUMN_TYPE"].lower(), r["IS_NULLABLE"], r["COLUMN_DEFAULT"])
            for r in cur.fetchall()
        }


def _columns_sem_default(raw: Any, table: str) -> dict[str, tuple[str, str]]:
    """Recorte usado pela 035 (`preferences_035.EXPECTED_COLUMNS` nao tem
    default)."""
    return {name: (col_type, nullable) for name, (col_type, nullable, _) in _columns(raw, table).items()}


def _index_columns(raw: Any, table: str, index: str) -> list[str]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = %s AND INDEX_NAME = %s ORDER BY SEQ_IN_INDEX",
            (table, index),
        )
        return [r["COLUMN_NAME"] for r in cur.fetchall()]


def _tabelas(raw: Any) -> set[str]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_TYPE = 'BASE TABLE'"
        )
        return {row["TABLE_NAME"] for row in cur.fetchall()}


def _max_version(raw: Any) -> int:
    with raw.cursor() as cur:
        cur.execute(f"SELECT MAX(version) AS v FROM {m.CONTROL_TABLE}")
        return int(cur.fetchone()["v"])


# ---------------------------------------------------------------------------
# A cadeia como um todo
# ---------------------------------------------------------------------------


def test_cadeia_aplica_todas_as_migracoes_pendentes_do_repo(cadeia: Cadeia) -> None:
    """A cadeia rodada pela fixture e a do repo INTEIRA (sem limite de versao):
    toda migracao acima do baseline aparece no relatorio, na ordem de versao."""
    pendentes = sorted(
        p.name
        for p in m.DEFAULT_MIGRATIONS_DIR.iterdir()
        if m.MIGRATION_FILENAME_RE.match(p.name) and int(p.name[:3]) > m.BASELINE_MAX_VERSION
    )
    assert list(cadeia.applied) == pendentes


def test_segunda_execucao_da_cadeia_nao_aplica_nada(cadeia: Cadeia) -> None:
    """Idempotencia - era afirmada separadamente pelos runners da 029, 031,
    032/033 e do PR-08."""
    assert cadeia.second.applied == []


# ---------------------------------------------------------------------------
# 029 - `exercise_aliases` sem UNIQUE global por `alias`
# ---------------------------------------------------------------------------


def test_runner_001_029_em_banco_novo_aplica_a_029(cadeia: Cadeia) -> None:
    assert cadeia.applied[MIGRATION_029.name] == PLAN_029
    assert aliases_indexes(cadeia.raw) == aurora_aliases_indexes()


# ---------------------------------------------------------------------------
# 031 - catalogo `advanced_techniques`
# ---------------------------------------------------------------------------


def test_runner_001_031_em_banco_novo_aplica_a_031(cadeia: Cadeia) -> None:
    assert len(cadeia.applied[MIGRATION_031.name]) == STATEMENTS_031
    assert sorted(advanced_techniques_rows(cadeia.raw)) == sorted(CODES)


# ---------------------------------------------------------------------------
# 032 + 033 - seguranca do equipamento
# ---------------------------------------------------------------------------


def test_runner_em_banco_novo_aplica_032_e_033(cadeia: Cadeia) -> None:
    assert cadeia.applied[MIGRATION_032.name] == [ALTER_EQUIPMENT, ALTER_EQUIPMENT_EXERCISES]
    assert len(cadeia.applied[MIGRATION_033.name]) == 2


# ---------------------------------------------------------------------------
# 034 + 035 (PR-08) - preferencias por PAR exercicio+equipamento
# ---------------------------------------------------------------------------


def test_runner_em_banco_novo_aplica_034_e_035_e_so_sobra_a_tabela_por_par(cadeia: Cadeia) -> None:
    assert len(cadeia.applied[preferences_034.MIGRATION_034.name]) == 1
    assert len(cadeia.applied[MIGRATION_035.name]) == 2
    assert _columns_sem_default(cadeia.raw, OLD_TABLE) == {}
    assert _columns_sem_default(cadeia.raw, TABLE_035) == EXPECTED_COLUMNS_035


# ---------------------------------------------------------------------------
# 037 - congelamento da prescricao
# ---------------------------------------------------------------------------


def test_runner_em_banco_novo_aplica_a_037(cadeia: Cadeia) -> None:
    assert set(NEW_COLUMNS_037[WPE]) <= set(_columns(cadeia.raw, WPE))
    assert set(NEW_COLUMNS_037[SETS]) <= set(_columns(cadeia.raw, SETS))
    assert _index_columns(cadeia.raw, WPE, DECISION_INDEX) == ["progression_decision_id"]
    assert _max_version(cadeia.raw) >= 37


# ---------------------------------------------------------------------------
# 038 - auditoria da periodizacao
# ---------------------------------------------------------------------------


def test_runner_em_banco_novo_aplica_a_038(cadeia: Cadeia) -> None:
    assert LOGS in _tabelas(cadeia.raw)
    assert set(NEW_TEMPLATE_COLUMNS_038) <= set(_columns(cadeia.raw, TEMPLATES))
    assert _index_columns(cadeia.raw, TEMPLATES, GENERATION_LOG_INDEX) == ["generation_log_id"]
    assert _max_version(cadeia.raw) >= 38
