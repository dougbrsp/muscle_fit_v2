"""Plano da migracao 050 (`daily_checkins`, Novo Dashboard F2.2) contra o
`information_schema` REAL da Aurora de producao.

Recorte reaproveitado: `tests/unit/data/aurora_schema_2026_09_17_035.json`
traz a lista COMPLETA de tabelas de producao e as colunas/indices/FKs de
`users` (SELECT somente leitura). Nenhuma leitura nova da AWS foi feita - e
nenhuma e necessaria: a 050 so pergunta se `daily_checkins` existe e se
`users.id` e um BIGINT PK InnoDB (a FK).

Producao hoje NAO tem `daily_checkins` -> 1 `CREATE TABLE`.
Estado convergido -> 0 statements. `down` -> 1 `DROP TABLE` (reversivel de
verdade: a tabela nasce vazia e nada a referencia).

Rodar: cd api && python -m pytest tests/unit/test_migration_050_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION_050 = m.DEFAULT_MIGRATIONS_DIR / "050_daily_checkins.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"

SCORES = ("energy", "soreness", "stress", "mood")
DROP = "DROP TABLE IF EXISTS daily_checkins"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria uma classe de erro nova a cada
    # chamada e `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_050).__globals__


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
    """Recorte com a 050 JA aplicada (estado pos-deploy)."""
    snap = copy.deepcopy(snapshot)
    snap["tables"].append({"table_name": "daily_checkins", "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
    tipos = {
        "id": ("bigint", "NO"),
        "user_id": ("bigint", "NO"),
        "local_date": ("date", "NO"),
        # MySQL 8 ainda mostra a largura do tinyint em alguns recortes.
        **{name: ("tinyint(4)", "YES") for name in SCORES},
        "notes": ("varchar(255)", "YES"),
        "created_at": ("datetime", "NO"),
        "updated_at": ("datetime", "NO"),
    }
    tipos.update(overrides)
    for pos, (col, (tipo, nulo)) in enumerate(tipos.items(), start=1):
        snap["columns"].append(
            {
                "t": "daily_checkins",
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
    assert "daily_checkins" not in tabelas


def test_producao_hoje_executa_um_unico_create_table():
    statements, db = _plan(_snapshot())
    assert statements == [(_create_sql(), None)]
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)
    assert all("information_schema" in q for q in db.queries)
    assert not db.executed, "plan() e somente leitura"


def test_o_plano_so_olha_daily_checkins_e_users():
    _, db = _plan(_snapshot())
    assert db.calls, "o plano precisa ler o information_schema"
    tabelas = {tabela for _sql, params in db.calls for tabela in params}
    assert tabelas == {"daily_checkins", "users"}


def test_ddl_tem_unique_do_dia_indice_fk_e_os_quatro_escores():
    sql = _create_sql()
    # O check-in do dia e UNICO por definicao -> o POST e upsert (ao contrario
    # da 048, onde dois eventos do mesmo tipo no mesmo dia sao dois fatos).
    assert "UNIQUE KEY uq_dc_user_date (user_id, local_date)" in sql
    assert "CONSTRAINT fk_dc_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in sql
    for name in SCORES:
        assert f"{name} TINYINT NULL" in sql, f"{name} tem de nascer NULL (nao respondido != 0)"
    assert "ENGINE=InnoDB" in sql
    assert _module()["SCORE_FIELDS"] == SCORES


def test_nenhum_escore_tem_default():
    """`NOT NULL DEFAULT 3` transformaria "nao respondi" em "estou mediano" -
    a mentira mais barata de cometer e a mais cara de descobrir, porque o
    numero fica plausivel."""
    sql = _create_sql().upper()
    for name in SCORES:
        assert f"{name.upper()} TINYINT NULL DEFAULT" not in sql


def test_nao_existe_coluna_de_sono_subjetivo():
    """Duracao e regularidade do sono ja vem de `sleep_metrics` e do
    Intervals.icu. Um escore subjetivo de sono aqui seria uma SEGUNDA verdade
    sobre a mesma coisa (ver a decisao 4 no cabecalho da migracao)."""
    sql = _create_sql().lower()
    assert "sleep" not in sql


def test_enum_do_banco_espelha_o_vocabulario_da_api():
    from app.daily_checkins_schema import SCORE_FIELDS, SCORE_MAX, SCORE_MIN

    assert tuple(SCORE_FIELDS) == _module()["SCORE_FIELDS"]
    assert (SCORE_MIN, SCORE_MAX) == (_module()["SCORE_MIN"], _module()["SCORE_MAX"])


def test_todo_escore_tem_rotulo_e_direcao():
    from app.daily_checkins_schema import SCORE_FIELDS, SCORE_HIGHER_IS_BETTER, SCORE_LABELS

    assert set(SCORE_LABELS) == set(SCORE_FIELDS)
    assert set(SCORE_HIGHER_IS_BETTER) == set(SCORE_FIELDS)
    # Disposicao e humor: alto e bom. Dor e estresse: alto e ruim.
    assert SCORE_HIGHER_IS_BETTER == {
        "energy": True,
        "mood": True,
        "soreness": False,
        "stress": False,
    }


def test_migracao_e_aditiva_sem_dml():
    fonte = MIGRATION_050.read_text(encoding="utf-8")
    # `ON UPDATE CURRENT_TIMESTAMP`, `ON DELETE CASCADE` e
    # `ON DUPLICATE KEY UPDATE` (citado na doc) sao DDL/prosa, nao DML.
    so_dml = (
        fonte.upper()
        .replace("UPDATE CURRENT_TIMESTAMP", "")
        .replace("ON DELETE CASCADE", "")
        .replace("ON DUPLICATE KEY UPDATE", "")
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
        ({"energy": ("int(11)", "YES")}, "energy"),
        ({"soreness": ("tinyint(4)", "NO")}, "IS_NULLABLE"),
        ({"notes": ("text", "YES")}, "notes"),
        ({"local_date": ("datetime", "NO")}, "local_date"),
    ],
)
def test_tabela_presente_em_outro_formato_falha_antes_de_escrever(overrides, trecho):
    Erro = _module()["DailyCheckinsError"]
    db = _RecordingDB(_com_a_tabela(_snapshot(), **overrides))
    with pytest.raises(Erro) as exc:
        _module()["plan"](db)
    assert trecho in str(exc.value)
    assert not db.executed


def test_coluna_faltando_na_tabela_existente_falha():
    Erro = _module()["DailyCheckinsError"]
    snap = _com_a_tabela(_snapshot())
    snap["columns"] = [c for c in snap["columns"] if not (c["t"] == "daily_checkins" and c["col"] == "mood")]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "daily_checkins.mood nao existe" in str(exc.value)


def test_users_incompativel_falha_inclusive_em_dry_run():
    Erro = _module()["DailyCheckinsError"]
    snap = _snapshot()
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != "users"]
    with pytest.raises(Erro) as exc:
        _module()["plan"](_RecordingDB(snap))
    assert "users nao existe" in str(exc.value)
