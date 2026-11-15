"""116 - Modelos com visibilidade e escopo `alunos` do `personal_trainer` em `modelos.treino` (ADR 0011, lote 6A).

Decisao do usuario (13/11/2026): Modelos de treino tem dono (`created_by_user_id`, migracao
007) e visibilidade publico/privado; os existentes ficam publicos. O escopo de
`modelos.treino` do grupo de sistema `personal_trainer` passa de `todos` para `alunos`
(alunos = proprios + publicos; a semantica por linha e do lote 6A seguinte). `admin` segue
com todos e `usuario` com `proprios`. `modelos.metas_ativacao`, `modelos.tipos_treino` e
`ia.modelo_periodizacao` NAO mudam. Grupos personalizados nunca sao tocados.

Duas partes, na ordem:

1. DDL idempotente: `workout_plan_templates.visibility ENUM('publico','privado') NOT NULL
   DEFAULT 'publico'`, so se a coluna nao existe. Tabela sem FULLTEXT, entao
   `ALGORITHM=INSTANT` (metadado; o DEFAULT cobre as linhas existentes = publicas). Sem
   FK, sem indice, sem `AFTER`. Coluna presente com tipo/nulabilidade/default diferente:
   `ModelosVisibilidadeError` antes de qualquer escrita.
2. DML (mesmo padrao da 115): `UPDATE access_group_grants SET scope = 'alunos'` nas linhas
   `modelos.treino` do `personal_trainer` (grupo de sistema) ainda em `todos`. Escopo ja
   editado pelo admin (ex.: `proprios`) ou ja em `alunos` NAO e tocado.

`plan(db)` so LE e emite so o necessario; sobre o estado convergido e vazio. Pre-requisito
do DML: tabelas da 111; sem elas o DML e omitido (plano so de DDL, com log), caso do
`--dry-run` em banco novo. Tabelas presentes sem o grupo de sistema `personal_trainer`:
erro proprio antes de qualquer escrita. Ao fim de `up` (fora de `--dry-run`) relê as
linhas ainda em `todos` e levanta o erro proprio se restar alguma.

Retrato congelado: nao importa `api/app`; 111 + 113 + 114 + 115 + 116 conferidos contra
`api/app/access/catalog.py` por teste de paridade.

Impacto em producao: 1 ALTER INSTANT (metadado) e ate 3 linhas atualizadas (3 acoes).
Ordem do deploy: migracao antes do codigo que le `visibility` (RUN_DB_MIGRATIONS=true).

down: volta para `todos` as linhas `modelos.treino` do `personal_trainer` em `alunos` e
remove a coluna `visibility` (perde a visibilidade gravada). Em producao o runner NAO roda
`down`.

Data API: um statement por chamada, SQL parametrizado, sem PREPARE/SET.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

T_GROUPS = "access_groups"
T_GRANTS = "access_group_grants"
TABLE = "workout_plan_templates"
COLUMN = "visibility"
COLUMN_TYPE = "enum('publico','privado')"
COLUMN_DEFAULT = "publico"

SCOPE_TODOS = "todos"
SCOPE_ALUNOS = "alunos"
GROUP_PERSONAL_TRAINER = "personal_trainer"
RESOURCES = ("modelos.treino",)
ACTIONS = ("ver", "editar", "excluir")

# Retrato congelado das linhas ALTERADAS: group_key -> (recurso, acao, escopo novo)
GRANTS_BY_GROUP: dict[str, tuple[tuple[str, str, str], ...]] = {
    GROUP_PERSONAL_TRAINER: tuple(
        (resource, action, SCOPE_ALUNOS) for resource in RESOURCES for action in ACTIONS
    ),
}
TARGET_GROUP_KEYS = tuple(GRANTS_BY_GROUP)

ADD_COLUMN_SQL = (
    f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} ENUM('publico','privado') "
    f"NOT NULL DEFAULT 'publico', ALGORITHM=INSTANT"
)
DROP_COLUMN_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"

_IN = ", ".join(["%s"] * len(RESOURCES))
SELECT_TABLES_SQL = (
    "SELECT TABLE_NAME FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s, %s)"
)
SELECT_COLUMN_SQL = (
    "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s"
)
SELECT_SYSTEM_GROUPS_SQL = f"SELECT id, group_key FROM {T_GROUPS} WHERE is_system = 1"
SELECT_SCOPE_GRANTS_SQL = (
    f"SELECT resource, action FROM {T_GRANTS} "
    f"WHERE group_id = %s AND scope = %s AND resource IN ({_IN})"
)
UPDATE_SCOPE_SQL = (
    f"UPDATE {T_GRANTS} SET scope = %s "
    f"WHERE group_id = %s AND resource IN ({_IN}) AND scope = %s"
)


class ModelosVisibilidadeError(Exception):
    """Estado incompativel, pre-requisito ausente ou atualizacao planejada nao gravada."""


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _tables(db) -> set[str]:
    return {
        _text(row["TABLE_NAME"])
        for row in db.query(SELECT_TABLES_SQL, (TABLE, T_GROUPS, T_GRANTS))
    }


def _column(db):
    rows = list(db.query(SELECT_COLUMN_SQL, (TABLE, COLUMN)))
    return rows[0] if rows else None


def _target_group_ids(db, tables: set[str]) -> dict[str, int] | None:
    """`group_key -> id`; None se a 111 nao foi aplicada; erro se faltar grupo de sistema."""
    faltando = {T_GROUPS, T_GRANTS} - tables
    if faltando:
        logger.info(
            "116: tabela(s) %s ausente(s); a 111 ainda nao foi aplicada. DML omitido.",
            sorted(faltando),
        )
        return None
    group_id_by_key = {
        _text(row["group_key"]): int(row["id"])
        for row in db.query(SELECT_SYSTEM_GROUPS_SQL, None)
    }
    ausentes = [k for k in TARGET_GROUP_KEYS if k not in group_id_by_key]
    if ausentes:
        raise ModelosVisibilidadeError(
            f"116: grupo(s) de sistema {ausentes} ausente(s) em {T_GROUPS}; "
            "rode a 111 antes. Nenhum statement executado."
        )
    return {k: group_id_by_key[k] for k in TARGET_GROUP_KEYS}


def _com_escopo(db, group_id: int, scope: str) -> set[tuple[str, str]]:
    return {
        (_text(row["resource"]), _text(row["action"]))
        for row in db.query(SELECT_SCOPE_GRANTS_SQL, (group_id, scope, *RESOURCES))
    }


def _ddl_plan(db, tables: set[str]) -> list[tuple[str, tuple | None]]:
    dry_run = bool(getattr(db, "dry_run", False))
    if TABLE not in tables:
        if dry_run:
            return [(ADD_COLUMN_SQL, None)]
        raise ModelosVisibilidadeError(
            f"116: tabela {TABLE} nao existe. Nenhum statement executado."
        )
    col = _column(db)
    if col is None:
        return [(ADD_COLUMN_SQL, None)]
    tipo = _text(col["COLUMN_TYPE"]).strip().lower()
    if (
        tipo != COLUMN_TYPE
        or _text(col["IS_NULLABLE"]) != "NO"
        or _text(col["COLUMN_DEFAULT"]) != COLUMN_DEFAULT
    ):
        raise ModelosVisibilidadeError(
            f"116: {TABLE}.{COLUMN} existe com forma inesperada ({tipo}, "
            f"IS_NULLABLE={_text(col['IS_NULLABLE'])}, "
            f"DEFAULT={_text(col['COLUMN_DEFAULT'])}). Nenhum statement executado."
        )
    return []


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura; so o necessario. DDL primeiro, depois o DML."""
    tables = _tables(db)
    statements = _ddl_plan(db, tables)
    ids = _target_group_ids(db, tables)
    if ids is None:
        return statements
    for group_key in TARGET_GROUP_KEYS:
        group_id = ids[group_key]
        if _com_escopo(db, group_id, SCOPE_TODOS):
            statements.append(
                (UPDATE_SCOPE_SQL, (SCOPE_ALUNOS, group_id, *RESOURCES, SCOPE_TODOS))
            )
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    tables = _tables(db)
    statements: list[tuple[str, tuple | None]] = []
    ids = _target_group_ids(db, tables)
    if ids is not None:
        statements += [
            (UPDATE_SCOPE_SQL, (SCOPE_TODOS, ids[group_key], *RESOURCES, SCOPE_ALUNOS))
            for group_key in TARGET_GROUP_KEYS
            if _com_escopo(db, ids[group_key], SCOPE_ALUNOS)
        ]
    if TABLE in tables and _column(db) is not None:
        statements.append((DROP_COLUMN_SQL, None))
    return statements


def up(db):
    statements = plan(db)
    for sql, params in statements:
        db.execute(sql, params)
    if getattr(db, "dry_run", False):
        return
    restantes = []
    for sql, params in statements:
        if sql == UPDATE_SCOPE_SQL:
            restantes += sorted(_com_escopo(db, params[1], SCOPE_TODOS))
    if restantes:
        raise ModelosVisibilidadeError(
            f"116: concessao(oes) (recurso, acao) {restantes} do personal_trainer "
            "ainda em escopo 'todos' apos o UPDATE."
        )


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
