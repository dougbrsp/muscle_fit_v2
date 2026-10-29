"""Plano da migracao 048 (`context_events`, Novo Dashboard F2.1) contra o
`information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_17_035.json`
traz a lista COMPLETA de tabelas de producao e as colunas/indices/FKs de
`users` (SELECT somente leitura). Nenhuma leitura nova da AWS foi feita - e
nenhuma e necessaria: a 048 so pergunta se `context_events` existe e se
`users.id` e um BIGINT PK InnoDB (a FK).

Producao hoje NAO tem `context_events` -> 1 `CREATE TABLE`.
Estado convergido -> 0 statements. `down` -> 1 `DROP TABLE` (reversivel de
verdade: a tabela nasce vazia e nada a referencia).

Rodar: cd api && python -m pytest tests/unit/test_migration_048_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_048 = m.DEFAULT_MIGRATIONS_DIR / "048_context_events.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"

TYPES = (
    "alcohol",
    "travel",
    "illness",
    "atypical_sleep",
    "gym_change",
    "plan_change",
    "diet_phase",
    "supplement",
    "injury_pain",
    "other",
)
SOURCES = ("manual", "derived")
TYPE_ENUM_SQL = "enum(" + ",".join(f"'{t}'" for t in TYPES) + ")"
SOURCE_ENUM_SQL = "enum(" + ",".join(f"'{s}'" for s in SOURCES) + ")"
DROP = "DROP TABLE IF EXISTS context_events"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria uma classe de erro nova a cada
    # chamada e `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_048).__globals__


def _create_sql() -> str:
    return " ".join(_module()["CREATE_SQL"].split())


class _RecordingDB(SafetySnapshotDB):
    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.calls: list[tuple[str, tuple]] = []
        self.executed: list[tuple[str, tuple | None]] = []

    def query(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        return super().query(sql, params)

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))


def _plan(snapshot, fn="plan"):
    db = _RecordingDB(snapshot)
    statements = _module()[fn](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def _com_a_tabela(snapshot: dict, **overrides) -> dict:
    """Recorte com a 048 JA aplicada (estado pos-deploy)."""
    snap = copy.deepcopy(snapshot)
    snap["tables"].append({"table_name": "context_events", "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
    tipos = {
        "id": ("bigint", "NO"),
        "user_id": ("bigint", "NO"),
        "event_type": (TYPE_ENUM_SQL, "NO"),
        "local_date": ("date", "NO"),
        "end_local_date": ("date", "YES"),
        "intensity": ("tinyint(4)", "YES"),  # MySQL 8 ainda mostra a largura
        "notes": ("varchar(255)", "YES"),
        "source": (SOURCE_ENUM_SQL, "NO"),
        "created_at": ("datetime", "NO"),
        "updated_at": ("datetime", "NO"),
    }
    tipos.update(overrides)
    for pos, (col, (tipo, nulo)) in enumerate(tipos.items(), start=1):
        snap["columns"].append(
            {
                "t": "context_events",
                "pos": pos,
                "col": col,
                "type": tipo,
                "nullable": nulo,
                "dflt": None,
                "ckey": "PRI" if col == "id" else "",
                "extra": "",
                "coll": None,
            }
        )
    return snap


def test_o_recorte_e_da_aurora_e_nao_tem_a_tabela():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    tabelas = {t["table_name"] for t in snap["tables"]}
    assert "users" in tabelas
    assert "context_events" not in tabelas


def test_producao_hoje_executa_um_unico_create_table():
    statements, db = _plan(_snapshot())
    assert statements == [(_create_sql(), None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed, "plan() e somente leitura"


def test_o_plano_so_olha_context_events_e_users():
    _, db = _plan(_snapshot())
    assert db.calls, "o plano precisa ler o information_schema"
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {"context_events", "users"}


def test_ddl_tem_indice_fk_e_os_dois_enums():
    sql = _create_sql()
    assert "KEY idx_ce_user_date (user_id, local_date)" in sql
    assert "CONSTRAINT fk_ce_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in sql
    assert "event_type ENUM(" + ",".join(f"'{t}'" for t in TYPES) + ") NOT NULL" in sql
    assert "source ENUM(" + ",".join(f"'{s}'" for s in SOURCES) + ") NOT NULL DEFAULT 'manual'" in sql
    assert "end_local_date DATE NULL" in sql
    assert "intensity TINYINT NULL" in sql
    assert "ENGINE=InnoDB" in sql
    assert _module()["EVENT_TYPES"] == TYPES


def test_sem_unique_de_proposito():
    """Dois eventos do MESMO tipo no MESMO dia sao legitimos (duas dores
    diferentes, dois voos) - ao contrario da 047, onde "cintura do dia" e
    unica por definicao. Por isso o POST e INSERT, nao upsert."""
    assert "UNIQUE" not in _create_sql().upper()


def test_enum_do_banco_espelha_o_vocabulario_da_api():
    from app.context_events_schema import EVENT_SOURCES, EVENT_TYPES

    assert tuple(EVENT_TYPES) == _module()["EVENT_TYPES"]
    assert tuple(EVENT_SOURCES) == _module()["EVENT_SOURCES"]


def test_todo_tipo_tem_rotulo_em_portugues():
    from app.context_events_schema import EVENT_TYPE_LABELS, EVENT_TYPES

    assert set(EVENT_TYPE_LABELS) == set(EVENT_TYPES)


def test_migracao_e_aditiva_sem_dml():
    fonte = MIGRATION_048.read_text(encoding="utf-8")
    # `ON UPDATE CURRENT_TIMESTAMP` e `ON DELETE CASCADE` sao DDL, nao DML.
    so_dml = fonte.upper().replace("UPDATE CURRENT_TIMESTAMP", "").replace("ON DELETE CASCADE", "")
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "ALTER TABLE"):
        assert proibido not in so_dml


def test_reexecucao_no_estado_convergido_nao_escreve_nada():
    statements, db = _plan(_com_a_tabela(_snapshot()))
    assert statements == []
    assert not db.executed


def test_up_executa_exatamente_o_plano():
    db = _RecordingDB(_snapshot())
    _module()["up"](db)
    assert db.executed == [(_create_sql(), None)]


def test_down_e_o_inverso_real():
    statements, _ = _plan(_com_a_tabela(_snapshot()), fn="plan_down")
    assert statements == [(DROP, None)]
    statements, _ = _plan(_snapshot(), fn="plan_down")
    assert statements == []
    db = _RecordingDB(_com_a_tabela(_snapshot()))
    _module()["down"](db)
    assert db.executed == [(DROP, None)]


@pytest.mark.parametrize(
    "overrides, trecho",
    [
        ({"event_type": ("varchar(40)", "NO")}, "event_type"),
        ({"intensity": ("int(11)", "YES")}, "intensity"),
        ({"end_local_date": ("date", "NO")}, "IS_NULLABLE"),
        ({"source": ("varchar(20)", "NO")}, "source"),
    ],
)
def test_tabela_presente_em_outro_formato_falha_antes_de_escrever(overrides, trecho):
    Erro = _module()["ContextEventsError"]
    db = _RecordingDB(_com_a_tabela(_snapshot(), **overrides))
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert trecho in str(exc.value)
    assert not db.executed


def test_coluna_faltando_na_tabela_existente_falha():
    Erro = _module()["ContextEventsError"]
    snap = _com_a_tabela(_snapshot())
    snap["columns"] = [c for c in snap["columns"] if not (c["t"] == "context_events" and c["col"] == "source")]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "context_events.source nao existe" in str(exc.value)


def test_users_incompativel_falha_inclusive_em_dry_run():
    Erro = _module()["ContextEventsError"]
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != "users"]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "users nao existe" in str(exc.value)
