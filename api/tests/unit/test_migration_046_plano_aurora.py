"""Plano da migracao 046 (reverter `load_convention` 'per_side'/'per_dumbbell'
para 'total') contra o `information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_17_035.json`
traz `equipment_exercises` COMPLETA (SELECT somente leitura, ver `_origem`).
Nenhuma leitura nova da AWS foi feita. Aquele recorte e ANTERIOR a 045, entao
ele modela aqui os dois estados que importam:

- como esta no arquivo (sem `load_convention`): banco anterior a 045 -> 0
  statements, no-op com aviso, sem erro;
- com a coluna acrescentada por `_com_a_coluna` (estado de producao HOJE, pos
  deploy da 045): 1 `UPDATE`.

O plano nao le dados, so `information_schema` - por isso o `UPDATE` aparece
sempre que a coluna existe. A idempotencia real e do `WHERE` (depois da
primeira execucao nao ha linha 'per_side'/'per_dumbbell') somada ao runner,
que registra a versao em `schema_migration_files` e nunca reexecuta o arquivo.
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_046 = m.DEFAULT_MIGRATIONS_DIR / "046_reverter_convencao_carga_para_total.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"

ENUM_SQL = "enum('total','per_side','per_dumbbell','assisted','bodyweight','bodyweight_plus')"
UPDATE = (
    "UPDATE equipment_exercises SET load_convention = 'total' "
    "WHERE load_convention IN ('per_side','per_dumbbell')"
)


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria uma classe de erro nova a cada
    # chamada e `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_046).__globals__


class _RecordingDB(SafetySnapshotDB):
    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.calls: list[tuple[str, tuple]] = []
        self.executed: list[tuple[str, tuple | None]] = []
        self.rowcount = 3

    def query(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        return super().query(sql, params)

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        return self.rowcount


def _plan(snapshot, fn="plan"):
    db = _RecordingDB(snapshot)
    statements = _module()[fn](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def _com_a_coluna(snapshot: dict, **overrides) -> dict:
    """Recorte com a 045 JA aplicada: e o estado de producao hoje."""
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


def test_o_recorte_e_da_aurora():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    engines = {t["table_name"]: t["engine"] for t in snap["tables"]}
    assert engines["equipment_exercises"] == "InnoDB"


def test_producao_com_a_045_aplicada_executa_um_unico_update():
    statements, db = _plan(_com_a_coluna(_snapshot()))
    assert statements == [(UPDATE, None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)


def test_o_plano_so_olha_a_tabela_equipment_exercises():
    _, db = _plan(_com_a_coluna(_snapshot()))
    assert db.calls, "o plano precisa ler o information_schema"
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {"equipment_exercises"}


def test_coluna_ausente_e_no_op_e_nao_erro():
    """Banco anterior a 045: nada a corrigir, e a migracao nao pode falhar."""
    colunas = {c["col"] for c in _snapshot()["columns"] if c["t"] == "equipment_exercises"}
    assert "load_convention" not in colunas
    statements, _ = _plan(_snapshot())
    assert statements == []

    db = _RecordingDB(_snapshot())
    _module()["up"](db)
    assert db.executed == []


def test_nenhum_dado_de_execucao_e_tocado():
    """A correcao e de CADASTRO: `exercise_sets.weight_kg` continua sendo
    carga total registrada (D12) e nao pode ser reescrita aqui."""
    assert _module()["TABLE"] == "equipment_exercises"
    # os dois estados possiveis do plano, e o `plan_down`
    planos = [
        _plan(_com_a_coluna(_snapshot()))[0],
        _plan(_snapshot())[0],
        _plan(_com_a_coluna(_snapshot()), fn="plan_down")[0],
    ]
    for statements in planos:
        for stmt, _params in statements:
            alvo = stmt.upper()
            assert "EXERCISE_SETS" not in alvo
            assert "WEIGHT_KG" not in alvo
            assert alvo.startswith("UPDATE EQUIPMENT_EXERCISES ")
            for proibido in ("INSERT ", "DELETE ", "DROP ", "ALTER "):
                assert proibido not in alvo


def test_o_update_so_mexe_nas_duas_convencoes_revertidas():
    mod = _module()
    assert mod["CONVENCOES_REVERTIDAS"] == ("per_side", "per_dumbbell")
    assert mod["CONVENCAO_ALVO"] == "total"
    # 'assisted', 'bodyweight' e 'bodyweight_plus' convertem/alteram leitura de
    # verdade e NAO podem ser tocados por esta correcao.
    for preservada in ("assisted", "bodyweight", "bodyweight_plus"):
        assert preservada not in UPDATE


def test_up_executa_exatamente_o_plano():
    db = _RecordingDB(_com_a_coluna(_snapshot()))
    _module()["up"](db)
    assert db.executed == [(UPDATE, None)]


def test_reexecutar_o_update_e_inofensivo():
    """O `WHERE` e a propria idempotencia: apos a primeira execucao nenhum par
    esta em 'per_side'/'per_dumbbell', entao o segundo UPDATE afeta 0 linhas.
    O runner, alem disso, registra a versao e nao reexecuta o arquivo."""
    assert "WHERE load_convention IN ('per_side','per_dumbbell')" in UPDATE
    db = _RecordingDB(_com_a_coluna(_snapshot()))
    db.rowcount = 0
    _module()["up"](db)
    _module()["up"](db)
    assert db.executed == [(UPDATE, None), (UPDATE, None)]


def test_down_e_no_op_porque_a_migracao_e_irreversivel():
    statements, _ = _plan(_com_a_coluna(_snapshot()), fn="plan_down")
    assert statements == []
    db = _RecordingDB(_com_a_coluna(_snapshot()))
    _module()["down"](db)
    assert db.executed == []
    assert "IRREVERSIVEL" in _module()["plan_down"].__doc__.upper()


@pytest.mark.parametrize(
    "tipo",
    [
        "enum('total','assisted')",  # sem per_side/per_dumbbell
        "enum('per_side','per_dumbbell')",  # sem o alvo 'total'
    ],
)
def test_enum_sem_os_valores_exigidos_falha_antes_de_qualquer_statement(tipo):
    erro = _module()["ConvencaoCargaError"]
    with pytest.raises(erro) as exc:
        _plan(_com_a_coluna(_snapshot(), type=tipo))
    assert "Nenhum statement executado" in str(exc.value)


def test_tabela_ausente_ou_com_engine_errada_falha():
    erro = _module()["ConvencaoCargaError"]

    sem_tabela = copy.deepcopy(_com_a_coluna(_snapshot()))
    sem_tabela["tables"] = [t for t in sem_tabela["tables"] if t["table_name"] != "equipment_exercises"]
    with pytest.raises(erro):
        _plan(sem_tabela)

    myisam = copy.deepcopy(_com_a_coluna(_snapshot()))
    for t in myisam["tables"]:
        if t["table_name"] == "equipment_exercises":
            t["engine"] = "MyISAM"
    with pytest.raises(erro):
        _plan(myisam)


def test_migracao_e_autocontida_e_compativel_com_o_data_api():
    source = MIGRATION_046.read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
    statements, _ = _plan(_com_a_coluna(_snapshot()))
    for sql, _params in statements:
        assert ";" not in sql
        upper = sql.upper()
        for proibido in ("PREPARE", "EXECUTE", "DEALLOCATE", "DELIMITER", "SET @", "USE "):
            assert proibido not in upper


def test_o_numero_046_e_unico_e_vem_depois_da_045():
    migracoes = m.discover_migrations(m.DEFAULT_MIGRATIONS_DIR)
    por_versao = {mig.version: mig.filename for mig in migracoes}
    assert por_versao[46] == MIGRATION_046.name
    assert [mig.version for mig in migracoes if mig.version == 46] == [46]
    ordem = [mig.version for mig in migracoes]
    assert ordem.index(45) < ordem.index(46)
    assert 46 > m.BASELINE_MAX_VERSION
