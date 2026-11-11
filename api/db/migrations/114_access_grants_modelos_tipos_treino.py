"""114 - Concessoes de `modelos.tipos_treino` ao grupo de sistema `personal_trainer` (ADR 0011, lote 2C-1).

Os tipos de treino (`/training-types`, hoje `require_admin_or_pt`, inclusive nos
GET) viram recurso controlavel por grupo: `modelos.tipos_treino` (dominio
`Modelos`, rotulo "Tipos de treino"). Recurso proprio porque `modelos.treino`/`ver`
e concedido a `usuario`; reaproveita-lo abriria a rota. Somente o grupo de sistema
`personal_trainer` recebe `ver`/`editar`/`excluir` com escopo `todos`; `usuario` e
`free` nao recebem nada; `admin` segue com acesso total implicito (sem linhas).
Retrato congelado: nao importa `api/app`; o acumulado 111 + 113 + 114 e conferido
contra `api/app/access/catalog.py` por teste de paridade.

So DML em `access_group_grants` (sem DDL, sem FK nova). A 111, 112 e 113 nao sao
editadas.

Idempotencia: `plan(db)` so LE (tabelas, grupo de sistema, concessoes atuais do
recurso) e emite so o que falta, um `INSERT IGNORE` parametrizado por
(grupo, acao). Concessao ja existente, mesmo com escopo editado pelo admin, NAO e
tocada. Sobre o estado convergido o plano e vazio. Grupo que nao e de sistema
(mesmo com a mesma `group_key`) e outras concessoes nunca sao tocados.

Pre-requisito: a 111. Se `access_groups` e/ou `access_group_grants` nao existirem,
`plan(db)` devolve plano VAZIO (sem erro, sem escrita, com log): caso do
`--dry-run` em banco novo. Tabelas presentes mas `personal_trainer` ausente como
grupo de sistema (`is_system = 1`): `AccessGrantsModelosTiposTreinoError` antes de
qualquer escrita.

Verificacao ao fim de `up`: o `INSERT IGNORE` engole qualquer erro, nao so
duplicidade; por isso `up` relê as concessoes e levanta o erro proprio se alguma
linha planejada nao existir. Em `--dry-run` (`db.dry_run`) nada e escrito, entao a
releitura e pulada.

Impacto em producao: com a 111 aplicada insere ate 3 linhas (1 grupo x 3 acoes);
nada le `modelos.tipos_treino` ate a conversao das rotas `/training-types`
(mesmo lote), entao o comportamento so muda quando elas forem convertidas.

down: remove so as concessoes de `modelos.tipos_treino` do grupo de sistema
`personal_trainer`. Em producao o runner nao roda `down`.

Data API: um statement por chamada, SQL parametrizado, sem PREPARE/SET.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

T_GROUPS = "access_groups"
T_GRANTS = "access_group_grants"

RESOURCE = "modelos.tipos_treino"

ACTION_VER = "ver"
ACTION_EDITAR = "editar"
ACTION_EXCLUIR = "excluir"
ACTIONS = (ACTION_VER, ACTION_EDITAR, ACTION_EXCLUIR)

SCOPE_TODOS = "todos"

GROUP_PERSONAL_TRAINER = "personal_trainer"

# Retrato congelado das linhas desta migracao: group_key -> (recurso, acao, escopo)
GRANTS_BY_GROUP: dict[str, tuple[tuple[str, str, str], ...]] = {
    GROUP_PERSONAL_TRAINER: tuple((RESOURCE, action, SCOPE_TODOS) for action in ACTIONS),
}
TARGET_GROUP_KEYS = tuple(GRANTS_BY_GROUP)

SELECT_TABLES_SQL = (
    "SELECT TABLE_NAME FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)"
)
SELECT_SYSTEM_GROUPS_SQL = f"SELECT id, group_key FROM {T_GROUPS} WHERE is_system = 1"
SELECT_RESOURCE_GRANTS_SQL = (
    f"SELECT group_id, action FROM {T_GRANTS} WHERE resource = %s"
)
INSERT_GRANT_SQL = (
    f"INSERT IGNORE INTO {T_GRANTS} (group_id, resource, action, scope) "
    "VALUES (%s, %s, %s, %s)"
)
DELETE_GRANT_SQL = f"DELETE FROM {T_GRANTS} WHERE group_id = %s AND resource = %s"


class AccessGrantsModelosTiposTreinoError(Exception):
    """Pre-requisito (111) incompleto ou concessao planejada nao gravada."""


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _target_group_ids(db) -> dict[str, int] | None:
    """`group_key -> id` dos grupos de sistema alvo; None se a 111 nao foi aplicada.
    Levanta o erro proprio se faltar algum grupo de sistema alvo."""
    presentes = {
        _text(row["TABLE_NAME"])
        for row in db.query(SELECT_TABLES_SQL, (T_GROUPS, T_GRANTS))
    }
    faltando = {T_GROUPS, T_GRANTS} - presentes
    if faltando:
        logger.info(
            "114: tabela(s) %s ausente(s); a 111 ainda nao foi aplicada. Plano vazio.",
            sorted(faltando),
        )
        return None
    group_id_by_key = {
        _text(row["group_key"]): int(row["id"])
        for row in db.query(SELECT_SYSTEM_GROUPS_SQL, None)
    }
    ausentes = [k for k in TARGET_GROUP_KEYS if k not in group_id_by_key]
    if ausentes:
        raise AccessGrantsModelosTiposTreinoError(
            f"114: grupo(s) de sistema {ausentes} ausente(s) em {T_GROUPS}; "
            "rode a 111 antes. Nenhum statement executado."
        )
    return {k: group_id_by_key[k] for k in TARGET_GROUP_KEYS}


def _existing(db) -> set[tuple[int, str]]:
    return {
        (int(row["group_id"]), _text(row["action"]))
        for row in db.query(SELECT_RESOURCE_GRANTS_SQL, (RESOURCE,))
    }


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura; so o que falta."""
    ids = _target_group_ids(db)
    if ids is None:
        return []
    existentes = _existing(db)
    statements: list[tuple[str, tuple | None]] = []
    for group_key in TARGET_GROUP_KEYS:
        group_id = ids[group_key]
        for resource, action, scope in GRANTS_BY_GROUP[group_key]:
            if (group_id, action) not in existentes:
                statements.append((INSERT_GRANT_SQL, (group_id, resource, action, scope)))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    ids = _target_group_ids(db)
    if ids is None:
        return []
    presentes = {group_id for group_id, _ in _existing(db)}
    return [
        (DELETE_GRANT_SQL, (ids[group_key], RESOURCE))
        for group_key in TARGET_GROUP_KEYS
        if ids[group_key] in presentes
    ]


def up(db):
    statements = plan(db)
    for sql, params in statements:
        db.execute(sql, params)
    if not statements or getattr(db, "dry_run", False):
        return
    # INSERT IGNORE engole qualquer erro (nao so duplicidade): confirmar a gravacao.
    existentes = _existing(db)
    faltando = [
        (params[0], params[2])
        for _, params in statements
        if (params[0], params[2]) not in existentes
    ]
    if faltando:
        raise AccessGrantsModelosTiposTreinoError(
            f"114: concessao(oes) (group_id, acao) {faltando} de {RESOURCE} nao "
            "gravada(s) apos o INSERT IGNORE."
        )


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
