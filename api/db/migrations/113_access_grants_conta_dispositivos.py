"""113 - Concessoes de `conta.dispositivos` aos grupos de sistema (ADR 0011, lote 2B).

O relogio (dispositivos) vira recurso controlavel por grupo: `conta.dispositivos`
(dominio `Conta`, rotulo "Relogio e dispositivos"). Decisao do usuario: os grupos
de sistema `usuario` e `personal_trainer` recebem `ver`/`editar`/`excluir` com
escopo `proprios`; `free` continua vazio; `admin` continua com acesso total
implicito (sem linhas). Retrato congelado: nao importa `api/app`; o acumulado
111 + 113 e conferido contra `api/app/access/catalog.py` por teste de paridade.

So DML em `access_group_grants` (sem DDL, sem FK nova). A 111 e a 112 nao sao
editadas.

Idempotencia: `plan(db)` so LE (tabelas, grupos de sistema, concessoes atuais do
recurso) e emite so o que falta, um `INSERT IGNORE` parametrizado por
(grupo, acao) (o UNIQUE group_id+resource+action protege tambem). Concessao ja
existente, mesmo com escopo editado pelo admin, NAO e tocada. Sobre o estado
convergido o plano e vazio; interrompida em qualquer ponto, reexecutar converge.
Grupos que nao sao de sistema (mesmo com a mesma `group_key`) e outras
concessoes nunca sao tocados.

Pre-requisito: a 111. Se `access_groups` e/ou `access_group_grants` nao
existirem, `plan(db)` devolve plano VAZIO (sem erro, sem escrita, com log): e o
caso do `--dry-run` em banco novo, que planeja o lote sem aplicar a 111 antes.
Tabelas presentes mas `usuario` ou `personal_trainer` ausente como grupo de
sistema (`is_system = 1`): `AccessGrantsContaDispositivosError` antes de qualquer
escrita.

Impacto em producao: com a 111 aplicada (producao, 10/11/2026) insere ate 6
linhas (2 grupos x 3 acoes); nada le `conta.dispositivos` ate a conversao das
rotas do relogio (proxima tarefa do lote), entao o comportamento so muda quando
as rotas forem convertidas.

down: remove as concessoes de `conta.dispositivos` dos grupos de sistema
`usuario` e `personal_trainer` (o recurso deixa de existir no catalogo quando o
codigo volta). Em producao o runner nao roda `down`.

Data API: um statement por chamada, SQL parametrizado, sem PREPARE/SET.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

T_GROUPS = "access_groups"
T_GRANTS = "access_group_grants"

RESOURCE = "conta.dispositivos"

ACTION_VER = "ver"
ACTION_EDITAR = "editar"
ACTION_EXCLUIR = "excluir"
ACTIONS = (ACTION_VER, ACTION_EDITAR, ACTION_EXCLUIR)

SCOPE_PROPRIOS = "proprios"

GROUP_PERSONAL_TRAINER = "personal_trainer"
GROUP_USUARIO = "usuario"

# Retrato congelado das linhas desta migracao: group_key -> (recurso, acao, escopo)
GRANTS_BY_GROUP: dict[str, tuple[tuple[str, str, str], ...]] = {
    GROUP_USUARIO: tuple((RESOURCE, action, SCOPE_PROPRIOS) for action in ACTIONS),
    GROUP_PERSONAL_TRAINER: tuple((RESOURCE, action, SCOPE_PROPRIOS) for action in ACTIONS),
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


class AccessGrantsContaDispositivosError(Exception):
    """Pre-requisito (111) incompleto; nenhum statement desta execucao foi aplicado."""


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
            "113: tabela(s) %s ausente(s); a 111 ainda nao foi aplicada. Plano vazio.",
            sorted(faltando),
        )
        return None
    group_id_by_key = {
        _text(row["group_key"]): int(row["id"])
        for row in db.query(SELECT_SYSTEM_GROUPS_SQL, None)
    }
    ausentes = [k for k in TARGET_GROUP_KEYS if k not in group_id_by_key]
    if ausentes:
        raise AccessGrantsContaDispositivosError(
            f"113: grupo(s) de sistema {ausentes} ausente(s) em {T_GROUPS}; "
            "rode a 111 antes. Nenhum statement executado."
        )
    return {k: group_id_by_key[k] for k in TARGET_GROUP_KEYS}


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura; so o que falta."""
    ids = _target_group_ids(db)
    if ids is None:
        return []
    existentes = {
        (int(row["group_id"]), _text(row["action"]))
        for row in db.query(SELECT_RESOURCE_GRANTS_SQL, (RESOURCE,))
    }
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
    presentes = {
        int(row["group_id"])
        for row in db.query(SELECT_RESOURCE_GRANTS_SQL, (RESOURCE,))
    }
    return [
        (DELETE_GRANT_SQL, (ids[group_key], RESOURCE))
        for group_key in TARGET_GROUP_KEYS
        if ids[group_key] in presentes
    ]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
