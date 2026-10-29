"""Plano da migracao 055 (porte + agregacao dos grupos musculares - Fase 4,
PR B) contra um recorte de `muscle_groups`.

O recorte (`tests/unit/data/schema_repo_2026_09_26_055.json`, ver `_origem`)
e o primeiro da serie que NAO veio de uma leitura de banco: a forma da tabela
vem do `CREATE TABLE muscle_groups` do baseline `001_schema.sql` e as linhas
(id + nome) vem da secao 4 de `002_reestruturacao.sql` - o UPDATE por id que
o usuario confirmou como "tabela definitiva". Nenhuma conexao foi aberta
porque nada roda localmente (ordem do usuario: o CI e o unico portao), e
porque neste caso o REPOSITORIO e a fonte: os dois arquivos rodam identicos
em DEV e em producao e nenhuma migracao posterior (003..054) toca a forma da
tabela nem os nomes dos grupos. Os dois primeiros testes deste arquivo sao
justamente a amarracao dessas pontas - se o baseline mudar, eles caem.

Producao hoje: sem as colunas -> 1 ALTER + 3 UPDATEs de porte + 4 de
agregacao. Estado convergido -> 0. E o seed nao desfaz edicao de admin:
todo UPDATE so toca linha ainda no default, e o plano nem o emite quando nao
ha linha a tocar.

Rodar: cd api && python -m pytest tests/unit/test_migration_055_plano_aurora.py
"""

from __future__ import annotations

import copy
import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.paridade_028 import SnapshotDB

MIGRATION_055 = m.DEFAULT_MIGRATIONS_DIR / "055_muscle_groups_porte_e_agregacao.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "schema_repo_2026_09_26_055.json"
BASELINE_001 = m.DEFAULT_MIGRATIONS_DIR / "001_schema.sql"
BASELINE_002 = m.DEFAULT_MIGRATIONS_DIR / "002_reestruturacao.sql"

TABELA = "muscle_groups"

ADD_COLUMNS = (
    "ALTER TABLE muscle_groups "
    "ADD COLUMN size_class ENUM('large', 'small', 'abs', 'unclassified') NOT NULL "
    "DEFAULT 'unclassified' AFTER secondary_muscles, "
    "ADD COLUMN methodology_parent_group_id BIGINT NULL AFTER size_class, "
    "ADD KEY idx_mg_methodology_parent (methodology_parent_group_id), "
    "ADD CONSTRAINT fk_mg_methodology_parent FOREIGN KEY (methodology_parent_group_id) "
    "REFERENCES muscle_groups(id) ON DELETE SET NULL"
)
DROP_FK = "ALTER TABLE muscle_groups DROP FOREIGN KEY fk_mg_methodology_parent"
DROP_COLUMNS = (
    "ALTER TABLE muscle_groups DROP COLUMN methodology_parent_group_id, DROP COLUMN size_class"
)


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    # Carregado UMA vez: recarregar criaria uma classe de erro nova a cada
    # chamada e `pytest.raises` deixaria de reconhece-la.
    return m.load_python_migration(MIGRATION_055).__globals__


class _DB055(SnapshotDB):
    """`SnapshotDB` + a leitura de DADO que a 055 faz (`SELECT id, name[,
    size_class][, methodology_parent_group_id] FROM muscle_groups`).

    A 055 precisa ler linha, e nao so `information_schema`: o que resta
    semear depende do que ja foi semeado e do que o admin mudou. O
    `SnapshotDB` sozinho recusaria essa consulta."""

    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.dry_run = False
        self.data_queries: list[str] = []

    def query(self, sql, params=None):
        if sql.strip().upper().startswith("SELECT") and "information_schema" not in sql:
            self.queries.append(sql)
            self.data_queries.append(sql)
            colunas = [c.strip() for c in sql.split("SELECT", 1)[1].split("FROM", 1)[0].split(",")]
            return [{col: row.get(col) for col in colunas} for row in self.snap["rows"]]
        return super().query(sql, params)

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no snapshot: {sql}")


def _plan(snapshot):
    db = _DB055(snapshot)
    return [(" ".join(sql.split()), params) for sql, params in _module()["plan"](db)], db


def _plan_down(snapshot):
    db = _DB055(snapshot)
    return [(" ".join(sql.split()), params) for sql, params in _module()["plan_down"](db)], db


# ---------------------------------------------------------------------------
# Estados sinteticos
# ---------------------------------------------------------------------------


def _com_coluna(snap: dict, col: str, tipo: str, *, nullable="YES", dflt=None, **overrides) -> dict:
    ultima = max(c["pos"] for c in snap["columns"])
    nova = {
        "t": TABELA, "pos": ultima + 1, "col": col, "type": tipo, "nullable": nullable,
        "dflt": dflt, "ckey": "", "extra": "", "coll": None, "gen": "",
    }
    nova.update(overrides)
    snap["columns"].append(nova)
    return snap


def _com_colunas_da_055(snap: dict) -> dict:
    _com_coluna(
        snap, "size_class", "enum('large','small','abs','unclassified')",
        nullable="NO", dflt="unclassified",
    )
    _com_coluna(snap, "methodology_parent_group_id", "bigint", ckey="MUL")
    snap["indexes"].append(
        {"t": TABELA, "idx": "idx_mg_methodology_parent", "seq": 1,
         "col": "methodology_parent_group_id", "non_unique": 1}
    )
    snap["foreign_keys"].append(
        {"t": TABELA, "fk": "fk_mg_methodology_parent", "col": "methodology_parent_group_id",
         "ref_t": TABELA, "ref_col": "id", "on_delete": "SET NULL", "on_update": "NO ACTION"}
    )
    return snap


def _semeado(snap: dict) -> dict:
    """Aplica em MEMORIA o efeito do seed sobre as linhas do recorte."""
    por_nome = {row["name"]: row for row in snap["rows"]}
    for row in snap["rows"]:
        row.setdefault("size_class", "unclassified")
        row.setdefault("methodology_parent_group_id", None)
    for size_class, nomes in _module()["SEED_PORTE"]:
        for nome in nomes:
            if nome in por_nome:
                por_nome[nome]["size_class"] = size_class
    for nome_pai, fatias in _module()["SEED_AGREGACAO"]:
        pai = por_nome.get(nome_pai)
        if pai is None:
            continue
        for nome in fatias:
            if nome in por_nome and por_nome[nome]["id"] != pai["id"]:
                por_nome[nome]["methodology_parent_group_id"] = pai["id"]
    return snap


def _convergido(snapshot: dict) -> dict:
    """Recorte com a 055 JA aplicada (DDL + seed): o estado pos-deploy."""
    return _semeado(_com_colunas_da_055(copy.deepcopy(snapshot)))


def _por_nome(snap: dict) -> dict:
    return {row["name"]: row for row in snap["rows"]}


# ---------------------------------------------------------------------------
# O recorte
# ---------------------------------------------------------------------------


def test_o_recorte_descreve_a_tabela_do_baseline_001():
    """A ponte entre "montei do repositorio" e "isto e producao": o recorte
    tem de bater com o `CREATE TABLE` do baseline, o unico lugar em que esta
    tabela foi definida e que rodou igual nos dois ambientes."""
    ddl = BASELINE_001.read_text(encoding="utf-8")
    bloco = re.search(
        rf"CREATE TABLE IF NOT EXISTS {TABELA} \((.*?)\n\) ENGINE=InnoDB", ddl, re.DOTALL
    )
    assert bloco, "o baseline 001 precisa continuar definindo muscle_groups"
    corpo = bloco.group(1)
    do_recorte = [c["col"] for c in sorted(_snapshot()["columns"], key=lambda c: c["pos"])]
    assert do_recorte == [
        "id", "name", "primary_muscles", "secondary_muscles", "created_at", "updated_at"
    ]
    for coluna in do_recorte:
        assert re.search(rf"^\s*{coluna}\s", corpo, re.MULTILINE), coluna
    assert "name VARCHAR(120) NOT NULL UNIQUE" in corpo
    # E o inverso: nada de porte no baseline, senao a 055 seria redundante.
    assert "size_class" not in corpo
    assert "methodology_parent_group_id" not in corpo


def test_o_recorte_tem_exatamente_os_nomes_que_a_002_fixou():
    """As LINHAS do recorte sao a secao 4 da 002 ("tabela definitiva
    confirmada pelo usuario"). Se alguem renomear um grupo por migracao, este
    teste cai junto com o seed que depende do nome."""
    ddl = BASELINE_002.read_text(encoding="utf-8")
    da_002 = {
        int(match.group(2)): match.group(1)
        for match in re.finditer(
            r"UPDATE muscle_groups SET name = '((?:[^']|'')*)'.*?WHERE id = (\d+);", ddl
        )
    }
    do_recorte = {row["id"]: row["name"] for row in _snapshot()["rows"]}
    assert do_recorte == da_002
    assert len(do_recorte) == 20


def test_todo_grupo_real_aparece_em_exatamente_uma_decisao_de_porte():
    """Nenhum grupo pode ficar de fora por esquecimento: ou esta numa lista de
    porte, ou esta declarado em `SEM_PORTE_NO_DOCUMENTO`. E nenhum pode estar
    nos dois."""
    mod = _module()
    reais = {row["name"] for row in _snapshot()["rows"]}
    classificados: list[str] = []
    for _size, nomes in mod["SEED_PORTE"]:
        classificados.extend(nomes)
    sem_porte = list(mod["SEM_PORTE_NO_DOCUMENTO"])
    assert len(classificados) == len(set(classificados)), "grupo em duas classes de porte"
    assert not set(classificados) & set(sem_porte)
    assert set(classificados) | set(sem_porte) == reais


def test_nenhum_nome_do_seed_e_inventado():
    mod = _module()
    reais = {row["name"] for row in _snapshot()["rows"]}
    for _size, nomes in mod["SEED_PORTE"]:
        assert set(nomes) <= reais
    for nome_pai, fatias in mod["SEED_AGREGACAO"]:
        assert nome_pai in reais
        assert set(fatias) <= reais
    assert set(mod["SEM_PORTE_NO_DOCUMENTO"]) <= reais


def test_o_abdomen_e_o_abs_e_o_core_fica_sem_porte():
    """D4 da fase: `Core` viraria `abs` SE fosse o abdomen do MuscleFit. Nao
    e - `Abdomen` existe como grupo proprio, e `Core` e o de estabilizacao.
    Somar os dois como abdomen contaria serie de estabilizacao como serie de
    abdomen."""
    mod = _module()
    porte = {nome: size for size, nomes in mod["SEED_PORTE"] for nome in nomes}
    assert porte["Abdômen"] == "abs"
    assert "Core" not in porte
    assert "Core" in mod["SEM_PORTE_NO_DOCUMENTO"]


def test_as_coxas_ficam_em_dois_blocos_e_nenhum_agrega_no_outro():
    """D3 da fase, recomendacao aplicada: quadriceps e posterior sao
    prescritos separadamente, entao nenhum dos dois e fatia do outro."""
    mod = _module()
    porte = {nome: size for size, nomes in mod["SEED_PORTE"] for nome in nomes}
    assert porte["Quadríceps"] == "large"
    assert porte["Posterior de coxa / Isquiotibiais"] == "large"
    fatias = {nome for _pai, nomes in mod["SEED_AGREGACAO"] for nome in nomes}
    assert "Quadríceps" not in fatias
    assert "Posterior de coxa / Isquiotibiais" not in fatias


def test_cada_fatia_tem_o_mesmo_porte_do_bloco_em_que_ela_soma():
    """Se a fatia somar no bloco com outro porte, a faixa comparada muda
    conforme a linha que a avaliacao olhar primeiro."""
    mod = _module()
    porte = {nome: size for size, nomes in mod["SEED_PORTE"] for nome in nomes}
    for nome_pai, fatias in mod["SEED_AGREGACAO"]:
        for fatia in fatias:
            assert porte[fatia] == porte[nome_pai], (fatia, nome_pai)


def test_nenhuma_fatia_e_pai_de_outra_nem_de_si_mesma():
    """Um nivel so: a agregacao e fatia -> bloco, nunca uma corrente."""
    mod = _module()
    pais = {nome for nome, _fatias in mod["SEED_AGREGACAO"]}
    fatias = {nome for _pai, nomes in mod["SEED_AGREGACAO"] for nome in nomes}
    assert not pais & fatias
    for nome_pai, nomes in mod["SEED_AGREGACAO"]:
        assert nome_pai not in nomes


# ---------------------------------------------------------------------------
# O plano
# ---------------------------------------------------------------------------


def test_producao_hoje_faz_um_alter_e_depois_o_seed():
    sqls = [sql for sql, _ in _plan(_snapshot())[0]]
    assert sqls[0] == ADD_COLUMNS
    assert len(sqls) == 1 + 3 + 4  # ALTER + 3 portes + 4 agregacoes
    assert all(sql.startswith("UPDATE muscle_groups") for sql in sqls[1:])


def test_o_seed_de_porte_manda_os_nomes_certos_para_cada_classe():
    mod = _module()
    esperado = {size: set(nomes) for size, nomes in mod["SEED_PORTE"]}
    visto = {}
    for sql, params in _plan(_snapshot())[0]:
        if "SET size_class" not in sql:
            continue
        size_class, default, *nomes = params
        assert default == "unclassified"
        visto[size_class] = set(nomes)
    assert visto == esperado


def test_o_seed_de_agregacao_resolve_o_pai_por_id_e_nunca_aponta_para_a_propria_linha():
    """O id do pai e resolvido em PYTHON, no plano, e nao por subconsulta: em
    MySQL, `UPDATE t SET x = (SELECT ... FROM t)` e o erro 1093. E o `id <>
    %s` do WHERE impede o ciclo trivial mesmo que dois nomes da lista caiam na
    mesma linha."""
    por_nome = _por_nome(_snapshot())
    esperado = {
        por_nome[pai]["id"]: {por_nome[fatia]["id"] for fatia in fatias}
        for pai, fatias in _module()["SEED_AGREGACAO"]
    }
    visto = {}
    for sql, params in _plan(_snapshot())[0]:
        if "SET methodology_parent_group_id" not in sql:
            continue
        assert "IS NULL AND id <> %s" in sql
        pai_id, pai_id_de_novo, *nomes = params
        assert pai_id == pai_id_de_novo
        visto[pai_id] = {por_nome[nome]["id"] for nome in nomes}
    assert visto == esperado


def test_o_seed_so_toca_linha_que_ainda_esta_no_default():
    for sql, _params in _plan(_snapshot())[0]:
        if not sql.startswith("UPDATE"):
            continue
        assert "size_class = %s AND name IN" in sql or "IS NULL AND id <> %s" in sql


def test_o_plano_nunca_insere_nem_apaga_linha():
    """O seed CLASSIFICA o que existe. Criar grupo que o usuario nao criou, ou
    apagar um que ele criou, seria outra migracao."""
    for sql, _ in _plan(_snapshot())[0]:
        assert not sql.startswith(("INSERT", "DELETE", "REPLACE"))


def test_estado_convergido_nao_executa_nada():
    assert _plan(_convergido(_snapshot()))[0] == []


def test_reexecucao_apos_aplicar_e_zero_statements():
    snap = _snapshot()
    assert len(_plan(snap)[0]) == 8
    assert _plan(_convergido(snap))[0] == []


def test_grupo_novo_nasce_sem_porte_e_o_seed_nao_o_inventa():
    """DEFAULT `unclassified`: grupo criado pelo admin (ou pelo
    `_find_or_create_muscle_group`) depois da migracao fica nao classificado,
    e reexecutar a 055 nao chuta porte para ele."""
    snap = _convergido(_snapshot())
    snap["rows"].append(
        {"id": 900, "name": "Serratil anterior", "size_class": "unclassified",
         "methodology_parent_group_id": None}
    )
    assert _plan(snap)[0] == []


def test_reclassificacao_do_admin_sobrevive_a_reexecucao():
    """"Editavel por admin" tem de valer tambem contra a propria migracao: se o
    admin marcar `Gluteos` como grande, reexecutar a 055 NAO pode devolve-lo a
    `small`."""
    snap = _convergido(_snapshot())
    _por_nome(snap)["Glúteos"]["size_class"] = "large"
    assert _plan(snap)[0] == []


def test_desagregacao_feita_pelo_admin_sobrevive_a_reexecucao():
    """O caso que so um guard de `IS NULL` NAO resolveria: o admin desagregou
    `Peitoral superior`, e `NULL` ali e indistinguivel de "nunca semeado".
    Quem segura e a regra de nascimento - numa tabela em que alguem ja mexeu,
    o seed nao roda."""
    snap = _convergido(_snapshot())
    _por_nome(snap)["Peitoral superior"]["methodology_parent_group_id"] = None
    assert _plan(snap)[0] == []


def test_desagregar_tudo_e_desclassificar_tudo_devolve_a_tabela_ao_seed():
    """O outro lado da mesma regra, dito por inteiro: a tabela SO volta a ser
    semeada se estiver inteiramente no default - que e o estado de uma
    aplicacao interrompida entre o ALTER e os UPDATEs. Se o admin zerar a
    classificacao inteira a mao, o seed volta; e uma consequencia declarada,
    nao um acidente."""
    snap = _com_colunas_da_055(copy.deepcopy(_snapshot()))
    for row in snap["rows"]:
        row["size_class"] = "unclassified"
        row["methodology_parent_group_id"] = None
    sqls = [sql for sql, _ in _plan(snap)[0]]
    assert len(sqls) == 7
    assert all(sql.startswith("UPDATE muscle_groups") for sql in sqls)


def test_uma_unica_classificacao_manual_ja_impede_o_reseed():
    """A regra e "ninguem tocou", nao "a maioria nao tocou": um grupo
    classificado a mao ja e prova de que a tabela nao e mais a recem-migrada,
    e a migracao nao sabe mais que o admin sobre ela."""
    snap = _com_colunas_da_055(copy.deepcopy(_snapshot()))
    for row in snap["rows"]:
        row["size_class"] = "unclassified"
        row["methodology_parent_group_id"] = None
    _por_nome(snap)["Manguito rotador"]["size_class"] = "small"
    assert _plan(snap)[0] == []


def test_grupo_renomeado_some_do_seed_sem_derrubar_o_resto():
    """Nome inexistente nao faz nada - o `IN` nao casa. Os outros continuam."""
    snap = copy.deepcopy(_snapshot())
    _por_nome(snap)["Glúteos"]["name"] = "Glúteo máximo"
    sqls = [(sql, params) for sql, params in _plan(snap)[0]]
    small = next(params for sql, params in sqls if sql.startswith("UPDATE") and "small" in params)
    assert "Glúteos" not in small
    assert "Bíceps" in small


def test_pai_ausente_deixa_a_fatia_sem_agregacao_em_vez_de_falhar():
    snap = copy.deepcopy(_snapshot())
    snap["rows"] = [row for row in snap["rows"] if row["name"] != "Peitoral"]
    sqls = [sql for sql, _ in _plan(snap)[0]]
    assert len([sql for sql in sqls if "SET methodology_parent_group_id" in sql]) == 3


def test_estado_parcial_pede_so_a_coluna_que_falta():
    snap = copy.deepcopy(_snapshot())
    _com_coluna(
        snap, "size_class", "enum('large','small','abs','unclassified')",
        nullable="NO", dflt="unclassified",
    )
    for row in snap["rows"]:
        row["size_class"] = "unclassified"
    sqls = [sql for sql, _ in _plan(snap)[0]]
    assert sqls[0] == (
        "ALTER TABLE muscle_groups ADD COLUMN methodology_parent_group_id BIGINT NULL "
        "AFTER size_class, ADD KEY idx_mg_methodology_parent (methodology_parent_group_id), "
        "ADD CONSTRAINT fk_mg_methodology_parent FOREIGN KEY (methodology_parent_group_id) "
        "REFERENCES muscle_groups(id) ON DELETE SET NULL"
    )
    assert ADD_COLUMNS not in sqls


def test_coluna_sem_a_fk_pede_so_a_fk():
    """Aplicacao interrompida entre a coluna e a constraint."""
    snap = _com_colunas_da_055(copy.deepcopy(_snapshot()))
    snap["foreign_keys"] = []
    _semeado(snap)
    sqls = [sql for sql, _ in _plan(snap)[0]]
    assert sqls == [
        "ALTER TABLE muscle_groups ADD CONSTRAINT fk_mg_methodology_parent "
        "FOREIGN KEY (methodology_parent_group_id) REFERENCES muscle_groups(id) ON DELETE SET NULL"
    ]


def test_o_plano_nao_le_coluna_que_ainda_nao_existe():
    """Antes do ALTER, `SELECT size_class` seria erro de SQL. O plano so pede
    as colunas que o `information_schema` confirmou."""
    _, db = _plan(_snapshot())
    assert db.data_queries == ["SELECT id, name FROM muscle_groups"]

    _, db_convergido = _plan(_convergido(_snapshot()))
    assert db_convergido.data_queries == [
        "SELECT id, name, size_class, methodology_parent_group_id FROM muscle_groups"
    ]


def test_o_plano_nao_escreve_nada():
    _, db = _plan(_snapshot())
    assert db.queries
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)


def test_o_plano_so_olha_a_tabela_de_grupos():
    """A 055 nao depende de nenhuma outra tabela - por isso nao depende da
    ordem em relacao a nenhuma outra migracao do lote."""
    _, db = _plan(_snapshot())
    assert all(TABELA in q or "TABLE_NAME IN (%s)" in q for q in db.queries)


# ---------------------------------------------------------------------------
# Recusas
# ---------------------------------------------------------------------------


def test_tabela_ausente_ou_com_engine_errada_falha():
    erro = _module()["MuscleGroupPorteError"]

    sem = copy.deepcopy(_snapshot())
    sem["tables"] = []
    with pytest.raises(erro):
        _plan(sem)

    myisam = copy.deepcopy(_snapshot())
    myisam["tables"][0]["engine"] = "MyISAM"
    with pytest.raises(erro):
        _plan(myisam)


@pytest.mark.parametrize(
    "tipo,nullable,dflt",
    [
        # ENUM com outro dominio: o porte deixaria de significar o que a
        # metodologia define.
        ("enum('large','small','abs')", "NO", "unclassified"),
        ("enum('grande','pequeno','abdomen','indefinido')", "NO", "unclassified"),
        ("varchar(20)", "NO", "unclassified"),
        # NULL e 'unclassified' diriam a mesma coisa de dois jeitos.
        ("enum('large','small','abs','unclassified')", "YES", "unclassified"),
        # Sem o DEFAULT, grupo criado por codigo VELHO (janela inversa) nao
        # teria valor e o INSERT falharia.
        ("enum('large','small','abs','unclassified')", "NO", None),
        ("enum('large','small','abs','unclassified')", "NO", "large"),
    ],
)
def test_size_class_divergente_falha_antes_de_qualquer_statement(tipo, nullable, dflt):
    erro = _module()["MuscleGroupPorteError"]
    snap = _com_coluna(copy.deepcopy(_snapshot()), "size_class", tipo, nullable=nullable, dflt=dflt)
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert "Nenhum statement executado" in str(exc.value)


@pytest.mark.parametrize("tipo,nullable", [("int", "YES"), ("bigint", "NO")])
def test_parent_divergente_falha_antes_de_qualquer_statement(tipo, nullable):
    erro = _module()["MuscleGroupPorteError"]
    snap = _com_coluna(
        copy.deepcopy(_snapshot()), "methodology_parent_group_id", tipo, nullable=nullable
    )
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert "Nenhum statement executado" in str(exc.value)


def test_o_enum_e_comparado_ignorando_o_espacamento_do_mysql():
    """O DDL escreve `ENUM('large', 'small', ...)` e o MySQL devolve
    `enum('large','small',...)`. Comparar cru faria toda reexecucao falhar."""
    assert _plan(_convergido(_snapshot()))[0] == []


# ---------------------------------------------------------------------------
# `down`
# ---------------------------------------------------------------------------


def test_down_derruba_a_fk_antes_das_colunas():
    sqls = [sql for sql, _ in _plan_down(_convergido(_snapshot()))[0]]
    assert sqls == [DROP_FK, DROP_COLUMNS]


def test_down_sobre_um_banco_que_nunca_aplicou_a_055_nao_faz_nada():
    assert _plan_down(_snapshot())[0] == []


def test_down_nao_precisa_desfazer_o_seed():
    """O seed vive NAS colunas: derrubadas elas, a classificacao some junto.
    Um UPDATE de "des-seed" seria escrever em coluna que esta prestes a
    deixar de existir."""
    sqls = [sql for sql, _ in _plan_down(_convergido(_snapshot()))[0]]
    assert not any(sql.startswith("UPDATE") for sql in sqls)


def test_up_e_down_falam_das_mesmas_pecas():
    criados = " ".join(sql for sql, _ in _plan(_snapshot())[0])
    removidos = " ".join(sql for sql, _ in _plan_down(_convergido(_snapshot()))[0])
    for peca in ("size_class", "methodology_parent_group_id", "fk_mg_methodology_parent"):
        assert peca in criados, peca
        assert peca in removidos, peca
