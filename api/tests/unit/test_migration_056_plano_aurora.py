"""Plano da migracao 056 (intencao declarada da metodologia por Modelo -
Fase 4, PR H) contra o `information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_17_035.json`
(SELECT somente leitura). Nenhuma leitura nova da AWS foi feita - a 056 so
pergunta se as duas tabelas novas existem e se `workout_plan_templates` e
`muscle_groups` (os dois pais das FKs) existem e sao InnoDB.

Producao hoje NAO tem as tabelas -> 2 `CREATE TABLE` (mae, depois filha).
Estado convergido -> 0 statements. `down` -> 2 `DROP TABLE` (filha primeiro;
reversivel de verdade: perder as tabelas devolve todo Modelo a `standard`,
que e o comportamento pre-PR H).

Rodar: cd api && python -m pytest tests/unit/test_migration_056_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from app.intelligence.methodology_volume import INTENT_KINDS
from tests.support.isolamento_v3 import TABELAS_V3
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_056 = m.DEFAULT_MIGRATIONS_DIR / "056_workout_plan_template_methodology_intent.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"

INTENT = "workout_plan_template_methodology_intent"
TARGETS = "workout_plan_template_methodology_intent_targets"
DROP_TARGETS = f"DROP TABLE IF EXISTS {TARGETS}"
DROP_INTENT = f"DROP TABLE IF EXISTS {INTENT}"

ENUM_MYSQL = "enum('standard','specialization','maintenance','cutting')"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION_056).__globals__


def _sql(name: str) -> str:
    return " ".join(_module()[name].split())


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


def _add_table(snap: dict, table: str, tipos: dict[str, tuple[str, str]], pk: set[str]) -> None:
    snap["tables"].append({"table_name": table, "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
    for pos, (col, (tipo, nulo)) in enumerate(tipos.items(), start=1):
        snap["columns"].append(
            {
                "t": table,
                "pos": pos,
                "col": col,
                "type": tipo,
                "nullable": nulo,
                "dflt": None,
                "ckey": "PRI" if col in pk else "",
                "extra": "",
                "coll": None,
            }
        )


def _com_a_mae(snapshot: dict, **overrides) -> dict:
    snap = copy.deepcopy(snapshot)
    tipos = {
        "template_id": ("bigint", "NO"),
        "intent": (ENUM_MYSQL, "NO"),
        "updated_at": ("datetime", "NO"),
    }
    tipos.update(overrides)
    _add_table(snap, INTENT, tipos, {"template_id"})
    return snap


def _com_as_duas(snapshot: dict, **overrides) -> dict:
    snap = _com_a_mae(snapshot)
    tipos = {"template_id": ("bigint", "NO"), "muscle_group_id": ("bigint", "NO")}
    tipos.update(overrides)
    _add_table(snap, TARGETS, tipos, {"template_id", "muscle_group_id"})
    return snap


def test_o_recorte_e_da_aurora_e_nao_tem_as_tabelas():
    snap = _snapshot()
    assert snap["versao"][0]["mysql"].startswith("8.0")
    tabelas = {t["table_name"] for t in snap["tables"]}
    assert {"workout_plan_templates", "muscle_groups"} <= tabelas
    assert INTENT not in tabelas and TARGETS not in tabelas


def test_producao_hoje_executa_dois_create_table_mae_antes_da_filha():
    statements, db = _plan(_snapshot())
    assert statements == [(_sql("CREATE_INTENT_SQL"), None), (_sql("CREATE_TARGETS_SQL"), None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed, "plan() e somente leitura"


def test_o_plano_so_olha_as_duas_novas_e_os_dois_pais():
    _, db = _plan(_snapshot())
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {INTENT, TARGETS, "workout_plan_templates", "muscle_groups"}


def test_uma_intencao_por_modelo_template_id_e_a_primary_key():
    sql = _sql("CREATE_INTENT_SQL")
    assert "template_id BIGINT NOT NULL PRIMARY KEY" in sql
    assert "AUTO_INCREMENT" not in sql, "sem id proprio: a chave natural e o Modelo"


def test_intent_e_enum_no_dominio_do_motor_com_default_standard():
    """O dominio e o do motor puro (`INTENT_KINDS`), que so muda com PR; o
    ENUM recusa no banco o que `MethodologyIntent.__post_init__` recusaria."""
    sql = _sql("CREATE_INTENT_SQL")
    assert _module()["INTENT_KINDS"] == INTENT_KINDS
    valores = ", ".join(f"'{k}'" for k in INTENT_KINDS)
    assert f"intent ENUM({valores}) NOT NULL DEFAULT 'standard'" in sql
    assert _module()["INTENT_COLUMN_TYPE"] == ENUM_MYSQL


def test_updated_at_sem_created_at():
    sql = _sql("CREATE_INTENT_SQL")
    assert "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in sql
    assert "created_at" not in sql


def test_fks_fisicas_todas_cascade_e_nenhuma_para_exercise_catalog():
    """Modelo apagado -> intencao apagada -> alvos apagados (uma cadeia).
    Grupo apagado -> alvo apagado (senao a especializacao passaria a valer
    "para ninguem" em silencio). E a 028 proibe FK nova para
    `exercise_catalog`: o alvo e um GRUPO."""
    mae, filha = _sql("CREATE_INTENT_SQL"), _sql("CREATE_TARGETS_SQL")
    assert (
        "CONSTRAINT fk_wptmi_template FOREIGN KEY (template_id) "
        "REFERENCES workout_plan_templates(id) ON DELETE CASCADE" in mae
    )
    assert (
        "CONSTRAINT fk_wptmit_intent FOREIGN KEY (template_id) "
        f"REFERENCES {INTENT}(template_id) ON DELETE CASCADE" in filha
    )
    assert "CONSTRAINT fk_wptmit_group FOREIGN KEY (muscle_group_id) REFERENCES muscle_groups(id) ON DELETE CASCADE" in filha
    assert "exercise_catalog" not in mae + filha
    assert "ENGINE=InnoDB" in mae and "ENGINE=InnoDB" in filha


def test_alvos_com_pk_composta_e_indice_pelo_grupo():
    filha = _sql("CREATE_TARGETS_SQL")
    assert "PRIMARY KEY (template_id, muscle_group_id)" in filha
    assert "KEY idx_wptmit_group (muscle_group_id)" in filha


def test_migracao_e_aditiva_sem_dml():
    """Sem seed: sem linha = `standard`, que e exatamente o comportamento de
    producao hoje. Nada a preencher."""
    fonte = MIGRATION_056.read_text(encoding="utf-8")
    so_dml = fonte.upper().replace("ON DELETE CASCADE", "").replace("ON UPDATE CURRENT_TIMESTAMP", "")
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "ALTER TABLE"):
        assert proibido not in so_dml


def test_as_duas_tabelas_estao_em_tabelas_v3_antes_dos_pais():
    """`truncar` desliga FOREIGN_KEY_CHECKS, mas a lista e lida na ordem filho
    -> pai; e sem as duas aqui uma intencao deixada por outro arquivo faria um
    Modelo recem-criado (mesmo id) nascer `cutting`."""
    assert TARGETS in TABELAS_V3 and INTENT in TABELAS_V3
    ordem = list(TABELAS_V3)
    assert ordem.index(TARGETS) < ordem.index(INTENT)
    assert ordem.index(INTENT) < ordem.index("workout_plan_templates")
    assert ordem.index(TARGETS) < ordem.index("muscle_groups")


def test_reexecucao_no_estado_convergido_nao_escreve_nada():
    statements, db = _plan(_com_as_duas(_snapshot()))
    assert statements == []
    assert not db.executed


def test_so_a_mae_presente_cria_so_a_filha():
    statements, _ = _plan(_com_a_mae(_snapshot()))
    assert statements == [(_sql("CREATE_TARGETS_SQL"), None)]


def test_filha_sem_a_mae_falha_antes_de_escrever():
    Erro = _module()["MethodologyIntentError"]
    snap = _com_as_duas(_snapshot())
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != INTENT]
    snap["columns"] = [c for c in snap["columns"] if c["t"] != INTENT]
    db = _RecordingDB(snap)
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert "filha sem a mae" in str(exc.value)
    assert not db.executed


def test_up_executa_exatamente_o_plano():
    db = _RecordingDB(_snapshot())
    _module()["up"](db)
    assert db.executed == [(_sql("CREATE_INTENT_SQL"), None), (_sql("CREATE_TARGETS_SQL"), None)]


def test_down_e_o_inverso_real_filha_primeiro():
    statements, _ = _plan(_com_as_duas(_snapshot()), fn="plan_down")
    assert statements == [(DROP_TARGETS, None), (DROP_INTENT, None)]
    statements, _ = _plan(_com_a_mae(_snapshot()), fn="plan_down")
    assert statements == [(DROP_INTENT, None)]
    statements, _ = _plan(_snapshot(), fn="plan_down")
    assert statements == []
    db = _RecordingDB(_com_as_duas(_snapshot()))
    _module()["down"](db)
    assert db.executed == [(DROP_TARGETS, None), (DROP_INTENT, None)]


@pytest.mark.parametrize(
    "fabrica, overrides, trecho",
    [
        (_com_as_duas, {"intent": ("varchar(20)", "NO")}, "intent"),
        (_com_as_duas, {"intent": ("enum('standard','cutting')", "NO")}, "intent"),
        (_com_a_mae, {"updated_at": ("datetime", "YES")}, "IS_NULLABLE"),
        (_com_a_mae, {"template_id": ("int(11)", "NO")}, "template_id"),
    ],
)
def test_mae_presente_em_outro_formato_falha_antes_de_escrever(fabrica, overrides, trecho):
    Erro = _module()["MethodologyIntentError"]
    snap = fabrica(_snapshot())
    # `overrides` recai sobre a MAE nas duas fabricas.
    for col in snap["columns"]:
        if col["t"] == INTENT and col["col"] in overrides:
            col["type"], col["nullable"] = overrides[col["col"]]
    db = _RecordingDB(snap)
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert trecho in str(exc.value)
    assert not db.executed


def test_filha_presente_em_outro_formato_falha_antes_de_escrever():
    Erro = _module()["MethodologyIntentError"]
    db = _RecordingDB(_com_as_duas(_snapshot(), muscle_group_id=("int(11)", "NO")))
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert "muscle_group_id" in str(exc.value)
    assert not db.executed


def test_coluna_faltando_na_tabela_existente_falha():
    Erro = _module()["MethodologyIntentError"]
    snap = _com_as_duas(_snapshot())
    snap["columns"] = [c for c in snap["columns"] if not (c["t"] == INTENT and c["col"] == "updated_at")]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert f"{INTENT}.updated_at nao existe" in str(exc.value)


@pytest.mark.parametrize("pai", ["workout_plan_templates", "muscle_groups"])
def test_pai_ausente_falha_antes_do_create(pai):
    """As FKs nascem JUNTO com as tabelas: sem o pai, o `CREATE TABLE`
    falharia no meio do deploy em vez de no plano."""
    Erro = _module()["MethodologyIntentError"]
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != pai]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert f"{pai} nao existe" in str(exc.value)
