"""Plano da migracao 041 (`activity_intervals_link`, melhoria M2) sobre um
recorte de `information_schema` construido no proprio teste.

Diferente das 028/032-038, esta migracao nao precisa de um snapshot real da
Aurora: ela nao altera NENHUMA tabela existente, so cria uma tabela nova. O
que precisa ser provado e:

- banco sem a tabela           -> exatamente 1 `CREATE TABLE`, sem ler dado;
- banco com a tabela no formato -> 0 statements (idempotente/convergido);
- banco com a tabela torta      -> erro ANTES de qualquer statement;
- tipo das PKs referenciadas    -> BIGINT signed nos tres lados (senao a FK
  nem seria criavel no MySQL).

Rodar: cd api && python -m pytest tests/unit/test_migration_041_intervals_hr_link.py
"""

from __future__ import annotations


import pytest

from app import migrate as m

MIGRATION_PATH = m.DEFAULT_MIGRATIONS_DIR / "041_intervals_hr_link.py"
MODULE = m.load_python_migration(MIGRATION_PATH).__globals__
LINK = MODULE["LINK"]
CREATE_SQL = " ".join(MODULE["CREATE_LINK_SQL"].split())


# ---------------------------------------------------------------------------
# Recorte minimo de information_schema
# ---------------------------------------------------------------------------

REFERENCED_TABLES = ("users", "activities", "intervals_activities")


def _base_snapshot() -> dict:
    """So as tres tabelas referenciadas, no formato real (BIGINT PK InnoDB).
    `activity_intervals_link` NAO existe - o estado de producao hoje."""
    return {
        "tables": {name: "InnoDB" for name in REFERENCED_TABLES},
        "columns": {
            (name, "id"): {"COLUMN_TYPE": "bigint", "IS_NULLABLE": "NO", "COLUMN_KEY": "PRI", "EXTRA": "auto_increment"}
            for name in REFERENCED_TABLES
        },
        "indexes": {},
        "fks": {},
    }


def _converged_snapshot() -> dict:
    """Banco que JA aplicou a 041 - o formato exato que o CREATE produz."""
    snapshot = _base_snapshot()
    snapshot["tables"][LINK] = "InnoDB"
    for column, (column_type, nullable) in MODULE["EXPECTED_COLUMNS"].items():
        snapshot["columns"][(LINK, column)] = {
            "COLUMN_TYPE": column_type,
            "IS_NULLABLE": nullable,
            "COLUMN_KEY": "PRI" if column == "id" else "",
            "EXTRA": "auto_increment" if column == "id" else "",
        }
    for index, columns in MODULE["EXPECTED_INDEXES"].items():
        snapshot["indexes"][index] = {
            "columns": list(columns),
            "non_unique": 0 if index in MODULE["UNIQUE_INDEXES"] else 1,
        }
    snapshot["fks"] = dict(MODULE["EXPECTED_FKS"])
    return snapshot


class SnapshotDB:
    """Responde SO leituras de `information_schema`. Qualquer escrita ou
    contagem de linhas durante o plano e falha do teste - prova que a 041 nao
    toca em dado nenhum para decidir.

    `dry_run` existe porque a `MigrationDB` real tem esse atributo e a 041 o
    consulta (tolerancia a 027 pendente no mesmo lote)."""

    def __init__(self, snapshot: dict, dry_run: bool = False):
        self.snap = snapshot
        self.dry_run = dry_run
        self.queries: list[str] = []

    def query(self, sql: str, params=None) -> list[dict]:
        self.queries.append(sql)
        wanted = set(params or ())
        snap = self.snap

        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": name, "ENGINE": engine} for name, engine in snap["tables"].items() if name in wanted]

        if "information_schema.COLUMNS" in sql:
            return [
                {"TABLE_NAME": table, "COLUMN_NAME": column, **info}
                for (table, column), info in snap["columns"].items()
                if table in wanted
            ]

        if "information_schema.STATISTICS" in sql:
            assert wanted == {LINK}
            return [
                {"INDEX_NAME": index, "COLUMN_NAME": column, "NON_UNIQUE": info["non_unique"]}
                for index, info in sorted(snap["indexes"].items())
                for column in info["columns"]
            ]

        if "KEY_COLUMN_USAGE" in sql:
            assert wanted == {LINK}
            return [
                {"COLUMN_NAME": column, "REFERENCED_TABLE_NAME": table} for column, table in snap["fks"].items()
            ]

        raise AssertionError(f"leitura inesperada no plano: {sql}")

    def query_one(self, sql: str, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params=None):
        raise AssertionError(f"escrita durante o plano: {sql}")


def _plan(snapshot: dict, dry_run: bool = False):
    db = SnapshotDB(snapshot, dry_run)
    statements = [(" ".join(sql.split()), params) for sql, params in MODULE["plan"](db)]
    return statements, db


# ---------------------------------------------------------------------------
# Plano
# ---------------------------------------------------------------------------


def test_banco_sem_a_tabela_executa_um_unico_create():
    statements, db = _plan(_base_snapshot())
    assert statements == [(CREATE_SQL, None)]
    assert all("information_schema" in query for query in db.queries)


def test_banco_ja_convergido_nao_executa_nada():
    statements, _ = _plan(_converged_snapshot())
    assert statements == []


def test_create_declara_as_tres_fks_e_a_unique_por_treino():
    assert "UNIQUE KEY uk_ail_activity (activity_id)" in CREATE_SQL
    for column, table in MODULE["EXPECTED_FKS"].items():
        assert f"FOREIGN KEY ({column}) REFERENCES {table}(id) ON DELETE CASCADE" in CREATE_SQL


def test_fks_sao_bigint_signed_como_as_pks_referenciadas():
    """`exercise_catalog.id` e UNSIGNED no projeto; as tres PKs referenciadas
    aqui sao SIGNED, entao nenhuma coluna da nova tabela pode ser UNSIGNED."""
    assert set(MODULE["REFERENCED"].values()) == {"bigint"}
    assert "UNSIGNED" not in CREATE_SQL.upper()


def test_status_persistido_cobre_exatamente_os_dois_casos_com_linha():
    """A tabela so guarda casamento que ACONTECEU. "Nenhum candidato" e
    "treino sem horario" nao viram linha - ver app/intervals_hr_match.py."""
    from app import intervals_hr_match

    assert set(intervals_hr_match.PERSISTED_STATUSES) == {"MATCHED", "MATCHED_AMBIGUOUS"}
    for status in intervals_hr_match.PERSISTED_STATUSES:
        assert f"'{status}'" in CREATE_SQL


# ---------------------------------------------------------------------------
# Estado incompativel: falha antes do primeiro statement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate, trecho",
    [
        (lambda s: s["columns"].pop((LINK, "avg_hr_bpm")), "avg_hr_bpm nao existe"),
        (
            lambda s: s["columns"].__setitem__(
                (LINK, "match_delta_seconds"),
                {"COLUMN_TYPE": "bigint", "IS_NULLABLE": "NO", "COLUMN_KEY": "", "EXTRA": ""},
            ),
            "match_delta_seconds e bigint",
        ),
        (lambda s: s["indexes"].__setitem__("uk_ail_activity", {"columns": ["activity_id"], "non_unique": 1}), "nao e UNIQUE"),
        (lambda s: s["fks"].pop("intervals_activity_id"), "FKs de"),
    ],
)
def test_tabela_em_formato_incompativel_falha_sem_executar(mutate, trecho):
    snapshot = _converged_snapshot()
    mutate(snapshot)
    with pytest.raises(MODULE["IntervalsHrLinkError"]) as exc:
        _plan(snapshot)
    assert trecho in str(exc.value)
    assert "Nenhum statement executado" in str(exc.value)


def test_tabela_referenciada_ausente_falha():
    snapshot = _base_snapshot()
    snapshot["tables"].pop("intervals_activities")
    with pytest.raises(MODULE["IntervalsHrLinkError"]) as exc:
        _plan(snapshot)
    assert "intervals_activities nao existe" in str(exc.value)


def test_em_dry_run_a_027_pendente_no_mesmo_lote_nao_quebra_o_plano():
    """Banco novo: o runner planeja o lote inteiro de uma vez e, em dry-run, a
    027 nao chega a criar `intervals_activities`. O plano da 041 ainda precisa
    aparecer - o formato e conferido de novo na execucao real. Mesma regra da
    033 com `advanced_techniques`."""
    snapshot = _base_snapshot()
    snapshot["tables"].pop("intervals_activities")
    snapshot["columns"].pop(("intervals_activities", "id"))
    statements, _ = _plan(snapshot, dry_run=True)
    assert statements == [(CREATE_SQL, None)]


def test_tabela_do_baseline_ausente_falha_mesmo_em_dry_run():
    """A tolerancia vale SO para a tabela da 027. `activities` vem do baseline
    001: se nao existe, o banco nao e o que a migracao espera."""
    snapshot = _base_snapshot()
    snapshot["tables"].pop("activities")
    with pytest.raises(MODULE["IntervalsHrLinkError"]) as exc:
        _plan(snapshot, dry_run=True)
    assert "activities nao existe" in str(exc.value)


def test_versao_041_e_unica_entre_as_migracoes_descobertas():
    """O runner deriva a versao do NOME do arquivo e RECUSA versao duplicada
    antes de executar qualquer coisa. O que precisa valer e **unicidade** da
    versao 41 - e nao "sou a ultima" ou "sou o proximo numero livre": foi
    exatamente esse tipo de assercao que deixou uma colisao de numero passar
    entre PRs paralelos. Outra migracao pode entrar depois desta sem que este
    teste precise mudar."""
    migrations = list(m.discover_migrations())
    minha = [item for item in migrations if item.version == 41]
    assert [item.filename for item in minha] == ["041_intervals_hr_link.py"]
    assert not minha[0].is_baseline
    assert minha[0].version > m.BASELINE_MAX_VERSION

    # Nenhuma versao duplicada no diretorio inteiro (a colisao que o runner
    # recusaria, detectada aqui antes de chegar ao banco).
    versoes = [item.version for item in migrations]
    assert len(versoes) == len(set(versoes)), sorted(
        v for v in versoes if versoes.count(v) > 1
    )


def test_plano_nao_usa_comando_proibido_pelo_data_api():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    for command in ("PREPARE ", "EXECUTE ", "DELIMITER", "SET @"):
        assert command not in source.upper().replace("PREPARED", "")
