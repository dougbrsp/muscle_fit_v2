"""Plano da migracao 054 (`generation_runs`, M16) contra o
`information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_17_035.json`
(SELECT somente leitura). Nenhuma leitura nova da AWS foi feita - a 054 so
pergunta se `generation_runs` existe, se `users.id` e um BIGINT PK InnoDB (a
FK com CASCADE) e se `workout_plans` existe e e InnoDB (a FK com SET NULL).

Producao hoje NAO tem `generation_runs` -> 1 `CREATE TABLE`. Estado
convergido -> 0 statements. `down` -> 1 `DROP TABLE` (reversivel de verdade:
nada aponta para a tabela, e perde-la so devolve o sistema ao comportamento
pre-M16 - a auditoria continua no `raw_payload`).

Rodar: cd api && python -m pytest tests/unit/test_migration_054_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_054 = m.DEFAULT_MIGRATIONS_DIR / "054_generation_runs.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"

DROP = "DROP TABLE IF EXISTS generation_runs"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION_054).__globals__


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
        {"table_name": "generation_runs", "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"}
    )
    tipos = {
        "id": ("bigint", "NO"),
        "user_id": ("bigint", "NO"),
        "workout_plan_id": ("bigint", "YES"),
        "job_id": ("varchar(36)", "YES"),
        "template_id": ("bigint", "YES"),
        "template_slot_sequence": ("int(11)", "YES"),
        "volume_mode": ("varchar(40)", "YES"),
        "engine_version": ("varchar(40)", "YES"),
        "llm_used": ("tinyint(1)", "NO"),
        "plan_date": ("date", "YES"),
        "audit_hash": ("char(64)", "NO"),
        "audit_json": ("json", "NO"),
        "request_json": ("json", "YES"),
        "created_at": ("datetime", "NO"),
    }
    tipos.update(overrides)
    for pos, (col, (tipo, nulo)) in enumerate(tipos.items(), start=1):
        snap["columns"].append(
            {
                "t": "generation_runs",
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
    assert "workout_plans" in tabelas
    assert "generation_runs" not in tabelas


def test_producao_hoje_executa_um_unico_create_table():
    statements, db = _plan(_snapshot())
    assert statements == [(_create_sql(), None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed, "plan() e somente leitura"


def test_o_plano_so_olha_a_tabela_nova_e_as_duas_referenciadas():
    _, db = _plan(_snapshot())
    assert db.calls
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {"generation_runs", "users", "workout_plans"}


def test_o_bloco_de_auditoria_vai_inteiro_num_json_nao_em_colunas():
    """A forma do `generation_audit` difere entre os motores (o Modo A grava
    `window`/`activation`/`selection`, o Modo B nao) e ainda muda com eles.
    Normalizar isso em colunas congelaria essa forma em DDL e criaria uma
    segunda verdade ao lado do bloco que continua no `raw_payload`."""
    sql = _create_sql()
    assert "audit_json JSON NOT NULL" in sql
    assert "request_json JSON NULL" in sql
    # Nada de coluna por campo da auditoria.
    for derivada in ("fallback_count", "substitution_count", "safety_clamp_count", "activation", "selection"):
        assert derivada not in sql


def test_so_sobe_para_coluna_o_que_a_f31_filtra_ou_junta():
    sql = _create_sql()
    for coluna in (
        "user_id BIGINT NOT NULL",
        "workout_plan_id BIGINT NULL",
        "template_id BIGINT NULL",
        "template_slot_sequence INT NULL",
        "volume_mode VARCHAR(40) NULL",
        "engine_version VARCHAR(40) NULL",
        "llm_used TINYINT(1) NOT NULL DEFAULT 0",
        "plan_date DATE NULL",
    ):
        assert coluna in sql


def test_a_ligacao_rascunho_ficha_e_o_hash_e_ele_nao_e_unico():
    """A ficha nasce num POST SEPARADO da geracao. O hash do bloco identifica
    a mesma auditoria dos dois lados sem mudar um byte dela (o M16 proibe
    mudar o formato neste PR). UNIQUE ali faria a SEGUNDA geracao de uma
    auditoria identica FALHAR - a tabela nao pode reprovar uma geracao."""
    sql = _create_sql()
    assert f"audit_hash CHAR({_module()['AUDIT_HASH_LENGTH']}) NOT NULL" in sql
    assert "KEY idx_gr_user_audit (user_id, audit_hash)" in sql
    assert "UNIQUE" not in sql


def test_fks_fisicas_sao_users_cascade_e_workout_plans_set_null():
    """`users`: sem usuario nao ha o que auditar -> CASCADE. `workout_plans`:
    apagar a ficha nao apaga o fato de que a geracao aconteceu -> SET NULL
    (mesma FK que a 025 ja tem provada em producao)."""
    sql = _create_sql()
    assert "CONSTRAINT fk_gr_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in sql
    assert (
        "CONSTRAINT fk_gr_plan FOREIGN KEY (workout_plan_id) REFERENCES workout_plans(id) ON DELETE SET NULL"
        in sql
    )
    assert "ENGINE=InnoDB" in sql


def test_template_id_tem_fk_logica_e_nao_fisica():
    """FK fisica apagaria o agrupamento historico: com SET NULL, apagar um
    Modelo tiraria "o que este Modelo gerou" de TODAS as linhas antigas; com
    CASCADE, apagaria o historico inteiro. O id orfao e o que preserva a
    serie."""
    sql = _create_sql()
    assert "KEY idx_gr_template_created (template_id, created_at)" in sql
    assert "REFERENCES workout_plan_templates" not in sql


def test_indices_de_leitura_da_f31():
    sql = _create_sql()
    assert "KEY idx_gr_user_created (user_id, created_at)" in sql
    assert "KEY idx_gr_plan (workout_plan_id)" in sql


def test_nao_existe_coluna_status():
    """A linha so nasce quando um rascunho FOI produzido; geracao que falhou
    nao tem auditoria para persistir. Uma coluna que hoje so teria um valor
    seria uma promessa que o codigo nao cumpre."""
    assert "status" not in _create_sql()


def test_migracao_e_aditiva_sem_dml():
    """Sem backfill: os jobs que guardariam o historico ja expiraram, e
    reconstruir a partir das fichas salvas inventaria a data da GERACAO (so
    existe a da ficha) e nao teria `request_json` - linhas que parecem
    historico sem ser."""
    fonte = MIGRATION_054.read_text(encoding="utf-8")
    so_dml = (
        fonte.upper()
        .replace("ON DELETE CASCADE", "")
        .replace("ON DELETE SET NULL", "")
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
        ({"audit_hash": ("varchar(64)", "NO")}, "audit_hash"),
        ({"audit_json": ("longtext", "NO")}, "audit_json"),
        ({"llm_used": ("tinyint(1)", "YES")}, "IS_NULLABLE"),
        ({"workout_plan_id": ("bigint", "NO")}, "IS_NULLABLE"),
        ({"job_id": ("varchar(64)", "YES")}, "job_id"),
    ],
)
def test_tabela_presente_em_outro_formato_falha_antes_de_escrever(overrides, trecho):
    Erro = _module()["GenerationRunsError"]
    db = _RecordingDB(_com_a_tabela(_snapshot(), **overrides))
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert trecho in str(exc.value)
    assert not db.executed


def test_coluna_faltando_na_tabela_existente_falha():
    Erro = _module()["GenerationRunsError"]
    snap = _com_a_tabela(_snapshot())
    snap["columns"] = [
        c for c in snap["columns"] if not (c["t"] == "generation_runs" and c["col"] == "request_json")
    ]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "generation_runs.request_json nao existe" in str(exc.value)


def test_users_incompativel_falha_inclusive_em_dry_run():
    Erro = _module()["GenerationRunsError"]
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != "users"]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "users nao existe" in str(exc.value)


def test_workout_plans_ausente_falha_antes_do_create():
    """A FK e criada JUNTO com a tabela: sem `workout_plans` o `CREATE TABLE`
    falharia no meio do deploy em vez de no plano."""
    Erro = _module()["GenerationRunsError"]
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != "workout_plans"]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "workout_plans nao existe" in str(exc.value)
