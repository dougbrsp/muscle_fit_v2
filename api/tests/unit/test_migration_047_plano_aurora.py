"""Plano da migracao 047 (`body_measurements`, Novo Dashboard F1.4) contra o
`information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_17_035.json`
traz a lista COMPLETA de tabelas de producao e as colunas/indices/FKs de
`users` (SELECT somente leitura, ver `_origem`). Nenhuma leitura nova da AWS
foi feita - e nenhuma e necessaria: a 047 so pergunta se `body_measurements`
existe e se `users.id` e um BIGINT PK InnoDB (a FK).

Producao hoje NAO tem `body_measurements` -> 1 `CREATE TABLE`.
Estado convergido -> 0 statements. `down` -> 1 `DROP TABLE` (reversivel de
verdade: a tabela nasce vazia e nada a referencia).

Rodar: cd api && python -m pytest tests/unit/test_migration_047_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_047 = m.DEFAULT_MIGRATIONS_DIR / "047_body_measurements.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"

SITES = ("waist", "hip", "chest", "arm_l", "arm_r", "thigh_l", "thigh_r", "calf_l", "calf_r")
ENUM_SQL = "enum(" + ",".join(f"'{s}'" for s in SITES) + ")"
DROP = "DROP TABLE IF EXISTS body_measurements"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria uma classe de erro nova a cada
    # chamada e `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_047).__globals__


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
    """Recorte com a 047 JA aplicada (estado pos-deploy)."""
    snap = copy.deepcopy(snapshot)
    snap["tables"].append({"table_name": "body_measurements", "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
    tipos = {
        "id": ("bigint", "NO"),
        "user_id": ("bigint", "NO"),
        "local_date": ("date", "NO"),
        "site": (ENUM_SQL, "NO"),
        "value_cm": ("decimal(5,1)", "NO"),
        "protocol_ok": ("tinyint(1)", "NO"),  # MySQL 8 ainda mostra a largura do tinyint(1)
        "notes": ("varchar(255)", "YES"),
        "source": ("varchar(40)", "NO"),
        "created_at": ("datetime", "NO"),
        "updated_at": ("datetime", "NO"),
    }
    tipos.update(overrides)
    for pos, (col, (tipo, nulo)) in enumerate(tipos.items(), start=1):
        snap["columns"].append(
            {
                "t": "body_measurements",
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
    assert "body_measurements" not in tabelas


def test_producao_hoje_executa_um_unico_create_table():
    statements, db = _plan(_snapshot())
    assert statements == [(_create_sql(), None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed, "plan() e somente leitura"


def test_o_plano_so_olha_body_measurements_e_users():
    _, db = _plan(_snapshot())
    assert db.calls, "o plano precisa ler o information_schema"
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {"body_measurements", "users"}


def test_ddl_tem_unique_indice_fk_e_enum_de_locais():
    sql = _create_sql()
    assert "UNIQUE KEY uq_bm_user_date_site (user_id, local_date, site)" in sql
    assert "KEY idx_bm_user_site_date (user_id, site, local_date)" in sql
    assert "CONSTRAINT fk_bm_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in sql
    assert "site ENUM(" + ",".join(f"'{s}'" for s in SITES) + ") NOT NULL" in sql
    assert "value_cm DECIMAL(5,1) NOT NULL" in sql
    assert "protocol_ok TINYINT(1) NOT NULL DEFAULT 1" in sql
    assert "source VARCHAR(40) NOT NULL DEFAULT 'manual'" in sql
    assert "ENGINE=InnoDB" in sql
    assert _module()["MEASUREMENT_SITES"] == SITES


def test_enum_do_banco_espelha_o_vocabulario_da_api():
    # Desde a 075 (nutrição L0) o vocabulário completo tem dono em
    # `domain/body_measurement_sites.py` e o teste da 075 compara com ele; a
    # tupla da 047 é o PREFIXO preservado (os locais novos entram no fim).
    from app.body_measurements import MEASUREMENT_SITES
    from app.domain.body_measurement_sites import SITES_047

    assert _module()["MEASUREMENT_SITES"] == SITES_047
    assert tuple(MEASUREMENT_SITES)[: len(SITES_047)] == _module()["MEASUREMENT_SITES"]


def test_migracao_e_aditiva_sem_dml():
    fonte = MIGRATION_047.read_text(encoding="utf-8")
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
        ({"value_cm": ("decimal(6,2)", "NO")}, "value_cm"),
        ({"site": ("varchar(20)", "NO")}, "site"),
        ({"notes": ("varchar(255)", "NO")}, "IS_NULLABLE"),
    ],
)
def test_tabela_presente_em_outro_formato_falha_antes_de_escrever(overrides, trecho):
    Erro = _module()["BodyMeasurementsError"]
    db = _RecordingDB(_com_a_tabela(_snapshot(), **overrides))
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert trecho in str(exc.value)
    assert not db.executed


def test_coluna_faltando_na_tabela_existente_falha():
    Erro = _module()["BodyMeasurementsError"]
    snap = _com_a_tabela(_snapshot())
    snap["columns"] = [c for c in snap["columns"] if not (c["t"] == "body_measurements" and c["col"] == "source")]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "body_measurements.source nao existe" in str(exc.value)


def test_users_incompativel_falha_inclusive_em_dry_run():
    Erro = _module()["BodyMeasurementsError"]
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != "users"]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "users nao existe" in str(exc.value)
