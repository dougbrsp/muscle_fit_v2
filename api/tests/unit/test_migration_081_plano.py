"""Plano da migração 081 (seed da TACO 4ª ed. em `food_items`, nutrição L3a).

- Banco com a 080 e sem a TACO -> 12 `INSERT ... ON DUPLICATE KEY UPDATE` de
  até 50 linhas (⌈597/50⌉), todos com valores como parâmetro.
- Convergido (contagem 597 e checksum dos dados iguais) -> 0 statements.
- Linha divergente ou ausente -> só o bloco dela.
- `food_items` ausente: DRY-RUN planeja os 12; execução real falha. Coluna
  escrita ausente, UNIQUE (source, source_id) ausente ou linha da TACO fora do
  seed -> erro antes do primeiro statement.
- Dados embutidos: 597 linhas, 15 grupos, nome normalizado = o do motor,
  citação/licença = as do motor, bloco reproduzido pelo gerador a partir da
  planilha versionada (SHA-256 igual), amostra conferida com o PDF oficial.
- Tamanho de cada statement dentro do limite da Data API (SQL <= 64 KiB).

Rodar: cd api && python -m pytest tests/unit/test_migration_081_plano.py
"""

from __future__ import annotations

import functools
import importlib.util
import math
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from app import db_dataapi
from app import migrate as m
from app.domain.nutrition import catalog
from app.domain.nutrition.food_text import normalize_name

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "081_food_catalog_seed_taco.py"
GERADOR = Path(__file__).resolve().parents[2] / "scripts" / "gerar_seed_taco.py"
TOTAL = 597
DATA_API_MAX_SQL_CHARS = 65536  # ExecuteStatement: `sql` com no máximo 65.536 caracteres [AWS]


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


@functools.lru_cache(maxsize=1)
def _gerador():
    spec = importlib.util.spec_from_file_location("gerar_seed_taco", GERADOR)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo  # `@dataclass` resolve o módulo por sys.modules
    spec.loader.exec_module(modulo)
    return modulo


def _linhas_do_banco(registros):
    """O que o MySQL devolve (Decimal nos DECIMAL, int no TINYINT/BIGINT)."""
    return {r["source_id"]: dict(r) for r in registros}


class FakeDB:
    def __init__(self, *, tabela=True, linhas=None, colunas=None, unico=True, dry_run=False):
        self.tabela = tabela
        self.linhas = {} if linhas is None else linhas
        self.colunas = list(_module()["WRITE_COLUMNS"]) if colunas is None else colunas
        self.unico = unico
        self.dry_run = dry_run
        self.queries: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": "food_items"}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            return [{"COLUMN_NAME": c} for c in self.colunas]
        if "information_schema.STATISTICS" in sql:
            if not self.unico:
                return []
            return [{"SEQ_IN_INDEX": 1, "COLUMN_NAME": "source", "NON_UNIQUE": 0},
                    {"SEQ_IN_INDEX": 2, "COLUMN_NAME": "source_id", "NON_UNIQUE": 0}]
        if sql.startswith("SELECT source_id FROM food_items WHERE source = %s"):
            assert params == ("taco",)
            return [{"source_id": sid} for sid in self.linhas]
        if "AND source_id IN (" in sql:
            assert params[0] == "taco"
            return [self.linhas[sid] for sid in params[1:] if sid in self.linhas]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def _linhas_por_statement(sql: str) -> int:
    return sql.count("), (") + 1


def test_banco_com_a_080_e_sem_a_taco_grava_12_blocos_de_ate_50():
    statements = _module()["plan"](FakeDB())
    assert len(statements) == math.ceil(TOTAL / 50) == 12
    colunas = len(_module()["WRITE_COLUMNS"])
    assert [len(p) // colunas for _, p in statements] == [50] * 11 + [47]
    assert sum(len(p) for _, p in statements) == TOTAL * colunas
    for sql, params in statements:
        assert sql.startswith("INSERT INTO food_items (source, source_id, ")
        assert "ON DUPLICATE KEY UPDATE" in sql and "source = VALUES(source)" not in sql
        assert sql.count("%s") == len(params)
        assert "'" not in sql and ":" not in sql, "valor sempre como parâmetro"
        assert _linhas_por_statement(sql) <= _module()["BLOCK_SIZE"]


def test_dry_run_sem_a_tabela_planeja_tudo_e_execucao_real_falha():
    assert len(_module()["plan"](FakeDB(tabela=False, dry_run=True))) == 12
    with pytest.raises(_module()["FoodCatalogSeedError"], match="Nenhum statement executado"):
        _module()["plan"](FakeDB(tabela=False))


def test_convergido_nao_faz_nada():
    db = FakeDB(linhas=_linhas_do_banco(_module()["seed_records"]()))
    assert _module()["plan"](db) == []
    assert all(q.strip().upper().startswith("SELECT") for q in db.queries)


def test_linha_divergente_ou_ausente_regrava_so_o_bloco_dela():
    registros = _module()["seed_records"]()
    linhas = _linhas_do_banco(registros)
    linhas["3"] = {**linhas["3"], "energy_kcal": Decimal("999.00")}  # bloco 1
    del linhas["597"]  # bloco 12
    statements = _module()["plan"](FakeDB(linhas=linhas))
    assert len(statements) == 2
    assert statements[0][1][1] == "1" and "597" in statements[1][1]


def test_texto_com_diferenca_so_de_acento_tambem_diverge():
    linhas = _linhas_do_banco(_module()["seed_records"]())
    linhas["53"] = {**linhas["53"], "name": "Pao, trigo, frances"}
    assert len(_module()["plan"](FakeDB(linhas=linhas))) == 1


@pytest.mark.parametrize(
    "ajuste",
    [
        {"colunas": [c for c in ("source", "source_id", "name")]},
        {"unico": False},
        {"linhas": {"9999": {"source_id": "9999"}}},
    ],
)
def test_estado_incompativel_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_module()["FoodCatalogSeedError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(**ajuste))


def test_plan_down_apaga_so_a_taco():
    assert _module()["plan_down"](FakeDB(linhas={"1": {}})) == [("DELETE FROM food_items WHERE source = %s", ("taco",))]
    assert _module()["plan_down"](FakeDB()) == []
    assert _module()["plan_down"](FakeDB(tabela=False)) == []


# ---------------------------------------------------------------------------
# Dados embutidos
# ---------------------------------------------------------------------------


def test_seed_tem_as_597_linhas_em_15_grupos():
    linhas = _module()["SEED_ROWS"]
    assert [int(linha[0]) for linha in linhas] == list(range(1, TOTAL + 1))
    grupos = {linha[3] for linha in linhas}
    assert len(grupos) == 15 and "Nozes e sementes" in grupos
    assert {linha[3] for linha in linhas if 587 <= int(linha[0]) <= 597} == {"Nozes e sementes"}
    assert all(linha[1] == linha[1].strip() and "  " not in linha[1] for linha in linhas)


# sha256 (fim de linha LF) da 081 como o runner registra (`migrate.compute_checksum`).
# Migração aplicada NÃO muda: o runner recusa arquivo com checksum diferente do
# registrado e trava TODAS as migrações do deploy (`migrate._check_consistency`).
# Este valor só pode mudar enquanto a 081 não foi aplicada em ambiente nenhum
# (antes do merge do PR do L3a); depois disso, dado novo da TACO ou motor
# diferente viram migração NOVA.
CHECKSUM_CONGELADO = "12a9ba23a38c2b00cd7828c698758143b1dcf3c644cc41f604417607c6b1c491"


def test_a_081_esta_congelada():
    assert m.compute_checksum(MIGRATION) == CHECKSUM_CONGELADO, (
        "a 081 mudou: se ela já foi aplicada em algum ambiente, desfaça e crie uma migração nova"
    )


def test_nome_normalizado_embutido_e_o_do_motor():
    """Paridade do `name_normalized` gravado com o motor ATUAL. Vale para a
    migração mais recente que grava `name_normalized` da TACO (hoje a 081). Se o
    motor mudar a normalização, este teste fica vermelho: a 081 continua
    congelada (`CHECKSUM_CONGELADO`) — crie uma migração nova que regrave
    `name_normalized` (TACO e cadastros) e mova esta paridade para ela, deixando
    aqui só a conferência dos dados que a 081 gravou."""
    divergentes = [(linha[0], linha[2], normalize_name(linha[1])) for linha in _module()["SEED_ROWS"]
                   if linha[2] != normalize_name(linha[1])]
    assert divergentes == []


def test_citacao_licenca_e_versao_sao_as_do_motor():
    """Mesma regra da paridade do nome: vale para a migração mais recente que
    grava a citação/licença da TACO; a 081 não é regravada."""
    mod = _module()
    assert mod["CITATION"] == catalog.TACO_CITATION
    assert mod["LICENSE"] == catalog.TACO_LICENSE
    assert mod["SOURCE_VERSION"] == catalog.TACO_SOURCE_VERSION
    assert mod["SOURCE"] == catalog.FoodSource.TACO.value
    assert mod["BASIS"] in [b.value for b in catalog.FOOD_BASES]
    assert set(mod["NULL_COLUMNS"]) | set(mod["SEED_COLUMNS"][4:]) == set(catalog.PER_100_FIELDS)
    assert set(mod["WRITE_COLUMNS"]) >= set(catalog.PER_100_FIELDS)


def test_gerador_reproduz_o_bloco_a_partir_da_planilha_versionada():
    gerador = _gerador()
    assert gerador.sha256_arquivo(gerador.PLANILHA) == _module()["SOURCE_FILE_SHA256"]
    bloco = gerador.gerar_bloco(gerador.ler_planilha(), gerador.sha256_arquivo(gerador.PLANILHA))
    assert gerador.bloco_atual(MIGRATION.read_text(encoding="utf-8")) == bloco


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("NA", None), ("*", None), ("", None), (" ", None), (None, None), ("Tr", Decimal("0.00")), (" Tr", Decimal("0.00")),
     ("2.5499999999999998", Decimal("2.55")), ("128.25848566666664", Decimal("128.26")),
     ("-4.5000000000007923E-2", Decimal("0.00")), ("7.8666666666666663E-2", Decimal("0.08"))],
)
def test_regras_de_conversao_do_gerador(texto, esperado):
    assert _gerador().converter_valor(texto, onde="teste") == esperado


def test_gerador_recusa_negativo_de_verdade_e_marcador_desconhecido():
    with pytest.raises(_gerador().PlanilhaInvalida):
        _gerador().converter_valor("-1.2", onde="teste")
    with pytest.raises(_gerador().PlanilhaInvalida):
        _gerador().converter_valor("ND", onde="teste")


# Valores IMPRESSOS no PDF oficial (NEPA-UNICAMP, 4ª ed., 2011, Tabela 1), grupos
# diferentes: número, kcal, proteína, lipídeos, carboidrato, fibra. A conferência
# completa (597 de 597) está no README de fontes (`scripts/conferir_taco_pdf.py`).
PDF_OFICIAL = [
    (3, "128", "2,5", "0,2", "28,1", "1,6"),
    (7, "394", "13,9", "8,5", "66,6", "9,1"),
    (35, "360", "9,8", "1,4", "75,1", "2,3"),
    (53, "300", "8,0", "3,1", "58,6", "2,3"),
    (88, "77", "0,6", "0,1", "18,4", "2,2"),
    (100, "25", "2,1", "0,5", "4,4", "3,4"),
    (129, "125", "0,6", "0,3", "30,1", "1,6"),
    (157, "15", "1,1", "0,2", "3,1", "1,2"),
    (182, "98", "1,3", "0,1", "26,0", "2,0"),
    (222, "56", "0,3", "Tr", "15,2", "1,3"),
    (260, "884", "NA", "100,0", "NA", "NA"),
    (277, "166", "26,2", "6,0", "0,0", "NA"),
    (317, "243", "26,1", "14,5", "0,0", "NA"),
    (326, "212", "26,7", "10,9", "0,0", "NA"),
    (377, "219", "35,9", "7,3", "0,0", "NA"),
    (410, "159", "32,0", "2,5", "0,0", "NA"),
    (458, "*", "*", "*", "*", "NA"),
    (463, "330", "22,6", "25,2", "3,0", "NA"),
    (471, "9", "0,7", "0,1", "1,5", "NA"),
    (488, "146", "13,3", "9,5", "0,6", "NA"),
    (494, "387", "0,3", "Tr", "99,5", "NA"),
    (507, "309", "0,0", "0,0", "84,0", "NA"),
    (540, "117", "8,7", "6,5", "11,6", "5,1"),
    (561, "76", "4,8", "0,5", "13,6", "8,5"),
    (589, "643", "14,5", "63,5", "15,1", "7,9"),
    (593, "584", "21,2", "50,4", "21,6", "11,9"),
]


@pytest.mark.parametrize(("numero", "kcal", "proteina", "lipideos", "carbo", "fibra"), PDF_OFICIAL, ids=[str(p[0]) for p in PDF_OFICIAL])
def test_amostra_bate_com_o_pdf_oficial(numero, kcal, proteina, lipideos, carbo, fibra):
    """Seed (2 casas) dentro de meia unidade da precisão impressa (+0,01 do
    duplo arredondamento)."""
    linha = dict(zip(_module()["SEED_COLUMNS"], _module()["SEED_ROWS"][numero - 1]))
    for campo, impresso in (("energy_kcal", kcal), ("protein_g", proteina), ("total_fat_g", lipideos),
                            ("carbohydrates_g", carbo), ("fiber_g", fibra)):
        valor = linha[campo]
        if impresso in ("NA", "*"):
            assert valor is None, (numero, campo)
            continue
        assert valor is not None, (numero, campo)
        if impresso == "Tr":
            assert Decimal(valor).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) == 0, (numero, campo, valor)
            continue
        casas = len(impresso.split(",")[1]) if "," in impresso else 0
        tolerancia = Decimal(1).scaleb(-casas) / 2 + Decimal("0.01")
        assert abs(Decimal(valor) - Decimal(impresso.replace(",", "."))) <= tolerancia, (numero, campo, valor, impresso)


# ---------------------------------------------------------------------------
# Tamanho de cada statement na Data API (R6)
# ---------------------------------------------------------------------------


def medir_statements() -> dict:
    """SQL traduzido (`%s` -> `:pN`) e payload estimado de cada bloco, como o
    shim da Data API envia (`db_dataapi._translate_sql`)."""
    maior_sql = maior_payload = 0
    for sql, params in _module()["plan"](FakeDB()):
        traduzido, parametros = db_dataapi._translate_sql(sql, params)
        maior_sql = max(maior_sql, len(traduzido.encode("utf-8")))
        maior_payload = max(maior_payload, db_dataapi._estimated_json_bytes({"sql": traduzido, "parameters": parametros}))
    return {"maior_sql_bytes": maior_sql, "maior_payload_bytes": maior_payload}


def test_cada_statement_cabe_na_data_api():
    medida = medir_statements()
    assert medida["maior_sql_bytes"] < DATA_API_MAX_SQL_CHARS
    assert medida["maior_payload_bytes"] < db_dataapi.BATCH_PAYLOAD_BUDGET_BYTES < db_dataapi.DATA_API_MAX_REQUEST_BYTES


def test_nenhum_parametro_de_texto_tem_formato_de_parametro_nomeado():
    textos = [v for _, p in _module()["plan"](FakeDB()) for v in p if isinstance(v, str)]
    assert not [t for t in textos if re.search(r"(?<!\w):[a-z]", t)]  # inofensivo como valor, mas fica registrado
