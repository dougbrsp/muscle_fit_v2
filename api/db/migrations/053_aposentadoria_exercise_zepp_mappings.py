"""053 - aposentadoria de `exercise_zepp_mappings` (M13), etapa 1: MEDIR.

O que M13 pedia ("reapontar as FKs remanescentes para `exercise_catalog`") JA
ESTA FEITO. Levantamento contra o `information_schema` REAL da Aurora
(`tests/unit/data/aurora_schema_2026_09_14_028.json`, o unico recorte com as
57 FKs do schema INTEIRO):

- FKs que APONTAM para `exercise_zepp_mappings`: **nenhuma**. As quatro do
  baseline (`fk_gym_equipment_mapping`, `fk_eem_mapping`,
  `fk_exercises_mapping`, `fk_wpe_mapping`) e as duas de 004/001
  (`fk_emc_mapping`, `fk_exercise_aliases_mapping`) ja foram reapontadas para
  `exercise_catalog(id)` pelas migracoes 002, 004, 006, 023 e 028;
- FKs que SAEM dela: so `fk_zm_primary_group` / `fk_zm_secondary_group`
  (-> `muscle_groups`), que nao prendem ninguem.

Ou seja, a tabela ja e uma ilha: nada no schema depende dela.

Por que isso importa para a restricao da 028
--------------------------------------------
A 028 ENUMERA as 10 FKs validas para `exercise_catalog(id)` (`CATALOG_FKS`) e
RECUSA qualquer outra - e registrar uma FK nova na lista quebraria todo banco
criado do zero (a tabela filha nasceria depois). Essa restricao teria mordido
se o M13 precisasse CRIAR uma FK nova para o catalogo. Ele nao precisa: as
tres referencias citadas no item (`exercises.mapping_id`,
`gym_equipment.mapping_id`, `equipment_exercise_mappings.mapping_id`) ja estao
na lista da 028, ja apontam para `exercise_catalog` e nao sao tocadas aqui.
Esta migracao nao cria, altera nem remove NENHUMA FK.

O que sobra do M13, entao
-------------------------
1. **Escrita** (resolvida no MESMO PR, no codigo): `POST
   /exercise-mapping-catalog/import` era a ultima escrita viva na tabela
   (`INSERT ... ON DUPLICATE KEY` por item + `UPDATE ... SET is_active = 0`
   quando `replace=true`). Passou a gravar no catalogo canonico pelo MESMO
   caminho de POST/PUT (`_write_catalog_item`). Nenhuma linha de `app/` le ou
   escreve mais em `exercise_zepp_mappings`.
2. **Dado orfao** (esta migracao): antes de remover a tabela num PR futuro e
   preciso saber se existe linha que so vive nela. Em producao o catalogo tem
   79 linhas canonicas contra 93 no legado (numero ja registrado em
   `app/exercise_mapping_catalog.py::list_mappings`), e parte dessa diferenca
   e consolidacao deliberada de duplicatas - nao da para reinserir as 14 as
   cegas sem ressuscitar duplicata na tela /grupos-musculares.
3. **Remocao** (PR futuro): `DROP TABLE`, so depois de (a) >= 7 dias sem
   `legacy_endpoint_used` para `/exercise-mapping-catalog/import` e (b) a
   decisao humana, item a item, sobre o que esta registrado aqui.

Esta migracao e o passo (2), e SO ele: ela MEDE, nao decide.

    exercise_zepp_mappings_retirement_audit
      legacy_id        BIGINT UNSIGNED PK   `exercise_zepp_mappings.id`
      exercise_name    VARCHAR(255) NOT NULL
      amazfit_name     VARCHAR(255) NOT NULL
      legacy_source    VARCHAR(160) NOT NULL
      legacy_is_active TINYINT(1)   NOT NULL
      recorded_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP

Uma linha por linha do legado SEM representacao canonica. "Representada"
significa qualquer um destes quatro (o mesmo conjunto de fallbacks que o
backend ja usa para resolver um id legado):

  a. `exercise_catalog.id = z.id` - a 002 copiou o legado preservando o id;
  b. `exercise_legacy_id_map.old_exercise_id = z.id` - consolidada numa
     canonica (e exatamente o registro historico dessa consolidacao);
  c. `exercise_catalog.name = z.exercise_name` - mesma linha com id novo;
  d. `exercise_aliases.alias = z.exercise_name` - virou apelido de uma canonica.

DELIBERADAMENTE **sem** INSERT em `exercise_catalog`. Reinserir linha legada no
catalogo e escrita num caminho quente (`exercise_catalog` tem 10 FKs filhas e
alimenta a classificacao de TODO treino); o criterio automatico erraria
justamente nas 14 consolidadas. O que esta tabela entrega e a EVIDENCIA para
essa decisao, congelada no deploy. Em base saudavel ela nasce e fica vazia.

Sem FK para `exercise_zepp_mappings`: a tabela auditada e a que vai embora, e
uma FK CASCADE levaria a evidencia junto com ela.

Fluxo (mesmo padrao da 047..051): `plan(db)` so LE (`information_schema` +
UMA contagem); `up(db)` executa o plano; `down(db)`/`plan_down(db)` sao o
inverso (`DROP TABLE IF EXISTS` - reversivel de verdade: a tabela e so
evidencia, nada a referencia e nenhuma leitura do produto a consulta).

- tabela ausente + nenhum orfao -> 1 `CREATE TABLE`;
- tabela ausente + N orfaos     -> `CREATE TABLE` + 1 `INSERT ... SELECT`;
- convergida                    -> 0 statements (reexecucao);
- `exercise_zepp_mappings` (ou qualquer fonte) ausente -> so o `CREATE TABLE`,
  sem contagem: e o estado do PR que finalmente remover o legado, e a 053 tem
  de continuar rodando num banco criado do zero depois dele;
- tabela presente com coluna/tipo/nulabilidade diferente ou ENGINE != InnoDB
  -> `ZeppRetirementAuditError` ANTES do primeiro statement.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "exercise_zepp_mappings_retirement_audit"
LEGACY_TABLE = "exercise_zepp_mappings"

# Fontes lidas pelo predicado de "representada". Se QUALQUER uma faltar, a
# medicao nao acontece (nao ha como decidir com meia evidencia).
SOURCE_TABLES = (LEGACY_TABLE, "exercise_catalog", "exercise_legacy_id_map", "exercise_aliases")

ALL_TABLES = (TABLE, *SOURCE_TABLES)

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  legacy_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  exercise_name VARCHAR(255) NOT NULL,
  amazfit_name VARCHAR(255) NOT NULL,
  legacy_source VARCHAR(160) NOT NULL,
  legacy_is_active TINYINT(1) NOT NULL,
  recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# Linha do legado SEM representacao canonica (a, b, c, d do cabecalho).
_UNREPRESENTED = f"""
FROM {LEGACY_TABLE} z
WHERE NOT EXISTS (SELECT 1 FROM exercise_catalog c WHERE c.id = z.id)
  AND NOT EXISTS (SELECT 1 FROM exercise_legacy_id_map m WHERE m.old_exercise_id = z.id)
  AND NOT EXISTS (SELECT 1 FROM exercise_catalog cn WHERE cn.name = z.exercise_name)
  AND NOT EXISTS (SELECT 1 FROM exercise_aliases a WHERE a.alias = z.exercise_name)
"""

_NOT_RECORDED = f"  AND NOT EXISTS (SELECT 1 FROM {TABLE} t WHERE t.legacy_id = z.id)\n"

INSERT_SQL = f"""
INSERT INTO {TABLE} (legacy_id, exercise_name, amazfit_name, legacy_source, legacy_is_active)
SELECT z.id, z.exercise_name, z.amazfit_name, z.source, z.is_active
{_UNREPRESENTED}{_NOT_RECORDED}"""

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "legacy_id": ("bigint unsigned", "NO"),
    "exercise_name": ("varchar(255)", "NO"),
    "amazfit_name": ("varchar(255)", "NO"),
    "legacy_source": ("varchar(160)", "NO"),
    "legacy_is_active": ("tinyint(1)", "NO"),
    "recorded_at": ("datetime", "NO"),
}

# `int(11)` -> `int`; `tinyint(1)` NAO e normalizado (a largura 1 e o que
# distingue o booleano do MySQL e esta no DDL acima).
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(smallint|mediumint|int|bigint)\(\d+\)")


class ZeppRetirementAuditError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise ZeppRetirementAuditError(f"053: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", " ".join(_text(column_type).strip().lower().split()))


def _read(db) -> tuple[dict[str, str], dict[tuple[str, str], dict]]:
    placeholders = ", ".join(["%s"] * len(ALL_TABLES))
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            ALL_TABLES,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            ALL_TABLES,
        )
    }
    return engines, columns


def _check_existing(engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get((TABLE, name))
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")


def _count_pending(db, *, audit_exists: bool) -> int:
    """Quantas linhas do legado ficariam por registrar. Uma unica leitura; a
    clausula do proprio registro so entra quando a tabela ja existe."""
    sql = "SELECT COUNT(*) AS n " + _UNREPRESENTED + (_NOT_RECORDED if audit_exists else "")
    row = db.query_one(sql)
    return int(next(iter(row.values())) or 0) if row else 0


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns = _read(db)
    audit_exists = TABLE in engines

    statements: list[tuple[str, tuple | None]] = []
    if audit_exists:
        _check_existing(engines, columns)
    else:
        statements.append((CREATE_SQL, None))

    faltando = [name for name in SOURCE_TABLES if name not in engines]
    if faltando:
        # Sem o legado (ou sem uma das fontes do predicado) nao ha o que medir.
        # E o estado esperado DEPOIS do PR que remover a tabela, e o de um
        # recorte parcial de schema - nos dois casos, seguir e o certo.
        return statements

    if _count_pending(db, audit_exists=audit_exists) > 0:
        statements.append((INSERT_SQL, None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP TABLE` se a tabela existe, senao nada."""
    engines, _columns = _read(db)
    if TABLE not in engines:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
