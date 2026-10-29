"""023 - reaponta as FKs de `mapping_id` para `exercise_catalog(id)`.

Tabelas: `gym_equipment.mapping_id` e `equipment_exercise_mappings.mapping_id`.
No DDL de 001_schema.sql elas referenciam a tabela legada
`exercise_zepp_mappings` (`fk_gym_equipment_mapping` / `fk_eem_mapping`), mas a
fonte canonica e `exercise_catalog` (ids preservados do legado). Enquanto a FK
aponta para o legado, o runtime contorna: `gym_equipment_sync` exclui do sync
exercicios que so existem no catalogo (`gym_equipment_sync.fk_legacy_excluded`)
e `equipment_exercises` pula o espelho legado
(`equipment_exercises.legacy_mirror_skipped`). Com a FK canonica esses
contornos viram no-op.

Nomes e alvos reais das FKs podem diferir entre ambientes (o schema
documentado da Aurora ja mostra as duas colunas `bigint unsigned` com FK para
`exercise_catalog`), entao tudo e DESCOBERTO em runtime via
`information_schema.KEY_COLUMN_USAGE` + `REFERENTIAL_CONSTRAINTS`.

Desenho (sem PREPARE / multi-statement / GET_LOCK - limites da Data API):

1. Planejamento, SO leituras, para as duas tabelas ANTES de qualquer DDL:
   - FK ja referencia `exercise_catalog(id)` e nao ha outra FK na coluna: no-op.
   - FK canonica + FK legada (retomada apos falha parcial): so DROP da legada.
   - Senao:
     a. tipos: `mapping_id` precisa ter o mesmo tipo/sinal de
        `exercise_catalog.id`. A UNICA divergencia aceita e a conhecida do DDL
        local (`bigint` -> `bigint unsigned`, a mesma correcao que a 006 fez em
        `workout_plan_exercises.mapping_id`; MySQL recusa a FK com ER 3780), e
        so se a coluna nao tiver DEFAULT/EXTRA/COMMENT a preservar. Qualquer
        outra divergencia FALHA (sem alterar coluna as cegas).
     b. orfaos: linhas com `mapping_id` sem `exercise_catalog.id` correspondente
        FALHAM com a contagem por tabela (sem ids/dados). Ids negativos (unico
        risco do BIGINT -> UNSIGNED) sao orfaos por definicao.
     c. regras ON DELETE / ON UPDATE copiadas da FK legada
        (`REFERENTIAL_CONSTRAINTS`). Sem FK nenhuma (retomada depois do DROP),
        usa CASCADE / NO ACTION - as regras do 001_schema.sql e da Aurora.
2. Execucao, um statement por chamada:
   - tipos compativeis: [ADD INDEX se faltar] -> ADD CONSTRAINT nova -> DROP
     da legada (nunca fica sem FK no meio);
   - conversao de tipo: DROP da legada -> MODIFY COLUMN -> [ADD INDEX] -> ADD
     CONSTRAINT nova (MySQL nao deixa mudar coluna presa a FK).

Reexecutavel apos falha parcial: cada rodada recalcula o plano a partir do
schema real. Em dry-run o runner so registra os statements planejados.
"""

import re

CANONICAL_TABLE = "exercise_catalog"
CANONICAL_COLUMN = "id"
COLUMN = "mapping_id"

# (tabela, nome da FK canonica, nome do indice criado se nao houver indice
# comecando por mapping_id)
TARGETS = (
    ("gym_equipment", "fk_gym_equipment_mapping_catalog", "idx_gym_equipment_mapping"),
    ("equipment_exercise_mappings", "fk_eem_mapping_catalog", "idx_eem_mapping"),
)

FALLBACK_RULES = ("CASCADE", "NO ACTION")
ALLOWED_RULES = frozenset({"CASCADE", "SET NULL", "RESTRICT", "NO ACTION"})
CONVERTIBLE_TYPES = ("bigint", "bigint unsigned")

_DISPLAY_WIDTH = re.compile(r"\(\d+\)")


class MappingFkMigrationError(Exception):
    """Pre-condicao nao atendida; nenhum DDL desta execucao foi aplicado."""


def _text(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return value


def _normalize_type(column_type):
    # "bigint(20) unsigned" (MySQL 5.7) == "bigint unsigned" (MySQL 8).
    return " ".join(_DISPLAY_WIDTH.sub("", str(_text(column_type) or "")).lower().split())


def _count(row):
    return int(row["n"] or 0) if row else 0


def _column_info(db, table, column):
    return db.query_one(
        "SELECT COLUMN_TYPE AS column_type, IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default, "
        "EXTRA AS extra, COLUMN_COMMENT AS column_comment FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )


def _mapping_foreign_keys(db, table):
    rows = db.query(
        "SELECT k.CONSTRAINT_NAME AS constraint_name, k.REFERENCED_TABLE_NAME AS referenced_table, "
        "k.REFERENCED_COLUMN_NAME AS referenced_column, r.DELETE_RULE AS delete_rule, "
        "r.UPDATE_RULE AS update_rule "
        "FROM information_schema.KEY_COLUMN_USAGE k "
        "JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
        "ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.TABLE_NAME = k.TABLE_NAME "
        "AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME "
        "WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME = %s AND k.COLUMN_NAME = %s "
        "AND k.REFERENCED_TABLE_NAME IS NOT NULL "
        "ORDER BY k.CONSTRAINT_NAME",
        (table, COLUMN),
    )
    return [{key: _text(value) for key, value in row.items()} for row in rows]


def _has_leading_index(db, table):
    return _count(
        db.query_one(
            "SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s AND SEQ_IN_INDEX = 1",
            (table, COLUMN),
        )
    ) > 0


def _count_orphans(db, table):
    # `table` vem de TARGETS (constante), nunca de entrada externa.
    return _count(
        db.query_one(
            f"SELECT COUNT(*) AS n FROM {table} t WHERE t.{COLUMN} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {CANONICAL_TABLE} c WHERE c.{CANONICAL_COLUMN} = t.{COLUMN})"
        )
    )


def _is_canonical(fk):
    return fk["referenced_table"] == CANONICAL_TABLE and fk["referenced_column"] == CANONICAL_COLUMN


def _rules(table, legacy):
    if not legacy:
        return FALLBACK_RULES
    distinct = {(str(fk["delete_rule"]).upper(), str(fk["update_rule"]).upper()) for fk in legacy}
    if len(distinct) > 1:
        raise MappingFkMigrationError(
            f"023: {table}.{COLUMN} tem FKs com regras ON DELETE/ON UPDATE diferentes {sorted(distinct)}; "
            "nao ha regra unica a preservar. Nenhum DDL executado."
        )
    delete_rule, update_rule = distinct.pop()
    for rule in (delete_rule, update_rule):
        if rule not in ALLOWED_RULES:
            raise MappingFkMigrationError(f"023: regra de FK inesperada em {table}.{COLUMN}: {rule!r}. Nenhum DDL executado.")
    return delete_rule, update_rule


def _drop_fk(table, name):
    return f"ALTER TABLE {table} DROP FOREIGN KEY `{name}`"


def _plan_table(db, table, fk_name, index_name, parent_type):
    if not db.table_exists(table) or not db.column_exists(table, COLUMN):
        return []

    fks = _mapping_foreign_keys(db, table)
    canonical = [fk for fk in fks if _is_canonical(fk)]
    legacy = [fk for fk in fks if not _is_canonical(fk)]

    if canonical:
        # A FK canonica ja garante a integridade; so sobram FKs extras (ex.:
        # execucao anterior interrompida entre o ADD e o DROP).
        return [_drop_fk(table, fk["constraint_name"]) for fk in legacy]

    info = _column_info(db, table, COLUMN)
    child_type = _normalize_type(info["column_type"])
    convert = False
    if child_type != parent_type:
        if (child_type, parent_type) != CONVERTIBLE_TYPES:
            raise MappingFkMigrationError(
                f"023: tipo incompativel para FK: {table}.{COLUMN} e {child_type!r}, "
                f"{CANONICAL_TABLE}.{CANONICAL_COLUMN} e {parent_type!r}. So a conversao "
                f"{CONVERTIBLE_TYPES[0]!r} -> {CONVERTIBLE_TYPES[1]!r} e automatica; "
                "trate este caso numa migracao nova. Nenhum DDL executado."
            )
        extras = [
            name
            for name in ("column_default", "extra", "column_comment")
            if (str(_text(info.get(name)) or "").strip() if info.get(name) is not None else "")
        ]
        if extras:
            raise MappingFkMigrationError(
                f"023: {table}.{COLUMN} precisa de conversao de tipo, mas tem atributos que o MODIFY "
                f"descartaria ({', '.join(extras)}). Nenhum DDL executado."
            )
        convert = True

    orphans = _count_orphans(db, table)
    if orphans:
        raise MappingFkMigrationError(
            f"023: {table}.{COLUMN} tem {orphans} linha(s) sem registro correspondente em "
            f"{CANONICAL_TABLE}.{CANONICAL_COLUMN}; a FK nao pode ser reapontada. Corrija os dados "
            "(migracao nova) e rode de novo. Nenhum DDL executado."
        )

    delete_rule, update_rule = _rules(table, legacy)
    drops = [_drop_fk(table, fk["constraint_name"]) for fk in legacy]
    drop_first = convert or any(fk["constraint_name"] == fk_name for fk in legacy)

    statements = list(drops) if drop_first else []
    if convert:
        null_sql = "NULL" if str(_text(info["is_nullable"])).upper() == "YES" else "NOT NULL"
        statements.append(f"ALTER TABLE {table} MODIFY COLUMN {COLUMN} BIGINT UNSIGNED {null_sql}")
    if not _has_leading_index(db, table):
        if db.index_exists(table, index_name):
            raise MappingFkMigrationError(
                f"023: {table} ja tem um indice {index_name} que nao comeca por {COLUMN}. Nenhum DDL executado."
            )
        statements.append(f"ALTER TABLE {table} ADD INDEX {index_name} ({COLUMN})")
    statements.append(
        f"ALTER TABLE {table} ADD CONSTRAINT {fk_name} FOREIGN KEY ({COLUMN}) "
        f"REFERENCES {CANONICAL_TABLE} ({CANONICAL_COLUMN}) ON DELETE {delete_rule} ON UPDATE {update_rule}"
    )
    if not drop_first:
        statements.extend(drops)
    return statements


def up(db):
    if not db.table_exists(CANONICAL_TABLE):
        raise MappingFkMigrationError(f"023: tabela {CANONICAL_TABLE} nao existe. Nenhum DDL executado.")
    parent = _column_info(db, CANONICAL_TABLE, CANONICAL_COLUMN)
    if not parent:
        raise MappingFkMigrationError(f"023: coluna {CANONICAL_TABLE}.{CANONICAL_COLUMN} nao existe. Nenhum DDL executado.")
    parent_type = _normalize_type(parent["column_type"])

    # Todas as pre-checagens (das duas tabelas) antes do primeiro DDL.
    plans = [_plan_table(db, table, fk_name, index_name, parent_type) for table, fk_name, index_name in TARGETS]
    for statements in plans:
        for statement in statements:
            db.execute(statement)
