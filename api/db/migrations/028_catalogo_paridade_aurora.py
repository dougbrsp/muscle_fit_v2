"""028 - DEV/CI no formato da Aurora: catalogo de exercicios e suas FKs.

Comparacao completa do `information_schema` (colunas: tipo, nulabilidade,
default, extra; indices; FKs) da Aurora de producao (extraido em 2026-09-14),
do MySQL de dev e de um banco novo de CI (001..027). Dev e CI tem exatamente as
mesmas divergencias estruturais; esta migracao resolve as 3 primeiras:

1. `exercise_catalog.id` sem AUTO_INCREMENT (002 criou a tabela preservando os
   ids de `exercise_zepp_mappings`). Na Aurora e AUTO_INCREMENT: `POST
   /exercise-mapping-catalog` de um exercicio NOVO (INSERT sem id) funciona la
   e falha em dev/CI com ER 1364 ("Field 'id' doesn't have a default value").
2. `exercises.mapping_id` BIGINT com FK `fk_exercises_mapping` para a tabela
   legada `exercise_zepp_mappings`. Na Aurora: BIGINT UNSIGNED com
   `fk_exercises_mapping_catalog` -> `exercise_catalog(id)` ON DELETE SET NULL.
3. `exercise_legacy_id_map`: FK `fk_exercise_legacy_id_map_canonical` ON DELETE
   CASCADE e indice `idx_exercise_legacy_id_map_canonical`. Na Aurora:
   `fk_elim_canonical` ON DELETE NO ACTION, indice `fk_elim_canonical`.
4. `gyms.owner_user_id` NULL. Na Aurora: NOT NULL (FK `fk_gyms_owner` RESTRICT
   igual nos tres ambientes).

Fora do escopo (decisao de dado, sem statement - ver PR):
- item 4: todo banco novo (001) semeia as academias "Gavioes" e "SmartFit" sem
  dono e sem nenhum usuario; o runner roda em banco novo (CI `backend-smoke`,
  `bootstrap_local_db.py`). Falhar ali quebraria todo ambiente novo, aplicar so
  onde nao ha NULL faria dev (0 NULL) e CI divergirem, e inventar dono esta
  fora de questao. Nenhum statement em `gyms`;
- `exercise_aliases.uq_exercise_aliases_alias` (UNIQUE global por alias) existe
  so em dev/CI;
- `exercise_mappings_backup_20260714` e `exercises_backup_before_mapping_sync`
  existem so na Aurora (backups ad-hoc do onprem).

MySQL 8 (medido no mysql:8.0 do CI) recusa `MODIFY` em coluna referenciada por
FK, mesmo so para acrescentar AUTO_INCREMENT (ER 1833), e recusa trocar o tipo
de coluna presa a FK (ER 3780). `SET FOREIGN_KEY_CHECKS = 0` depende de estado
de sessao entre chamadas, que a Data API nao garante. Por isso, quando o id
precisa de AUTO_INCREMENT, TODAS as FKs para `exercise_catalog(id)` saem antes
do `MODIFY` e voltam depois, no formato da Aurora (`CATALOG_FKS`). As FKs que
apontam para o catalogo na Aurora sao exatamente essas 10; qualquer outra FK
para `exercise_catalog` faz a migracao falhar (nao ha como recria-la com
seguranca).

Fluxo: `plan(db)` so LE (`information_schema` + contagens) e devolve os
statements; `up(db)` executa o plano. Toda incompatibilidade e detectada no
plano, antes do primeiro statement - nada e aplicado:

- `exercise_catalog.id` = 0 (AUTO_INCREMENT renumeraria a linha) -> falha;
- linha filha sem `exercise_catalog` correspondente para uma FK a criar -> falha;
- tipo/nulabilidade/default inesperados, tabela/coluna/indice ausente, nome de
  FK ja usado por outra coluna, FK para outro alvo nao previsto -> falha.

Na Aurora: 0 statements e nenhuma contagem (so as 4 leituras de schema).
Data API: sem PREPARE/SET/multi-statement; um statement por chamada.
Reexecutavel: se falhar no meio (DDL faz commit implicito), o plano seguinte
parte do schema real e completa o que falta.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

CATALOG = "exercise_catalog"
CATALOG_ID_DDL = "BIGINT UNSIGNED NOT NULL AUTO_INCREMENT"


# NamedTuple (e nao dataclass): o runner carrega a migracao por caminho de
# arquivo, sem registrar o modulo em sys.modules.
class CatalogFk(NamedTuple):
    table: str
    column: str
    name: str
    on_delete: str
    nullable: str  # IS_NULLABLE da coluna na Aurora
    index: str  # indice que comeca pela coluna, na Aurora
    legacy_ref: Optional[str] = None  # FK antiga substituida (so exercises.mapping_id)


# FKs para exercise_catalog(id) na Aurora (ON UPDATE NO ACTION em todas). Coluna
# filha BIGINT UNSIGNED em todas.
# `legacy_ref`: FK antiga para `exercise_zepp_mappings` (com coluna BIGINT com
# sinal) aceita e substituida. Em `exercises.mapping_id` e o estado real de
# dev/CI. Em `gym_equipment`/`equipment_exercise_mappings` e o estado ANTES da
# 023: numa execucao real a 023 ja rodou, mas no `--dry-run` de um banco novo o
# runner so planeja a 023 (nao aplica), e a 028 planeja sobre o schema sem ela -
# aceitar o estado pre-023 evita um dry-run que falharia so por isso. O plano
# resultante e o mesmo formato final da 023 (nomes/regras da Aurora).
_ZEPP = "exercise_zepp_mappings"
CATALOG_FKS = (
    CatalogFk("equipment_exercise_mappings", "mapping_id", "fk_eem_mapping_catalog", "CASCADE", "NO", "idx_eem_mapping", _ZEPP),
    CatalogFk("equipment_exercises", "exercise_id", "fk_eqex_exercise", "CASCADE", "NO", "idx_eqex_exercise_active"),
    CatalogFk(
        "exercise_aliases", "exercise_id", "fk_exercise_aliases_exercise_catalog", "CASCADE", "NO", "idx_exercise_aliases_exercise"
    ),
    CatalogFk("exercise_external_mappings", "exercise_id", "fk_exm_exercise", "CASCADE", "NO", "idx_exm_exercise_provider_active"),
    CatalogFk("exercise_legacy_id_map", "canonical_exercise_id", "fk_elim_canonical", "NO ACTION", "NO", "fk_elim_canonical"),
    CatalogFk("exercise_muscle_contributions", "exercise_id", "fk_emc_exercise_catalog", "CASCADE", "NO", "uq_emc_exercise_group"),
    CatalogFk("exercises", "mapping_id", "fk_exercises_mapping_catalog", "SET NULL", "YES", "idx_exercises_mapping_id", _ZEPP),
    CatalogFk(
        "gym_equipment", "mapping_id", "fk_gym_equipment_mapping_catalog", "CASCADE", "NO", "idx_gym_equipment_mapping", _ZEPP
    ),
    CatalogFk("workout_plan_exercises", "mapping_id", "fk_wpe_mapping_catalog", "SET NULL", "YES", "idx_wpe_mapping"),
    CatalogFk("workout_plan_template_slot_items", "mapping_id", "fk_wptsi_mapping", "SET NULL", "YES", "idx_wptsi_mapping"),
)

TABLES = tuple(dict.fromkeys((CATALOG, *(fk.table for fk in CATALOG_FKS))))

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(smallint|mediumint|int|bigint)\(\d+\)")


class CatalogoParidadeError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise CatalogoParidadeError(f"028: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `bigint(20) unsigned` (MySQL 5.7) == `bigint unsigned` (MySQL 8).
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", " ".join(_text(column_type).strip().lower().split()))


def _in_clause(values) -> str:
    return ", ".join(["%s"] * len(values))


def _count(db, sql: str) -> int:
    row = db.query_one(sql)
    return int(next(iter(row.values())) or 0) if row else 0


def _read_schema(db):
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({_in_clause(TABLES)})",
            TABLES,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): {key: row[key] for key in row}
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA, COLUMN_COMMENT "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({_in_clause(TABLES)})",
            TABLES,
        )
    }
    indexes: dict[tuple[str, str], dict] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({_in_clause(TABLES)}) "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
        TABLES,
    ):
        entry = indexes.setdefault(
            (_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"])), {"columns": [], "unique": int(row["NON_UNIQUE"]) == 0}
        )
        entry["columns"].append(_text(row["COLUMN_NAME"]))
    # Todas as FKs do schema: nomes de FK sao unicos por schema no InnoDB.
    fks = [
        {key: _text(value) for key, value in row.items()}
        for row in db.query(
            "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
            "r.DELETE_RULE, r.UPDATE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
            "ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME "
            "AND r.TABLE_NAME = k.TABLE_NAME "
            "WHERE k.TABLE_SCHEMA = DATABASE() AND k.REFERENCED_TABLE_NAME IS NOT NULL"
        )
    ]
    return engines, columns, indexes, fks


def _column(columns, table: str, name: str) -> dict:
    info = columns.get((table, name))
    if info is None:
        _fail(f"coluna {table}.{name} nao existe")
    return info


def _require_no_default_or_comment(label: str, info: dict) -> None:
    # MODIFY COLUMN reescreve a definicao inteira: nada a preservar alem do tipo.
    if info["COLUMN_DEFAULT"] is not None or _text(info.get("COLUMN_COMMENT")):
        _fail(f"{label} tem DEFAULT/COMMENT que o MODIFY descartaria")


def _is_target(fk: dict, spec: CatalogFk) -> bool:
    return (
        fk["CONSTRAINT_NAME"] == spec.name
        and fk["REFERENCED_TABLE_NAME"] == CATALOG
        and fk["REFERENCED_COLUMN_NAME"] == "id"
        and fk["DELETE_RULE"] == spec.on_delete
        and fk["UPDATE_RULE"] == "NO ACTION"
    )


def _plan_catalog(db, columns, indexes, fks) -> list[tuple[str, tuple | None]]:
    catalog_id = _column(columns, CATALOG, "id")
    if _normalize_type(catalog_id["COLUMN_TYPE"]) != "bigint unsigned" or _text(catalog_id["IS_NULLABLE"]) != "NO":
        _fail(f"{CATALOG}.id e {_text(catalog_id['COLUMN_TYPE'])} NULL={_text(catalog_id['IS_NULLABLE'])}, esperado bigint unsigned NOT NULL")
    if _text(catalog_id["COLUMN_KEY"]) != "PRI":
        _fail(f"{CATALOG}.id nao e PRIMARY KEY")
    needs_auto_increment = "auto_increment" not in _text(catalog_id["EXTRA"]).lower()

    specs = {(spec.table, spec.column): spec for spec in CATALOG_FKS}
    for fk in fks:
        if fk["REFERENCED_TABLE_NAME"] == CATALOG and (fk["TABLE_NAME"], fk["COLUMN_NAME"]) not in specs:
            _fail(f"FK {fk['CONSTRAINT_NAME']} de {fk['TABLE_NAME']}.{fk['COLUMN_NAME']} para {CATALOG} nao prevista")

    drops: list[str] = []
    modifies: list[str] = []
    renames: list[str] = []
    adds: list[str] = []
    orphan_checks: list[CatalogFk] = []

    for spec in CATALOG_FKS:
        label = f"{spec.table}.{spec.column}"
        info = _column(columns, spec.table, spec.column)
        if _text(info["IS_NULLABLE"]) != spec.nullable:
            _fail(f"{label} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {spec.nullable}")
        column_type = _normalize_type(info["COLUMN_TYPE"])
        # Unica conversao aceita: BIGINT -> BIGINT UNSIGNED na coluna com FK legada
        # (mesma correcao que a 006/023 fizeram nas outras colunas mapping_id).
        converting = column_type != "bigint unsigned"
        if converting:
            if not (spec.legacy_ref and column_type == "bigint" and not _text(info["EXTRA"])):
                _fail(f"{label} e {_text(info['COLUMN_TYPE'])}, esperado bigint unsigned")
            _require_no_default_or_comment(label, info)
            null_sql = "NULL" if spec.nullable == "YES" else "NOT NULL"
            modifies.append(f"ALTER TABLE {spec.table} MODIFY COLUMN {spec.column} BIGINT UNSIGNED {null_sql}")

        col_fks = [fk for fk in fks if fk["TABLE_NAME"] == spec.table and fk["COLUMN_NAME"] == spec.column]
        for fk in col_fks:
            if fk["REFERENCED_TABLE_NAME"] not in (CATALOG, spec.legacy_ref):
                _fail(f"FK {fk['CONSTRAINT_NAME']} de {label} aponta para {fk['REFERENCED_TABLE_NAME']}, nao prevista")
        for fk in fks:
            if fk["CONSTRAINT_NAME"] == spec.name and (fk["TABLE_NAME"], fk["COLUMN_NAME"]) != (spec.table, spec.column):
                _fail(f"constraint {spec.name} ja existe em {fk['TABLE_NAME']}.{fk['COLUMN_NAME']}")

        target = next((fk for fk in col_fks if _is_target(fk, spec)), None)
        enforced_by_catalog = any(fk["REFERENCED_TABLE_NAME"] == CATALOG for fk in col_fks)
        # Coluna alterada (a filha ou o id referenciado) nao pode ter FK presa.
        rebuild = needs_auto_increment or converting
        for fk in col_fks:
            if fk is not target or rebuild:
                drops.append(f"ALTER TABLE {spec.table} DROP FOREIGN KEY {fk['CONSTRAINT_NAME']}")
        if target is None or rebuild:
            if not enforced_by_catalog:
                orphan_checks.append(spec)
            adds.append(
                f"ALTER TABLE {spec.table} ADD CONSTRAINT {spec.name} FOREIGN KEY ({spec.column}) "
                f"REFERENCES {CATALOG}(id) ON DELETE {spec.on_delete}"
            )

        table_indexes = {name: idx for (table, name), idx in indexes.items() if table == spec.table}
        expected = table_indexes.get(spec.index)
        if expected is not None:
            if expected["columns"][0] != spec.column:
                _fail(f"indice {spec.index} de {spec.table} existe em {expected['columns']}, esperado comecando por {spec.column}")
            continue
        simple = [
            name
            for name, idx in table_indexes.items()
            if name != "PRIMARY" and not idx["unique"] and idx["columns"] == [spec.column]
        ]
        if len(simple) != 1:
            _fail(f"indice {spec.index} ({spec.column}) ausente em {spec.table}")
        renames.append(f"ALTER TABLE {spec.table} RENAME INDEX {simple[0]} TO {spec.index}")

    if needs_auto_increment:
        _require_no_default_or_comment(f"{CATALOG}.id", catalog_id)
        zero_ids = _count(db, f"SELECT COUNT(*) AS n FROM {CATALOG} WHERE id = 0")
        if zero_ids:
            _fail(f"{zero_ids} linha(s) de {CATALOG} com id = 0; AUTO_INCREMENT renumeraria a linha")
    for spec in orphan_checks:
        orphans = _count(
            db,
            f"SELECT COUNT(*) AS n FROM {spec.table} c WHERE c.{spec.column} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {CATALOG} p WHERE p.id = c.{spec.column})",
        )
        if orphans:
            _fail(f"{orphans} linha(s) de {spec.table}.{spec.column} sem {CATALOG} correspondente; FK {spec.name} nao pode ser criada")

    catalog_modify = [f"ALTER TABLE {CATALOG} MODIFY COLUMN id {CATALOG_ID_DDL}"] if needs_auto_increment else []
    # Ordem: sem FK nenhuma presa as colunas alteradas antes dos MODIFY.
    return [(sql, None) for sql in drops + modifies + catalog_modify + renames + adds]


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes, fks = _read_schema(db)
    for table in TABLES:
        if table not in engines:
            _fail(f"tabela {table} nao existe")
        if engines[table].lower() != "innodb":
            _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    # Todas as leituras e contagens acontecem aqui, antes do primeiro statement.
    return _plan_catalog(db, columns, indexes, fks)


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)
