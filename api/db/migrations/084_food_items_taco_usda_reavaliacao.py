"""084 - campos "em reavaliação" da TACO preenchidos com o USDA FoodData Central (M85, 2026-10-18).

O seed da TACO 4ª ed. (081, congelada) grava NULL nas células `*` ("análise em
reavaliação na publicação"). Por decisão do usuário (2026-10-17), esses campos
passam a usar o valor do alimento equivalente do USDA FoodData Central (os 2
leites; o iogurte sabor abacaxi ficou sem valor por decisão de 2026-10-18):

    food_items.field_sources_json  JSON NULL (coluna nova)
      origem dos valores da linha que NÃO vieram da fonte da linha
      (`domain/nutrition/catalog.FieldSource`): campos, fonte (`usda`), fdcId,
      versão (`sr_legacy-AAAA-MM-DD`: tipo de dado + publicação no FDC), nome no
      FDC, data da consulta, motivo (`taco_under_review`), citação e licença.
      NULL = todos os valores são da fonte da linha.

    UPDATE das linhas da TACO em `FILLS`: SÓ os campos `*`, com os valores por
    100 g do snapshot versionado (`USDA_SNAPSHOT_FILE`, SHA-256 no bloco),
    mapeados pelo motor do L3b (`external_sources.map_usda_food`: ids de
    nutriente, unidade, energia, base de 100 g) e arredondados para 2 casas pelo
    gerador `api/scripts/gerar_taco_usda_reavaliacao.py`. Literais aqui: nenhuma
    chamada de rede na migração. O que a TACO mediu (ex.: sódio do leite) não muda;
    `NA` e célula vazia continuam NULL. Itens sem equivalente no FDC (iogurte sabor
    abacaxi, coco verde e as preparações cárneas com fibra `*`) ficam NULL: a lista e o motivo estão no
    gerador (`SEM_EQUIVALENTE`) e no mapa §48.1.

Itens já gravados no diário congelam os nutrientes (L4, E4): nada aqui reescreve
`meal_items`/`meals`; só as entradas NOVAS usam os valores preenchidos.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ (`information_schema` + as linhas da TACO de `FILLS`); `up(db)`
executa:

- coluna ausente -> 1 `ALTER TABLE ... ADD COLUMN field_sources_json JSON NULL`;
- cada linha de `FILLS` cujos campos ou cuja proveniência diferem -> 1 `UPDATE`
  (valores como parâmetro); num banco com a 081: 1 ALTER + 2 UPDATE = 3;
- tudo igual -> 0 statements (reexecução);
- campo a preencher com valor DIFERENTE e não nulo (a TACO mediu ou alguém
  mudou) -> erro antes do 1º statement: a migração nunca sobrescreve valor medido;
- `food_items` ausente ou linha da TACO ausente: DRY-RUN planeja tudo (a 080/081
  ainda não gravaram); execução real -> erro; coluna `field_sources_json` com
  outro tipo ou NOT NULL -> erro.

`down`/`plan_down`: devolve NULL aos campos preenchidos (só se ainda forem os
valores desta migração; senão erro), zera a proveniência e faz `DROP COLUMN`.

Interação com a 081 (congelada): o `plan` da 081 compara as linhas com o próprio
seed e, depois desta migração, enxerga os blocos dos itens preenchidos como
divergentes. O runner nunca reexecuta migração aplicada, então isso só aparece
em teste que roda o `plan` da 081 depois da 084 (a integração confere que a
diferença é exatamente a destes itens).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; valores
sempre como parâmetro (nenhuma literal com ':' ou '%' no SQL); módulo
autocontido (não importa código do app).
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

logger = logging.getLogger("fitness.migrate")

TABLE = "food_items"
SOURCE = "taco"
COLUMN = "field_sources_json"
ADD_COLUMN_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} JSON NULL AFTER citation"
DROP_COLUMN_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"

# >>> DADOS GERADOS por api/scripts/gerar_taco_usda_reavaliacao.py (nao editar a mao) >>>
TACO_SOURCE_FILE_SHA256 = 'c6f43399897e7ddd5b065764ade3323a0a50354584ff20112e4781f92256d4db'
USDA_SNAPSHOT_FILE = 'docs/nutricao/fontes/usda_taco_reavaliacao.json'
USDA_SNAPSHOT_SHA256 = '9b9e30f5a35c67d0e974087c6105c1a8949219f05da31c73fd55fda3b5a57b90'
RETRIEVED_ON = '2026-10-18'
# (source_id da TACO, ((campo, valor por 100 g), ...), field_sources_json)
FILLS = (
    ('457', (('energy_kcal', '35.00'), ('carbohydrates_g', '4.86'), ('protein_g', '3.37'), ('total_fat_g', '0.18')),
     '[{"citation":"U.S. Department of Agriculture, Agricultural Research Service. FoodData Central, 2019. fdc.nal.usda.gov. SR Legacy, FDC ID 173432.","fields":["energy_kcal","carbohydrates_g","protein_g","total_fat_g"],"license":"CC0 1.0 Universal (domínio público).","name":"Milk, nonfat, fluid, without added vitamin A and vitamin D (fat free or skim)","reason":"taco_under_review","retrieved_on":"2026-10-18","source":"usda","source_id":"173432","source_version":"sr_legacy-2019-04-01"}]'),
    ('458', (('energy_kcal', '61.00'), ('carbohydrates_g', '4.78'), ('protein_g', '3.15'), ('total_fat_g', '3.27')),
     '[{"citation":"U.S. Department of Agriculture, Agricultural Research Service. FoodData Central, 2019. fdc.nal.usda.gov. SR Legacy, FDC ID 172217.","fields":["energy_kcal","carbohydrates_g","protein_g","total_fat_g"],"license":"CC0 1.0 Universal (domínio público).","name":"Milk, whole, 3.25% milkfat, without added vitamin A and vitamin D","reason":"taco_under_review","retrieved_on":"2026-10-18","source":"usda","source_id":"172217","source_version":"sr_legacy-2019-04-01"}]'),
)
# <<< DADOS GERADOS <<<

class TacoUsdaFillError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise TacoUsdaFillError(f"084: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def fills() -> list[dict]:
    """`FILLS` como registros: source_id, valores (Decimal) e a proveniência (texto JSON)."""
    return [
        {"source_id": source_id, "values": {campo: Decimal(valor) for campo, valor in valores}, "json": origem}
        for source_id, valores, origem in FILLS
    ]


def fill_fields() -> tuple[str, ...]:
    """Campos tocados por alguma linha, na ordem de aparição."""
    campos: list[str] = []
    for registro in fills():
        for campo in registro["values"]:
            if campo not in campos:
                campos.append(campo)
    return tuple(campos)


def _decimal_text(value) -> str:
    return f"{Decimal(_text(value)):.2f}"


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(_text(value))
    return value


def update_statement(record: dict) -> tuple[str, tuple]:
    campos = list(record["values"])
    sql = f"UPDATE {TABLE} SET {', '.join(f'{c} = %s' for c in campos)}, {COLUMN} = %s WHERE source = %s AND source_id = %s"
    return sql, (*(record["values"][c] for c in campos), record["json"], SOURCE, record["source_id"])


def revert_statement(record: dict) -> tuple[str, tuple]:
    campos = list(record["values"])
    sql = f"UPDATE {TABLE} SET {', '.join(f'{c} = NULL' for c in campos)}, {COLUMN} = NULL WHERE source = %s AND source_id = %s"
    return sql, (SOURCE, record["source_id"])


def _read_schema(db):
    """(tabela existe?, {coluna: (tipo, IS_NULLABLE)})."""
    tabelas = db.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    if not tabelas:
        return False, {}
    colunas = {
        _text(row["COLUMN_NAME"]): (_text(row["COLUMN_TYPE"]).strip().lower(), _text(row["IS_NULLABLE"]))
        for row in db.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
    }
    return True, colunas


def _read_rows(db, with_column: bool) -> dict[str, dict]:
    registros = fills()
    if not registros:
        return {}
    colunas = ["source_id", *fill_fields(), *([COLUMN] if with_column else [])]
    marcadores = ", ".join(["%s"] * len(registros))
    linhas = db.query(
        f"SELECT {', '.join(colunas)} FROM {TABLE} WHERE source = %s AND source_id IN ({marcadores})",
        (SOURCE, *(registro["source_id"] for registro in registros)),
    )
    return {_text(linha["source_id"]): linha for linha in linhas}


def _check_columns(colunas: dict) -> bool:
    """Colunas escritas existem; devolve se a coluna da proveniência já existe."""
    faltando = [campo for campo in fill_fields() if campo not in colunas]
    if faltando:
        _fail(f"{TABLE} sem as colunas {faltando}")
    if COLUMN not in colunas:
        return False
    tipo, nulo = colunas[COLUMN]
    if tipo != "json" or nulo != "YES":
        _fail(f"{TABLE}.{COLUMN} e {tipo} IS_NULLABLE={nulo}, esperado json NULL")
    return True


def _divergent_measured(record: dict, row: dict) -> list[str]:
    """Campos a preencher que já têm valor DIFERENTE (nunca sobrescrito)."""
    return [
        campo
        for campo, valor in record["values"].items()
        if row.get(campo) is not None and _decimal_text(row[campo]) != _decimal_text(valor)
    ]


def _converged(record: dict, row: dict) -> bool:
    if any(row.get(campo) is None for campo in record["values"]):
        return False
    try:
        return _json_value(row.get(COLUMN)) == json.loads(record["json"])
    except ValueError:
        return False


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    registros = fills()
    dry_run = bool(getattr(db, "dry_run", False))
    tabela, colunas = _read_schema(db)
    if not tabela:
        if dry_run:
            return [(ADD_COLUMN_SQL, None), *(update_statement(r) for r in registros)]
        _fail(f"tabela {TABLE} nao existe (a 080 cria)")
    tem_coluna = _check_columns(colunas)
    linhas = _read_rows(db, with_column=tem_coluna)
    statements: list[tuple[str, tuple | None]] = [] if tem_coluna else [(ADD_COLUMN_SQL, None)]
    for registro in registros:
        linha = linhas.get(registro["source_id"])
        if linha is None:
            if dry_run:
                statements.append(update_statement(registro))
                continue
            _fail(f"linha {SOURCE} {registro['source_id']} ausente (a 081 grava a TACO)")
        divergentes = _divergent_measured(registro, linha)
        if divergentes:
            _fail(f"linha {SOURCE} {registro['source_id']} ja tem outro valor em {divergentes} (nunca sobrescrito)")
        if tem_coluna and _converged(registro, linha):
            continue
        statements.append(update_statement(registro))
    logger.info("084: %d linha(s) da TACO a preencher; %d statement(s)", len(registros), len(statements))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: NULL nos campos preenchidos (se ainda forem os desta
    migração), proveniência zerada e DROP da coluna."""
    tabela, colunas = _read_schema(db)
    if not tabela or COLUMN not in colunas:
        return []
    linhas = _read_rows(db, with_column=True)
    statements: list[tuple[str, tuple | None]] = []
    for registro in fills():
        linha = linhas.get(registro["source_id"])
        if linha is None:
            continue
        divergentes = _divergent_measured(registro, linha)
        if divergentes:
            _fail(f"linha {SOURCE} {registro['source_id']} tem outro valor em {divergentes} (nao e desta migracao)")
        if any(linha.get(campo) is not None for campo in registro["values"]) or linha.get(COLUMN) is not None:
            statements.append(revert_statement(registro))
    statements.append((DROP_COLUMN_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
