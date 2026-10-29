"""Plano da migracao 044 (`users.timezone`, Novo Dashboard F0.1) contra o
`information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_19_038.json`
traz `users` COMPLETA (SELECT somente leitura, ver `_origem`). Nenhuma
leitura nova da AWS foi feita - e nenhuma e necessaria: a 044 so olha
`users`, e as migracoes 039-043 nao mudam nada que ela leia (040 adiciona
`preferred_weight_unit`, mas a 044 nao usa `AFTER`, entao a presenca ou
ausencia dessa coluna e irrelevante para o plano).

Producao hoje NAO tem `users.timezone` -> 1 statement.
Estado convergido -> 0 statements. `down` -> 1 DROP COLUMN (reversivel).
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_044 = m.DEFAULT_MIGRATIONS_DIR / "044_users_timezone.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_19_038.json"

ALTER = "ALTER TABLE users ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'America/Sao_Paulo'"
DROP = "ALTER TABLE users DROP COLUMN timezone"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria uma classe de erro nova a cada
    # chamada e `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_044).__globals__


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


def _com_a_coluna(snapshot: dict, **overrides) -> dict:
    """Recorte com a 044 JA aplicada (estado pos-deploy): coluna no fim."""
    snap = copy.deepcopy(snapshot)
    users = [c for c in snap["columns"] if c["t"] == "users"]
    nova = {
        "t": "users",
        "pos": max(c["pos"] for c in users) + 1,
        "col": "timezone",
        "type": "varchar(64)",
        "nullable": "NO",
        "dflt": "America/Sao_Paulo",
        "ckey": "",
        "extra": "",
        "coll": "utf8mb4_0900_ai_ci",
    }
    nova.update(overrides)
    snap["columns"].append(nova)
    return snap


def test_o_recorte_e_da_aurora_e_users_ainda_nao_tem_a_coluna():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    engines = {t["table_name"]: t["engine"] for t in snap["tables"]}
    assert engines["users"] == "InnoDB"
    colunas = {c["col"] for c in snap["columns"] if c["t"] == "users"}
    assert "timezone" not in colunas


def test_producao_hoje_executa_um_unico_statement():
    statements, db = _plan(_snapshot())
    assert statements == [(ALTER, None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)


def test_o_plano_so_olha_a_tabela_users():
    """Independente das 039-043: nenhuma consulta pergunta por outra tabela."""
    _, db = _plan(_snapshot())
    assert db.calls, "o plano precisa ler o information_schema"
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {"users"}


def test_a_coluna_nasce_com_default_sao_paulo_e_nao_muda_ninguem():
    assert "NOT NULL DEFAULT 'America/Sao_Paulo'" in ALTER
    assert _module()["EXPECTED_DEFAULT"] == "America/Sao_Paulo"
    assert _module()["EXPECTED_NULLABLE"] == "NO"
    # o default da migracao e o mesmo que a aplicacao usa como fallback
    from app.timezones import DEFAULT_TIMEZONE

    assert _module()["DEFAULT_TIMEZONE"] == DEFAULT_TIMEZONE


def test_sem_after_a_migracao_nao_depende_da_040():
    assert "AFTER" not in ALTER.upper()
    snap = _snapshot()
    assert "preferred_weight_unit" not in {c["col"] for c in snap["columns"] if c["t"] == "users"}
    statements, _ = _plan(snap)
    assert statements == [(ALTER, None)]


def test_estado_convergido_nao_executa_nada():
    statements, _ = _plan(_com_a_coluna(_snapshot()))
    assert statements == []


def test_reexecucao_apos_aplicar_e_zero_statements():
    snap = _snapshot()
    primeira, _ = _plan(snap)
    assert len(primeira) == 1
    segunda, _ = _plan(_com_a_coluna(snap))
    assert segunda == []


def test_up_executa_exatamente_o_plano():
    db = _RecordingDB(_snapshot())
    _module()["up"](db)
    assert db.executed == [(ALTER, None)]


def test_down_e_o_inverso_e_idempotente():
    # coluna presente -> 1 DROP
    statements, _ = _plan(_com_a_coluna(_snapshot()), fn="plan_down")
    assert statements == [(DROP, None)]
    # coluna ausente (producao hoje ou ja revertida) -> nada
    statements, _ = _plan(_snapshot(), fn="plan_down")
    assert statements == []

    db = _RecordingDB(_com_a_coluna(_snapshot()))
    _module()["down"](db)
    assert db.executed == [(DROP, None)]


@pytest.mark.parametrize(
    "override",
    [
        {"type": "varchar(32)"},
        {"type": "enum('America/Sao_Paulo')"},
        {"nullable": "YES"},
        {"dflt": None},
        {"dflt": "UTC"},
    ],
)
def test_coluna_divergente_falha_antes_de_qualquer_statement(override):
    erro = _module()["UsersTimezoneError"]
    with pytest.raises(erro) as exc:
        _plan(_com_a_coluna(_snapshot(), **override))
    assert "Nenhum statement executado" in str(exc.value)


def test_tabela_ausente_ou_com_engine_errada_falha():
    erro = _module()["UsersTimezoneError"]

    sem_users = copy.deepcopy(_snapshot())
    sem_users["tables"] = [t for t in sem_users["tables"] if t["table_name"] != "users"]
    with pytest.raises(erro):
        _plan(sem_users)

    myisam = copy.deepcopy(_snapshot())
    for t in myisam["tables"]:
        if t["table_name"] == "users":
            t["engine"] = "MyISAM"
    with pytest.raises(erro):
        _plan(myisam)


def test_migracao_e_autocontida_e_compativel_com_o_data_api():
    source = MIGRATION_044.read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
    for fn in ("plan", "plan_down"):
        statements, _ = _plan(_com_a_coluna(_snapshot()) if fn == "plan_down" else _snapshot(), fn=fn)
        for sql, _params in statements:
            assert ";" not in sql
            upper = sql.upper()
            for proibido in ("PREPARE", "EXECUTE", "DEALLOCATE", "DELIMITER", "SET @", "USE "):
                assert proibido not in upper


def test_o_numero_044_e_unico_no_diretorio():
    migracoes = m.discover_migrations(m.DEFAULT_MIGRATIONS_DIR)
    por_versao = {mig.version: mig.filename for mig in migracoes}
    assert por_versao[44] == MIGRATION_044.name
    assert [mig.version for mig in migracoes if mig.version == 44] == [44]
    assert 44 > m.BASELINE_MAX_VERSION
