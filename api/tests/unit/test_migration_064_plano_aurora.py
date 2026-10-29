"""Plano da migracao 064 (cards ilustrados de exercicios: tabela
`exercise_card_generations` + 3 colunas de ponteiro em `exercise_catalog`)
contra recortes REAIS do `information_schema`.

Recortes reaproveitados (nenhuma leitura nova da AWS):

- `tests/unit/data/aurora_schema_2026_09_17_036.json` - Aurora de producao
  (SELECT somente leitura; ver `_origem`): traz `exercise_catalog` e `users`
  com as colunas `id`, que sao exatamente o que a 064 pergunta;
- `tests/unit/data/schema_dev_2026_09_20_039.json` - MySQL 8.0.46 do DEV, o
  fallback que a 039/049 ja usam, com as mesmas duas tabelas.

Os dois recortes sao anteriores as colunas da 039/042 em `exercise_catalog`,
o que nao muda nada aqui: a 064 so le `exercise_catalog.id` e as 3 colunas
`card_*`, e NENHUMA migracao entre a 036 e a 063 cria `card_*` nem
`exercise_card_generations` (o teste `test_nenhuma_migracao_anterior_*`
prova isso varrendo o diretorio). `exercise_catalog.id` e `users.id` nao
mudam de tipo desde a 028/baseline.

Producao hoje: sem a tabela e sem as colunas -> 1 `CREATE TABLE` + 3
`ALTER TABLE ... ADD COLUMN`. Estado convergido -> 0 statements. `down` ->
3 `DROP COLUMN` + `DROP TABLE`.

Rodar: cd api && python -m pytest tests/unit/test_migration_064_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_064 = m.DEFAULT_MIGRATIONS_DIR / "064_exercise_card_generations.py"
DATA = Path(__file__).resolve().parents[1] / "unit" / "data"
AURORA = DATA / "aurora_schema_2026_09_17_036.json"
DEV = DATA / "schema_dev_2026_09_20_039.json"
RECORTES = pytest.mark.parametrize("recorte", [AURORA, DEV], ids=["aurora-036", "dev-039"])

TABLE = "exercise_card_generations"
CATALOG = "exercise_catalog"
ADD_GENERATION_ID = f"ALTER TABLE {CATALOG} ADD COLUMN card_generation_id BIGINT NULL"
ADD_VERSION = f"ALTER TABLE {CATALOG} ADD COLUMN card_version INT NULL"
ADD_PUBLISHED_AT = f"ALTER TABLE {CATALOG} ADD COLUMN card_published_at DATETIME NULL"
DROP_GENERATION_ID = f"ALTER TABLE {CATALOG} DROP COLUMN card_generation_id"
DROP_VERSION = f"ALTER TABLE {CATALOG} DROP COLUMN card_version"
DROP_PUBLISHED_AT = f"ALTER TABLE {CATALOG} DROP COLUMN card_published_at"
DROP_TABLE = f"DROP TABLE IF EXISTS {TABLE}"

INDICES = {
    "idx_ecg_exercise_status": ("KEY", ("exercise_id", "status")),
    "uq_ecg_exercise_version": ("UNIQUE KEY", ("exercise_id", "version")),
    "uq_ecg_exercise_idem": ("UNIQUE KEY", ("exercise_id", "idempotency_key")),
    "idx_ecg_created_by_created": ("KEY", ("created_by", "created_at")),
    "idx_ecg_status_started": ("KEY", ("status", "started_at")),
}

# Tipos como o information_schema do MySQL 8 os mostra (com as larguras de
# exibicao que ainda aparecem em alguns servidores, para provar a
# normalizacao).
TIPOS_CONVERGIDOS = {
    "id": ("bigint(20)", "NO"),
    "exercise_id": ("bigint(20) unsigned", "NO"),
    "card_type": ("varchar(40)", "NO"),
    "status": ("varchar(16)", "NO"),
    "version": ("int(11)", "YES"),
    "parent_generation_id": ("bigint", "YES"),
    "idempotency_key": ("char(36)", "NO"),
    "job_id": ("varchar(36)", "YES"),
    "provider": ("varchar(20)", "NO"),
    "model": ("varchar(60)", "YES"),
    "quality": ("varchar(10)", "YES"),
    "size": ("varchar(12)", "YES"),
    "prompt_version": ("varchar(40)", "NO"),
    "text_level": ("varchar(10)", "NO"),
    "prompt_text": ("text", "YES"),
    "request_instruction": ("text", "YES"),
    "reject_reason": ("text", "YES"),
    "preview_s3_key": ("varchar(255)", "YES"),
    "card_s3_key": ("varchar(255)", "YES"),
    "card_png_s3_key": ("varchar(255)", "YES"),
    "thumb_s3_key": ("varchar(255)", "YES"),
    "width": ("int", "YES"),
    "height": ("int", "YES"),
    "bytes": ("int", "YES"),
    "tokens_in": ("int", "YES"),
    "tokens_out": ("int", "YES"),
    "cost_usd": ("decimal(8,4)", "YES"),
    "error_code": ("varchar(40)", "YES"),
    "error_message": ("text", "YES"),
    "created_by": ("bigint", "NO"),
    "approved_by": ("bigint", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
    "started_at": ("datetime", "YES"),
    "generated_at": ("datetime", "YES"),
    "approved_at": ("datetime", "YES"),
    "published_at": ("datetime", "YES"),
    "rejected_at": ("datetime", "YES"),
    "failed_at": ("datetime", "YES"),
}
COLUNAS_CATALOGO = {
    "card_generation_id": ("bigint", "YES"),
    "card_version": ("int", "YES"),
    "card_published_at": ("datetime", "YES"),
}


def _snapshot(path: Path = AURORA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria outra classe de erro e
    # `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_064).__globals__


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _create_sql() -> str:
    return _norm(_module()["CREATE_SQL"])


class _RecordingDB(SafetySnapshotDB):
    def __init__(self, snapshot, dry_run: bool = False):
        super().__init__(snapshot, dry_run=dry_run)
        self.calls: list[tuple[str, tuple]] = []
        self.executed: list[tuple[str, tuple | None]] = []

    def query(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        return super().query(sql, params)

    def execute(self, sql, params=None):
        self.executed.append((_norm(sql), params))


def _plan(snapshot, fn="plan"):
    db = _RecordingDB(snapshot)
    statements = _module()[fn](db)
    return [(_norm(sql), params) for sql, params in statements], db


def _add_columns(snap: dict, table: str, tipos: dict[str, tuple[str, str]], start: int = 1) -> None:
    for pos, (col, (tipo, nulo)) in enumerate(tipos.items(), start=start):
        snap["columns"].append(
            {"t": table, "pos": pos, "col": col, "type": tipo, "nullable": nulo, "dflt": None,
             "ckey": "PRI" if (table, col) == (TABLE, "id") else "", "extra": "", "coll": None}
        )


def _com_tabela(snapshot: dict, *, tipos: dict | None = None, indices: dict | None = None) -> dict:
    snap = copy.deepcopy(snapshot)
    snap["tables"].append({"table_name": TABLE, "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
    _add_columns(snap, TABLE, tipos if tipos is not None else TIPOS_CONVERGIDOS)
    snap["indexes"].append({"t": TABLE, "idx": "PRIMARY", "seq": 1, "col": "id", "non_unique": 0})
    for nome, (tipo, colunas) in (indices if indices is not None else INDICES).items():
        for seq, col in enumerate(colunas, start=1):
            snap["indexes"].append(
                {"t": TABLE, "idx": nome, "seq": seq, "col": col, "non_unique": 0 if tipo.startswith("UNIQUE") else 1}
            )
    return snap


def _com_colunas_catalogo(snapshot: dict, colunas: dict | None = None) -> dict:
    snap = copy.deepcopy(snapshot)
    _add_columns(snap, CATALOG, colunas if colunas is not None else COLUNAS_CATALOGO, start=90)
    return snap


def _convergido(snapshot: dict) -> dict:
    return _com_colunas_catalogo(_com_tabela(snapshot))


def _sem_coluna(snap: dict, table: str, col: str) -> dict:
    snap = copy.deepcopy(snap)
    snap["columns"] = [c for c in snap["columns"] if not (c["t"] == table and c["col"] == col)]
    return snap


def _trocar_coluna(snap: dict, table: str, col: str, **campos) -> dict:
    snap = copy.deepcopy(snap)
    for c in snap["columns"]:
        if c["t"] == table and c["col"] == col:
            c.update(campos)
    return snap


# ---------------------------------------------------------------------------
# Os recortes
# ---------------------------------------------------------------------------


@RECORTES
def test_recortes_tem_as_referencias_com_os_tipos_de_producao_e_nada_da_064(recorte):
    snap = _snapshot(recorte)
    assert snap["versao"][0]["mysql"].startswith("8.0")
    tabelas = {t["table_name"] for t in snap["tables"]}
    assert {CATALOG, "users"} <= tabelas
    assert TABLE not in tabelas
    ids = {(c["t"], c["col"]): (c["type"], c["ckey"]) for c in snap["columns"] if c["col"] == "id"}
    assert ids[(CATALOG, "id")] == ("bigint unsigned", "PRI")
    assert ids[("users", "id")] == ("bigint", "PRI")
    assert not [c for c in snap["columns"] if c["t"] == CATALOG and c["col"].startswith("card_")]


def test_nenhuma_migracao_anterior_cria_a_tabela_ou_as_colunas():
    """O que torna os recortes de setembro validos para hoje: nada entre a
    baseline e a 063 menciona a tabela nem as colunas `card_*`."""
    anteriores = [mig for mig in m.discover_migrations() if mig.version < 64]
    assert anteriores and anteriores[-1].version == 63
    for mig in anteriores:
        fonte = mig.path.read_text(encoding="utf-8", errors="ignore")
        assert TABLE not in fonte, mig.filename
        assert not re.search(r"\bcard_(generation_id|version|published_at)\b", fonte), mig.filename


def test_064_e_a_proxima_migracao_e_unica_no_numero():
    versoes = [mig.version for mig in m.discover_migrations()]
    assert versoes.count(64) == 1
    assert 63 in versoes


# ---------------------------------------------------------------------------
# O plano em producao
# ---------------------------------------------------------------------------


@RECORTES
def test_producao_hoje_cria_a_tabela_e_adiciona_as_tres_colunas(recorte):
    statements, db = _plan(_snapshot(recorte))
    assert statements == [
        (_create_sql(), None),
        (ADD_GENERATION_ID, None),
        (ADD_VERSION, None),
        (ADD_PUBLISHED_AT, None),
    ]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed, "plan() e somente leitura"


def test_o_plano_so_olha_a_tabela_nova_e_as_duas_referencias():
    _, db = _plan(_snapshot())
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {TABLE, CATALOG, "users"}


def test_up_executa_exatamente_o_plano_um_statement_por_chamada():
    db = _RecordingDB(_snapshot())
    _module()["up"](db)
    assert db.executed == [(_create_sql(), None), (ADD_GENERATION_ID, None), (ADD_VERSION, None), (ADD_PUBLISHED_AT, None)]
    for sql, _params in db.executed:
        assert ";" not in sql


@RECORTES
def test_reexecucao_no_estado_convergido_nao_escreve_nada(recorte):
    statements, db = _plan(_convergido(_snapshot(recorte)))
    assert statements == []
    db2 = _RecordingDB(_convergido(_snapshot(recorte)))
    _module()["up"](db2)
    assert db2.executed == []


def test_retoma_depois_de_falha_no_meio():
    """Tabela criada e uma coluna adicionada (deploy morreu no meio): a
    reexecucao so faz o que falta."""
    snap = _com_colunas_catalogo(_com_tabela(_snapshot()), {"card_generation_id": ("bigint", "YES")})
    statements, _ = _plan(snap)
    assert statements == [(ADD_VERSION, None), (ADD_PUBLISHED_AT, None)]


def test_colunas_presentes_e_tabela_ausente_so_cria_a_tabela():
    statements, _ = _plan(_com_colunas_catalogo(_snapshot()))
    assert statements == [(_create_sql(), None)]


def test_down_e_o_inverso_real():
    statements, _ = _plan(_convergido(_snapshot()), fn="plan_down")
    assert statements == [(DROP_PUBLISHED_AT, None), (DROP_VERSION, None), (DROP_GENERATION_ID, None), (DROP_TABLE, None)]
    statements, _ = _plan(_snapshot(), fn="plan_down")
    assert statements == []
    db = _RecordingDB(_convergido(_snapshot()))
    _module()["down"](db)
    assert [sql for sql, _ in db.executed] == [DROP_PUBLISHED_AT, DROP_VERSION, DROP_GENERATION_ID, DROP_TABLE]


# ---------------------------------------------------------------------------
# O DDL
# ---------------------------------------------------------------------------


def test_create_nao_tem_foreign_key_nem_references():
    """A 028 enumera as FKs validas para `exercise_catalog(id)` e recusa
    novas; `created_by`/`approved_by` ficam sem FK por decisao (o card e do
    catalogo, nao do admin)."""
    sql = _create_sql().upper()
    assert "FOREIGN KEY" not in sql
    assert "REFERENCES" not in sql
    assert "CONSTRAINT" not in sql
    todos = " ".join(_norm(v) for k, v in _module().items() if k.endswith("_SQL")).upper()
    assert "FOREIGN KEY" not in todos
    assert "REFERENCES" not in todos


def test_os_cinco_indices_por_nome():
    sql = _create_sql()
    for nome, (tipo, colunas) in INDICES.items():
        assert f"{tipo} {nome} ({', '.join(colunas)})" in sql, nome
    # Exatamente 5 indices secundarios (alem da PK), nenhum sem nome.
    declarados = re.findall(r"(?:UNIQUE )?KEY (\w+) \(", sql)
    assert sorted(declarados) == sorted(INDICES)
    assert sql.count("PRIMARY KEY") == 1
    assert _module()["EXPECTED_INDEXES"] == {
        nome: (colunas, tipo.startswith("UNIQUE")) for nome, (tipo, colunas) in INDICES.items()
    }


def test_colunas_do_create_sao_as_que_o_plano_confere():
    """`EXPECTED_COLUMNS` (o que a reexecucao confere) e o `CREATE` (o que a
    primeira execucao cria) nao podem divergir."""
    corpo = _module()["CREATE_SQL"].split("(", 1)[1]
    nomes = [
        linha.strip().split()[0]
        for linha in corpo.splitlines()
        if linha.strip() and not re.match(r"\s*(UNIQUE KEY|KEY|\))", linha)
    ]
    assert nomes == list(_module()["EXPECTED_COLUMNS"])
    assert list(TIPOS_CONVERGIDOS) == nomes


def test_ddl_identico_ao_do_plano_aprovado():
    sql = _create_sql()
    for trecho in (
        "id BIGINT AUTO_INCREMENT PRIMARY KEY",
        "exercise_id BIGINT UNSIGNED NOT NULL",
        "card_type VARCHAR(40) NOT NULL DEFAULT 'exercise_execution'",
        "status VARCHAR(16) NOT NULL",
        "version INT NULL",
        "parent_generation_id BIGINT NULL",
        "idempotency_key CHAR(36) NOT NULL",
        "job_id VARCHAR(36) NULL",
        "provider VARCHAR(20) NOT NULL DEFAULT 'openai'",
        "prompt_version VARCHAR(40) NOT NULL",
        "text_level VARCHAR(10) NOT NULL DEFAULT 'minimal'",
        "cost_usd DECIMAL(8,4) NULL",
        "created_by BIGINT NOT NULL",
        "approved_by BIGINT NULL",
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci",
    ):
        assert trecho in sql, trecho


def test_status_e_varchar_sem_enum_e_sem_coluna_gerada():
    sql = _create_sql().upper()
    assert "ENUM(" not in sql
    assert "GENERATED ALWAYS" not in sql
    assert " AS (" not in sql
    assert "VIRTUAL" not in sql and "STORED" not in sql


def test_referencias_logicas_tem_o_tipo_das_chaves_de_producao():
    snap = _snapshot()
    tipos = {(c["t"], c["col"]): c["type"] for c in snap["columns"]}
    esperado = _module()["EXPECTED_COLUMNS"]
    assert esperado["exercise_id"][0] == tipos[(CATALOG, "id")]
    assert esperado["created_by"][0] == tipos[("users", "id")]
    assert esperado["approved_by"][0] == tipos[("users", "id")]
    # O ponteiro do catalogo aponta para `exercise_card_generations.id` (BIGINT).
    assert _module()["CATALOG_EXPECTED_TYPES"]["card_generation_id"] == esperado["id"][0]


def test_colunas_do_catalogo_sao_anulaveis_sem_default_sem_after():
    """NULL = sem card. Coluna anulavel no fim da tabela = DDL instantaneo
    (ALGORITHM=INSTANT padrao do MySQL 8.0.29+, Aurora 3.08 = 8.0.39)."""
    for sql in (ADD_GENERATION_ID, ADD_VERSION, ADD_PUBLISHED_AT):
        assert sql in {_norm(v) for k, v in _module().items() if k.startswith("ADD_")}
        upper = sql.upper()
        assert upper.endswith(" NULL") and "NOT NULL" not in upper
        assert "DEFAULT" not in upper and "AFTER" not in upper and "FIRST" not in upper


def test_migracao_e_aditiva_sem_dml():
    fonte = MIGRATION_064.read_text(encoding="utf-8")
    codigo = fonte.split('"""', 2)[2]  # sem o docstring
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "PREPARE", "SET @", "USE "):
        assert proibido not in codigo.upper(), proibido


# ---------------------------------------------------------------------------
# Pre-condicoes: falham ANTES do primeiro statement
# ---------------------------------------------------------------------------


def _quebrar(alvo: str) -> dict:
    snap = _snapshot()
    if alvo == "catalogo_id_signed":
        return _trocar_coluna(snap, CATALOG, "id", type="bigint")
    if alvo == "catalogo_id_sem_pk":
        return _trocar_coluna(snap, CATALOG, "id", ckey="")
    if alvo == "catalogo_ausente":
        snap["tables"] = [t for t in snap["tables"] if t["table_name"] != CATALOG]
        return snap
    if alvo == "catalogo_myisam":
        for t in snap["tables"]:
            if t["table_name"] == CATALOG:
                t["engine"] = "MyISAM"
        return snap
    if alvo == "users_id_int":
        return _trocar_coluna(snap, "users", "id", type="int")
    if alvo == "users_id_sem_pk":
        return _trocar_coluna(snap, "users", "id", ckey="")
    if alvo == "users_ausente":
        snap["tables"] = [t for t in snap["tables"] if t["table_name"] != "users"]
        return snap
    if alvo == "users_sem_id":
        return _sem_coluna(snap, "users", "id")
    raise AssertionError(alvo)


@pytest.mark.parametrize(
    "alvo, trecho",
    [
        ("catalogo_id_signed", "exercise_catalog.id e bigint, esperado bigint unsigned"),
        ("catalogo_id_sem_pk", "exercise_catalog.id nao e PRIMARY KEY"),
        ("catalogo_ausente", "tabela exercise_catalog nao existe"),
        ("catalogo_myisam", "exercise_catalog usa ENGINE=MyISAM"),
        ("users_id_int", "users.id e int, esperado bigint"),
        ("users_id_sem_pk", "users.id nao e PRIMARY KEY"),
        ("users_ausente", "tabela users nao existe"),
        ("users_sem_id", "users.id nao existe"),
    ],
)
def test_precondicao_falha_antes_de_qualquer_statement(alvo, trecho):
    Erro = _module()["ExerciseCardGenerationsError"]
    db = _RecordingDB(_quebrar(alvo))
    with pytest.raises(Erro) as exc:
        _module()["up"](db)
    assert trecho in str(exc.value)
    assert "Nenhum statement executado" in str(exc.value)
    assert db.executed == []


@pytest.mark.parametrize(
    "snap_fn, trecho",
    [
        (lambda s: _com_tabela(s, tipos={**TIPOS_CONVERGIDOS, "exercise_id": ("bigint", "NO")}), "exercise_id e bigint"),
        (lambda s: _com_tabela(s, tipos={**TIPOS_CONVERGIDOS, "status": ("enum('PENDING')", "NO")}), "status e enum"),
        (lambda s: _com_tabela(s, tipos={**TIPOS_CONVERGIDOS, "version": ("int", "NO")}), "IS_NULLABLE"),
        (lambda s: _com_tabela(s, tipos={k: v for k, v in TIPOS_CONVERGIDOS.items() if k != "failed_at"}),
         "failed_at nao existe"),
        (lambda s: _com_tabela(s, indices={k: v for k, v in INDICES.items() if k != "uq_ecg_exercise_idem"}),
         "uq_ecg_exercise_idem nao existe"),
        (lambda s: _com_tabela(s, indices={**INDICES, "uq_ecg_exercise_version": ("KEY", ("exercise_id", "version"))}),
         "deveria ser UNIQUE"),
        (lambda s: _com_tabela(s, indices={**INDICES, "idx_ecg_status_started": ("KEY", ("started_at", "status"))}),
         "idx_ecg_status_started cobre"),
    ],
)
def test_tabela_presente_em_outro_formato_falha_antes_de_escrever(snap_fn, trecho):
    Erro = _module()["ExerciseCardGenerationsError"]
    db = _RecordingDB(_com_colunas_catalogo(snap_fn(_snapshot()), {}))
    with pytest.raises(Erro) as exc:
        _module()["up"](db)
    assert trecho in str(exc.value)
    assert db.executed == []


@pytest.mark.parametrize(
    "colunas, trecho",
    [
        ({**COLUNAS_CATALOGO, "card_version": ("bigint", "YES")}, "card_version e bigint, esperado int"),
        ({**COLUNAS_CATALOGO, "card_published_at": ("timestamp", "YES")}, "esperado datetime"),
        ({**COLUNAS_CATALOGO, "card_generation_id": ("bigint", "NO")}, "IS_NULLABLE=NO"),
    ],
)
def test_coluna_do_catalogo_em_outro_formato_falha_antes_do_create(colunas, trecho):
    """A validacao das colunas vem ANTES de qualquer statement: mesmo com a
    tabela ausente (CREATE pendente), nada e executado."""
    Erro = _module()["ExerciseCardGenerationsError"]
    db = _RecordingDB(_com_colunas_catalogo(_snapshot(), colunas))
    with pytest.raises(Erro) as exc:
        _module()["up"](db)
    assert trecho in str(exc.value)
    assert db.executed == []
