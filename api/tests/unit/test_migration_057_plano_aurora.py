"""Plano da migração 057 (macro-grupos de volume editáveis - Fase 6, PR 1)
contra o recorte de `muscle_groups` pós-055.

Recorte: o mesmo `tests/unit/data/schema_repo_2026_09_26_055.json` da 055
(forma do baseline 001 + nomes da 002), com a 055 aplicada EM MEMÓRIA (as
duas colunas, a FK e o seed de porte, lidos do próprio módulo da 055). Nenhuma
conexão foi aberta: o CI é o único portão.

Produção hoje: sem as tabelas e sem a coluna -> 2 CREATE + 1 ALTER + 3 INSERT
de macro-grupo + 9 INSERT de faixa + 1 UPDATE de associação. Estado
convergido -> 0. O seed não desfaz edição de admin.

E a garantia de paridade do PR: cada célula semeada é IGUAL à célula de
`METHODOLOGY_RANGES` que o motor v1 usava (valores, origem e evidência). Desde
a Fase 6, PR 2, o motor não tem mais a tabela em código; a v1 ficou congelada
em `tests/unit/data/methodology_ranges_v1.json` (gerado pelo motor v1).

Rodar: cd api && python -m pytest tests/unit/test_migration_057_plano_aurora.py
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.paridade_028 import SnapshotDB

MIGRATION_055 = m.DEFAULT_MIGRATIONS_DIR / "055_muscle_groups_porte_e_agregacao.py"
MIGRATION_057 = m.DEFAULT_MIGRATIONS_DIR / "057_macro_grupos_volume.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "schema_repo_2026_09_26_055.json"
# `METHODOLOGY_RANGES` do motor v1 (Fase 4), congelado quando a tabela saiu do
# código (Fase 6, PR 2): "porte|bucket" -> [ex_min, ex_max, s_min, s_max,
# ex_source, ex_evidence, s_source, s_evidence].
RANGES_V1_PATH = Path(__file__).resolve().parent / "data" / "methodology_ranges_v1.json"

GROUPS = "muscle_groups"
CLASSES = "muscle_volume_classes"
RANGES = "muscle_volume_class_ranges"

SOURCE_ENUM = "enum('methodology','derived','admin')"
BUCKET_ENUM = "enum('1x','2x','3x_plus')"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION_057).__globals__


@functools.lru_cache(maxsize=1)
def _module_055() -> dict:
    return m.load_python_migration(MIGRATION_055).__globals__


def _sql(name: str) -> str:
    return " ".join(_module()[name].split())


# ---------------------------------------------------------------------------
# Recorte pós-055
# ---------------------------------------------------------------------------


def _add_column(snap: dict, table: str, col: str, tipo: str, nullable: str, *, ckey: str = "") -> None:
    ultima = max((c["pos"] for c in snap["columns"] if c["t"] == table), default=0)
    snap["columns"].append(
        {"t": table, "pos": ultima + 1, "col": col, "type": tipo, "nullable": nullable,
         "dflt": None, "ckey": ckey, "extra": "", "coll": None, "gen": ""}
    )


def _pos_055() -> dict:
    """O recorte da 055 com a 055 aplicada: é o estado de produção hoje."""
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    _add_column(snap, GROUPS, "size_class", "enum('large','small','abs','unclassified')", "NO")
    _add_column(snap, GROUPS, "methodology_parent_group_id", "bigint", "YES", ckey="MUL")
    snap["indexes"].append(
        {"t": GROUPS, "idx": "idx_mg_methodology_parent", "seq": 1,
         "col": "methodology_parent_group_id", "non_unique": 1}
    )
    snap["foreign_keys"].append(
        {"t": GROUPS, "fk": "fk_mg_methodology_parent", "col": "methodology_parent_group_id",
         "ref_t": GROUPS, "ref_col": "id", "on_delete": "SET NULL", "on_update": "NO ACTION"}
    )
    porte = {nome: size for size, nomes in _module_055()["SEED_PORTE"] for nome in nomes}
    for row in snap["rows"]:
        row["size_class"] = porte.get(row["name"], "unclassified")
    snap["class_rows"] = []
    snap["range_rows"] = []
    return snap


def _com_tabelas(snap: dict) -> dict:
    snap["tables"].append({"table_name": CLASSES, "engine": "InnoDB"})
    snap["tables"].append({"table_name": RANGES, "engine": "InnoDB"})
    for col, (tipo, nulo) in _module()["EXPECTED_CLASSES_COLUMNS"].items():
        _add_column(snap, CLASSES, col, tipo, nulo, ckey="PRI" if col == "id" else "")
    for col, (tipo, nulo) in _module()["EXPECTED_RANGES_COLUMNS"].items():
        _add_column(snap, RANGES, col, tipo, nulo)
    return snap


def _com_coluna(snap: dict, *, fk: bool = True) -> dict:
    _add_column(snap, GROUPS, "volume_class_id", "bigint", "YES", ckey="MUL")
    snap["indexes"].append(
        {"t": GROUPS, "idx": "idx_mg_volume_class", "seq": 1, "col": "volume_class_id", "non_unique": 1}
    )
    if fk:
        snap["foreign_keys"].append(
            {"t": GROUPS, "fk": "fk_mg_volume_class", "col": "volume_class_id",
             "ref_t": CLASSES, "ref_col": "id", "on_delete": "RESTRICT", "on_update": "NO ACTION"}
        )
    for row in snap["rows"]:
        row.setdefault("volume_class_id", None)
    return snap


def _semeado(snap: dict) -> dict:
    """Aplica em MEMÓRIA o efeito do seed."""
    ids = {}
    for pos, (name, _d, _s, size, *_r, faixas) in enumerate(_module()["SEED_CLASSES"], start=1):
        ids[size] = pos
        snap["class_rows"].append({"id": pos, "name": name, "weekly_source": "derived"})
        for faixa in faixas:
            snap["range_rows"].append(
                {"class_id": pos, "frequency_bucket": faixa[0],
                 "sets_source": faixa[7], "exercises_source": faixa[5]}
            )
    for row in snap["rows"]:
        row["volume_class_id"] = ids.get(row["size_class"])
    return snap


def _convergido() -> dict:
    return _semeado(_com_coluna(_com_tabelas(_pos_055())))


def _por_nome(snap: dict) -> dict:
    return {row["name"]: row for row in snap["rows"]}


class _DB057(SnapshotDB):
    """`SnapshotDB` + as leituras de DADO da 057 (grupos, classes e faixas)."""

    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.data_queries: list[str] = []
        self.executed: list[tuple[str, tuple | None]] = []

    def query(self, sql, params=None):
        if sql.strip().upper().startswith("SELECT") and "information_schema" not in sql:
            self.queries.append(sql)
            self.data_queries.append(sql)
            colunas = [c.strip() for c in sql.split("SELECT", 1)[1].split("FROM", 1)[0].split(",")]
            tabela = sql.split("FROM", 1)[1].split()[0]
            fonte = {GROUPS: "rows", CLASSES: "class_rows", RANGES: "range_rows"}[tabela]
            return [{col: row.get(col) for col in colunas} for row in self.snap[fonte]]
        return super().query(sql, params)

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))


def _plan(snapshot, fn="plan"):
    db = _DB057(snapshot)
    return [(" ".join(sql.split()), params) for sql, params in _module()[fn](db)], db


# ---------------------------------------------------------------------------
# Paridade do seed com o motor de hoje
# ---------------------------------------------------------------------------


def test_seed_e_identico_as_celulas_de_methodology_ranges():
    """A garantia do PR: o cadastro nasce dizendo exatamente o que o código
    diz hoje - valores, origem e evidência, célula a célula, incluindo a
    coluna 3x+ que o motor deriva em tempo de execução."""
    visto = {}
    for _name, _d, _s, size, _wmin, _wmax, faixas in _module()["SEED_CLASSES"]:
        for bucket, ex_min, ex_max, s_min, s_max, ex_src, ex_evid, s_src, s_evid in faixas:
            visto[(size, bucket)] = (ex_min, ex_max, s_min, s_max, ex_src, ex_evid, s_src, s_evid)
    esperado = {
        tuple(chave.split("|")): tuple(celula)
        for chave, celula in json.loads(RANGES_V1_PATH.read_text(encoding="utf-8")).items()
    }
    assert len(esperado) == 9
    assert visto == esperado


def test_semanal_nasce_derivada_de_2x_vezes_a_faixa_por_sessao():
    faixas_2x = {
        size: next(f for f in faixas if f[0] == "2x")
        for _n, _d, _s, size, _a, _b, faixas in _module()["SEED_CLASSES"]
    }
    semanal = {size: (wmin, wmax) for _n, _d, _s, size, wmin, wmax, _f in _module()["SEED_CLASSES"]}
    assert semanal == {"large": (18, 32), "small": (12, 24), "abs": (12, 24)}
    for size, faixa in faixas_2x.items():
        assert semanal[size] == (2 * faixa[3], 2 * faixa[4])
    assert _module()["WEEKLY_EVIDENCE"] == "2x × faixa por sessão"


def test_classes_nascem_grandes_pequenos_outros_nessa_ordem():
    nomes = [(name, sort, size) for name, _d, sort, size, *_r in _module()["SEED_CLASSES"]]
    assert nomes == [("Grandes", 1, "large"), ("Pequenos", 2, "small"), ("Outros", 3, "abs")]


# ---------------------------------------------------------------------------
# O plano
# ---------------------------------------------------------------------------


def test_producao_hoje_cria_tabelas_coluna_e_semeia():
    statements, db = _plan(_pos_055())
    sqls = [sql for sql, _ in statements]
    assert sqls[:3] == [_sql("CREATE_CLASSES_SQL"), _sql("CREATE_RANGES_SQL"), _sql("ADD_VOLUME_CLASS_SQL")]
    assert sqls[3:6] == [_sql("INSERT_CLASS_SQL")] * 3
    assert sqls[6:15] == [_sql("INSERT_RANGE_SQL")] * 9
    assert sqls[15:] == [_sql("ASSOCIATE_SQL")]
    assert not db.executed, "plan() é somente leitura"


def test_o_ddl_tem_as_fks_certas():
    classes, ranges, alter = _sql("CREATE_CLASSES_SQL"), _sql("CREATE_RANGES_SQL"), _sql("ADD_VOLUME_CLASS_SQL")
    assert "UNIQUE KEY uq_mvc_name (name)" in classes
    assert "PRIMARY KEY (class_id, frequency_bucket)" in ranges
    assert f"FOREIGN KEY (class_id) REFERENCES {CLASSES}(id) ON DELETE CASCADE" in ranges
    assert f"REFERENCES {CLASSES}(id) ON DELETE RESTRICT" in alter
    assert "AFTER methodology_parent_group_id" in alter
    assert "exercise_catalog" not in classes + ranges + alter
    assert "ENGINE=InnoDB" in classes and "ENGINE=InnoDB" in ranges


def test_size_class_nao_e_tocado():
    """Contract fica para depois: o código velho continua lendo `size_class`
    na janela migração -> deploy."""
    for sql, _ in _plan(_pos_055())[0]:
        assert "DROP COLUMN size_class" not in sql
        assert "SET size_class" not in sql and "SET mg.size_class" not in sql


def test_insert_de_classe_leva_a_semanal_derivada():
    statements, _ = _plan(_pos_055())
    params = [p for sql, p in statements if sql == _sql("INSERT_CLASS_SQL")]
    assert [p[0] for p in params] == ["Grandes", "Pequenos", "Outros"]
    for p in params:
        assert p[5] == "derived"
        assert p[6] == "2x × faixa por sessão"
    assert (params[0][3], params[0][4]) == (18, 32)


def test_faixa_resolve_a_classe_por_nome_no_banco():
    statements, _ = _plan(_pos_055())
    params = [p for sql, p in statements if sql == _sql("INSERT_RANGE_SQL")]
    assert {(p[-1], p[0]) for p in params} == {
        (nome, bucket) for nome in ("Grandes", "Pequenos", "Outros") for bucket in ("1x", "2x", "3x_plus")
    }
    assert "FROM muscle_volume_classes WHERE name = %s" in _sql("INSERT_RANGE_SQL")


def test_associacao_mapeia_size_class_para_classe_num_statement_so():
    statements, _ = _plan(_pos_055())
    assoc = [p for sql, p in statements if sql == _sql("ASSOCIATE_SQL")]
    assert assoc == [("large", "Grandes", "small", "Pequenos", "abs", "Outros")]
    assert "WHERE mg.volume_class_id IS NULL" in _sql("ASSOCIATE_SQL")


def test_efeito_da_associacao_nas_linhas_do_recorte():
    """7 grandes, 7 pequenos, 1 em Outros, 5 sem macro-grupo (os
    `unclassified`: Adutores, Lombar, Flexores do quadril, Manguito, Core)."""
    snap = _convergido()
    contagem: dict = {}
    for row in snap["rows"]:
        contagem[row["volume_class_id"]] = contagem.get(row["volume_class_id"], 0) + 1
    assert contagem == {1: 7, 2: 7, 3: 1, None: 5}
    sem = {row["name"] for row in snap["rows"] if row["volume_class_id"] is None}
    assert sem == set(_module_055()["SEM_PORTE_NO_DOCUMENTO"])


def test_estado_convergido_nao_executa_nada():
    assert _plan(_convergido())[0] == []


def test_seed_interrompido_entre_inserts_completa_so_o_que_falta():
    snap = _com_coluna(_com_tabelas(_pos_055()))
    snap["class_rows"] = [{"id": 1, "name": "Grandes", "weekly_source": "derived"}]
    snap["range_rows"] = [
        {"class_id": 1, "frequency_bucket": "1x", "sets_source": "methodology", "exercises_source": "methodology"}
    ]
    statements, _ = _plan(snap)
    classes = [p[0] for sql, p in statements if sql == _sql("INSERT_CLASS_SQL")]
    faixas = [(p[-1], p[0]) for sql, p in statements if sql == _sql("INSERT_RANGE_SQL")]
    assert classes == ["Pequenos", "Outros"]
    assert len(faixas) == 8 and ("Grandes", "1x") not in faixas
    assert statements[-1][0] == _sql("ASSOCIATE_SQL")


def test_associacao_feita_pelo_admin_impede_o_reseed():
    snap = _convergido()
    _por_nome(snap)["Lombar"]["volume_class_id"] = 3
    _por_nome(snap)["Abdômen"]["volume_class_id"] = None
    assert _plan(snap)[0] == []


def test_edicao_admin_de_faixa_ou_semanal_impede_o_reseed():
    snap = _com_coluna(_com_tabelas(_pos_055()))
    _semeado(snap)
    for row in snap["rows"]:
        row["volume_class_id"] = None
    snap["range_rows"][0]["sets_source"] = "admin"
    assert _plan(snap)[0] == []

    snap2 = _com_coluna(_com_tabelas(_pos_055()))
    _semeado(snap2)
    for row in snap2["rows"]:
        row["volume_class_id"] = None
    snap2["class_rows"][1]["weekly_source"] = "admin"
    assert _plan(snap2)[0] == []


def test_classe_criada_pelo_admin_impede_o_reseed():
    snap = _com_coluna(_com_tabelas(_pos_055()))
    snap["class_rows"] = [{"id": 9, "name": "Posteriores", "weekly_source": "derived"}]
    assert _plan(snap)[0] == []


def test_coluna_sem_a_fk_pede_so_a_fk():
    snap = _semeado(_com_coluna(_com_tabelas(_pos_055()), fk=False))
    assert _plan(snap)[0] == [(_sql("ADD_VOLUME_CLASS_FK_SQL"), None)]


def test_o_plano_nao_le_coluna_que_ainda_nao_existe():
    _, db = _plan(_pos_055())
    assert db.data_queries == ["SELECT id, name, size_class FROM muscle_groups"]
    _, db = _plan(_convergido())
    assert db.data_queries == [
        "SELECT id, name, size_class, volume_class_id FROM muscle_groups",
        "SELECT id, name, weekly_source FROM muscle_volume_classes",
        "SELECT class_id, frequency_bucket, sets_source, exercises_source FROM muscle_volume_class_ranges",
    ]


def test_up_executa_exatamente_o_plano():
    db = _DB057(_pos_055())
    _module()["up"](db)
    assert db.executed == _plan(_pos_055())[0]


# ---------------------------------------------------------------------------
# Recusas
# ---------------------------------------------------------------------------


def test_sem_a_055_falha_antes_de_escrever():
    erro = _module()["VolumeClassesError"]
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    db = _DB057(snap)
    with pytest.raises(erro) as exc:
        _module()["plan"](db)
    assert "055" in str(exc.value)
    assert not db.executed


def test_dry_run_com_a_055_pendente_planeja_o_nascimento_completo():
    """Mesmo padrão da 033/041/042: no dry-run do lote a 055 ainda não criou
    `size_class`, então o plano é o de "nada existe" - sem ler a coluna que
    não existe e sem recusar. Na execução real continua recusando (acima)."""
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    db = _DB057(snap)
    db.dry_run = True
    sqls = [" ".join(sql.split()) for sql, _ in _module()["plan"](db)]
    esperado, _ = _plan(_pos_055())
    assert sqls == [sql for sql, _ in esperado]
    assert all("size_class" not in q for q in db.data_queries)
    assert not db.executed


def test_filha_sem_a_mae_falha():
    erro = _module()["VolumeClassesError"]
    snap = _com_tabelas(_pos_055())
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != CLASSES]
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert "filha sem a mae" in str(exc.value)


@pytest.mark.parametrize(
    "tabela,coluna,tipo",
    [
        (CLASSES, "weekly_source", "enum('methodology','derived')"),
        (CLASSES, "weekly_sets_min", "int"),
        (RANGES, "frequency_bucket", "enum('1x','2x')"),
        (RANGES, "sets_min", "decimal(4,1)"),
    ],
)
def test_tabela_existente_em_outro_formato_falha(tabela, coluna, tipo):
    erro = _module()["VolumeClassesError"]
    snap = _com_tabelas(_pos_055())
    for col in snap["columns"]:
        if col["t"] == tabela and col["col"] == coluna:
            col["type"] = tipo
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert coluna in str(exc.value)


def test_volume_class_id_not_null_falha():
    erro = _module()["VolumeClassesError"]
    snap = _com_tabelas(_pos_055())
    _add_column(snap, GROUPS, "volume_class_id", "bigint", "NO")
    with pytest.raises(erro):
        _plan(snap)


def test_enum_e_comparado_ignorando_espacamento():
    assert _module()["SOURCE_COLUMN_TYPE"] == SOURCE_ENUM
    assert _module()["BUCKET_COLUMN_TYPE"] == BUCKET_ENUM


# ---------------------------------------------------------------------------
# `down`
# ---------------------------------------------------------------------------


def test_down_derruba_fk_coluna_e_tabelas_filha_primeiro():
    statements, _ = _plan(_convergido(), fn="plan_down")
    assert [sql for sql, _ in statements] == [
        _sql("DROP_FK_SQL"), _sql("DROP_COLUMN_SQL"), _sql("DROP_RANGES_SQL"), _sql("DROP_CLASSES_SQL")
    ]


def test_down_sem_a_057_nao_faz_nada():
    assert _plan(_pos_055(), fn="plan_down")[0] == []


def test_migracao_nao_importa_codigo_do_app():
    fonte = MIGRATION_057.read_text(encoding="utf-8")
    assert "from app" not in fonte and "import app" not in fonte
