"""Plano da migracao 051 (`dashboard_alert_states`, Novo Dashboard F2.2)
contra o `information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_17_035.json`
(SELECT somente leitura). Nenhuma leitura nova da AWS foi feita - a 051 so
pergunta se `dashboard_alert_states` existe e se `users.id` e um BIGINT PK
InnoDB (a FK).

Producao hoje NAO tem `dashboard_alert_states` -> 1 `CREATE TABLE`.
Estado convergido -> 0 statements. `down` -> 1 `DROP TABLE` (reversivel de
verdade: a tabela e memoria de EXIBICAO; perde-la volta o hub ao
comportamento pre-F2.2, sem perder dado de treino nenhum).

Rodar: cd api && python -m pytest tests/unit/test_migration_051_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_051 = m.DEFAULT_MIGRATIONS_DIR / "051_dashboard_alert_states.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"

SEVERITIES = ("INFO", "ATTENTION", "ACTION")
SEVERITY_ENUM_SQL = "enum(" + ",".join(f"'{s}'" for s in SEVERITIES) + ")"
DROP = "DROP TABLE IF EXISTS dashboard_alert_states"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION_051).__globals__


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
    snap = copy.deepcopy(snapshot)
    snap["tables"].append(
        {"table_name": "dashboard_alert_states", "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"}
    )
    tipos = {
        "id": ("bigint", "NO"),
        "user_id": ("bigint", "NO"),
        "alert_key": ("varchar(500)", "NO"),
        "alert_key_hash": ("char(64)", "NO"),
        "rule_code": ("varchar(64)", "NO"),
        "fingerprint": ("varchar(500)", "NO"),
        "severity": (SEVERITY_ENUM_SQL, "NO"),
        "last_reason": ("varchar(32)", "NO"),
        "first_seen_at": ("datetime", "NO"),
        "last_seen_at": ("datetime", "NO"),
        "last_emitted_at": ("datetime", "NO"),
        "emitted_count": ("int(11)", "NO"),
        "suppressed_count": ("int(11)", "NO"),
        "resolved_at": ("datetime", "YES"),
        "snoozed_until": ("datetime", "YES"),
        "created_at": ("datetime", "NO"),
        "updated_at": ("datetime", "NO"),
    }
    tipos.update(overrides)
    for pos, (col, (tipo, nulo)) in enumerate(tipos.items(), start=1):
        snap["columns"].append(
            {
                "t": "dashboard_alert_states",
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
    assert "dashboard_alert_states" not in tabelas


def test_producao_hoje_executa_um_unico_create_table():
    statements, db = _plan(_snapshot())
    assert statements == [(_create_sql(), None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed, "plan() e somente leitura"


def test_o_plano_so_olha_a_tabela_nova_e_users():
    _, db = _plan(_snapshot())
    assert db.calls
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {"dashboard_alert_states", "users"}


def test_a_identidade_do_estado_e_o_hash_da_alert_key():
    """O UNIQUE e `(user_id, alert_key_hash)`, NAO `(user_id, fingerprint)`.

    Duas razoes medidas (ver o cabecalho da migracao): a impressao muda sozinha
    quando uma chave e conjunto ou traz a data da janela, e ela nao tem teto de
    tamanho - truncar dentro de um UNIQUE viraria colisao, e uma colisao aqui
    silencia para sempre um alerta que nao e o mesmo."""
    sql = _create_sql()
    assert "UNIQUE KEY uq_das_user_key (user_id, alert_key_hash)" in sql
    assert "UNIQUE" not in sql.replace("UNIQUE KEY uq_das_user_key (user_id, alert_key_hash)", "")
    assert "alert_key_hash CHAR(64) NOT NULL" in sql
    assert "alert_key VARCHAR(500) NOT NULL" in sql
    assert "fingerprint VARCHAR(500) NOT NULL" in sql


def test_ddl_tem_indice_por_regra_fk_e_o_enum_de_severidade():
    sql = _create_sql()
    assert "KEY idx_das_user_rule (user_id, rule_code)" in sql
    assert "CONSTRAINT fk_das_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in sql
    assert "severity ENUM(" + ",".join(f"'{s}'" for s in SEVERITIES) + ") NOT NULL" in sql
    assert "ENGINE=InnoDB" in sql
    assert _module()["SEVERITIES"] == SEVERITIES


def test_resolved_at_e_snoozed_until_nascem_nulos():
    """`NULL` em `resolved_at` = episodio ABERTO; `NULL` em `snoozed_until` =
    nunca silenciado. Um DEFAULT em qualquer dos dois afirmaria um estado que
    nao aconteceu."""
    sql = _create_sql()
    assert "resolved_at DATETIME NULL" in sql
    assert "snoozed_until DATETIME NULL" in sql


def test_o_motivo_da_ultima_exibicao_e_gravado():
    """Sem `last_reason`, a segunda leitura do MESMO dia teria de recalcular o
    motivo - e "primeira vez" viraria "cooldown vencido" entre a home e a tela
    de insights, mudando o payload de duas leituras da mesma ocasiao."""
    assert "last_reason VARCHAR(32) NOT NULL" in _create_sql()


def test_enum_do_banco_espelha_o_contrato_de_severidade():
    from app.intelligence.dashboard.contracts import Severity

    assert tuple(s.value for s in Severity) == SEVERITIES


def test_migracao_e_aditiva_sem_dml():
    """Nao ha backfill: nao existe historico de "alerta mostrado" para
    reconstruir, e inventar um silenciaria alerta que o usuario nunca viu."""
    fonte = MIGRATION_051.read_text(encoding="utf-8")
    so_dml = (
        fonte.upper()
        .replace("UPDATE CURRENT_TIMESTAMP", "")
        .replace("ON DELETE CASCADE", "")
    )
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
        ({"severity": ("varchar(20)", "NO")}, "severity"),
        ({"alert_key_hash": ("varchar(64)", "NO")}, "alert_key_hash"),
        ({"snoozed_until": ("datetime", "NO")}, "IS_NULLABLE"),
        ({"emitted_count": ("bigint", "NO")}, "emitted_count"),
    ],
)
def test_tabela_presente_em_outro_formato_falha_antes_de_escrever(overrides, trecho):
    Erro = _module()["DashboardAlertStatesError"]
    db = _RecordingDB(_com_a_tabela(_snapshot(), **overrides))
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert trecho in str(exc.value)
    assert not db.executed


def test_coluna_faltando_na_tabela_existente_falha():
    Erro = _module()["DashboardAlertStatesError"]
    snap = _com_a_tabela(_snapshot())
    snap["columns"] = [
        c for c in snap["columns"] if not (c["t"] == "dashboard_alert_states" and c["col"] == "snoozed_until")
    ]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "dashboard_alert_states.snoozed_until nao existe" in str(exc.value)


def test_users_incompativel_falha_inclusive_em_dry_run():
    Erro = _module()["DashboardAlertStatesError"]
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != "users"]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "users nao existe" in str(exc.value)
