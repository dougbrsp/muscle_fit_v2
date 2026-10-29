"""Plano da migracao 040 (unidade de carga preferida) contra o
`information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_19_038.json` ja
traz `users` COMPLETA, lida em 2026-09-19 por SELECT somente leitura (ver
`_origem` do arquivo). Nenhuma leitura nova da AWS foi feita para esta
migracao - e nenhuma e necessaria, porque nem a 038 nem a 039 tocam `users`.

A 039 (`039_descricao_execucao_exercicio.py`, colunas aditivas em
`exercise_catalog`) entra ANTES desta e foi o motivo da renumeracao 039 -> 040.
As assercoes abaixo olham EXCLUSIVAMENTE `users`, entao o estado de
`exercise_catalog` depois da 039 e irrelevante aqui - e o `plan()` desta
migracao so consulta `users`, o que `test_producao_hoje_executa_um_unico_statement`
verifica lendo as queries emitidas.

Producao hoje NAO tem `users.preferred_weight_unit` -> 1 statement.
Estado convergido -> 0 statements.
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_040 = m.DEFAULT_MIGRATIONS_DIR / "040_preferencia_unidade_carga.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_19_038.json"

ALTER = (
    "ALTER TABLE users ADD COLUMN preferred_weight_unit "
    "ENUM('kg','lb') NOT NULL DEFAULT 'kg' AFTER is_advanced_user"
)


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria uma classe de erro nova a cada
    # chamada e `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_040).__globals__


class _RecordingDB(SafetySnapshotDB):
    """`SafetySnapshotDB` que tambem guarda os PARAMETROS de cada consulta -
    e neles que vai o nome da tabela pedida."""

    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.calls: list[tuple[str, tuple]] = []

    def query(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        return super().query(sql, params)


def _plan(snapshot):
    db = _RecordingDB(snapshot)
    statements = _module()["plan"](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def _com_a_coluna(snapshot: dict, **overrides) -> dict:
    """Recorte com a 040 JA aplicada (estado pos-deploy)."""
    snap = copy.deepcopy(snapshot)
    users = [c for c in snap["columns"] if c["t"] == "users"]
    ancora = next(c for c in users if c["col"] == "is_advanced_user")
    nova = {
        "t": "users",
        "pos": ancora["pos"] + 1,
        "col": "preferred_weight_unit",
        "type": "enum('kg','lb')",
        "nullable": "NO",
        "dflt": "kg",
        "ckey": "",
        "extra": "",
        "coll": "utf8mb4_0900_ai_ci",
    }
    nova.update(overrides)
    for coluna in users:
        if coluna["pos"] > ancora["pos"]:
            coluna["pos"] += 1
    snap["columns"].append(nova)
    return snap


def test_o_recorte_e_da_aurora_e_users_ainda_nao_tem_a_coluna():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    engines = {t["table_name"]: t["engine"] for t in snap["tables"]}
    assert engines["users"] == "InnoDB"
    colunas = {c["col"] for c in snap["columns"] if c["t"] == "users"}
    assert "preferred_weight_unit" not in colunas
    assert "is_advanced_user" in colunas  # ancora do AFTER


def test_producao_hoje_executa_um_unico_statement():
    statements, db = _plan(_snapshot())
    assert statements == [(ALTER, None)]
    # o plano so LE: nenhum SELECT de dados, nenhuma escrita
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)


def test_o_plano_so_olha_a_tabela_users():
    """Garante que esta migracao e independente da 039 (que mexe em
    `exercise_catalog`): nenhuma consulta dela pergunta por outra tabela, entao
    a ordem entre as duas nao importa."""
    _, db = _plan(_snapshot())
    assert db.calls, "o plano precisa ler o information_schema"
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {"users"}


def test_a_coluna_nasce_com_default_kg_e_nao_muda_ninguem():
    """Toda linha ja gravada passa a dizer 'kg', que e exatamente o que o
    sistema faz hoje - o deploy nao altera o comportamento de nenhum usuario."""
    assert "NOT NULL DEFAULT 'kg'" in ALTER
    assert _module()["EXPECTED_DEFAULT"] == "kg"
    assert _module()["EXPECTED_NULLABLE"] == "NO"


def test_estado_convergido_nao_executa_nada():
    statements, _ = _plan(_com_a_coluna(_snapshot()))
    assert statements == []


def test_reexecucao_apos_aplicar_e_zero_statements():
    snap = _snapshot()
    primeira, _ = _plan(snap)
    assert len(primeira) == 1
    segunda, _ = _plan(_com_a_coluna(snap))
    assert segunda == []


@pytest.mark.parametrize(
    "override",
    [
        {"type": "varchar(2)"},
        {"nullable": "YES"},
        {"dflt": None},
        {"dflt": "lb"},
        {"type": "enum('kg','lb','st')"},
    ],
)
def test_coluna_divergente_falha_antes_de_qualquer_statement(override):
    erro = _module()["PreferenciaUnidadeCargaError"]
    with pytest.raises(erro) as exc:
        _plan(_com_a_coluna(_snapshot(), **override))
    assert "Nenhum statement executado" in str(exc.value)


def test_tabela_ausente_ou_com_engine_errada_falha():
    erro = _module()["PreferenciaUnidadeCargaError"]

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


def test_ancora_do_after_ausente_falha_antes_do_alter():
    erro = _module()["PreferenciaUnidadeCargaError"]
    sem_ancora = copy.deepcopy(_snapshot())
    sem_ancora["columns"] = [
        c for c in sem_ancora["columns"] if not (c["t"] == "users" and c["col"] == "is_advanced_user")
    ]
    with pytest.raises(erro) as exc:
        _plan(sem_ancora)
    assert "ancora" in str(exc.value)


def test_migracao_e_autocontida_e_compativel_com_o_data_api():
    source = MIGRATION_040.read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
    # Um statement por chamada e nada que dependa de estado de sessao: o
    # `plan` devolve SQL sem `;`, `PREPARE`, `SET @` ou `USE`.
    statements, _ = _plan(_snapshot())
    for sql, _params in statements:
        assert ";" not in sql
        upper = sql.upper()
        for proibido in ("PREPARE", "EXECUTE", "DEALLOCATE", "DELIMITER", "SET @", "USE "):
            assert proibido not in upper


def test_o_numero_040_e_unico_no_diretorio():
    """Renumerada de 039 para 040 por colisao com a migracao do M4, entregue
    em paralelo. O runner RECUSA versao duplicada (`discover_migrations`), entao
    o que este teste protege e a unicidade - nao "ser a ultima", que envelhece a
    cada migracao nova."""
    migracoes = m.discover_migrations(m.DEFAULT_MIGRATIONS_DIR)
    por_versao = {mig.version: mig.filename for mig in migracoes}
    assert por_versao[40] == MIGRATION_040.name
    assert [mig.version for mig in migracoes if mig.version == 40] == [40]
    assert 40 > m.BASELINE_MAX_VERSION
