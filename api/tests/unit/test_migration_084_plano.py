"""Plano da migração 084 (campos "em reavaliação" da TACO preenchidos com o USDA
FoodData Central, M85).

- Banco com a 081 aplicada (campos `*` NULL) e sem a coluna -> 1 `ALTER TABLE`
  (`field_sources_json JSON NULL`) + 1 `UPDATE` por item (os 2 leites) = 3
  statements, valores sempre como parâmetro.
- Convergido -> 0 statements, inclusive com o JSON que o MySQL devolve
  reformatado (chaves em outra ordem, espaços).
- Linha que perdeu o preenchimento (ex.: a 081 reexecutada) -> só o UPDATE dela.
- Campo a preencher com outro valor (medido ou alterado) -> erro antes do 1º
  statement: a migração nunca sobrescreve valor. Tabela/linha ausentes: DRY-RUN
  planeja; execução real falha. Coluna com outro tipo -> erro.
- `plan_down`: NULL nos campos preenchidos + proveniência zerada + DROP COLUMN.
- Os dados embutidos tocam SÓ campos `*` da planilha (a conferência completa,
  com o gerador e o snapshot do USDA, está em
  `test_nutrition_taco_usda_reavaliacao.py`).
- Interação com a 081 congelada: depois da 084 o `plan` da 081 enxerga como
  divergentes exatamente os blocos dos itens preenchidos.

Rodar: cd api && python -m pytest tests/unit/test_migration_084_plano.py
"""

from __future__ import annotations

import functools
import json
from decimal import Decimal

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "084_food_items_taco_usda_reavaliacao.py"
M081 = m.DEFAULT_MIGRATIONS_DIR / "081_food_catalog_seed_taco.py"
JSON_TYPE = ("json", "YES")


@functools.lru_cache(maxsize=None)
def _globals(path) -> dict:
    return m.load_python_migration(path).__globals__


def _module() -> dict:
    return _globals(MIGRATION)


def _seed_081() -> dict[str, dict]:
    """Linhas da 081 como o MySQL devolve (Decimal nos DECIMAL), pelo `source_id`."""
    return {r["source_id"]: dict(r) for r in _globals(M081)["seed_records"]()}


def _json_como_mysql(texto: str) -> str:
    """O MySQL guarda o JSON normalizado: chaves por (tamanho, nome) e `", "`/`": "`."""

    def ordena(valor):
        if isinstance(valor, dict):
            return {chave: ordena(valor[chave]) for chave in sorted(valor, key=lambda k: (len(k), k))}
        if isinstance(valor, list):
            return [ordena(item) for item in valor]
        return valor

    return json.dumps(ordena(json.loads(texto)), ensure_ascii=False, separators=(", ", ": "))


def _linha_084(source_id: str, *, mysql_json: bool = True) -> dict:
    """Linha da TACO depois da 084, com o JSON como o MySQL devolve."""
    registro = next(r for r in _module()["fills"]() if r["source_id"] == source_id)
    linha = {**_seed_081()[source_id], **registro["values"]}
    if mysql_json:
        linha["field_sources_json"] = _json_como_mysql(registro["json"])
        assert linha["field_sources_json"] != registro["json"]  # o texto difere; o conteúdo não
    return linha


class FakeDB:
    def __init__(self, *, tabela=True, coluna=None, linhas=None, dry_run=False, sem_colunas=()):
        self.tabela = tabela
        self.coluna = coluna  # None = coluna ausente; senão (tipo, IS_NULLABLE)
        self.linhas = {sid: dict(_seed_081()[sid]) for sid in self._ids()} if linhas is None else linhas
        self.dry_run = dry_run
        self.sem_colunas = set(sem_colunas)
        self.queries: list[tuple[str, tuple]] = []

    @staticmethod
    def _ids() -> list[str]:
        return [r["source_id"] for r in _module()["fills"]()]

    def query(self, sql, params=None):
        self.queries.append((sql, params))
        if "information_schema.TABLES" in sql:
            assert params == ("food_items",)
            return [{"TABLE_NAME": "food_items"}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            colunas = [c for c in _globals(M081)["WRITE_COLUMNS"] if c not in self.sem_colunas]
            linhas = [{"COLUMN_NAME": c, "COLUMN_TYPE": "decimal(7,2)", "IS_NULLABLE": "YES"} for c in colunas]
            if self.coluna is not None:
                linhas.append({"COLUMN_NAME": "field_sources_json", "COLUMN_TYPE": self.coluna[0],
                               "IS_NULLABLE": self.coluna[1]})
            return linhas
        if sql.startswith("SELECT source_id, ") and "FROM food_items WHERE source = %s AND source_id IN (" in sql:
            assert params[0] == "taco"
            colunas = sql[len("SELECT "): sql.index(" FROM")].split(", ")
            if self.coluna is None:
                assert "field_sources_json" not in colunas, "coluna ausente não pode ir no SELECT"
            return [{c: self.linhas[sid].get(c) for c in colunas} for sid in params[1:] if sid in self.linhas]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


# ---------------------------------------------------------------------------
# Plano
# ---------------------------------------------------------------------------


def test_banco_com_a_081_e_sem_a_coluna_faz_3_statements():
    statements = _module()["plan"](FakeDB())
    assert len(statements) == 1 + len(_module()["FILLS"]) == 3
    assert [r["source_id"] for r in _module()["fills"]()] == ["457", "458"]  # o 450 ficou sem valor
    assert statements[0] == ("ALTER TABLE food_items ADD COLUMN field_sources_json JSON NULL AFTER citation", None)
    for (sql, params), registro in zip(statements[1:], _module()["fills"]()):
        campos = list(registro["values"])
        assert sql == (
            f"UPDATE food_items SET {', '.join(f'{c} = %s' for c in campos)}, field_sources_json = %s "
            "WHERE source = %s AND source_id = %s"
        )
        assert sql.count("%s") == len(params)
        assert "'" not in sql and ":" not in sql, "valor sempre como parâmetro"
        assert params[: len(campos)] == tuple(registro["values"].values())
        assert params[len(campos):] == (registro["json"], "taco", registro["source_id"])
        assert all(isinstance(v, Decimal) for v in params[: len(campos)])


def test_convergido_nao_faz_nada_mesmo_com_o_json_reformatado_pelo_mysql():
    linhas = {sid: _linha_084(sid) for sid in FakeDB._ids()}
    db = FakeDB(coluna=JSON_TYPE, linhas=linhas)
    assert _module()["plan"](db) == []
    assert all(sql.startswith("SELECT") for sql, _ in db.queries)


def test_linha_que_perdeu_o_preenchimento_regrava_so_ela():
    linhas = {sid: _linha_084(sid) for sid in FakeDB._ids()}
    # A 081 reexecutada (teste que apaga a tabela de controle) devolve os `*` a NULL,
    # mas não toca na coluna da 084.
    linhas["458"] = {**_seed_081()["458"], "field_sources_json": linhas["458"]["field_sources_json"]}
    statements = _module()["plan"](FakeDB(coluna=JSON_TYPE, linhas=linhas))
    assert len(statements) == 1 and statements[0][1][-1] == "458"


def test_proveniencia_ausente_com_valores_iguais_grava_so_a_proveniencia_da_linha():
    linhas = {sid: _linha_084(sid) for sid in FakeDB._ids()}
    linhas["457"]["field_sources_json"] = None
    statements = _module()["plan"](FakeDB(coluna=JSON_TYPE, linhas=linhas))
    assert [p[-1] for _, p in statements] == ["457"]


def test_nunca_sobrescreve_valor_diferente():
    linhas = {sid: dict(_seed_081()[sid]) for sid in FakeDB._ids()}
    linhas["458"]["energy_kcal"] = Decimal("64.00")
    with pytest.raises(_module()["TacoUsdaFillError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(linhas=linhas))


def test_tabela_ou_linha_ausente_dry_run_planeja_e_execucao_real_falha():
    assert len(_module()["plan"](FakeDB(tabela=False, dry_run=True))) == 3
    with pytest.raises(_module()["TacoUsdaFillError"], match="a 080 cria"):
        _module()["plan"](FakeDB(tabela=False))
    assert len(_module()["plan"](FakeDB(linhas={}, dry_run=True))) == 3
    with pytest.raises(_module()["TacoUsdaFillError"], match="a 081 grava"):
        _module()["plan"](FakeDB(linhas={}))


@pytest.mark.parametrize("ajuste", [{"coluna": ("longtext", "YES")}, {"coluna": ("json", "NO")},
                                    {"sem_colunas": ("energy_kcal",)}])
def test_estado_incompativel_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_module()["TacoUsdaFillError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(**ajuste))


def test_plan_down_devolve_null_e_apaga_a_coluna():
    linhas = {sid: _linha_084(sid) for sid in FakeDB._ids()}
    statements = _module()["plan_down"](FakeDB(coluna=JSON_TYPE, linhas=linhas))
    assert statements[-1] == ("ALTER TABLE food_items DROP COLUMN field_sources_json", None)
    reversoes = statements[:-1]
    assert [p for _, p in reversoes] == [("taco", sid) for sid in FakeDB._ids()]
    for (sql, _), registro in zip(reversoes, _module()["fills"]()):
        assert sql == (
            f"UPDATE food_items SET {', '.join(f'{c} = NULL' for c in registro['values'])}, field_sources_json = NULL "
            "WHERE source = %s AND source_id = %s"
        )
    assert _module()["plan_down"](FakeDB()) == []  # sem a coluna: nada a desfazer
    assert _module()["plan_down"](FakeDB(tabela=False)) == []
    linhas["457"]["energy_kcal"] = Decimal("1.00")
    with pytest.raises(_module()["TacoUsdaFillError"], match="nao e desta migracao"):
        _module()["plan_down"](FakeDB(coluna=JSON_TYPE, linhas=linhas))


# ---------------------------------------------------------------------------
# Dados embutidos e a 081
# ---------------------------------------------------------------------------


def test_preenche_so_campos_que_a_081_deixou_null_e_nada_que_a_taco_mediu():
    seed = _seed_081()
    for registro in _module()["fills"]():
        linha = seed[registro["source_id"]]
        assert all(linha[campo] is None for campo in registro["values"]), registro["source_id"]
        assert registro["values"], registro["source_id"]
    assert seed["458"]["sodium_mg"] == Decimal("63.76")  # medido pela TACO: fica
    assert "sodium_mg" not in dict(_module()["FILLS"][1][1]) and _module()["FILLS"][1][0] == "458"


def test_depois_da_084_o_plano_da_081_so_ve_os_blocos_dos_itens_preenchidos():
    """Por que os testes de integração da 081 conferem `plan == blocos da 084` (e
    não `[]`): o runner nunca reexecuta a 081, mas o `plan` dela compara com o
    próprio seed."""
    g081 = _globals(M081)
    ids_084 = {r["source_id"] for r in _module()["fills"]()}
    no_banco = {sid: g081["canonical"](linha) for sid, linha in _seed_081().items()}
    for sid in ids_084:
        no_banco[sid] = g081["canonical"](_linha_084(sid, mysql_json=False))
    esperado = [
        g081["upsert_statement"](bloco)
        for bloco in g081["blocks"](g081["seed_records"]())
        if any(r["source_id"] in ids_084 for r in bloco)
    ]
    divergentes = [
        g081["upsert_statement"](bloco)
        for bloco in g081["blocks"](g081["seed_records"]())
        if any(no_banco[r["source_id"]] != g081["canonical"](r) for r in bloco)
    ]
    assert divergentes == esperado and len(esperado) == 1  # bloco 451–500 (457 e 458)


# sha256 (fim de linha LF) da 084 como o runner registra (`migrate.compute_checksum`).
# Migração aplicada NÃO muda (o runner recusa checksum divergente e trava TODAS as
# migrações do deploy). Este valor só pode mudar enquanto a 084 não foi aplicada em
# ambiente nenhum (antes do merge do PR do M85); depois disso, equivalente ou dado
# novo do USDA viram migração NOVA.
CHECKSUM_CONGELADO = "e1642a1750a6acada498c1ba36d2cb8f8ede347ad4e26ce8c4a32faeb9ae4406"


def test_a_084_esta_congelada():
    assert m.compute_checksum(MIGRATION) == CHECKSUM_CONGELADO, (
        "a 084 mudou: se ela já foi aplicada em algum ambiente, desfaça e crie uma migração nova"
    )
