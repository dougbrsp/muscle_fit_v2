"""Plano da migracao 045 (`equipment_exercises.load_convention`, Novo
Dashboard F0.2) contra o `information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_17_035.json`
traz `equipment_exercises` COMPLETA (SELECT somente leitura, ver `_origem`).
Nenhuma leitura nova da AWS foi feita - e nenhuma e necessaria: a 045 so olha
`equipment_exercises`, e as colunas que aquele recorte nao tem (a 032
acrescentou `max_safe_rpe_override` e `safety_notes`) sao irrelevantes para o
plano, porque a 045 NAO usa `AFTER` e so pergunta por `load_convention`.

Producao hoje NAO tem `equipment_exercises.load_convention` -> 1 statement.
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

MIGRATION_045 = m.DEFAULT_MIGRATIONS_DIR / "045_equipment_exercises_load_convention.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"

ENUM_SQL = "enum('total','per_side','per_dumbbell','assisted','bodyweight','bodyweight_plus')"
ALTER = (
    "ALTER TABLE equipment_exercises ADD COLUMN load_convention "
    "ENUM('total','per_side','per_dumbbell','assisted','bodyweight','bodyweight_plus') NULL"
)
DROP = "ALTER TABLE equipment_exercises DROP COLUMN load_convention"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria uma classe de erro nova a cada
    # chamada e `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_045).__globals__


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
    """Recorte com a 045 JA aplicada (estado pos-deploy): coluna no fim."""
    snap = copy.deepcopy(snapshot)
    linhas = [c for c in snap["columns"] if c["t"] == "equipment_exercises"]
    nova = {
        "t": "equipment_exercises",
        "pos": max(c["pos"] for c in linhas) + 1,
        "col": "load_convention",
        "type": ENUM_SQL,
        "nullable": "YES",
        "dflt": None,
        "ckey": "",
        "extra": "",
        "coll": "utf8mb4_0900_ai_ci",
    }
    nova.update(overrides)
    snap["columns"].append(nova)
    return snap


def test_o_recorte_e_da_aurora_e_a_tabela_ainda_nao_tem_a_coluna():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    engines = {t["table_name"]: t["engine"] for t in snap["tables"]}
    assert engines["equipment_exercises"] == "InnoDB"
    colunas = {c["col"] for c in snap["columns"] if c["t"] == "equipment_exercises"}
    assert "load_convention" not in colunas


def test_producao_hoje_executa_um_unico_statement():
    statements, db = _plan(_snapshot())
    assert statements == [(ALTER, None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)


def test_o_plano_so_olha_a_tabela_equipment_exercises():
    _, db = _plan(_snapshot())
    assert db.calls, "o plano precisa ler o information_schema"
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {"equipment_exercises"}


def test_a_coluna_nasce_nula_e_sem_backfill():
    """NULL = `total` (D12): nenhuma linha existente muda de significado e
    nenhum UPDATE de dados acontece."""
    assert ALTER.endswith("NULL")
    assert "DEFAULT" not in ALTER.upper()
    assert _module()["EXPECTED_NULLABLE"] == "YES"
    fonte = MIGRATION_045.read_text(encoding="utf-8")
    for proibido in ("UPDATE ", "INSERT ", "DELETE "):
        assert proibido not in fonte.upper().replace("UPDATE CURRENT_TIMESTAMP", "")


def test_sem_after_a_migracao_nao_depende_da_032():
    assert "AFTER" not in ALTER.upper()
    colunas = {c["col"] for c in _snapshot()["columns"] if c["t"] == "equipment_exercises"}
    # o recorte e anterior a 032 (nao tem as colunas de seguranca) e ainda assim o plano fecha
    assert "max_safe_rpe_override" not in colunas
    statements, _ = _plan(_snapshot())
    assert statements == [(ALTER, None)]


def test_o_vocabulario_do_enum_e_o_mesmo_do_codigo():
    """O enum do banco e o `LoadConvention` do dashboard sao espelhos: se um
    ganhar um valor novo sem o outro, a comparabilidade quebra em silencio."""
    from app.load_conventions import LoadConvention

    assert _module()["LOAD_CONVENTIONS"] == tuple(item.value for item in LoadConvention)


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
    statements, _ = _plan(_com_a_coluna(_snapshot()), fn="plan_down")
    assert statements == [(DROP, None)]
    statements, _ = _plan(_snapshot(), fn="plan_down")
    assert statements == []

    db = _RecordingDB(_com_a_coluna(_snapshot()))
    _module()["down"](db)
    assert db.executed == [(DROP, None)]


@pytest.mark.parametrize(
    "override",
    [
        {"type": "varchar(20)"},
        {"type": "enum('total','per_side')"},
        {"nullable": "NO"},
        {"dflt": "total"},
    ],
)
def test_coluna_divergente_falha_antes_de_qualquer_statement(override):
    erro = _module()["LoadConventionError"]
    with pytest.raises(erro) as exc:
        _plan(_com_a_coluna(_snapshot(), **override))
    assert "Nenhum statement executado" in str(exc.value)


def test_tabela_ausente_ou_com_engine_errada_falha():
    erro = _module()["LoadConventionError"]

    sem_tabela = copy.deepcopy(_snapshot())
    sem_tabela["tables"] = [t for t in sem_tabela["tables"] if t["table_name"] != "equipment_exercises"]
    with pytest.raises(erro):
        _plan(sem_tabela)

    myisam = copy.deepcopy(_snapshot())
    for t in myisam["tables"]:
        if t["table_name"] == "equipment_exercises":
            t["engine"] = "MyISAM"
    with pytest.raises(erro):
        _plan(myisam)


def test_migracao_e_autocontida_e_compativel_com_o_data_api():
    source = MIGRATION_045.read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
    for fn in ("plan", "plan_down"):
        statements, _ = _plan(_com_a_coluna(_snapshot()) if fn == "plan_down" else _snapshot(), fn=fn)
        for sql, _params in statements:
            assert ";" not in sql
            upper = sql.upper()
            for proibido in ("PREPARE", "EXECUTE", "DEALLOCATE", "DELIMITER", "SET @", "USE "):
                assert proibido not in upper


def test_o_numero_045_e_unico_no_diretorio():
    migracoes = m.discover_migrations(m.DEFAULT_MIGRATIONS_DIR)
    por_versao = {mig.version: mig.filename for mig in migracoes}
    assert por_versao[45] == MIGRATION_045.name
    assert [mig.version for mig in migracoes if mig.version == 45] == [45]
    assert 45 > m.BASELINE_MAX_VERSION
